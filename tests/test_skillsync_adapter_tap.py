"""CORE-TAP F1.1: the adapter `tap` (a TAP parser chosen with `--format tap`, section 4 of the spec `docs/specs/2026-11-18-core-tap.md`).

Red-first: `skillsync_workflow/test_adapters/tap.py` does not exist yet, so these tests fail on the missing adapter (`unknown --format 'tap'` / `KeyError`).
The expectations come from the contract (result lines, directives, names, groups, leaves emitted at once, YAML, plan and `end`) and from recordings of Node 20.18.1,
22.22.0 and 24.11.1 (`tests/fixtures/test-runs/tap-*.jsonl`); three fixtures are hand written (`"synthetic": true`)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay, replay_fixture, load, chunks_from_text, stream_text  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'tap.py'
HEAD = 'TAP version 13'


def setUpModule():
    test_adapters.discover()


def L(*lines):
    return '\n'.join(lines) + '\n'


def run(text, exit_code=0, explicit=True, size=None, fd='out'):
    """The text through the adapter: `explicit=True` is `--format tap` (resolve), `False` an instance as `auto` (through `node-test`) would use it."""
    chunks = chunks_from_text(text, size or max(len(text), 1), fd=fd)
    adapter = None if explicit else test_adapters.REGISTRY['tap']()
    return replay('tap', chunks, argv=['node', 'x.js'], tool='node', exit_code=exit_code, adapter=adapter)


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


def live(lines, explicit=True):
    """Feeds line by line; returns `(recorder, adapter, ctx, sizes)` where `sizes[i]` is the number of test emits right after line `i`."""
    recorder = ar.Recorder()
    adapter = test_adapters.resolve('tap', ['node', 'x.js'], 'node') if explicit else test_adapters.REGISTRY['tap']()
    ctx = test_adapters.AdapterContext(['node', 'x.js'], ar.ROOT, ar.ROOT, recorder)
    adapter.begin(ctx)
    sizes = []
    for line in lines:
        adapter.feed('out', line, ctx)
        sizes.append(len(recorder.emitted()))
    return recorder, adapter, ctx, sizes


class Registration(unittest.TestCase):
    def test_registry_and_resolve(self):
        self.assertIn('tap', test_adapters.REGISTRY)
        self.assertIn('tap', test_adapters.names())
        adapter = test_adapters.resolve('tap', ['node', 'x.js'], 'node')
        self.assertEqual((adapter.name, adapter.live, adapter.tools), ('tap', 'completion', ()))
        self.assertIs(adapter.explicit, True)   # `--format tap` marks the instance
        self.assertIs(adapter.explicit_only, True)
        self.assertEqual(adapter.env(), {})

    def test_the_base_class_defaults_and_an_instance_made_by_hand(self):
        self.assertIs(test_adapters.Adapter.explicit, False)
        self.assertIs(test_adapters.Adapter.explicit_only, False)
        self.assertIs(test_adapters.REGISTRY['tap']().explicit, False)   # only `resolve(<name>)` sets it

    def test_another_adapter_resolved_by_name_is_marked_too_and_auto_never_picks_tap(self):
        self.assertIs(test_adapters.resolve('vitest', ['vitest', 'run'], 'vitest').explicit, True)
        for argv, tool in ((['node', 'x.test.js'], 'node'), (['node', '--test', 'x.test.js'], 'node'), (['bats', '--tap', 't.bats'], 'bats'), (['prove', '-v'], 'prove'), (['npm', 'test'], 'unknown')):
            with self.subTest(argv=argv):
                self.assertNotIsInstance(test_adapters.resolve('auto', argv, tool), test_adapters.REGISTRY['tap'])

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class ResultLines(unittest.TestCase):
    def test_with_and_without_a_number_and_the_description(self):
        text = L(HEAD, 'ok 1 - numbered', 'ok - unnumbered', 'ok 3 after a number', 'ok bare words', 'not ok 5 - broken', 'ok 6 - a - b - c')
        rec = run(text)[0]
        self.assertEqual(table(rec), [('numbered', 'passed', None), ('unnumbered', 'passed', None), ('after a number', 'passed', None), ('bare words', 'passed', None),
                                      ('broken', 'failed', None), ('a - b - c', 'passed', None)])
        self.assertEqual(name_of(rec, 'a - b - c'), 'a - b - c')

    def test_an_empty_description_is_a_name(self):
        rec = run(L(HEAD, 'ok 1', 'ok', 'not ok 3 -', 'ok 4 - '))[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted()],
                         [('(bez nazwy)', '(bez nazwy)', 'passed'), ('(bez nazwy) (2)', '(bez nazwy) (2)', 'passed'), ('(bez nazwy) (3)', '(bez nazwy) (3)', 'failed'),
                          ('(bez nazwy) (4)', '(bez nazwy) (4)', 'passed')])

    def test_the_word_boundary_after_ok(self):
        rec = run(L(HEAD, 'okay 1 - no', 'ok-x', 'ok_y 1 - no', 'not okay 2 - no', 'not ok-x', 'oks - no', 'ok', 'ok 2 - yes', 'not ok', 'not ok 4 - yes too'))[0]
        self.assertEqual([c[3] for c in rec.emitted()], ['passed', 'passed', 'failed', 'failed'])
        self.assertNotIn('no', [c[2] for c in rec.emitted()])

    def test_comments_logs_and_other_lines_are_not_results(self):
        text = L(HEAD, '# ok - a log written by a test', '# not ok 3 - another', 'log: ok 1 - inside a line', '', 'some words', '#ok 1 - no space', '# tests 3', '# pass 3',
                 'ok 1 - real')
        self.assertEqual(table(run(text)[0]), [('real', 'passed', None)])

    def test_stderr_is_ignored(self):
        rec = run(L(HEAD, 'ok 1 - a', 'ok 2 - not mine'), fd='err')[0]
        self.assertEqual(rec.emitted(), [])
        chunks = chunks_from_text(L(HEAD, 'ok 1 - mine'), 1000) + chunks_from_text(L('ok 2 - from stderr', 'not ok 3 - from stderr'), 1000, fd='err')
        self.assertEqual(table(replay('tap', chunks, argv=['node', 'x.js'], tool='node')[0]), [('mine', 'passed', None)])

    def test_ansi_is_removed(self):
        rec = run(L(HEAD, '\x1b[32mok 1 - green\x1b[0m', '\x1b[1;31mnot ok 2 - \x1b[31mred\x1b[39m\x1b[0m'))[0]
        self.assertEqual([(c[1], c[2], c[3]) for c in rec.emitted()], [('green', 'green', 'passed'), ('red', 'red', 'failed')])

    def test_a_line_over_the_limit_is_not_a_result(self):
        long_line = 'ok 1 - ' + 'x' * (2001 - 7)
        at_limit = 'ok 2 - ' + 'y' * (2000 - 7)
        self.assertEqual((len(long_line), len(at_limit)), (2001, 2000))
        rec = run(L(HEAD, long_line, at_limit, 'ok 3 - after'))[0]
        self.assertEqual([c[3] for c in rec.emitted()], ['passed', 'passed'])
        self.assertEqual(ids(rec)[-1], 'after')
        self.assertEqual(ids(rec)[0], 'y' * (2000 - 7))   # the line of exactly 2000 characters is still a result


class Directives(unittest.TestCase):
    def one(self, line):
        rec = run(L(HEAD, line))[0]
        self.assertEqual(len(rec.emitted()), 1, line)
        c = rec.emitted()[0]
        return c[1], c[2], c[3], c[6]

    def test_skip_in_every_spelling(self):
        for line in ('ok 1 - a # SKIP not now', 'ok 1 - a # skip not now', 'ok 1 - a # Skip not now', 'ok 1 - a # SKIPPED not now', 'ok 1 - a # skipped not now'):
            with self.subTest(line=line):
                test_id, name, state, msg = self.one(line)
                self.assertEqual((test_id, name, state), ('a', 'a', 'skipped'))
                self.assertIn('not now', msg)

    def test_skip_without_a_reason(self):
        self.assertEqual(self.one('ok 3 - skip bare # SKIP')[:3], ('skip bare', 'skip bare', 'skipped'))
        self.assertEqual(self.one('not ok 3 - skip bare # SKIP')[:3], ('skip bare', 'skip bare', 'skipped'))

    def test_todo_is_skipped_for_ok_and_not_ok_and_the_note_says_todo(self):
        for line in ('ok 1 - a # TODO later', 'not ok 1 - a # TODO later', 'ok 1 - a # todo later', 'not ok 1 - a # TODO', 'not ok 1 - a # Todo fix it'):
            with self.subTest(line=line):
                test_id, name, state, msg = self.one(line)
                self.assertEqual((test_id, name, state), ('a', 'a', 'skipped'))
                self.assertIn('todo', msg.lower())

    def test_other_text_after_a_hash_belongs_to_the_name(self):
        for line, name, state in (('ok 1 - a # just a note', 'a # just a note', 'passed'), ('not ok 1 - a # because', 'a # because', 'failed'),
                                  ('ok 1 - a # SKIPPER', 'a # SKIPPER', 'passed'), ('ok 1 - a # SKIPPING it', 'a # SKIPPING it', 'passed'),
                                  ('ok 1 - a # TODOS', 'a # TODOS', 'passed'), ('ok 1 - a #SKIP', 'a #SKIP', 'passed'), ('ok 1 - a# SKIP', 'a# SKIP', 'passed')):
            with self.subTest(line=line):
                test_id, got, st, msg = self.one(line)
                self.assertEqual((got, st), (name, state))
                self.assertEqual(test_id, name)

    def test_only_the_first_unescaped_hash_decides(self):
        self.assertEqual(self.one('ok 1 - a # note # SKIP why')[1:3], ('a # note # SKIP why', 'passed'))
        self.assertEqual(self.one('ok 1 - a # SKIP why # TODO later')[1:3], ('a', 'skipped'))

    def test_an_escaped_hash_is_not_a_directive(self):
        self.assertEqual(self.one('ok 1 - a \\# SKIP b')[1:3], ('a # SKIP b', 'passed'))
        self.assertEqual(self.one('not ok 1 - a \\# TODO b')[1:3], ('a # TODO b', 'failed'))
        self.assertEqual(self.one('ok 1 - a \\# b # SKIP why')[1:3], ('a # b', 'skipped'))

    def test_a_directive_after_an_escaped_backslash_still_counts(self):
        self.assertEqual(self.one('ok 1 - dir\\\\ # SKIP why')[1:3], ('dir\\', 'skipped'))


class Names(unittest.TestCase):
    def name(self, raw):
        rec = run(L(HEAD, 'ok 1 - ' + raw))[0]
        return rec.emitted()[0][2]

    def test_one_left_to_right_pass(self):
        self.assertEqual(self.name('name with \\# hash'), 'name with # hash')
        self.assertEqual(self.name('name with \\\\ backslash and - dash'), 'name with \\ backslash and - dash')
        self.assertEqual(self.name('multi\\\\nline name'), 'multi\\nline name')   # backslash + `n`, never a space or a new line
        self.assertEqual(self.name('unknown \\x \\n escape'), 'unknown \\x \\n escape')   # `\x` stays as it is
        self.assertEqual(self.name('trailing \\'), 'trailing \\')

    def test_sequential_replaces_would_give_a_wrong_name(self):
        raw = 'x\\\\#y'   # an escaped backslash, then a literal `#` (no space around it: no directive)
        wanted = 'x\\#y'
        self.assertEqual(self.name(raw), wanted)
        self.assertNotEqual(raw.replace('\\\\', '\\').replace('\\#', '#'), wanted)   # `\\` first: the backslash is eaten twice, the result is `x#y`

    def test_the_name_in_a_subtest_line_is_decoded_the_same_way(self):
        text = L(HEAD, '# Subtest: with \\# and \\\\', '    # Subtest: inner', '    ok 1 - inner', 'ok 1 - with \\# and \\\\')
        rec = run(text)[0]
        self.assertEqual(table(rec), [('with # and \\::inner', 'passed', 'with # and \\')])


class Groups(unittest.TestCase):
    CHILD = L(HEAD, '# Subtest: g', '    # Subtest: a', '    ok 1 - a', '    # Subtest: b', '    ok 2 - b # SKIP no', '    1..2', 'ok 1 - g', '1..1')

    def test_leaves_get_the_group_and_the_id_of_the_path(self):
        rec = run(self.CHILD)[0]
        self.assertEqual(table(rec), [('g::a', 'passed', 'g'), ('g::b', 'skipped', 'g')])
        self.assertEqual((name_of(rec, 'g::a'), name_of(rec, 'g::b')), ('a', 'b'))   # the name does not carry the group

    def test_nested_groups_and_siblings(self):
        text = L(HEAD, '# Subtest: top', '    # Subtest: mid', '        # Subtest: deep', '        ok 1 - deep', '        1..1', '    ok 1 - mid', '    # Subtest: other', '    ok 2 - other',
                 '    1..2', 'ok 1 - top', '# Subtest: next', '    # Subtest: leaf', '    ok 1 - leaf', '    1..1', 'ok 2 - next', '# Subtest: alone', 'ok 3 - alone')
        self.assertEqual(table(run(text)[0]), [('top > mid::deep', 'passed', 'top > mid'), ('top::other', 'passed', 'top'), ('next::leaf', 'passed', 'next'), ('alone', 'passed', None)])

    def test_a_leaf_is_emitted_before_the_lines_of_its_parent(self):
        lines = [HEAD, '# Subtest: parent', '    # Subtest: first', '    ok 1 - first', '      ---', '      duration_ms: 1.5', '      ...', '    # Subtest: second', '    ok 2 - second',
                 '    1..2', 'ok 1 - parent', '  ---', '  duration_ms: 3', '  type: \'suite\'', '  ...', '1..1']
        rec, adapter, ctx, sizes = live(lines)
        self.assertEqual(sizes[lines.index('    ok 1 - first')], 1)   # at once: the parent is still to come
        self.assertEqual(sizes[lines.index('    ok 2 - second')], 2)
        self.assertEqual(sizes[lines.index('ok 1 - parent')], 2)   # `ok` of a parent emits nothing
        self.assertEqual(sizes[-1], 2)
        self.assertEqual(ids(rec), ['parent::first', 'parent::second'])
        self.assertTrue(adapter.end(ctx, 0, '', []))

    def test_a_failed_leaf_is_emitted_before_its_block_ends(self):
        lines = [HEAD, '# Subtest: p', '    # Subtest: bad', '    not ok 1 - bad', '      ---', "      error: 'boom'", '      ...', '    1..1', 'not ok 1 - p']
        rec, adapter, ctx, sizes = live(lines)
        self.assertEqual(sizes[lines.index('    not ok 1 - bad')], 1)
        self.assertEqual([c[3:7] for c in rec.emitted()][0], ('failed', 'p', None, None))   # the first emit has no message yet
        self.assertEqual(msg_of(rec, 'p::bad'), 'boom')

    def test_k3_a_parent_with_a_failed_child_is_not_a_test(self):
        text = L(HEAD, '# Subtest: p', '    # Subtest: good', '    ok 1 - good', '    # Subtest: bad', '    not ok 2 - bad', '      ---', "      error: 'boom'", '      ...', '    1..2',
                 'not ok 1 - p', '  ---', "  failureType: 'subtestsFailed'", "  error: '1 subtest failed'", '  ...', '1..1')
        rec = run(text, exit_code=1)[0]
        self.assertEqual(table(rec), [('p::good', 'passed', 'p'), ('p::bad', 'failed', 'p')])
        self.assertNotIn('p', ids(rec))
        self.assertNotIn('1 subtest failed', [c[6] for c in rec.emitted()])

    def test_k3_a_failed_ancestor_of_a_failed_grandchild_emits_nothing_at_any_level(self):
        text = L(HEAD, '# Subtest: top', '    # Subtest: mid', '        # Subtest: leaf', '        not ok 1 - leaf', '        1..1', '    not ok 1 - mid', '    1..1', 'not ok 1 - top', '1..1')
        rec = run(text, exit_code=1)[0]
        self.assertEqual(table(rec), [('top > mid::leaf', 'failed', 'top > mid')])

    def test_k3_a_failed_parent_without_a_failed_child_is_a_failed_leaf_of_its_own(self):
        text = L(HEAD, '# Subtest: p', '    # Subtest: ok one', '    ok 1 - ok one', '    # Subtest: skipped one', '    ok 2 - skipped one # SKIP x', '    1..2', 'not ok 1 - p', '  ---',
                 "  error: 'assertion after the subtests'", '  ...', '1..1')
        rec = run(text, exit_code=1)[0]
        self.assertEqual(table(rec), [('p::ok one', 'passed', 'p'), ('p::skipped one', 'skipped', 'p'), ('p', 'failed', None)])
        self.assertEqual(msg_of(rec, 'p'), 'assertion after the subtests')

    def test_k3_a_nested_failed_parent_keeps_the_group_of_its_own_level(self):
        text = L(HEAD, '# Subtest: top', '    # Subtest: mid', '        # Subtest: leaf', '        ok 1 - leaf', '        1..1', '    not ok 1 - mid', '    1..1', 'not ok 1 - top', '1..1')
        rec = run(text, exit_code=1)[0]
        # `mid` failed on its own (its child passed): a failed leaf `top::mid`; `top` has a failed descendant now (`mid`), so it adds nothing
        self.assertEqual(table(rec), [('top > mid::leaf', 'passed', 'top > mid'), ('top::mid', 'failed', 'top')])

    def test_a_result_with_no_subtest_line_has_no_group(self):
        rec = run(L(HEAD, '    ok 1 - indented', 'ok 2 - plain'))[0]
        self.assertEqual(table(rec), [('indented', 'passed', None), ('plain', 'passed', None)])

    def test_duplicates_get_a_suffix_in_the_id_and_in_the_name_in_order_of_arrival(self):
        rec = run(L(HEAD, 'ok 1 - dup', 'ok 2 - dup', 'ok 3 - other', 'not ok 4 - dup', '# Subtest: g', '    # Subtest: dup', '    ok 1 - dup', '    # Subtest: dup', '    ok 2 - dup', '    1..2', 'ok 5 - g'))[0]
        self.assertEqual(table(rec), [('dup', 'passed', None), ('dup (2)', 'passed', None), ('other', 'passed', None), ('dup (3)', 'failed', None), ('g::dup', 'passed', 'g'),
                                      ('g::dup (2)', 'passed', 'g')])
        self.assertEqual((name_of(rec, 'dup (2)'), name_of(rec, 'dup (3)'), name_of(rec, 'g::dup (2)')), ('dup (2)', 'dup (3)', 'dup (2)'))

    def test_the_message_of_a_failed_duplicate_goes_to_its_own_id(self):
        text = L(HEAD, 'not ok 1 - dup', '  ---', "  error: 'first'", '  ...', 'not ok 2 - dup', '  ---', "  error: 'second'", '  ...')
        rec = run(text, exit_code=1)[0]
        self.assertEqual((msg_of(rec, 'dup'), msg_of(rec, 'dup (2)')), ('first', 'second'))


class Yaml(unittest.TestCase):
    def failed(self, *block, exit_code=1, **kw):
        return run(L(HEAD, 'not ok 1 - a', *block), exit_code=exit_code, **kw)[0]

    def test_a_failed_test_is_emitted_again_with_the_message_and_the_duration(self):
        rec = self.failed('  ---', '  duration_ms: 12.5', "  error: 'it broke'", '  ...')
        emits = rec.emitted()
        self.assertEqual([c[3] for c in emits], ['failed', 'failed'])
        self.assertEqual((emits[0][5], emits[0][6]), (None, None))   # the first emit is immediate, bare
        self.assertEqual(emits[1][6], 'it broke')
        self.assertAlmostEqual(emits[1][5], 12.5, delta=1)

    def test_error_in_its_three_spellings(self):
        self.assertEqual(msg_of(self.failed('  ---', "  error: 'quoted: with a colon'", '  ...'), 'a'), 'quoted: with a colon')
        self.assertEqual(msg_of(self.failed('  ---', '  error: plain text', '  ...'), 'a'), 'plain text')
        block = self.failed('  ---', '  error: |-', '    ', '    Expected values to be strictly equal:', '    ', '    1 !== 2', '  code: \'ERR_ASSERTION\'', '  ...')
        self.assertEqual(msg_of(block, 'a'), 'Expected values to be strictly equal:')   # the first non-empty line of the block scalar

    def test_message_is_read_too(self):
        self.assertEqual(msg_of(self.failed('  ---', '  message: from another producer', '  ...'), 'a'), 'from another producer')

    def test_a_deeper_key_is_not_a_top_level_key(self):
        rec = self.failed('  ---', '  stack: |-', '    error: not mine', '    duration_ms: 999', "  error: 'mine'", '  duration_ms: 7', '  ...')
        self.assertEqual(msg_of(rec, 'a'), 'mine')
        self.assertAlmostEqual([c[5] for c in rec.emitted() if c[5] is not None][-1], 7, delta=1)

    def test_a_line_that_looks_like_a_result_inside_a_block_scalar_is_not_a_result(self):
        text = L(HEAD, 'not ok 1 - a', '  ---', '  error: |-', '    first line', '    ok 2 - fake', '    not ok 3 - fake too', '    # Subtest: fake', '  code: \'X\'', '  ...', 'ok 2 - real')
        rec = run(text, exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'failed', None), ('real', 'passed', None)])
        self.assertEqual(msg_of(rec, 'a'), 'first line')

    def test_a_passed_or_skipped_test_is_emitted_once_without_duration_or_message(self):
        text = L(HEAD, 'ok 1 - good', '  ---', '  duration_ms: 3.2', "  error: 'a stray key'", '  ...', 'ok 2 - skip # SKIP why', '  ---', '  duration_ms: 1', '  ...')
        rec = run(text)[0]
        self.assertEqual(rec.states('good'), ['passed'])
        self.assertEqual(rec.states('skip'), ['skipped'])
        self.assertEqual([(c[5], c[6]) for c in rec.emitted() if c[1] == 'good'], [(None, None)])
        self.assertNotIn('a stray key', [c[6] for c in rec.emitted()])

    def test_a_block_without_the_end_marker_ends_at_a_line_that_is_not_deeper(self):
        for following in ('ok 2 - next', '# Subtest: next'):
            with self.subTest(following=following):
                lines = [HEAD, 'not ok 1 - a', '  ---', "  error: 'no end marker'", '  duration_ms: 4', following] + (['ok 2 - next'] if following.startswith('#') else [])
                rec, adapter, ctx, sizes = live(lines)
                self.assertEqual(msg_of(rec, 'a'), 'no end marker')
                self.assertEqual(table(rec), [('a', 'failed', None), ('next', 'passed', None)])

    def test_the_message_comes_before_the_next_result(self):
        lines = [HEAD, 'not ok 1 - a', '  ---', "  error: 'late'", 'ok 2 - b']
        rec = live(lines)[0]
        self.assertEqual([(c[1], c[6]) for c in rec.emitted()], [('a', None), ('a', 'late'), ('b', None)])

    def test_a_block_over_the_limit_without_an_end_marker(self):
        lines = [HEAD, 'not ok 1 - a', '  ---', "  error: 'kept'"] + [f'  key{i}: value' for i in range(250)] + ['ok 2 - b', 'not ok 3 - c']
        rec, adapter, ctx, sizes = live(lines)
        self.assertEqual(table(rec), [('a', 'failed', None), ('b', 'passed', None), ('c', 'failed', None)])
        self.assertEqual(msg_of(rec, 'a'), 'kept')

    def test_a_block_under_a_nested_result_and_two_blocks_in_a_row(self):
        text = L(HEAD, '# Subtest: g', '    # Subtest: a', '    not ok 1 - a', '      ---', '      duration_ms: 2', "      error: 'nested boom'", '      ...', '    # Subtest: b', '    not ok 2 - b', '      ---',
                 "      error: 'second boom'", '      ...', '    1..2', 'not ok 1 - g', '  ---', "  error: '2 subtests failed'", '  ...')
        rec = run(text, exit_code=1)[0]
        self.assertEqual((msg_of(rec, 'g::a'), msg_of(rec, 'g::b')), ('nested boom', 'second boom'))
        self.assertNotIn('g', ids(rec))

    def test_a_failed_test_without_a_block_stays_failed(self):
        rec = run(L(HEAD, 'not ok 1 - a', 'ok 2 - b'), exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'failed', None), ('b', 'passed', None)])
        self.assertIsNone(msg_of(rec, 'a'))


class UnloadedFile(unittest.TestCase):
    YAML = ('  ---', '  duration_ms: 25.9', "  type: 'test'", "  failureType: 'testCodeFailure'", '  exitCode: 1', '  signal: ~', "  error: 'test failed'", "  code: 'ERR_TEST_FAILURE'", '  ...')

    def test_the_message_is_the_error_line_of_the_comments_before_it(self):
        text = L(HEAD, 'ok 1 - fine', '# /work/proj/c.test.js:1', '# const x = ;', '#           ^', "# SyntaxError: Unexpected token ';'", '#     at wrapSafe (node:internal/modules/cjs/loader:1638:18)',
                 '# Node.js v22.22.0', '# Subtest: c.test.js', 'not ok 2 - c.test.js', *self.YAML, '1..2')
        rec = run(text, exit_code=1)[0]
        self.assertEqual(table(rec), [('fine', 'passed', None), ('c.test.js', 'failed', None)])
        self.assertEqual(msg_of(rec, 'c.test.js'), "SyntaxError: Unexpected token ';'")

    def test_the_last_matching_comment_wins_and_a_plain_error_name_counts(self):
        text = L(HEAD, '# Error: first', '# TypeError: second', '# Subtest: d.test.js', 'not ok 1 - d.test.js', *self.YAML)
        self.assertEqual(msg_of(run(text, exit_code=1)[0], 'd.test.js'), 'TypeError: second')
        text = L(HEAD, '# /work/proj/d.test.js:1', '# throw new Error(\'file level crash\');', '# ^', '# Error: file level crash', '# Subtest: d.test.js', 'not ok 1 - d.test.js', *self.YAML)
        self.assertEqual(msg_of(run(text, exit_code=1)[0], 'd.test.js'), 'Error: file level crash')

    def test_without_an_error_comment_the_message_is_test_failed(self):
        text = L(HEAD, '# some log', '# ErrorLike but no colon-less match: not an error line', '# Subtest: e.test.js', 'not ok 1 - e.test.js', *self.YAML)
        self.assertEqual(msg_of(run(text, exit_code=1)[0], 'e.test.js'), 'test failed')

    def test_comments_before_the_previous_result_do_not_count(self):
        text = L(HEAD, '# Error: belongs to the past', 'ok 1 - between', '# Subtest: f.test.js', 'not ok 2 - f.test.js', *self.YAML)
        self.assertEqual(msg_of(run(text, exit_code=1)[0], 'f.test.js'), 'test failed')

    def test_an_ordinary_failure_keeps_its_own_message_not_a_comment(self):
        text = L(HEAD, '# Error: a log line of the test', '# Subtest: g', 'not ok 1 - g', '  ---', "  error: 'the real reason'", '  ...')
        self.assertEqual(msg_of(run(text, exit_code=1)[0], 'g'), 'the real reason')


class Modes(unittest.TestCase):
    def test_auto_needs_the_header(self):
        text = L('ok 1 - early', '    ok 2 - early indented', HEAD, 'ok 3 - late')
        self.assertEqual(table(run(text, explicit=False)[0]), [('late', 'passed', None)])
        self.assertEqual(run(L('ok 1 - a', 'not ok 2 - b'), explicit=False)[0].emitted(), [])

    def test_the_header_may_carry_any_version(self):
        for header in ('TAP version 13', 'TAP version 14', 'TAP version 12'):
            with self.subTest(header=header):
                self.assertEqual(table(run(L(header, 'ok 1 - a'), explicit=False)[0]), [('a', 'passed', None)])

    def test_explicit_accepts_unindented_lines_without_a_header(self):
        rec = run(L('ok - adds', 'ok - subtracts', 'some log', 'not ok - breaks'))[0]
        self.assertEqual(table(rec), [('adds', 'passed', None), ('subtracts', 'passed', None), ('breaks', 'failed', None)])

    def test_explicit_ignores_indented_lines_without_a_header(self):
        rec = run(L('    ok 1 - indented', '  not ok 2 - also', '\tok 3 - tab', 'ok 4 - flat'))[0]
        self.assertEqual(table(rec), [('flat', 'passed', None)])

    def test_with_a_header_explicit_mode_reads_indented_lines_too(self):
        rec = run(L(HEAD, '    ok 1 - indented'))[0]
        self.assertEqual(table(rec), [('indented', 'passed', None)])

    def test_bail_out_stops_the_emission(self):
        text = L(HEAD, 'ok 1 - before', 'Bail out! database is down', 'ok 2 - after', 'not ok 3 - after too')
        rec, adapter, ctx, complete = run(text, exit_code=1)
        self.assertEqual(table(rec), [('before', 'passed', None)])
        self.assertFalse(complete)

    def test_bail_out_in_the_middle_of_a_failure_block_does_not_lose_what_was_sent(self):
        rec = run(L(HEAD, 'ok 1 - a', 'not ok 2 - b', 'Bail out! stop'), exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'failed', None)])

    def test_the_adapter_invents_no_test_for_a_failed_process(self):
        rec = run(L('ok - a', 'ok - b'), exit_code=1)[0]
        self.assertEqual(table(rec), [('a', 'passed', None), ('b', 'passed', None)])
        self.assertNotIn('failed', [c[3] for c in rec.emitted()])

    def test_the_adapter_never_reports_a_running_state(self):
        for text in (L(HEAD, 'ok 1 - a', 'not ok 2 - b'), L(HEAD, '# Subtest: g', '    # Subtest: x', '    ok 1 - x', 'ok 1 - g')):
            self.assertNotIn('running', [c[3] for c in run(text)[0].emitted()])


class PlanAndEnd(unittest.TestCase):
    def collected(self, recorder):
        return [c[1] for c in recorder.calls if c[0] == 'collected']

    def test_collected_only_with_a_plan_before_the_first_result_and_no_groups(self):
        self.assertEqual(self.collected(run(L(HEAD, '1..3', 'ok 1 - a', 'ok 2 - b', 'ok 3 - c'))[0]), [3])
        self.assertEqual(self.collected(run(L(HEAD, 'ok 1 - a', 'ok 2 - b', '1..2'))[0]), [])   # Node prints the plan at the end
        self.assertEqual(self.collected(run(L(HEAD, '1..2', '# Subtest: g', '    # Subtest: a', '    ok 1 - a', '    1..1', 'ok 1 - g', 'ok 2 - b'))[0]), [])
        self.assertEqual(self.collected(run(L(HEAD, '    1..4', 'ok 1 - a'))[0]), [])   # a plan of a subtest is not the plan of the run
        self.assertEqual(self.collected(run(L(HEAD, 'ok 1 - a', '1..1'))[0]), [])

    def test_end_true_with_a_plan_equal_to_the_results_of_level_zero(self):
        for exit_code in (0, 1):   # a failing run is complete too: the counters come from the adapter
            with self.subTest(exit_code=exit_code):
                rec, adapter, ctx, complete = run(L(HEAD, 'ok 1 - a', 'not ok 2 - b', 'ok 3 - c # SKIP', '1..3'), exit_code=exit_code)
                self.assertTrue(complete)
        self.assertTrue(run(L(HEAD, '1..2', 'ok 1 - a', 'ok 2 - b'))[3])   # the plan may come first

    def test_the_plan_counts_parents_and_files_not_only_the_leaves(self):
        text = L(HEAD, '# Subtest: g', '    # Subtest: a', '    ok 1 - a', '    # Subtest: b', '    ok 2 - b', '    1..2', 'ok 1 - g', '1..1')
        rec, adapter, ctx, complete = run(text)
        self.assertEqual(len(rec.emitted()), 2)   # two leaves, one line of level 0
        self.assertTrue(complete)

    def test_end_false_when_the_plan_and_the_lines_differ(self):
        self.assertFalse(run(L(HEAD, 'ok 1 - a', 'ok 2 - b', '1..3'))[3])
        self.assertFalse(run(L(HEAD, 'ok 1 - a', 'ok 2 - b', '1..1'))[3])
        self.assertFalse(run(L(HEAD, 'ok 1 - a', '1..2'), exit_code=1)[3])

    def test_a_plan_of_a_subtest_is_not_the_plan_of_the_run(self):
        text = L(HEAD, '    1..9', 'ok 1 - a', '    ok 2 - indented without a subtest line', '1..1')
        self.assertTrue(run(text)[3])   # level 0: one result and the plan 1..1; the indented plan and result are not level 0

    def test_end_true_without_a_plan_only_for_exit_zero_with_a_result(self):
        self.assertTrue(run(L('ok - a', 'ok - b'), exit_code=0)[3])
        self.assertTrue(run(L('ok - a', 'not ok - b'), exit_code=0)[3])   # complete: the failed test then wins over the exit code in the recorder
        self.assertFalse(run(L('ok - a', 'ok - b'), exit_code=1)[3])   # a script that threw: the rest never ran
        self.assertFalse(run(L('ok - a'), exit_code=2)[3])

    def test_end_false_without_a_result(self):
        self.assertFalse(run('', exit_code=0)[3])
        self.assertFalse(run(L(HEAD), exit_code=0)[3])
        self.assertFalse(run(L(HEAD, '1..0'), exit_code=0)[3])   # an empty plan is not a result
        self.assertFalse(run(L(HEAD, '1..0 # SKIP nothing to do'), exit_code=0)[3])
        self.assertFalse(run(L(HEAD, '# just a log', 'ok-x'), exit_code=0)[3])
        self.assertFalse(run(L('some output', 'ok'), exit_code=0, explicit=False)[3])   # auto: no header, nothing seen

    def test_end_false_after_bail_out_even_when_the_plan_matches_or_there_is_no_plan(self):
        self.assertFalse(run(L(HEAD, '1..2', 'ok 1 - a', 'ok 2 - b', 'Bail out! late'), exit_code=0)[3])
        self.assertFalse(run(L(HEAD, 'ok 1 - a', 'Bail out! no plan'), exit_code=0)[3])
        self.assertFalse(run(L('ok - a', 'Bail out! bare'), exit_code=0)[3])


class Continuity(unittest.TestCase):
    """The same stream cut at any place gives the same emits (a pipe delivers any split; `partial` carries the unfinished tail)."""

    def texts(self):
        out = {name: stream_text(name) for name in ('tap-node22-describe.jsonl', 'tap-node22-edge.jsonl', 'tap-node22-multi.jsonl', 'tap-node24-todo.jsonl', 'tap-bare-throws.jsonl',
                                                    'tap-bare-green.jsonl', 'tap-bail-out.jsonl')}
        out['ansi'] = L(HEAD, '\x1b[32mok 1 - green\x1b[0m', '# Subtest: g', '    # Subtest: x', '    not ok 1 - x', '      ---', "      error: 'é ż'", '      ...', 'not ok 1 - g', '1..1')
        return out

    def test_sizes_1_3_7_give_the_emits_of_the_whole_stream(self):
        for name, text in self.texts().items():
            exit_code = 0 if name in ('tap-bare-green.jsonl', 'tap-node24-todo.jsonl') else 1
            whole = run(text, exit_code=exit_code)
            for size in (1, 3, 7):
                with self.subTest(fixture=name, size=size):
                    cut = run(text, exit_code=exit_code, size=size)
                    self.assertEqual(cut[0].calls, whole[0].calls)
                    self.assertEqual(cut[3], whole[3])
            self.assertTrue(whole[0].emitted(), name)

    def test_the_unfinished_tail_alone_is_not_a_result(self):
        recorder, adapter, ctx, sizes = live([HEAD])
        adapter.partial('out', 'ok 1 - not finished yet', ctx)
        self.assertEqual(recorder.emitted(), [])


class Fixtures(unittest.TestCase):
    """K1, K3, K4, K14 on the recordings (Node 20.18.1, 22.22.0, 24.11.1; `--format tap`)."""
    DESCRIBE = [('math::adds', 'passed', 'math'), ('math::fails', 'failed', 'math'), ('math::skipped one', 'skipped', 'math'), ('math::todo one', 'skipped', 'math'),
                ('math > nested::deep', 'passed', 'math > nested'), ('top level', 'passed', None), ('slow', 'passed', None)]
    EDGE = [('name with # hash', 'passed', None), ('name with \\ backslash and - dash', 'passed', None), ('dup', 'passed', None), ('dup (2)', 'passed', None),
            ('with subtests::inner ok', 'passed', 'with subtests'), ('with subtests::inner bad', 'failed', 'with subtests'), ('cancelled by timeout', 'failed', None),
            ('multi\\nline name', 'passed', None)]

    def test_k1_describe_on_node_20_22_and_24(self):
        for name in ('tap-node20-describe.jsonl', 'tap-node22-describe.jsonl', 'tap-node24-describe.jsonl'):
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('tap', name)
                self.assertEqual(table(rec), self.DESCRIBE)
                self.assertTrue(complete)   # plan 1..3 = the three lines of level 0 (a describe counts), exit 1 does not matter
                self.assertEqual(msg_of(rec, 'math::fails'), 'Expected values to be strictly equal:')
                self.assertEqual(rec.states('math'), [])   # the describe is not a test, neither `ok` nor `not ok`
                self.assertNotIn('math', ids(rec))
                self.assertIn('not now', msg_of(rec, 'math::skipped one'))
                self.assertIn('todo', msg_of(rec, 'math::todo one').lower())
                self.assertNotIn('running', [c[3] for c in rec.emitted()])

    def test_k1_the_failed_leaf_is_sent_twice_the_others_once(self):
        rec = replay_fixture('tap', 'tap-node22-describe.jsonl')[0]
        self.assertEqual(rec.states('math::fails'), ['failed', 'failed'])
        for test_id in ('math::adds', 'math::skipped one', 'math::todo one', 'math > nested::deep', 'top level', 'slow'):
            self.assertEqual(len(rec.states(test_id)), 1, test_id)
        failed = [c for c in rec.emitted() if c[1] == 'math::fails']
        self.assertAlmostEqual(failed[1][5], 1.27, delta=1)   # duration_ms: 1.269695

    def test_k1_k3_k4_edge_cases_on_node_22_and_24(self):
        for name in ('tap-node22-edge.jsonl', 'tap-node24-edge.jsonl'):
            with self.subTest(fixture=name):
                rec, adapter, ctx, complete = replay_fixture('tap', name)
                self.assertEqual(table(rec), self.EDGE)   # the parent `not ok 5 - with subtests` (subtestsFailed) is not a test
                self.assertTrue(complete)   # plan 1..7 = the seven lines of level 0
                self.assertEqual(msg_of(rec, 'with subtests::inner bad'), 'boom')
                self.assertEqual(msg_of(rec, 'cancelled by timeout'), 'test timed out after 50ms')
                self.assertNotIn('with subtests', ids(rec))
                self.assertEqual(name_of(rec, 'dup (2)'), 'dup (2)')
                self.assertEqual(name_of(rec, 'multi\\nline name'), 'multi\\nline name')   # a backslash and an `n`, not a line break

    def test_k4_the_names_of_the_edge_stream_are_the_ones_a_naive_decoder_would_get_wrong(self):
        text = stream_text('tap-node22-edge.jsonl')
        self.assertIn('multi\\\\nline name', text)   # two backslashes in the stream, one in the name
        self.assertIn('\\# hash', text)

    def test_multi_two_files_with_a_duplicate_and_two_files_that_did_not_load(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-node22-multi.jsonl')
        self.assertEqual(table(rec), [('adds', 'passed', None), ('only a', 'passed', None), ('adds (2)', 'passed', None), ('b fails', 'failed', None),
                                      ('c.test.js', 'failed', None), ('d.test.js', 'failed', None)])
        self.assertTrue(complete)   # plan 1..6 = six lines of level 0 although the numbers run 1, 2, 3, 4, 3, 4
        self.assertEqual(msg_of(rec, 'b fails'), 'The expression evaluated to a falsy value:')
        self.assertEqual(msg_of(rec, 'c.test.js'), "SyntaxError: Unexpected token ';'")
        self.assertEqual(msg_of(rec, 'd.test.js'), 'Error: file level crash')
        self.assertFalse(any('looks like' in c[2] for c in rec.emitted()))   # `# ok - ...` is a console.log of the test, printed as a comment

    def test_k14_a_todo_that_failed_is_skipped_not_failed(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-node24-todo.jsonl')
        self.assertEqual(table(rec), [('todo fails', 'skipped', None), ('todo passes', 'skipped', None), ('skip bare', 'skipped', None)])
        self.assertNotIn('failed', [c[3] for c in rec.emitted()])
        self.assertTrue(complete)
        self.assertEqual(load('tap-node24-todo.jsonl')[0]['exit'], 0)   # the recorded exit code of that run is 0

    def test_a_bare_script_green(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-bare-green.jsonl')
        self.assertEqual(table(rec), [('adds', 'passed', None), ('subtracts', 'passed', None), ('multiplies', 'passed', None)])
        self.assertTrue(complete)   # no plan, exit 0, three results

    def test_a_bare_script_that_threw_reports_what_it_printed_and_is_not_complete(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-bare-throws.jsonl')
        self.assertEqual(table(rec), [('adds', 'passed', None), ('subtracts', 'passed', None)])   # the stack on stderr is nobody's result
        self.assertFalse(complete)   # exit 1 without a plan

    def test_a_bare_script_with_a_failure_line_and_exit_zero(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-bare-not-ok-exit0.jsonl')
        self.assertEqual(table(rec), [('adds', 'passed', None), ('subtracts', 'failed', None), ('multiplies', 'passed', None)])
        self.assertTrue(complete)   # complete: the recorder lets the failed test win over exit 0 (K6, `test_skillsync_test_run.py`)

    def test_bail_out(self):
        rec, adapter, ctx, complete = replay_fixture('tap', 'tap-bail-out.jsonl')
        self.assertEqual(table(rec), [('first', 'passed', None), ('second', 'passed', None), ('third', 'failed', None)])
        self.assertFalse(complete)

    def test_every_node_fixture_is_replayed_by_the_adapter_alone(self):
        for name in sorted(p.name for p in ar.FIXTURES.glob('tap-*.jsonl')):
            with self.subTest(fixture=name):
                self.assertTrue(replay_fixture('tap', name)[0].emitted())
                self.assertTrue(load(name)[0].get('format'))


if __name__ == '__main__':
    unittest.main()
