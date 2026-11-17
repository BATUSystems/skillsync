"""180-X: every command of the core with two positionals or an optional positional reads `command <p1> <option> <p2>` the same on every Python.

The table is GENERATED from the definitions of the parsers (a new command enters it by itself). Python before 3.12 matched a positional `nargs='?'/'*'` EMPTY
right after the first positional, so the second positional after an option ended in `unrecognized arguments`; the leaf parsers read intermixed there
(`argtools`). The intermixed reading is forced here, so the test also exercises that path on 3.12 and later."""
import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import argtools, cli  # noqa: E402


def build_parser():
    captured = {}
    original = argparse.ArgumentParser.parse_args

    def capture(self, *args, **kwargs):
        captured['parser'] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = capture
    saved = sys.argv
    sys.argv = ['skillsync']
    try:
        with contextlib.suppress(SystemExit):
            cli.main()
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.argv = saved
    return captured['parser']


def leaves(parser, path=()):
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    for action in subparsers:
        for name, child in action.choices.items():
            yield from leaves(child, path + (name,))
    if not subparsers and path:
        yield path, parser


def value_for(action, fallback):
    if action.choices:
        return str(next(iter(action.choices)))
    if action.type in (int, float):
        return '1'
    return fallback


def table(root):
    """`[(command words, parser, positionals, flag, required option argv)]` for the parsers with >= 2 positionals or an optional positional."""
    rows = []
    for path, parser in leaves(root):
        positionals = [a for a in parser._actions if not a.option_strings and a.dest != 'help']
        if len(positionals) < 2 and not any(a.nargs in ('?', '*') for a in positionals):
            continue
        flag = next((a.option_strings[0] for a in parser._actions if a.option_strings and a.nargs == 0 and a.option_strings[0] not in ('-h', '--help')), None)
        required = []
        for a in parser._actions:
            if a.option_strings and a.required:
                required += [a.option_strings[0]] + ([] if a.nargs == 0 else [value_for(a, 'X')])
        rows.append((path, parser, positionals, flag, required))
    return rows


def parse(root, argv):
    with contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            return root.parse_args(argv), ''
        except SystemExit:
            return None, err.getvalue()


class PositionalsAndOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = build_parser()
        argtools.intermix_leaf_parsers(cls.root, force=True)   # the intermixed path on every Python
        cls.rows = table(cls.root)

    def test_the_table_is_not_empty_and_has_the_known_commands(self):
        names = {' '.join(path) for path, *_ in self.rows}
        self.assertTrue({'test-run', 'prototype sync', 'prototype gate', 'prototype question wait', 'prototype merge-comments'} <= names, names)

    def test_an_option_between_the_positionals_is_read_like_the_same_call_without_it(self):
        for path, parser, positionals, flag, required in self.rows:
            if flag is None or len(positionals) < 2:
                continue
            values = [value_for(a, 'v%d' % index) if not a.choices else value_for(a, 'v') for index, a in enumerate(positionals)]
            plain, between = list(path) + required + values, list(path) + required + values[:1] + [flag] + values[1:]
            with self.subTest(command=' '.join(path)):
                expected, error = parse(self.root, plain)
                self.assertIsNotNone(expected, f'the plain call {plain} must parse: {error}')
                got, error = parse(self.root, between)
                self.assertIsNotNone(got, f'{between}: {error}')
                for action in positionals:
                    self.assertEqual(getattr(got, action.dest), getattr(expected, action.dest), (path, action.dest))

    def test_errors_and_results_are_those_of_the_standard_reading(self):
        """3.12 and later read these calls natively: the intermixed reading must give the SAME namespace, message and exit for the calls that never had a problem."""
        plain = build_parser()
        if sys.version_info < (3, 12):
            # there the standard reading is the defective one (the table above is the proof), so there is nothing to compare with: the real parser must be the fixed one
            self.assertTrue(all(isinstance(parser, argtools.IntermixedParser) and parser.intermix for _, parser, *_ in table(plain)))
        else:
            for path, parser, positionals, flag, required in self.rows:
                for argv in (list(path), list(path) + required, list(path) + ['--no-such-option-x'], list(path) + required + ['v', '--no-such-option-x'],
                             list(path) + required + ['v1', 'v2', 'v3', 'v4', 'v5']):
                    with self.subTest(argv=argv):
                        standard, standard_error = parse(plain, argv)
                        mixed, mixed_error = parse(self.root, argv)
                        self.assertEqual(standard_error, mixed_error)
                        self.assertEqual(None if standard is None else vars(standard).keys(), None if mixed is None else vars(mixed).keys())
                        if standard is not None:
                            self.assertEqual({k: v for k, v in vars(standard).items() if k != 'func'}, {k: v for k, v in vars(mixed).items() if k != 'func'})

    def test_every_command_that_needs_the_fix_can_have_it(self):
        """A new command with a REMAINDER, a sub-parser or a mutually exclusive group holding a positional cannot be read intermixed: it must not hide in the table."""
        for path, parser in leaves(self.root):
            positionals = [a for a in parser._actions if not a.option_strings and a.dest != 'help']
            if len(positionals) >= 2 or any(a.nargs in ('?', '*') for a in positionals):
                with self.subTest(command=' '.join(path)):
                    self.assertTrue(argtools._is_leaf(parser), 'this parser cannot be read intermixed: option between positionals fails on Python before 3.12')

    def test_the_defect_and_the_fix_on_the_running_python(self):
        """The vanilla parser fails on Python before 3.12 exactly where the fixed one works (this is the defect the table guards; on 3.12 and later both work)."""
        def build(cls):
            parser = cls(prog='x')
            parser.add_argument('path')
            parser.add_argument('slug', nargs='?')
            parser.add_argument('--json', action='store_true')
            return parser
        argv = ['p', '--json', 's']
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                vanilla = build(argparse.ArgumentParser).parse_args(argv)
            except SystemExit:
                vanilla = None
        self.assertEqual(vanilla is not None, sys.version_info >= (3, 12), 'argparse of this Python: the defect is expected before 3.12 only')
        fixed = build(argtools.IntermixedParser)
        fixed.intermix = True
        parsed = fixed.parse_args(argv)
        self.assertEqual((parsed.path, parsed.slug, parsed.json), ('p', 's', True))

    def test_the_real_parser_is_intermixed_only_where_needed(self):
        parser = build_parser()
        argtools.intermix_leaf_parsers(parser)
        leaf = dict((' '.join(path), p) for path, p in leaves(parser))['prototype sync']
        self.assertIsInstance(leaf, argtools.IntermixedParser)
        self.assertEqual(leaf.intermix, sys.version_info < (3, 12))   # 3.12 and later: the standard reading, nothing changes
        self.assertNotIsInstance(parser, argtools.IntermixedParser)   # the root with subcommands stays a plain parser


if __name__ == '__main__':
    unittest.main()
