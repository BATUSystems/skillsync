"""180-A3: the adapter of Playwright Test 1.59.1 on recorded streams (two projects x two workers, retries, `line`, an interrupted run, the junit report)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'playwright.py'
D, MOB = '[chromium-desktop]', '[chromium-mobile]'
M = 'e2e/math.spec.js::math › '


def states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


class Registration(unittest.TestCase):
    def test_name_liveness_and_selection(self):
        for argv, tool in ((['playwright', 'test'], 'playwright'), (['npx', 'playwright', 'test', '--project=x'], 'npx'), (['./node_modules/.bin/playwright', 'test'], 'playwright')):
            with self.subTest(argv=argv):
                adapter = test_adapters.resolve('auto', argv, tool)
                self.assertEqual((adapter.name, adapter.live), ('playwright', 'completion'))   # no start line in a pipe
                self.assertEqual(adapter.env(), {})

    def test_other_commands_are_not_taken(self):
        cls = test_adapters.REGISTRY['playwright']
        for argv, expected in ((['npx', 'playwright', 'install'], False), (['npx', 'playwright', 'show-report'], False), (['npx', 'vitest'], False), (['playwright', 'test'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class List(unittest.TestCase):
    def test_two_projects_and_two_workers_every_test_once_per_project(self):
        recorder, _, _, complete = replay_fixture('playwright', 'playwright-list.jsonl')
        final = states(recorder)
        self.assertEqual(len(final), 26)   # 13 tests x 2 projects, none collapsed
        self.assertTrue(complete)          # 26 lines = the 8 failed + 2 skipped + 16 passed of the summary
        for project in (D, MOB):
            suffix = f' {project}'
            self.assertEqual(final[M + 'adds' + suffix], 'passed')
            self.assertEqual(final[M + 'is skipped' + suffix], 'skipped')
            self.assertEqual(final[M + 'fails with a message' + suffix], 'failed')
            self.assertEqual(final['e2e/math.spec.js::times out' + suffix], 'failed')
            self.assertEqual(final['e2e/second.spec.js::serial pair › serial first' + suffix], 'passed')
            self.assertEqual(final[M + 'żółć – unicode name' + suffix], 'passed')
        self.assertEqual(sum(1 for v in final.values() if v == 'failed'), 8)

    def test_no_running_state_and_the_lines_of_the_projects_interleave(self):
        recorder = replay_fixture('playwright', 'playwright-list.jsonl')[0]
        self.assertNotIn('running', {c[3] for c in recorder.tests()})
        projects = [c[1].rsplit(' ', 1)[-1] for c in recorder.tests() if c[5] is not None]
        self.assertTrue(D in projects and MOB in projects)

    def test_the_message_of_a_failure_comes_from_the_detailed_block(self):
        recorder = replay_fixture('playwright', 'playwright-list.jsonl')[0]
        msg = recorder.last()[M + 'fails with a message ' + D][1]
        self.assertIn('Error: expect(received).toBe(expected)', msg)
        self.assertLessEqual(len(msg.split('\n')), 3)
        self.assertNotIn('\x1b', msg)

    def test_a_flaky_test_that_passed_on_a_retry_is_passed_and_stays_passed(self):
        recorder = replay_fixture('playwright', 'playwright-list-retries2.jsonl')[0]
        key = M + 'flaky on the first attempt ' + D
        self.assertEqual([c[3] for c in recorder.tests() if c[1] == key][:2], ['failed', 'passed'])
        self.assertEqual(states(recorder)[key], 'passed')   # the failure block of the first attempt must not turn it red again
        self.assertEqual(states(recorder)[M + 'fails with a message ' + D], 'failed')

    def test_the_same_result_for_any_chunking(self):
        meta, chunks = load('playwright-list.jsonl')
        want = replay_fixture('playwright', 'playwright-list.jsonl')[0].calls
        for size in (1, 7, 100, 5000):
            with self.subTest(size=size):
                self.assertEqual(replay('playwright', rechunk(chunks, size), argv=meta['command'], tool='playwright')[0].calls, want)

    def test_a_killed_run_has_no_summary_so_the_report_decides(self):
        recorder, _, _, complete = replay_fixture('playwright', 'playwright-list-interrupted.jsonl')
        self.assertFalse(complete)
        self.assertTrue(recorder.tests())
        self.assertEqual(set(states(recorder).values()) - {'passed', 'failed', 'skipped'}, set())


class Others(unittest.TestCase):
    def test_the_line_reporter_prints_the_end_of_a_test_without_its_result_so_nothing_is_emitted(self):
        recorder, _, _, complete = replay_fixture('playwright', 'playwright-line.jsonl')
        self.assertEqual([c for c in recorder.calls if c[0] == 'test'], [])
        self.assertFalse(complete)

    def test_the_junit_report_loses_the_project(self):
        report = test_reports.parse_junit(ar.FIXTURES / 'playwright-junit.xml')
        ids = [r['id'] for r in report]
        self.assertEqual(len(ids), 26)
        self.assertEqual(len(set(ids)), 13)   # the same id for both projects: why the stream (with the project in the id) is the better source when it is complete


class Hostile(unittest.TestCase):
    def test_garbage_and_long_lines_emit_nothing(self):
        for text in ('', '  ✓ \n', '  ✓   1 [a] › :1:1 › x\n', '  ✘   1 ' + '[' * 5000 + '\n', '  ✓   1 [p] › f.js:1:1 › ' + 'a › ' * 20000 + 'x (1ms)\n'):
            with self.subTest(text=text[:20]):
                self.assertLessEqual(len(replay('playwright', chunks_from_text(text, size=999), tool='playwright')[0].calls), 1)

    def test_a_summary_line_inside_a_test_name_is_not_a_summary(self):
        text = '  ✓   1 [p] › f.js:1:1 › 3 failed (5ms)\n  1 passed (1.0s)\n'
        recorder, _, _, complete = replay('playwright', chunks_from_text(text), tool='playwright')
        self.assertEqual(states(recorder), {'f.js::3 failed [p]': 'passed'})
        self.assertTrue(complete)


if __name__ == '__main__':
    unittest.main()
