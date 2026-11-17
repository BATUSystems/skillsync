"""180-A3: the end record of a run (`status`, `counts`, `source`) of the web tools, through the real `TestRecorder` fed with the recorded streams: a complete stream of
an adapter gives the counters of its tests, a counters-only adapter (Karma) its last counters, anything incomplete keeps the summary of the output."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

from skillsync_workflow import test_adapters, test_events, test_run  # noqa: E402


def run_through_recorder(fixture, argv=None):
    """The `end` event of a run whose output is the fixture, and all the events."""
    meta, chunks = ar.load(fixture)
    argv = list(argv or meta['command'])
    with tempfile.TemporaryDirectory(prefix='web-counts-') as tmp:
        root = Path(tmp)
        tool = test_run.detect_tool(argv)
        adapter = test_adapters.resolve('auto', argv, tool)
        recorder = test_run.TestRecorder(root, 'R1', 'F1.1', 'flag', adapter=adapter)
        check = {'argv': argv, 'command': ' '.join(argv), 'cwd': str(root)}
        recorder.started(check)
        texts = {'out': '', 'err': ''}
        for chunk in chunks:
            texts[chunk['fd']] += chunk['data']
        output = texts['out'] + texts['err']
        for fd in ('out', 'err'):
            for line in texts[fd].split('\n'):
                recorder.line(check, fd, line.rstrip('\r'))
        recorder.finished(check, {'exit_code': meta['exit'], 'duration_seconds': 1.0}, output)
        events = test_events.read_events(recorder.path)[0]
    return [e for e in events if e['t'] == 'end'][-1], events


class EndRecord(unittest.TestCase):
    def check(self, fixture, status, counts, source, argv=None):
        end, _ = run_through_recorder(fixture, argv)
        self.assertEqual((end['status'], end.get('counts'), end.get('source')), (status, counts, source), end)

    def test_a_complete_stream_gives_the_counters_of_its_tests(self):
        for fixture, status, counts in (
                ('vitest-verbose.jsonl', 'FAIL', {'total': 12, 'passed': 8, 'failed': 2, 'skipped': 2}),
                ('vitest-verbose-retry2.jsonl', 'FAIL', {'total': 10, 'passed': 6, 'failed': 2, 'skipped': 2}),
                ('playwright-list-retries2.jsonl', 'FAIL', {'total': 13, 'passed': 9, 'failed': 3, 'skipped': 1}),
                ('cypress-spec.jsonl', 'FAIL', {'total': 11, 'passed': 7, 'failed': 2, 'skipped': 2}),
                ('cypress-spec-chrome.jsonl', 'PASS', {'total': 1, 'passed': 1, 'failed': 0, 'skipped': 0}),
                ('angular-vitest-verbose.jsonl', 'FAIL', {'total': 6, 'passed': 4, 'failed': 1, 'skipped': 1})):
            with self.subTest(fixture=fixture):
                self.check(fixture, status, counts, 'adapter')

    def test_karma_gives_its_last_counters_with_and_without_junit_in_the_command(self):
        for fixture in ('karma-progress.jsonl', 'karma-dots.jsonl', 'karma-junit-progress.jsonl'):
            with self.subTest(fixture=fixture):
                self.check(fixture, 'FAIL', {'total': 11, 'passed': 7, 'failed': 2, 'skipped': 2}, 'adapter')   # the case of the legacy reader: it read `skipped 11`
        self.check('angular-karma-pass.jsonl', 'PASS', {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0}, 'adapter')
        self.check('angular-karma-mixed.jsonl', 'FAIL', {'total': 6, 'passed': 4, 'failed': 1, 'skipped': 1}, 'adapter')

    def test_a_reporter_without_test_lines_gives_the_counters_of_its_summary(self):
        for fixture, status, counts in (
                ('vitest-default.jsonl', 'FAIL', {'total': 12, 'passed': 8, 'failed': 2, 'skipped': 2}),
                ('vitest-default-color.jsonl', 'PASS', {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0}),
                ('playwright-line.jsonl', 'FAIL', {'total': 13, 'passed': 8, 'failed': 4, 'skipped': 1}),
                ('angular-vitest-pass.jsonl', 'PASS', {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0}),
                ('angular-vitest-mixed.jsonl', 'FAIL', {'total': 6, 'passed': 4, 'failed': 1, 'skipped': 1})):
            with self.subTest(fixture=fixture):
                self.check(fixture, status, counts, 'adapter')

    def test_an_interrupted_run_has_no_counters_of_an_adapter(self):
        for fixture in ('vitest-interrupted-verbose.jsonl', 'playwright-list-interrupted.jsonl', 'cypress-spec-interrupted.jsonl', 'angular-vitest-interrupted.jsonl',
                        'angular-karma-interrupted.jsonl'):
            with self.subTest(fixture=fixture):
                end, _ = run_through_recorder(fixture)
                self.assertNotEqual(end.get('source'), 'adapter', end)
                self.assertNotEqual(end['status'], 'PASS')

    def test_a_vitest_title_repeated_keeps_its_failure_in_the_counters(self):
        text = ' × f.test.js > d > same 1ms\n   → boom\n ✓ f.test.js > d > same 1ms\n\n Tests  1 failed | 1 passed (2)\n'
        with tempfile.TemporaryDirectory(prefix='web-counts-') as tmp:
            end, _ = self.feed_text(tmp, ['vitest', 'run', '--reporter=verbose'], text, 1)
        self.assertEqual((end['status'], end['counts']), ('FAIL', {'total': 2, 'passed': 1, 'failed': 1, 'skipped': 0}))

    def test_a_truncated_or_running_state_never_gives_counters(self):
        for mutate in ('truncated', 'running'):
            with self.subTest(mutate=mutate):
                recorder = self.recorder_with(['vitest', 'run', '--reporter=verbose'])
                writer = recorder.writer
                writer.test('a::t1', 't1', 'passed')
                if mutate == 'truncated':
                    writer.truncated = True
                else:
                    writer.test('a::t2', 't2', 'running')
                self.assertIsNone(recorder._adapter_counts(True))

    def test_counters_that_do_not_add_up_or_still_run_are_not_final(self):
        recorder = self.recorder_with(['karma', 'start'])
        recorder.writer.counts(passed=2, failed=0, skipped=0, running=1, total=3, force=True)
        self.assertIsNone(recorder._adapter_counts(False))
        recorder.writer.counts(passed=2, failed=0, skipped=0, running=0, total=3, force=True)
        self.assertIsNone(recorder._adapter_counts(False))
        recorder.writer.counts(passed=3, failed=0, skipped=0, running=0, total=3, force=True)
        self.assertEqual(recorder._adapter_counts(False), {'total': 3, 'passed': 3, 'failed': 0, 'skipped': 0})

    def recorder_with(self, argv):
        self.tmp = tempfile.TemporaryDirectory(prefix='web-counts-')
        self.addCleanup(self.tmp.cleanup)
        recorder = test_run.TestRecorder(self.tmp.name, 'R1', 'F1.1', 'flag', adapter=test_adapters.resolve('auto', argv, test_run.detect_tool(argv)))
        recorder.started({'argv': argv, 'command': ' '.join(argv), 'cwd': self.tmp.name})
        return recorder

    def feed_text(self, tmp, argv, text, exit_code):
        recorder = test_run.TestRecorder(tmp, 'R1', 'F1.1', 'flag', adapter=test_adapters.resolve('auto', argv, test_run.detect_tool(argv)))
        check = {'argv': argv, 'command': ' '.join(argv), 'cwd': tmp}
        recorder.started(check)
        for line in text.split('\n'):
            recorder.line(check, 'out', line)
        recorder.finished(check, {'exit_code': exit_code, 'duration_seconds': 1.0}, text)
        events = test_events.read_events(recorder.path)[0]
        return [e for e in events if e['t'] == 'end'][-1], events

    def test_every_green_run_is_a_pass_with_a_positive_count(self):
        for fixture in ('cypress-spec-chrome.jsonl', 'vitest-default-color.jsonl', 'angular-karma-pass.jsonl', 'angular-vitest-pass.jsonl'):
            with self.subTest(fixture=fixture):
                end, _ = run_through_recorder(fixture)
                self.assertEqual(end['status'], 'PASS', end)
                self.assertGreater(end['counts']['passed'], 0)


class KarmaReport(unittest.TestCase):
    """Counters always, the list when a JUnit report is there: a counters-only adapter (Karma, `ng test` with Karma) leaves the names to the report."""

    def run_karma(self, fixture, argv, junit=None, place=None):
        meta, chunks = ar.load(fixture)
        with tempfile.TemporaryDirectory(prefix='karma-report-') as tmp:
            if place:
                target = descend(tmp, place)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ar.FIXTURES / 'karma-junit.xml', target)
            adapter = test_adapters.resolve('auto', argv, test_run.detect_tool(argv))
            recorder = test_run.TestRecorder(tmp, 'R1', 'F1.1', 'flag', junit=[str(child(tmp, junit))] if junit else [], adapter=adapter)
            check = {'argv': argv, 'command': ' '.join(argv), 'cwd': tmp}
            recorder.started(check)
            text = ''.join(c['data'] for c in chunks)
            for line in text.split('\n'):
                recorder.line(check, 'out', line)
            recorder.finished(check, {'exit_code': meta['exit'], 'duration_seconds': 1.0}, text)
            events = test_events.read_events(recorder.path)[0]
        return [e for e in events if e['t'] == 'end'][-1], [e for e in events if e['t'] == 'test']

    def test_a_named_report_gives_the_list_and_the_same_counters(self):
        end, tests = self.run_karma('karma-junit-progress.jsonl', ['karma', 'start'], junit='karma-junit.xml', place='karma-junit.xml')
        self.assertEqual((end['status'], end['source'], end['counts']), ('FAIL', 'junit', {'total': 11, 'passed': 7, 'failed': 2, 'skipped': 2}))
        self.assertEqual(len(tests), 11)
        self.assertEqual(sorted({t['state'] for t in tests}), ['failed', 'passed', 'skipped'])
        self.assertTrue(any(t.get('msg') for t in tests if t['state'] == 'failed'))

    def test_the_default_place_of_karma_junit_reporter_is_found_without_the_flag(self):
        for argv in (['karma', 'start'], ['ng', 'test', '--no-watch']):
            with self.subTest(argv=argv):
                end, tests = self.run_karma('karma-junit-progress.jsonl', argv, place='junit-out/karma-junit.xml')
                self.assertEqual((end['source'], len(tests)), ('junit', 11))

    def test_without_a_report_there_is_no_list_and_the_counters_stay(self):
        end, tests = self.run_karma('karma-progress.jsonl', ['karma', 'start'])
        self.assertEqual((end['source'], end['counts']['total'], tests), ('adapter', 11, []))

    def test_the_notice_says_how_to_get_the_list_until_a_report_is_named(self):
        for argv in (['karma', 'start'], ['ng', 'test']):
            tool = test_run.detect_tool(argv)
            adapter = test_adapters.resolve('auto', argv, tool)
            without = test_run.adapter_notice('auto', argv, tool, adapter)
            self.assertIn('--junit', without)
            self.assertIn('junit reporter', without)
            self.assertNotIn('--junit', test_run.adapter_notice('auto', argv, tool, adapter, ['report.xml']))


if __name__ == '__main__':
    unittest.main()
