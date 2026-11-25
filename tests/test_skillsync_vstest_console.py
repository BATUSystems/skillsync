"""CORE-1170-VSTEST: `skillsync test-run` recognises `vstest.console.exe` (K1 detection, K2 adapter choice, K3 replay of the console, K4 argument reader and TRX places,
K5 a process run with a stand-in program, K6 the summary block without a report).

The consoles and reports are the synthetic fixtures `tests/fixtures/test-runs/vstest-console-*` (`.txt` are stored with LF: CRLF is made by the test)."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay, replay_fixture  # noqa: E402
from tmp_paths import child  # noqa: E402
from test_skillsync_test_run import Base  # noqa: E402

from skillsync_workflow import quality, test_adapters, test_reports, test_run  # noqa: E402
from skillsync_workflow.test_adapters.dotnet import expand_cmd, program_name, vstest_command, vstest_info, vstest_options  # noqa: E402

FIXTURES = ar.FIXTURES
NOCOLLECT = lambda argv, timeout=30: None   # noqa: E731
CASES = ('green-en', 'green-pl', 'red-en', 'red-pl')
RESULT_WORDS = ('Passed', 'Failed', 'Skipped', 'Powodzenie', 'Niepowodzenie', 'Pominięto')
VSTEST_ARGV = ['vstest.console.exe', 'Demo.Tests.dll', '/Logger:trx;LogFileName=demo.trx', '/ResultsDirectory:C:\\out']


def console(case):
    return (FIXTURES / f'vstest-console-{case}.txt').read_text(encoding='utf-8')


def result_lines(text):
    """The lines of test results of a console: two spaces, a result word, the name, `[time]`."""
    return [line for line in text.splitlines() if line.startswith('  ') and line[2:].split(' ', 1)[0] in RESULT_WORDS and line.rstrip().endswith(']')]


def feed_text(text, argv=VSTEST_ARGV):
    return replay('dotnet', [{'t': 0, 'fd': 'out', 'data': text}], argv=argv, tool='vstest', collect=NOCOLLECT)[0]


class Detection(unittest.TestCase):
    """K1: `detect_tool` and the narrow `cmd /c "..."` reading (Q4)."""

    def test_the_program_is_found_in_every_spelling(self):
        for command in (['vstest.console.exe', 'a.dll'], ['vstest.console', 'a.dll'], ['VSTEST.CONSOLE.EXE', 'a.dll'],
                        ['C:\\Program Files\\Microsoft Visual Studio\\2022\\Professional\\Common7\\IDE\\Extensions\\TestPlatform\\vstest.console.exe', 'a.dll'],
                        ['C:/Program Files/VS/TestPlatform/vstest.console.exe', 'a.dll'], ['/opt/tp/vstest.console', 'a.dll'],
                        '"C:\\Program Files\\VS\\vstest.console.exe" a.dll /Logger:trx', '"C:/Program Files/VS/vstest.console.exe" a.dll', 'vstest.console.exe a.dll',
                        ['"C:\\Program Files\\VS\\vstest.console.exe"', 'a.dll']):
            with self.subTest(command=command):
                self.assertEqual(test_run.detect_tool(command), 'vstest')

    def test_cmd_as_separate_words_and_as_one_quoted_word(self):
        for command in (['cmd', '/c', 'vstest.console.exe', 'a.dll'], ['cmd.exe', '/C', 'vstest.console.exe', 'a.dll'], ['cmd', '/k', 'vstest.console.exe', 'a.dll'],
                        ['cmd', '/c', 'vstest.console.exe a.dll /Logger:trx;LogFileName=x.trx'], ['cmd', '/c', '"vstest.console.exe" a.dll'],
                        ['cmd', '/c', '"C:\\Program Files\\VS\\vstest.console.exe" a.dll /ResultsDirectory:C:\\o'], ['CMD', '/C', 'vstest.console.exe a.dll'],
                        ['cmd', '/c', 'C:\\Program Files\\VS\\vstest.console.exe'], ['cmd', '/c', '"C:\\Program Files\\VS\\vstest.console.exe"']):
            with self.subTest(command=command):
                self.assertEqual(test_run.detect_tool(command), 'vstest')

    def test_other_names_are_unknown(self):
        for command in (['vstest.consolex', 'a.dll'], ['vstest.consolex.exe', 'a.dll'], ['my-vstest.console', 'a.dll'], ['my-vstest.console.exe', 'a.dll'], ['vstest', 'a.dll'],
                        ['vstest.exe', 'a.dll'], ['cmd', '/c', 'my-vstest.console.exe a.dll'], ['cmd', '/c', 'echo vstest.consolex'], ['echo', 'hello']):
            with self.subTest(command=command):
                self.assertEqual(test_run.detect_tool(command), 'unknown')

    def test_dotnet_is_unchanged_and_cmd_around_dotnet_is_a_known_consequence_of_q4(self):
        self.assertEqual(test_run.detect_tool(['dotnet', 'test']), 'dotnet')
        self.assertEqual(test_run.detect_tool(['dotnet', 'vstest', 'a.dll']), 'dotnet')
        self.assertEqual(test_run.detect_tool(['cmd', '/c', 'dotnet', 'test']), 'dotnet')   # already before this change (the tool of the first three words)
        self.assertEqual(test_run.detect_tool(['cmd', '/c', 'dotnet test --no-build']), 'dotnet')   # Q4: the one quoted word is split, so `dotnet` is found as well (a documented consequence)

    def test_the_narrow_split_leaves_everything_else_alone(self):
        self.assertEqual(expand_cmd(['cmd', '/c', 'vstest.console.exe a.dll']), ['cmd', '/c', 'vstest.console.exe', 'a.dll'])
        self.assertEqual(expand_cmd(['cmd', '/c', '"a b" c']), ['cmd', '/c', 'a b', 'c'])
        self.assertEqual(expand_cmd(['cmd', '/c', 'C:\\x y\\vstest.console.exe']), ['cmd', '/c', 'C:\\x y\\vstest.console.exe'])   # the third word IS the program (a path with a space)
        for words in (['cmd', '/c', 'one'], ['cmd', '/x', 'a b'], ['powershell', '-Command', 'a b'], ['x', '/c', 'a b'], ['cmd', '/c'], [], ['cmd', '/c', 'a "open']):
            with self.subTest(words=words):
                self.assertEqual(expand_cmd(words), words)   # an open quote leaves the words as they were

    def test_the_program_name_reader(self):
        for word, name in (('C:\\a b\\VSTEST.CONSOLE.EXE', 'vstest.console'), ('"vstest.console.exe"', 'vstest.console'), ('/x/vstest.console', 'vstest.console'), ('a.cmd', 'a'), ('b.bat', 'b')):
            self.assertEqual(program_name(word), name)


class Acceptance(unittest.TestCase):
    """K2: the adapter `dotnet` takes a run of `vstest.console`, not a command that only informs."""
    CLS = test_adapters.REGISTRY['dotnet']

    def test_a_run_is_accepted_in_every_form(self):
        for argv in (['vstest.console.exe', 'a.dll'], ['vstest.console', 'a.dll', '/Tests:A,B'], ['C:\\VS\\vstest.console.exe', 'a.dll', '/Logger:trx'], ['/opt/vstest.console', 'a.dll'],
                     ['cmd', '/c', 'vstest.console.exe', 'a.dll'], ['cmd', '/c', 'vstest.console.exe a.dll /Logger:trx'], ['vstest.console.exe', 'a.dll', '/Enablecodecoverage'],
                     ['vstest.console.exe', 'a.dll', '/Logger', 'trx;LogFileName=l.trx']):
            with self.subTest(argv=argv):
                self.assertTrue(self.CLS().accepts(argv))

    def test_a_command_that_only_informs_is_refused(self):
        for flag in ('/ListTests', '/ListTests:a.dll', '/lt', '/lfqt', '/ListFullyQualifiedTests', '/ListDiscoverers', '/ListExecutors', '/ListLoggers', '/ListSettingsProviders', '/Help', '/?', '-?',
                     '/listtests', '--ListTests', '-lt', '/LT', '/HELP'):
            with self.subTest(flag=flag):
                self.assertFalse(self.CLS().accepts(['vstest.console.exe', 'a.dll', flag]))
                self.assertFalse(self.CLS().accepts(['cmd', '/c', f'vstest.console.exe a.dll {flag}']))

    def test_other_programs_and_the_dotnet_rules_are_unchanged(self):
        cls = self.CLS
        for argv, expected in ((['vstest.consolex', 'a.dll'], False), (['my-vstest.console', 'a.dll'], False), (['vstest', 'a.dll'], False), (['dotnet', 'test'], True),
                               (['dotnet', 'test', '--list-tests'], False), (['dotnet', 'vstest', 'a.dll'], False), ([], False)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_auto_chooses_the_adapter_by_the_tool_only_for_a_run(self):
        self.assertEqual(test_adapters.resolve('auto', ['vstest.console.exe', 'a.dll'], 'vstest').name, 'dotnet')
        self.assertIsNone(test_adapters.resolve('auto', ['vstest.console.exe', 'a.dll', '/lt'], 'vstest'))
        self.assertIsNone(test_adapters.resolve('auto', ['vstest.console.exe', 'a.dll', '/Help'], 'vstest'))

    def test_the_information_flags_of_the_shared_reader(self):
        self.assertTrue(vstest_info(vstest_options(['a.dll', '/lt'])))
        self.assertTrue(vstest_info(vstest_options(['-?'])))
        self.assertFalse(vstest_info(vstest_options(['a.dll', '/Tests:lt'])))   # `lt` is the VALUE of /Tests
        self.assertFalse(vstest_info(vstest_options(['a.dll', '/Tests', 'lt'])))
        self.assertFalse(vstest_info(vstest_options(['a.dll', '--', '/lt'])))   # behind a lone `--` come run settings


class Replay(unittest.TestCase):
    """K3: the adapter reads the console of `vstest.console`; the text (LF and CRLF) gives the events of the recorded stream."""

    def test_the_console_text_gives_the_events_of_the_recording(self):
        for case in CASES:
            with self.subTest(case=case):
                from_fixture = replay_fixture('dotnet', f'vstest-console-{case}.jsonl', collect=NOCOLLECT)[0]
                for label, text in (('lf', console(case)), ('crlf', console(case).replace('\n', '\r\n'))):
                    rec = feed_text(text)
                    self.assertEqual(rec.emitted(), from_fixture.emitted(), label)
                    self.assertEqual(rec.last(), from_fixture.last(), label)

    def test_every_result_line_is_one_test_and_the_ids_are_those_of_the_trx(self):
        for case in CASES:
            color = case.split('-')[0]
            with self.subTest(case=case):
                rec = feed_text(console(case))
                lines = result_lines(console(case))
                self.assertGreater(len(lines), 0)
                ids = {c[1] for c in rec.emitted()}
                self.assertEqual(len(ids), len(lines))   # one test per result line (a red test is reported by the [FAIL] line and then by its result: the same id)
                trx = {r['id']: r['state'] for r in test_reports.parse_trx(FIXTURES / f'vstest-console-{color}.trx')}
                self.assertEqual(ids, set(trx))
                self.assertEqual({k: v[0] for k, v in rec.last().items()}, trx)

    def test_the_states_are_those_of_the_language_independent_words(self):
        for case, red in (('green-en', 0), ('green-pl', 0), ('red-en', 1), ('red-pl', 1)):
            with self.subTest(case=case):
                states = [v[0] for v in feed_text(console(case)).last().values()]
                self.assertEqual(states.count('failed'), red)
                self.assertEqual(states.count('skipped'), red)
                self.assertNotIn('running', states)

    def test_a_word_the_adapter_does_not_know_is_ignored_not_guessed(self):
        text = '  Bestanden Demo.Tests.Math.AddTests.Adds_two_numbers [58 ms]\r\n  Passed Demo.Tests.Math.AddTests.Other_one [1 ms]\r\n'
        got = feed_text(text).last()
        self.assertEqual(got, {'Demo.Tests.Math.AddTests::Demo.Tests.Math.AddTests.Other_one': ('passed', None)})

    def test_a_skipped_result_does_not_take_the_next_line_for_its_reason(self):
        # the summary pointer (`Results File:` / `Plik wynikowy:`) follows the last result directly; it is no message of a test
        for case in ('red-en', 'red-pl'):
            with self.subTest(case=case):
                last = feed_text(console(case)).last()
                reason = last['Demo.Tests.Math.DivideTests::Demo.Tests.Math.DivideTests.Rounding_rule_is_pending'][1]
                self.assertNotIn('C:\\out\\demo.trx', reason or '')

    def test_a_dotnet_message_keeps_its_unindented_lines_and_ends_at_the_end_of_the_run(self):
        # the real `dotnet test` prints the further lines of a message in column 0 (`Expected:` / `Actual:`): they stay in the message (the first three lines, as before)
        text = ('  Failed Demo.RedTests.Boom [7 ms]\n  Error Message:\n   Assert.Equal() Failure: Values differ\nExpected: 3\nActual:   4\nFourth line\n  Stack Trace:\n     at X\n')
        rec = replay('dotnet', [{'t': 0, 'fd': 'out', 'data': text}], argv=['dotnet', 'test'], tool='dotnet', collect=NOCOLLECT)[0]
        self.assertEqual(rec.last()['Demo.RedTests::Demo.RedTests.Boom'], ('failed', 'Assert.Equal() Failure: Values differ Expected: 3 Actual:   4'))
        # a skipped test followed by the end lines of the run: none of them is its reason (both languages, dotnet and vstest)
        for end in ('Results File: C:\\out\\demo.trx\n', 'Plik wynikowy: C:\\out\\demo.trx\n', 'Total tests: 1\n', 'Łączna liczba testów: 1\n', 'Test Run Successful.\n', 'Przebieg testu zakończony pomyślnie.\n'):
            for tool, argv in (('dotnet', ['dotnet', 'test']), ('vstest', VSTEST_ARGV)):
                with self.subTest(end=end, tool=tool):
                    skipped = '  Skipped Demo.RedTests.Later [< 1 ms]\n  Error Message:\n   Not yet\n' + end
                    rec = replay('dotnet', [{'t': 0, 'fd': 'out', 'data': skipped}], argv=argv, tool=tool, collect=NOCOLLECT)[0]
                    self.assertEqual(rec.last()['Demo.RedTests::Demo.RedTests.Later'], ('skipped', 'Not yet'))

    def test_the_failed_test_keeps_its_message(self):
        last = feed_text(console('red-en')).last()
        self.assertIn('DivideByZeroException', last['Demo.Tests.Math.DivideTests::Demo.Tests.Math.DivideTests.Division_by_zero_is_refused'][1])


class Options(unittest.TestCase):
    """K4: the one reader of the program and its arguments."""

    def test_case_and_prefixes(self):
        for word in ('/ResultsDirectory:D', '/resultsdirectory:D', '-ResultsDirectory:D', '--RESULTSDIRECTORY:D', '--resultsdirectory=D', '/ResultsDirectory=D'):
            with self.subTest(word=word):
                self.assertEqual(vstest_options([word]), [('resultsdirectory', 'D')])

    def test_value_forms_of_the_logger(self):
        self.assertEqual(vstest_options(['/Logger', 'trx;LogFileName=x.trx']), [('logger', 'trx;LogFileName=x.trx')])
        self.assertEqual(vstest_options(['/Logger:trx;LogFileName=x.trx']), [('logger', 'trx;LogFileName=x.trx')])
        self.assertEqual(vstest_options(['/Logger:"trx;LogFileName=C:\\a b\\x.trx"']), [('logger', '"trx;LogFileName=C:\\a b\\x.trx"')])
        self.assertEqual(vstest_options(['/Logger:console;verbosity=detailed', '/Logger:trx']), [('logger', 'console;verbosity=detailed'), ('logger', 'trx')])
        self.assertEqual(vstest_options(['/Logger']), [('logger', None)])

    def test_the_value_in_the_next_word_only_for_an_option_that_takes_one(self):
        self.assertEqual(vstest_options(['/ResultsDirectory', 'C:\\out', 'a.dll']), [('resultsdirectory', 'C:\\out'), ('', 'a.dll')])
        self.assertEqual(vstest_options(['/ResultsDirectory', '/tmp/out']), [('resultsdirectory', '/tmp/out')])   # a path is a value, not an option
        self.assertEqual(vstest_options(['/Logger', '/Parallel']), [('logger', None), ('parallel', None)])   # the next word is an option: no value
        self.assertEqual(vstest_options(['/Parallel', 'a.dll']), [('parallel', 'a.dll')])
        self.assertEqual(vstest_options(['/Enablecodecoverage', 'a.dll']), [('enablecodecoverage', None), ('', 'a.dll')])   # not a valued option: the next word is a source

    def test_sources_and_the_stop_at_a_lone_double_dash(self):
        self.assertEqual(vstest_options(['a.dll', 'b.dll']), [('', 'a.dll'), ('', 'b.dll')])
        self.assertEqual(vstest_options(['a.dll', '--', 'MSTest.Parallelize=true', '/lt']), [('', 'a.dll')])
        self.assertEqual(vstest_options([]), [])

    def test_the_command_reader(self):
        self.assertEqual(vstest_command(['vstest.console.exe', 'a.dll', '/lt']), ('vstest.console.exe', [('', 'a.dll'), ('lt', None)]))
        self.assertEqual(vstest_command(['timeout', '60', 'C:\\VS\\vstest.console.exe', 'a.dll'])[1], [('', 'a.dll')])   # the program may stand behind a wrapper
        self.assertEqual(vstest_command(['cmd', '/c', 'vstest.console.exe a.dll /ResultsDirectory:C:\\o']), ('vstest.console.exe', [('', 'a.dll'), ('resultsdirectory', 'C:\\o')]))
        self.assertIsNone(vstest_command(['dotnet', 'test']))
        self.assertIsNone(vstest_command(['vstest.consolex', 'a.dll']))
        self.assertIsNone(vstest_command([]))


class Reports(unittest.TestCase):
    """K4: `find_reports` for `vstest` (the places named by the command, else `TestResults`, only files written since the start; `--junit` first)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='vstest-reports-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cwd = child(self.tmp, 'proj')
        self.cwd.mkdir()
        self.outside = child(self.tmp, 'outside')
        self.outside.mkdir()
        self.started = time.time()

    def trx(self, directory, name, age=0):
        directory.mkdir(parents=True, exist_ok=True)
        path = child(directory, name)
        shutil.copyfile(FIXTURES / 'vstest-console-green.trx', path)
        if age:
            stamp = time.time() - age
            os.utime(path, (stamp, stamp))
        return path

    def find(self, command, explicit=(), since=None):
        return test_run.find_reports('vstest', self.cwd, list(explicit), self.started - 1 if since is None else since, command)

    def test_the_results_directory_of_the_command_also_outside_the_project(self):
        made = self.trx(self.outside, 'r.trx')
        for command in ([f'vstest.console.exe', 'a.dll', f'/ResultsDirectory:{self.outside}'], ['vstest.console.exe', 'a.dll', '/resultsdirectory', str(self.outside)],
                        ['vstest.console.exe', 'a.dll', f'--ResultsDirectory={self.outside}'], ['cmd', '/c', f'vstest.console.exe a.dll /ResultsDirectory:{self.outside}']):
            with self.subTest(command=command):
                self.assertEqual(self.find(command), [made])

    def test_quotes_of_the_results_directory_are_stripped_like_those_of_the_log_file_name(self):
        made = self.trx(self.outside, 'r.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/ResultsDirectory:"{self.outside}"']), [made])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/ResultsDirectory', f"'{self.outside}'"]), [made])

    def test_a_relative_results_directory_is_relative_to_the_cwd(self):
        made = self.trx(child(self.cwd, 'out'), 'r.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/ResultsDirectory:out']), [made])

    def test_an_absolute_log_file_name_is_found_where_it_is(self):
        made = self.trx(self.outside, 'named.trx')
        self.trx(self.outside, 'other.trx')   # a sibling the command does not name
        for logger in (f'/Logger:trx;LogFileName={made}', f'/Logger:"trx;LogFileName={made}"', f'/logger:TRX;logfilename={made}'):
            with self.subTest(logger=logger):
                self.assertEqual(self.find(['vstest.console.exe', 'a.dll', logger]), [made])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/Logger', f'trx;LogFileName={made}']), [made])

    def test_a_relative_log_file_name_goes_to_the_results_directory_else_to_test_results(self):
        in_dir = self.trx(self.outside, 'rel.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/Logger:trx;LogFileName=rel.trx', f'/ResultsDirectory:{self.outside}']), [in_dir])
        default = self.trx(child(self.cwd, 'TestResults'), 'rel2.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/Logger:trx;LogFileName=rel2.trx']), [default])

    def test_several_loggers_the_non_trx_ones_name_no_report(self):
        one, two = self.trx(self.outside, 'one.trx'), self.trx(self.outside, 'two.trx')
        found = self.find(['vstest.console.exe', 'a.dll', '/Logger:console;verbosity=normal', f'/Logger:trx;LogFileName={one}', f'/Logger:trx;LogFileName={two}'])
        self.assertEqual(sorted(found), sorted([one, two]))
        html = self.trx(self.outside, 'page.html.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/Logger:html;LogFileName={html}']), [])

    def test_the_default_is_test_results_in_the_cwd(self):
        made = self.trx(child(self.cwd, 'TestResults'), 'd.trx')
        self.trx(self.outside, 'far.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll']), [made])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', '/Logger:trx']), [made])   # a trx logger without a file name: the default place
        self.assertEqual(self.find(None), [made])   # no command at all: the default place as for `dotnet`

    def test_a_file_older_than_the_start_is_refused_with_every_kind_of_place(self):
        old_default = self.trx(child(self.cwd, 'TestResults'), 'old.trx', age=3600)
        old_named = self.trx(self.outside, 'oldnamed.trx', age=3600)
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll']), [])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/ResultsDirectory:{self.outside}']), [])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/Logger:trx;LogFileName={old_named}']), [])
        fresh = self.trx(child(self.cwd, 'TestResults'), 'new.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll']), [fresh])
        self.assertTrue(old_default.exists())

    def test_a_file_outside_the_named_places_is_not_taken(self):
        self.trx(self.outside, 'far.trx')
        self.trx(child(self.cwd, 'elsewhere'), 'x.trx')
        named = child(self.tmp, 'named')
        named.mkdir()
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/ResultsDirectory:{named}']), [])   # the directory the command names is empty: the default place is NOT searched too
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll']), [])   # `**/TestResults/*.trx` only
        self.trx(child(self.cwd, 'TestResults'), 'outside_named.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/ResultsDirectory:{named}']), [])

    def test_junit_has_priority(self):
        theirs = self.trx(child(self.tmp, 'given'), 'given.trx')
        self.trx(self.outside, 'r.trx')
        self.trx(child(self.cwd, 'TestResults'), 'd.trx')
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll', f'/ResultsDirectory:{self.outside}'], explicit=[str(theirs)]), [theirs])
        self.assertEqual(self.find(['vstest.console.exe', 'a.dll'], explicit=[str(theirs.parent)]), [theirs])

    def test_dotnet_keeps_its_default_places_and_ignores_the_command(self):
        made = self.trx(child(self.cwd, 'TestResults'), 'd.trx')
        self.trx(self.outside, 'r.trx')
        self.assertEqual(test_run.find_reports('dotnet', self.cwd, [], self.started - 1, ['dotnet', 'test', f'/ResultsDirectory:{self.outside}']), [made])
        self.assertEqual(test_run.find_reports('dotnet', self.cwd, [], self.started - 1), [made])


