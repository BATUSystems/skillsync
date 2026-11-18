"""CORE-TAP F2.1: the adapter `node-test` (`node --test`: the stream decides between TAP and the `spec` reporter; sections 4.5, 4A, 6 and K5, K12-K15 of the spec
`docs/specs/2026-11-18-core-tap.md`).

Red-first: `skillsync_workflow/test_adapters/node_test.py` does not exist yet, so these tests fail on the missing adapter (`unknown --format 'node-test'` / `KeyError`).
The expectations come from the contract (the rules of 4A one by one, the choice by `accepts`, the notes of `test-run`) and from recordings of Node 20.18.1, 22.22.0 and 24.11.1
(`tests/fixtures/test-runs/node-test-*.jsonl`; two of them are hand written: `"synthetic": true`)."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay, replay_fixture, load, chunks_from_text, stream_text  # noqa: E402

from skillsync_workflow import test_adapters, test_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_FILE = Path(test_adapters.__file__).parent / 'node_test.py'
HEAD = 'TAP version 13'
ARGV = ['node', '--test']


def setUpModule():
    test_adapters.discover()


def L(*lines):
    return '\n'.join(lines) + '\n'


def tail(tests, suites=0, extra=()):
    """The summary block of the `spec` reporter."""
    return L(f'ℹ tests {tests}', f'ℹ suites {suites}', 'ℹ pass 0', 'ℹ fail 0', 'ℹ cancelled 0', 'ℹ skipped 0', 'ℹ todo 0', 'ℹ duration_ms 12.5', *extra)


def run(text, exit_code=0, size=None, fd='out', argv=None):
    """The text through `--format node-test` (resolve by name), cut into chunks of `size` characters."""
    chunks = chunks_from_text(text, size or max(len(text), 1), fd=fd)
    return replay('node-test', chunks, argv=argv or ARGV, tool='node', exit_code=exit_code)


def table(recorder):
    """`[(id, state, group)]` in the order of the first emission, the last state of each id."""
    final = {}
    for c in recorder.emitted():
        final[c[1]] = (c[3], c[4])
    return [(k, v[0], v[1]) for k, v in final.items()]


def ids(recorder):
    return [c[1] for c in recorder.emitted()]


def msg_of(recorder, test_id):
    found = [c[6] for c in recorder.emitted() if c[1] == test_id and c[6]]
    return found[-1] if found else None


def name_of(recorder, test_id):
    return next(c[2] for c in recorder.emitted() if c[1] == test_id)


def live(lines, argv=None):
    """Feeds line by line (as given: `\\r` and ANSI stay); returns `(recorder, adapter, ctx, sizes)` where `sizes[i]` is the number of test emits right after line `i`."""
    recorder = ar.Recorder()
    adapter = test_adapters.resolve('node-test', argv or ARGV, 'node')
    ctx = test_adapters.AdapterContext(argv or ARGV, ar.ROOT, ar.ROOT, recorder)
    adapter.begin(ctx)
    sizes = []
    for line in lines:
        adapter.feed('out', line, ctx)
        sizes.append(len(recorder.emitted()))
    return recorder, adapter, ctx, sizes


class Registration(unittest.TestCase):
    def test_registry_and_attributes(self):
        self.assertIn('node-test', test_adapters.REGISTRY)
        self.assertIn('node-test', test_adapters.names())
        cls = test_adapters.REGISTRY['node-test']
        self.assertEqual((cls.name, cls.tools, cls.live), ('node-test', ('node',), 'completion'))
        self.assertIs(cls.explicit_only, False)   # `auto` may pick it
        self.assertEqual(cls().env(), {})   # Q5: the format is read as it is, the environment is not changed

    def test_auto_picks_node_test_for_node_test_and_nothing_for_a_bare_script(self):
        self.assertIsInstance(test_adapters.resolve('auto', ['node', '--test'], 'node'), test_adapters.REGISTRY['node-test'])
        self.assertIs(test_adapters.resolve('auto', ['node', '--test'], 'node').explicit, False)
        self.assertIsNone(test_adapters.resolve('auto', ['node', 'plik.js'], 'node'))
        self.assertIsNone(test_adapters.resolve('auto', ['node', 'plik.test.js'], 'node'))
        self.assertNotIsInstance(test_adapters.resolve('auto', ['node', 'x.js'], 'node') or object(), test_adapters.REGISTRY['tap'])

    def test_by_name_it_works_under_any_command_and_is_marked_explicit(self):
        for argv in (['node', 'plik.test.js'], ['node', 'x.js', '--test'], ['python3', '-c', 'print(1)'], ['npm', 'test']):
            with self.subTest(argv=argv):
                adapter = test_adapters.resolve('node-test', argv, 'node')
                self.assertIsInstance(adapter, test_adapters.REGISTRY['node-test'])
                self.assertIs(adapter.explicit, True)

    def test_no_hint_about_a_reporter(self):
        """4.5: both formats are readable, so there is no static `hint` (the stream decides)."""
        adapter = test_adapters.REGISTRY['node-test']()
        for argv in (['node', '--test'], ['node', '--test', '--test-reporter=spec'], ['node', '--test', '--test-reporter=tap', 'x.test.js']):
            with self.subTest(argv=argv):
                self.assertFalse(adapter.hint(argv, False))
                self.assertFalse(adapter.hint(argv, True))

    def test_the_module_only_reads_text_and_emits(self):
        self.assertTrue(ADAPTER_FILE.is_file(), 'skillsync_workflow/test_adapters/node_test.py')
        self.assertEqual(ar.audit(ADAPTER_FILE), [])

    def test_the_module_imports_in_a_fresh_interpreter_without_an_import_cycle(self):
        """`unwrap_command` lives in `test_run`, which imports `test_adapters`: `node_test` must import it lazily (inside `accepts`)."""
        for module in ('skillsync_workflow.test_adapters.node_test', 'skillsync_workflow.test_adapters', 'skillsync_workflow.test_run'):
            with self.subTest(first=module):
                done = subprocess.run([sys.executable, '-c', f'import {module}; import skillsync_workflow.test_run as r, skillsync_workflow.test_adapters as a; a.discover(); assert "node-test" in a.REGISTRY'],
                                      capture_output=True, text=True, cwd=str(ROOT), timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)


class Accepts(unittest.TestCase):
    """4.5: `accepts` reads the argv behind the wrappers; `--test` is a separate word before the first positional argument."""
    YES = [
        ['node', '--test'],
        ['node', '--test', 'x.test.js'],
        ['node', '--test', 'a.test.js', 'b.test.js'],
        ['timeout', '100', 'node', '--test'],
        ['env', 'X=1', 'node', '--test'],
        ['/path/to/node.exe', '--test'],
        ['C:\\Program Files\\nodejs\\node.exe', '--test'],
        ['/opt/node-v22/bin/node', '--test', 'x.test.js'],
        ['node', '--require', './x.js', '--test'],
        ['node', '-r', './x.js', '--test'],
        ['node', '--import', './y.mjs', '--test'],
        ['node', '--env-file', '.env', '--test'],
        ['node', '--loader', 'ts-node/esm', '--test'],
        ['node', '--conditions', 'dev', '--test'],
        ['node', '-C', 'dev', '--test'],
        ['node', '--conditions=dev', '--test'],
        ['node', '--experimental-strip-types', '--test'],
        ['node', '--test', '--test-reporter', 'tap'],
        ['node', '--test', '--test-reporter=tap'],
        ['node', '--test', '--test-reporter=spec'],
        ['node', '--test', '--test-reporter', 'spec', 'x.test.js'],
        ['node', '--test', '--test-reporter=tap', '--test-reporter=tap'],
        ['node', '--test-reporter=tap', '--test'],
        ['node', '--test', '--test-name-pattern', 'adds'],
        ['node', '--test', '--test-name-pattern=adds', 'x.test.js'],
        ['node', '--test', '--test-only'],
        ['node', '--test-only', '--test'],
    ]
    NO = [
        ['node', 'plik.js'],
        ['node', 'plik.test.js'],
        ['node', 'x.js', '--test'],   # `--test` after the script is an argument of the script
        ['node', '--test-only', 'plik.js'],
        ['node', '--test-name-pattern', 'foo', 'plik.js'],   # the value of the flag is not a positional argument, `plik.js` is, and there is no `--test`
        ['node', '--require', './x.js', 'plik.js', '--test'],
        ['node', '--test', '--test-reporter=dot'],
        ['node', '--test', '--test-reporter', 'dot'],
        ['node', '--test', '--test-reporter=junit'],
        ['node', '--test', '--test-reporter=lcov'],
        ['node', '--test', '--test-reporter=tap', '--test-reporter=dot'],
        ['node', '--test', '--test-reporter=spec', '--test-reporter-destination=stdout', '--test-reporter=tap', '--test-reporter-destination=out.tap'],   # a mixture
        ['node'],
        ['python3', '--test'],
        ['npm', 'test'],
        ['vitest', '--test'],
        ['nodemon', '--test'],
    ]

    def accepts(self, argv):
        return test_adapters.REGISTRY['node-test']().accepts(list(argv))

    def test_the_table_of_accepted_commands(self):
        for argv in self.YES:
            with self.subTest(argv=argv):
                self.assertTrue(self.accepts(argv))

    def test_the_table_of_refused_commands(self):
        for argv in self.NO:
            with self.subTest(argv=argv):
                self.assertFalse(self.accepts(argv))

    def test_a_wrapper_the_table_of_wrappers_unwraps_is_followed(self):
        argv = ['npx', 'node', '--test']
        if test_run.unwrap_command(argv) == ['node', '--test']:
            self.assertTrue(self.accepts(argv))
        else:
            self.skipTest('unwrap_command does not unwrap npx here')
        self.assertFalse(self.accepts(['npx', 'node', 'plik.js']))

    def test_two_wrappers_deep(self):
        self.assertTrue(self.accepts(['timeout', '100', 'env', 'X=1', 'node', '--test']))
        self.assertFalse(self.accepts(['timeout', '100', 'env', 'X=1', 'node', 'plik.js']))

    def test_the_choice_of_auto_agrees_with_accepts(self):
        for argv in self.YES + self.NO:
            with self.subTest(argv=argv):
                tool = test_run.detect_tool(argv)
                picked = test_adapters.resolve('auto', argv, tool)
                self.assertEqual(isinstance(picked, test_adapters.REGISTRY['node-test']), tool == 'node' and self.accepts(argv))

    def test_detect_tool_calls_node_test_a_node(self):
        self.assertEqual(test_run.detect_tool(['node', '--test', 'x']), 'node')
        self.assertEqual(test_run.detect_tool('node --test x'), 'node')


class Notice(unittest.TestCase):
    """4.5, K5: the sentence of `test-run` about the adapter for `node` (section 4.5, the last bullet)."""

    def notice(self, argv, fmt='auto'):
        tool = test_run.detect_tool(argv)
        return test_run.adapter_notice(fmt, argv, tool, test_adapters.resolve(fmt, argv, tool))

    def test_a_node_test_run_without_a_reporter_is_the_plain_notice_without_a_hint(self):
        notice = self.notice(['node', '--test'])
        self.assertEqual(notice, 'test-run: adapter node-test (live completion)')
        self.assertNotIn(';', notice)
        self.assertEqual(self.notice(['node', '--test', '--test-reporter=tap', 'x.test.js']), 'test-run: adapter node-test (live completion)')
        self.assertEqual(self.notice(['timeout', '100', 'node', '--test', '--test-reporter=spec']), 'test-run: adapter node-test (live completion)')

    def test_f5_1_minor_5_a_mixture_of_reporters_or_a_destination_asks_for_one_reporter_on_stdout(self):
        """Review F5-1 m5: the command already names reporters, so "add ..." is no advice; the remedy is ONE reporter, read from stdout."""
        mixes = (['node', '--test', '--test-reporter=spec', '--test-reporter-destination=stdout', '--test-reporter=tap', '--test-reporter-destination=out.tap'],
                 ['node', '--test', '--test-reporter=spec', '--test-reporter-destination=out.txt', '--test-reporter=dot'],
                 ['timeout', '100', 'node', '--test', '--test-reporter', 'tap', '--test-reporter', 'dot'])
        for argv in mixes:
            with self.subTest(argv=argv):
                notice = self.notice(argv)
                self.assertTrue(notice.startswith('test-run: '), notice)
                self.assertIn('node-test', notice)
                self.assertIn('stdout', notice)
                self.assertIn('single', notice)
                self.assertIn('--test-reporter=spec', notice)
                self.assertIn('tap', notice)
                self.assertNotIn(' add ', notice)
                self.assertNotIn('--format node-test', notice)

    def test_f5_1_minor_5_one_unreadable_reporter_is_replaced_not_added(self):
        notice = self.notice(['node', '--test', '--test-reporter=dot'])
        self.assertNotIn(' add ', notice)
        self.assertIn('--test-reporter=spec', notice)

    def test_a_reporter_other_than_tap_and_spec_names_the_two_that_are_readable(self):
        for argv in (['node', '--test', '--test-reporter=dot'], ['node', '--test', '--test-reporter', 'junit', 'x.test.js'], ['timeout', '100', 'node', '--test', '--test-reporter=lcov']):
            with self.subTest(argv=argv):
                notice = self.notice(argv)
                self.assertTrue(notice.startswith('test-run: '), notice)
                self.assertIn('node-test', notice)
                self.assertIn('tap', notice)
                self.assertIn('spec', notice)
                self.assertIn('--test-reporter=spec', notice)
                self.assertNotIn('--format node-test', notice)   # the command has `--test`: the remedy is the reporter, not the format

    def test_the_flag_after_the_script_is_not_a_test_flag(self):
        """Review F2-1 m3: the notice follows the walk over the flags (`scan`); `--test` after the script belongs to the script. Known limit: a flag with a value
        outside the table ends the walk, so a `--test` after it is not seen (the advice is then the one for a bare script)."""
        notice = self.notice(['node', 'x.js', '--test'])
        self.assertIn('--format node-test', notice)
        self.assertNotIn('--test-reporter', notice)
        self.assertNotIn('--test-reporter', self.notice(['node', '--test', '--unknown-flag', 'v', 'x.js']) or '')

    def test_a_bare_script_is_told_to_use_format_tap_or_node_test(self):
        for argv in (['node', 'plik.js'], ['node', 'x.test.js'], ['node', '--require', './x.js', 'plik.js'], ['env', 'X=1', 'node', 'plik.js']):
            with self.subTest(argv=argv):
                notice = self.notice(argv)
                self.assertIn('--format tap', notice)
                self.assertIn('--format node-test', notice)
                self.assertNotIn('--test-reporter', notice)
                self.assertNotIn('does not accept these arguments', notice)   # the old generic sentence is replaced

    def test_a_flag_that_is_not_test_gets_the_sentence_of_the_bare_script(self):
        notice = self.notice(['node', '--test-only', 'plik.js'])
        self.assertIn('--format tap', notice)

    def test_other_tools_keep_their_sentences(self):
        """The one existing case of the generic sentence (an adapter of the tool refuses the arguments) and the case of an unknown tool do not change."""
        self.assertEqual(self.notice(['pytest', '-q']), 'test-run: adapter pytest not used: it does not accept these arguments (see docs/test-adapters.md); counters and the report only')
        self.assertIn('no adapter for unknown', self.notice(['make', 'test']))
        self.assertIn('no adapter for npm', self.notice(['npm', 'test']))
        self.assertEqual(self.notice(['dotnet', 'test']), 'test-run: adapter dotnet (live completion)')
        for argv in (['pytest', '-q'], ['make', 'test'], ['npm', 'test']):
            self.assertNotIn('--format node-test', self.notice(argv))
            self.assertNotIn('--format tap', self.notice(argv))
            self.assertNotIn('--test-reporter', self.notice(argv))

    def test_an_explicit_format_is_silent(self):
        self.assertIsNone(self.notice(['node', 'plik.test.js'], fmt='node-test'))
        self.assertIsNone(self.notice(['node', 'plik.js'], fmt='tap'))
        self.assertIsNone(self.notice(['node', '--test', '--test-reporter=dot'], fmt='none'))


class Recognition(unittest.TestCase):
    """The first significant line decides between TAP and `spec`; the lines before it are skipped; after that the format does not change."""

    def test_a_tap_header_makes_it_tap(self):
        rec, adapter, ctx, complete = run(L(HEAD, 'ok 1 - a', 'not ok 2 - b', '1..2'), exit_code=1)
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'failed', None)])
        self.assertTrue(complete)

    def test_a_spec_marker_makes_it_spec(self):
        rec, adapter, ctx, complete = run(L('✔ a (1.5ms)', '✖ b (2ms)') + tail(2))
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'failed', None)])
        self.assertTrue(complete)

    def test_every_marker_recognizes_the_stream_also_after_an_indent(self):
        for first in ('▶ g', '✔ a (1ms)', '✖ a (1ms)', '﹣ a (1ms)', '   ✔ a (1ms)'):
            with self.subTest(first=first):
                rec = run(L('some log', first, '✔ b (1ms)'))[0]
                self.assertIn('b', [c[2] for c in rec.emitted()])

    def test_log_lines_before_the_first_marker_are_skipped_even_when_they_look_like_tap(self):
        rec = run(L('', 'starting...', 'ok 1 - not a test', 'not ok 2 - not a test either', '# Subtest: nope', '▶ g', '  ✔ a (1ms)', '✔ g (2ms)') + tail(1, 1))[0]
        self.assertEqual(table(rec), [('g::a', 'passed', 'g')])

    def test_logs_before_the_tap_header_are_skipped_too(self):
        rec = run(L('some log', 'x not a result (1ms)', HEAD, 'ok 1 - a', '1..1'))[0]
        self.assertEqual(table(rec), [('a', 'passed', None)])

    def test_an_empty_line_or_a_log_with_a_marker_before_the_tap_header_does_not_take_the_stream(self):
        """Review F2-1 M1: only a group header `▶` or a marker line of the shape of a result `(Nms)` chooses `spec`; a blank line and a log never do."""
        for before in ([''], ['   '], ['✔ załadowano'], ['✖ nie udało się'], ['', '✔ załadowano', '', '✖ nie']):
            with self.subTest(before=before):
                rec, adapter, ctx, complete = run(L(*before, HEAD, 'ok 1 - a', '1..1'))
                self.assertEqual(table(rec), [('a', 'passed', None)])
                self.assertTrue(complete)

    def test_a_blank_line_before_a_stream_of_another_reporter_emits_nothing(self):
        """A dot stream: a blank line does not choose `spec`; a line without the shape of a result `(Nms)` does not either. Decision: `✖ x (1ms)` has the shape, so it is read as `spec`."""
        rec, adapter, ctx, complete = run(L('', '..F.', '✖ x', '✔ y', 'ℹ tests 4'), exit_code=1)
        self.assertEqual(rec.calls, [])
        self.assertFalse(complete)

    def test_a_result_shaped_line_after_a_blank_line_chooses_spec(self):
        rec = run(L('', '✖ x (1ms)', '✔ y (1ms)') + tail(2), exit_code=1)[0]
        self.assertEqual(table(rec), [('x', 'failed', None), ('y', 'passed', None)])

    def test_an_ordinary_failure_after_an_error_log_does_not_take_the_log_as_its_message(self):
        """Review F2-1 m1: only a name that looks like a file (a `/` or .js .mjs .cjs .ts) is a file that did not load."""
        rec = run(L('✔ a (1ms)', 'Error: unrelated log', '✖ f (1ms)', '✔ g (1ms)') + tail(3), exit_code=1)[0]
        self.assertFalse(msg_of(rec, 'f'))
        self.assertEqual(rec.states('f'), ['failed'])
        loaded = run(L('✔ a (1ms)', 'Error: log', '✖ sub/c.test (1ms)') + tail(2), exit_code=1)[0]
        self.assertEqual(msg_of(loaded, 'sub/c.test'), 'Error: log')

    def test_the_spec_parser_keeps_a_bounded_table_of_names(self):
        from skillsync_workflow.test_adapters import node_test
        old = node_test.MAX_SEEN
        node_test.MAX_SEEN = 3
        try:
            rec = run(L(*[f'✔ t{i} (1ms)' for i in range(10)]) + tail(10))[0]
        finally:
            node_test.MAX_SEEN = old
        self.assertEqual(len([c for c in rec.emitted()]), 3)

    def test_tap_lines_without_the_header_are_nothing_in_auto(self):
        rec, adapter, ctx, complete = run(L('ok 1 - a', 'not ok 2 - b', '1..2'), exit_code=1)
        self.assertEqual(rec.emitted(), [])
        self.assertFalse(complete)

    def test_a_stream_of_another_reporter_emits_nothing_and_is_not_complete(self):
        for exit_code in (0, 1):
            with self.subTest(exit=exit_code):
                rec, adapter, ctx, complete = run(L('..F.', '.', '', 'Failed tests:', 'ℹ tests 4'), exit_code=exit_code)
                self.assertEqual(rec.calls, [])
                self.assertFalse(complete)   # True would make the recorder ignore a JUnit report

    def test_an_empty_stream_is_not_complete(self):
        for exit_code in (0, 1):
            self.assertEqual(run('', exit_code=exit_code)[3], False)
            self.assertEqual(run('', exit_code=exit_code)[0].calls, [])

    def test_after_spec_is_recognized_tap_lines_are_not_read(self):
        rec = run(L('✔ a (1ms)', HEAD, 'ok 1 - tap line', 'not ok 2 - another', '# Subtest: x', '    ok 1 - deep', '✔ b (1ms)'))[0]
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'passed', None)])

    def test_after_tap_is_recognized_spec_lines_are_not_read(self):
        rec = run(L(HEAD, 'ok 1 - a', '✔ spec line (1ms)', '▶ g', '  ✔ x (1ms)', '✖ y (1ms)', 'ok 2 - b', '1..2'))[0]
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'passed', None)])

    def test_stderr_is_ignored_in_both_formats(self):
        self.assertEqual(run(L('✔ a (1ms)') + tail(1), fd='err')[0].calls, [])
        self.assertEqual(run(L(HEAD, 'ok 1 - a', '1..1'), fd='err')[0].calls, [])
        rec, adapter, ctx, complete = run(L('✔ a (1ms)') + tail(1), fd='err')
        self.assertFalse(complete)

    def test_the_stream_that_arrives_in_pieces_is_recognized_on_the_whole_line(self):
        recorder, adapter, ctx, sizes = live([])
        adapter.partial('out', '✔ a (1', ctx)
        self.assertEqual(recorder.emitted(), [])
        adapter.feed('out', '✔ a (1ms)', ctx)
        self.assertEqual(ids(recorder), ['a'])


class SpecLines(unittest.TestCase):
    """Rule 1: the line of a result."""

    def test_the_four_markers_the_name_and_the_time(self):
        rec = run(L('✔ passed one (1.25ms)', '✖ failed one (12ms)', '﹣ skipped one (0.5ms)', '▶ group', '  ✔ child (3ms)', '✔ group (4ms)') + tail(4, 1))[0]
        self.assertEqual(table(rec), [('passed one', 'passed', None), ('failed one', 'failed', None), ('skipped one', 'skipped', None), ('group::child', 'passed', 'group')])
        by = {c[1]: c[5] for c in rec.emitted()}
        self.assertAlmostEqual(by['passed one'], 1.25, delta=0.5)
        self.assertAlmostEqual(by['failed one'], 12, delta=0.5)
        self.assertAlmostEqual(by['skipped one'], 0.5, delta=0.5)
        self.assertEqual(name_of(rec, 'passed one'), 'passed one')

    def test_the_time_may_be_an_integer(self):
        rec = run(L('✔ a (12ms)', '✔ b (0ms)'))[0]
        self.assertEqual([c[5] for c in rec.emitted()], [12, 0])

    def test_the_name_is_greedy_the_last_time_closed_by_the_end_of_the_line_counts(self):
        rec = run(L('✔ adds (2ms) and (3ms) more (4.5ms)', '✔ call f(1ms) now (6ms)'))[0]
        self.assertEqual([c[2] for c in rec.emitted()], ['adds (2ms) and (3ms) more', 'call f(1ms) now'])
        self.assertAlmostEqual(rec.emitted()[0][5], 4.5, delta=0.5)
        self.assertAlmostEqual(rec.emitted()[1][5], 6, delta=0.5)

    def test_a_note_that_ends_in_a_time_swallows_the_name_r9(self):
        """R9 of the spec (known limit): the name is greedy, so a note that itself ends in ` (Nms)` is taken for the time, and the text before it for the name."""
        rec = run(L('✔ a (1ms) # n (2ms)'))[0]
        self.assertEqual([(c[2], c[3]) for c in rec.emitted()], [('a (1ms) # n', 'passed')])
        self.assertAlmostEqual(rec.emitted()[0][5], 2, delta=0.5)

    def test_a_hash_in_the_name_is_part_of_the_name(self):
        rec = run(L('✔ name with # hash (1.8ms)', '✔ # starts with a hash (1ms)'))[0]
        self.assertEqual([(c[2], c[3]) for c in rec.emitted()], [('name with # hash', 'passed'), ('# starts with a hash', 'passed')])

    def test_a_backslash_in_the_name_is_not_an_escape(self):
        rec = run(L('✔ name with \\ backslash and - dash (0.3ms)', '✔ name with \\# hash (1ms)'))[0]
        self.assertEqual([c[2] for c in rec.emitted()], ['name with \\ backslash and - dash', 'name with \\# hash'])   # unlike TAP, `spec` does not escape

    def test_the_note_after_a_hash_is_the_note_not_the_name(self):
        rec = run(L('﹣ skipped (0.1ms) # not now', '✔ later (0.2ms) # a note with # inside'))[0]
        self.assertEqual([c[2] for c in rec.emitted()], ['skipped', 'later'])
        self.assertEqual(msg_of(rec, 'skipped'), 'not now')
        self.assertIn('a note with # inside', msg_of(rec, 'later'))

    def test_a_line_without_a_time_is_not_a_result_and_is_counted_as_dropped(self):
        rec, adapter, ctx, complete = run(L('✔ a (1ms)', '✔ b without a time', '✖ c', '﹣ d (x ms)', '✔ e (1ms)') + tail(2))
        self.assertEqual(ids(rec), ['a', 'e'])
        self.assertFalse(complete)   # `dropped` > 0

    def test_a_continuation_line_without_a_marker_is_ignored(self):
        rec = run(L('✔ first line of a name', 'second line of a name (0.9ms)', '✔ b (1ms)'))[0]
        self.assertEqual(ids(rec), ['b'])

    def test_a_marker_without_a_space_or_in_the_middle_of_a_line_is_not_a_result(self):
        rec = run(L('✔ a (1ms)', '✔b (1ms)', 'log ✔ c (1ms)', '    log: ✖ d (1ms)', '-✔ e (1ms)', '✔ f (1ms)'))[0]
        self.assertEqual(ids(rec), ['a', 'f'])

    def test_logs_without_a_prefix_are_not_results(self):
        """K15: `console.log` of a test and the stderr of a file go between the lines of the results without a prefix."""
        text = L('✔ a (1ms)', 'ok - looks like a result but is a log line', 'not ok 2 - another one', '# ok - and a comment', 'TAP version 13', '1..3', '✔ b (1ms)') + tail(2)
        rec, adapter, ctx, complete = run(text)
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'passed', None)])
        self.assertTrue(complete)   # the log lines do not move N == L - S

    def test_a_line_over_the_limit_is_not_a_result(self):
        rec = run(L('✔ ' + 'n' * 2100 + ' (1ms)', '✔ ok (1ms)'))[0]
        self.assertEqual(ids(rec), ['ok'])

    def test_ansi_and_the_carriage_return_are_removed(self):
        recorder, adapter, ctx, sizes = live(['\x1b[90m▶ g\x1b[39m\r', '  \x1b[32m✔ a\x1b[39m \x1b[90m(1.5ms)\x1b[39m\r', '  \x1b[33m﹣\x1b[39m s (0.1ms)\r', '  ✔ c (1ms) \x1b[33m# note\x1b[39m\r',
                                              '\x1b[32m✔ g\x1b[39m \x1b[90m(4ms)\x1b[39m\r', '\x1b[31m✖ b (2ms)\x1b[39m\r'])
        self.assertEqual(table(recorder), [('g::a', 'passed', 'g'), ('g::s', 'skipped', 'g'), ('g::c', 'skipped', 'g'), ('b', 'failed', None)])
        self.assertEqual(name_of(recorder, 'g::a'), 'a')   # no escape sequence and no `\r` stays in the name

    def test_a_carriage_return_after_the_time_and_the_note(self):
        recorder, adapter, ctx, sizes = live(['✔ a (1.5ms)\r', '✔ b (1ms) # later\r', 'ℹ tests 2\r', 'ℹ suites 0\r'])
        self.assertEqual([(c[1], c[3]) for c in recorder.emitted()], [('a', 'passed'), ('b', 'skipped')])
        self.assertTrue(adapter.end(ctx, 0, '', []))


class SpecGroups(unittest.TestCase):
    """Rules 2 and 3: groups, leaves, ids."""

    def test_the_header_of_a_group_emits_nothing_and_a_leaf_at_once(self):
        recorder, adapter, ctx, sizes = live(['▶ g', '  ✔ a (1ms)', '  ✖ b (2ms)', '  ▶ h', '    ✔ c (1ms)', '  ✔ h (2ms)', '✖ g (9ms)', '✔ top (1ms)'])
        self.assertEqual(sizes, [0, 1, 2, 2, 3, 3, 3, 4])   # `▶` nothing, a leaf at its own line, a closing line of a group nothing (here the failed `g` has a failed child)
        self.assertEqual(table(recorder), [('g::a', 'passed', 'g'), ('g::b', 'failed', 'g'), ('g > h::c', 'passed', 'g > h'), ('top', 'passed', None)])

    def test_a_good_closing_line_emits_nothing(self):
        rec = run(L('▶ g', '  ✔ a (1ms)', '✔ g (2ms)', '▶ h', '  ▶ i', '    ✔ b (1ms)', '  ✔ i (1ms)', '✔ h (3ms)'))[0]
        self.assertEqual(ids(rec), ['g::a', 'h > i::b'])
        self.assertEqual(table(rec)[1][2], 'h > i')

    def test_a_group_named_like_its_leaf(self):
        rec = run(L('▶ g', '  ✔ g (1ms)', '✔ g (2ms)', '▶ same', '  ▶ same', '    ✔ x (1ms)', '  ✔ same (1ms)', '✔ same (2ms)'))[0]
        self.assertEqual(table(rec), [('g::g', 'passed', 'g'), ('same > same::x', 'passed', 'same > same')])   # the closing line is at the indent of the header, the leaf is deeper

    def test_k3_a_failed_group_with_a_failed_child_emits_nothing_at_any_level(self):
        rec = run(L('▶ a', '  ▶ b', '    ✖ c (1ms)', '  ✖ b (2ms)', '✖ a (3ms)'))[0]
        self.assertEqual(table(rec), [('a > b::c', 'failed', 'a > b')])
        self.assertNotIn('a', ids(rec))
        self.assertNotIn('a::b', ids(rec))

    def test_k3_a_failed_group_without_a_failed_child_is_a_failed_leaf_of_its_own(self):
        rec = run(L('▶ g', '  ✔ a (1ms)', '  ﹣ s (1ms)', '✖ g (4ms)'))[0]
        self.assertEqual(table(rec), [('g::a', 'passed', 'g'), ('g::s', 'skipped', 'g'), ('g', 'failed', None)])
        self.assertEqual(name_of(rec, 'g'), 'g')

    def test_k3_a_nested_failed_group_keeps_the_group_of_its_own_level(self):
        rec = run(L('▶ a', '  ▶ b', '    ✔ c (1ms)', '  ✖ b (2ms)', '✔ a (3ms)'))[0]
        self.assertEqual(table(rec), [('a > b::c', 'passed', 'a > b'), ('a::b', 'failed', 'a')])

    def test_k3_a_failed_child_of_a_sibling_group_does_not_excuse_this_group(self):
        rec = run(L('▶ a', '  ▶ b', '    ✖ c (1ms)', '  ✖ b (2ms)', '  ▶ d', '    ✔ e (1ms)', '  ✖ d (2ms)', '✖ a (5ms)'))[0]
        self.assertEqual(table(rec), [('a > b::c', 'failed', 'a > b'), ('a > d::e', 'passed', 'a > d'), ('a::d', 'failed', 'a')])   # `d` had no failed descendant; `a` has one

    def test_a_closing_line_needs_the_name_of_the_open_group(self):
        rec = run(L('▶ g', '  ✔ a (1ms)', '✖ other (2ms)'))[0]
        self.assertEqual(table(rec), [('g::a', 'passed', 'g'), ('other', 'failed', None)])

    def test_the_same_name_in_two_groups_is_not_a_duplicate(self):
        rec = run(L('▶ a', '  ✔ x (1ms)', '✔ a (1ms)', '▶ b', '  ✔ x (1ms)', '✔ b (1ms)'))[0]
        self.assertEqual(ids(rec), ['a::x', 'b::x'])

    def test_duplicates_get_a_suffix_in_the_id_and_in_the_name_in_order_of_arrival(self):
        rec = run(L('✔ dup (1ms)', '✔ dup (1ms)', '✖ dup (1ms)', '▶ g', '  ✔ dup (1ms)', '  ✔ dup (1ms)', '✔ g (1ms)'))[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted()],
                         [('dup', 'dup', 'passed'), ('dup (2)', 'dup (2)', 'passed'), ('dup (3)', 'dup (3)', 'failed'), ('g::dup', 'dup', 'passed'), ('g::dup (2)', 'dup (2)', 'passed')])

    def test_the_group_of_a_top_level_leaf_is_none(self):
        rec = run(L('✔ a (1ms)'))[0]
        self.assertIsNone(rec.emitted()[0][4])

    def test_a_group_open_at_the_end_of_the_stream_keeps_the_leaves_already_sent(self):
        recorder, adapter, ctx, sizes = live(['▶ g', '  ✔ a (1ms)', '  ✔ b (1ms)'])
        self.assertEqual(ids(recorder), ['g::a', 'g::b'])
        self.assertFalse(adapter.end(ctx, 1, '', []))


class Depth(unittest.TestCase):
    """Review F5-1 m2: the depth of the groups is limited in both readers (a hostile stream cannot grow the group name without a bound)."""

    def test_spec_groups_deeper_than_the_limit_are_not_stacked(self):
        name = 'n' * 250
        lines = [' ' * (2 * i) + f'▶ {name}' for i in range(400)] + [' ' * 800 + '✔ deep (1ms)']
        rec = run(L(*lines))[0]
        groups = [c[4] for c in rec.emitted()]
        self.assertEqual(len(groups), 1)
        self.assertLess(len(groups[0] or ''), 50 * (len(name) + 3) + 1)

    def test_tap_groups_deeper_than_the_limit_are_not_stacked(self):
        name = 'n' * 250
        lines = [HEAD] + [' ' * (2 * i) + f'# Subtest: {name}' for i in range(400)] + [' ' * 800 + 'ok 1 - deep']
        rec = run(L(*lines))[0]
        groups = [c[4] for c in rec.emitted()]
        self.assertEqual(len(groups), 1)
        self.assertLess(len(groups[0] or ''), 50 * (len(name) + 3) + 1)


class Interrupted(unittest.TestCase):
    """Review F5-1 m3: an interrupted process (exit code None: SIGTERM, timeout) is never a complete stream."""

    def test_spec_without_the_summary_and_tap_without_a_plan_are_not_complete(self):
        for text in (L('▶ g', '  ✔ a (1ms)'), L(HEAD, 'ok 1 - a')):
            with self.subTest(text=text):
                rec, adapter, ctx, complete = run(text, exit_code=None)
                self.assertFalse(complete)
                self.assertEqual(ids(rec).count('a') + ids(rec).count('g::a'), 1)   # what was read stays; nothing is invented
                self.assertNotIn('failed', [c[3] for c in rec.emitted()])


class SpecStates(unittest.TestCase):
    """Rule 4: the states and the notes."""

    def test_the_four_states_and_the_notes(self):
        rec = run(L('✔ p (1ms)', '✖ f (1ms)', '﹣ s (1ms)', '﹣ s2 (1ms) # not now', '✔ t (1ms) # later', '✖ tf (1ms) # known bug', '✔ t2 (1ms) # TODO', '﹣ s3 (1ms) # SKIP'))[0]
        self.assertEqual([(c[1], c[3]) for c in rec.emitted()],
                         [('p', 'passed'), ('f', 'failed'), ('s', 'skipped'), ('s2', 'skipped'), ('t', 'skipped'), ('tf', 'skipped'), ('t2', 'skipped'), ('s3', 'skipped')])
        self.assertEqual(msg_of(rec, 's2'), 'not now')
        self.assertEqual(msg_of(rec, 't'), 'todo: later')
        self.assertIn('known bug', msg_of(rec, 'tf'))
        self.assertEqual(msg_of(rec, 't2'), 'todo: TODO')
        self.assertEqual(msg_of(rec, 's3'), 'SKIP')
        self.assertFalse(msg_of(rec, 'p'))
        self.assertFalse(msg_of(rec, 's'))

    def test_k14_a_todo_that_failed_is_skipped_not_failed(self):
        rec = run(L('✖ todo fails (0.45ms) # known bug', '✔ ok (1ms)') + tail(2))[0]
        self.assertEqual([c[3] for c in rec.emitted()], ['skipped', 'passed'])
        self.assertNotIn('failed', [c[3] for c in rec.emitted()])

    def test_the_time_of_each_line_goes_with_the_first_emit_of_its_test(self):
        rec = run(L('✔ p (7ms)', '✖ f (8ms)', '﹣ s (9ms)', '✖ t (10ms) # x'))[0]
        self.assertEqual([c[5] for c in rec.emitted()], [7, 8, 9, 10])

    def test_a_passed_or_skipped_test_is_emitted_once(self):
        rec = run(L('✔ p (1ms)', '    detail that is deeper', '﹣ s (1ms)', '    another', '✔ t (1ms) # x', '    more'))[0]
        self.assertEqual(len(rec.emitted()), 3)


class SpecMessages(unittest.TestCase):
    """Rule 5: Node 20 prints the detail of a failure under the line; Node 22 and 24 do not."""

    def test_node_20_the_first_non_empty_deeper_line_is_the_message_and_the_leaf_is_sent_again(self):
        rec = run(L('▶ g', '  ✔ a (1ms)', '  ✖ f (2.5ms)', '    AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:', '    ', '    1 !== 2', '        at x.js:1:1', '✖ g (4ms)') + tail(2, 1), exit_code=1)[0]
        self.assertEqual(rec.states('g::f'), ['failed', 'failed'])
        self.assertEqual(msg_of(rec, 'g::f'), 'AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:')
        first, again = [c for c in rec.emitted() if c[1] == 'g::f']
        self.assertFalse(first[6])   # the first emit is at the line of the result, before any detail
        self.assertAlmostEqual(again[5], 2.5, delta=0.5)
        self.assertEqual(rec.states('g::a'), ['passed'])

    def test_blank_lines_before_the_text_are_skipped(self):
        rec = run(L('✖ f (1ms)', '    ', '', '      indented detail', '✔ g (1ms)'))[0]
        self.assertEqual(msg_of(rec, 'f'), 'indented detail')

    def test_the_detail_ends_at_the_next_marker(self):
        rec = run(L('✖ f (1ms)', '✔ g (1ms)', '    not a detail of f, nor a result'))[0]
        self.assertFalse(msg_of(rec, 'f'))
        self.assertEqual(rec.states('f'), ['failed'])

    def test_the_message_comes_before_the_next_result(self):
        rec = run(L('✖ f (1ms)', '    boom', '✔ g (1ms)'))[0]
        self.assertEqual([(c[1], bool(c[6])) for c in rec.emitted()], [('f', False), ('f', True), ('g', False)])

    def test_the_message_is_there_when_the_summary_follows_the_detail(self):
        rec = run(L('✖ f (1ms)', '    Error: boom', '    at x') + tail(1))[0]
        self.assertEqual(msg_of(rec, 'f'), 'Error: boom')

    def test_the_detail_is_looked_for_in_at_most_thirty_lines(self):
        rec = run(L('✖ f (1ms)', *([''] * 35), '    too far', '✔ g (1ms)'))[0]
        self.assertFalse(msg_of(rec, 'f'))
        near = run(L('✖ f (1ms)', *([''] * 20), '    near enough', '✔ g (1ms)'))[0]
        self.assertEqual(msg_of(near, 'f'), 'near enough')

    def test_a_line_that_is_not_deeper_than_the_result_is_not_its_detail(self):
        """Node 22 and 24: a log without a prefix after a failed test at the same indent is nobody's message."""
        rec = run(L('✖ f (1ms)', 'a log line at column zero', '    ✔ not a result', '✔ g (1ms)'))[0]
        self.assertFalse(msg_of(rec, 'f'))
        self.assertEqual(rec.states('f'), ['failed'])
        nested = run(L('▶ g', '  ✖ f (1ms)', 'a log at column zero', '  a log at the indent of f', '✖ g (1ms)'))[0]
        self.assertFalse(msg_of(nested, 'g::f'))

    def test_node_22_and_24_have_no_message_for_an_ordinary_failure(self):
        rec = run(L('✔ a (1ms)', '✖ f (1ms)', '✔ b (1ms)') + tail(3), exit_code=1)[0]
        self.assertEqual(rec.states('f'), ['failed'])
        self.assertFalse(msg_of(rec, 'f'))

    def test_a_file_that_did_not_load_takes_the_error_line_before_it_node_20(self):
        text = L('✔ a (1ms)', '/work/proj/c.test.js:1', 'const x = ;', '          ^', '', "SyntaxError: Unexpected token ';'", '    at wrapSafe (node:internal/modules/cjs/loader:1378:20)', '', 'Node.js v20.18.1',
                 '✖ /work/proj/c.test.js (84ms)', "  'test failed'", '')
        rec = run(text + tail(2), exit_code=1)[0]
        self.assertEqual(msg_of(rec, '/work/proj/c.test.js'), "SyntaxError: Unexpected token ';'")   # not the line `'test failed'`

    def test_a_file_that_did_not_load_takes_the_error_line_before_it_node_22_and_24(self):
        text = L('✔ a (1ms)', '/work/proj/c.test.js:1', 'const x = ;', '', "SyntaxError: Unexpected token ';'", '    at wrapSafe (node:internal/modules/cjs/loader:1692:18)', 'Node.js v22.22.0', '✖ c.test.js (59ms)',
                 '/work/proj/d.test.js:1', 'throw new Error(\'file level crash\');', '', 'Error: file level crash', '    at Object.<anonymous> (/work/proj/d.test.js:1:7)', 'Node.js v22.22.0', '✖ d.test.js (53ms)')
        rec = run(text + tail(3), exit_code=1)[0]
        self.assertEqual(msg_of(rec, 'c.test.js'), "SyntaxError: Unexpected token ';'")
        self.assertEqual(msg_of(rec, 'd.test.js'), 'Error: file level crash')   # the window starts after the previous result: the SyntaxError of c does not leak

    def test_the_error_line_before_the_previous_result_does_not_count(self):
        rec = run(L('✔ first (1ms)', 'Error: old log', '✔ a (1ms)', 'a plain line', '✖ c.test.js (5ms)') + tail(3), exit_code=1)[0]
        self.assertFalse(msg_of(rec, 'c.test.js'))

    def test_the_last_error_line_wins(self):
        rec = run(L('✔ a (1ms)', 'Error: first', 'TypeError: second', 'text', '✖ c.test.js (5ms)') + tail(2), exit_code=1)[0]
        self.assertEqual(msg_of(rec, 'c.test.js'), 'TypeError: second')


