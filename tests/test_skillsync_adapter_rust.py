"""180-A4: the adapters of `cargo test` (libtest, default format) on recorded streams (cargo / rustc 1.97.1)."""
import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'rust.py'
LIB = {'tests::ok': 'passed', 'tests::fails': 'failed', 'tests::prints': 'passed', 'tests::ignored': 'skipped', 'tests::ignored_without_reason': 'skipped', 'tests::slow_ok': 'passed',
       'tests::slow_fail': 'failed', 'tests::expects_a_panic': 'passed', 'tests::expected_panic_did_not_happen': 'failed', 'tests::nested::deep': 'passed', 'tests::nested::żółć_unicode': 'passed'}
GREEN = {'green_one': 'passed', 'green_two': 'passed'}
INTEGRATION = {'integration_ok': 'passed', 'integration_fails': 'failed'}
DOC = {'src/lib.rs - add (line 5)': 'passed', 'src/lib.rs - broken_doc (line 14)': 'failed'}
EVERYTHING = {**LIB, **GREEN, **INTEGRATION, **DOC}


def final_states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


def run(fixture, adapter=None, **kw):
    meta, chunks = load(fixture)
    return replay(adapter or ('cargo-test-serial' if any('--test-threads=1' in w for w in meta['command']) else 'cargo-test'), chunks, argv=meta['command'], tool='cargo',
                  exit_code=meta['exit'], **kw)


