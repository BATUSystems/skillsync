"""180-J2: the test commands part of the guard hook (`library/hooks/guard-commands.py --tests warn`): the protocol (stdin JSON -> stdout JSON with
`additionalContext`, exit 0, no permission decision), the one-time exception and its trail, and, above all, that a failure of this part can never weaken the
refusal of deletion that lives in the same script."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / 'library' / 'hooks'
TABLE = ROOT / 'skillsync_workflow' / 'data' / 'test_tools.json'


class HookBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='guard-tests-hook-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / 'proj'
        (self.project / '.git').mkdir(parents=True)
        self.hooks = self.project / '.claude' / 'hooks'
        self.hooks.mkdir(parents=True)
        shutil.copy2(HOOKS / 'guard-commands.py', self.hooks / 'guard-commands.py')
        shutil.copy2(HOOKS / 'guard-tests.py', self.hooks / 'guard-tests.py')
        shutil.copy2(TABLE, self.hooks / 'test-tools.json')

    def run_hook(self, command, *args, agent_id=None, cwd=None, stdin=None, env=None):
        payload = {'tool_name': 'Bash', 'cwd': str(cwd or self.project), 'tool_input': {'command': command}}
        if agent_id:
            payload['agent_id'] = agent_id
        result = subprocess.run([sys.executable, str(self.hooks / 'guard-commands.py'), *args], input=stdin if stdin is not None else json.dumps(payload),
                                capture_output=True, text=True, timeout=30, env={**os.environ, **(env or {})})
        return result

    def warn(self, command, *extra, **kwargs):
        return self.run_hook(command, '--mode', 'deny', '--tests', 'warn', *extra, **kwargs)

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {'hookSpecificOutput'})
        out = data['hookSpecificOutput']
        self.assertEqual(set(out), {'hookEventName', 'additionalContext'}, 'no permissionDecision, no updatedInput')
        self.assertEqual(out['hookEventName'], 'PreToolUse')
        return out['additionalContext']


class Protocol(HookBase):
    def test_a_wide_run_adds_a_warning_in_additional_context_and_exits_zero(self):
        text = self.context(self.warn('python3 -m unittest discover -s tests'))
        self.assertIn('skillsync test-run', text)
        self.assertIn('not evidence', text)
        self.assertIn(str(self.project).replace('\\', '/'), text)
        self.assertLess(len(text), 10000)

    def test_narrow_exempt_wrapped_and_unrelated_commands_stay_silent(self):
        for command in ('python3 -m unittest tests.test_a', 'pytest tests/test_a.py', 'pytest --collect-only', 'skillsync test-run . -- pytest',
                        'flock /run/x.lock systemd-run --user --scope pytest', 'ls -la', 'echo pytest', 'git commit -m "run pytest and dotnet test"',
                        'grep -rn "unittest discover" docs', "cat <<'EOF'\npytest\nEOF"):
            with self.subTest(command=command):
                self.assertIsNone(self.context(self.warn(command)))

    def test_it_finds_the_run_inside_a_shell_a_substitution_and_a_chain(self):
        for command in ("bash -c 'pytest'", 'echo $(dotnet test)', 'cd src && npx vitest run', 'timeout 600 python3 -m unittest discover', 'env X=1 pytest', 'eval "cargo test"'):
            with self.subTest(command=command):
                self.assertIsNotNone(self.context(self.warn(command)), command)

    def test_the_main_session_and_a_subagent_get_different_closing_sentences(self):
        main = self.context(self.warn('pytest'))
        sub = self.context(self.warn('pytest', agent_id='agent-1'))
        self.assertNotIn('sub-agent', main)
        self.assertIn('sub-agent', sub)

    def test_several_wide_runs_are_counted(self):
        text = self.context(self.warn('pytest && dotnet test'))
        self.assertIn('2 wide test runs', text)

    def test_the_threshold_and_the_wrappers_come_from_the_arguments(self):
        modules = ' '.join(f'tests.test_m{i}' for i in range(12))
        self.assertIsNone(self.context(self.warn('python3 -m unittest ' + modules)))
        self.assertIsNotNone(self.context(self.warn('python3 -m unittest ' + modules, '--wide-modules', '10')))
        self.assertIsNotNone(self.context(self.warn('make test')))
        self.assertIsNone(self.context(self.warn('make test', '--wrapper', 'make test')))   # a wrapper of the manifest: a prefix of the command

    def test_tests_off_is_silent_and_a_bad_mode_value_is_silent(self):
        self.assertIsNone(self.context(self.run_hook('pytest', '--mode', 'deny', '--tests', 'off')))
        self.assertIsNone(self.context(self.run_hook('pytest', '--mode', 'deny')))
        self.assertIsNone(self.context(self.run_hook('pytest', '--mode', 'deny', '--tests', 'maybe')))

    def test_garbage_on_stdin_is_exit_zero_and_no_output(self):
        for stdin in ('', 'not json', '[]', '{"tool_name": "Bash"}', '{"tool_name": "Read", "tool_input": {}}', '{"tool_name": "Bash", "tool_input": {"command": 3}}'):
            with self.subTest(stdin=stdin):
                result = self.run_hook('x', '--mode', 'deny', '--tests', 'warn', stdin=stdin)
                self.assertEqual((result.returncode, result.stdout.strip()), (0, ''))

    def test_the_delete_mode_off_still_warns_about_tests(self):
        self.assertIsNotNone(self.context(self.run_hook('pytest', '--mode', 'off', '--tests', 'warn')))
        result = self.run_hook('rm -rf /srv/x', '--mode', 'off', '--tests', 'warn')   # deleting is not refused when the delete part is off
        self.assertEqual((result.returncode, result.stdout.strip()), (0, ''))

    def test_a_warning_is_json_on_stdout_only_nothing_on_stderr(self):
        result = self.warn('pytest')
        self.assertEqual(result.stderr, '')


class ReviewFixes(HookBase):
    def test_running_the_hook_leaves_no_pycache_in_the_hook_directory(self):
        self.warn('pytest')
        self.assertFalse((self.hooks / '__pycache__').exists(), sorted(p.name for p in self.hooks.iterdir()))

    def test_a_non_ascii_command_never_breaks_the_output_on_a_narrow_console(self):
        result = self.warn("pytest zażółć", env={'PYTHONIOENCODING': 'cp1252'})
        text = self.context(result)
        self.assertIsNotNone(text)

    def test_a_manifest_prefix_with_a_slash_matches_and_an_open_quote_does_not_silence_the_part(self):
        self.assertIsNone(self.context(self.warn('make test', '--wrapper', 'make test')))
        self.assertIsNotNone(self.context(self.warn('pytest', '--wrapper', "it's an open quote")), 'the rest of the part keeps working')

    def test_powershell_and_cmd_are_unwrapped_like_a_shell(self):
        for command in ('powershell -c "dotnet test"', 'pwsh -Command dotnet test', 'cmd /c pytest'):
            with self.subTest(command=command):
                self.assertIsNotNone(self.context(self.warn(command)), command)

    def test_a_bare_flock_is_not_the_wrapper_and_systemd_run_alone_is(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('gt', HOOKS / 'guard-tests.py')
        gt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gt)
        self.assertFalse(gt.is_wrapper(['flock', '/tmp/x.lock', 'pytest']), 'a bare flock is not the station wrapper (it is also not in the table: such a command is not recognised)')
        self.assertIsNone(self.context(self.warn('systemd-run --user --scope pytest')))
        self.assertIsNone(self.context(self.warn('flock /tmp/x.lock systemd-run --user --scope pytest')))

    def test_a_command_without_any_tool_word_is_cheap(self):
        import time
        self.warn('ls')   # warm the file cache
        start = time.perf_counter()
        for _ in range(5):
            self.warn('ls -la docs')
        self.assertLess((time.perf_counter() - start) / 5, 0.25)

    def test_a_system_exit_in_the_classifier_files_silences_only_the_test_part(self):
        (self.hooks / 'guard-tests.py').write_text('raise SystemExit(3)\n', encoding='utf-8')
        self.assertEqual(self.warn('pytest').returncode, 0)
        result = self.run_hook('rm -rf /srv/not-a-temp-dir/x', '--mode', 'deny', '--tests', 'warn')
        self.assertEqual(result.returncode, 2)

    def test_a_bad_wide_modules_value_falls_back_to_the_default(self):
        self.assertIsNotNone(self.context(self.warn('python3 -m unittest discover', '--wide-modules', '\u00b2')))


class Exception_(HookBase):
    def trail(self):
        path = self.project / '.skillsync' / 'hook-exceptions.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_the_exception_prefix_silences_the_warning_and_leaves_a_trail(self):
        result = self.warn("SKILLSYNC_GUARD_EXCEPT='the user asked for a full run' python3 -m unittest discover", agent_id='agent-2')
        self.assertIsNone(self.context(result))
        rows = self.trail()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reason'], 'the user asked for a full run')
        self.assertEqual(rows[0]['kind'], ['tests'])
        self.assertEqual(rows[0]['agent_id'], 'agent-2')

    def test_the_exception_covers_only_its_command(self):
        text = self.context(self.warn("SKILLSYNC_GUARD_EXCEPT='x' pytest && dotnet test"))
        self.assertIsNotNone(text)
        self.assertIn('dotnet test', text)
        self.assertNotIn('2 wide', text)


class FailureNeverWeakensTheRefusal(HookBase):
    """The wish of the owner of the project: an error of the test part cannot switch the refusal of deletion off."""
    DELETE = 'rm -rf /srv/not-a-temp-dir/data'

    def refused(self, *args):
        result = self.run_hook(self.DELETE, '--mode', 'deny', *args)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('trash', result.stderr)

    def test_baseline_the_deletion_is_refused_with_and_without_the_test_part(self):
        self.refused()
        self.refused('--tests', 'warn')

    def test_a_missing_table_silences_only_the_test_part(self):
        (self.hooks / 'test-tools.json').unlink()
        self.assertIsNone(self.context(self.warn('pytest')))
        self.refused('--tests', 'warn')

    def test_a_broken_table_silences_only_the_test_part(self):
        for text in ('', '{', '[]', '{"schema": 99}', '{"schema": 1, "tools": 5}'):
            with self.subTest(text=text):
                (self.hooks / 'test-tools.json').write_text(text, encoding='utf-8')
                self.assertIsNone(self.context(self.warn('pytest')))
                self.refused('--tests', 'warn')

    def test_a_missing_or_broken_classifier_silences_only_the_test_part(self):
        (self.hooks / 'guard-tests.py').write_text('this is not python (', encoding='utf-8')
        self.assertIsNone(self.context(self.warn('pytest')))
        self.refused('--tests', 'warn')
        (self.hooks / 'guard-tests.py').unlink()
        self.assertIsNone(self.context(self.warn('pytest')))
        self.refused('--tests', 'warn')

    def test_an_exception_raised_by_the_classifier_silences_only_the_test_part(self):
        (self.hooks / 'guard-tests.py').write_text(
            'import json\n\ndef load_table(path):\n    return {"tools": (), "modules": {}, "wrappers": {}, "layers": 6}\n\n'
            'def classify(*args, **kwargs):\n    raise RuntimeError("boom")\n\ndef warning_text(*args, **kwargs):\n    raise RuntimeError("boom")\n', encoding='utf-8')
        self.assertIsNone(self.context(self.warn('pytest')))
        self.refused('--tests', 'warn')

    def test_a_classifier_that_returns_rubbish_is_silent(self):
        (self.hooks / 'guard-tests.py').write_text(
            'def load_table(path):\n    return {"tools": (), "modules": {}, "wrappers": {}, "layers": 6}\n\n'
            'def classify(*args, **kwargs):\n    return 5\n\ndef warning_text(*args, **kwargs):\n    return None\n', encoding='utf-8')
        self.assertEqual(self.warn('pytest').returncode, 0)
        self.refused('--tests', 'warn')

    def test_a_refusal_comes_first_when_a_command_has_both(self):
        result = self.run_hook(self.DELETE + ' && pytest', '--mode', 'deny', '--tests', 'warn')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.strip(), '', 'a refused command gets no warning on top of the refusal')

    def test_an_unwritable_trail_does_not_break_the_test_part(self):
        (self.project / '.skillsync').write_text('a file, not a directory', encoding='utf-8')
        result = self.warn("SKILLSYNC_GUARD_EXCEPT='x' pytest")
        self.assertEqual(result.returncode, 0)

    def test_the_delete_exception_still_works_with_the_test_part_on(self):
        result = self.run_hook("SKILLSYNC_GUARD_EXCEPT='agreed' " + self.DELETE, '--mode', 'deny', '--tests', 'warn')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / '.skillsync' / 'hook-exceptions.jsonl').exists())


class Speed(HookBase):
    def test_an_unrelated_command_costs_one_process_start(self):
        import time
        start = time.perf_counter()
        for _ in range(5):
            self.warn('ls -la')
        self.assertLess((time.perf_counter() - start) / 5, 0.5, 'a Python start and a classification, nothing heavier')


if __name__ == '__main__':
    unittest.main()
