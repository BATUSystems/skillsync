"""The argparse tree of `skillsync` for the help guard and other tests.

This is the only place with access to private argparse details (`_actions`, `_SubParsersAction`,
`_choices_actions`, `_expand_help`). The tree is captured the way `test_skillsync_completion_generate`
does it: `cli.main()` runs with `parse_args` replaced by a function that keeps the root parser.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Node:
    path: tuple[str, ...]
    parser: argparse.ArgumentParser
    help: str | None            # `help=` given to add_parser (from the parent `_choices_actions`)
    leaf: bool
    args: list = field(default_factory=list)   # argparse actions of this node (no sub-command action)

    @property
    def key(self) -> str:
        return " ".join(self.path)


def capture_root() -> argparse.ArgumentParser:
    """Build the real parser tree without parsing anything."""
    sys.path.insert(0, str(ROOT))
    from skillsync_workflow import cli

    holder: dict = {}
    original = argparse.ArgumentParser.parse_args
    saved_argv = sys.argv

    def grab(self, *_args, **_kwargs):
        holder["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = grab
    sys.argv = ["skillsync"]
    try:
        try:
            cli.main()
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.argv = saved_argv
    return holder["parser"]


def _sub_actions(parser):
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def nodes(root: argparse.ArgumentParser) -> list[Node]:
    """All commands below the root (aliases once, by `id(parser)`), depth first."""
    out: list[Node] = []
    seen: set[int] = {id(root)}

    def walk(parser, path):
        subs = _sub_actions(parser)
        for action in subs:
            helps = {c.dest: c.help for c in action._choices_actions}
            for name, child in action.choices.items():
                if id(child) in seen:
                    continue
                seen.add(id(child))
                child_subs = _sub_actions(child)
                out.append(Node(path + (name,), child, helps.get(name), not child_subs,
                                [a for a in child._actions if not isinstance(a, argparse._SubParsersAction)]))
                walk(child, path + (name,))

    walk(root, ())
    return out


def is_help_action(action) -> bool:
    return isinstance(action, (argparse._HelpAction, argparse._VersionAction))


def checked_args(node: Node):
    """Arguments the rules look at: not `-h`, `--version`, nor hidden (`help=SUPPRESS`)."""
    for action in node.args:
        if is_help_action(action):
            continue
        if action.help is argparse.SUPPRESS:
            continue
        yield action


def expand_help(parser: argparse.ArgumentParser, action) -> str:
    """The help text after the `%`-expansion argparse does while rendering (raises on a bad `%`)."""
    return parser._get_formatter()._expand_help(action)


def hidden_command(root: argparse.ArgumentParser, node: Node) -> bool:
    return node.help is argparse.SUPPRESS


def option_names(node: Node) -> set[str]:
    return {o for a in node.args for o in a.option_strings}
