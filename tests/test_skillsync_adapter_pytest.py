"""180-A2: the adapter of `pytest -v`, on recorded streams (pytest 9.2.0, Python 3.14.10) and against the JUnit report of the same run."""
import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk, stream_text  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'pytest.py'
NOCOLLECT = lambda argv, timeout=30: None   # noqa: E731
EXPECTED_MIXED = {
    'test_green.GreenTests::test_adds': 'passed', 'test_green.GreenTests::test_prints': 'passed', 'test_green.GreenTests::test_slow': 'passed',
    'test_red.RedTests::test_boom': 'failed', 'test_red.RedTests::test_error': 'failed', 'test_red.RedTests::test_long_message': 'failed', 'test_red.RedTests::test_slow_red': 'failed',
    'test_skips.SkipTests::test_ok': 'passed', 'test_skips.SkipTests::test_runtime_skip': 'skipped', 'test_skips.SkipTests::test_skip_if': 'skipped',
    'test_skips.SkipTests::test_skipped': 'skipped', 'test_skips.SkipTests::test_unexpected_success': 'failed', 'test_skips.SkipTests::test_xfail': 'skipped',
    'test_subtests.SubTests::test_sub': 'failed', 'test_subtests.SubTests::test_sub_named': 'passed',
    'test_pytest_only::test_same[1-1]': 'passed', 'test_pytest_only::test_same[2-2]': 'passed', 'test_pytest_only::test_same[3-4]': 'failed',
    'test_pytest_only::test_len[\\u0105\\u0119 \\u015b\\u0107]': 'passed', 'test_pytest_only::test_len[a b]': 'passed',
    'test_pytest_only::test_len[' + 'x' * 80 + ']': 'passed', 'test_pytest_only::test_xfail': 'skipped', 'test_pytest_only::test_xpass': 'passed',
    'test_pytest_only::test_skip': 'skipped', 'test_pytest_only::test_fixture_error': 'failed', 'test_pytest_only::test_plain': 'passed',
}


def final_states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


