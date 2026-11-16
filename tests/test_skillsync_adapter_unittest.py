"""180-A2: the adapter of `python -m unittest -v`, on recorded streams (Python 3.14.10) and on the real tool."""
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters, test_events as ev  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'unittest.py'
CLI = ar.ROOT / 'skillsync'
GREEN = 'test_green.GreenTests::'
EXPECTED_MIXED = {
    GREEN + 'test_adds': 'passed', GREEN + 'test_prints': 'passed', GREEN + 'test_slow': 'passed',
    'test_red.RedTests::test_boom': 'failed', 'test_red.RedTests::test_error': 'failed', 'test_red.RedTests::test_long_message': 'failed',
    'test_red.RedTests::test_slow_red': 'failed',
    'test_skips.SkipTests::test_ok': 'passed', 'test_skips.SkipTests::test_runtime_skip': 'skipped', 'test_skips.SkipTests::test_skip_if': 'skipped',
    'test_skips.SkipTests::test_skipped': 'skipped', 'test_skips.SkipTests::test_unexpected_success': 'failed', 'test_skips.SkipTests::test_xfail': 'skipped',
    'test_subtests.SubTests::test_sub': 'failed', 'test_subtests.SubTests::test_sub (i=1)': 'failed', 'test_subtests.SubTests::test_sub_named': 'passed',
}


def final_states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


