"""CTRL_BREAK as a stop signal of the foreground servers (`dashboard serve`, `prototype serve`) on Windows.

On Windows nobody can send `SIGTERM` from outside (`os.kill(pid, sig)` and `Popen.terminate()` end the process with `TerminateProcess`), and the
one signal a launcher can send to a child started with `CREATE_NEW_PROCESS_GROUP` is `CTRL_BREAK_EVENT`, which the process receives as `SIGBREAK`.
Without a handler the console default handler ends the process at once (exit code 0xC000013A, no `finally`, the state file stays). With the
handler installed here `SIGBREAK` takes the same road as Ctrl+C: `KeyboardInterrupt` in the main thread, then the `finally` of the server
(state files removed, sockets closed) and exit code 0.

Nothing here runs on other systems: `install_sigbreak()` returns None unless `sys.platform == 'win32'` (Linux and macOS keep their handlers as they were).
"""
from __future__ import annotations

import signal
import sys
import threading

_stopping = False


def _stop_on_sigbreak(_signum, _frame):
    """The first CTRL_BREAK stops the server like Ctrl+C; a second one while the server is stopping is ignored, so it cannot interrupt
    the cleanup in `finally` (state files, sockets) half way."""
    global _stopping
    if _stopping:
        return
    _stopping = True
    raise KeyboardInterrupt


def install_sigbreak():
    """Register the `SIGBREAK` handler: Windows and main thread only. Returns `(previous handler,)` (restore it with
    `signal.signal(signal.SIGBREAK, previous[0])` when the server stops) or None when nothing was changed."""
    global _stopping
    if sys.platform != 'win32' or threading.current_thread() is not threading.main_thread():
        return None
    try:
        previous = signal.signal(signal.SIGBREAK, _stop_on_sigbreak)
    except (ValueError, OSError):
        return None
    _stopping = False
    return (previous,)
