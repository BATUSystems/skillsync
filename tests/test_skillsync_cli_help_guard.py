"""Guard of `skillsync --help` (CORE-1160): every command and flag has a help text, every leaf command lists its exit codes,
`docs/cli.md` knows every command and the flags it names exist, and the texts are English ASCII.

The rules work on the real parser tree (`tests/parser_tree.py`) and are also run on synthetic trees (see the
`SyntheticRules` tests), so a rule that stopped looking would fail here. Known gaps live in
`tests/data/cli_help_exceptions/<package>.yaml`: key = (command path, rule, dest), a reason is required, an entry that
no longer matches a finding is an error, and the number of entries may only go down (`MAX_EXCEPTIONS`).
"""
import argparse
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parser_tree import ROOT, Node, capture_root, checked_args, expand_help, nodes  # noqa: E402

sys.path.insert(0, str(ROOT))
from skillsync_workflow import cli_help  # noqa: E402

EXCEPTIONS_DIR = ROOT / 'tests' / 'data' / 'cli_help_exceptions'
CLI_MD = ROOT / 'docs' / 'cli.md'
# The upper bound of known gaps. Each package lowers it; a rise breaks the test (rule G8).
MAX_EXCEPTIONS = 11
RULES = ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7')
POLISH_WORDS = ('oraz', 'dla', 'przez', 'jest', 'nie', 'gdy', 'lub', 'ktory', 'ktora', 'ktore', 'domyslnie', 'domyślnie', 'albo',
                'jako', 'tylko', 'przy', 'brak', 'wiecej', 'nazwa', 'wartosc', 'plik', 'katalog')


class Finding(tuple):
    """(path, rule, dest) plus a message; compares by the key only."""

    def __new__(cls, path, rule, dest, message=''):
        self = super().__new__(cls, (path, rule, dest))
        self.message = message
        return self


def node_path(node: Node) -> str:
    return ' '.join(node.path)


def g1(tree):
    """Every command and every argument has a non-empty help."""
    out = []
    for node in tree:
        if node.help is argparse.SUPPRESS:
            continue
        if not (node.help or '').strip():
            out.append(Finding(node_path(node), 'G1', '', 'command without help'))
        for action in checked_args(node):
            if not (action.help or '').strip():
                out.append(Finding(node_path(node), 'G1', action.dest, f'argument {action.option_strings or action.dest} without help'))
    return out


def g2(tree):
    """Every leaf command and every group has a description."""
    out = []
    for node in tree:
        if node.help is argparse.SUPPRESS:
            continue
        if not (node.parser.description or '').strip():
            out.append(Finding(node_path(node), 'G2', '', 'command without description'))
    return out


def g3(tree, exit_class=None, classes=None, custom=None):
    """Every leaf command has an entry in EXIT_CLASS; its epilog starts with `Exit codes:` and names exactly the codes of the class."""
    exit_class = cli_help.EXIT_CLASS if exit_class is None else exit_class
    classes = cli_help.EXIT_CLASSES if classes is None else classes
    custom = cli_help.CUSTOM_EPILOG if custom is None else custom
    out = []
    for node in tree:
        if not node.leaf or node.help is argparse.SUPPRESS:
            continue
        name = exit_class.get(node.path)
        if name is None:
            out.append(Finding(node_path(node), 'G3', '', 'no entry in EXIT_CLASS'))
            continue
        epilog = node.parser.epilog or ''
        if not epilog.startswith('Exit codes:'):
            out.append(Finding(node_path(node), 'G3', '', 'epilog does not start with "Exit codes:"'))
            continue
        if name == 'custom':
            if not custom.get(node.path):
                out.append(Finding(node_path(node), 'G3', '', 'custom class without a reason text'))
            continue
        want = set(classes[name][0])
        got = {int(n) for n in re.findall(r'(?<![\w.])(\d+)(?![\w.])', epilog[len('Exit codes:'):])}
        if got != want:
            out.append(Finding(node_path(node), 'G3', '', f'epilog names {sorted(got)}, class {name} has {sorted(want)}'))
    return out


