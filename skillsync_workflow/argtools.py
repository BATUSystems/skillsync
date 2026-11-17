"""One shared fix for the argparse of Python before 3.12: a positional with `nargs='?'` or `'*'` that follows another positional is matched EMPTY at the first
positional, so `skillsync prototype sync <path> --json <slug>` ended in `unrecognized arguments`. The leaf parsers (those without subcommands) read their
arguments intermixed (`parse_known_intermixed_args`) there; from 3.12 argparse does that itself and nothing changes (the same class, the same messages, the same
exit codes). Parsers with a `REMAINDER` or sub-parser positional are left alone (intermixed parsing does not support them)."""
import argparse
import sys

NEEDS_FIX = sys.version_info < (3, 12)


class IntermixedParser(argparse.ArgumentParser):
    """A leaf parser that reads options and positionals intermixed (when `intermix` is on)."""
    intermix = NEEDS_FIX
    _busy = False

    def parse_known_args(self, args=None, namespace=None):
        if self.intermix and not self._busy:
            self._busy = True   # `parse_known_intermixed_args` calls `parse_known_args` itself
            try:
                return self.parse_known_intermixed_args(args, namespace)
            finally:
                self._busy = False
        return super().parse_known_args(args, namespace)


def _is_leaf(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) or action.nargs in (argparse.PARSER, argparse.REMAINDER):
            return False
    for group in parser._mutually_exclusive_groups:   # intermixed reading refuses a mutually exclusive group that holds a positional
        if any(not action.option_strings for action in group._group_actions):
            return False
    return True


def intermix_leaf_parsers(root, force=None):
    """Every sub-parser of `root` that has no subcommands becomes an `IntermixedParser` (`force=True` turns the intermixed reading on whatever the Python is: tests)."""
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            for parser in action.choices.values():
                if any(isinstance(item, argparse._SubParsersAction) for item in parser._actions):
                    intermix_leaf_parsers(parser, force)
                elif _is_leaf(parser):
                    parser.__class__ = IntermixedParser
                    if force is not None:
                        parser.intermix = force
