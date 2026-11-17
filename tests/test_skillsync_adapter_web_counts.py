"""180-A3: the end record of a run (`status`, `counts`, `source`) of the web tools, through the real `TestRecorder` fed with the recorded streams: a complete stream of
an adapter gives the counters of its tests, a counters-only adapter (Karma) its last counters, anything incomplete keeps the summary of the output."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402

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

    def test_karma_gives_its_last_counters_with_and_without_the_report(self):
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

    def test_every_green_run_is_a_pass_with_a_positive_count(self):
        for fixture in ('cypress-spec-chrome.jsonl', 'vitest-default-color.jsonl', 'angular-karma-pass.jsonl', 'angular-vitest-pass.jsonl'):
            with self.subTest(fixture=fixture):
                end, _ = run_through_recorder(fixture)
                self.assertEqual(end['status'], 'PASS', end)
                self.assertGreater(end['counts']['passed'], 0)


if __name__ == '__main__':
    unittest.main()