def g4(tree):
    """An argument with a real default shows it in the rendered help (after the `%` expansion)."""
    out = []
    for node in tree:
        for action in checked_args(node):
            if not action.help:
                continue
            try:
                text = expand_help(node.parser, action)
            except (TypeError, ValueError, KeyError) as exc:
                out.append(Finding(node_path(node), 'G4', action.dest, f'help cannot be rendered: {exc}'))
                continue
            default = action.default
            if action.required or default is None or default == '' or default is True or default is False or default == argparse.SUPPRESS \
                    or isinstance(default, (list, tuple, set, dict)):
                continue
            if isinstance(action, (argparse._StoreConstAction, argparse._CountAction)):
                continue
            if not re.search(r'(?<![\w.])' + re.escape(str(default)) + r'(?![\w])', text):
                out.append(Finding(node_path(node), 'G4', action.dest, f'default {default!r} not in help'))
    return out


def g7(tree):
    """Texts are ASCII English."""
    out = []
    for node in tree:
        items = [('', node.help or ''), ('', node.parser.description or ''), ('', node.parser.epilog or '')]
        items += [(a.dest, a.help or '') for a in node.args if a.help is not argparse.SUPPRESS]
        for dest, text in items:
            words = {w.lower() for w in re.findall(r'[A-Za-z\u0080-￿]+', text)}
            bad = [c for c in text if ord(c) >= 128]
            polish = sorted(words & set(POLISH_WORDS))
            if bad or polish:
                out.append(Finding(node_path(node), 'G7', dest, f'non-ASCII {bad[:3]!r} or Polish words {polish}'))
    return out


def unique(findings):
    """One finding per key (two flags of a command may share a `dest`)."""
    seen, out = set(), []
    for finding in findings:
        if tuple(finding) not in seen:
            seen.add(tuple(finding))
            out.append(finding)
    return out


def tree_findings(tree):
    return unique(g1(tree) + g2(tree) + g3(tree) + g4(tree) + g7(tree))


# ---- documentation (G5, G6) -------------------------------------------------------------------------------------------------

def slug(heading: str) -> str:
    text = re.sub(r'[`()]', '', heading).strip().lower()
    text = re.sub(r'[^\w\s-]', '', text)
    return re.sub(r'\s', '-', text)


def norm_heading(text: str) -> str:
    text = re.sub(r'\([^)]*\)', '', text.replace('`', ''))
    return re.sub(r'\s+', ' ', text).strip().lower()


def parse_doc(text: str):
    """(headings: [(level, title, line index)], lines)."""
    lines = text.split('\n')
    headings, fenced = [], False
    for i, line in enumerate(lines):
        if line.startswith('```'):
            fenced = not fenced
        match = None if fenced else re.match(r'^(#{1,6})\s+(.*\S)\s*$', line)
        if match:
            headings.append((len(match.group(1)), match.group(2), i))
    return headings, lines


def index_rows(lines, headings):
    """Rows of the table under `## Spis komend`: [(commands, link text, anchor)]."""
    start = next((h for h in headings if h[1].strip().lower() == 'spis komend'), None)
    if start is None:
        return []
    end = next((h[2] for h in headings if h[2] > start[2]), len(lines))
    rows = []
    for line in lines[start[2] + 1:end]:
        if not line.startswith('|') or set(line) <= set('|- '):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 2 or cells[0].lower() == 'komendy':
            continue
        commands = re.findall(r'`([a-z][\w-]*)`', re.sub(r'\([^)]*\)', '', cells[0]))
        link = re.search(r'\[([^\]]+)\]\(#([^)]+)\)', cells[1])
        rows.append((commands, link.group(1) if link else '', link.group(2) if link else ''))
    return rows


def g5(top_commands, doc_text):
    """Every top-level command is in the index; the link text names a heading and the anchor fits it."""
    headings, lines = parse_doc(doc_text)
    rows = index_rows(lines, headings)
    by_title = {norm_heading(h[1]): h for h in headings}
    listed, out = set(), []
    for commands, text, anchor in rows:
        listed.update(commands)
        title = by_title.get(norm_heading(text))
        if title is None:
            out.append(Finding(' '.join(commands), 'G5', '', f'link text {text!r} is no heading'))
        elif slug(title[1]) != anchor:
            out.append(Finding(' '.join(commands), 'G5', '', f'anchor #{anchor} does not fit heading {title[1]!r}'))
    for command in sorted(set(top_commands) - listed):
        out.append(Finding(command, 'G5', '', 'command missing from the index of docs/cli.md'))
    return out


