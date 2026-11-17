"""CTRL_BREAK as a stop signal of the foreground servers (`dashboard serve`, `prototype serve`) on Windows.

On Windows nobody can send `SIGTERM` from outside (`os.kill(pid, sig)` and `Popen.terminate()` end the process with `TerminateProcess`), and the
one signal a launcher can send to a child started with `CREATE_NEW_PROCESS_GROUP` is `CTRL_BREAK_EVENT`, which the process receives as `SIGBREAK`.
Without a handler the console default handler ends the process at once (exit code 0xC000013A, no `finally`, the state file stays). With the
handler installed here `SIGBREAK` takes the same road as Ctrl+C: `KeyboardInterrupt` in the main thread, then the `finally` of the server
(state files removed, sockets closed) and exit code 0.

Closing the console window (and "End task" of Task Manager for a console application, per the Windows documentation) is a third way: the system sends
`CTRL_CLOSE_EVENT` to a handler registered with `SetConsoleCtrlHandler`, in a NEW thread, and ends the process as soon as the handler returns TRUE
(the documentation lists the limit `SPI_GETHUNGAPPTIMEOUT`, 5000 ms by default, for this event; that the console host honours exactly this value is an
assumption, measured only as far as the cleanup takes 0.1 to 0.6 s). Without a handler the default handler ends it at
once (measured: exit code 0xC000013A, the state file stays). `install_console_close(cleanup_done)` registers such a handler: it hands the stop to the main
thread by the road of `SIGBREAK` and returns TRUE only after the server has finished its cleanup (`cleanup_done`) or the wait ran out.

Nothing here runs on other systems: `install_sigbreak()` and `install_console_close()` return None unless `sys.platform == 'win32'` (Linux and macOS keep
their handlers as they were).
"""
from __future__ import annotations

import _thread
import ctypes
import signal
import sys
import threading
import time

IGNORE_SECONDS = 10.0   # a second CTRL_BREAK this soon after the first is ignored; later ones stop again (a swallowed first one cannot disarm the stop for good)
_stopped_at = None


def _stop_on_sigbreak(_signum, _frame):
    """The first CTRL_BREAK stops the server like Ctrl+C; a second one within `IGNORE_SECONDS` is ignored, so it cannot interrupt the
    cleanup in `finally` (state files, sockets) half way. After that window another CTRL_BREAK stops again."""
    global _stopped_at
    now = time.monotonic()
    if _stopped_at is not None and now - _stopped_at < IGNORE_SECONDS:
        return
    _stopped_at = now
    raise KeyboardInterrupt


def install_sigbreak():
    """Register the `SIGBREAK` handler: Windows and main thread only. Returns `(previous handler,)` (restore it with
    `signal.signal(signal.SIGBREAK, previous[0])` when the server stops) or None when nothing was changed."""
    global _stopped_at
    if sys.platform != 'win32' or threading.current_thread() is not threading.main_thread():
        return None
    try:
        previous = signal.signal(signal.SIGBREAK, _stop_on_sigbreak)
    except (ValueError, OSError):
        return None
    _stopped_at = None
    return (previous if previous is not None else signal.SIG_DFL,)   # None = a handler set outside Python: fall back to the default on restore


CTRL_CLOSE_EVENT = 2
SPI_GETHUNGAPPTIMEOUT = 0x0078
DEFAULT_HUNG_APP_MS = 5000
MAX_WAIT_SECONDS = 4.0     # the wait for the cleanup; the system limit minus one second when that is lower
MIN_WAIT_SECONDS = 0.5
_console = None            # the last registration (module level: the ctypes callback must not be collected while it is registered or still returning)


def close_wait_seconds(limit_ms):
    """How long the handler of the closed console waits for the cleanup: `min(4 s, limit - 1 s)`, never below half a second. `limit_ms` is the system
    "hung application timeout" (None or a nonsense value: the default 5000 ms)."""
    if not isinstance(limit_ms, int) or isinstance(limit_ms, bool) or limit_ms <= 0:
        limit_ms = DEFAULT_HUNG_APP_MS
    return max(MIN_WAIT_SECONDS, min(MAX_WAIT_SECONDS, limit_ms / 1000.0 - 1.0))


def _hung_app_timeout_ms():
    try:
        value = ctypes.c_uint(0)
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETHUNGAPPTIMEOUT, 0, ctypes.byref(value), 0):
            return int(value.value)
    except (AttributeError, OSError, ValueError):
        pass
    return DEFAULT_HUNG_APP_MS


class ConsoleClose:
    """A registered `CTRL_CLOSE_EVENT` handler. `uninstall()` removes it (idempotent)."""

    def __init__(self, cleanup_done, wait_seconds):
        self.cleanup_done = cleanup_done
        self.wait_seconds = wait_seconds
        self.cleanup_started = threading.Event()   # set by the server at the start of its `finally`: a close then only waits, it does not interrupt again
        self.callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(self._handle)   # kept in `self` and in the module (`_console`)
        self.active = False

    def _handle(self, event):
        """Runs in a thread made by the system (not the main one; `Event.wait` makes a dummy `threading` thread object for it, harmless). Any exception is
        swallowed: the process ends right after this returns anyway, and an exception here must not skip the wait for the cleanup."""
        if event != CTRL_CLOSE_EVENT:
            return 0
        if not self.cleanup_started.is_set():   # not while the server already cleans up (Ctrl+C, an earlier CTRL_BREAK): a second interrupt would cut it short
            try:
                _thread.interrupt_main(signal.SIGBREAK)   # the SIGBREAK handler of the main thread raises `KeyboardInterrupt`: the road of Ctrl+C
            except BaseException:   # noqa: BLE001 - nothing may escape into the system thread
                pass
        try:
            self.cleanup_done.wait(self.wait_seconds)
        except BaseException:   # noqa: BLE001
            pass
        return 1

    def uninstall(self):
        global _console
        if self.active:
            self.active = False
            try:
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self.callback, 0)
            except (AttributeError, OSError):
                pass
        # `_console` keeps the last registration (and so its callback) alive on purpose: the system thread of a handler that is still returning must not
        # find its thunk collected; the next `install_console_close` replaces it


def install_console_close(cleanup_done):
    """Register the handler of the closed console window: Windows only, and only when `install_sigbreak()` has put its handler in (the stop is handed to
    the main thread by that road). `cleanup_done` is a `threading.Event` that the server sets when its cleanup is finished. Returns a `ConsoleClose`
    (call `uninstall()` at the end of the server) or None when nothing was registered."""
    global _console
    if sys.platform != 'win32' or signal.getsignal(signal.SIGBREAK) is not _stop_on_sigbreak:
        return None
    try:
        registration = ConsoleClose(cleanup_done, close_wait_seconds(_hung_app_timeout_ms()))
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(registration.callback, 1):
            return None
    except (AttributeError, OSError, ValueError):
        return None
    registration.active = True
    _console = registration
    return registration


def cleanup_begun(registration):
    """The first thing a server does in its `finally`: from now on a closed console window only waits for the cleanup (it does not interrupt it)."""
    if registration is not None:
        registration.cleanup_started.set()


def cleanup_finished(registration, cleanup_done):
    """The last thing a server does in its `finally`: tell the handler of the closed console that the cleanup is done and take the handler out."""
    cleanup_done.set()
    if registration is not None:
        registration.uninstall()
