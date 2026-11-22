"""CORE-1140-T3 S1: the adapter `lines` (`--format lines`: one line `PASS name` / `FAIL name` per check, e.g. a shell `*_checks.sh`).

Red-first: `skillsync_workflow/test_adapters/lines.py` does not exist yet, so these tests fail on the missing adapter (`unknown --format 'lines'` / `KeyError`).
Expectations come from the contract (section "Gramatyka (kontrakt)", Q1-Q3, K1-K5 of `docs/specs/2026-11-22-test-run-format-lines.md`); all streams are synthetic."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay, chunks_from_text  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402
from skillsync_workflow import test_events as ev  # noqa: E402

NO_NAME = '(bez nazwy)'
MAX_LINE = 2000
MAX_SEEN = 50000


def setUpModule():
    test_adapters.discover()


def L(*lines):
    return '\n'.join(lines) + '\n'


def run(text, exit_code=0, size=None, fd='out'):
    """The text through `--format lines` (resolve by name, so `explicit=True`)."""
    chunks = chunks_from_text(text, size or max(len(text), 1), fd=fd)
    return replay('lines', chunks, argv=['bash', 'checks.sh'], tool='bash', exit_code=exit_code)


def table(recorder):
    """`[(id, state)]` in the order of the first emission, the last state of each id."""
    final = {}
    for c in recorder.emitted():
        final[c[1]] = c[3]
    return list(final.items())


def msg_of(recorder, test_id):
    found = [c[6] for c in recorder.emitted() if c[1] == test_id and c[6]]
    return found[-1] if found else None


def live(lines):
    """Feeds line by line; returns `(recorder, adapter, ctx, sizes)` where `sizes[i]` = number of test emits right after line `i`."""
    recorder = ar.Recorder()
    adapter = test_adapters.resolve('lines', ['bash', 'checks.sh'], 'bash')
    ctx = test_adapters.AdapterContext(['bash', 'checks.sh'], ar.ROOT, ar.ROOT, recorder)
    adapter.begin(ctx)
    sizes = []
    for line in lines:
        adapter.feed('out', line, ctx)
        sizes.append(len(recorder.emitted()))
    return recorder, adapter, ctx, sizes


def run_masked(text, exit_code=1):
    """The text through the REAL event writer with `tests.mask` on; returns the `test` events read back from the file."""
    with tempfile.TemporaryDirectory(prefix='lines-adapter-') as tmp:
        root = Path(tmp)
        path, _ = ev.new_attempt(root, 'R1', 'S1')
        writer = ev.EventWriter(path, mask=True)
        adapter = test_adapters.resolve('lines', ['bash', 'checks.sh'], 'bash')
        ctx = test_adapters.AdapterContext(['bash', 'checks.sh'], root, root, writer)
        adapter.begin(ctx)
        for line in text.split('\n'):
            adapter.feed('out', line.rstrip('\r'), ctx)
        adapter.end(ctx, exit_code, text, [])
        writer.close()
        return [e for e in ev.read_events(path)[0] if e.get('t') == 'test']


class Registration(unittest.TestCase):
    """K5."""

    def test_lines_is_known_and_resolves_to_an_explicit_instance(self):
        self.assertIn('lines', test_adapters.REGISTRY)
        self.assertIn('lines', test_adapters.names())
        adapter = test_adapters.resolve('lines', ['bash', 'checks.sh'], 'bash')
        self.assertEqual((adapter.name, adapter.live, adapter.tools), ('lines', 'completion', ()))
        self.assertIs(adapter.explicit, True)
        self.assertIs(adapter.explicit_only, True)

    def test_auto_never_picks_lines(self):
        lines_cls = test_adapters.REGISTRY['lines']
        for argv, tool in ((['bash', 'checks.sh'], 'bash'), (['sh', 'x.sh'], 'sh'), (['./checks.sh'], 'unknown'), (['make', 'check'], 'make'), (['lines'], 'lines'), (['npm', 'test'], 'unknown')):
            with self.subTest(argv=argv):
                self.assertNotIsInstance(test_adapters.resolve('auto', argv, tool), lines_cls)

    def test_the_unknown_format_message_lists_lines(self):
        with self.assertRaises(ValueError) as caught:
            test_adapters.resolve('no-such-format', ['x'], 'x')
        self.assertIn('lines', str(caught.exception))


class Grammar(unittest.TestCase):
    """K1: the table of the grammar."""

    def one(self, line):
        return [(c[1], c[2], c[3]) for c in run(L(line))[0].emitted()]

    def test_the_separators_and_the_name(self):
        rows = (('PASS name', 'passed'), ('FAIL name', 'failed'), ('PASS: name', 'passed'), ('FAIL - name', 'failed'), ('PASS – name', 'passed'), ('PASS — name', 'passed'),
                ('FAIL: name', 'failed'), ('PASS\tname', 'passed'), ('FAIL\t- name', 'failed'), ('PASS-name', 'passed'), ('PASS:name', 'passed'), ('FAIL—name', 'failed'),
                ('PASS  two  spaces', 'passed'))
        for line, state in rows:
            with self.subTest(line=line):
                got = self.one(line)
                self.assertEqual(len(got), 1, line)
                self.assertEqual(got[0][2], state)
                self.assertEqual(got[0][0], got[0][1])   # id = name
                self.assertNotIn(got[0][1][:1], ' \t:-–—')   # no separator left in the name

    def test_names_after_every_separator(self):
        self.assertEqual(self.one('PASS name'), [('name', 'name', 'passed')])
        self.assertEqual(self.one('PASS: name'), [('name', 'name', 'passed')])
        self.assertEqual(self.one('FAIL - name'), [('name', 'name', 'failed')])
        self.assertEqual(self.one('PASS – name'), [('name', 'name', 'passed')])
        self.assertEqual(self.one('PASS — name'), [('name', 'name', 'passed')])
        self.assertEqual(self.one('PASS\tname'), [('name', 'name', 'passed')])
        self.assertEqual(self.one('PASS name with  inner - dashes: and colons'), [('name with  inner - dashes: and colons',) * 2 + ('passed',)])

    def test_a_bare_word_has_no_name(self):
        self.assertEqual(self.one('PASS'), [(NO_NAME, NO_NAME, 'passed')])
        self.assertEqual(self.one('FAIL'), [(NO_NAME, NO_NAME, 'failed')])

    def test_not_results(self):
        lines = ('ALL PASS (strict)', 'SOME FAIL (strict)', 'SUMMARY 3 checks', 'PASSED x', 'FAILED x', 'Pass x', 'pass: x', 'fail x', 'Fail x', '  PASS x', '\tFAIL x', ' FAIL',
                 'xPASS x', 'PASS' + 'x', 'PASSnazwa', 'FAILnazwa', 'result: PASS x', 'we PASS x', 'x FAIL x', '', '   ', 'PASS-', 'FAIL:', 'PASS - ', 'some log')
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(run(L(line))[0].emitted(), [], line)

    def test_a_summary_among_results_is_not_counted(self):
        rec, adapter, ctx, complete = run(L('PASS a', 'FAIL b', 'SUMMARY 2 checks', 'SOME FAIL (strict)'), exit_code=1)
        self.assertEqual(table(rec), [('a', 'passed'), ('b', 'failed')])
        self.assertTrue(complete)

    def test_case_is_significant(self):
        self.assertEqual(run(L('Pass x', 'pass x', 'PASS x', 'Fail x', 'fail y'))[0].emitted()[0][1], 'x')
        self.assertEqual(len(run(L('Pass x', 'pass x', 'Fail x', 'fail y'))[0].emitted()), 0)

    def test_stderr_is_not_read(self):
        self.assertEqual(run(L('PASS a', 'FAIL b'), fd='err')[0].emitted(), [])
        chunks = chunks_from_text(L('PASS mine'), 1000) + chunks_from_text(L('PASS from stderr', 'FAIL from stderr'), 1000, fd='err')
        rec = replay('lines', chunks, argv=['bash', 'x.sh'], tool='bash')[0]
        self.assertEqual(table(rec), [('mine', 'passed')])


class Counters(unittest.TestCase):
    """K2: through the recorder stand-in."""

    def test_only_pass(self):
        rec, adapter, ctx, complete = run(L('PASS a', 'PASS b', 'PASS c', 'ALL PASS (strict)'))
        self.assertEqual(table(rec), [('a', 'passed'), ('b', 'passed'), ('c', 'passed')])
        self.assertEqual(len(rec.emitted()), 3)   # a passed check is emitted once
        self.assertEqual(sorted(set(s for s, _ in rec.last().values())), ['passed'])
        self.assertTrue(complete)

    def test_fail_and_pass(self):
        rec, adapter, ctx, complete = run(L('PASS a', 'FAIL b', 'PASS c', 'SOME FAIL (strict)'), exit_code=1)
        last = rec.last()
        self.assertEqual({k: v[0] for k, v in last.items()}, {'a': 'passed', 'b': 'failed', 'c': 'passed'})
        self.assertEqual(sum(1 for s, _ in last.values() if s == 'passed'), 2)
        self.assertEqual(sum(1 for s, _ in last.values() if s == 'failed'), 1)
        self.assertTrue(complete)

    def test_no_results_emits_nothing_and_end_is_false(self):
        for text in ('', L('some output', 'ALL PASS (strict)', 'SUMMARY 0'), L('Pass a', '  PASS b')):
            with self.subTest(text=text):
                rec, adapter, ctx, complete = run(text, exit_code=0)
                self.assertEqual(rec.calls, [])   # nothing at all: not a test, not counts, not collected
                self.assertIs(complete, False)

    def test_results_make_end_true_for_any_exit_code(self):
        for exit_code in (0, 1, 2):
            with self.subTest(exit_code=exit_code):
                self.assertIs(run(L('PASS a'), exit_code=exit_code)[3], True)
        self.assertIs(run(L('FAIL a'), exit_code=0)[3], True)   # exit 0 with a FAIL line: the recorder decides, the adapter reports what it read

    def test_the_adapter_invents_no_test_for_a_failed_process(self):
        rec = run(L('PASS a', 'PASS b'), exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'passed'), ('b', 'passed')])
        self.assertNotIn('failed', [c[3] for c in rec.emitted()])

    def test_the_adapter_never_reports_a_running_state(self):
        self.assertNotIn('running', [c[3] for c in run(L('PASS a', 'FAIL b', 'detail'))[0].emitted()])

    def test_a_result_is_emitted_at_once(self):
        lines = ['PASS a', 'noise', 'PASS b', 'FAIL c']
        rec, adapter, ctx, sizes = live(lines)
        self.assertEqual(sizes, [1, 1, 2, 3])   # `FAIL c` at once, its message (none yet) later

    def test_the_same_result_for_any_cut_of_the_stream(self):
        text = L('PASS a', 'FAIL b', '  detail one', 'PASS: c', 'SUMMARY x')
        whole = run(text, exit_code=1)
        for size in (1, 3, 7):
            with self.subTest(size=size):
                cut = run(text, exit_code=1, size=size)
                self.assertEqual(cut[0].calls, whole[0].calls)
                self.assertEqual(cut[3], whole[3])

    def test_an_unfinished_tail_alone_is_not_a_result(self):
        recorder, adapter, ctx, sizes = live(['PASS before'])
        adapter.partial('out', 'PASS not finished yet', ctx)
        self.assertEqual(len(recorder.emitted()), 1)


class Duplicates(unittest.TestCase):
    """K2/K3: id = name, a duplicate gets ` (2)`, ` (3)` in the id and in the name."""

    def test_suffixes_in_order_of_arrival(self):
        rec = run(L('PASS dup', 'PASS dup', 'PASS other', 'FAIL dup', 'PASS: dup'), exit_code=1)[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted() if c[3] != 'failed' or c[6] is None],
                         [('dup', 'dup', 'passed'), ('dup (2)', 'dup (2)', 'passed'), ('other', 'other', 'passed'), ('dup (3)', 'dup (3)', 'failed'), ('dup (4)', 'dup (4)', 'passed')])

    def test_the_bare_word_is_a_name_too(self):
        rec = run(L('PASS', 'PASS', 'FAIL'), exit_code=1)[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted()],
                         [(NO_NAME, NO_NAME, 'passed'), (f'{NO_NAME} (2)', f'{NO_NAME} (2)', 'passed'), (f'{NO_NAME} (3)', f'{NO_NAME} (3)', 'failed')])

    def test_the_message_of_a_failed_duplicate_goes_to_its_own_id(self):
        rec = run(L('FAIL dup', 'first reason', 'FAIL dup', 'second reason'), exit_code=1)[0]
        self.assertEqual((msg_of(rec, 'dup'), msg_of(rec, 'dup (2)')), ('first reason', 'second reason'))

    def test_no_groups(self):
        rec = run(L('PASS a', 'FAIL b'), exit_code=1)[0]
        self.assertEqual([c[4] for c in rec.emitted()], [None, None])


class Robustness(unittest.TestCase):
    """K3."""

    def test_crlf(self):
        text = 'PASS a\r\nFAIL b\r\n  detail\r\nPASS\r\nALL PASS (x)\r\n'
        rec = run(text, exit_code=1)[0]
        self.assertEqual([(c[1], c[3]) for c in rec.emitted() if c[6] is None], [('a', 'passed'), ('b', 'failed'), (NO_NAME, 'passed')])
        self.assertEqual(msg_of(rec, 'b'), 'detail')
        self.assertTrue(all('\r' not in c[1] and '\r' not in c[2] for c in rec.emitted()))

    def test_a_carriage_return_reaching_the_adapter_is_stripped(self):
        recorder, adapter, ctx, sizes = live(['PASS a\r', 'FAIL\r', 'PASS c \r', 'FAIL b  \t'])
        self.assertEqual([(c[1], c[3]) for c in recorder.emitted()], [('a', 'passed'), (NO_NAME, 'failed'), ('c', 'passed'), ('b', 'failed')])

    def test_ansi_around_the_words(self):
        rec = run(L('\x1b[32mPASS\x1b[0m green', '\x1b[1;31mFAIL\x1b[0m \x1b[31mred\x1b[39m\x1b[0m', '\x1b[32mPASS\x1b[0m', '\x1b[2K\x1b[32mPASS: tail\x1b[0m'), exit_code=1)[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted()], [('green', 'green', 'passed'), ('red', 'red', 'failed'), (NO_NAME, NO_NAME, 'passed'), ('tail', 'tail', 'passed')])

    def test_separator_only_names_are_not_results_of_any_length(self):
        rec = run(L('FAIL a', 'PASS ---', 'FAIL ::', 'PASS ——', 'PASS - – —', 'PASS b'), exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'failed'), ('b', 'passed')])
        self.assertEqual(msg_of(rec, 'a'), 'PASS ---\nFAIL ::\nPASS ——\nPASS - – —')

    def test_character_set_and_osc_sequences_are_stripped(self):
        rows = ('\x1b[32mPASS\x1b(B\x1b[m nazwa', '\x1b]0;title\x07PASS nazwa', '\x1b]8;;http://x\x1b\\PASS nazwa', 'PASS\x1b)0 nazwa')
        for line in rows:
            with self.subTest(line=line):
                self.assertEqual([(c[1], c[3]) for c in run(L(line))[0].emitted()], [('nazwa', 'passed')])

    def test_an_id_of_exactly_max_name_is_kept_whole(self):
        name = 'm' * 300
        recorder = live([f'PASS {name}', f'PASS {name}'])[0]
        ids = [c[1] for c in recorder.emitted()]
        self.assertEqual(ids, [name, name + ' (2)'])

    def test_a_name_that_looks_like_a_suffix_may_collide_known_behaviour(self):
        # Known: the id is the name plus ` (n)`; a literal `dup (2)` among duplicates of `dup` shares an id with the second `dup`.
        # The counters still count every result line (3 emits, `end` True); only the id of the table may repeat.
        recorder, adapter, ctx, sizes = live(['PASS dup', 'PASS dup (2)', 'PASS dup'])
        self.assertEqual(len(recorder.emitted()), 3)
        self.assertEqual(adapter.results, 3)
        self.assertTrue(adapter.end(ctx, 0, '', []))

    def test_a_line_over_the_limit_is_skipped(self):
        at_limit = 'PASS ' + 'y' * (MAX_LINE - 5)
        over = 'PASS ' + 'x' * (MAX_LINE + 1 - 5)
        self.assertEqual((len(at_limit), len(over)), (MAX_LINE, MAX_LINE + 1))
        rec = run(L(over, at_limit, 'PASS after'))[0]
        self.assertEqual(len(rec.emitted()), 2)
        self.assertEqual(rec.emitted()[1][1], 'after')
        self.assertTrue(rec.emitted()[0][3] == 'passed')

    def test_a_very_long_line_does_not_become_a_detail_of_a_failure(self):
        rec = run(L('FAIL a', 'z' * (MAX_LINE + 50), 'short detail'), exit_code=1)[0]
        self.assertNotIn('zzzz', msg_of(rec, 'a') or '')
        self.assertIn('short detail', msg_of(rec, 'a') or '')

    def test_a_very_long_name_is_cut_in_the_id_and_duplicates_stay_distinct(self):
        name = 'n' * 1500
        recorder, adapter, ctx, sizes = live([f'PASS {name}', f'PASS {name}', f'PASS {name}x'])
        ids = [c[1] for c in recorder.emitted()]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)   # the counters never lose a check
        self.assertTrue(all(len(i) <= 300 + len(' (3)') + 1 for i in ids), [len(i) for i in ids])
        self.assertTrue(ids[0].startswith('n' * 100))

    def test_more_than_max_seen_unique_names_do_not_raise_and_still_count(self):
        recorder = ar.Recorder()
        adapter = test_adapters.resolve('lines', ['bash', 'x.sh'], 'bash')
        ctx = test_adapters.AdapterContext(['bash', 'x.sh'], ar.ROOT, ar.ROOT, recorder)
        adapter.begin(ctx)
        total = MAX_SEEN + 25
        for i in range(total):
            adapter.feed('out', f'PASS n{i}', ctx)
        self.assertEqual(len(recorder.emitted()), total)   # past the memory limit a check is still a check
        self.assertTrue(adapter.end(ctx, 0, '', []))
        for value in vars(adapter).values():
            if isinstance(value, dict):
                self.assertLessEqual(len(value), MAX_SEEN + 1)   # the memory of names does not grow without a bound

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(Path(test_adapters.__file__).parent / 'lines.py'), [])


class FailureMessage(unittest.TestCase):
    """K4 (Q2): the text after `FAIL` up to the next result line."""

    def test_the_failure_is_emitted_at_once_and_again_with_the_message(self):
        lines = ['PASS a', 'FAIL b', '  expected 1', '  got 2', 'PASS c']
        rec, adapter, ctx, sizes = live(lines)
        self.assertEqual(sizes[1], 2)   # `FAIL b` is out at once (the first emit is bare)
        first = [c for c in rec.emitted() if c[1] == 'b']
        self.assertEqual([c[3] for c in first], ['failed', 'failed'])
        self.assertIsNone(first[0][6])
        self.assertIn('expected 1', first[1][6])
        self.assertIn('got 2', first[1][6])
        self.assertEqual(sizes[2], 2)   # the detail lines emit nothing by themselves
        self.assertEqual(sizes[3], 2)
        self.assertEqual(sizes[4], 4)   # the block ended at `PASS c`: the message (a second emit of `b`) and `c`
        self.assertEqual([c[1] for c in rec.emitted()], ['a', 'b', 'b', 'c'])

    def test_the_message_comes_before_the_next_result(self):
        rec = live(['FAIL a', 'why', 'PASS b'])[0]
        self.assertEqual([(c[1], c[6]) for c in rec.emitted()], [('a', None), ('a', 'why'), ('b', None)])

    def test_the_block_ends_at_the_next_fail_too(self):
        rec = run(L('FAIL a', 'because a', 'FAIL b', 'because b'), exit_code=1)[0]
        self.assertEqual((msg_of(rec, 'a'), msg_of(rec, 'b')), ('because a', 'because b'))

    def test_a_block_ending_with_the_stream(self):
        rec = run(L('PASS a', 'FAIL b', 'tail one', 'tail two'), exit_code=1)[0]
        self.assertIn('tail one', msg_of(rec, 'b'))
        self.assertIn('tail two', msg_of(rec, 'b'))

    def test_without_detail_there_is_no_second_emit(self):
        rec = run(L('FAIL a', '', '   ', 'PASS b'), exit_code=1)[0]
        self.assertEqual(rec.states('a'), ['failed'])
        self.assertIsNone(msg_of(rec, 'a'))

    def test_empty_lines_are_skipped_indented_or_not_all_count(self):
        rec = run(L('FAIL a', 'first', '', '    indented second', '', '\tthird', 'PASS b'), exit_code=1)[0]
        msg = msg_of(rec, 'a')
        for part in ('first', 'indented second', 'third'):
            self.assertIn(part, msg)
        self.assertNotIn('\n\n', msg)

    def test_a_summary_or_other_line_is_part_of_the_block_but_a_result_ends_it(self):
        rec = run(L('FAIL a', 'ALL PASS (not a result)', 'PASS: b', 'after'), exit_code=1)[0]
        self.assertIn('ALL PASS (not a result)', msg_of(rec, 'a'))
        self.assertNotIn('PASS: b', msg_of(rec, 'a'))
        self.assertNotIn('after', msg_of(rec, 'a'))   # `after` belongs to nobody: `b` passed

    def test_at_most_ten_non_empty_lines(self):
        lines = ['FAIL a'] + [f'detail-{i:02d}' for i in range(1, 16)] + ['PASS b']
        msg = msg_of(run(L(*lines), exit_code=1)[0], 'a')
        for i in range(1, 11):
            self.assertIn(f'detail-{i:02d}', msg)
        for i in range(11, 16):
            self.assertNotIn(f'detail-{i:02d}', msg)

    def test_empty_lines_do_not_use_up_the_ten(self):
        lines = ['FAIL a'] + [x for i in range(1, 11) for x in (f'detail-{i:02d}', '')] + ['detail-11', 'PASS b']
        msg = msg_of(run(L(*lines), exit_code=1)[0], 'a')
        self.assertIn('detail-10', msg)
        self.assertNotIn('detail-11', msg)

    def test_at_most_500_characters(self):
        lines = ['FAIL a'] + [('line%d ' % i) + 'x' * 190 for i in range(1, 6)] + ['PASS b']
        msg = msg_of(run(L(*lines), exit_code=1)[0], 'a')
        self.assertLessEqual(len(msg.replace('\n', '')), 500)   # separators between the lines are not counted against the limit
        self.assertIn('line1 ', msg)
        self.assertNotIn('line4 ', msg)
        self.assertNotIn('line5 ', msg)

    def test_exactly_500_characters_fit_and_the_sixth_line_does_not(self):
        lines = ['FAIL a'] + [f'{i}' * 100 for i in range(1, 7)] + ['PASS b']
        msg = msg_of(run(L(*lines), exit_code=1)[0], 'a')
        self.assertEqual(msg.split('\n'), [f'{i}' * 100 for i in range(1, 6)])

    def test_a_single_long_detail_line_is_cut_to_the_limit(self):
        rec = run(L('FAIL a', 'q' * 700), exit_code=1)[0]
        msg = msg_of(rec, 'a')
        self.assertTrue(msg)
        self.assertLessEqual(len(msg), 500)
        self.assertTrue(msg.startswith('q' * 100))
        self.assertEqual(rec.states('a'), ['failed', 'failed'])

    def test_a_long_line_after_short_ones_is_cut_to_the_remaining_limit(self):
        rec = run(L('FAIL a', 'short', 'r' * 900, 'later', 'PASS b'), exit_code=1)[0]
        msg = msg_of(rec, 'a')
        self.assertTrue(msg.startswith('short\nrrrr'))
        self.assertLessEqual(len(msg.replace('\n', '')), 500)
        self.assertNotIn('later', msg)

    def test_a_pass_has_no_message(self):
        rec = run(L('PASS a', 'text after a pass', 'more text', 'PASS b'))[0]
        self.assertEqual([c[6] for c in rec.emitted()], [None, None])
        self.assertEqual(rec.states('a'), ['passed'])

    def test_text_after_a_pass_does_not_go_to_an_earlier_failure(self):
        rec = run(L('FAIL a', 'belongs to a', 'PASS b', 'belongs to nobody'), exit_code=1)[0]
        self.assertEqual(msg_of(rec, 'a'), 'belongs to a')
        self.assertEqual(rec.states('b'), ['passed'])

    def test_the_message_goes_through_the_mask_of_the_recorder(self):
        token = 'gh' + 'p_' + 'A1b2C3d4' * 4   # assembled at run time: no literal token in the source
        text = L('PASS a', 'FAIL b', f'  curl failed with Authorization: Bearer {token}', f'  password={token}', 'PASS c')
        events = run_masked(text)
        failed = [e for e in events if e['id'] == 'b' and e.get('msg')]
        self.assertEqual(len(failed), 1)
        self.assertNotIn(token, failed[0]['msg'])
        self.assertTrue(failed[0]['msg'].strip())
        self.assertNotIn(token, repr(events))
        self.assertEqual([e['state'] for e in events if e['id'] == 'b'], ['failed', 'failed'])

    def test_the_mask_is_the_recorders_not_the_adapters(self):
        token = 'gh' + 'p_' + 'A1b2C3d4' * 4
        recorder = run(L('FAIL b', f'  token {token}'), exit_code=1)[0]
        self.assertIn(token, msg_of(recorder, 'b'))   # the stand-in does not mask: the adapter passes the raw text on


if __name__ == '__main__':
    unittest.main()
