"""CLAUDE-4: the wrapper `tools/claude-eval/run.py` around `claude plugin eval` forces the safety flags, builds a clean environment, validates the cases and stages a copy
of the skills. Nothing here calls the real `claude`: the functions are tested directly and one subprocess runs a fake `claude`."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tmp_paths import child  # noqa: E402

RUN_PY = ROOT / 'tools' / 'claude-eval' / 'run.py'


def load():
    """The wrapper as a module (the directory name has a hyphen, so no `import`)."""
    spec = importlib.util.spec_from_file_location('claude_eval_run', RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding='utf-8')
    return Path(path)


GOOD_PROMPT = '---\nruns: 3\nmax_turns: 6\nallowed_tools: [Read, Glob, Grep, Skill]\ntags: [feature]\n---\nUse the skill feature for ticket ABC-123.\n'
GOOD_GRADER = '---\ntype: tool_used\ntool: Read\ninput_match: ticket-intake\n---\n'


class Tmp(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        (self.base / 'profile').mkdir()

    def case(self, name='feature-key', prompt=GOOD_PROMPT, graders=None):
        folder = child(child(self.base, 'evals'), name)
        write(folder / 'prompt.md', prompt)
        for file, text in (graders if graders is not None else {'reads.md': GOOD_GRADER}).items():
            write(folder / 'graders' / file, text)
        return folder

    def library(self, skills=('feature', 'bugfix', 'pre-implement', 'workflow')):
        library = child(self.base, 'library')
        for name in skills:
            write(library / 'skills' / name / 'SKILL.md', f'---\nname: {name}\ndescription: d\n---\nBody {name}\n')
        write(library / 'skills' / 'feature' / 'references' / 'ticket-intake.md', 'intake\n')
        return library


class Arguments(Tmp):
    def parse(self, *extra, config=True):
        args = ['--model', 'haiku', '--max-cost-usd', '0.5']
        if config:
            args += ['--config-dir', str(self.base / 'profile')]
        return self.mod.parse_args(args + list(extra))

    def refused(self, *argv):
        with self.assertRaises(SystemExit) as raised:
            self.mod.parse_args(list(argv))
        self.assertNotEqual(raised.exception.code, 0)

    def test_a_complete_command_line_is_accepted(self):
        args = self.parse()
        self.assertEqual((args.model, args.max_cost_usd, args.runs), ('haiku', 0.5, 3))

    def test_a_cost_ceiling_is_required_and_positive(self):
        profile = str(self.base / 'profile')
        self.refused('--model', 'haiku', '--config-dir', profile)
        self.refused('--model', 'haiku', '--max-cost-usd', '0', '--config-dir', profile)
        self.refused('--model', 'haiku', '--max-cost-usd', '-1', '--config-dir', profile)
        self.refused('--model', 'haiku', '--max-cost-usd', 'abc', '--config-dir', profile)

    def test_a_model_is_required(self):
        self.refused('--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'))

    def test_the_profile_must_be_an_absolute_path(self):
        self.refused('--model', 'haiku', '--max-cost-usd', '1')
        self.refused('--model', 'haiku', '--max-cost-usd', '1', '--config-dir', 'relative/profile')
        self.refused('--model', 'haiku', '--max-cost-usd', '1', '--config-dir', '~/profile')

    def test_the_number_of_runs_is_one_to_three(self):
        for value in ('1', '2', '3'):
            self.assertEqual(self.parse('--runs', value).runs, int(value))
        for value in ('0', '4', '10', 'x'):
            with self.subTest(value=value):
                self.refused('--model', 'haiku', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'), '--runs', value)

    def test_a_flag_outside_the_whitelist_is_refused(self):
        for flag in ('--scaffold', '--allow-tools', '--publish-report', '--allow-real-servers', '--mocks', '--judge-model', '--concurrency', '--ablation'):
            with self.subTest(flag=flag):
                self.refused('--model', 'haiku', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'), flag, 'x')


class Command(Tmp):
    def command(self, **override):
        args = self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', '0.5', '--config-dir', str(self.base / 'profile')])
        stage, out = self.base / 'stage', self.base / 'out'
        for key, value in override.items():
            setattr(args, key, value)
        return self.mod.build_command(args, stage, out), stage, out

    def test_the_forced_flags_are_always_there(self):
        argv, stage, out = self.command()
        self.assertEqual(argv[1:3], ['plugin', 'eval'])
        self.assertEqual(argv[3], str(stage / 'plugin'))
        for flag in ('--no-publish', '--trust-plugin'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--max-cost-usd') + 1], '0.5')
        self.assertEqual(argv[argv.index('--model') + 1], 'haiku')
        self.assertEqual(argv[argv.index('--runs') + 1], '3')
        self.assertEqual(argv[argv.index('--concurrency') + 1], '1')
        self.assertEqual(argv[argv.index('--ablation') + 1], 'with-without')
        self.assertTrue(argv[argv.index('--json') + 1].startswith(str(out)))
        self.assertTrue(argv[argv.index('--report') + 1].startswith(str(out)))

    def test_the_dangerous_flags_never_appear(self):
        argv, _, _ = self.command()
        for flag in ('--scaffold', '--allow-tools', '--publish-report', '--allow-real-servers', '--mocks', '--dangerously-skip-permissions'):
            self.assertNotIn(flag, argv)
        self.assertEqual([a for a in argv[1:] if not a.startswith('-') and os.path.isabs(a)][:1], [argv[3]])

    def test_a_case_filter_is_passed_through_and_the_binary_is_the_first_element(self):
        argv, _, _ = self.command(case='feature-*', claude_bin='/opt/fake/claude')
        self.assertEqual(argv[0], '/opt/fake/claude')
        self.assertEqual(argv[argv.index('--case') + 1], 'feature-*')

    def test_the_ceiling_is_a_plain_decimal(self):
        argv, _, _ = self.command(max_cost_usd=3.0)
        self.assertEqual(argv[argv.index('--max-cost-usd') + 1], '3.0')


class Environment(Tmp):
    def test_only_the_allowlist_and_the_profile_reach_the_child(self):
        environ = {'PATH': '/usr/bin', 'HOME': str(self.base), 'LANG': 'C.UTF-8', 'LC_ALL': 'C', 'ANTHROPIC_API_KEY': 'x', 'ANTHROPIC_BASE_URL': 'http://x',
                   'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_USE_VERTEX': '1', 'EVAL_ANY': '1', 'CLAUDE_CONFIG_DIR': '/other/profile', 'AWS_PROFILE': 'p', 'SECRET': 's'}
        env = self.mod.build_env(environ, str(self.base / 'profile'))
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.base / 'profile'))
        for key in ('PATH', 'HOME', 'LANG', 'LC_ALL'):
            self.assertEqual(env[key], environ[key])
        for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'EVAL_ANY', 'AWS_PROFILE', 'SECRET'):
            self.assertNotIn(key, env)

    def test_an_empty_environment_still_gets_the_profile(self):
        self.assertEqual(self.mod.build_env({}, str(self.base / 'profile')), {'CLAUDE_CONFIG_DIR': str(self.base / 'profile')})


class Validation(Tmp):
    def errors(self):
        return self.mod.validate_cases(self.base / 'evals')

    def test_a_good_case_has_no_error(self):
        self.case()
        self.assertEqual(self.errors(), [])

    def test_no_case_is_an_error(self):
        (self.base / 'evals').mkdir()
        self.assertTrue(self.errors())

    def test_a_case_without_a_grader_is_an_error(self):
        self.case(graders={})
        self.assertTrue(any('grader' in e for e in self.errors()), self.errors())

    def test_the_keys_of_the_prompt_are_a_closed_list(self):
        for line in ('env: {EVAL_X: 1}', 'model: opus', 'plugins: [../..]', 'scaffold_script: x.sh', 'unknown_key: 1'):
            with self.subTest(line=line):
                self.case(prompt=GOOD_PROMPT.replace('tags: [feature]', line))
                self.assertTrue(self.errors(), line)

    def test_the_limits_of_runs_turns_and_time_are_enforced(self):
        for line in ('runs: 4', 'runs: 0', 'max_turns: 11', 'timeout_seconds: 301'):
            with self.subTest(line=line):
                key = line.split(':')[0]
                prompt = '\n'.join(item for item in GOOD_PROMPT.split('\n') if not item.startswith(key + ':')).replace('---\n', f'---\n{line}\n', 1)
                self.case(prompt=prompt)
                self.assertTrue(self.errors(), line)

    def test_only_read_tools_may_be_allowed(self):
        for tools in ('[Bash]', '[Read, Write]', '[WebFetch]', '[Edit]', '[Read, mcp__x]'):
            with self.subTest(tools=tools):
                self.case(prompt=GOOD_PROMPT.replace('[Read, Glob, Grep, Skill]', tools))
                self.assertTrue(any('allowed_tools' in e for e in self.errors()), self.errors())

    def test_only_deterministic_graders_are_allowed(self):
        for kind in ('llm', 'baseline', 'unknown'):
            with self.subTest(kind=kind):
                self.case(graders={'g.md': f'---\ntype: {kind}\ncriteria: x\n---\n'})
                self.assertTrue(any('type' in e for e in self.errors()), self.errors())
        for kind in ('regex', 'tool_used', 'tool_order', 'file_exists'):
            with self.subTest(kind=kind):
                self.case(graders={'g.md': f'---\ntype: {kind}\n---\n'})
                self.assertFalse(any('type' in e for e in self.errors()), self.errors())

    def test_a_case_yaml_is_refused_because_the_prompt_file_is_the_only_form(self):
        folder = self.case()
        write(folder / 'case.yaml', 'schema_version: "1.1"\nname: x\n')
        self.assertTrue(any('case.yaml' in e for e in self.errors()), self.errors())

    def test_a_broken_frontmatter_is_an_error_not_an_exception(self):
        self.case(prompt='---\nruns: [unclosed\n---\nx\n')
        self.assertTrue(self.errors())


class Hardening(Tmp):
    def errors(self):
        return self.mod.validate_cases(self.base / 'evals')

    def test_a_marker_inside_a_line_does_not_cut_the_header(self):
        self.case(prompt='---\ndescription: a --- b\nenv: {EVAL_X: 1}\nmodel: opus\nallowed_tools: [Bash]\n---\nx\n')
        errors = self.errors()
        self.assertTrue(any('env' in e for e in errors) and any('allowed_tools' in e for e in errors), errors)

    def test_a_duplicate_key_is_an_error(self):
        self.case(prompt='---\nallowed_tools: [Bash]\nallowed_tools: [Read]\n---\nx\n')
        self.assertTrue(any('duplicate' in e for e in self.errors()), self.errors())

    def test_a_non_string_key_and_unhashable_values_are_errors_not_exceptions(self):
        self.case(prompt='---\n1: a\n---\nx\n')
        self.assertTrue(self.errors())
        self.case(prompt=GOOD_PROMPT.replace('[Read, Glob, Grep, Skill]', '[[Read], {a: b}]'))
        self.assertTrue(any('allowed_tools' in e for e in self.errors()), self.errors())
        self.case(graders={'g.md': '---\ntype: [a]\n---\n'})
        self.assertTrue(any('type' in e for e in self.errors()), self.errors())

    def test_an_extra_file_in_a_case_is_an_error(self):
        for extra in ('scaffold.sh', '.mcp.json', 'mocks/server.json', 'graders/g.yaml'):
            with self.subTest(extra=extra):
                folder = self.case()
                write(folder / extra, 'x\n')
                self.assertTrue(any(Path(extra).parts[-2 if extra.startswith('graders') else 0] in e or Path(extra).name in e for e in self.errors()), self.errors())
                (folder / extra).unlink()
                if (folder / extra).parent != folder and (folder / extra).parent.name == 'mocks':
                    (folder / 'mocks').rmdir()

    def test_the_stage_holds_only_prompts_and_graders_and_is_validated_after_copying(self):
        library = self.library()
        folder = self.case()
        write(folder / 'scaffold.sh', 'x\n')
        write(self.base / 'evals' / 'README.md', 'top\n')
        stage = self.base / 'stage'
        self.mod.stage_plugin(library, self.base / 'evals', stage, skills=('feature',))
        files = sorted(str(p.relative_to(stage / 'plugin' / 'evals')) for p in (stage / 'plugin' / 'evals').rglob('*') if p.is_file())
        self.assertEqual(files, [str(Path('feature-key') / 'graders' / 'reads.md'), str(Path('feature-key') / 'prompt.md')])

    def test_a_model_must_be_a_plain_identifier_and_the_cost_is_capped(self):
        profile = str(self.base / 'profile')
        for model in ('', '-x', '--allow-tools', 'a b', 'a=b'):
            with self.subTest(model=model):
                with self.assertRaises(SystemExit):
                    self.mod.parse_args(['--model', model, '--max-cost-usd', '1', '--config-dir', profile])
        for cost in ('5.01', '100000', 'nan', 'inf', '1e999'):
            with self.subTest(cost=cost):
                with self.assertRaises(SystemExit):
                    self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', cost, '--config-dir', profile])
        self.assertEqual(self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', '5', '--config-dir', profile]).max_cost_usd, 5.0)

    def test_a_trailing_newline_does_not_pass_the_name_checks(self):
        profile = str(self.base / 'profile')
        with self.assertRaises(SystemExit):
            self.mod.parse_args(['--model', 'opus\n', '--max-cost-usd', '1', '--config-dir', profile])
        with self.assertRaises(SystemExit):
            self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', '1', '--config-dir', profile, '--case', 'x\n'])

    def test_a_graders_directory_that_is_a_link_is_refused_and_not_staged(self):
        folder = self.case(graders={})
        real = child(self.base, 'foreign')
        write(real / 'g.md', GOOD_GRADER)
        try:
            os.symlink(real, folder / 'graders', target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('symbolic links unavailable')
        self.assertTrue(any('graders' in e for e in self.errors()), self.errors())
        self.mod.stage_plugin(self.library(), self.base / 'evals', self.base / 'stage', skills=('feature',))
        self.assertFalse((self.base / 'stage' / 'plugin' / 'evals' / 'feature-key' / 'graders').exists())

    def test_the_case_filter_cannot_look_like_a_flag(self):
        with self.assertRaises(SystemExit):
            self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', '1', '--config-dir', str(self.base / 'p'), '--case=--allow-tools'])

    def test_the_profile_must_be_an_existing_directory_without_dotdot(self):
        for bad in (str(self.base / 'missing'), str(self.base / 'a' / '..' / 'b')):
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    self.mod.parse_args(['--model', 'haiku', '--max-cost-usd', '1', '--config-dir', bad])

    def test_a_skill_directory_that_is_a_symbolic_link_is_refused(self):
        library = self.library(skills=('feature',))
        real = self.base / 'real'
        write(real / 'SKILL.md', 'x\n')
        shutil_target = library / 'skills' / 'bugfix'
        try:
            os.symlink(real, shutil_target, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('symbolic links unavailable')
        self.case()
        with self.assertRaises(SystemExit):
            self.mod.stage_plugin(library, self.base / 'evals', self.base / 'stage', skills=('feature', 'bugfix'))

    def test_a_hard_linked_file_in_a_skill_is_refused(self):
        library = self.library(skills=('feature',))
        outside = write(child(self.base, 'outside.md'), 'secret\n')
        try:
            os.link(outside, library / 'skills' / 'feature' / 'hard.md')
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('hard links unavailable')
        self.case()
        with self.assertRaises(SystemExit):
            self.mod.stage_plugin(library, self.base / 'evals', self.base / 'stage', skills=('feature',))


class Staging(Tmp):
    def test_the_stage_has_copies_a_manifest_and_the_cases(self):
        library, evals = self.library(), self.base / 'evals'
        self.case()
        stage = self.base / 'stage'
        self.mod.stage_plugin(library, evals, stage, skills=('feature', 'bugfix'))
        plugin = stage / 'plugin'
        self.assertTrue((plugin / 'skills' / 'feature' / 'SKILL.md').is_file())
        self.assertTrue((plugin / 'skills' / 'feature' / 'references' / 'ticket-intake.md').is_file())
        self.assertTrue((plugin / 'skills' / 'bugfix' / 'SKILL.md').is_file())
        self.assertFalse((plugin / 'skills' / 'workflow').exists())
        manifest = json.loads((plugin / '.claude-plugin' / 'plugin.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], 'skillsync-eval')
        self.assertTrue((plugin / 'evals' / 'feature-key' / 'prompt.md').is_file())

    def test_the_stage_never_contains_a_symbolic_link(self):
        library = self.library()
        outside = write(child(self.base, 'outside.md'), 'secret\n')
        try:
            os.symlink(outside, library / 'skills' / 'feature' / 'link.md')
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('symbolic links unavailable')
        self.case()
        stage = self.base / 'stage'
        self.mod.stage_plugin(library, self.base / 'evals', stage, skills=('feature',))
        for path in stage.rglob('*'):
            self.assertFalse(path.is_symlink(), path)
        self.assertFalse((stage / 'plugin' / 'skills' / 'feature' / 'link.md').exists())

    def test_a_skill_missing_from_the_library_is_an_error(self):
        library = self.library(skills=('feature',))
        self.case()
        with self.assertRaises(SystemExit):
            self.mod.stage_plugin(library, self.base / 'evals', self.base / 'stage', skills=('feature', 'bugfix'))

    def test_the_default_skills_bring_feature_with_bugfix(self):
        self.assertIn('feature', self.mod.DEFAULT_SKILLS)
        self.assertIn('bugfix', self.mod.DEFAULT_SKILLS)

    def test_dispatch_is_staged_only_when_the_library_hides_it_from_the_model(self):
        library = self.library(skills=('feature', 'bugfix', 'pre-implement', 'workflow', 'dispatch'))
        self.assertNotIn('dispatch', self.mod.skills_for(library))
        write(library / 'skills' / 'dispatch' / 'SKILL.md', '---\nname: dispatch\ndescription: d\ndisable-model-invocation: true\n---\nB\n')
        self.assertIn('dispatch', self.mod.skills_for(library))


class FakeClaude(Tmp):
    def fake(self, exit_code=0):
        script = write(child(self.base, 'fake_claude.py'), 'import json, os, sys\n'
                       'json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}, open(' + repr(str(self.base / 'record.json')) + ', "w"))\n'
                       f'sys.exit({exit_code})\n')
        launcher = child(self.base, 'claude')
        if os.name == 'nt':
            self.skipTest('a fake executable needs a shebang')
        launcher.write_text(f'#!{sys.executable}\n' + script.read_text(encoding='utf-8'), encoding='utf-8')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        return launcher

    def run_wrapper(self, *extra, exit_code=0, env_extra=None):
        launcher = self.fake(exit_code)
        self.case()
        library = self.library()
        record = self.base / 'record.json'
        env = {**os.environ, 'ANTHROPIC_API_KEY': 'must-not-reach-the-child', 'SECRET_TOKEN': 'x', **(env_extra or {})}
        done = subprocess.run([sys.executable, str(RUN_PY), '--model', 'haiku', '--max-cost-usd', '0.5', '--config-dir', str(self.base / 'profile'),
                               '--claude-bin', str(launcher), '--evals-dir', str(self.base / 'evals'), '--library', str(library), '--out', str(self.base / 'out'), *extra],
                              capture_output=True, text=True, env=env, timeout=60)
        return done, record

    def test_the_wrapper_runs_the_binary_with_the_forced_flags_in_a_clean_environment(self):
        done, record = self.run_wrapper()
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(record.read_text(encoding='utf-8'))
        self.assertIn('--no-publish', seen['argv'])
        self.assertEqual(seen['env']['CLAUDE_CONFIG_DIR'], str(self.base / 'profile'))
        self.assertNotIn('ANTHROPIC_API_KEY', seen['env'])
        self.assertNotIn('SECRET_TOKEN', seen['env'])
        argv = seen['argv']
        for flag, value in (('--max-cost-usd', '0.5'), ('--model', 'haiku'), ('--runs', '3'), ('--concurrency', '1')):
            self.assertEqual(argv.count(flag), 1)
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertEqual(seen['env']['PATH'], os.environ['PATH'])
        self.assertNotEqual(Path(seen['cwd']).resolve(), ROOT)
        self.assertFalse(str(Path(seen['cwd']).resolve()).startswith(str(self.base / 'out')))
        self.assertTrue((Path(seen['argv'][2]) / '.claude-plugin' / 'plugin.json').is_file())

    def test_the_exit_code_of_the_binary_is_passed_on(self):
        for code in (1, 2):
            with self.subTest(code=code):
                done, _ = self.run_wrapper('--out', str(self.base / f'out{code}'), exit_code=code)
                self.assertEqual(done.returncode, code)

    def test_a_dry_run_prints_the_command_and_runs_nothing(self):
        done, record = self.run_wrapper('--dry-run')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(record.exists())
        shown = json.loads(done.stdout)
        self.assertIn('--no-publish', shown['argv'])
        self.assertNotIn('ANTHROPIC_API_KEY', shown['env'])
        self.assertIn('CLAUDE_CONFIG_DIR', shown['env'])

    def test_an_invalid_case_stops_the_wrapper_before_the_binary(self):
        launcher = self.fake()
        self.case(prompt=GOOD_PROMPT.replace('tags: [feature]', 'env: {EVAL_X: 1}'))
        library = self.library()
        record = self.base / 'record.json'
        done = subprocess.run([sys.executable, str(RUN_PY), '--model', 'haiku', '--max-cost-usd', '0.5', '--config-dir', str(self.base / 'profile'),
                               '--claude-bin', str(launcher), '--evals-dir', str(self.base / 'evals'), '--library', str(library), '--out', str(self.base / 'out')],
                              capture_output=True, text=True, env=dict(os.environ), timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(record.exists())
        self.assertIn('env', done.stderr)


if __name__ == '__main__':
    unittest.main()