class SummaryBlock(unittest.TestCase):
    """K6: the summary of `vstest.console` read from the text only for the tool `vstest`."""

    def test_the_fixtures_in_both_languages_and_with_crlf(self):
        for case, want in (('green-en', (5, 0)), ('green-pl', (5, 0)), ('red-en', (3, 1)), ('red-pl', (3, 1))):
            for label, text in (('lf', console(case)), ('crlf', console(case).replace('\n', '\r\n'))):
                with self.subTest(case=case, eol=label):
                    self.assertEqual(quality._test_counts(text, 'vstest'), want)

    def test_skipped_in_both_spellings_and_a_missing_line_is_zero(self):
        base = 'Total tests: 6\n     Passed: 4\n'
        self.assertEqual(quality._test_counts(base + '    Skipped: 2\n', 'vstest'), (4, 2))
        self.assertEqual(quality._test_counts(base, 'vstest'), (4, 0))
        pl = 'Łączna liczba testów: 6\n     Zakończone pomyślnie: 4\n'
        self.assertEqual(quality._test_counts(pl + '    Pominięte: 2\n', 'vstest'), (4, 2))
        self.assertEqual(quality._test_counts(pl + '    Pominięto: 2\n', 'vstest'), (4, 2))
        self.assertEqual(quality._test_counts(pl + '  Pominięto: 2\n Łączny czas: 1,5 Sekundy\n', 'vstest'), (4, 2))   # any indent, the time line does not end the block
        self.assertEqual(quality._test_counts(pl + '\t\tPominięte: 2', 'vstest'), (4, 2))

    def test_a_total_without_a_passed_line_is_no_false_pass(self):
        for text in ('Total tests: 5\n Total time: 1 Seconds\n', 'Łączna liczba testów: 5\n Łączny czas: 1,0 Sekundy\n', 'Total tests: 5\n    Failed: 5\n'):
            with self.subTest(text=text):
                ran, skipped = quality._test_counts(text, 'vstest')
                self.assertEqual(ran, 0)
                self.assertEqual(quality.evidence_reason('test', ran, skipped), 'Zero executed tests')

    def test_a_block_without_the_total_line_is_not_a_summary(self):
        for text in ('     Passed: 3\n     Failed: 1\n', 'Test Run Successful.\n     Passed: 5\n', 'Passed: 5', '', 'Łączny czas: 1,0 Sekundy\n     Zakończone pomyślnie: 3\n'):
            with self.subTest(text=text):
                self.assertEqual(quality._test_counts(text, 'vstest'), (None, None))

    def test_the_label_must_start_the_line_and_the_lines_must_be_indented_results(self):
        for text in ('log: Total tests: 5\n     Passed: 5\n', 'Total tests: five\n     Passed: 5\n', 'Total tests: 5 of 9\n     Passed: 5\n'):
            with self.subTest(text=text):
                self.assertEqual(quality._test_counts(text, 'vstest'), (None, None))

    def test_the_last_block_of_the_output_wins(self):
        text = console('red-en') + '\n' + console('green-en')
        self.assertEqual(quality._test_counts(text, 'vstest'), (5, 0))

    def test_damaged_letters_never_give_a_wrong_pass(self):
        # a console in an OEM code page read as UTF-8 with `replace`: the Polish letters become U+FFFD
        for case, want in (('green-pl', (5, 0)), ('red-pl', (3, 1))):
            damaged = console(case).encode('cp852').decode('utf-8', errors='replace')
            self.assertIn('\ufffd', damaged)
            with self.subTest(case=case):
                got = quality._test_counts(damaged, 'vstest')
                self.assertIn(got, (want, (None, None)))   # the right numbers or none: never a different count

    def test_the_other_tools_read_what_they_read_before(self):
        for case in CASES:
            for tool in (None, 'dotnet', 'pytest', 'unittest'):
                with self.subTest(case=case, tool=tool):
                    self.assertEqual(quality._test_counts(console(case), tool), (None, None))   # the block is read for `vstest` only
        self.assertEqual(quality._test_counts('Passed! - Failed: 0, Passed: 910, Skipped: 2, Total: 912', None), (910, 2))
        self.assertEqual(quality._test_counts('Passed! - Failed: 0, Passed: 910, Skipped: 2, Total: 912', 'dotnet'), (910, 2))
        self.assertEqual(quality._test_counts('Ran 4 tests in 0.1s\n\nOK', 'vstest'), quality._test_counts('Ran 4 tests in 0.1s\n\nOK'))

    def test_recorded_dotnet_runs_read_as_before(self):
        # (tool None) = (tool `dotnet`) on every recorded `dotnet test` stream; only `green-default-en` has the one-line summary the old reader knows
        expected = {'dotnet-vstest-green-default-en.jsonl': (3, 0)}
        for path in sorted(FIXTURES.glob('dotnet-*.jsonl')):
            text = ''.join(c['data'] for c in ar.load(path.name)[1] if c['fd'] == 'out')
            with self.subTest(fixture=path.name):
                want = expected.get(path.name, (None, None))
                self.assertEqual(quality._test_counts(text), want)
                self.assertEqual(quality._test_counts(text, None), want)
                self.assertEqual(quality._test_counts(text, 'dotnet'), want)

    def test_the_failure_count(self):
        for case in ('red-en', 'red-pl'):
            with self.subTest(case=case):
                self.assertEqual(test_run._failure_count(console(case), 'vstest'), 1)
                self.assertEqual(test_run._failure_count(console(case).replace('\n', '\r\n'), 'vstest'), 1)
                self.assertEqual(test_run._failure_count(console(case)), 0)   # without the tool the block is not read
                self.assertEqual(test_run._failure_count(console(case), 'dotnet'), 0)
        for case in ('green-en', 'green-pl'):
            self.assertEqual(test_run._failure_count(console(case), 'vstest'), 0)
        self.assertEqual(test_run._failure_count('Total tests: 4\n    Passed: 1\n    Failed: 3\n', 'vstest'), 3)
        self.assertEqual(test_run._failure_count('Failed: 3\n', 'vstest'), 0)   # no block: nothing is read
        self.assertEqual(test_run._failure_count('FAILED (failures=2, errors=1)', 'vstest'), 3)   # the other readers still work for the tool