class Registration(unittest.TestCase):
    def test_two_adapters_chosen_by_the_thread_count_in_the_arguments(self):
        plain = test_adapters.resolve('auto', ['cargo', 'test'], 'cargo')
        serial = test_adapters.resolve('auto', ['cargo', 'test', '--', '--test-threads=1'], 'cargo')
        self.assertEqual((plain.name, plain.live), ('cargo-test', 'completion'))
        self.assertEqual((serial.name, serial.live), ('cargo-test-serial', 'running'))
        self.assertEqual(test_adapters.resolve('auto', ['cargo', 'test', '--', '--test-threads', '1'], 'cargo').name, 'cargo-test-serial')
        self.assertEqual(test_adapters.resolve('auto', ['cargo', 'test', '--', '--test-threads=4'], 'cargo').name, 'cargo-test')
        self.assertEqual(test_adapters.resolve('auto', ['RUST_TEST_THREADS=1', 'cargo', 'test'], 'cargo').name, 'cargo-test-serial')   # visible when the command is written as a string
        self.assertEqual(test_adapters.resolve('auto', ['cargo', 'test', '--', '--test-threads=01'], 'cargo').name, 'cargo-test-serial')

    def test_which_commands_are_cargo_test(self):
        for argv, expected in ((['cargo', 'test'], True), (['cargo', '+nightly', 'test', '--lib'], True), (['cargo', 't'], True), (['/opt/tools/bin/cargo', 'test'], True),
                               (['cargo.exe', 'test'], True), (['cargo', '--offline', 'test'], True), (['cargo', 'nextest', 'run'], False), (['cargo', 'build'], False),
                               (['cargo', 'run', '--', 'test'], False), (['cargo'], False), (['make', 'test'], False), (['cargo', 'nextest', 'run', '--', '--test-threads=1'], False),
                               (['cargo', 'build', '--test-threads=1'], False), (['make', 'test', '--test-threads=1'], False), (['cargo', '-Z', 'unstable-options', 'test'], True),
                               (['cargo', '--color', 'always', 'test'], True), (['cargo', '--config', 'k=v', 't'], True)):
            with self.subTest(argv=argv):
                self.assertEqual(test_adapters.resolve('auto', argv, 'cargo') is not None, expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class SeveralThreads(unittest.TestCase):
    def test_all_binaries_and_doc_tests_in_one_run(self):
        rec, *_ = run('rust-no-fail-fast.jsonl')
        self.assertEqual(final_states(rec), EVERYTHING)
        self.assertNotIn('running', [c[3] for c in rec.emitted()])   # the line of a test appears after its end: there is no running state

    def test_groups_come_from_the_headers_of_the_binaries(self):
        rec, *_ = run('rust-no-fail-fast.jsonl')
        groups = {c[1]: c[4] for c in rec.emitted()}
        self.assertEqual((groups['tests::ok'], groups['green_one'], groups['integration_ok'], groups['src/lib.rs - add (line 5)']), ('demo', 'green', 'integration', 'doc-tests demo'))

    def test_failure_messages_and_ignore_reasons(self):
        last = run('rust-no-fail-fast.jsonl')[0].last()
        self.assertEqual(last['tests::fails'][1], 'assertion `left == right` failed left: 2 right: 3')
        self.assertEqual(last['tests::slow_fail'][1], 'slow failure second line')
        self.assertEqual(last['tests::expected_panic_did_not_happen'][1], 'test did not panic as expected at src/lib.rs:67:8')
        self.assertEqual(last['tests::ignored'], ('skipped', 'not ready'))
        self.assertEqual(last['tests::ignored_without_reason'], ('skipped', None))
        self.assertTrue(last['src/lib.rs - broken_doc (line 14)'][1].startswith('assertion `left == right` failed'))

    def test_should_panic_tests_have_one_id(self):
        rec, *_ = run('rust-no-fail-fast.jsonl')
        self.assertIn('tests::expects_a_panic', final_states(rec))
        self.assertFalse([k for k in final_states(rec) if 'should panic' in k])   # the decoration of the result line is not part of the name

    def test_default_run_stops_after_the_first_failing_binary(self):
        self.assertEqual(final_states(run('rust-default-failfast.jsonl')[0]), LIB)

    def test_single_binaries_and_filters(self):
        self.assertEqual(final_states(run('rust-green-only.jsonl')[0]), GREEN)
        self.assertEqual(final_states(run('rust-lib-default.jsonl')[0]), LIB)
        self.assertEqual(final_states(run('rust-filter-one.jsonl')[0]), {'tests::slow_ok': 'passed'})
        self.assertEqual(final_states(run('rust-doc-tests.jsonl')[0]), DOC)

    def test_ignored_runs(self):
        self.assertEqual(final_states(run('rust-ignored-only.jsonl')[0]), {'tests::ignored': 'passed', 'tests::ignored_without_reason': 'passed'})   # `--ignored` runs only them
        rec, *_ = run('rust-include-ignored.jsonl')
        self.assertEqual({k: v for k, v in final_states(rec).items() if k.startswith('tests::ignored')}, {'tests::ignored': 'passed', 'tests::ignored_without_reason': 'passed'})

    def test_quiet_run_names_only_the_failures(self):
        rec, *_ = run('rust-quiet.jsonl')
        self.assertEqual(final_states(rec), {'tests::expected_panic_did_not_happen': 'failed', 'tests::fails': 'failed', 'tests::slow_fail': 'failed'})

    def test_runs_without_test_lines_emit_nothing(self):
        for name in ('rust-compile-error.jsonl', 'rust-json-format-stable.jsonl', 'rust-no-tests-matched.jsonl', 'rust-list-all.jsonl', 'rust-green-list.jsonl'):
            with self.subTest(name=name):
                self.assertEqual(run(name)[0].emitted(), [])

    def test_merged_streams_and_colours(self):
        self.assertEqual(final_states(run('rust-no-fail-fast-merged.jsonl')[0]), EVERYTHING)
        self.assertEqual({k: v for k, v in final_states(run('rust-ansi.jsonl')[0]).items()}, LIB)

    def test_every_chunk_size_gives_the_same_result(self):
        meta, chunks = load('rust-no-fail-fast.jsonl')
        for size in (1, 5, 40):
            with self.subTest(size=size):
                self.assertEqual(final_states(replay('cargo-test', rechunk(chunks, size), argv=meta['command'], tool='cargo', exit_code=101)[0]), EVERYTHING)

    def test_unfinished_lines_are_not_starts_here(self):
        rec, *_ = run('rust-threads1.jsonl', adapter='cargo-test')   # a serial run read as `completion`: the unfinished lines are ignored
        self.assertNotIn('running', [c[3] for c in rec.emitted()])
        self.assertEqual(final_states(rec), EVERYTHING)


class OneThread(unittest.TestCase):
    def test_every_test_that_runs_has_a_start_before_its_result(self):
        rec, *_ = run('rust-threads1.jsonl')
        for name, state in EVERYTHING.items():
            if name in ('tests::ignored', 'tests::ignored_without_reason'):
                continue   # libtest prints an ignored test in one piece
            self.assertEqual(rec.states(name)[0], 'running', name)
            self.assertEqual(rec.states(name)[-1], state, name)
        self.assertEqual(final_states(rec), EVERYTHING)

    def test_the_start_of_the_slow_test_is_seen_while_it_runs(self):
        meta, chunks = load('rust-threads1.jsonl')
        cut = next(i for i, c in enumerate(chunks) if c['data'].endswith('test tests::slow_ok ... '))
        rec, *_ = replay('cargo-test-serial', chunks[:cut + 1], argv=meta['command'], tool='cargo')
        self.assertEqual(rec.states('tests::slow_ok'), ['running'])
        self.assertEqual(rec.states('tests::ok'), ['running', 'passed'])

    def test_text_printed_by_a_test_between_start_and_result(self):
        rec, *_ = run('rust-threads1-nocapture.jsonl')
        self.assertEqual(rec.states('tests::prints'), ['running', 'passed'])   # `test tests::prints ... hello from the test` then `ok` on its own line
        self.assertEqual({k: v for k, v in final_states(rec).items()}, LIB)

    def test_merged_serial_run(self):
        self.assertEqual(final_states(run('rust-threads1-merged.jsonl')[0]), EVERYTHING)

    def test_an_interrupted_run_fails_the_test_that_never_finished(self):
        rec, *_ = run('rust-interrupted.jsonl')
        self.assertEqual(rec.last()['tests::slow_ok'], ('failed', 'no result: the run ended while the test was running'))
        self.assertEqual(rec.states('tests::slow_ok'), ['running', 'failed'])
        self.assertEqual(rec.last()['tests::ok'][0], 'passed')

    def test_a_clean_exit_invents_nothing(self):
        rec, *_ = replay('cargo-test-serial', [{'t': 0, 'fd': 'out', 'data': 'test tests::a ... '}], argv=['cargo', 'test', '--', '--test-threads=1'], exit_code=0)
        self.assertEqual(rec.last()['tests::a'][0], 'running')
        rec, *_ = replay('cargo-test-serial', [{'t': 0, 'fd': 'out', 'data': 'test tests::a ... '}], argv=['cargo', 'test', '--', '--test-threads=1'], exit_code=None)
        self.assertEqual(rec.last()['tests::a'][0], 'failed')


class Synthetic(unittest.TestCase):
    def feed(self, text, adapter='cargo-test', argv=None, size=9, exit_code=1):
        return replay(adapter, chunks_from_text(text, size=size), argv=argv or ['cargo', 'test'], tool='cargo', exit_code=exit_code)[0]

    def test_the_same_name_from_another_binary_gets_the_group_as_a_prefix(self):
        rec = self.feed('     Running tests/a.rs (/x/deps/a-0123456789abcdef)\ntest works ... ok\n     Running tests/b.rs (/x/deps/b-0123456789abcdef)\ntest works ... FAILED\n')
        self.assertEqual(final_states(rec), {'works': 'passed', 'b::works': 'failed'})

    def test_older_single_line_panic_messages(self):
        rec = self.feed("test t ... FAILED\n\nfailures:\n\n---- t stdout ----\nthread 't' panicked at 'boom happened', src/lib.rs:3:5\nnote: run with `RUST_BACKTRACE=1`\n\nfailures:\n    t\n\ntest result: FAILED. 0 passed; 1 failed\n")
        self.assertEqual(rec.last()['t'], ('failed', 'boom happened'))

    def test_bench_lines_and_other_text_are_not_tests(self):
        rec = self.feed('test tests::b ... bench:         123 ns/iter (+/- 4)\ntest result: ok. 0 passed; 0 failed\nrunning 2 tests\ntest-like line\ntests::x: test\n')
        self.assertEqual(rec.emitted(), [])

    def test_a_name_with_dots_and_spaces(self):
        rec = self.feed('test src/lib.rs - Foo::bar (line 12) ... ok\ntest a::b ... ... ok\n')
        self.assertEqual(set(final_states(rec)), {'src/lib.rs - Foo::bar (line 12)', 'a::b ...'})

    def test_garbage_does_not_raise_and_states_are_valid(self):
        rng = random.Random(9)
        fragments = ['test ', ' ... ', 'ok', 'FAILED', 'ignored', ', why', 'failures:', '---- t stdout ----', '    t', 'test result: ok.', '     Running unittests src/lib.rs (/x/deps/d-0123456789abcdef)',
                     "thread 't' panicked at src/lib.rs:1:1:", 'boom', 'tests::a', ' --- FAILED', '\x1b[32m', 'ą', '\x00', ' ']
        for _ in range(250):
            lines = ''.join(''.join(rng.choice(fragments) for _ in range(rng.randint(0, 5))) + '\n' for _ in range(rng.randint(1, 20)))
            for adapter in ('cargo-test', 'cargo-test-serial'):
                for call in self.feed(lines, adapter=adapter, size=rng.randint(1, 20)).emitted():
                    self.assertIn(call[3], ('running', 'passed', 'failed', 'skipped'))
                    self.assertTrue(call[1])

    def test_lines_cut_in_the_middle_never_invent_a_state(self):
        text = ''.join(c['data'] for c in load('rust-no-fail-fast.jsonl')[1] if c['fd'] == 'out')
        for cut in (40, 300, 800, 1500, 2600):
            with self.subTest(cut=cut):
                for ident, (state, _) in self.feed(text[:cut]).last().items():
                    self.assertEqual(state, EVERYTHING.get(ident, state) if ident in EVERYTHING else state)
                    self.assertIn(ident, EVERYTHING)

    def test_hostile_long_lines_are_cheap(self):
        started = time.monotonic()
        self.feed('test ' + 'a ... ' * 330 + '\n' + 'test ' + 'x' * 300000 + ' ... ok\n' + ('test a ... ' * 190 + '\n') * 40 + '---- ' * 400 + '\n', size=997)
        self.assertLess(time.monotonic() - started, 3)

    def test_the_table_of_seen_tests_is_bounded(self):
        lines = ''.join(f'test t{i} ... ok\n' for i in range(60000))
        rec, adapter, *_ = replay('cargo-test', chunks_from_text(lines, size=8192), argv=['cargo', 'test'], tool='cargo')
        self.assertLessEqual(len(adapter.seen), 50000)

    def test_failures_of_an_old_terse_run_come_from_the_list_only(self):
        rec = self.feed('running 2 tests\n.F\nfailures:\n\n---- tests::x stdout ----\n\nfailures:\n    tests::x\n\ntest result: FAILED. 1 passed; 1 failed\n')
        self.assertEqual(final_states(rec), {'tests::x': 'failed'})

    def test_a_long_panic_message_is_cut_to_three_lines(self):
        rec = self.feed("test t ... FAILED\n\nfailures:\n\n---- t stdout ----\nthread 't' panicked at src/lib.rs:1:1:\nl1\nl2\nl3\nl4\nl5\n\nfailures:\n    t\n")
        self.assertEqual(rec.last()['t'], ('failed', 'l1 l2 l3'))

    def test_show_output_never_turns_a_pass_into_a_failure(self):
        text = ('     Running unittests src/lib.rs (/x/deps/demo-0123456789abcdef)\n\nrunning 2 tests\ntest tests::a - should panic ... ok\ntest tests::b ... ok\n\nsuccesses:\n\n'
                "---- tests::a stdout ----\nthread 'tests::a' (1) panicked at src/lib.rs:62:9:\nboom\nnote: run with `RUST_BACKTRACE=1`\n\n---- tests::b stdout ----\nhello\n\n"
                'successes:\n    tests::a\n    tests::b\n\ntest result: ok. 2 passed; 0 failed\n')
        rec = self.feed(text, exit_code=0)
        self.assertEqual(final_states(rec), {'tests::a': 'passed', 'tests::b': 'passed'})
        self.assertEqual(rec.states('tests::a'), ['passed'])
        failing = ('test tests::c ... FAILED\n\nsuccesses:\n\n---- tests::a stdout ----\n\nsuccesses:\n    tests::a\n\nfailures:\n\n---- tests::c stdout ----\n'
                   "thread 'tests::c' (2) panicked at src/lib.rs:5:5:\nreal failure\n\nfailures:\n    tests::c\n\ntest result: FAILED. 0 passed; 1 failed\n")
        self.assertEqual(self.feed(failing).last()['tests::c'], ('failed', 'real failure'))

    def test_blocks_of_the_successes_section_name_no_failure_even_without_a_result_line(self):
        rec = self.feed("\nsuccesses:\n\n---- tests::unseen stdout ----\nthread 'x' (1) panicked at src/lib.rs:1:1:\nboom\n\nsuccesses:\n    tests::unseen\n\ntest result: ok. 1 passed\n", exit_code=0)
        self.assertEqual(rec.emitted(), [])

    def test_a_block_never_takes_back_a_pass(self):
        rec = self.feed("test tests::a ... ok\n\nfailures:\n\n---- tests::a stdout ----\nthread 'x' (1) panicked at src/lib.rs:1:1:\nboom\n\nfailures:\n    tests::a\n", exit_code=0)
        self.assertEqual(final_states(rec), {'tests::a': 'passed'})

    def test_the_same_name_in_two_binaries_with_the_same_label(self):
        text = ('     Running unittests src/lib.rs (/x/deps/demo-aaaaaaaaaaaaaaaa)\ntest tests::it_works ... FAILED\n\ntest result: FAILED. 0 passed; 1 failed\n'
                '     Running unittests src/main.rs (/x/deps/demo-bbbbbbbbbbbbbbbb)\ntest tests::it_works ... ok\n\ntest result: ok. 1 passed; 0 failed\n')
        rec = self.feed(text)
        self.assertEqual(sorted(final_states(rec).values()), ['failed', 'passed'])   # the failure of the first binary is not overwritten by the pass of the second
        self.assertEqual(len(final_states(rec)), 2)

    def test_a_started_test_whose_binary_died_is_failed_when_the_next_binary_starts(self):
        text = ('     Running tests/a.rs (/x/deps/a-0123456789abcdef)\ntest crashes ... ')
        pieces = [{'t': 0, 'fd': 'out', 'data': text}, {'t': 1, 'fd': 'err', 'data': '\n     Running tests/b.rs (/x/deps/b-0123456789abcdef)\n'}, {'t': 2, 'fd': 'out', 'data': '\ntest fine ... ok\n'}]
        rec = replay('cargo-test-serial', pieces, argv=['cargo', 'test', '--', '--test-threads=1'], tool='cargo', exit_code=0)[0]
        self.assertEqual(rec.last()['crashes'], ('failed', 'no result: the test binary ended without a result for this test'))
        self.assertEqual(rec.last()['fine'][0], 'passed')

    def test_a_start_that_arrives_with_its_line_end_and_a_lone_result(self):
        for size in (1, 4, 1000):
            with self.subTest(size=size):
                rec = replay('cargo-test-serial', chunks_from_text('test t::a ... hello from the test\nok\ntest t::b ... \nFAILED\n', size=size), argv=['cargo', 'test', '--', '--test-threads=1'],
                             tool='cargo')[0]
                self.assertEqual(final_states(rec), {'t::a': 'passed', 't::b': 'failed'})
                self.assertEqual(rec.states('t::a')[0], 'running')

    def test_serial_nocapture_run_reads_the_same_with_any_chunking(self):
        meta, chunks = load('rust-threads1-nocapture.jsonl')
        for size in (1, 3, 60):
            with self.subTest(size=size):
                rec = replay('cargo-test-serial', rechunk(chunks, size), argv=meta['command'], tool='cargo', exit_code=101)[0]
                self.assertEqual(final_states(rec), LIB)

    def test_benchmarks_and_report_time_suffixes(self):
        rec = replay('cargo-test-serial', chunks_from_text('test b ... \ntest b ... bench:         123 ns/iter (+/- 4)\ntest t ... ok <0.002s>\n', size=7), argv=['cargo', 'test', '--', '--test-threads=1'], tool='cargo')[0]
        self.assertEqual(final_states(rec), {'b': 'passed', 't': 'passed'})   # a benchmark is not left `running`

    def test_a_windows_path_with_parentheses_in_the_header(self):
        rec = self.feed('     Running tests\\x.rs (C:\\Program Files (x86)\\t\\deps\\x-0123456789abcdef.exe)\ntest w ... ok\n')
        self.assertEqual([c[4] for c in rec.emitted()], ['x'])

    def test_terse_failures_lose_the_should_panic_decoration(self):
        rec = self.feed('tests::p - should panic --- FAILED\n')
        self.assertEqual(list(final_states(rec)), ['tests::p'])

    def test_a_failure_block_without_a_message_still_reports_nothing_extra(self):
        rec = self.feed('test t ... FAILED\n\nfailures:\n\n---- t stdout ----\n\nfailures:\n    t\n')
        self.assertEqual(final_states(rec), {'t': 'failed'})


class ThroughTheWriter(unittest.TestCase):
    def test_only_the_run_directory_is_written(self):
        meta, chunks = load('rust-threads1.jsonl')
        events, tree = ar.replay_to_files('cargo-test-serial', chunks, argv=meta['command'], tool='cargo')
        self.assertTrue(events)
        self.assertTrue(all(name.startswith('.skillsync/runs/R1/tests/') for name in tree), tree)


if __name__ == '__main__':
    unittest.main()