class SpecSummary(unittest.TestCase):
    """Rules 6 and 7: where the parsing stops and when the counters of the adapter are complete."""

    def test_k15_the_summary_ends_the_parsing_the_block_of_repeats_gives_no_result(self):
        text = L('✔ a (1ms)', '✖ f (2ms)') + tail(2, 0, ('', '✖ failing tests:', '', 'test at x.test.js:5:3', '✖ f (2ms)', '  Error: boom', '✔ late (1ms)', 'ℹ tests 99'))
        rec, adapter, ctx, complete = run(text, exit_code=1)
        self.assertEqual(ids(rec), ['a', 'f'])
        self.assertEqual(rec.states('f'), ['failed'])
        self.assertTrue(complete)   # the repeats are neither in L nor in the results; the second `ℹ tests` does not replace N

    def test_the_summary_line_has_to_be_at_indent_zero(self):
        rec, adapter, ctx, complete = run(L('✔ a (1ms)', '  ℹ tests 5', '✔ b (1ms)') + tail(2))
        self.assertEqual(ids(rec), ['a', 'b'])
        self.assertTrue(complete)

    def test_end_is_true_when_n_equals_leaves_plus_closings_minus_suites(self):
        text = L('▶ g', '  ✔ a (1ms)', '  ✔ b (1ms)', '✔ g (3ms)', '✔ top (1ms)')
        self.assertTrue(run(text + tail(3, 1))[3])   # L = 4 (a, b, g, top), S = 1, N = 3
        self.assertFalse(run(text + tail(4, 1))[3])
        self.assertFalse(run(text + tail(2, 1))[3])
        self.assertFalse(run(text + tail(3, 0))[3])
        self.assertTrue(run(L('✔ a (1ms)', '✔ b (1ms)') + tail(2, 0))[3])
        self.assertFalse(run(L('✔ a (1ms)', '✔ b (1ms)') + tail(3, 0))[3])

    def test_the_exit_code_does_not_matter_for_end(self):
        text = L('✔ a (1ms)', '✖ b (1ms)') + tail(2)
        self.assertTrue(run(text, exit_code=1)[3])
        self.assertTrue(run(text, exit_code=0)[3])

    def test_end_is_false_without_the_summary_or_with_half_of_it(self):
        self.assertFalse(run(L('✔ a (1ms)'))[3])
        self.assertFalse(run(L('✔ a (1ms)', 'ℹ tests 1'))[3])   # no `ℹ suites`
        self.assertFalse(run(L('✔ a (1ms)', 'ℹ suites 0'))[3])   # no `ℹ tests`
        self.assertTrue(run(L('✔ a (1ms)', 'ℹ tests 1', 'ℹ suites 0'))[3])

    def test_end_is_false_for_a_stream_without_a_result(self):
        self.assertFalse(run(L('▶ g') + tail(0, 0))[3])
        self.assertFalse(run(tail(0, 0))[3])

    def test_end_is_false_when_a_line_was_dropped(self):
        self.assertFalse(run(L('✔ a (1ms)', '✔ broken name') + tail(1))[3])   # the counts fit (N = L = 1); only `dropped` makes it False
        self.assertTrue(run(L('✔ a (1ms)') + tail(1))[3])

    def test_end_is_false_when_a_group_is_left_open(self):
        text = L('▶ g', '  ✔ a (1ms)', '  ✔ b (1ms)') + tail(1, 1)   # N = L - S = 1, but no closing line of `g`
        self.assertFalse(run(text)[3])
        self.assertTrue(run(L('▶ g', '  ✔ a (1ms)', '✔ g (1ms)') + tail(1, 1))[3])

    def test_end_does_not_depend_on_the_chunks(self):
        text = L('▶ g', '  ✔ a (1ms)', '✔ g (1ms)') + tail(1, 1)
        for size in (1, 3, 7):
            self.assertTrue(run(text, size=size)[3], size)