FLAG = re.compile(r'(?<![\w-])(--[a-z][\w-]*)(?![\w-])')


def subtree_options(tree, top):
    """(all option names, required option names) of a top-level command and everything below it."""
    names, required = {'--help', '--version'}, set()
    for node in tree:
        if node.path[0] != top:
            continue
        for action in node.args:
            names.update(action.option_strings)
            if action.required and action.option_strings:
                required.add(action.option_strings[0])
    return names, required


ANCHOR = re.compile(r'^[-*|\s]*((?:\*\*)?`[^`]+`(?:\*\*)?(?:\s*,\s*(?:\*\*)?`[^`]+`(?:\*\*)?)*)')


def anchored_commands(unit_start: str, commands) -> set:
    """Commands a text unit is about: the leading backtick tokens (`` `a`, `b` ``, `` `skillsync a <x>` ``) by their first word."""
    match = ANCHOR.match(unit_start)
    if not match:
        return set()
    found = set()
    for token in re.findall(r'`([^`]+)`', match.group(1)):
        words = token.split()
        if words and words[0] == 'skillsync':
            words = words[1:]
        if words and words[0] in commands:
            found.add(words[0])
    return found


def command_units(commands, headings, lines):
    """[(anchored commands, text, line index)] for the table rows, paragraphs and sub-sections of `docs/cli.md` below `## Spis komend`
    that begin with a command in backticks; a heading of that form owns its whole sub-section."""
    heading_at = {h[2]: h for h in headings}
    spis = next((h for h in headings if h[1].strip().lower() == 'spis komend'), None)
    skip_to = next((h[2] for h in headings if spis and h[2] > spis[2]), 0)
    units, i = [], skip_to
    while i < len(lines):
        line = lines[i]
        if i in heading_at:
            level, title, _ = heading_at[i]
            anchored = anchored_commands(title, commands)
            if anchored:
                end = next((h[2] for h in headings if h[2] > i and h[0] <= level), len(lines))
                units.append((anchored, '\n'.join(lines[i + 1:end]), i))
            i += 1
            continue
        anchored = anchored_commands(line, commands)
        if line.lstrip().startswith('|'):
            if anchored:
                units.append((anchored, line, i))
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and j not in heading_at and not lines[j].lstrip().startswith('|'):
            j += 1
        if anchored:
            units.append((anchored, '\n'.join(lines[i:j]), i))
        i = j
    return units


def g6(tree, top_commands, doc_text):
    """Simplifications against the spec (accepted in H-2): a block is a text unit (table row, paragraph or sub-section) that starts with the command
    in backticks, inside the section the index links to; a command alone in its section without such a unit uses the whole section; a shared row
    allows the flags of all its commands, and `prototype` the flags of its whole sub-tree. Flags named in the blocks of a command exist in its parser;
    required flags of a command appear in its blocks. A command with no block of its own is skipped (G5 covers the section)."""
    headings, lines = parse_doc(doc_text)
    top = set(top_commands)
    units = command_units(top, headings, lines)
    rows = index_rows(lines, headings)
    ranges, alone = {}, set()
    for commands, text, _anchor in rows:
        head = next((h for h in headings if norm_heading(h[1]) == norm_heading(text)), None)
        if head is not None:
            end = next((h[2] for h in headings if h[2] > head[2] and h[0] <= head[0]), len(lines))
            for command in commands:
                ranges[command] = (head[2], end)
            if len(commands) == 1:
                alone.add(commands[0])
    out = []
    for command in sorted(top):
        low, high = ranges.get(command, (0, len(lines)))
        mine = [(anchored, text) for anchored, text, at in units if command in anchored and low <= at < high]
        if not mine and command in alone:
            mine = [({command}, '\n'.join(lines[low:high]))]   # the only command of its section: the whole section is its block
        if not mine:
            continue
        _names, required = subtree_options(tree, command)
        mentioned_all = set()
        for anchored, text in mine:
            allowed = set()
            for other in anchored:
                allowed |= subtree_options(tree, other)[0]
            mentioned = set(FLAG.findall(text))
            mentioned_all |= mentioned
            for flag in sorted(mentioned - allowed):
                out.append(Finding(command, 'G6', flag, f'{flag} is named in docs/cli.md next to `{command}` but unknown to it'))
        for flag in sorted(required - mentioned_all):
            out.append(Finding(command, 'G6', flag, f'required {flag} of `{command}` is not in docs/cli.md'))
    return out