class Registration(unittest.TestCase):
    def test_name_liveness_and_tool(self):
        adapter = test_adapters.resolve('auto', ['python', '-m', 'unittest', '-v'], 'unittest')
        self.assertEqual((adapter.name, adapter.live), ('unittest', 'running'))
        self.assertEqual(adapter.env(), {})

    def test_accepts_only_the_verbose_form(self):
        cls = test_adapters.REGISTRY['unittest']
        for argv, expected in ((['python', '-m', 'unittest', '-v'], True), (['python', '-m', 'unittest', '-vv', 'x'], True), (['python', '-m', 'unittest', '--verbose'], True),
                               (['python', '-m', 'unittest', '-bv'], True), (['python', '-m', 'unittest'], False), (['python', '-m', 'unittest', 'discover'], False),
                               (['python', '-m', 'unittest', '--version'], False), (['python', '-v', '-m', 'unittest'], False), (['python', '-v', '-m', 'unittest', '-v'], True),
                               (['pytest', '-v'], False), (['python', '-m', 'unittest', '-kview'], False), (['python', '-m', 'unittest', '-bv'], True), (['python', '-m', 'unittest', '-fv'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_auto_without_verbose_gives_no_adapter(self):
        self.assertIsNone(test_adapters.resolve('auto', ['python', '-m', 'unittest'], 'unittest'))

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class RecordedStreams(unittest.TestCase):
    def test_green_run_each_test_runs_then_passes(self):
        rec, adapter, ctx, skipped = replay_fixture('unittest', 'unittest-v-green.jsonl')
        self.assertFalse(skipped)
        for name in ('test_adds', 'test_prints', 'test_slow'):
            self.assertEqual(rec.states(GREEN + name), ['running', 'passed'])
        # the start was emitted BEFORE the result of the same test and after the result of the previous one
        order = [(c[1], c[3]) for c in rec.tests()]
        self.assertEqual(order[:3], [(GREEN + 'test_adds', 'running'), (GREEN + 'test_adds', 'passed'), (GREEN + 'test_prints', 'running')])

    def test_start_is_seen_before_the_result_arrives(self):
        meta, chunks = load('unittest-v-green.jsonl')
        slow = next(i for i, c in enumerate(chunks) if c['data'].startswith('test_slow'))
        rec, *_ = replay('unittest', chunks[:slow + 1], argv=meta['command'])   # the stream cut right after the unfinished start line of the slow test
        self.assertEqual(rec.states(GREEN + 'test_slow'), ['running'])
        self.assertEqual(rec.states(GREEN + 'test_adds'), ['running', 'passed'])

    def test_mixed_run_states_and_messages(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-mixed.jsonl')
        self.assertEqual(final_states(rec), EXPECTED_MIXED)
        last = rec.last()
        self.assertEqual(last['test_red.RedTests::test_error'][1], 'ValueError: kaput')
        self.assertEqual(last['test_red.RedTests::test_boom'][1], 'AssertionError: 1 != 2')
        self.assertEqual(last['test_red.RedTests::test_slow_red'][1], "AssertionError: 'a' != 'b'")
        self.assertEqual(last['test_skips.SkipTests::test_skipped'][1], 'not ready')
        self.assertEqual(last['test_skips.SkipTests::test_xfail'][1], 'expected failure')
        self.assertEqual(last['test_skips.SkipTests::test_unexpected_success'][1], 'unexpected success')
        self.assertLessEqual(len(last['test_red.RedTests::test_long_message'][1]), 300)

    def test_a_failing_subtest_fails_the_parent_and_names_the_parameters(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-subtests.jsonl')
        self.assertEqual(final_states(rec), {'test_subtests.SubTests::test_sub': 'failed', 'test_subtests.SubTests::test_sub (i=1)': 'failed',
                                             'test_subtests.SubTests::test_sub_named': 'passed'})

    def test_merged_streams_give_the_same_final_states(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-mixed-merged.jsonl')
        self.assertEqual(final_states(rec), EXPECTED_MIXED)

    def test_every_chunk_size_gives_the_same_final_states(self):
        meta, chunks = load('unittest-v-mixed.jsonl')
        for size in (1, 2, 5, 33):
            with self.subTest(size=size):
                rec, *_ = replay('unittest', rechunk(chunks, size), argv=meta['command'])
                self.assertEqual(final_states(rec), EXPECTED_MIXED)

    def test_a_test_that_never_finished_is_failed_with_a_reason(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-interrupted.jsonl')
        self.assertEqual(rec.last()['test_red.RedTests::test_slow_red'], ('failed', 'no result: the run ended while the test was running'))
        self.assertEqual(rec.states('test_red.RedTests::test_slow_red'), ['running', 'failed'])
        self.assertEqual(rec.last()[GREEN + 'test_slow'][0], 'passed')

    def test_an_import_error_is_a_failed_test(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-import-error.jsonl')
        last = rec.last()
        self.assertEqual(last['unittest.loader._FailedTest::test_collect_error'][0], 'failed')
        self.assertEqual(last[GREEN + 'test_adds'][0], 'passed')

    def test_the_dotted_run_has_nothing_to_read_and_is_not_accepted(self):
        meta, chunks = load('unittest-dotted.jsonl')
        self.assertIsNone(test_adapters.resolve('auto', meta['command'], 'unittest'))
        rec, adapter, *_ = replay('unittest', chunks, argv=['python', '-m', 'unittest', '-v'], adapter=test_adapters.REGISTRY['unittest']())
        # forced, the dots carry no names; only the `FAIL:`/`ERROR:` blocks of the summary name tests, and those are failures
        self.assertTrue(rec.tests() and all(c[3] == 'failed' for c in rec.tests()))
        self.assertNotIn('test_green.GreenTests::test_adds', rec.last())

    def test_a_name_that_is_not_a_module_is_one_failed_test(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-missing-module.jsonl')
        self.assertEqual(final_states(rec), {'unittest.loader._FailedTest::test_nonexistent_module_xyz': 'failed'})

    def test_buffered_output_run(self):
        rec, *_ = replay_fixture('unittest', 'unittest-v-buffer.jsonl')
        self.assertEqual(final_states(rec)[GREEN + 'test_prints'], 'passed')
        self.assertEqual(final_states(rec)['test_red.RedTests::test_boom'], 'failed')


class Hostile(unittest.TestCase):
    def feed_lines(self, lines, tail='', size=11):
        chunks = chunks_from_text('\n'.join(lines) + ('\n' if lines else '') + tail, size=size, fd='err')
        return replay('unittest', chunks, argv=['python', '-m', 'unittest', '-v'])[0]

    def test_docstring_form_name_on_its_own_line(self):
        rec = self.feed_lines(['test_x (m.C.test_x)', 'First line of the docstring ... ok', 'test_y (m.C.test_y)', 'Doc ... FAIL'])
        self.assertEqual(final_states(rec), {'m.C::test_x': 'passed', 'm.C::test_y': 'failed'})

    def test_output_of_a_test_between_the_start_and_the_result(self):
        rec = self.feed_lines(['test_x (m.C.test_x) ... hello from the test', 'ok', 'test_y (m.C.test_y) ... ', 'progress 50%', 'FAIL'])
        self.assertEqual(final_states(rec), {'m.C::test_x': 'passed', 'm.C::test_y': 'failed'})
        self.assertEqual(rec.states('m.C::test_y'), ['running', 'failed'])

    def test_ansi_colours_are_ignored(self):
        rec = self.feed_lines(['\x1b[1mtest_x (m.C.test_x)\x1b[0m ... \x1b[32mok\x1b[0m'])
        self.assertEqual(final_states(rec), {'m.C::test_x': 'passed'})

    def test_garbage_built_from_real_fragments_never_raises_and_states_are_valid(self):
        rng = random.Random(3)
        fragments = ['test_x (m.C.test_x)', ' ... ', 'ok', 'FAIL', 'ERROR', "skipped 'r'", 'expected failure', '=' * 70, '-' * 70, 'FAIL: test_x (m.C.test_x)', 'ERROR: setUpClass (m.C)',
                     'Traceback (most recent call last):', '  File "x.py", line 1', 'ValueError: kaput', 'Ran 3 tests in 0.1s', '(i=1)', '[msg]', 'ą', '\x00', '\x1b[3', '\r', ' ', '  ', 'hello']
        for _ in range(300):
            lines = [''.join(rng.choice(fragments) for _ in range(rng.randint(0, 6))) for _ in range(rng.randint(1, 25))]
            rec = self.feed_lines(lines, tail=rng.choice(['', 'test_y (m.C.test_y) ... ', 'ok']))
            for call in rec.tests():
                self.assertIn(call[3], ('running', 'passed', 'failed', 'skipped'))
                self.assertTrue(call[1] and call[2])

    def test_lines_cut_in_the_middle_never_invent_a_state(self):
        meta, chunks = load('unittest-v-mixed.jsonl')
        text = ''.join(c['data'] for c in chunks if c['fd'] == 'err')
        for cut in (7, 45, 160, 333, 700, 1200):
            with self.subTest(cut=cut):
                rec = replay('unittest', chunks_from_text(text[:cut], size=9, fd='err'), argv=meta['command'])[0]
                for test_id, (state, _) in rec.last().items():
                    self.assertIn(test_id, EXPECTED_MIXED)
                    self.assertIn(state, ('running', EXPECTED_MIXED[test_id]))   # a cut test is running or has its real result

    def test_a_very_long_line_is_cheap(self):
        started = time.monotonic()
        rec = self.feed_lines(['test_x (m.C.test_x) ... ' + 'x' * 300000, 'test_y (m.C.test_y) ... ok', 'a (' * 100000 + ' ... ', '[' * 200000 + ' ... '], size=1000)
        self.assertLess(time.monotonic() - started, 3)   # hostile shapes are cut to MAX_LINE before any regex runs
        self.assertEqual(final_states(rec)['m.C::test_y'], 'passed')

    def test_a_very_long_name_is_cut_not_crashing(self):
        rec = self.feed_lines(['test_' + 'a' * 5000 + ' (m.C.' + 'b' * 5000 + ') ... ok'])
        self.assertEqual(rec.tests(), [])   # cut at 2000 characters the line no longer closes its parenthesis: nothing is reported


class LooksLikeUnittest(unittest.TestCase):
    """Output that only resembles unittest must not move a state (the findings of the review)."""

    def run_chunks(self, pieces, exit_code=0):
        chunks = [{'t': i / 100, 'fd': fd, 'data': text} for i, (fd, text) in enumerate(pieces)]
        return replay('unittest', chunks, argv=['python', '-m', 'unittest', '-v'], exit_code=exit_code)[0]

    def test_a_line_printed_by_a_test_does_not_open_a_failure_block(self):
        rec = self.run_chunks([('err', 'test_x (m.C.test_x) ... '), ('out', 'FAIL: test_other (m.C.test_other)\n'), ('out', '-' * 70 + '\n'), ('err', 'ok\n'), ('err', 'test_y (m.C.test_y) ... '), ('err', 'ok\n')])
        self.assertEqual(final_states(rec), {'m.C::test_x': 'passed', 'm.C::test_y': 'passed'})

    def test_a_result_word_on_another_stream_is_not_the_result(self):
        rec = self.run_chunks([('err', 'test_x (m.C.test_x) ... '), ('out', 'ok\n'), ('out', 'FAIL\n'), ('err', "skipped 'why'\n")])
        self.assertEqual(rec.last()['m.C::test_x'], ('skipped', 'why'))   # the stdout lines that look like results are ignored
        self.assertEqual(rec.states('m.C::test_x'), ['running', 'skipped'])

    def test_an_unrecognised_result_is_not_turned_into_a_failure_when_the_run_printed_its_summary(self):
        text = [('err', 'test_a (m.C.test_a) ... '), ('err', 'xok\n'), ('err', 'test_b (m.C.test_b) ... FAIL\n'), ('err', 'Ran 2 tests in 0.1s\n\nFAILED (failures=1)\n')]
        self.assertEqual(self.run_chunks(text, exit_code=1).last()['m.C::test_a'][0], 'running')   # nothing invented
        killed = self.run_chunks(text[:3], exit_code=1)
        self.assertEqual(killed.last()['m.C::test_a'], ('failed', 'no result: the run ended while the test was running'))   # no summary: it did not finish

    def test_every_started_test_is_closed_when_the_run_was_killed(self):
        rec = self.run_chunks([('err', 'test_a (m.C.test_a) ... hello\n'), ('err', 'test_b (m.C.test_b) ... ')], exit_code=-9)
        self.assertEqual({k: v[0] for k, v in rec.last().items()}, {'m.C::test_a': 'failed', 'm.C::test_b': 'failed'})
        ok = self.run_chunks([('err', 'test_a (m.C.test_a) ... hello\n'), ('err', 'test_b (m.C.test_b) ... ')], exit_code=0)
        self.assertEqual({k: v[0] for k, v in ok.last().items()}, {'m.C::test_a': 'running', 'm.C::test_b': 'running'})   # exit 0: nothing invented

    def test_subtests_with_a_message(self):
        lines = ['test_s (m.C.test_s) ... ', "  test_s (m.C.test_s) [step one] (i=1) ... FAIL", '  test_s (m.C.test_s) [only msg] ... FAIL', '  test_s (m.C.test_s) (k=2) ... ok']
        rec = replay('unittest', chunks_from_text('\n'.join(lines) + '\n', size=6, fd='err'), argv=['python', '-m', 'unittest', '-v'])[0]
        self.assertEqual(final_states(rec), {'m.C::test_s': 'failed', 'm.C::test_s [step one] (i=1)': 'failed', 'm.C::test_s [only msg]': 'failed', 'm.C::test_s (k=2)': 'passed'})

    def test_subtest_parameters_may_contain_parentheses_and_brackets(self):
        lines = ['test_s (m.C.test_s) ... ', '  test_s (m.C.test_s) (pair=(1, 2)) ... FAIL', '  test_s (m.C.test_s) [a] b]] (k=[1]) ... FAIL', '', '=' * 70,
                 'FAIL: test_s (m.C.test_s) (pair=(1, 2))', '-' * 70, 'AssertionError: no', '', '-' * 70, 'Ran 1 test in 0s']
        rec = replay('unittest', chunks_from_text('\n'.join(lines) + '\n', size=7, fd='err'), argv=['python', '-m', 'unittest', '-v'], exit_code=1)[0]
        last = rec.last()
        self.assertEqual(last['m.C::test_s (pair=(1, 2))'], ('failed', 'AssertionError: no'))
        self.assertEqual(last['m.C::test_s'][0], 'failed')   # no test is left `running`
        self.assertNotIn('running', {v[0] for v in last.values()})

    def test_a_run_interrupted_before_an_exit_code_closes_the_started_test(self):
        rec = replay('unittest', [{'t': 0, 'fd': 'err', 'data': 'test_a (m.C.test_a) ... '}], argv=['python', '-m', 'unittest', '-v'], exit_code=None)[0]
        self.assertEqual(rec.last()['m.C::test_a'][0], 'failed')

    def test_the_old_qualified_shape_and_setup_blocks(self):
        lines = ['test_init (m.A) ... ok', 'test_init (m.B) ... FAIL', '', '=' * 70, 'ERROR: setUpClass (m.C)', '-' * 70, 'Traceback (most recent call last):',
                 '  File "x.py", line 1, in setUpClass', 'RuntimeError: no database', '', '=' * 70, 'ERROR: setUpModule (m)', '-' * 70, 'ValueError: bad module', '', '-' * 70, 'Ran 2 tests in 0s']
        rec = replay('unittest', chunks_from_text('\n'.join(lines) + '\n', size=8, fd='err'), argv=['python', '-m', 'unittest', '-v'])[0]
        last = rec.last()
        self.assertEqual({k: v[0] for k, v in last.items()}, {'m.A::test_init': 'passed', 'm.B::test_init': 'failed', 'm.C::setUpClass': 'failed', 'm::setUpModule': 'failed'})
        self.assertEqual((last['m.C::setUpClass'][1], last['m::setUpModule'][1]), ('RuntimeError: no database', 'ValueError: bad module'))

    def test_a_dotted_failed_test_name(self):
        rec = replay('unittest', chunks_from_text('pkg.test_a (unittest.loader._FailedTest.pkg.test_a) ... ERROR\n', size=5, fd='err'), argv=['python', '-m', 'unittest', '-v'])[0]
        self.assertEqual(final_states(rec), {'unittest.loader._FailedTest::pkg.test_a': 'failed'})

    def test_failure_messages_of_docstring_tests_chained_exceptions_and_import_errors(self):
        lines = ['', '=' * 70, 'FAIL: test_x (m.C.test_x)', 'First line of the docstring', '-' * 70, 'Traceback (most recent call last):', '  File "a.py", line 1, in test_x', '    self.assertEqual(1, 2)',
                 'AssertionError: 1 != 2', '', '=' * 70, 'ERROR: test_y (m.C.test_y)', '-' * 70, 'Traceback (most recent call last):', '  File "a.py", line 3, in f', 'KeyError: 1', '',
                 'During handling of the above exception, another exception occurred:', '', 'Traceback (most recent call last):', '  File "a.py", line 5, in test_y', 'ValueError: final', '',
                 '=' * 70, 'ERROR: test_z (unittest.loader._FailedTest.test_z)', '-' * 70, 'ImportError: Failed to import test module: test_z', 'Traceback (most recent call last):',
                 '  File "loader.py", line 1', "ModuleNotFoundError: No module named 'nope'", '', '-' * 70, 'Ran 3 tests in 0s']
        last = replay('unittest', chunks_from_text('\n'.join(lines) + '\n', size=10, fd='err'), argv=['python', '-m', 'unittest', '-v'])[0].last()
        self.assertEqual(last['m.C::test_x'][1], 'AssertionError: 1 != 2')
        self.assertEqual(last['m.C::test_y'][1], 'ValueError: final')
        self.assertEqual(last['unittest.loader._FailedTest::test_z'][1], "ModuleNotFoundError: No module named 'nope'")

    def test_text_that_resembles_a_result_is_not_one(self):
        rec = self.run_chunks([('err', 'test_x (m.C.test_x) ... skipped 3 files\n'), ('err', 'ok\n'), ('err', 'Loading (cfg) ... '), ('err', 'done\n')])
        self.assertEqual(final_states(rec), {'m.C::test_x': 'passed'})   # `skipped 3 files` is output, `Loading (cfg)` is not a test
        self.assertEqual(rec.states('m.C::test_x'), ['running', 'passed'])

    def test_failure_blocks_with_rules_inside_the_traceback(self):
        lines = ['', '=' * 70, 'FAIL: test_x (m.C.test_x)', '-' * 70, 'Traceback (most recent call last):', 'AssertionError: first', '=' * 7 + ' printed by the test', 'ValueError: later', '',
                 '-' * 70, 'Ran 1 test in 0s']
        last = replay('unittest', chunks_from_text('\n'.join(lines) + '\n', size=10, fd='err'), argv=['python', '-m', 'unittest', '-v'])[0].last()
        self.assertEqual(last['m.C::test_x'][0], 'failed')

    def test_the_table_of_seen_tests_is_bounded(self):
        lines = '\n'.join(f'test_a{i} (m.C.test_a{i}) ... ok' for i in range(60000)) + '\n'
        rec, adapter, *_ = replay('unittest', chunks_from_text(lines, size=4096, fd='err'), argv=['python', '-m', 'unittest', '-v'])
        self.assertLessEqual(len(adapter.seen), 50000)

    def test_partial_with_a_cut_ansi_escape(self):
        rec = self.run_chunks([('err', 'test_x (m.C.test_x) ... \x1b[3'), ('err', '2mok\x1b[0m\n')])
        self.assertEqual(rec.last().get('m.C::test_x', (None,))[0] in (None, 'passed'), True)


class OnTheRealTool(unittest.TestCase):
    """`skillsync test-run` over the real `python -m unittest -v` of a tiny project: events in order, exit code of the command untouched, nothing written elsewhere."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='adapter-unittest-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp) / 'p'
        (self.root / '.github').mkdir(parents=True)
        (self.root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
        (self.root / '.skillsync/runs/R1').mkdir(parents=True)
        (self.root / 'test_tiny.py').write_text(
            'import time, unittest\n\n\nclass T(unittest.TestCase):\n    def test_a_ok(self):\n        pass\n\n    def test_b_slow(self):\n        time.sleep(0.5)\n\n'
            '    def test_c_red(self):\n        self.assertEqual(1, 2)\n\n    @unittest.skip("why")\n    def test_d_skip(self):\n        pass\n', encoding='utf-8')
        self.env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'XDG_DATA_HOME': f'{self.tmp}/d', 'XDG_CACHE_HOME': f'{self.tmp}/c', 'SKILLSYNC_CONFIG': f'{self.tmp}/cfg.yaml'}

    def run_it(self, *extra):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', 'F1.1', *extra, '--', sys.executable, '-m', 'unittest', '-v', 'test_tiny'],
                              cwd=str(self.root), capture_output=True, text=True, env=self.env, timeout=120)

    def test_events_exit_code_and_files(self):
        before = {str(p.relative_to(self.root)) for p in self.root.rglob('*')}
        done = self.run_it()
        self.assertEqual(done.returncode, 1, done.stderr)   # the exit code of the command: one test failed
        path = self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl'
        events = [e for e in ev.read_events(path)[0] if e['t'] == 'test']
        seq = [(e['id'].split('::')[1], e['state']) for e in events]
        self.assertEqual(seq.index(('test_b_slow', 'running')) < seq.index(('test_b_slow', 'passed')), True)
        last = {}
        for name, state in seq:
            last[name] = state
        self.assertEqual(last, {'test_a_ok': 'passed', 'test_b_slow': 'passed', 'test_c_red': 'failed', 'test_d_skip': 'skipped'})
        start = ev.read_attempt(path).start
        self.assertEqual((start['adapter'], start['live']), ('unittest', 'running'))
        new = {str(p.relative_to(self.root)) for p in self.root.rglob('*')} - before
        self.assertTrue(all(p.startswith('.skillsync/') for p in new))

    def test_format_none_runs_the_same_command_without_the_adapter(self):
        done = self.run_it('--format', 'none')
        self.assertEqual(done.returncode, 1)
        attempt = ev.read_attempt(self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl')
        self.assertNotIn('adapter', attempt.start)


class ThroughTheWriter(unittest.TestCase):
    def test_only_the_run_directory_is_written(self):
        meta, chunks = load('unittest-v-mixed.jsonl')
        events, tree = ar.replay_to_files('unittest', chunks, argv=meta['command'])
        self.assertTrue(all(name.startswith('.skillsync/runs/R1/tests/') for name in tree), tree)
        self.assertTrue(events)


if __name__ == '__main__':
    unittest.main()
