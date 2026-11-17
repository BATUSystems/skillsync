"""180-A2: the adapter of `dotnet test` (VSTest and Microsoft.Testing.Platform) on recorded streams (SDK 10.0.114, VSTest 18.0.2 / xunit 3.1.5, MTP 2.5.1)
and against the TRX reports of the same runs. The console is localized (pl_PL): the adapter asks for English and reads by structure."""
import random
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text, rechunk, stream_text  # noqa: E402

from skillsync_workflow import test_adapters, test_reports  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'dotnet.py'
NOCOLLECT = lambda argv, timeout=30: None   # noqa: E731
GREEN, RED, SKIP, THEORY = 'Demo.GreenTests::Demo.GreenTests.', 'Demo.RedTests::Demo.RedTests.', 'Demo.SkippedTests::Demo.SkippedTests.', 'Demo.TheoryTests::Demo.TheoryTests.'
RED_IDS = {RED + 'Boom', RED + 'Throws', RED + 'SlowRed', THEORY + 'Same(a: 3, b: 4)'}


def final_states(recorder):
    return {k: v[0] for k, v in recorder.last().items()}


def trx_states(name):
    return {r['id']: r['state'] for r in test_reports.parse_trx(ar.FIXTURES / name)}


class Registration(unittest.TestCase):
    def test_name_liveness_and_environment(self):
        adapter = test_adapters.resolve('auto', ['dotnet', 'test', '--no-build'], 'dotnet')
        self.assertEqual((adapter.name, adapter.live), ('dotnet', 'completion'))   # no per-test start line exists in either console
        self.assertEqual(adapter.env(), {'DOTNET_CLI_UI_LANGUAGE': 'en', 'VSLANG': '1033'})

    def test_accepts_dotnet_test_only(self):
        cls = test_adapters.REGISTRY['dotnet']
        for argv, expected in ((['dotnet', 'test'], True), (['dotnet', 'test', 'x.sln', '--no-build'], True), (['dotnet', 'build'], False), (['dotnet', 'test', '--list-tests'], False),
                               (['dotnet', 'run'], False), (['dotnet', 'run', 'test'], False), (['dotnet', 'build', 'test'], False), (['dotnet', 'watch', 'test'], False),
                               (['/usr/share/dotnet/dotnet', 'test'], True), (['C:\\Program Files\\dotnet\\dotnet.exe', 'test'], True), (['test'], False)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class VSTest(unittest.TestCase):
    def test_no_state_running_exists_in_any_recording(self):
        for name in ('dotnet-vstest-green-normal-en.jsonl', 'dotnet-vstest-red-detailed-en.jsonl', 'dotnet-vstest-all-detailed-en.jsonl', 'dotnet-vstest-all-normal-pl.jsonl',
                     'dotnet-mtp-mixed-en.jsonl', 'dotnet-mtp-mixed-detailed-en.jsonl'):
            with self.subTest(name=name):
                rec, *_ = replay_fixture('dotnet', name, collect=NOCOLLECT)
                self.assertNotIn('running', [c[3] for c in rec.emitted()])

    def test_default_verbosity_prints_no_tests_and_nothing_is_invented(self):
        for name in ('dotnet-vstest-green-default-en.jsonl', 'dotnet-vstest-green-default-pl.jsonl'):
            rec, *_ = replay_fixture('dotnet', name, collect=NOCOLLECT)
            self.assertEqual(rec.emitted(), [])

    def test_green_normal(self):
        rec, *_ = replay_fixture('dotnet', 'dotnet-vstest-green-normal-en.jsonl', collect=NOCOLLECT)
        self.assertEqual(final_states(rec), {GREEN + 'Adds': 'passed', GREEN + 'Prints': 'passed', GREEN + 'SlowOne': 'passed'})

    def test_every_console_language_gives_the_same_result(self):
        want = {GREEN + 'Adds': 'passed', GREEN + 'Prints': 'passed', GREEN + 'SlowOne': 'passed'}
        for name in ('dotnet-vstest-green-normal-pl.jsonl', 'dotnet-vstest-green-detailed-en.jsonl', 'dotnet-vstest-green-detailed-c.jsonl'):
            with self.subTest(name=name):
                self.assertEqual(final_states(replay_fixture('dotnet', name, collect=NOCOLLECT)[0]), want)

    def test_red_and_skipped_are_known_from_the_stderr_line_before_the_console_result(self):
        meta, chunks = load('dotnet-vstest-red-detailed-en.jsonl')
        first_console_result = next(i for i, c in enumerate(chunks) if c['fd'] == 'out' and '  Failed Demo.RedTests.Throws' in c['data'])
        rec, *_ = replay('dotnet', chunks[:first_console_result], argv=meta['command'], collect=NOCOLLECT)
        self.assertEqual(final_states(rec), {RED + 'Throws': 'failed', RED + 'Boom': 'failed'})   # before any result line of the console batch
        self.assertEqual([c[6] for c in rec.emitted()], [None, None])

    def test_the_console_result_adds_the_message_without_a_second_result(self):
        rec, *_ = replay_fixture('dotnet', 'dotnet-vstest-red-detailed-en.jsonl', collect=NOCOLLECT)
        for ident in (RED + 'Throws', RED + 'Boom', RED + 'SlowRed'):
            calls = [c for c in rec.emitted() if c[1] == ident]
            self.assertEqual([c[3] for c in calls], ['failed', 'failed'])
            self.assertEqual([c[6] is not None for c in calls], [False, True])   # the message arrives once
        last = rec.last()
        self.assertEqual(last[RED + 'Throws'][1], 'System.InvalidOperationException : kaput')
        self.assertTrue(last[RED + 'Boom'][1].startswith('Assert.Equal() Failure: Values differ'))
        self.assertLessEqual(len(last[RED + 'SlowRed'][1]), 500)

    def test_merged_streams_give_the_same_result(self):
        rec, *_ = replay_fixture('dotnet', 'dotnet-vstest-red-detailed-merged-en.jsonl', collect=NOCOLLECT)
        self.assertEqual(final_states(rec), {RED + 'Boom': 'failed', RED + 'Throws': 'failed', RED + 'SlowRed': 'failed'})

    def test_skipped_and_its_reason(self):
        last = replay_fixture('dotnet', 'dotnet-vstest-skipped-detailed-en.jsonl', collect=NOCOLLECT)[0].last()
        self.assertEqual(last[SKIP + 'Later'], ('skipped', 'not ready'))
        self.assertEqual(last[SKIP + 'Ok'][0], 'passed')
        pl = replay_fixture('dotnet', 'dotnet-vstest-skipped-normal-pl.jsonl', collect=NOCOLLECT)[0]
        self.assertEqual(final_states(pl), {SKIP + 'Later': 'skipped', SKIP + 'Ok': 'passed'})

    def test_theory_rows_keep_their_arguments_unicode_and_brackets(self):
        got = final_states(replay_fixture('dotnet', 'dotnet-vstest-theory-detailed-en.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual(got, {THEORY + 'Same(a: 1, b: 1)': 'passed', THEORY + 'Same(a: 2, b: 2)': 'passed', THEORY + 'Same(a: 3, b: 4)': 'failed',
                               THEORY + 'Length(s: "ąę ść", n: 5)': 'passed', THEORY + 'Length(s: "a b", n: 3)': 'passed',
                               THEORY + 'Complex(xs: [1, 2])': 'passed', THEORY + 'Complex(xs: [3])': 'passed'})

    def test_ids_and_states_are_those_of_the_trx_of_the_same_run(self):
        mine = final_states(replay_fixture('dotnet', 'dotnet-vstest-all-detailed-en.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual(mine, trx_states('dotnet-vstest-all.trx'))   # the TRX overwrites every entry with the same state
        pl = final_states(replay_fixture('dotnet', 'dotnet-vstest-all-normal-pl.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual({k: v for k, v in pl.items()}, {k: v for k, v in trx_states('dotnet-vstest-all.trx').items()})

    def test_every_chunk_size_gives_the_same_result(self):   # the adapter reads complete lines only (no `partial`): this checks the line assembly of the replay, not a state machine
        meta, chunks = load('dotnet-vstest-all-detailed-en.jsonl')
        want = trx_states('dotnet-vstest-all.trx')
        for size in (1, 4, 61):
            with self.subTest(size=size):
                self.assertEqual(final_states(replay('dotnet', rechunk(chunks, size), argv=meta['command'], collect=NOCOLLECT)[0]), want)

    def test_runs_without_results_emit_nothing(self):
        for name in ('dotnet-vstest-nomatch-en.jsonl', 'dotnet-vstest-build-error-en.jsonl', 'dotnet-vstest-missing-project-en.jsonl', 'dotnet-vstest-list-en.jsonl'):
            with self.subTest(name=name):
                self.assertEqual(replay_fixture('dotnet', name, collect=NOCOLLECT)[0].emitted(), [])

    def test_an_interrupted_run_reports_what_was_read(self):
        rec, *_ = replay_fixture('dotnet', 'dotnet-vstest-interrupted-en.jsonl', collect=NOCOLLECT)
        got = final_states(rec)
        self.assertEqual({k: v for k, v in got.items() if v == 'failed'}, {RED + 'Boom': 'failed', RED + 'Throws': 'failed'})   # the red tests read before the interrupt
        self.assertTrue(set(got.values()) <= {'failed', 'skipped', 'passed'})


class MTP(unittest.TestCase):
    def test_normal_output_prints_only_failed_and_skipped(self):
        got = final_states(replay_fixture('dotnet', 'dotnet-mtp-mixed-en.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual(got, {SKIP + 'Later': 'skipped', **{k: 'failed' for k in RED_IDS}})   # passed tests are not printed: the TRX adds them

    def test_detailed_output_names_every_test_and_matches_the_trx(self):
        got = final_states(replay_fixture('dotnet', 'dotnet-mtp-mixed-detailed-en.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual(got, trx_states('dotnet-mtp-all.trx'))

    def test_localized_output_is_read_by_structure(self):
        got = final_states(replay_fixture('dotnet', 'dotnet-mtp-mixed-pl.jsonl', collect=NOCOLLECT)[0])
        self.assertEqual(got, {SKIP + 'Later': 'skipped', **{k: 'failed' for k in RED_IDS}})

    def test_green_normal_prints_only_the_assembly_line(self):
        self.assertEqual(replay_fixture('dotnet', 'dotnet-mtp-green-en.jsonl', collect=NOCOLLECT)[0].emitted(), [])


class Collector(unittest.TestCase):
    def test_vstest_list_is_read_by_structure_in_either_language(self):
        want = sorted(trx_states('dotnet-vstest-all.trx'))
        for name in ('dotnet-vstest-list-en.jsonl', 'dotnet-vstest-list-pl.jsonl'):
            with self.subTest(name=name):
                rec, *_ = replay('dotnet', [], argv=['dotnet', 'test', '--no-build'], collect=lambda argv, timeout=30, n=name: stream_text(n))
                collected = [c for c in rec.calls if c[0] == 'collected'][0]
                self.assertEqual((collected[1], sorted(collected[2])), (15, want))

    def test_mtp_list_matches_too(self):
        rec, *_ = replay('dotnet', [], argv=['dotnet', 'test', '--no-build'], collect=lambda argv, timeout=30: stream_text('dotnet-mtp-list-en.jsonl'))
        self.assertEqual(sorted([c for c in rec.calls if c[0] == 'collected'][0][2]), sorted(trx_states('dotnet-mtp-all.trx')))

    def collector_argv(self, argv):
        seen = []
        replay('dotnet', [], argv=argv, collect=lambda a, timeout=30: seen.append(a))
        return seen

    def test_only_arguments_that_select_tests_reach_the_collector(self):
        self.assertEqual(self.collector_argv(['dotnet', 'test', 'x.sln', '--no-build', '--filter', 'Name~A', '--logger', 'trx;LogFileName=a.trx', '--results-directory', 'out', '-c', 'Release',
                                              '--collect', 'XPlat Code Coverage', '--blame', '--', 'RunConfiguration.X=1']),
                         [['dotnet', 'test', 'x.sln', '--no-build', '--filter', 'Name~A', '-c', 'Release', '--list-tests']])
        self.assertEqual(self.collector_argv(['dotnet', 'test', '--no-build', '--filter=Name~A', '--configuration=Release', 'tests/Foo.csproj', '--no-restore']),
                         [['dotnet', 'test', '--no-build', '--filter=Name~A', '--configuration=Release', 'tests/Foo.csproj', '--no-restore', '--list-tests']])

    def test_flags_with_values_are_dropped_and_arguments_with_backslashes_are_normalised(self):
        self.assertEqual(self.collector_argv(['dotnet', 'test', '--no-build', '-v', 'n', '-l', 'trx', '-a', 'adapters', '-s', 'x.runsettings', '-d', 'log.txt', 'a.csproj']),
                         [['dotnet', 'test', '--no-build', 'a.csproj', '--list-tests']])
        rec, *_ = replay('dotnet', [], argv=['dotnet', 'test', '--no-build'], collect=lambda a, timeout=30: '  Demo.T.Open(path: "C:\\x\\y")\n')
        self.assertEqual([c for c in rec.calls if c[0] == 'collected'][0][2], ['Demo.T::Demo.T.Open(path: "C:/x/y")'])

    def test_no_list_when_the_listing_could_differ_from_the_run_or_would_build(self):
        for argv in (['dotnet', 'test'], ['dotnet', 'test', '--filter', 'A'],   # no --no-build: the listing would build the project
                     ['dotnet', 'test', '--no-build', '--project', 'x.csproj'], ['dotnet', 'test', '--no-build', '--test-modules', 'a.dll'], ['dotnet', 'test', '--no-build', '-p:X=1'],
                     ['dotnet', 'run', 'test', '--no-build'], ['dotnet', 'watch', 'test', '--no-build']):
            with self.subTest(argv=argv):
                self.assertEqual(self.collector_argv(argv), [])

    def test_a_failed_collector_changes_nothing(self):
        self.assertEqual(replay('dotnet', [], argv=['dotnet', 'test'], collect=NOCOLLECT)[0].calls, [])


class Hostile(unittest.TestCase):
    def feed(self, text, size=9):
        return replay('dotnet', chunks_from_text(text, size=size), argv=['dotnet', 'test'], collect=NOCOLLECT)[0]

    def test_unknown_language_words_are_ignored_not_guessed(self):
        rec = self.feed('  Gelukt Demo.A.B [1 ms]\n  Mislukt Demo.A.C [2 ms]\n  Passed Demo.A.D [1,5 s]\n  Failed Demo.A.E [< 1 ms]\n')
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.D': 'passed', 'Demo.A::Demo.A.E': 'failed'})   # durations in any culture shape: never parsed

    def test_ansi_colours(self):
        rec = self.feed('\x1b[32m  Passed Demo.A.B [1 ms]\x1b[0m\n\x1b[31m  Failed Demo.A.C [1 ms]\x1b[0m\n')
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.B': 'passed', 'Demo.A::Demo.A.C': 'failed'})

    def test_lines_that_only_look_like_results(self):
        rec = self.feed('Total tests: 3\n     Passed: 3\nPassed!  - Failed:     0, Passed:     3, Skipped:     0, Total:     3, Duration: 1 s - demo.dll (net10.0)\n'
                        'Test run for /x/demo.dll (.NETCoreApp,Version=v10.0)\nRunning tests from /x/demo.dll (net10.0|x64)\n  from /x/demo.dll (net10.0|x64)\n')
        self.assertEqual(rec.emitted(), [])

    def test_garbage_does_not_raise_and_states_are_valid(self):
        rng = random.Random(11)
        alphabet = list(' \t.()[]{}:,;"\'`<>=_-/\\0123456789ms Passed Failed Skipped passed failed skipped xUnit.net FAIL SKIP Error Message Demo.A') + ['ą', '\x00', '\x1b[0m', '\r', '\n', '\n']
        for _ in range(80):
            rec = self.feed(''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 500))), size=rng.randint(1, 40))
            for call in rec.emitted():
                self.assertIn(call[3], ('passed', 'failed', 'skipped'))

    def test_lines_cut_in_the_middle_never_invent_a_state(self):
        want = trx_states('dotnet-vstest-all.trx')
        text = ''.join(c['data'] for c in load('dotnet-vstest-all-detailed-en.jsonl')[1] if c['fd'] == 'out')
        for cut in (60, 400, 900, 1500, 2300):
            with self.subTest(cut=cut):
                for ident, (state, _) in self.feed(text[:cut], size=17).last().items():
                    self.assertEqual(state, want.get(ident, state))

    def test_a_very_long_line_is_cheap(self):
        started = time.monotonic()
        rec = self.feed('  Passed Demo.A.' + 'x' * 400000 + ' [1 ms]\n  Passed Demo.A.B [1 ms]\n' + ('  Passed Demo.A(' + 'x [1] ' * 300) * 50 + '\n' + 'passed Demo.A (' * 130 + '\n', size=997)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(final_states(rec)['Demo.A::Demo.A.B'], 'passed')

    def test_polish_result_words_map_to_states(self):
        rec = self.feed('  Powodzenie Demo.A.P [1 ms]\n  Niepowodzenie Demo.A.F [1 ms]\n  Pominięto Demo.A.S [1 ms]\n')
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.P': 'passed', 'Demo.A::Demo.A.F': 'failed', 'Demo.A::Demo.A.S': 'skipped'})
        rec = self.feed('zakończone niepowodzeniem Demo.A.M (3ms)\npominięte Demo.A.N (0ms)\n')   # Microsoft.Testing.Platform words
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.M': 'failed', 'Demo.A::Demo.A.N': 'skipped'})

    def test_names_that_cannot_be_matched_with_the_trx_are_not_reported(self):
        rec = self.feed('  Passed Add [3 ms]\n  Failed Adds_two [2 ms]\npassed Subtract (1ms)\n')
        self.assertEqual(rec.emitted(), [])   # NUnit/MSTest print the bare method, DisplayName is free text: the id would not match the TRX one

    def test_nested_and_generic_classes(self):
        rec = self.feed('  Passed Demo.Outer+Inner.Method [1 ms]\n  Passed Demo.Box<int>.Get(x: 1) [1 ms]\n')
        self.assertEqual(final_states(rec), {'Demo.Outer+Inner::Demo.Outer+Inner.Method': 'passed', 'Demo.Box<int>::Demo.Box<int>.Get(x: 1)': 'passed'})

    def test_the_message_of_an_assertion_is_not_a_failing_test(self):
        rec = self.feed('[xUnit.net 00:00:00.46]     Demo.A.B [FAIL]\n[xUnit.net 00:00:00.46]       Assert.True(x, "login shows [FAIL]") [FAIL]\n')
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.B': 'failed'})

    def test_text_printed_by_a_test_after_its_result_is_not_a_result(self):
        rec = self.feed('  Passed Demo.A.B [1 ms]\n  Standard Output Messages:\n  Passed Demo.A.FAKE [1 ms]\nfailed Demo.A.FAKE2 (3ms)\n\n  Passed Demo.A.C [1 ms]\n')
        self.assertEqual(final_states(rec), {'Demo.A::Demo.A.B': 'passed', 'Demo.A::Demo.A.C': 'passed'})

    def test_the_same_test_on_two_target_frameworks_keeps_the_failure(self):
        rec = self.feed('  Failed Demo.A.B [1 ms]\n  Passed Demo.A.B [1 ms]\n  Skipped Demo.A.B [1 ms]\n')
        self.assertEqual(rec.last()['Demo.A::Demo.A.B'][0], 'failed')   # a later pass or skip does not take a failure back (the TRX settles it)
        rec = self.feed('  Passed Demo.A.B [1 ms]\n  Failed Demo.A.B [1 ms]\n')
        self.assertEqual(rec.last()['Demo.A::Demo.A.B'][0], 'failed')

    def test_build_output_and_summaries_of_mtp_are_not_results(self):
        rec = self.feed('Restore complete (0,3s)\n  mtp net10.0 succeeded (4,5s) -> bin/Debug/net10.0/mtp.dll\nwarning CS8618: Non-nullable field \'x\' must contain a value\n'
                        'Test run summary: Failed!\n  total: 15\n  failed: 4\nExit code: 2\nTest run completed with non-success exit code: 2 (see: https://aka.ms/testingplatform/exitcodes)\n')
        self.assertEqual(rec.emitted(), [])

    def test_skipped_tests_of_the_xunit_line_arrive_before_the_console_result(self):
        meta, chunks = load('dotnet-vstest-skipped-detailed-en.jsonl')
        first_console = next(i for i, c in enumerate(chunks) if '  Skipped Demo.SkippedTests.Later' in c['data'] or '  Passed Demo.SkippedTests.Ok' in c['data'])
        rec, *_ = replay('dotnet', chunks[:first_console], argv=meta['command'], collect=NOCOLLECT)
        self.assertEqual(final_states(rec), {SKIP + 'Later': 'skipped'})   # `[SKIP]` of the xUnit adapter (stdout), before the batch of results

    def test_the_table_of_seen_tests_is_bounded(self):
        lines = ''.join(f'  Passed Demo.A.T{i} [1 ms]\n' for i in range(60000))
        rec, adapter, *_ = replay('dotnet', chunks_from_text(lines, size=8192), argv=['dotnet', 'test'], collect=NOCOLLECT)
        self.assertLessEqual(len(adapter.state), 50000)

    def test_a_long_message_is_cut_not_kept(self):
        rec = self.feed('  Failed Demo.A.B [1 ms]\n  Error Message:\n' + ''.join(f'   line {i}\n' for i in range(100000)), size=4096)
        self.assertEqual(rec.last()['Demo.A::Demo.A.B'][1], 'line 0 line 1 line 2')

    def test_a_message_without_a_stack_trace_still_ends_at_the_next_result(self):
        rec = self.feed('  Failed Demo.A.B [1 ms]\n  Error Message:\n   boom\n  Failed Demo.A.C [1 ms]\n  Error Message:\n   bang\n')
        self.assertEqual(rec.last()['Demo.A::Demo.A.B'], ('failed', 'boom'))
        self.assertEqual(rec.last()['Demo.A::Demo.A.C'], ('failed', 'bang'))


class ThroughTheWriter(unittest.TestCase):
    def test_only_the_run_directory_is_written(self):
        meta, chunks = load('dotnet-vstest-all-detailed-en.jsonl')
        events, tree = ar.replay_to_files('dotnet', chunks, argv=meta['command'], collect=NOCOLLECT)
        self.assertTrue(events)
        self.assertTrue(all(name.startswith('.skillsync/runs/R1/tests/') for name in tree), tree)

    def test_the_adapter_leaves_the_reports_to_the_recorder(self):
        rec, adapter, ctx, skipped = replay_fixture('dotnet', 'dotnet-vstest-all-detailed-en.jsonl', collect=NOCOLLECT, reports=[ar.FIXTURES / 'dotnet-vstest-all.trx'])
        self.assertFalse(skipped)   # `end` returns False: the TRX is still parsed and overwrites the entries


if __name__ == '__main__':
    unittest.main()