# ---- exceptions (G8) ---------------------------------------------------------------------------------------------------------

def load_exceptions(directory=EXCEPTIONS_DIR):
    entries = []
    for path in sorted(Path(directory).glob('*.yaml')) if Path(directory).is_dir() else []:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        for item in data.get('exceptions') or []:
            entries.append((path.name, item))
    return entries


def apply_exceptions(findings, entries):
    """(findings not covered, problems of the entries themselves)."""
    keys, problems = {}, []
    for name, item in entries:
        key = (item.get('path', ''), item.get('rule', ''), item.get('dest', ''))
        if not str(item.get('reason', '')).strip():
            problems.append(f'{name}: {key} has no reason')
        if key in keys:
            problems.append(f'{name}: {key} is listed twice')
        keys[key] = name
    found = {tuple(f) for f in findings}
    for key, name in keys.items():
        if key not in found:
            problems.append(f'{name}: {key} no longer matches a finding (delete it)')
    return [f for f in findings if tuple(f) not in keys], problems


# ---- tests -------------------------------------------------------------------------------------------------------------------

class RealTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'COLUMNS' not in os.environ:
            os.environ['COLUMNS'] = '80'
            cls.addClassCleanup(os.environ.pop, 'COLUMNS', None)
        root = capture_root()
        cls.root = root
        cls.tree = nodes(root)
        cls.top = sorted({n.path[0] for n in cls.tree if n.help is not argparse.SUPPRESS})
        cls.doc = CLI_MD.read_text(encoding='utf-8')
        cls.findings = tree_findings(cls.tree) + g5(cls.top, cls.doc) + g6(cls.tree, cls.top, cls.doc)
        cls.entries = load_exceptions()

    def test_every_rule_passes_after_the_known_exceptions(self):
        left, problems = apply_exceptions(self.findings, self.entries)
        report = sorted(f'{f[1]} {f[0]!r} {f[2]!r}: {f.message}' for f in left) + problems
        self.assertEqual(report[:40], [], f'{len(report)} problems in total')

    def test_exceptions_only_go_down(self):
        self.assertLessEqual(len(self.entries), MAX_EXCEPTIONS)

    def test_exceptions_keep_a_reason(self):
        _left, problems = apply_exceptions(self.findings, self.entries)
        self.assertEqual([p for p in problems if 'no reason' in p], [])

    def test_every_leaf_command_has_an_exit_class_entry(self):
        leaves = {n.path for n in self.tree if n.leaf}
        self.assertEqual(sorted(leaves - set(cli_help.EXIT_CLASS)), [])
        self.assertEqual(sorted(set(cli_help.EXIT_CLASS) - leaves), [])

    def test_every_node_and_argument_has_a_key(self):
        for node in self.tree:
            self.assertEqual(getattr(node.parser, 'node_key', None), node.path)
            for action in node.args:
                self.assertEqual(getattr(action, 'node_key', None), (node.path, action.dest))

    def test_every_node_renders_at_both_widths(self):
        """G9: `format_help()` of every node, at 80 and 200 columns and with NO_COLOR, raises nothing."""
        saved = {k: os.environ.get(k) for k in ('COLUMNS', 'NO_COLOR')}
        try:
            os.environ['NO_COLOR'] = '1'
            for columns in ('80', '200'):
                os.environ['COLUMNS'] = columns
                self.assertTrue(self.root.format_help().strip())
                for node in self.tree:
                    self.assertTrue(node.parser.format_help().strip(), node_path(node))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_the_guard_is_fast(self):
        import time
        start = time.perf_counter()
        tree_findings(self.tree)
        self.assertLess(time.perf_counter() - start, 10)


