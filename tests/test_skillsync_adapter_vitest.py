"""180-A3: the adapter of Vitest 4.1.12 on recorded streams (parallel files, a failure, a skip, a todo, parameterized tests, retries, an interrupted run)."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'vitest.py'
M, S = 'tests/math.test.js::math > ', 'tests/second.test.js::'
EXPECTED = {
    M + 'adds': 'passed', M + 'subtracts': 'passed', M + 'is skipped': 'skipped', M + 'is a todo': 'skipped', M + 'fails with a message': 'failed',
    M + 'nested > deep ok': 'passed', M + 'adds 1 + 1 = 2': 'passed', M + 'adds 2 + 3 = 5': 'passed', M + 'adds 2 + 2 = 5': 'failed',
    M + 'żółć – unicode name': 'passed', S + 'second file ok': 'passed', S + 'second file flaky': 'passed',
}


def states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


class Registration(unittest.TestCase):
    def test_name_liveness_and_selection(self):
        for argv, tool in ((['vitest', 'run'], 'vitest'), (['/x/node_modules/.bin/vitest', 'run'], 'vitest')):
            with self.subTest(argv=argv):
                adapter = test_adapters.resolve('auto', argv, tool)
                self.assertEqual((adapter.name, adapter.live), ('vitest', 'completion'))   # no reporter prints a line when a test starts
                self.assertEqual(adapter.env(), {})

    def test_other_commands_are_not_taken(self):
        cls = test_adapters.REGISTRY['vitest']
        for argv, expected in ((['npx', 'jest'], False), (['ng', 'test'], False), (['karma', 'start'], False), (['npm', 'test'], False), (['vitest'], True), (['npx', 'vitest'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class Verbose(unittest.TestCase):
    def test_final_states_ids_groups_and_the_message(self):
        recorder, _, _, complete = replay_fixture('vitest', 'vitest-verbose.jsonl')
        self.assertEqual(states(recorder), EXPECTED)
        self.assertTrue(complete)   # 12 lines = the 12 of the summary: the report is not needed
        self.assertEqual({c[4] for c in recorder.tests()}, {'tests/math.test.js', 'tests/second.test.js'})
        self.assertEqual(recorder.last()[M + 'fails with a message'][1], 'expected 4 to be 5 // Object.is equality')
        self.assertEqual(recorder.last()[M + 'is a todo'][1], 'todo')

    def test_no_running_state_exists_and_the_files_interleave(self):
        recorder = replay_fixture('vitest', 'vitest-verbose.jsonl')[0]
        self.assertNotIn('running', {c[3] for c in recorder.tests()})
        order = [c[1].split('::')[0] for c in recorder.tests()]
        self.assertNotEqual(order, sorted(order))   # a line of the second file arrives between the lines of the first one

    def test_ids_equal_the_ids_of_the_junit_report(self):
        report = {r['id']: r['state'] for r in test_reports.parse_junit(ar.FIXTURES / 'vitest-junit.xml')}
        self.assertEqual(report, EXPECTED)

    def test_the_same_result_for_any_chunking(self):
        meta, chunks = load('vitest-verbose.jsonl')
        want = replay_fixture('vitest', 'vitest-verbose.jsonl')[0].calls
        rng = random.Random(7)
        for size in (1, 3, 7, 50, 1000):
            with self.subTest(size=size):
                self.assertEqual(replay('vitest', rechunk(chunks, size), argv=meta['command'], tool='vitest')[0].calls, want)
        shuffled = []
        for chunk in chunks:
            text, pos = chunk['data'], 0
            while pos < len(text):
                step = rng.randint(1, 40)
                shuffled.append({**chunk, 'data': text[pos:pos + step]})
                pos += step
        self.assertEqual(replay('vitest', shuffled, argv=meta['command'], tool='vitest')[0].calls, want)

    def test_a_retried_test_is_one_line_with_the_total_time(self):
        recorder = replay_fixture('vitest', 'vitest-verbose-retry2.jsonl')[0]
        failed = [c for c in recorder.tests() if c[1] == M + 'fails with a message' and c[3] == 'failed']
        self.assertTrue(failed and failed[0][5] > 1000)   # 3 attempts of 400 ms in one line (retry x2)
        self.assertEqual(recorder.last()[M + 'fails with a message'][1], 'expected 4 to be 5 // Object.is equality')
        self.assertTrue(replay_fixture('vitest', 'vitest-verbose-retry2.jsonl')[3])   # 10 lines = the 10 of the summary
        self.assertEqual(states(recorder)[M + 'fails with a message'], 'failed')

    def test_a_run_killed_in_the_middle_never_shows_the_hanging_test(self):
        recorder, _, _, complete = replay_fixture('vitest', 'vitest-interrupted-verbose.jsonl')
        self.assertEqual(states(recorder), {'tests/hang.test.js::first quick': 'passed'})
        self.assertFalse(complete)   # no summary: the report (if any) decides


class Default(unittest.TestCase):
    def test_the_default_reporter_has_no_describe_path_so_nothing_is_emitted(self):
        recorder, _, _, complete = replay_fixture('vitest', 'vitest-default.jsonl')
        self.assertEqual(recorder.calls, [])   # an id without the describe path would not match the report: no events, the counters of the summary remain
        self.assertFalse(complete)

    def test_ansi_colours_change_nothing(self):
        text = ''.join(c['data'] for c in load('vitest-verbose.jsonl')[1] if c['fd'] == 'out')
        coloured = '\n'.join(('\x1b[32m' + line + '\x1b[39m') if line.startswith(' ✓') else line for line in text.split('\n'))
        want = replay('vitest', chunks_from_text(text), tool='vitest')[0].calls
        self.assertEqual(replay('vitest', chunks_from_text(coloured), tool='vitest')[0].calls, want)


class Hostile(unittest.TestCase):
    def test_garbage_and_long_lines_emit_nothing_and_do_not_stall(self):
        for text in ('', '\n\n', 'random text\n × not a test line\n', ' ✓ \n ✓ > \n',
                     ' ✓ ' + 'a ' * 5000 + '> ' + 'b ' * 5000 + '12ms\n', ' ✓ x > ' + 'y' * 100000 + '\n'):
            with self.subTest(text=text[:20]):
                recorder = replay('vitest', chunks_from_text(text, size=512), tool='vitest')[0]
                self.assertEqual(recorder.calls, [])   # a line longer than the limit is not a result (it is cut by nobody: it is ignored)

    def test_a_skipped_name_that_ends_like_a_time_keeps_its_name_and_other_shapes(self):
        text = (' ↓ f.test.js > waits 5s\n □ f.test.js > todo 12ms\n ✓ f.test.js > fast 1.2s\n ✓ f.test.js > exact 12ms\n ✓ dir with space/f.test.js > spaced 3ms\n'
                ' × f.test.js > broken 7ms (retry x2)\n   → boom\n ✓ f.test.js > a > b > c 1ms\n Tests  1 failed | 4 passed | 1 skipped | 1 todo (7)\n')
        recorder, _, _, complete = replay('vitest', chunks_from_text(text), tool='vitest')
        self.assertEqual(states(recorder), {'f.test.js::waits 5s': 'skipped', 'f.test.js::todo 12ms': 'skipped', 'f.test.js::fast': 'passed', 'f.test.js::exact': 'passed',
                                            'dir with space/f.test.js::spaced': 'passed', 'f.test.js::broken': 'failed', 'f.test.js::a > b > c': 'passed'})
        self.assertEqual([c[5] for c in recorder.tests() if c[1] == 'f.test.js::fast'], [1200])
        self.assertEqual(recorder.last()['f.test.js::broken'][1], 'boom')
        self.assertTrue(complete)

    def test_a_line_of_another_file_between_a_failure_and_its_message_drops_the_message(self):
        text = ' × a.test.js > bad 1ms\n ✓ b.test.js > fine 1ms\n   → not the message of bad\n'
        recorder = replay('vitest', chunks_from_text(text), tool='vitest')[0]
        self.assertIsNone(recorder.last()['a.test.js::bad'][1])

    def test_a_title_repeated_in_a_describe_is_two_lines_and_complete(self):
        text = ' ✓ f.test.js > d > same 1ms\n ✓ f.test.js > d > same 1ms\n Tests  2 passed (2)\n'
        self.assertTrue(replay('vitest', chunks_from_text(text), tool='vitest')[3])

    def test_a_message_line_without_a_failed_test_before_it_is_ignored(self):
        recorder = replay('vitest', chunks_from_text('   → expected 4 to be 5\n ✓ a.test.js > ok 1ms\n   → not a message of a failure\n'), tool='vitest')[0]
        self.assertEqual(states(recorder), {'a.test.js::ok': 'passed'})

    def test_the_seen_table_is_bounded(self):
        from skillsync_workflow.test_adapters import vitest
        lines = ''.join(f' ✓ f.test.js > t{i} 1ms\n' for i in range(vitest.MAX_SEEN + 50))
        recorder = replay('vitest', chunks_from_text(lines, size=65536), tool='vitest')[0]
        self.assertLessEqual(len(recorder.tests()), vitest.MAX_SEEN)


if __name__ == '__main__':
    unittest.main()
