"""Calls of the `skillsync` entry point inside the test process (180-K): the same answer as `python skillsync <argv>` without paying for the start of an
interpreter and the import of the package on every call (0.4-0.7 s, thousands of times in a full run).

`run_cli(argv, cwd=, env=)` returns a `subprocess.CompletedProcess` (bytes decoded as UTF-8, `errors='replace'`) like `subprocess.run(..., capture_output=True,
text=True)` of the real wrapper. What it isolates, and restores after every call, even when the command fails:
- the working directory (`os.chdir`), the whole of `os.environ` (replaced by `{**os.environ, **env}`, like the environment of a child process),
  `sys.argv`, `sys.path`, `sys.stdout`, `sys.stderr`, the handlers of SIGINT/SIGTERM (and SIGBREAK on Windows; main thread only);
- modules that the call imported from outside the package (a module loaded from the working directory must not stay for the next test); modules of
  `skillsync_workflow` stay loaded: that is the whole gain, and the product keeps no state between calls (checked by `tests/test_skillsync_cli_inprocess.py`).
`SystemExit` gives the return code (`None` -> 0, an `int`, any other value is printed to stderr and gives 1 like the interpreter does); any other
exception is printed as a traceback to the captured stderr and gives 1, so the assertions on a `Traceback` in stderr keep their meaning.
Beyond what `retro-scan` uses, `cli.py` itself keeps module state (`CONFIG_PATH` is computed at import, `_WARNED_NO_SYMLINK` warns once per process, `_composed_lock`): check
it before the helper is used for another command (`link`, `push`...).
NOT for tests that need a process boundary: a command that can hang (a FIFO, a lock) needs a timeout that only a subprocess gives; signals sent to the
command, the exit code of the interpreter itself, and tests of the entry point (`skillsync`, `python -m skillsync_workflow`) stay subprocesses."""
import faulthandler
import io
import os
import signal
import subprocess
import sys
import threading
import traceback

PACKAGE = 'skillsync_workflow'
HANG_LIMIT_S = 120   # a call that does not return stops the whole run with the traceback of the stuck thread (instead of waiting for the limit of the CI job)
_SIGNALS = [getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGBREAK') if hasattr(signal, name)]


class _Stream(io.TextIOWrapper):
    """A UTF-8 text stream over bytes that `ensure_utf8_streams` may reconfigure, like the real standard streams of a child process."""

    def __init__(self):
        super().__init__(io.BytesIO(), encoding='utf-8', errors='backslashreplace', newline='\n', write_through=True)   # like a pipe: LF as written, no CRLF on Windows

    def text(self):
        self.flush()
        return self.buffer.getvalue().decode('utf-8', errors='replace')


def _restore_modules(before, roots):
    """Drops the modules that the call imported from `roots` (the working directory and the paths it put on `sys.path`). The standard library, the
    installed packages and the package itself stay loaded: dropping `http.server` would give the next import a second copy of it."""
    for name in list(sys.modules):
        if name in before:
            continue
        origin = getattr(sys.modules[name], '__file__', None)
        if origin and any(os.path.abspath(origin).startswith(root + os.sep) for root in roots):
            del sys.modules[name]


def run_cli(argv, cwd=None, env=None):
    """Runs `skillsync <argv...>` in this process; see the module docstring for what is isolated and what is not."""
    from skillsync_workflow import cli
    argv = [str(item) for item in argv]
    saved_cwd, saved_env, saved_argv, saved_path = os.getcwd(), dict(os.environ), list(sys.argv), list(sys.path)
    saved_streams = (sys.stdout, sys.stderr)
    main_thread = threading.current_thread() is threading.main_thread()
    saved_handlers = {number: signal.getsignal(number) for number in _SIGNALS} if main_thread else {}
    modules_before = set(sys.modules)
    out, err = _Stream(), _Stream()
    code = 0
    faulthandler.dump_traceback_later(HANG_LIMIT_S, exit=True)
    try:
        if cwd is not None:
            os.chdir(cwd)
        # a child process of the test has no terminal: argparse wraps its text at 80 columns there, so it does here (a wide terminal must not change it)
        given = {key: str(value) for key, value in (env or {}).items()}
        os.environ.clear()
        os.environ.update({**{k: v for k, v in saved_env.items() if k != 'COLUMNS'}, 'COLUMNS': '80', **given})
        sys.argv = ['skillsync', *argv]
        sys.stdout, sys.stderr = out, err
        try:
            cli.ensure_utf8_streams()
            result = cli.main()
            code = 0 if result is None else result
        except SystemExit as exit_:
            if exit_.code is None:
                code = 0
            elif isinstance(exit_.code, int):
                code = exit_.code
            else:
                print(exit_.code, file=sys.stderr)
                code = 1
        except KeyboardInterrupt:
            raise   # a Ctrl+C stops the run of the tests, as it would stop a child (the state is restored below)
        except BaseException:   # the interpreter prints the traceback of an uncaught exception to stderr and exits with 1
            traceback.print_exc(file=sys.stderr)
            code = 1
    finally:
        faulthandler.cancel_dump_traceback_later()
        added = [os.path.abspath(entry) for entry in sys.path if entry and entry not in saved_path]
        sys.stdout, sys.stderr = saved_streams
        sys.argv = saved_argv
        sys.path[:] = saved_path
        here = os.path.abspath(os.getcwd())
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        for number, handler in saved_handlers.items():
            signal.signal(number, handler)
        _restore_modules(modules_before, [here, *added])
    return subprocess.CompletedProcess(['skillsync', *argv], code, out.text(), err.text())