def synthetic(build):
    root = argparse.ArgumentParser(prog='skillsync')
    subs = root.add_subparsers(dest='command')
    build(subs)
    return nodes(root)


class SyntheticRules(unittest.TestCase):
    def test_g1_flags_a_command_and_a_flag_without_help(self):
        def build(subs):
            p = subs.add_parser('demo')
            p.add_argument('--x')
        keys = {tuple(f) for f in g1(synthetic(build))}
        self.assertEqual(keys, {('demo', 'G1', ''), ('demo', 'G1', 'x')})

    def test_g1_is_quiet_when_everything_is_described(self):
        def build(subs):
            p = subs.add_parser('demo', help='Demo.')
            p.add_argument('--x', help='A flag.')
        self.assertEqual(g1(synthetic(build)), [])

    def test_g2_flags_a_command_without_description(self):
        def build(subs):
            subs.add_parser('demo', help='Demo.')
        self.assertEqual([tuple(f) for f in g2(synthetic(build))], [('demo', 'G2', '')])

    def test_g3_needs_an_entry_an_epilog_and_the_right_codes(self):
        def build(subs):
            subs.add_parser('a', help='A.', description='A.')
            subs.add_parser('b', help='B.', description='B.', epilog='Exit codes: 0 PASS.')
            subs.add_parser('c', help='C.', description='C.', epilog=cli_help.EXIT_CLASSES['standard'][1])
        tree = synthetic(build)
        classes = {('a',): 'standard', ('b',): 'standard', ('c',): 'standard'}
        found = {f[0]: f.message for f in g3(tree, exit_class={k: v for k, v in classes.items() if k != ('a',)})}
        self.assertIn('no entry', found['a'])
        self.assertIn('names [0]', found['b'])
        self.assertNotIn('c', found)

    def test_g3_custom_needs_a_reason_text(self):
        def build(subs):
            subs.add_parser('a', help='A.', description='A.', epilog='Exit codes: it depends.')
        tree = synthetic(build)
        self.assertEqual([f.message for f in g3(tree, exit_class={('a',): 'custom'}, custom={})], ['custom class without a reason text'])
        self.assertEqual(g3(tree, exit_class={('a',): 'custom'}, custom={('a',): 'x'}), [])

    def test_g4_wants_the_default_in_the_rendered_text(self):
        def build(subs):
            p = subs.add_parser('demo', help='D.', description='D.')
            p.add_argument('--a', default=2.0, help='Seconds.')
            p.add_argument('--b', default=2.0, help='Seconds (default: %(default)s).')
            p.add_argument('--c', default='auto', help='Mode (default: auto).')
            p.add_argument('--d', default=0, help='Zero.')
            p.add_argument('--e', required=True, help='Required.')
            p.add_argument('--f', action='store_true', help='Flag.')
            p.add_argument('--g', default='.', help='Directory, e.g. a.b')
            p.add_argument('--h', default='', help='Empty.')
            p.add_argument('--i', action='count', default=0, help='Count.')
        keys = {f[2] for f in g4(synthetic(build))}
        self.assertEqual(keys, {'a', 'd', 'g'})

    def test_g4_catches_a_percent_that_cannot_be_rendered(self):
        def build(subs):
            p = subs.add_parser('demo', help='D.', description='D.')
            p.add_argument('--t', default='x', help='HH:MM from `date +%H:%M`')
        try:
            tree = synthetic(build)
        except ValueError:
            return   # Python 3.14 rejects a bad `%` when the argument is added: the product cannot ship it at all
        self.assertEqual([f[2] for f in g4(tree)], ['t'])

    def test_g7_flags_polish_and_non_ascii(self):
        def build(subs):
            subs.add_parser('a', help='Zrób to dla mnie.', description='A.')
            subs.add_parser('b', help='Fine — but dashed.', description='B.')
            subs.add_parser('c', help='Fine.', description='C.')
        self.assertEqual({f[0] for f in g7(synthetic(build))}, {'a', 'b'})

    def test_g5_wants_a_row_and_a_matching_anchor(self):
        doc = '## Spis komend\n\n| Komendy | Sekcja |\n|---|---|\n| `one` | [Alpha (`one`)](#alpha-one) |\n| `two` | [Beta](#beta) |\n\n## Alpha (`one`)\n\n## Gamma\n'
        found = {(f[0], f.message[:12]) for f in g5(['one', 'two', 'three'], doc)}
        self.assertEqual(found, {('two', 'link text \'B'), ('three', 'command miss')})
        wrong = doc.replace('#alpha-one', '#alpha')
        self.assertTrue(any('anchor' in f.message for f in g5(['one'], wrong)))

    def test_g6_flags_an_unknown_flag_and_a_missing_required_one(self):
        def build(subs):
            p = subs.add_parser('demo', help='D.', description='D.')
            p.add_argument('--real', help='R.')
            p.add_argument('--must', required=True, help='M.')
        tree = synthetic(build)
        doc = '## Spis komend\n\n| Komendy | Sekcja |\n|---|---|\n| `demo` | [Demo](#demo) |\n\n## Demo\n\n`demo --real --ghost` does things.\n'
        found = {(f[0], f[2]) for f in g6(tree, ['demo'], doc)}
        self.assertEqual(found, {('demo', '--ghost'), ('demo', '--must')})
        good = doc.replace('--ghost', '--must')
        self.assertEqual(g6(tree, ['demo'], good), [])

    def test_a_bad_percent_in_a_help_breaks_the_rendering(self):
        def build(subs):
            p = subs.add_parser('demo', help='D.', description='D.')
            p.add_argument('--t', help='Pick 50%.')
        with self.assertRaises((TypeError, ValueError, KeyError)):
            synthetic(build)[0].parser.format_help()   # 3.14 raises already when the argument is added

    def test_g8_reports_stale_and_reasonless_entries(self):
        finding = Finding('demo', 'G1', 'x')
        entries = [('A.yaml', {'path': 'demo', 'rule': 'G1', 'dest': 'x', 'reason': ''}),
                   ('A.yaml', {'path': 'gone', 'rule': 'G1', 'dest': '', 'reason': 'was fixed'})]
        left, problems = apply_exceptions([finding], entries)
        self.assertEqual(left, [])
        self.assertTrue(any('no reason' in p for p in problems))
        self.assertTrue(any('no longer matches' in p for p in problems))

    def test_the_tree_walk_sees_aliases_once_and_skips_help(self):
        def build(subs):
            p = subs.add_parser('demo', aliases=['d'], help='D.', description='D.')
            p.add_argument('--x', help='X.')
        tree = synthetic(build)
        self.assertEqual([n.path for n in tree], [('demo',)])
        self.assertEqual([a.dest for a in checked_args(tree[0])], ['x'])