class Continuity(unittest.TestCase):
    """The same stream cut at any place gives the same emits (a pipe delivers any split).

    What it proves: `feed` always gets whole lines (the replay joins the tails of the chunks into lines, as `quality._stream` does), so the test proves the whole chain
    chunk -> line -> recognition -> result: an emit never depends on where the pipe cut the stream, and the recognition of the format does not either. It does not
    exercise the adapter's own `partial` (which it may not have)."""
    NAMES = ('node-test-spec-node20-describe.jsonl', 'node-test-spec-node22-describe.jsonl', 'node-test-spec-node22-edge.jsonl', 'node-test-spec-node24-multi.jsonl', 'node-test-spec-node20-multi.jsonl',
             'node-test-todo-node24-spec.jsonl', 'node-test-spec-node24-pty.jsonl', 'node-test-spec-node24-pty-ansi.jsonl', 'node-test-tap-node22-describe.jsonl', 'node-test-tap-node22-multi.jsonl',
             'node-test-tap-node20-edge.jsonl', 'node-test-dot-unknown.jsonl')

    def test_sizes_1_3_7_give_the_emits_of_the_whole_stream(self):
        for name in self.NAMES:
            meta = load(name)[0]
            text = stream_text(name)
            whole = replay('node-test', chunks_from_text(text, len(text)), argv=meta['command'], tool='node', exit_code=meta['exit'])
            for size in (1, 3, 7):
                with self.subTest(fixture=name, size=size):
                    cut = replay('node-test', chunks_from_text(text, size), argv=meta['command'], tool='node', exit_code=meta['exit'])
                    self.assertEqual(cut[0].calls, whole[0].calls)
                    self.assertEqual(cut[3], whole[3])
            if 'dot-unknown' not in name:
                self.assertTrue(whole[0].emitted(), name)

    def test_the_recorded_chunks_give_the_same_as_the_whole_text(self):
        for name in self.NAMES:
            with self.subTest(fixture=name):
                by_chunks = replay_fixture('node-test', name)
                text = stream_text(name)
                meta = load(name)[0]
                whole = replay('node-test', chunks_from_text(text, len(text)), argv=meta['command'], tool='node', exit_code=meta['exit'])
                self.assertEqual(by_chunks[0].calls, whole[0].calls)


