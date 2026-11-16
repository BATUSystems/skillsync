"""180-C (D3): the closing of the console window on Windows (`CTRL_CLOSE_EVENT`) is handed to the main thread by the road of `SIGBREAK`, and the handler waits
for the cleanup of the server (state files) before it returns TRUE (after which the system ends the process).

On every system: the logic through stand-ins for the platform and for `ctypes` (nothing is registered outside Windows). On Windows only (the class exists
only there, so no skips elsewhere): a real child with a hidden console whose window is closed with `WM_CLOSE`; skipped with the reason when the console is
not a classic one (a pseudoconsole is "for message queue purposes only")."""
import contextlib
import io
import json
import signal
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import stopsignal  # noqa: E402

FAKE_SIGBREAK = 21


@contextlib.contextmanager
def fake_windows(registered=1, hung_ms=5000, handler_is_ours=True):
    """Windows as far as `stopsignal` can tell: the platform, `SIGBREAK`, a `SIGBREAK` handler of ours, `ctypes.windll` and `WINFUNCTYPE` (the callback is the
    function itself, so a test calls it as the system would)."""
    kernel32 = mock.MagicMock()
    kernel32.SetConsoleCtrlHandler.return_value = registered
    windll = mock.MagicMock(kernel32=kernel32)
    ours = stopsignal._stop_on_sigbreak if handler_is_ours else (lambda *_args: None)
    with mock.patch.object(stopsignal.sys, 'platform', 'win32'), mock.patch.object(signal, 'SIGBREAK', FAKE_SIGBREAK, create=True), \
            mock.patch.object(stopsignal.signal, 'getsignal', return_value=ours), mock.patch.object(stopsignal.ctypes, 'windll', windll, create=True), \
            mock.patch.object(stopsignal.ctypes, 'WINFUNCTYPE', lambda *_types: (lambda function: function), create=True), \
            mock.patch.object(stopsignal, '_hung_app_timeout_ms', return_value=hung_ms):
        yield kernel32


class WaitSeconds(unittest.TestCase):
    def test_the_wait_is_four_seconds_or_the_limit_minus_one_and_never_below_half_a_second(self):
        for limit, expected in ((5000, 4.0), (6000, 4.0), (3000, 2.0), (2000, 1.0), (1500, 0.5), (1000, 0.5), (1, 0.5)):
            with self.subTest(limit=limit):
                self.assertEqual(stopsignal.close_wait_seconds(limit), expected)

    def test_an_unknown_limit_means_the_default_of_the_system(self):
        for value in (None, 0, -5, 'x', True, 4.5):
            with self.subTest(value=value):
                self.assertEqual(stopsignal.close_wait_seconds(value), 4.0)


