"""180-A3: `ng test` runs Vitest or Karma, chosen by the project: the `angular` adapter reads the lines and lets whichever tool prints emit. The fixtures
`angular-*.jsonl` are real streams of `ng test` (Angular CLI 21.2.30, one project per engine, recorded through pipes)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'angular.py'


class Selection(unittest.TestCase):
    def test_ng_test_selects_the_angular_adapter_and_the_others_do_not_take_it(self):
        adapter = test_adapters.resolve('auto', ['ng', 'test', '--no-watch'], 'angular')
        self.assertEqual((adapter.name, adapter.live), ('angular', 'counts'))
        for name in ('vitest', 'karma', 'cypress', 'playwright'):
            self.assertFalse(test_adapters.REGISTRY[name]().accepts(['ng', 'test']))
        self.assertTrue(test_adapters.REGISTRY['angular']().accepts(['npx', 'ng', 'test']))   # `accepts` looks for the command, whatever wraps it
        self.assertFalse(test_adapters.REGISTRY['angular']().accepts(['ng', 'build']))

    def test_npx_and_the_short_alias_and_the_wrong_subcommands(self):
        self.assertEqual(test_adapters.resolve('angular', ['npx', 'ng', 'test'], 'npx').name, 'angular')   # `--format angular` works under any wrapper
        cls = test_adapters.REGISTRY['angular']
        for argv, expected in ((['ng', 't'], True), (['ng', 'generate', 'component', 'test'], False), (['ng.cmd', 'test'], True), (['npx', 'vitest', 'test'], False), (['ng'], False)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_format_forces_one_of_the_engines(self):
        self.assertEqual(test_adapters.resolve('vitest', ['ng', 'test'], 'angular').name, 'vitest')
        self.assertEqual(test_adapters.resolve('karma', ['ng', 'test'], 'angular').name, 'karma')

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


def tests_of(recorder):
    return [c for c in recorder.calls if c[0] == 'test']


def counts_of(recorder):
    return [c[1] for c in recorder.calls if c[0] == 'counts']


class Engines(unittest.TestCase):
    def test_a_vitest_run_gives_the_tests_and_the_report_is_not_needed(self):
        recorder, _, _, complete = replay_fixture('angular', 'vitest-verbose.jsonl', argv=['ng', 'test'], tool='angular')
        direct = replay_fixture('vitest', 'vitest-verbose.jsonl')[0]
        self.assertEqual(recorder.calls, direct.calls)
        self.assertTrue(complete)

    def test_a_karma_run_gives_the_counters(self):
        recorder, _, _, complete = replay_fixture('angular', 'karma-progress.jsonl', argv=['ng', 'test'], tool='angular')
        direct = replay_fixture('karma', 'karma-progress.jsonl')[0]
        self.assertEqual(recorder.calls, direct.calls)
        self.assertFalse(complete)

    def test_an_unknown_reporter_emits_nothing(self):
        self.assertEqual(replay('angular', chunks_from_text('Application bundle generation complete.\nsome other reporter 3 of 4\n'), argv=['ng', 'test'], tool='angular')[0].calls, [])


class RealNgTest(unittest.TestCase):
    """Streams of a real `ng test`: Vitest (the default runner of Angular 21) and Karma."""

    def run_fixture(self, name):
        return replay_fixture('angular', name, argv=['ng', 'test', '--no-watch'], tool='angular')

    def test_the_default_vitest_reporter_prints_no_test_line_so_only_the_summary_counters_are_emitted(self):
        for name, counts in (('angular-vitest-pass.jsonl', {'passed': 2, 'failed': 0, 'skipped': 0, 'running': 0, 'total': 2}),
                             ('angular-vitest-mixed.jsonl', {'passed': 4, 'failed': 1, 'skipped': 1, 'running': 0, 'total': 6})):
            with self.subTest(name=name):
                recorder, _, _, complete = self.run_fixture(name)
                self.assertEqual(recorder.calls, [('counts', counts)])   # no `test` event: the list needs `--reporters=verbose`
                self.assertFalse(complete)

    def test_verbose_vitest_ids_do_not_carry_the_project_prefix(self):
        recorder, _, _, complete = self.run_fixture('angular-vitest-verbose.jsonl')
        states = {c[1]: c[3] for c in tests_of(recorder) if c[3] != 'failed' or c[6]}
        self.assertEqual(states, {
            'src/app/mix.spec.ts::Mix > passes one': 'passed',
            'src/app/mix.spec.ts::Mix > fails on purpose': 'failed',
            'src/app/mix.spec.ts::Mix > is skipped': 'skipped',
            'src/app/mix.spec.ts::Mix > nested group > passes nested': 'passed',
            'src/app/app.spec.ts::App > should create the app': 'passed',
            'src/app/app.spec.ts::App > should render title': 'passed'})
        self.assertTrue(all(not c[1].startswith('|') for c in tests_of(recorder)))
        failure = [c for c in tests_of(recorder) if c[3] == 'failed' and c[6]]
        self.assertEqual(failure[0][6], 'expected 4 to be 5 // Object.is equality')
        self.assertTrue(complete)

    def test_an_interrupted_verbose_run_keeps_what_ended_and_is_not_complete(self):
        recorder, _, _, complete = self.run_fixture('angular-vitest-interrupted.jsonl')
        self.assertTrue(tests_of(recorder))
        self.assertFalse(complete)

    def test_karma_counters_of_a_green_and_a_mixed_run(self):
        green = counts_of(self.run_fixture('angular-karma-pass.jsonl')[0])
        self.assertEqual(green[-1], {'passed': 2, 'failed': 0, 'skipped': 0, 'running': 0, 'total': 2})
        mixed = counts_of(self.run_fixture('angular-karma-mixed.jsonl')[0])
        self.assertEqual(mixed[-1], {'passed': 4, 'failed': 1, 'skipped': 1, 'running': 0, 'total': 6})
        self.assertEqual(max(c['failed'] for c in mixed), 1)

    def test_a_karma_run_interrupted_before_the_first_test_gives_no_counters(self):
        recorder, _, _, complete = self.run_fixture('angular-karma-interrupted.jsonl')
        self.assertEqual(recorder.calls, [])
        self.assertFalse(complete)

    def test_the_chunking_of_the_pipe_does_not_change_the_result(self):
        for name in ('angular-vitest-verbose.jsonl', 'angular-karma-mixed.jsonl'):
            whole = self.run_fixture(name)[0].calls
            for size in (1, 7, 64):
                with self.subTest(name=name, size=size):
                    chunks = ar.rechunk(load(name)[1], size)
                    self.assertEqual(replay('angular', chunks, argv=['ng', 'test'], tool='angular')[0].calls, whole)


if __name__ == '__main__':
    unittest.main()