DESCRIBE = [('math::adds', 'passed', 'math'), ('math::fails', 'failed', 'math'), ('math::skipped one', 'skipped', 'math'), ('math::todo one', 'skipped', 'math'),
            ('math > nested::deep', 'passed', 'math > nested'), ('top level', 'passed', None), ('slow', 'passed', None)]
EDGE = [('name with # hash', 'passed', None), ('name with \\ backslash and - dash', 'passed', None), ('dup', 'passed', None), ('dup (2)', 'passed', None),
        ('with subtests::inner ok', 'passed', 'with subtests'), ('with subtests::inner bad', 'failed', 'with subtests'), ('cancelled by timeout', 'failed', None)]
NEW_LINE = 'multi\\nline name'   # the TAP id of the test with a new line in its name: a backslash and an `n`
MULTI = [('adds', 'passed', None), ('only a', 'passed', None), ('adds (2)', 'passed', None), ('b fails', 'failed', None), ('c.test.js', 'failed', None), ('d.test.js', 'failed', None)]


class SpecFixtures(unittest.TestCase):
    """The recordings of the `spec` reporter (Node 20.18.1, 22.22.0, 24.11.1)."""

    def test_describe_on_node_20_22_and_24(self):
        for v in ('20', '22', '24'):
            name = f'node-test-spec-node{v}-describe.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), DESCRIBE)
                self.assertTrue(complete)   # N = 7 = L (9) - S (2)
                self.assertNotIn('math', ids(rec))   # the closing line of a group whose child failed is not a test
                self.assertNotIn('math > nested', ids(rec))
                self.assertIn('not now', msg_of(rec, 'math::skipped one'))
                self.assertIn('later', msg_of(rec, 'math::todo one'))
                self.assertIn('todo', msg_of(rec, 'math::todo one').lower())
                self.assertNotIn('running', [c[3] for c in rec.emitted()])
                self.assertEqual(len(rec.states('math::adds')), 1)

    def test_k15_the_block_of_repeats_is_not_a_result(self):
        for v in ('20', '22', '24'):
            with self.subTest(node=v):
                name = f'node-test-spec-node{v}-describe.jsonl'
                text = stream_text(name)
                self.assertIn('✖ failing tests:', text)
                self.assertEqual(text.count('✖ fails ('), 2)   # the line of the result and its repeat
                rec = replay_fixture('node-test', name)[0]
                self.assertEqual(sorted(set(ids(rec))), sorted(i for i, _, _ in DESCRIBE))

    def test_the_message_of_the_failure_only_on_node_20(self):
        rec20 = replay_fixture('node-test', 'node-test-spec-node20-describe.jsonl')[0]
        self.assertEqual(rec20.states('math::fails'), ['failed', 'failed'])
        self.assertEqual(msg_of(rec20, 'math::fails'), 'AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:')
        for v in ('22', '24'):
            rec = replay_fixture('node-test', f'node-test-spec-node{v}-describe.jsonl')[0]
            self.assertEqual(rec.states('math::fails'), ['failed'])
            self.assertFalse(msg_of(rec, 'math::fails'))
        for rec in (rec20,):
            for test_id in ('math::adds', 'math::skipped one', 'math::todo one', 'math > nested::deep', 'top level', 'slow'):
                self.assertEqual(len(rec.states(test_id)), 1, test_id)

    def test_edge_the_name_with_a_new_line_is_lost_and_end_is_false(self):
        for v in ('20', '22', '24'):
            name = f'node-test-spec-node{v}-edge.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), EDGE)   # `✔ multi` / `line name (0.9ms)`: the first line has no time and the second no marker
                self.assertFalse(complete)   # K15: dropped, and N (9) != L (8)
                self.assertNotIn('multi', ids(rec))
                self.assertEqual(name_of(rec, 'dup (2)'), 'dup (2)')
                self.assertNotIn('with subtests', ids(rec))
                self.assertEqual(len(rec.emitted()), len(EDGE) + (2 if v == '20' else 0))   # Node 20 sends the two failures again with a message

    def test_edge_node_20_messages(self):
        rec = replay_fixture('node-test', 'node-test-spec-node20-edge.jsonl')[0]
        self.assertEqual(msg_of(rec, 'with subtests::inner bad'), 'Error: boom')
        self.assertIn('test timed out after 50ms', msg_of(rec, 'cancelled by timeout'))
        for v in ('22', '24'):
            rec = replay_fixture('node-test', f'node-test-spec-node{v}-edge.jsonl')[0]
            self.assertFalse(msg_of(rec, 'with subtests::inner bad'))
            self.assertFalse(msg_of(rec, 'cancelled by timeout'))

    def test_edge_would_be_complete_with_a_name_on_one_line(self):
        """K15: the same stream with the name on one line has N == L - S (9 - 0), so only the lost test made `end` False."""
        for v in ('22', '24'):
            with self.subTest(node=v):
                text = stream_text(f'node-test-spec-node{v}-edge.jsonl')
                fixed = text.replace('✔ multi\nline name (', '✔ multi line name (')
                self.assertNotEqual(fixed, text)
                rec, adapter, ctx, complete = run(fixed, exit_code=1)
                self.assertTrue(complete)
                self.assertIn('multi line name', ids(rec))

    def test_multi_node_22_and_24(self):
        for v in ('22', '24'):
            name = f'node-test-spec-node{v}-multi.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), MULTI)
                self.assertTrue(complete)   # N = 6 = L (6) - S (0)
                self.assertEqual(msg_of(rec, 'c.test.js'), "SyntaxError: Unexpected token ';'")
                self.assertEqual(msg_of(rec, 'd.test.js'), 'Error: file level crash')
                self.assertFalse(msg_of(rec, 'b fails'))
                self.assertFalse(any('looks like' in c[2] for c in rec.emitted()))   # a log line without a prefix

    def test_multi_node_20_has_absolute_names_of_files_and_messages_under_the_lines(self):
        rec, adapter, ctx, complete = replay_fixture('node-test', 'node-test-spec-node20-multi.jsonl')
        self.assertEqual(table(rec), [('adds', 'passed', None), ('only a', 'passed', None), ('adds (2)', 'passed', None), ('b fails', 'failed', None),
                                      ('/work/proj/multi/c.test.js', 'failed', None), ('/work/proj/multi/d.test.js', 'failed', None)])   # anonymized absolute paths of Node 20
        self.assertTrue(complete)
        self.assertEqual(msg_of(rec, '/work/proj/multi/c.test.js'), "SyntaxError: Unexpected token ';'")   # `'test failed'` under the line loses to the Error line before it
        self.assertEqual(msg_of(rec, '/work/proj/multi/d.test.js'), 'Error: file level crash')
        self.assertEqual(msg_of(rec, 'b fails'), 'AssertionError [ERR_ASSERTION]: The expression evaluated to a falsy value:')

    def test_k14_todo_on_node_22_and_24(self):
        for v in ('22', '24'):
            name = f'node-test-todo-node{v}-spec.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), [('todo fails', 'skipped', None), ('todo passes', 'skipped', None), ('skip bare', 'skipped', None)])
                self.assertNotIn('failed', [c[3] for c in rec.emitted()])
                self.assertTrue(complete)
                self.assertEqual(load(name)[0]['exit'], 0)   # the recorded exit code is 0: a todo that failed is not red
                self.assertIn('known bug', msg_of(rec, 'todo fails'))
                self.assertIn('todo', msg_of(rec, 'todo passes').lower())
                self.assertEqual(len(rec.emitted()), 3)   # the failing block at the end has a `✖ todo fails` line again: not a result

    def test_a_terminal_run_with_carriage_returns(self):
        name = 'node-test-spec-node24-pty.jsonl'
        self.assertIn('\r\n', stream_text(name))
        rec, adapter, ctx, complete = replay_fixture('node-test', name)
        self.assertEqual(table(rec), DESCRIBE)
        self.assertTrue(complete)
        recorder, adapter, ctx, sizes = live(stream_text(name).split('\n'))   # the lines with their `\r`, as a reader that does not strip it would give them
        self.assertEqual(table(recorder), DESCRIBE)
        self.assertTrue(adapter.end(ctx, 1, '', []))

    def test_a_terminal_run_with_colors(self):
        name = 'node-test-spec-node24-pty-ansi.jsonl'
        text = stream_text(name)
        self.assertIn('\x1b[', text)
        self.assertTrue(load(name)[0].get('synthetic'))
        rec, adapter, ctx, complete = replay_fixture('node-test', name)
        self.assertEqual(table(rec), DESCRIBE)
        self.assertTrue(complete)
        self.assertEqual(name_of(rec, 'math::adds'), 'adds')   # no escape sequence in the name
        self.assertTrue(all('\x1b' not in c[2] and '\x1b' not in (c[6] or '') for c in rec.emitted()))
        recorder, adapter, ctx, sizes = live(text.split('\n'))   # the same with the `\r` of the lines left on
        self.assertEqual(table(recorder), DESCRIBE)

    def test_a_stream_of_the_dot_reporter_gives_nothing(self):
        rec, adapter, ctx, complete = replay_fixture('node-test', 'node-test-dot-unknown.jsonl')
        self.assertEqual(rec.calls, [])
        self.assertFalse(complete)
        self.assertTrue(load('node-test-dot-unknown.jsonl')[0].get('synthetic'))