class Registration(unittest.TestCase):
    def tearDown(self):
        if stopsignal._console is not None:
            stopsignal._console.active = False
            stopsignal._console = None

    def test_nothing_is_registered_outside_windows(self):
        for platform in ('linux', 'darwin'):
            with self.subTest(platform), mock.patch.object(stopsignal.sys, 'platform', platform):
                self.assertIsNone(stopsignal.install_console_close(threading.Event()))
                self.assertIsNone(stopsignal._console)

    def test_the_handler_is_registered_with_the_live_callback_and_removed_again(self):
        done = threading.Event()
        with fake_windows(hung_ms=3000) as kernel32:
            registration = stopsignal.install_console_close(done)
            self.assertIsNotNone(registration)
            self.assertIs(stopsignal._console, registration)                      # the module holds the callback while it is registered
            self.assertEqual(registration.wait_seconds, 2.0)                      # the limit of the system minus one second
            kernel32.SetConsoleCtrlHandler.assert_called_once_with(registration.callback, 1)
            registration.uninstall()
            registration.uninstall()                                              # idempotent
            self.assertEqual(kernel32.SetConsoleCtrlHandler.call_args_list[-1].args, (registration.callback, 0))
            self.assertEqual(kernel32.SetConsoleCtrlHandler.call_count, 2)
            self.assertIsNone(stopsignal._console)

    def test_without_the_sigbreak_handler_of_ours_nothing_is_registered(self):
        with fake_windows(handler_is_ours=False) as kernel32:
            self.assertIsNone(stopsignal.install_console_close(threading.Event()))
            kernel32.SetConsoleCtrlHandler.assert_not_called()

    def test_a_refusal_of_the_system_leaves_nothing_registered(self):
        with fake_windows(registered=0):
            self.assertIsNone(stopsignal.install_console_close(threading.Event()))
            self.assertIsNone(stopsignal._console)

    def test_cleanup_finished_sets_the_event_and_removes_the_handler(self):
        done = threading.Event()
        with fake_windows() as kernel32:
            registration = stopsignal.install_console_close(done)
            stopsignal.cleanup_finished(registration, done)
            self.assertTrue(done.is_set())
            self.assertEqual(kernel32.SetConsoleCtrlHandler.call_args_list[-1].args, (registration.callback, 0))
        other = threading.Event()
        stopsignal.cleanup_finished(None, other)                                  # a server that registered nothing
        self.assertTrue(other.is_set())


class TheHandler(unittest.TestCase):
    """What the system thread does on each event."""

    def setUp(self):
        self.done = threading.Event()
        with fake_windows():
            self.registration = stopsignal.install_console_close(self.done)
        self.addCleanup(lambda: setattr(stopsignal, '_console', None))
        self.registration.wait_seconds = 0.3

    def test_other_events_are_left_to_the_default_and_to_python(self):
        for event in (0, 1, 5, 6):                                                # CTRL_C, CTRL_BREAK, LOGOFF, SHUTDOWN
            with self.subTest(event=event), mock.patch.object(stopsignal._thread, 'interrupt_main') as interrupt:
                started = time.monotonic()
                self.assertEqual(self.registration.callback(event), 0)
                self.assertLess(time.monotonic() - started, 0.2)                  # no waiting
                interrupt.assert_not_called()

    def test_close_hands_the_stop_to_the_main_thread_and_returns_true_after_the_cleanup(self):
        with mock.patch.object(stopsignal.signal, 'SIGBREAK', FAKE_SIGBREAK, create=True), mock.patch.object(stopsignal._thread, 'interrupt_main') as interrupt:
            timer = threading.Timer(0.05, self.done.set)
            timer.start()
            self.addCleanup(timer.cancel)
            started = time.monotonic()
            self.assertEqual(self.registration.callback(stopsignal.CTRL_CLOSE_EVENT), 1)
            self.assertLess(time.monotonic() - started, 0.25)                     # back as soon as the cleanup is done, not after the whole wait
            interrupt.assert_called_once_with(FAKE_SIGBREAK)

    def test_a_cleanup_that_never_ends_gives_up_after_the_wait_and_still_returns_true(self):
        with mock.patch.object(stopsignal.signal, 'SIGBREAK', FAKE_SIGBREAK, create=True), mock.patch.object(stopsignal._thread, 'interrupt_main'):
            started = time.monotonic()
            self.assertEqual(self.registration.callback(stopsignal.CTRL_CLOSE_EVENT), 1)
            self.assertGreaterEqual(time.monotonic() - started, 0.25)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertFalse(self.done.is_set())

    def test_an_exception_never_leaves_the_handler_and_the_wait_still_happens(self):
        self.done.set()
        with mock.patch.object(stopsignal.signal, 'SIGBREAK', FAKE_SIGBREAK, create=True), \
                mock.patch.object(stopsignal._thread, 'interrupt_main', side_effect=RuntimeError('boom')):
            self.assertEqual(self.registration.callback(stopsignal.CTRL_CLOSE_EVENT), 1)
        broken = mock.MagicMock()
        broken.wait.side_effect = OSError('boom')
        self.registration.cleanup_done = broken
        with mock.patch.object(stopsignal.signal, 'SIGBREAK', FAKE_SIGBREAK, create=True), mock.patch.object(stopsignal._thread, 'interrupt_main'):
            self.assertEqual(self.registration.callback(stopsignal.CTRL_CLOSE_EVENT), 1)


