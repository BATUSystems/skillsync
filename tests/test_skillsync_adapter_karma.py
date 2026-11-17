"""180-A3: the adapter of Karma 6.4.4 + Jasmine on recorded streams (`progress` with rewritten lines and colours, `dots`, an interrupted run, the junit report)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'karma.py'


def counts(recorder):
    return [c[1] for c in recorder.calls if c[0] == 'counts']


def setUpModule():
    test_adapters.discover()   # a single test, or another order, finds the registry filled


class Registration(unittest.TestCase):
    def test_name_liveness_and_selection(self):
        for argv, tool in ((['karma', 'start', 'karma.conf.js'], 'karma'), (['./node_modules/.bin/karma', 'start', '--single-run'], 'karma')):
            with self.subTest(argv=argv):
                adapter = test_adapters.resolve('auto', argv, tool)
                self.assertEqual((adapter.name, adapter.live), ('karma', 'counts'))   # only numbers while the run goes
                self.assertEqual(adapter.env(), {})

    def test_auto_selects_through_the_tool_of_the_command(self):
        from skillsync_workflow import test_run
        self.assertEqual(test_run.detect_tool(['karma', 'start']), 'karma')
        self.assertEqual(test_adapters.resolve('auto', ['karma', 'start'], test_run.detect_tool(['karma', 'start'])).name, 'karma')
        self.assertEqual(test_adapters.resolve('karma', ['npx', 'karma', 'start'], 'npx').name, 'karma')   # `--format karma` works under any wrapper

    def test_other_commands_are_not_taken(self):
        cls = test_adapters.REGISTRY['karma']
        for argv, expected in ((['ng', 'test'], False), (['npx', 'vitest'], False), (['npm', 'test'], False), (['karma', 'start'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class Progress(unittest.TestCase):
    def test_counters_follow_the_rewritten_lines_and_end_at_the_totals(self):
        recorder, _, _, complete = replay_fixture('karma', 'karma-progress.jsonl')
        series = counts(recorder)
        self.assertEqual(series[-1], {'passed': 7, 'failed': 2, 'skipped': 2, 'running': 0, 'total': 11})   # TOTAL: 2 FAILED, 7 SUCCESS, 2 skipped, 11 specs
        self.assertEqual(series[0]['total'], 11)
        self.assertEqual([s['passed'] + s['failed'] + s['skipped'] for s in series][-1], 11)
        self.assertFalse(complete)   # the names come from the junit report
        self.assertEqual([c for c in recorder.calls if c[0] == 'test'], [])   # a failure line cannot be split into the `classname` and `name` of the report

    def test_executed_does_not_count_the_skipped_specs(self):
        text = 'Chrome Headless 1 (Linux 0): Executed 4 of 11 (1 FAILED) (skipped 2) (0 secs / 1 secs)\n'
        self.assertEqual(counts(replay('karma', chunks_from_text(text), tool='karma')[0]), [{'passed': 3, 'failed': 1, 'skipped': 2, 'running': 5, 'total': 11}])

    def test_the_same_result_for_any_chunking_and_for_cursor_sequences(self):
        meta, chunks = load('karma-progress.jsonl')
        want = replay_fixture('karma', 'karma-progress.jsonl')[0].calls
        for size in (1, 5, 64, 4096):
            with self.subTest(size=size):
                self.assertEqual(replay('karma', rechunk(chunks, size), argv=meta['command'], tool='karma')[0].calls, want)
        text = ''.join(c['data'] for c in chunks)
        self.assertIn('\x1b[1A\x1b[2K', text)   # the lines are rewritten with cursor control sequences
        self.assertIn('\x1b[32m', text)         # and the log lines are coloured

    def test_several_browsers_are_summed(self):
        text = ('Chrome 1 (Linux): Executed 3 of 10 SUCCESS (0 secs / 1 secs)\nFirefox 2 (Linux): Executed 5 of 10 (1 FAILED) (0 secs / 1 secs)\n'
                'Chrome 1 (Linux): Executed 6 of 10 SUCCESS (0 secs / 1 secs)\n')
        last = counts(replay('karma', chunks_from_text(text), tool='karma')[0])[-1]
        self.assertEqual(last, {'passed': 10, 'failed': 1, 'skipped': 0, 'running': 9, 'total': 20})

    def test_an_interrupted_run_keeps_the_counters_it_reached(self):
        series = counts(replay_fixture('karma', 'karma-interrupted-progress.jsonl')[0])
        self.assertEqual(series[-1], {'passed': 3, 'failed': 1, 'skipped': 2, 'running': 5, 'total': 11})   # `Executed 4 of 11 (1 FAILED) (skipped 2)` and no more

    def test_other_shapes_of_the_line(self):
        tail = ' (0 secs / 1 secs)\n'
        for text, want in (('Chrome 1 (Linux 0): Executed 0 of 0 SUCCESS' + tail, {'passed': 0, 'failed': 0, 'skipped': 0, 'running': 0, 'total': 0}),
                           ('Chrome 1 (Linux 0): Executed 3 of 8 (skipped 2) SUCCESS' + tail, {'passed': 3, 'failed': 0, 'skipped': 2, 'running': 3, 'total': 8}),
                           ('Chrome 1 (Linux 0): Executed 3 of 8 ERROR' + tail, {'passed': 3, 'failed': 0, 'skipped': 0, 'running': 5, 'total': 8}),
                           ('Chrome 1 (Linux 0): Executed 2 of 8 DISCONNECTED' + tail, {'passed': 2, 'failed': 0, 'skipped': 0, 'running': 6, 'total': 8}),
                           ('Chrome 1 (Linux 0): Executed 5 of 8 (3 FAILED) (skipped 1) (4.2 secs / 3.9 secs)\n', {'passed': 2, 'failed': 3, 'skipped': 1, 'running': 2, 'total': 8})):
            with self.subTest(text=text[:40]):
                self.assertEqual(counts(replay('karma', chunks_from_text(text), tool='karma')[0]), [want])

    def test_lines_that_only_look_like_the_counter_are_not_counters(self):
        for text in ("Chrome 1 (Linux 0) LOG: 'Job: Executed 5 of 8 steps'\n", "Chrome 1 (Linux 0) math x: Executed 1 of 2 FAILED\n", "WARN [web-server]: Executed 5 of 8\n",
                     "Chrome 1 (Linux 0): Executed 5 of 8 steps done\n"):
            with self.subTest(text=text[:40]):
                self.assertEqual(replay('karma', chunks_from_text(text), tool='karma')[0].calls, [])


class Dots(unittest.TestCase):
    def test_the_dots_reporter_has_no_counters_while_running_so_only_the_final_line_counts(self):
        recorder = replay_fixture('karma', 'karma-dots.jsonl')[0]
        self.assertEqual([c for c in recorder.calls if c[0] == 'test'], [])
        self.assertEqual(counts(recorder), [{'passed': 7, 'failed': 2, 'skipped': 2, 'running': 0, 'total': 11}])   # the final `Executed 9 of 11 ...` line only


class Report(unittest.TestCase):
    def test_the_junit_report_gives_the_names(self):
        report = {r['id']: r['state'] for r in test_reports.parse_junit(ar.FIXTURES / 'karma-junit.xml')}
        self.assertEqual(len(report), 11)
        self.assertEqual(report['math::math fails with a message'], 'failed')
        self.assertEqual(report['math nested::math nested deep ok'], 'passed')
        self.assertEqual(sorted(set(report.values())), ['failed', 'passed', 'skipped'])


class Hostile(unittest.TestCase):
    def test_garbage_and_long_lines_emit_nothing(self):
        for text in ('', 'Executed of\n', ': Executed 1 of 2\n', 'x' * 100000 + ': Executed 1 of 2 ' + 'y' * 100000 + '\n'):
            with self.subTest(text=text[:20]):
                self.assertEqual(len(replay('karma', chunks_from_text(text, size=777), tool='karma')[0].calls), 0)

    def test_the_number_of_browsers_is_bounded(self):
        from skillsync_workflow.test_adapters import karma
        text = ''.join(f'B{i} (Linux {i}): Executed 1 of 2 SUCCESS (0 secs / 0 secs)\n' for i in range(karma.MAX_BROWSERS + 30))
        series = counts(replay('karma', chunks_from_text(text, size=65536), tool='karma')[0])
        self.assertEqual(series[-1]['total'], 2 * karma.MAX_BROWSERS)


if __name__ == '__main__':
    unittest.main()