class TapFixtures(unittest.TestCase):
    """The TAP streams of `node --test` read through `node-test` (the stream starts with `TAP version`)."""
    EDGE_TAP = EDGE + [(NEW_LINE, 'passed', None)]

    def test_describe_on_node_20_22_and_24(self):
        for v in ('20', '22', '24'):
            name = f'node-test-tap-node{v}-describe.jsonl'
            with self.subTest(fixture=name):
                self.assertIn('--test', load(name)[0]['command'])
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), DESCRIBE)
                self.assertTrue(complete)
                self.assertEqual(msg_of(rec, 'math::fails'), 'Expected values to be strictly equal:')

    def test_edge_on_node_20_22_and_24(self):
        for v in ('20', '22', '24'):
            name = f'node-test-tap-node{v}-edge.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), self.EDGE_TAP)
                self.assertTrue(complete)   # TAP keeps the test with a new line in its name (`multi\\nline name`)
                self.assertEqual(msg_of(rec, 'with subtests::inner bad'), 'boom')

    def test_multi_on_node_22_and_24(self):
        for v in ('22', '24'):
            name = f'node-test-tap-node{v}-multi.jsonl'
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('node-test', name)
                self.assertEqual(table(rec), MULTI)
                self.assertTrue(complete)
                self.assertEqual(msg_of(rec, 'c.test.js'), "SyntaxError: Unexpected token ';'")
                self.assertEqual(msg_of(rec, 'd.test.js'), 'Error: file level crash')

    def test_every_node_test_fixture_has_its_meta_and_a_command_with_test(self):
        names = sorted(p.name for p in ar.FIXTURES.glob('node-test-*.jsonl'))
        self.assertGreaterEqual(len(names), 22)
        for name in names:
            with self.subTest(fixture=name):
                meta = load(name)[0]
                self.assertIn('--test', meta['command'])
                self.assertIn(meta['stream'], ('spec', 'tap', 'dot'))
                self.assertEqual(meta['stream'], 'spec' if '-spec' in name else 'tap' if '-tap-' in name else 'dot')


