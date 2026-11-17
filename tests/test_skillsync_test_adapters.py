"""180-A stage 2: the interface of the output adapters (registry, `--format`, the hooks of the recorder, the harness)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import guard_real_state, home_env  # noqa: E402
from adapter_harness import ROOT, run_adapter, tests_of  # noqa: E402

import subprocess  # noqa: E402

from skillsync_workflow import quality, test_adapters, test_events as ev, test_run  # noqa: E402
from skillsync_workflow.test_adapters import Adapter, register  # noqa: E402

CLI = ROOT / 'skillsync'


class SampleAdapter(Adapter):
    """A toy adapter of the lines `RESULT <id> <state>` (the test of the interface, not a tool)."""
    name = 'sample-inproc'
    tools = ('sample-tool',)
    live = 'completion'

    def env(self):
        return {'SAMPLE_ADAPTER': '1'}

    def begin(self, ctx):
        ctx.emit.collected(2, ['a', 'b'])

    def partial(self, stream, text, ctx):
        if text.startswith('START '):
            ctx.emit.test(text.split(' ', 1)[1].strip(), text.split(' ', 1)[1].strip(), 'running')

    def feed(self, stream, text, ctx):
        parts = text.split(' ', 2)
        if parts[0] == 'RESULT' and len(parts) == 3:
            _, test_id, state = parts
            ctx.emit.test(test_id, test_id, state, group='g', ms=1, msg='boom' if state == 'failed' else None)
        elif text.startswith('RAISE'):
            raise RuntimeError('adapter bug')

    def end(self, ctx, exit_code, output, reports):
        return 'ALLDONE' in output


def setUpModule():
    """The toy adapter lives in the global registry only while this module runs (one process may run many modules: a leak made other tests see it)."""
    register(SampleAdapter)


def tearDownModule():
    test_adapters.REGISTRY.pop(SampleAdapter.name, None)


class Registry(unittest.TestCase):
    def test_register_discover_and_resolve(self):
        self.assertIn('sample-inproc', test_adapters.names())
        self.assertIsInstance(test_adapters.resolve('sample-inproc', ['x'], 'x'), SampleAdapter)
        self.assertIsInstance(test_adapters.resolve('auto', ['x'], 'sample-tool'), SampleAdapter)
        self.assertIsNone(test_adapters.resolve('auto', ['x'], 'other-tool'))
        self.assertIsNone(test_adapters.resolve('none', ['x'], 'sample-tool'))
        with self.assertRaises(ValueError) as caught:
            test_adapters.resolve('nope', ['x'], 'x')
        self.assertIn('sample-inproc', str(caught.exception))

    def test_bad_and_duplicate_names_are_refused(self):
        for bad in ('', 'auto', 'none'):
            with self.assertRaises(ValueError):
                register(type('Bad', (Adapter,), {'name': bad}))
        with self.assertRaises(ValueError):
            register(type('Dup', (Adapter,), {'name': 'sample-inproc'}))

    def test_a_broken_module_in_the_package_does_not_break_the_others(self):
        package = Path(test_adapters.__file__).parent
        broken = package / 'zz_broken_for_test.py'
        broken.write_text('raise RuntimeError("broken")\n', encoding='utf-8')
        try:
            test_adapters._DISCOVERED = False
            self.assertIn('sample-inproc', test_adapters.names())
        finally:
            broken.unlink()
            test_adapters._DISCOVERED = False


class Harness(unittest.TestCase):
    def test_events_of_an_adapter_over_a_fixture(self):
        result = run_adapter('sample-inproc', ['RESULT t1 running', 'RESULT t1 passed', 'RESULT t2 failed', 'noise'], ['RESULT t3 skipped'], argv=['sample-tool'])
        self.assertEqual(tests_of(result), [('t1', 'passed'), ('t2', 'failed'), ('t3', 'skipped')])
        self.assertEqual(result['events'][0]['t'], 'collected')
        self.assertEqual(result['env'], {'SAMPLE_ADAPTER': '1'})
        self.assertFalse(result['skip_reports'])

    def test_a_garbage_format_emits_no_tests_and_never_raises(self):
        result = run_adapter('sample-inproc', ['\x00\x01 garbage', '', 'RESULT', 'RESULT only-id'], argv=['sample-tool'])
        self.assertEqual(tests_of(result), [])

    def test_end_may_declare_that_every_test_was_reported(self):
        self.assertTrue(run_adapter('sample-inproc', ['ALLDONE'], argv=['sample-tool'])['skip_reports'])


CLI_ADAPTER = '''
from skillsync_workflow.test_adapters import Adapter, register


@register
class SampleCli(Adapter):
    name = 'sample-lines'
    tools = ()

    def env(self):
        return {'SAMPLE_ADAPTER': '1'}

    def partial(self, stream, text, ctx):
        if text.startswith('START '):
            ctx.emit.test(text.split(' ', 1)[1].strip(), text.split(' ', 1)[1].strip(), 'running')

    def feed(self, stream, text, ctx):
        parts = text.split(' ', 2)
        if parts[0] == 'RESULT' and len(parts) == 3:
            ctx.emit.test(parts[1], parts[1], parts[2], group='g')
        elif parts[0] == 'RAISE':
            raise RuntimeError('adapter bug')
'''


class InTheRecorder(unittest.TestCase):
    def setUp(self):
        module = Path(test_adapters.__file__).parent / 'zz_sample_for_cli_test.py'
        module.write_text(CLI_ADAPTER, encoding='utf-8')
        self.addCleanup(lambda: module.unlink())
        self.tmp = tempfile.mkdtemp(prefix='adapters-run-')
        guard_real_state(self)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp) / 'p'
        (self.root / '.github').mkdir(parents=True)
        (self.root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
        (self.root / '.skillsync/runs/R1').mkdir(parents=True)
        self.env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **home_env(self.tmp), 'SKILLSYNC_CONFIG': f'{self.tmp}/cfg.yaml'}

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', 'F1.1', *args], capture_output=True, text=True, env=self.env, timeout=60)

    def attempt(self):
        return ev.read_attempt(self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl')

    def test_unknown_format_is_blocked_before_the_command_runs(self):
        done = self.run_cli('--format', 'nope', '--', sys.executable, '-c', "open('ran', 'w').write('x')")
        self.assertEqual(done.returncode, 2)
        self.assertIn('unknown --format', done.stderr)
        self.assertFalse((self.root / 'ran').exists())

    def test_format_none_and_no_adapter_behave_as_in_stage_1(self):
        done = self.run_cli('--format', 'none', '--', sys.executable, '-c', "print('Ran 2 tests in 0s'); print('OK')")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.attempt().tests, {})
        self.assertNotIn('adapter', self.attempt().start)

    def test_the_start_of_a_test_seen_in_the_unfinished_line_becomes_running(self):
        script = "import sys, time; sys.stdout.write('START t1'); sys.stdout.flush(); time.sleep(0.6); sys.stdout.write('\\nRESULT t1 passed\\n'); print('Ran 1 test in 0s'); print('OK')"
        self.run_cli('--format', 'sample-lines', '--', sys.executable, '-c', script)
        events = [e for e in ev.read_events(self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl')[0] if e['t'] == 'test']
        self.assertEqual([(e['id'], e['state']) for e in events], [('t1', 'running'), ('t1', 'passed')])

    def test_the_liveness_of_the_adapter_is_recorded_and_reaches_the_page_data(self):
        from skillsync_workflow import dashboard_tests as dt
        self.run_cli('--format', 'sample-lines', '--', sys.executable, '-c', "print('RESULT a passed'); print('Ran 1 test in 0s'); print('OK')")
        self.assertEqual(self.attempt().start['live'], 'running')   # the default of the base class
        tests, _ = dt.build_tests(self.root, 'R1')
        self.assertEqual(tests['sections'][0]['attempts'][0]['live'], 'running')
        self.assertEqual(test_adapters.resolve('sample-inproc', ['x'], 'x').live, 'completion')

    def test_a_forced_adapter_reads_the_lines_and_adds_its_env(self):
        script = "import os; print('RESULT a passed'); print('RESULT b failed'); print('Ran 2 tests in 0s'); print('FAILED (failures=1)'); raise SystemExit(1 if os.environ.get('SAMPLE_ADAPTER') == '1' else 0)"
        done = self.run_cli('--format', 'sample-lines', '--', sys.executable, '-c', script)
        self.assertEqual(done.returncode, 1)   # the env of the adapter reached the command
        state = self.attempt()
        self.assertEqual({k: v['state'] for k, v in state.tests.items()}, {'a': 'passed', 'b': 'failed'})
        self.assertEqual(state.start['adapter'], 'sample-lines')
        self.assertEqual(state.counts['failed'], 1)

    def test_an_adapter_that_raises_is_dropped_and_the_run_goes_on(self):
        script = "print('RESULT a passed'); print('RAISE'); print('RESULT b failed'); print('Ran 2 tests in 0s'); print('OK')"
        done = self.run_cli('--format', 'sample-lines', '--', sys.executable, '-c', script)
        self.assertEqual(done.returncode, 0)
        state = self.attempt()
        self.assertEqual(list(state.tests), ['a'])   # nothing after the exception; the run is still recorded
        self.assertEqual(state.end['status'], 'PASS')

    def test_the_output_and_the_exit_code_of_the_command_are_not_touched(self):
        done = self.run_cli('--format', 'sample-lines', '--', sys.executable, '-c', "print('RESULT a passed'); print('visible line'); raise SystemExit(7)")
        self.assertEqual(done.returncode, 7)
        self.assertIn('visible line', done.stdout)
        self.assertIn('RESULT a passed', done.stdout)


if __name__ == '__main__':
    unittest.main()
