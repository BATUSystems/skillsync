"""Adapters of the output of test tools (180-A, stage 2): the live state of single tests from the lines a tool prints.

One module per tool in this package (`dotnet.py`, `pytest.py`, ...); the registry discovers the modules by itself, so a new file is enough. An
adapter reads the lines of the command (`feed`) and the report files after it (`end`) and reports what it understood ONLY through `ctx.emit`
(`collected`, `test`, `counts`); the recorder (`test_run.TestRecorder`) owns the event file, the mask, the limits and the exit code. An adapter that
does not understand the output emits nothing, and the run falls back to the counters of the summary and the report. The interface (the five
families of runners, `live`, `partial`, who owns which file) is described in `library/skills/workflow/references/test-run.md`.
"""
from __future__ import annotations

import importlib
import pkgutil
import subprocess
from pathlib import Path

REGISTRY = {}
_DISCOVERED = False
COLLECT_MAX_BYTES = 4 * 1024 * 1024


class Adapter:
    """Base class: override what the tool needs. One instance per run."""
    name = ''
    tools = ()
    # How alive the state of a single test is: 'running' (the tool prints a line when a test STARTS: `emit.test(..., 'running')` is possible), 'completion'
    # (a line only after a test ENDS: no `running` state; a test is `waiting` until its result), 'counts' (only numbers: `emit.counts`). Recorded in `start`.
    live = 'running'
    # `resolve(<name>)` (`--format <name>`) sets `explicit` on the instance it returns; `auto` leaves it False. An `explicit_only` adapter (`tap`) has no `tools`:
    # `auto` never picks it by the tool, only `--format` or another adapter that delegates to it.
    explicit = False
    explicit_only = False

    def accepts(self, argv):
        return True

    def hint(self, argv, has_report=False):
        """Optional (`has_report`: a JUnit report was named with `--junit`): a short sentence for the user about what the command gives the page and how to get more (shown in the adapter notice of `test-run`); None for nothing."""
        return None

    def env(self):
        return {}

    def begin(self, ctx):
        return None

    def feed(self, stream, text, ctx):
        return None

    def partial(self, stream, text, ctx):
        """Optional: the UNFINISHED tail of the current line, each time it grows (pytest `-v` prints `nodeid ` when a test STARTS and `PASSED` when it ends,
        unittest `-v` `test_x (m.C.test_x) ... `). The complete line comes later through `feed`; a start seen here may be emitted as `running`."""
        return None

    def end(self, ctx, exit_code, output, reports):
        """Return True when every test was reported (the reports are not parsed then)."""
        return False


def register(cls):
    """Class decorator: registers an adapter under `cls.name` (a duplicate name is a programming error)."""
    if not cls.name or cls.name in ('auto', 'none'):
        raise ValueError(f'bad adapter name: {cls.name!r}')
    if cls.name in REGISTRY and REGISTRY[cls.name] is not cls:
        raise ValueError(f'duplicate adapter name: {cls.name}')
    REGISTRY[cls.name] = cls
    return cls


def discover():
    """Imports every module of the package once (an import error of one adapter must not break the others)."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    for info in pkgutil.iter_modules(__path__):
        try:
            importlib.import_module(f'{__name__}.{info.name}')
        except Exception:   # a broken adapter is skipped; `--format <its name>` then reports an unknown name
            continue


def names():
    discover()
    return sorted(REGISTRY)


def resolve(fmt, argv, tool):
    """`fmt` (`auto`, `none` or a name) -> a new adapter instance or None. Unknown names raise `ValueError` with the list of the names."""
    discover()
    fmt = fmt or 'auto'
    if fmt == 'none':
        return None
    if fmt != 'auto':
        if fmt not in REGISTRY:
            raise ValueError(f'unknown --format {fmt!r}; known: auto, none, {", ".join(sorted(REGISTRY)) or "(no adapters yet)"}')
        adapter = REGISTRY[fmt]()
        adapter.explicit = True
        return adapter
    for name in sorted(REGISTRY):
        cls = REGISTRY[name]
        try:
            if tool in cls.tools and not cls.explicit_only and cls().accepts(list(argv)):
                return cls()
        except Exception:
            continue
    return None


class Emit:
    """The ONLY way an adapter writes: the recorder's writer does the cleaning, the mask, the limits and the file."""

    def __init__(self, writer):
        self._writer = writer

    def collected(self, total, names=None):
        self._writer.collected(total, names)

    def test(self, id, name, state, group=None, ms=None, msg=None):
        self._writer.test(id, name, state, group=group, ms=ms, msg=msg)

    def counts(self, passed=0, failed=0, skipped=0, running=0, total=None):
        self._writer.counts(passed=passed, failed=failed, skipped=skipped, running=running, total=total)


class AdapterContext:
    def __init__(self, argv, cwd, root, writer, tool=None, argv_core=None):
        self.argv = list(argv)
        self.tool = tool or 'unknown'   # the tool of the command (`test_run.detect_tool`): `playwright` for `npx playwright test`
        self.argv_core = list(argv_core) if argv_core is not None else list(argv)   # argv behind the known wrappers (`test_run.unwrap_command`)
        self.cwd = Path(cwd)
        self.root = Path(root)
        self.emit = Emit(writer)

    def collect(self, argv, timeout=30):
        """Runs a test COLLECTOR (`pytest --collect-only -q`, `cargo test -- --list`...) in the same directory; its output or None. The only process an adapter may start."""
        try:
            done = subprocess.run(list(argv), cwd=str(self.cwd), capture_output=True, timeout=float(timeout), check=False)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if done.returncode != 0:
            return None
        return (done.stdout[:COLLECT_MAX_BYTES]).decode('utf-8', errors='replace')