from cli_inprocess import run_cli  # noqa: E402
from test_skillsync_workflow import WorkflowFixture  # noqa: E402


class ClassRepresentatives(WorkflowFixture):
    """G3b: one representative command per exit class runs for real; the codes it returns belong to the class
    and the epilog it prints names them (the class table is not checked against every command: each package proves the codes of its own
    commands in its report). Known limit: `serve` is not run to exit code 0, because that needs a running server and a signal."""

    # (class, command path, argv after the command, expected exit code)
    CASES = (
        ('standard', ('doctor',), ['{root}'], 0),
        ('standard', ('doctor',), ['/nonexistent-skillsync-dir'], 2),
        ('standard', ('map',), ['{root}', '--check'], 1),
        ('no-fail', ('retro',), ['{root}'], 0),
        ('no-fail', ('retro',), ['{root}', '--run-id', '../bad'], 2),
        ('sync', ('list',), [], 0),
        ('sync', ('import',), ['/nonexistent-skillsync-dir'], 1),
        ('sync', ('link',), ['{root}', '--no-such-flag'], 2),
        ('wait', ('approvals', 'wait'), ['{root}'], 2),
        ('prototype', ('prototype', 'list'), ['{root}'], 0),
        ('prototype', ('prototype', 'list'), ['/nonexistent-skillsync-dir'], 2),
        ('prototype-wait', ('prototype', 'wait'), ['{root}', 'x'], 2),
        ('serve', ('dashboard', 'serve'), ['{root}', '--port', '1'], 2),
        ('generate', ('completion', 'bash'), [], 0),
        ('generate', ('completion', 'bash'), ['--no-such-flag'], 2),
        ('custom', ('test-run',), ['{root}', '--', sys.executable, '-c', 'pass'], 2),
    )

    def test_the_codes_of_the_representatives_belong_to_their_class_and_epilog(self):
        for class_name, command, rest, expected in self.CASES:
            with self.subTest(class_name=class_name, command=command, rest=rest):
                result = run_cli([*command, *[a.format(root=self.root) for a in rest]])
                self.assertEqual(result.returncode, expected, result.stdout[-300:] + result.stderr[-300:])
                if class_name != 'custom':
                    self.assertIn(expected, cli_help.exit_codes(class_name))
                helped = run_cli([*command, '--help']).stdout
                self.assertIn('Exit codes:', helped)
                self.assertIn(str(expected), helped.split('Exit codes:')[1])

    def test_wait_class_returns_pass_blocked_and_timeout(self):
        (self.root / '.skillsync' / 'runs' / 'r1').mkdir(parents=True)
        sha = 'a' * 40
        waits = ['approvals', 'wait', str(self.root), '--run', 'r1', '--word', 'GO plan', '--sha', sha, '--timeout', '0', '--interval', '1']
        self.assertEqual(set(cli_help.exit_codes('wait')), {0, 2, 3})
        first = run_cli(waits)
        self.assertEqual(first.returncode, 3, first.stdout[-400:] + first.stderr[-400:])
        self.assertEqual(run_cli(['approvals', 'wait', '/nonexistent-skillsync-dir', '--word', 'GO plan', '--timeout', '0']).returncode, 2)
        recorded = run_cli(['approvals', 'record', str(self.root), '--run', 'r1', '--word', 'GO plan', '--sha', sha, '--source', 'cli'])
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertEqual(run_cli(waits).returncode, 0)

    def test_a_class_with_a_representative_exists_for_every_used_class(self):
        used = set(cli_help.EXIT_CLASS.values())
        self.assertEqual(sorted(used - {c[0] for c in self.CASES}), [])


