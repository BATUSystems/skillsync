"""180-A3: the adapter of Cypress 16.3.0 on recorded streams (the `spec` reporter in Electron and Chrome, two files, retries, an interrupted run, the junit report)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'cypress.py'
M, S = 'math.cy.js::math > ', 'second.cy.js::'
MATH = {M + 'adds': 'passed', M + 'subtracts': 'passed', M + 'is skipped': 'skipped', M + 'fails with a message': 'failed', M + 'adds 1 + 1 = 2': 'passed',
        M + 'adds 2 + 3 = 5': 'passed', M + 'adds 2 + 2 = 5': 'failed', M + 'żółć – unicode name': 'passed', M + 'is pending without a body': 'skipped',
        M + 'nested > deep ok': 'passed'}


def states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


class Registration(unittest.TestCase):
    def test_name_liveness_and_selection(self):
        for argv, tool in ((['cypress', 'run'], 'cypress'), (['cypress', 'run', '--browser', 'chrome'], 'cypress')):
            with self.subTest(argv=argv):
                adapter = test_adapters.resolve('auto', argv, tool)
                self.assertEqual((adapter.name, adapter.live), ('cypress', 'completion'))
                self.assertEqual(adapter.env(), {})

    def test_other_commands_are_not_taken(self):
        cls = test_adapters.REGISTRY['cypress']
        for argv, expected in ((['npx', 'cypress', 'open'], False), (['npx', 'cypress', 'install'], False), (['npx', 'playwright', 'test'], False), (['cypress', 'run'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class Spec(unittest.TestCase):
    def test_two_files_final_states_names_from_the_indentation_and_the_messages(self):
        recorder, _, _, complete = replay_fixture('cypress', 'cypress-spec.jsonl')
        self.assertEqual(states(recorder), {**MATH, S + 'second ok': 'passed'})
        self.assertTrue(complete)   # 11 result lines = 10 tests of math.cy.js + 1 of second.cy.js, the sum of the summaries of the two files
        self.assertEqual(recorder.last()[M + 'fails with a message'][1], 'AssertionError: expected 4 to equal 5')
        self.assertEqual(recorder.last()[M + 'adds 2 + 2 = 5'][1], 'AssertionError: expected 4 to equal 5')
        self.assertNotIn('running', {c[3] for c in recorder.emitted()})

    def test_the_failure_listing_after_the_summary_is_not_read_as_tests(self):
        recorder = replay_fixture('cypress', 'cypress-spec.jsonl')[0]
        self.assertEqual(len(states(recorder)), 11)
        self.assertFalse([k for k in states(recorder) if k.endswith('> math') or k == 'math.cy.js::math'])

    def test_attempt_lines_are_ignored_and_the_final_line_decides(self):
        recorder, _, _, complete = replay_fixture('cypress', 'cypress-spec-retries1.jsonl')
        self.assertEqual(states(recorder), MATH)
        self.assertTrue(complete)
        self.assertFalse([k for k in states(recorder) if 'Attempt' in k])

    def test_chrome_gives_the_same_line_shape(self):
        recorder, _, _, complete = replay_fixture('cypress', 'cypress-spec-chrome.jsonl')
        self.assertEqual(states(recorder), {S + 'second ok': 'passed'})
        self.assertTrue(complete)

    def test_an_interrupted_run_keeps_what_finished_and_has_no_summary(self):
        recorder, _, _, complete = replay_fixture('cypress', 'cypress-spec-interrupted.jsonl')
        self.assertEqual(states(recorder), {M + 'adds': 'passed', M + 'subtracts': 'passed', M + 'is skipped': 'skipped'})
        self.assertFalse(complete)

    def test_the_suite_stack_follows_the_indentation_back_up(self):
        text = ('  Running:  a.cy.js  (1 of 1)\n\n\n  outer\n    inner\n      ✓ deep (1ms)\n    ✓ shallow (2ms)\n  ✓ top (3ms)\n  other\n    ✓ after (4ms)\n\n\n  4 passing (1s)\n')
        recorder, _, _, complete = replay('cypress', chunks_from_text(text), tool='cypress')
        self.assertEqual(sorted(states(recorder)), ['a.cy.js::other > after', 'a.cy.js::outer > inner > deep', 'a.cy.js::outer > shallow', 'a.cy.js::top'])
        self.assertTrue(complete)

    def test_a_failing_test_outside_any_describe_keeps_its_message(self):
        text = ('  Running:  a.cy.js  (1 of 1)\n\n\n    1) top fails\n    ✓ top ok (2ms)\n\n\n  1 passing (1s)\n  1 failing\n\n  1) top fails:\n     AssertionError: expected 1 to equal 2\n      at x\n')
        recorder, _, _, complete = replay('cypress', chunks_from_text(text), tool='cypress')
        self.assertEqual(states(recorder), {'a.cy.js::top fails': 'failed', 'a.cy.js::top ok': 'passed'})
        self.assertEqual(recorder.last()['a.cy.js::top fails'][1], 'AssertionError: expected 1 to equal 2')
        self.assertTrue(complete)

    def test_suites_with_titles_that_start_with_a_digit_a_bracket_or_a_dash_and_summary_like_titles(self):
        text = ('  Running:  a.cy.js  (1 of 1)\n\n\n  404 page\n    ✓ shows (1ms)\n  (admin) area\n    ✓ shows (1ms)\n  3 passing scenarios\n    ✓ one (1ms)\n  -dash suite\n    ✓ two (1ms)\n\n\n'
                '  4 passing (1s)\n')
        recorder, _, _, complete = replay('cypress', chunks_from_text(text), tool='cypress')
        self.assertEqual(sorted(states(recorder)), ['a.cy.js::(admin) area > shows', 'a.cy.js::-dash suite > two', 'a.cy.js::3 passing scenarios > one', 'a.cy.js::404 page > shows'])
        self.assertTrue(complete)

    def test_a_title_repeated_in_a_suite_keeps_a_failure(self):
        text = ('  Running:  a.cy.js  (1 of 1)\n\n\n  s\n    1) loop\n    ✓ loop (1ms)\n\n\n  1 passing (1s)\n  1 failing\n')
        recorder, _, _, complete = replay('cypress', chunks_from_text(text), tool='cypress')
        self.assertEqual(states(recorder), {'a.cy.js::s > loop': 'failed'})
        self.assertTrue(complete)   # two lines, two tests of the summary

    def test_the_windows_mark_and_stderr_noise(self):
        text = '  Running:  a.cy.js  (1 of 1)\n\n\n  s\n    √ ok (1ms)\n\n\n  1 passing (1s)\n'
        self.assertEqual(states(replay('cypress', chunks_from_text(text), tool='cypress')[0]), {'a.cy.js::s > ok': 'passed'})
        noisy = replay('cypress', [{'t': 0, 'fd': 'err', 'data': '  Running:  a.cy.js  (1 of 1)\n    ✓ x (1ms)\n'}], tool='cypress')[0]
        self.assertEqual(noisy.calls, [])

    def test_an_empty_file_makes_the_stream_incomplete(self):
        text = ('  Running:  a.cy.js  (1 of 2)\n\n\n    ✓ ok (1ms)\n\n\n  1 passing (1s)\n  Running:  b.cy.js  (2 of 2)\n')
        self.assertFalse(replay('cypress', chunks_from_text(text), tool='cypress')[3])

    def test_the_same_result_for_any_chunking(self):
        meta, chunks = load('cypress-spec.jsonl')
        want = replay_fixture('cypress', 'cypress-spec.jsonl')[0].calls
        for size in (1, 9, 128, 8000):
            with self.subTest(size=size):
                self.assertEqual(replay('cypress', rechunk(chunks, size), argv=meta['command'], tool='cypress')[0].calls, want)

    def test_ansi_colours_change_nothing(self):
        text = ''.join(c['data'] for c in load('cypress-spec-chrome.jsonl')[1])
        coloured = text.replace('✓', '\x1b[32m✓\x1b[39m')
        self.assertEqual(replay('cypress', chunks_from_text(coloured), tool='cypress')[0].calls, replay('cypress', chunks_from_text(text), tool='cypress')[0].calls)


class Report(unittest.TestCase):
    def test_the_mocha_junit_ids_do_not_carry_the_file(self):
        report = {r['id']: r['state'] for r in test_reports.parse_junit(ar.FIXTURES / 'cypress-junit.xml')}
        self.assertEqual(report['adds::math adds'], 'passed')
        self.assertEqual(report['fails with a message::math fails with a message'], 'failed')   # `classname` is the test title: why the stream is read for the ids


class Hostile(unittest.TestCase):
    def test_garbage_before_a_running_header_and_long_lines_emit_nothing(self):
        for text in ('', '    ✓ not in a file (1ms)\n', '  Running:  \n', '  Running:  a.cy.js  (1 of 1)\n' + '    ✓ ' + 'x' * 100000 + ' (1ms)\n'):
            with self.subTest(text=text[:20]):
                self.assertEqual(len(replay('cypress', chunks_from_text(text, size=999), tool='cypress')[0].calls), 0)

    def test_deeply_nested_suites_are_bounded(self):
        text = '  Running:  a.cy.js  (1 of 1)\n' + ''.join(' ' * (2 + i) + f's{i}\n' for i in range(200)) + ' ' * 300 + '✓ t (1ms)\n'
        recorder = replay('cypress', chunks_from_text(text), tool='cypress')[0]
        self.assertEqual(len(recorder.emitted()), 1)
        self.assertLessEqual(recorder.emitted()[0][2].count(' > '), 32)


if __name__ == '__main__':
    unittest.main()