class ServersCallTheHandlerLast(unittest.TestCase):
    """Both servers set `cleanup_done` (through `cleanup_finished`) as the last thing of their cleanup, after the state files are removed."""

    def test_prototype_serve_finishes_the_cleanup_before_it_tells_the_handler(self):
        events = []
        given = {}

        class Instance:
            url = 'http://127.0.0.1:1/'
            app = Namespace(warnings=[])

            def serve_forever(self):
                events.append('serve_forever')
                raise KeyboardInterrupt

            def server_close(self):
                events.append('server_close')

        def install(done):
            given['done'] = done
            events.append('install_console_close')
            return 'registration'

        def finished(registration, done):
            events.append(('cleanup_finished', registration, done is given['done']))

        with tempfile.TemporaryDirectory(prefix='console-close-') as tmp:
            repo = Path(tmp)
            (repo / 'docs' / 'prototypes').mkdir(parents=True)
            args = Namespace(no_source_writes=True, users=None, host='127.0.0.1', port=0, secure_cookies=False, trust_proxy=False, login_to_view=False)
            with mock.patch.object(pt.server, 'make_server', return_value=Instance()), mock.patch.object(pt.stopsignal, 'install_console_close', side_effect=install), \
                    mock.patch.object(pt.stopsignal, 'cleanup_finished', side_effect=finished):
                report = pt.serve_prototypes(repo, {}, args, io.StringIO())
        self.assertEqual((report['status'], report['stopped']), ('PASS', True), report)
        self.assertEqual(events, ['install_console_close', 'serve_forever', 'server_close', ('cleanup_finished', 'registration', True)])

    def test_dashboard_stop_handlers_register_the_console_handler_only_when_asked(self):
        done = threading.Event()
        with mock.patch.object(dh.stopsignal, 'install_sigbreak'), mock.patch.object(dh.stopsignal, 'install_console_close', return_value='registration') as install:
            self.assertEqual(dh._install_stop_handlers(done), 'registration')
            install.assert_called_once_with(done)
            install.reset_mock()
            self.assertIsNone(dh._install_stop_handlers())
            install.assert_not_called()

    def test_dashboard_serve_tells_the_handler_at_the_end_of_its_cleanup(self):
        from tests import test_skillsync_dashboard_server as base  # noqa: E402

        case = base.ServeCommandTests('test_help_lists_serve_and_its_flags')
        case.setUp()
        self.addCleanup(case.tearDown)
        calls = []

        def interrupted(_message):
            calls.append('log')
            raise KeyboardInterrupt

        for _attempt in range(4):
            args = Namespace(path=str(case.root), layers=[f'core={ROOT / "library"}'], port=base.free_port(), plan=None, host='127.0.0.1', approve_host=[],
                             read_host=[], no_approvals=False, session_url=None, poll=0.2, verbose=False, prototype_port=None)
            out = io.StringIO()
            seen = []
            with mock.patch.object(dh, 'log', side_effect=interrupted), contextlib.redirect_stdout(out), \
                    mock.patch.object(dh.stopsignal, 'install_console_close', return_value='registration') as install, \
                    mock.patch.object(dh.stopsignal, 'cleanup_finished', side_effect=lambda registration, done: seen.append((registration, done))):
                code = dh.serve_command(args)
            if calls or '"BLOCKED"' not in out.getvalue():
                break
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['status'], 'PASS')
        self.assertTrue(calls)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], 'registration')
        self.assertIsInstance(seen[0][1], threading.Event)
        install.assert_called_once_with(seen[0][1])