from test_skillsync_prototype_question_cli import QA, RUN, QuestionCase  # noqa: E402


class QuestionClassRepresentatives(QuestionCase):
    """G3b for `prototype` (0, 1, 2) and `prototype-wait` (0, 2, 3): the commands run for real in a project with one prototype run."""

    def test_prototype_class_returns_pass_fail_and_blocked(self):
        self.q('list', code=0)
        self.q('show', 'qn-ffffffffffff', code=1)
        self.q('add', '--text', 'Q?', '--assume', 'a', '--origin', 'x', '--slug', 'no-such-run', code=2)
        self.assertEqual(set(cli_help.exit_codes('prototype')), {0, 1, 2})

    def test_the_read_only_question_commands_never_fail_with_1(self):
        """`question list|export|url` are `no-fail`: they run for real and return 0 or 2, never 1."""
        self.add('A?', QA)
        self.assertEqual(self.run_cli('list').returncode, 0)
        self.assertEqual(self.run_cli('export').returncode, 0)
        self.assertEqual(self.run_cli('list', '--slug', 'no-such-run').returncode, 2)
        self.assertEqual(self.run_cli('export', '--slug', 'no-such-run').returncode, 2)
        url = self.run_cli('url', QA)
        self.assertIn(url.returncode, (0, 2), url.stdout + url.stderr)
        self.assertEqual(self.run_cli('url', '--no-such-flag').returncode, 2)
        for command in ('list', 'export', 'url'):
            self.assertEqual(cli_help.EXIT_CLASS[('prototype', 'question', command)], 'no-fail')

    def test_prototype_wait_class_returns_pass_blocked_and_timeout(self):
        self.assertEqual(set(cli_help.exit_codes('prototype-wait')), {0, 1, 2, 3})
        self.add('A?', QA)
        self.q('wait', '--all', '--slug', RUN, '--timeout', '0', code=3)
        self.q('wait', '--all', '--slug', RUN, '--timeout', '0', '--after', 'nonexistent-op', code=1)
        self.q('wait', '--all', '--slug', 'no-such-run', '--timeout', '0', code=2)
        self.answer(QA)
        self.q('wait', '--all', '--timeout', '0', code=0)


if __name__ == '__main__':
    unittest.main()