@unittest.skipIf(os.name == 'nt', 'the stand-in program is a script with a shebang (the Windows variant is a `.cmd`: not run here)')
class ProcessRun(Base):
    """K5: `skillsync test-run -- vstest.console.exe ...` with a stand-in program that prints a console of the fixtures and writes the TRX during the run."""

    def stand_in(self, case, name='vstest.console.exe', crlf=False, writes_trx=True, code=None):
        """A program named `name` first on PATH: prints the console of `case`, writes the TRX of its colour into the `/ResultsDirectory` (else `TestResults` of the cwd)."""
        color = case.split('-')[0]
        code = (0 if color == 'green' else 1) if code is None else code
        bin_dir = child(Path(self.tmp), 'bin')
        bin_dir.mkdir(exist_ok=True)
        console_file, report_file = str(FIXTURES / f'vstest-console-{case}.txt'), str(FIXTURES / f'vstest-console-{color}.trx')
        lines = [f'#!{sys.executable}', 'import os, shutil, sys',
                 f'text = open({console_file!r}, encoding="utf-8", newline="").read()']
        if crlf:
            lines.append('text = text.replace(chr(10), chr(13) + chr(10))')
        lines += ['directory = None',
                  'for a in sys.argv[1:]:',
                  '    if a.lower().startswith("/resultsdirectory:"):',
                  '        directory = a.split(":", 1)[1]',
                  'directory = directory or os.path.join(os.getcwd(), "TestResults")',
                  'os.makedirs(directory, exist_ok=True)']
        if writes_trx:
            lines.append(f'shutil.copyfile({report_file!r}, os.path.join(directory, "d.trx"))')
        lines += ['sys.stdout.buffer.write(text.encode("utf-8"))', 'sys.stdout.flush()', f'sys.exit({code})']
        script = '\n'.join(lines) + '\n'
        exe = child(bin_dir, name)
        exe.write_text(script, encoding='utf-8')
        exe.chmod(0o755)
        self.env = {**self.env, 'PATH': f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}"}

    def run_vstest(self, *args, program='vstest.console.exe'):
        return self.run_cli('--step', 'F1.1', '--', program, 'Demo.Tests.dll', *args)

    def test_a_green_run_is_a_pass_without_a_flag_and_the_counts_come_from_the_trx(self):
        results = child(Path(self.tmp), 'results')
        for case in ('green-en', 'green-pl'):
            with self.subTest(case=case):
                self.stand_in(case, crlf=(case == 'green-pl'))
                n = (self.root / '.skillsync/runs/R1/tests/F1.1').exists() and len(list((self.root / '.skillsync/runs/R1/tests/F1.1').glob('*.jsonl'))) + 1 or 1
                done = self.run_vstest('/Logger:trx;LogFileName=d.trx', f'/ResultsDirectory:{results}')
                self.assertEqual(done.returncode, 0, done.stderr)
                state = self.attempt(n=n)
                self.assertEqual(state.end['status'], 'PASS', state.end)
                self.assertEqual(state.end['source'], 'junit')
                self.assertEqual((state.counts['total'], state.counts['passed'], state.counts['failed'], state.counts['skipped']), (5, 5, 0, 0))
                self.assertEqual(state.start['tool'], 'vstest')
                self.assertEqual(state.start['adapter'], 'dotnet')
                self.assertEqual({t['state'] for t in state.tests.values()}, {'passed'})
                self.assertEqual(len(state.tests), 5)

    def test_the_default_test_results_directory_is_read_when_the_command_names_nothing(self):
        self.stand_in('green-en')
        done = self.run_vstest()
        self.assertEqual(done.returncode, 0, done.stderr)
        state = self.attempt()
        self.assertEqual((state.end['status'], state.end['source'], state.counts['total']), ('PASS', 'junit', 5))

    def test_a_program_without_the_exe_and_in_a_cmd_shell_word_is_the_same_tool(self):
        self.stand_in('green-en', name='vstest.console')
        done = self.run_vstest(program='vstest.console')
        self.assertEqual(done.returncode, 0, done.stderr)
        state = self.attempt()
        self.assertEqual((state.end['status'], state.start['tool'], state.counts['total']), ('PASS', 'vstest', 5))

    def test_a_red_run_is_a_fail_with_exit_one(self):
        results = child(Path(self.tmp), 'results')
        for crlf, n in ((False, 1), (True, 2)):
            with self.subTest(crlf=crlf):
                self.stand_in('red-pl' if crlf else 'red-en', crlf=crlf)
                done = self.run_vstest(f'/ResultsDirectory:{results}')
                self.assertEqual(done.returncode, 1, done.stderr)
                state = self.attempt(n=n)
                self.assertEqual(state.end['status'], 'FAIL', state.end)
                self.assertEqual(state.end['source'], 'junit')
                self.assertEqual((state.counts['total'], state.counts['passed'], state.counts['failed'], state.counts['skipped']), (5, 3, 1, 1))
                self.assertEqual({t['state'] for t in state.tests.values()}, {'passed', 'failed', 'skipped'})

    def test_a_red_run_without_a_trx_has_the_counters_of_the_block(self):
        # `Passed:` of the console block does not include the failures: 5 total, 3 passed, 1 failed, 1 skipped (not 4 / 2 / 1 / 1)
        for case in ('red-en', 'red-pl'):
            with self.subTest(case=case):
                self.stand_in(case, writes_trx=False)
                n = len(list((self.root / '.skillsync/runs/R1/tests/F1.1').glob('*.jsonl'))) + 1 if (self.root / '.skillsync/runs/R1/tests/F1.1').exists() else 1
                done = self.run_vstest()
                self.assertEqual(done.returncode, 1, done.stderr)
                state = self.attempt(n=n)
                self.assertEqual(state.end['status'], 'FAIL', state.end)
                self.assertEqual((state.counts['total'], state.counts['passed'], state.counts['failed'], state.counts['skipped']), (5, 3, 1, 1))

    def test_a_clean_exit_with_a_failed_block_is_no_pass(self):
        for body, want in (('Total tests: 5\n     Passed: 3\n     Failed: 1\n    Skipped: 1\n', (5, 3, 1, 1)), ('Total tests: 5\n     Failed: 5\n', (5, 0, 5, 0))):
            with self.subTest(body=body):
                self.stand_in('green-en', writes_trx=False)
                exe = child(child(Path(self.tmp), 'bin'), 'vstest.console.exe')
                exe.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({body!r})\n", encoding='utf-8')
                done = self.run_vstest()
                state = self.attempt(n=1 if want[2] == 1 else 2)
                self.assertNotEqual(state.end['status'], 'PASS', state.end)
                self.assertIn(f'{want[2]} failed tests', state.end.get('reason') or '')
                self.assertEqual((state.counts['total'], state.counts['passed'], state.counts['failed'], state.counts['skipped']), want)
                self.assertNotEqual(done.returncode, 0)

    def test_a_report_older_than_the_start_is_no_evidence(self):
        stale = child(child(self.root, 'TestResults'), 'old.trx')
        stale.parent.mkdir()
        shutil.copyfile(FIXTURES / 'vstest-console-green.trx', stale)
        old = time.time() - 86400
        os.utime(stale, (old, old))
        self.stand_in('green-en', writes_trx=False)
        done = self.run_vstest()
        state = self.attempt()
        self.assertNotEqual(state.end['source'], 'junit', state.end)   # the stale report is not read: the numbers come from elsewhere or not at all
        self.assertTrue(stale.exists())
        self.assertEqual(done.returncode, 0 if state.end['status'] == 'PASS' else 2, done.stderr)

    def test_no_report_and_no_summary_is_not_a_pass(self):
        self.stand_in('green-en', writes_trx=False)
        bin_dir = child(Path(self.tmp), 'bin')
        exe = child(bin_dir, 'vstest.console.exe')
        exe.write_text(f"#!{sys.executable}\nprint('Starting test execution, please wait...')\n", encoding='utf-8')
        done = self.run_vstest()
        state = self.attempt()
        self.assertNotEqual(state.end['status'], 'PASS', state.end)
        self.assertEqual(done.returncode, 2, done.stderr)


if __name__ == '__main__':
    unittest.main()