if sys.platform == 'win32':
    import subprocess

    CHILD = (
        'import sys, threading, time\n'
        'from skillsync_workflow import stopsignal\n'
        'assert stopsignal.install_sigbreak() is not None\n'
        'done = threading.Event()\n'
        'registration = stopsignal.install_console_close(done)\n'
        'assert registration is not None\n'
        'print("ready", flush=True)\n'
        'try:\n'
        '    while True:\n'
        '        time.sleep(0.05)\n'
        'except KeyboardInterrupt:\n'
        '    pass\n'
        'finally:\n'
        '    open(sys.argv[1], "w").write("cleaned")\n'
        '    stopsignal.cleanup_finished(registration, done)\n')

    # Closes the console window of a child like the "Close" button: verified first (a classic console window, owned by the child or a console host, and
    # the console holds only the child and this helper); anything else: nothing is sent.
    CLOSER = r"""
import ctypes, json, os, sys
from ctypes import wintypes as w
k = ctypes.windll.kernel32; u = ctypes.windll.user32
pid = int(sys.argv[1]); out = {'posted': False}
k.FreeConsole()
if not k.AttachConsole(pid):
    out['reason'] = 'AttachConsole failed'; print(json.dumps(out)); sys.exit(0)
hwnd = k.GetConsoleWindow()
buf = ctypes.create_unicode_buffer(256)
if hwnd:
    u.GetClassNameW(hwnd, buf, 256)
owner = w.DWORD(0)
if hwnd:
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
lst = (w.DWORD * 16)(); count = k.GetConsoleProcessList(lst, 16)
members = sorted(lst[i] for i in range(min(count, 16)))
h = k.OpenProcess(0x1000, False, owner.value); image = ''
if h:
    n = w.DWORD(1024); b = ctypes.create_unicode_buffer(1024)
    if k.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
        image = os.path.basename(b.value).lower()
    k.CloseHandle(h)
out.update(klass=buf.value, owner=owner.value, image=image, members=members)
if not hwnd or buf.value != 'ConsoleWindowClass':
    out['reason'] = 'not a classic console window'
elif owner.value != pid and image not in ('conhost.exe', 'openconsole.exe'):
    out['reason'] = 'the window belongs to another process'
elif set(members) != {pid, os.getpid()}:
    out['reason'] = 'the console is not exclusively ours'
else:
    out['posted'] = True
    print(json.dumps(out), flush=True)
    u.PostMessageW(hwnd, 0x0010, 0, 0)
    import time; time.sleep(1)
    sys.exit(0)
print(json.dumps(out), flush=True)
"""

    class ClosingTheConsoleWindowOnWindows(unittest.TestCase):
        def test_a_child_with_a_hidden_console_cleans_up_when_its_window_is_closed(self):
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0                                              # SW_HIDE
            with tempfile.TemporaryDirectory(prefix='console-close-') as tmp:
                marker = Path(tmp) / 'cleaned.txt'
                child = subprocess.Popen([sys.executable, '-c', CHILD, str(marker)], cwd=str(ROOT), stdout=subprocess.PIPE, text=True,
                                         creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
                self.addCleanup(lambda: child.poll() is None and child.kill())
                self.addCleanup(child.stdout.close)
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                done = subprocess.run([sys.executable, '-c', CLOSER, str(child.pid)], capture_output=True, text=True, timeout=60)
                lines = [line for line in done.stdout.splitlines() if line.startswith('{')]
                info = json.loads(lines[0]) if lines else {'posted': False, 'reason': done.stderr[-200:]}
                if not info.get('posted'):
                    child.kill()
                    self.skipTest('tylko na Windows z klasyczną konsolą: okna konsoli nie da się zamknąć komunikatem (%s)' % info.get('reason'))
                child.wait(timeout=20)
                self.assertEqual(marker.read_text(), 'cleaned')


if __name__ == '__main__':
    unittest.main()