class Registration(unittest.TestCase):
    def test_name_liveness_and_tool(self):
        adapter = test_adapters.resolve('auto', ['python', '-m', 'pytest', '-v'], 'pytest')
        self.assertEqual((adapter.name, adapter.live), ('pytest', 'running'))

    def test_accepts_only_verbose_runs_that_run_tests(self):
        cls = test_adapters.REGISTRY['pytest']
        for argv, expected in ((['pytest', '-v'], True), (['pytest', '-vv'], True), (['pytest', '--verbose', 'x'], True), (['pytest', '-vx'], True), (['pytest'], False),
                               (['pytest', '-q'], False), (['pytest', '-rA'], False), (['pytest', '-v', '--collect-only'], False), (['pytest', '-v', '--co'], False),
                               (['pytest', '-n', '4'], False), (['pytest', '-pvcr'], False), (['pytest', '-ktest_convert'], False), (['pytest', '--collectonly', '-v'], False),
                               (['pytest', '-vvs'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class RecordedStreams(unittest.TestCase):
    def test_green_run_start_comes_before_the_result(self):
        rec, *_ = replay_fixture('pytest', 'pytest-v-green.jsonl', collect=NOCOLLECT)
        for name in ('test_adds', 'test_prints', 'test_slow'):
            self.assertEqual(rec.states('test_green.GreenTests::' + name), ['running', 'passed'])

    def test_start_seen_in_the_unfinished_line_before_the_test_ends(self):
        meta, chunks = load('pytest-v-green.jsonl')
        slow = next(i for i, c in enumerate(chunks) if c['data'].endswith('test_slow '))   # the chunk that ends with the unfinished start of the slow test
        rec, *_ = replay('pytest', chunks[:slow + 1], argv=meta['command'], collect=NOCOLLECT)
        self.assertEqual(rec.states('test_green.GreenTests::test_slow'), ['running'])

    def test_mixed_run_states(self):
        rec, *_ = replay_fixture('pytest', 'pytest-v-mixed.jsonl', collect=NOCOLLECT)
        self.assertEqual(final_states(rec), EXPECTED_MIXED)

    def test_messages_and_reasons(self):
        last = replay_fixture('pytest', 'pytest-v-mixed.jsonl', collect=NOCOLLECT)[0].last()
        self.assertEqual(last['test_red.RedTests::test_boom'][1], 'AssertionError: 1 != 2')
        self.assertEqual(last['test_red.RedTests::test_error'][1], 'ValueError: kaput')
        self.assertEqual(last['test_skips.SkipTests::test_skipped'][1], 'not ready')
        self.assertEqual(last['test_pytest_only::test_xfail'][1], 'xfail: known bug')
        self.assertEqual(last['test_pytest_only::test_xpass'][1], 'xpass: fixed already')
        self.assertEqual(last['test_pytest_only::test_fixture_error'][1], 'RuntimeError: fixture exploded')
        self.assertEqual(last['test_pytest_only::test_same[3-4]'][1], 'assert 3 == 4')
        self.assertTrue(last['test_subtests.SubTests::test_sub'][1].startswith('sub-test (i=1)'))   # a failed sub-test fails its parent test (printed in the summary)

    def test_ids_are_the_ids_of_the_junit_report_of_the_same_kind_of_run(self):
        junit = {r['id']: r['state'] for r in test_reports.parse_junit(ar.FIXTURES / 'pytest-junit-mixed.xml')}
        rec, *_ = replay_fixture('pytest', 'pytest-v-junit.jsonl', collect=NOCOLLECT)
        mine = final_states(rec)
        self.assertEqual(set(junit) - set(mine), set())
        for ident, state in junit.items():
            self.assertEqual(mine[ident], state, ident)   # the report would overwrite each entry with the same state

    def test_every_chunk_size_gives_the_same_final_states(self):
        meta, chunks = load('pytest-v-mixed.jsonl')
        for size in (1, 3, 40):
            with self.subTest(size=size):
                rec, *_ = replay('pytest', rechunk(chunks, size), argv=meta['command'], collect=NOCOLLECT)
                self.assertEqual(final_states(rec), EXPECTED_MIXED)

    def test_unbuffered_and_merged_streams_give_the_same_final_states(self):
        for name in ('pytest-v-green-unbuffered.jsonl', 'pytest-v-merged.jsonl'):
            with self.subTest(name=name):
                rec, *_ = replay_fixture('pytest', name, collect=NOCOLLECT)
                self.assertEqual(final_states(rec)['test_green.GreenTests::test_slow'], 'passed')

    def test_ansi_colours_do_not_hide_the_results(self):
        rec, *_ = replay_fixture('pytest', 'pytest-v-ansi.jsonl', collect=NOCOLLECT)
        last = final_states(rec)
        self.assertEqual(last['test_green.GreenTests::test_adds'], 'passed')
        self.assertEqual(last['test_red.RedTests::test_boom'], 'failed')

    def test_an_interrupted_run_fails_the_test_that_never_finished(self):
        rec, *_ = replay_fixture('pytest', 'pytest-interrupted.jsonl', collect=NOCOLLECT)
        self.assertEqual(rec.last()['test_red.RedTests::test_slow_red'], ('failed', 'no result: the run ended while the test was running'))
        self.assertEqual(rec.states('test_red.RedTests::test_slow_red'), ['running', 'failed'])

    def test_runs_without_test_lines_name_only_what_the_summary_names(self):
        failed = {'test_red.RedTests::test_boom', 'test_red.RedTests::test_error', 'test_red.RedTests::test_long_message', 'test_red.RedTests::test_slow_red'}
        for name, argv, expected in (('pytest-collect-error.jsonl', None, set()), ('pytest-no-tests.jsonl', None, set()), ('pytest-q.jsonl', ['pytest', '-v'], failed),
                                     ('pytest-rA-mixed.jsonl', ['pytest', '-v'], failed | {'test_skips.SkipTests::test_unexpected_success', 'test_pytest_only::test_same[3-4]',
                                                                                            'test_pytest_only::test_fixture_error', 'test_subtests.SubTests::test_sub'} - {'test_subtests.SubTests::test_sub'})):
            with self.subTest(name=name):
                rec, *_ = replay_fixture('pytest', name, collect=NOCOLLECT, **({'argv': argv} if argv else {}))
                got = final_states(rec)
                self.assertEqual({k for k, v in got.items() if v == 'failed'}, {k for k in got}, 'only failures are named by the summary lines')
                self.assertEqual(set(got) - {'test_subtests.SubTests::test_sub'}, expected)

    def test_the_collector_lists_the_tests_to_come(self):
        text = stream_text('pytest-collect-only-q.jsonl')
        seen = []
        rec, *_ = replay('pytest', [], argv=['python3', '-m', 'pytest', '-v', '-p', 'no:cacheprovider', '-n', '4', '--maxfail', '2', '--junit-xml', 'out.xml', '-k', 'a or b', '-x',
                                             'test_green.py'], collect=lambda argv, timeout=30: seen.append(argv) or text)
        self.assertEqual(seen, [['python3', '-m', 'pytest', '-k', 'a or b', 'test_green.py', '--collect-only', '-q']])   # flags that run or write are dropped WITH their values
        collected = [c for c in rec.calls if c[0] == 'collected'][0]
        self.assertEqual(collected[1], 14)
        self.assertIn('test_green.GreenTests::test_adds', collected[2])
        self.assertIn('test_pytest_only::test_same[3-4]', collected[2])
        self.assertTrue(all('\\' not in name for name in collected[2]))   # a backslash in a parameter is normalised like the id of a `test` event

    def test_unreadable_arguments_give_no_list(self):
        for argv in (['pytest', '-v', '--strange-flag'], ['coverage', 'run', '-m', 'pytest', '-v'], ['docker', 'run', '-v', '/a:/b', 'img', 'pytest', '-v'],
                     ['timeout', '5', 'pytest', '-v'], ['make', 'test']):
            with self.subTest(argv=argv):
                seen = []
                replay('pytest', [], argv=argv, collect=lambda a, timeout=30: seen.append(a))
                self.assertEqual(seen, [])

    def test_flags_that_write_files_are_dropped_with_their_values(self):
        seen = []
        replay('pytest', [], argv=['pytest', '-v', '--html', 'r.html', '--alluredir=out', 'x'], collect=lambda a, timeout=30: seen.append(a))
        self.assertEqual(seen, [['pytest', 'x', '--collect-only', '-q']])

    def test_flags_that_change_which_tests_run_give_no_list(self):
        for argv in (['pytest', '-v', '--lf'], ['pytest', '-v', '--sw'], ['pytest', '-v', '--last-failed'], ['pytest', '-v', '-xktest_a'], ['pytest', '-v', '--new-first']):
            with self.subTest(argv=argv):
                seen = []
                replay('pytest', [], argv=argv, collect=lambda a, timeout=30: seen.append(a))
                self.assertEqual(seen, [])
        seen = []
        replay('pytest', [], argv=['pytest', '-v', '--reruns', '3', '--reruns-delay=1', 'x'], collect=lambda a, timeout=30: seen.append(a))
        self.assertEqual(seen, [['pytest', 'x', '--collect-only', '-q']])

    def test_a_failed_collector_changes_nothing(self):
        rec, *_ = replay('pytest', [], argv=['pytest', '-v'], collect=NOCOLLECT)
        self.assertEqual(rec.calls, [])


class RealInterleaving(unittest.TestCase):
    """Recorded `-n 2` (pytest-xdist 3.8) and `-s` runs."""

    def test_xdist_results_are_read_and_starts_are_distrusted(self):
        rec, adapter, *_ = replay_fixture('pytest', 'pytest-v-xdist.jsonl', collect=NOCOLLECT)
        want = {k: v for k, v in EXPECTED_MIXED.items() if k.split('::')[0] in ('test_green.GreenTests', 'test_red.RedTests', 'test_skips.SkipTests')}
        self.assertEqual(final_states(rec), want)   # the trailing space of `[gw1] [ 7%] PASSED nodeid ` does not end up in the id
        self.assertTrue(adapter.parallel and adapter.note)
        self.assertTrue(all(i == i.strip() for i in final_states(rec)))

    def test_output_of_the_test_between_the_start_and_the_state(self):
        rec, *_ = replay_fixture('pytest', 'pytest-v-s.jsonl', collect=NOCOLLECT)
        self.assertEqual(rec.states('test_green.GreenTests::test_prints'), ['running', 'passed'])
        self.assertEqual(final_states(rec), {'test_green.GreenTests::test_adds': 'passed', 'test_green.GreenTests::test_prints': 'passed', 'test_green.GreenTests::test_slow': 'passed'})


class Interleaving(unittest.TestCase):
    """Output of tests between the start and the result (`-s`), overlapping tests (xdist): never a wrong state."""

    def run_lines(self, *pieces):
        """Each piece is ONE chunk read from the pipe (so a piece that ends without a newline is an unfinished line)."""
        chunks = [{'t': i / 100, 'fd': 'out', 'data': piece} for i, piece in enumerate(pieces)]
        return replay('pytest', chunks, argv=['pytest', '-v'], collect=NOCOLLECT)[0]

    def test_output_of_the_test_between_start_and_result(self):
        rec = self.run_lines('t.py::T::test_a ', 'hello from the test\n', 'PASSED                [ 50%]\n', 't.py::T::test_b ')
        self.assertEqual(rec.states('t.T::test_a'), ['running', 'passed'])
        self.assertEqual(rec.last()['t.T::test_b'][0], 'running')
        rec = self.run_lines('t.py::T::test_a hello printed with the start in one chunk\n', 'PASSED                [ 50%]\n')
        self.assertEqual(rec.states('t.T::test_a'), ['running', 'passed'])   # the line proves the start even if the unfinished line was never seen alone

    def test_xdist_lines_report_results_only(self):
        rec = self.run_lines('[gw0] [ 50%] PASSED t.py::T::test_a\n', '[gw1] [100%] FAILED t.py::T::test_b\n')
        self.assertEqual(final_states(rec), {'t.T::test_a': 'passed', 't.T::test_b': 'failed'})
        self.assertEqual(rec.states('t.T::test_a'), ['passed'])

    def test_two_starts_without_a_result_stop_trusting_starts(self):
        rec = self.run_lines('t.py::T::test_a ', '\nt.py::T::test_b ', '\n[gw0] PASSED t.py::T::test_b\n', 't.py::T::test_c ')
        self.assertEqual(rec.states('t.T::test_a'), ['running'])
        self.assertEqual(rec.states('t.T::test_c'), [])   # after the overlap no further `running` is invented
        parallel = replay('pytest', [{'t': 0, 'fd': 'out', 'data': 't.py::T::test_a '}, {'t': 1, 'fd': 'out', 'data': '\nt.py::T::test_b '}], argv=['pytest', '-v'], collect=NOCOLLECT)[1]
        self.assertTrue(parallel.parallel and parallel.note)


class Layouts(unittest.TestCase):
    def test_to_id_nested_classes_directories_and_parameters(self):
        from skillsync_workflow.test_adapters import pytest as adapter
        cases = {'a/b/test_x.py::A::B::test_y[p::q]': ('a.b.test_x.A.B::test_y[p::q]', 'test_y[p::q]', 'a.b.test_x.A.B'),
                 'test_x.py::test_y': ('test_x::test_y', 'test_y', 'test_x'), 'tests\\sub\\test_x.py::T::t[a\\b]': ('tests.sub.test_x.T::t[a\\b]', 't[a\\b]', 'tests.sub.test_x.T')}
        for node, want in cases.items():
            self.assertEqual(adapter.to_id(node), want, node)
        self.assertEqual(adapter.to_id('test_x.py::T::t', 'services.api'), ('services.api.test_x.T::t', 't', 'services.api.test_x.T'))

    def run_in(self, cwd, header, lines):
        chunks = chunks_from_text('\n'.join([header, *lines]) + '\n', size=50)
        return replay('pytest', chunks, argv=['pytest', '-v'], collect=NOCOLLECT, cwd=cwd)[0]

    def test_ids_follow_the_rootdir_when_the_command_runs_in_a_subdirectory(self):
        rec = self.run_in('/work/repo/services/api/tests', 'rootdir: /work/repo/services/api', ['test_x.py::T::test_a PASSED   [100%]'])
        self.assertEqual(list(final_states(rec)), ['tests.test_x.T::test_a'])   # the classname of the JUnit report is rootdir-relative
        rec = self.run_in('/work/repo', 'rootdir: /work/repo, configfile: pytest.ini', ['tests/test_x.py::T::test_a PASSED   [100%]'])
        self.assertEqual(list(final_states(rec)), ['tests.test_x.T::test_a'])
        rec = self.run_in('/elsewhere', 'rootdir: /work/repo', ['test_x.py::T::test_a PASSED   [100%]'])
        self.assertEqual(list(final_states(rec)), ['test_x.T::test_a'])   # a command outside the rootdir: no offset invented

    def test_vv_suffix_and_count_style(self):
        rec = replay('pytest', chunks_from_text('t.py::T::test_a <- base.py PASSED        [ 1/26]\nt.py::T::test_b PASSED  [ 2/26]\n', size=9), argv=['pytest', '-vv'], collect=NOCOLLECT)[0]
        self.assertEqual(final_states(rec), {'t.T::test_a': 'passed', 't.T::test_b': 'passed'})


class Sections(unittest.TestCase):
    """Text printed by tests (failure sections, captured output) never makes a result."""

    def lines(self, text):
        return replay('pytest', chunks_from_text(text, size=13), argv=['pytest', '-v'], collect=NOCOLLECT)[0]

    def test_result_and_summary_looking_lines_outside_their_sections_are_ignored(self):
        rec = self.lines('=== test session starts ===\nt.py::T::test_a PASSED  [100%]\n=== FAILURES ===\n___ T.test_a ___\nCaptured stdout call:\nt.py::T::fake PASSED  [ 50%]\n'
                         'FAILED t.py::T::fake2 - boom\n=== short test summary info ===\nFAILED t.py::T::test_a - AssertionError\n= 1 failed in 0.1s =\nFAILED t.py::T::late - x\n')
        self.assertEqual(final_states(rec), {'t.T::test_a': 'passed'})   # nothing from the failure section, the captured output or after the closing line

    def test_a_parameter_with_a_dash_and_nested_subtest_text(self):
        rec = self.lines('=== short test summary info ===\nFAILED t.py::test_x[1 - 2] - AssertionError: 1 != 2\nSUBFAILED(i=(1), j=2) t.py::T::test_s - boom\n')
        last = rec.last()
        self.assertEqual(last['t::test_x[1 - 2]'], ('failed', 'AssertionError: 1 != 2'))
        self.assertEqual(last['t.T::test_s'][0], 'failed')

    def test_text_printed_by_the_test_after_its_start(self):
        pieces = ['t.py::T::test_a ', 'progress ', '50% ', 'done\n', 'PASSED  [100%]\n']
        chunks = [{'t': i / 100, 'fd': 'out', 'data': p} for i, p in enumerate(pieces)]
        rec = replay('pytest', chunks, argv=['pytest', '-v'], collect=NOCOLLECT)[0]
        self.assertEqual(rec.states('t.T::test_a'), ['running', 'passed'])
        self.assertEqual(list(final_states(rec)), ['t.T::test_a'])   # the printed text is neither a test nor a state

    def test_a_banner_printed_by_a_test_does_not_end_the_run_section(self):
        pieces = ['=== test session starts ===\n', 't.py::T::test_a ', '=== my banner ===\n', 'PASSED  [ 50%]\n', 't.py::T::test_b PASSED  [100%]\n']
        rec = replay('pytest', [{'t': i / 100, 'fd': 'out', 'data': p} for i, p in enumerate(pieces)], argv=['pytest', '-v'], collect=NOCOLLECT)[0]
        self.assertEqual(final_states(rec), {'t.T::test_a': 'passed', 't.T::test_b': 'passed'})

    def test_the_table_of_seen_tests_is_bounded(self):
        lines = ''.join(f't.py::test_{i} PASSED  [100%]\n' for i in range(60000))
        rec, adapter, *_ = replay('pytest', chunks_from_text(lines, size=8192), argv=['pytest', '-v'], collect=NOCOLLECT)
        self.assertLessEqual(len(adapter.seen), 50000)

    def test_a_printed_status_word_is_not_a_result(self):
        pieces = ['t.py::T::test_a ', 'status: FAILED\n', 'PASSED  [100%]\n']
        rec = replay('pytest', [{'t': i / 100, 'fd': 'out', 'data': p} for i, p in enumerate(pieces)], argv=['pytest', '-v'], collect=NOCOLLECT)[0]
        self.assertEqual(final_states(rec), {'t.T::test_a': 'passed'})

    def test_a_start_cut_inside_a_parameter_is_not_a_start(self):
        pieces = ['t.py::test_len[a ', 'b]', ' PASSED  [100%]\n']
        rec = replay('pytest', [{'t': i / 100, 'fd': 'out', 'data': p} for i, p in enumerate(pieces)], argv=['pytest', '-v'], collect=NOCOLLECT)[0]
        self.assertEqual(list(final_states(rec)), ['t::test_len[a b]'])
        self.assertEqual(rec.states('t::test_len[a b]'), ['passed'])   # the cut tail `t.py::test_len[a ` is no node id: no bogus entry

    def test_ids_with_the_closing_summary_of_an_interrupted_run(self):
        pieces = ['t.py::T::test_a ', '\n!!!!! KeyboardInterrupt !!!!!\n= 1 passed in 0.1s =\n']
        rec = replay('pytest', [{'t': i / 100, 'fd': 'out', 'data': p} for i, p in enumerate(pieces)], argv=['pytest', '-v'], collect=NOCOLLECT, exit_code=2)[0]
        self.assertEqual(rec.last()['t.T::test_a'], ('failed', 'no result: the run ended while the test was running'))


class Hostile(unittest.TestCase):
    def feed(self, text, size=7):
        return replay('pytest', chunks_from_text(text, size=size), argv=['pytest', '-v'], collect=NOCOLLECT)[0]

    def test_garbage_does_not_raise_and_states_are_valid(self):
        rng = random.Random(5)
        alphabet = list(' \t_:.[]()%\'"\\-=PASEDFILRORXpyt/::0123456789') + ['ą', '\x00', '\x1b[31m', '\r', '\n', '\n', '::']
        for _ in range(60):
            rec = self.feed(''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 400))), size=rng.randint(1, 30))
            for call in rec.emitted():
                self.assertIn(call[3], ('running', 'passed', 'failed', 'skipped'))

    def test_lines_cut_in_the_middle_never_invent_a_state(self):
        text = ''.join(c['data'] for c in load('pytest-v-mixed.jsonl')[1] if c['fd'] == 'out')
        for cut in (30, 210, 480, 900, 1700, 2600):
            with self.subTest(cut=cut):
                rec = self.feed(text[:cut], size=13)
                for ident, (state, _) in rec.last().items():
                    self.assertIn(ident, EXPECTED_MIXED)   # no id outside the real ones, whatever the cut
                    allowed = ('running', EXPECTED_MIXED[ident]) + (('passed',) if ident == 'test_subtests.SubTests::test_sub' else ())   # its failure comes in the summary
                    self.assertIn(state, allowed)

    def test_a_very_long_line_is_cheap(self):
        started = time.monotonic()
        rec = self.feed('t.py::T::test_a ' + 'x' * 300000 + '\nt.py::T::test_b PASSED  [100%]\n', size=997)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(final_states(rec)['t.T::test_b'], 'passed')

    def test_hostile_shapes_are_cheap(self):
        started = time.monotonic()
        for text in ('a::' * 700 + ' PASSED (' * 100 + '\n', 't.py::t[' * 300 + ' PASSED\n', 't.py::T::t ' + '[ 1%] ' * 300 + '\n', ' - ' * 800 + '\n'):
            self.feed(text, size=997)
        self.assertLess(time.monotonic() - started, 3)

    def test_a_very_long_parameter_id_is_cut_and_not_reported(self):
        rec = self.feed('t.py::test_len[' + 'x' * 3000 + '] PASSED  [100%]\n')
        self.assertEqual(rec.emitted(), [])   # the line is cut at 2000 characters: it no longer closes its bracket


class ThroughTheWriter(unittest.TestCase):
    def test_only_the_run_directory_is_written(self):
        meta, chunks = load('pytest-v-mixed.jsonl')
        events, tree = ar.replay_to_files('pytest', chunks, argv=meta['command'], collect=NOCOLLECT)
        self.assertTrue(events)
        self.assertTrue(all(name.startswith('.skillsync/runs/R1/tests/') for name in tree), tree)


if __name__ == '__main__':
    unittest.main()
