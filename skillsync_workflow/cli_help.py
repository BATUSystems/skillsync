"""Exit-code classes and the epilog of every leaf command of `skillsync --help`.

The classes come from `docs/domain/statuses-and-exit-codes.md`. `EXIT_CLASS` is the explicit
map from a leaf command (its path as a tuple) to a class: there is no default class, so a new
command without an entry is caught by `tests/test_skillsync_cli_help_guard.py` (rule G3).
`finish(parser)` runs once in `cli.main` after every module registered its commands: it sets
the epilog of each leaf command from the map and records for every node and argument its key
in the tree (`node_key`, plain data for the guard and for a later catalog of texts).

Texts here are ASCII English on purpose (rule G7).
"""
from __future__ import annotations

import argparse

# class -> (exit codes in order, epilog text). A `custom` command keeps its own epilog.
EXIT_CLASSES: dict[str, tuple[tuple[int, ...], str]] = {
    "standard": ((0, 1, 2), "Exit codes: 0 PASS or NOT_RUN, 1 FAIL, 2 BLOCKED (invalid arguments or environment)."),
    "no-fail": ((0, 2), "Exit codes: 0 PASS or NOT_RUN, 2 BLOCKED (invalid arguments or environment); never FAIL."),
    "sync": ((0, 1, 2), "Exit codes: 0 done (also with warnings or conflicts reported), 1 explicit error, 2 blocked or invalid arguments."),
    "wait": ((0, 2, 3), "Exit codes: 0 condition met, 2 BLOCKED (invalid arguments or environment), 3 timed out."),
    "prototype": ((0, 1, 2), "Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (invalid arguments, refusal or environment)."),
    "prototype-wait": ((0, 1, 2, 3), "Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (invalid arguments, refusal or environment), 3 TIMEOUT."),
    "serve": ((0, 2), "Exit codes: 0 stopped (Ctrl+C or SIGTERM), 2 BLOCKED (refused start, port taken or invalid arguments)."),
    "generate": ((0, 2), "Exit codes: 0 script written to stdout, 2 invalid arguments."),
    "custom": ((), ""),
}

# `custom` commands: the full epilog (they do not fit a class). Key: command path.
CUSTOM_EPILOG: dict[tuple[str, ...], str] = {
    ("test-run",): "Exit codes: the exit code of the test command itself (0 only with a positive count of executed tests), 2 BLOCKED, 124 timeout, 127 program not found.",
    ("attribution",): "Exit codes: 0 AI attribution is switched off (or was written), 1 it is not switched off or the settings file cannot be read or written.",
}