class K12(unittest.TestCase):
    """K12: the same project in two formats gives the same tables (id, state, group); only `msg` may differ, and the test with a new line in its name is excluded (lost in `spec`)."""

    def tables(self, tap, spec):
        t = table(replay_fixture('node-test', tap)[0])
        s = table(replay_fixture('node-test', spec)[0])
        return t, s

    def test_describe_node_20_22_24(self):
        for v in ('20', '22', '24'):
            with self.subTest(node=v):
                t, s = self.tables(f'node-test-tap-node{v}-describe.jsonl', f'node-test-spec-node{v}-describe.jsonl')
                self.assertEqual(t, s)
                self.assertEqual(s, DESCRIBE)

    def test_edge_node_20_22_24_without_the_test_with_a_new_line(self):
        for v in ('20', '22', '24'):
            with self.subTest(node=v):
                t, s = self.tables(f'node-test-tap-node{v}-edge.jsonl', f'node-test-spec-node{v}-edge.jsonl')
                self.assertEqual([row for row in t if row[0] != NEW_LINE], s)
                self.assertEqual(len(t), len(s) + 1)   # exactly one test is missing from `spec`

    def test_multi_node_22_and_24(self):
        """Comparable: Node 22 and 24 print no name of a file for a passed test in TAP either, so the ids are the names alone and agree (also the files that did not load, by their relative names).
        Node 20 has no TAP recording of the multi-file run (the names of its files are absolute: see `SpecFixtures`)."""
        for v in ('22', '24'):
            with self.subTest(node=v):
                t, s = self.tables(f'node-test-tap-node{v}-multi.jsonl', f'node-test-spec-node{v}-multi.jsonl')
                self.assertEqual(t, s)

    def test_the_messages_may_differ_but_the_failures_are_the_same_set(self):
        for v in ('22', '24'):
            tap = replay_fixture('node-test', f'node-test-tap-node{v}-describe.jsonl')[0]
            spec = replay_fixture('node-test', f'node-test-spec-node{v}-describe.jsonl')[0]
            self.assertEqual({c[1] for c in tap.emitted() if c[3] == 'failed'}, {c[1] for c in spec.emitted() if c[3] == 'failed'})
            self.assertTrue(msg_of(tap, 'math::fails') and not msg_of(spec, 'math::fails'))   # Node 22 and 24 `spec` has no message


