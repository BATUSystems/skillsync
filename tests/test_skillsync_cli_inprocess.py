"""180-K: `tests/cli_inprocess.run_cli` answers like the real `skillsync` wrapper and leaves the test process as it found it.

Parity: the same arguments give the same return code, stdout and stderr in this process and in a subprocess (version, a BLOCKED answer, an argparse error).
Isolation: cwd, environment, argv, sys.path, streams, signal handlers and modules loaded from outside the package are restored, also after an exception.
The product keeps no state between calls: two calls in one process give the same answer as two subprocesses (the guard of the claim in `cli_inprocess`)."""
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli_inprocess import run_cli

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skillsync'


def child(argv, cwd=None, env=None):
    return subprocess.run([sys.executable, str(CLI), *argv], capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=cwd,
                          env={**os.environ, 'COLUMNS': '80', **(env or {})}, timeout=60)


class Parity(unittest.TestCase):
    def same(self, argv, **kw):
        mine, theirs = run_cli(argv, **kw), child(argv, **kw)
        self.assertEqual((mine.returncode, mine.stdout, mine.stderr), (theirs.returncode, theirs.stdout, theirs.stderr))
        return mine

    def test_version(self):
        self.assertEqual(self.same(['--version']).returncode, 0)

    def test_an_argparse_error_is_exit_2_on_stderr(self):
        result = self.same(['no-such-command'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage:', result.stderr)

    def test_a_blocked_answer_has_the_same_json_and_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.same(['retro-scan', tmp, '--run-id', 'x', '--signals', '.skillsync/runs/x/in.json', '--json'], cwd=tmp)
        self.assertEqual(result.returncode, 2)

    def test_two_calls_in_one_process_equal_two_subprocesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ['retro-scan', tmp, '--run-id', 'x', '--signals', 'nope.json', '--json']
            first, second = run_cli(argv, cwd=tmp), run_cli(argv, cwd=tmp)
            fresh = child(argv, cwd=tmp)
        self.assertEqual((first.returncode, first.stdout), (fresh.returncode, fresh.stdout))
        self.assertEqual((second.returncode, second.stdout), (fresh.returncode, fresh.stdout))


class Isolation(unittest.TestCase):
    def snapshot(self):
        return (os.getcwd(), dict(os.environ), list(sys.argv), list(sys.path), sys.stdout, sys.stderr,
                {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM)})

    def test_everything_is_restored_after_a_call(self):
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(['--version'], cwd=tmp, env={'SKILLSYNC_PROBE': 'x'})
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('SKILLSYNC_PROBE', os.environ)

    def test_the_environment_of_the_call_is_the_parent_plus_the_given_keys(self):
        os.environ['SKILLSYNC_PARENT_KEY'] = 'p'
        self.addCleanup(os.environ.pop, 'SKILLSYNC_PARENT_KEY', None)
        seen = {}
        import skillsync_workflow.cli as cli
        original = cli.main
        cli.main = lambda: seen.update(parent=os.environ.get('SKILLSYNC_PARENT_KEY'), given=os.environ.get('SKILLSYNC_GIVEN_KEY'), argv=list(sys.argv))
        self.addCleanup(setattr, cli, 'main', original)
        run_cli(['a', 'b'], env={'SKILLSYNC_GIVEN_KEY': 'g'})
        self.assertEqual(seen, {'parent': 'p', 'given': 'g', 'argv': ['skillsync', 'a', 'b']})

    def test_a_failure_restores_the_process_and_is_a_traceback_on_stderr(self):
        import skillsync_workflow.cli as cli
        original = cli.main
        before = self.snapshot()

        def boom():
            os.chdir(tempfile.gettempdir())
            os.environ['SKILLSYNC_LEAK'] = '1'
            sys.path.insert(0, '/nonexistent')
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise RuntimeError('boom')
        cli.main = boom
        self.addCleanup(setattr, cli, 'main', original)
        result = run_cli(['x'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Traceback', result.stderr)
        self.assertIn('RuntimeError: boom', result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_system_exit_values(self):
        import skillsync_workflow.cli as cli
        original = cli.main
        self.addCleanup(setattr, cli, 'main', original)
        for raised, code, text in ((None, 0, ''), (3, 3, ''), ('message', 1, 'message')):
            def fail(raised=raised):
                raise SystemExit(raised)
            cli.main = fail
            result = run_cli(['x'])
            self.assertEqual((result.returncode, result.stderr.strip()), (code, text), raised)

    def test_a_module_imported_from_the_working_directory_does_not_stay(self):
        import skillsync_workflow.cli as cli
        original = cli.main
        self.addCleanup(setattr, cli, 'main', original)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'cli_probe_module.py').write_text('VALUE = 1\n', encoding='utf-8')

            def load():
                sys.path.insert(0, tmp)
                import cli_probe_module   # noqa: F401
            cli.main = load
            run_cli(['x'], cwd=tmp)
        self.assertNotIn('cli_probe_module', sys.modules)


    def test_a_standard_library_module_imported_by_the_call_stays_loaded(self):
        import skillsync_workflow.cli as cli
        original = cli.main
        self.addCleanup(setattr, cli, 'main', original)
        sys.modules.pop('colorsys', None)

        def load():
            import colorsys   # noqa: F401
        cli.main = load
        run_cli(['x'])
        self.assertIn('colorsys', sys.modules)   # dropping it would give the next import a second copy

    def test_a_keyboard_interrupt_stops_the_run_and_restores_the_process(self):
        import skillsync_workflow.cli as cli
        original = cli.main
        before = self.snapshot()

        def interrupt():
            os.environ['SKILLSYNC_LEAK'] = '1'
            raise KeyboardInterrupt
        cli.main = interrupt
        self.addCleanup(setattr, cli, 'main', original)
        with self.assertRaises(KeyboardInterrupt):
            run_cli(['x'])
        self.assertEqual(self.snapshot(), before)

    def test_output_has_unix_line_ends_and_a_fixed_width(self):
        result = run_cli(['--help'])
        self.assertNotIn('\r', result.stdout)
        os.environ['COLUMNS'] = '200'
        self.addCleanup(os.environ.pop, 'COLUMNS', None)
        wide_terminal = run_cli(['--help'])
        self.assertEqual(wide_terminal.stdout, result.stdout)

    def test_the_package_stays_loaded(self):
        run_cli(['--version'])
        self.assertIn('skillsync_workflow.cli', sys.modules)


if __name__ == '__main__':
    unittest.main()