# Leaf command path -> class. Every leaf command needs an entry (rule G3).
EXIT_CLASS: dict[tuple[str, ...], str] = {
    # synchronisation
    ("init",): "sync",
    ("import",): "sync",
    ("link",): "sync",
    ("instructions",): "sync",
    ("pull",): "sync",
    ("push",): "sync",
    ("watch",): "sync",
    ("memory-sync",): "sync",
    ("attribution",): "custom",
    ("list",): "sync",
    ("add",): "sync",
    ("skills", "add"): "sync",
    ("skills", "list"): "sync",
    ("skills", "remove"): "sync",
    ("skills", "index"): "sync",
    # checks and context
    ("route",): "no-fail",
    ("doctor",): "standard",
    ("readiness",): "no-fail",
    ("validate",): "standard",
    ("review",): "no-fail",
    ("test-run",): "custom",
    ("smart-test",): "standard",
    ("checkpoint",): "no-fail",
    ("resume",): "no-fail",
    ("metrics",): "no-fail",
    ("benchmark",): "standard",
    # runs and subagents
    ("agent-run", "start"): "no-fail",
    ("agent-run", "eta"): "no-fail",
    ("agent-run", "end"): "no-fail",
    ("review-round", "start"): "no-fail",
    ("review-round", "freeze"): "no-fail",
    ("resources",): "no-fail",
    ("plan-fresh",): "no-fail",
    # workflow extensions
    ("env",): "standard",
    ("e2e",): "standard",
    ("tracker",): "standard",
    ("ci",): "standard",
    ("claim",): "no-fail",
    ("profiles",): "no-fail",
    ("upgrade",): "standard",
    ("guard",): "standard",
    ("suggest",): "no-fail",
    ("map",): "standard",
    ("knowledge", "init"): "no-fail",
    ("status",): "no-fail",
    ("approvals", "wait"): "wait",
    ("approvals", "record"): "no-fail",
    ("plan", "approve"): "no-fail",
    ("plan", "archive"): "no-fail",
    ("plan", "unarchive"): "no-fail",
    ("spec", "approve"): "no-fail",
    ("spec", "snapshot"): "standard",
    ("retro",): "no-fail",
    ("retro-decide",): "no-fail",
    ("retro-scan",): "no-fail",
    ("dispatch",): "no-fail",
    # prototype
    ("prototype", "init"): "no-fail",
    ("prototype", "sync"): "prototype",
    ("prototype", "index"): "no-fail",
    ("prototype", "list"): "no-fail",
    ("prototype", "ids"): "prototype",
    ("prototype", "migrate"): "no-fail",
    ("prototype", "serve"): "serve",
    ("prototype", "user", "add"): "no-fail",
    ("prototype", "user", "passwd"): "no-fail",
    ("prototype", "user", "roles"): "no-fail",
    ("prototype", "user", "remove"): "no-fail",
    ("prototype", "user", "list"): "no-fail",
    ("prototype", "user", "import"): "no-fail",
    ("prototype", "login-link"): "no-fail",
    ("prototype", "iterate"): "prototype",
    ("prototype", "restore"): "no-fail",
    ("prototype", "accept"): "no-fail",
    ("prototype", "gate"): "prototype",
    ("prototype", "comments"): "no-fail",
    ("prototype", "triage"): "no-fail",
    ("prototype", "decide"): "no-fail",
    ("prototype", "resolve"): "no-fail",
    ("prototype", "reply"): "no-fail",
    ("prototype", "writing"): "no-fail",
    ("prototype", "wait"): "prototype-wait",
    ("prototype", "question", "add"): "prototype",
    ("prototype", "question", "list"): "prototype",
    ("prototype", "question", "show"): "prototype",
    ("prototype", "question", "export"): "prototype",
    ("prototype", "question", "record"): "prototype",
    ("prototype", "question", "withdraw"): "prototype",
    ("prototype", "question", "migrate"): "prototype",
    ("prototype", "question", "purge"): "prototype",
    ("prototype", "question", "url"): "prototype",
    ("prototype", "question", "wait"): "prototype-wait",
    ("prototype", "question", "merge"): "prototype",
    ("prototype", "merge-comments"): "no-fail",
    # layers, leaks, machine, dashboard, completion, feedback
    ("leakcheck",): "standard",
    ("leakguard", "patterns"): "standard",
    ("leakguard", "install-hook"): "standard",
    ("layers", "init"): "standard",
    ("layers", "add"): "standard",
    ("layers", "use"): "standard",
    ("layers", "update"): "standard",
    ("layers", "list"): "standard",
    ("layers", "check"): "standard",
    ("layers", "translations"): "standard",
    ("layers", "migrate"): "standard",
    ("setup",): "standard",
    ("dashboard", "show"): "no-fail",
    ("dashboard", "metrics"): "no-fail",
    ("dashboard", "serve"): "serve",
    ("dashboard", "url"): "no-fail",
    ("completion", "bash"): "generate",
    ("completion", "zsh"): "generate",
    ("completion", "powershell"): "generate",
    ("completion", "install"): "standard",
    ("completion", "uninstall"): "standard",
    ("feedback", "new"): "standard",
    ("feedback", "targets"): "standard",
    ("feedback", "trust"): "standard",
    ("feedback", "draft"): "standard",
    ("feedback", "approve"): "standard",
    ("feedback", "send"): "standard",
    ("feedback", "list"): "standard",
    ("feedback", "wait"): "wait",
}


def subparser_actions(parser: argparse.ArgumentParser) -> list:
    """The sub-command actions of a parser (argparse keeps them in a private `_actions`; the only
    production use of that detail, mirrored by `completion.py`)."""
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def exit_codes(class_name: str) -> tuple[int, ...]:
    return EXIT_CLASSES[class_name][0]


def epilog_for(path: tuple[str, ...]) -> str | None:
    """The `Exit codes:` epilog of a leaf command, or None for a command without an entry."""
    class_name = EXIT_CLASS.get(path)
    if class_name == "custom":
        return CUSTOM_EPILOG.get(path)
    if class_name is None:
        return None
    return EXIT_CLASSES[class_name][1]


def finish(parser: argparse.ArgumentParser) -> None:
    """Set the epilog of every leaf command from `EXIT_CLASS` and record `node_key` of every node
    and argument (`parser.node_key`, `action.node_key`). One pass over the tree; no change of any
    flag, default, type or destination."""
    seen: set[int] = set()

    def walk(node: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        node.node_key = path
        subs = subparser_actions(node)
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            action.node_key = (path, action.dest)
        if not subs:
            if path:
                epilog = epilog_for(path)
                if epilog is not None and not node.epilog:
                    node.epilog = epilog
            return
        for action in subs:
            for name, child in action.choices.items():
                if id(child) in seen:
                    continue
                seen.add(id(child))
                walk(child, path + (name,))

    walk(parser, ())
