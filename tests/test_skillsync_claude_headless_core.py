"""CLAUDE-6 F1: the shared core of the wrappers that start the real `claude` (`tools/claude-headless/claude_headless_core.py`).

K5: `tools/claude-eval/run.py` keeps its names and its behaviour after the refactor: its `--dry-run` equals the golden file written from the code of CLAUDE-4
before the refactor, and the names that its tests use are the objects of the core. Nothing here starts the real `claude`."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PY = ROOT / 'tools' / 'claude-headless' / 'claude_headless_core.py'
RUN_PY = ROOT / 'tools' / 'claude-eval' / 'run.py'
GOLDEN = ROOT / 'tests' / 'fixtures' / 'claude_headless' / 'claude-eval-dry-run.json'
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/fixture-home', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'SECRET_TOKEN': 'must-not-pass'}
SHARED = ('ENV_ALLOWLIST', 'HEADER_RE', 'MAX_COST_CEILING_USD', 'MODEL_RE', 'UniqueKeyLoader', 'absolute_dir', 'build_env', 'frontmatter',
          'model_name', 'positive_cost')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='claude-core-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.core = load(CORE_PY, 'claude_headless_core_under_test')


class CoreValidation(Tmp):
    def test_cost_limit_is_above_zero_and_at_most_the_ceiling(self):
        self.assertEqual(self.core.positive_cost('1.5'), 1.5)
        self.assertEqual(self.core.positive_cost('5'), 5.0)
        for bad in ('0', '-1', '5.01', 'abc', '', 'nan'):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                self.core.positive_cost(bad)

    def test_profile_directory_must_be_absolute_existing_and_without_dotdot(self):
        self.assertEqual(self.core.absolute_dir(str(self.base)), str(self.base))
        for bad in ('relative/dir', str(self.base / 'missing'), str(self.base / 'a' / '..' / 'b'), ''):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                self.core.absolute_dir(bad)

    def test_model_is_a_plain_identifier(self):
        for good in ('haiku', 'sonnet', 'claude-sonnet-4-5', 'opus[1m]', 'a.b:c'):
            self.assertEqual(self.core.model_name(good), good)
        for bad in ('', '-x', 'a b', 'a;b', '$(x)', 'a/b'):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                self.core.model_name(bad)


class CoreEnvironment(Tmp):
    def test_only_the_allowlist_locale_and_the_profile_reach_the_child(self):
        env = self.core.build_env(ENV, str(self.base))
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'HOME': '/fixture-home', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'CLAUDE_CONFIG_DIR': str(self.base)})
        self.assertNotIn('SECRET_TOKEN', env)

    def test_a_given_home_replaces_the_home_and_nothing_else_changes(self):
        plain = self.core.build_env(ENV, str(self.base))
        moved = self.core.build_env(ENV, str(self.base), home=self.base / 'home')
        self.assertEqual(moved['HOME'], str(self.base / 'home'))
        self.assertEqual({k: v for k, v in moved.items() if k != 'HOME'}, {k: v for k, v in plain.items() if k != 'HOME'})

    def test_the_config_dir_of_the_argument_wins_over_the_inherited_one(self):
        env = self.core.build_env({**ENV, 'CLAUDE_CONFIG_DIR': '/inherited'}, str(self.base))
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.base))

    def test_the_core_starts_no_process(self):
        source = CORE_PY.read_text(encoding='utf-8')
        for word in ('subprocess', 'os.system', 'os.exec', 'Popen'):
            self.assertNotIn(word, source)


class CoreFrontmatter(Tmp):
    def read(self, text):
        path = self.base / 'f.md'
        path.write_text(text, encoding='utf-8')
        return self.core.frontmatter(path)

    def test_valid_header(self):
        self.assertEqual(self.read('---\na: 1\nb: [x, y]\n---\nbody\n'), ({'a': 1, 'b': ['x', 'y']}, None))

    def test_errors_are_returned_not_raised(self):
        for text, expected in (('no header\n', 'no frontmatter'), ('---\na: 1\na: 2\n---\n', 'invalid YAML'), ('---\n1: x\n---\n', 'invalid YAML'),
                               ('---\n- a\n---\n', 'frontmatter is not a mapping'), ('---\na: [unclosed\n---\n', 'invalid YAML')):
            with self.subTest(text=text):
                data, error = self.read(text)
                self.assertIsNone(data)
                self.assertTrue(error.startswith(expected), error)
        self.assertEqual(self.core.frontmatter(self.base / 'missing.md'), (None, 'unreadable (FileNotFoundError)'))

    def test_the_closing_marker_is_a_whole_line(self):
        self.assertEqual(self.read('---\na: "x --- y"\n---\n')[0], {'a': 'x --- y'})


class EvalWrapperAfterTheRefactor(Tmp):
    def test_the_names_of_the_wrapper_are_the_objects_of_the_core(self):
        wrapper = load(RUN_PY, 'claude_eval_run_under_test')
        for name in SHARED:
            with self.subTest(name=name):
                self.assertTrue(hasattr(wrapper, name), f'run.py lost {name}')
        self.assertIs(wrapper.build_env, sys.modules['claude_headless_core'].build_env)
        self.assertIs(wrapper.frontmatter, sys.modules['claude_headless_core'].frontmatter)

    def test_the_dry_run_equals_the_golden_file_written_before_the_refactor(self):
        (self.base / 'profile').mkdir()
        result = subprocess.run([sys.executable, str(RUN_PY), '--model', 'haiku', '--max-cost-usd', '1.5', '--config-dir', str(self.base / 'profile'),
                                 '--runs', '2', '--case', 'feature-*', '--out', str(self.base / 'out'), '--dry-run'],
                                capture_output=True, text=True, env=ENV, cwd=self.base, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = json.loads(result.stdout.replace(str(self.base), '<TMP>'))
        self.assertEqual(printed, json.loads(GOLDEN.read_text(encoding='utf-8')))
        self.assertNotIn('SECRET_TOKEN', result.stdout)


if __name__ == '__main__':
    unittest.main()