class K13(unittest.TestCase):
    """K13: `node --test` chooses the same adapter whichever format the Node version prints; the stream decides."""

    def pick(self, argv):
        return test_adapters.resolve('auto', argv, test_run.detect_tool(argv))

    def test_node_22_tap_and_node_24_spec_are_read_by_the_same_adapter_chosen_by_auto(self):
        argv = ['node', '--test', 'nt.test.js']
        results = {}
        for label, name in (('node22 TAP', 'node-test-tap-node22-describe.jsonl'), ('node24 spec', 'node-test-spec-node24-describe.jsonl')):
            adapter = self.pick(argv)
            self.assertIsInstance(adapter, test_adapters.REGISTRY['node-test'], label)
            results[label] = replay('node-test', load(name)[1], argv=argv, tool='node', exit_code=1, adapter=adapter)
        self.assertEqual(table(results['node22 TAP'][0]), table(results['node24 spec'][0]))
        self.assertEqual(table(results['node22 TAP'][0]), DESCRIBE)
        self.assertTrue(results['node22 TAP'][3] and results['node24 spec'][3])
        self.assertIs(results['node22 TAP'][1].explicit, False)

    def test_auto_with_each_reporter_choice(self):
        for argv in (['node', '--test'], ['node', '--test', '--test-reporter=tap'], ['node', '--test', '--test-reporter=spec'], ['timeout', '60', 'node', '--test', 'x.test.js']):
            with self.subTest(argv=argv):
                self.assertIsInstance(self.pick(argv), test_adapters.REGISTRY['node-test'])

    def test_the_stream_of_another_reporter_under_auto_emits_nothing(self):
        argv = ['node', '--test', '--test-reporter=tap']   # chosen by the command, but the stream is dots
        adapter = self.pick(argv)
        self.assertIsNotNone(adapter)
        rec, _, _, complete = replay('node-test', load('node-test-dot-unknown.jsonl')[1], argv=argv, tool='node', exit_code=1, adapter=adapter)
        self.assertEqual(rec.calls, [])
        self.assertFalse(complete)


if __name__ == '__main__':
    unittest.main()
