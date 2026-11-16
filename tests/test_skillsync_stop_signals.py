"""CTRL_BREAK (SIGBREAK) as a stop signal of `dashboard serve` and `prototype serve` on Windows (170-D).

Everywhere: nothing is registered outside Windows (Linux and macOS keep exactly their SIGTERM handling). Windows only (the classes below exist only
there, so no skips on other systems): the handler is installed, raises `KeyboardInterrupt` once in the main thread and ignores a second signal during the
cleanup; a child in its own process group stops cleanly on `CTRL_BREAK_EVENT` (needs a console: skipped with the reason when there is none).
Run: `python3 -m unittest discover -s tests -p test_skillsync_stop_signals.py`.
"""
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import stopsignal  # noqa: E402


class OutsideWindowsTests(unittest.TestCase):
    def test_install_registers_nothing_outside_windows(self):
        for platform in ('linux', 'darwin'):
            with self.subTest(platform), mock.patch.object(stopsignal.sys, 'platform', platform), mock.patch.object(signal, 'signal') as register:
                self.assertIsNone(stopsignal.install_sigbreak())
            register.assert_not_called()

    def test_the_dashboard_registers_only_sigterm_outside_windows(self):
        with mock.patch.object(stopsignal.sys, 'platform', 'linux'), mock.patch.object(signal, 'signal') as register:
            dh._install_stop_handlers()
        self.assertEqual([call.args for call in register.call_args_list], [(signal.SIGTERM, dh._stop_on_sigterm)])

    def test_the_dashboard_registers_sigterm_and_sigbreak_on_windows(self):
        fake_break = getattr(signal, 'SIGBREAK', 21)
        with mock.patch.object(stopsignal.sys, 'platform', 'win32'), mock.patch.object(signal, 'SIGBREAK', fake_break, create=True), \
                mock.patch.object(signal, 'signal') as register:
            dh._install_stop_handlers()
        self.assertEqual([call.args for call in register.call_args_list],
                         [(signal.SIGTERM, dh._stop_on_sigterm), (fake_break, stopsignal._stop_on_sigbreak)])

    def test_a_worker_thread_registers_nothing(self):
        import threading
        results = []
        with mock.patch.object(stopsignal.sys, 'platform', 'win32'), mock.patch.object(signal, 'SIGBREAK', 21, create=True), \
                mock.patch.object(signal, 'signal') as register:
            worker = threading.Thread(target=lambda: results.append(stopsignal.install_sigbreak()))
            worker.start()
            worker.join()
        self.assertEqual(results, [None])
        register.assert_not_called()


class HandlerWindowTests(unittest.TestCase):
    """The handler itself needs no Windows: the first call raises, calls within the window do not, later ones raise again."""

    def test_first_raises_second_within_the_window_is_ignored_later_raises_again(self):
        stopsignal._stopped_at = None
        self.addCleanup(setattr, stopsignal, '_stopped_at', None)
        with mock.patch.object(stopsignal.time, 'monotonic', return_value=100.0):
            with self.assertRaises(KeyboardInterrupt):
                stopsignal._stop_on_sigbreak(21, None)
            self.assertIsNone(stopsignal._stop_on_sigbreak(21, None))
        with mock.patch.object(stopsignal.time, 'monotonic', return_value=100.0 + stopsignal.IGNORE_SECONDS + 0.1):
            with self.assertRaises(KeyboardInterrupt):
                stopsignal._stop_on_sigbreak(21, None)

    def test_a_handler_set_outside_python_is_restored_as_the_default(self):
        with mock.patch.object(stopsignal.sys, 'platform', 'win32'), mock.patch.object(signal, 'SIGBREAK', 21, create=True), \
                mock.patch.object(signal, 'signal', return_value=None):
            self.assertEqual(stopsignal.install_sigbreak(), (signal.SIG_DFL,))


class PrototypeServeInstallsSigbreakTests(unittest.TestCase):
    """`prototype serve` asks for the CTRL_BREAK handler before it announces the address (the same window as for SIGTERM) and puts the previous one back."""

    def test_install_happens_before_serve_forever_and_the_previous_handler_is_restored(self):
        events = []

        class Instance:
            url = 'http://127.0.0.1:1/'
            app = Namespace(warnings=[])

            def serve_forever(self):
                events.append('serve_forever')
                raise KeyboardInterrupt

            def server_close(self):
                events.append('server_close')

        def install():
            events.append('install_sigbreak')
            return ('previous',)

        with tempfile.TemporaryDirectory(prefix='stop-signals-') as tmp:
            repo = Path(tmp)
            (repo / 'docs' / 'prototypes').mkdir(parents=True)
            args = Namespace(no_source_writes=True, users=None, host='127.0.0.1', port=0, secure_cookies=False, trust_proxy=False, login_to_view=False)
            with mock.patch.object(pt.server, 'make_server', return_value=Instance()), mock.patch.object(pt.stopsignal, 'install_sigbreak', side_effect=install), \
                    mock.patch.object(pt.signal, 'SIGBREAK', 21, create=True), mock.patch.object(pt.signal, 'signal') as register:
                report = pt.serve_prototypes(repo, {}, args, io.StringIO())
        self.assertEqual((report['status'], report['stopped']), ('PASS', True), report)
        self.assertLess(events.index('install_sigbreak'), events.index('serve_forever'), events)
        register.assert_any_call(21, 'previous')


if sys.platform == 'win32':
    CHILD = (
        'import sys, time\n'
        'from skillsync_workflow import stopsignal\n'
        'assert stopsignal.install_sigbreak() is not None\n'
        'print("ready", flush=True)\n'
        'try:\n'
        '    while True:\n'
        '        time.sleep(0.05)\n'
        'except KeyboardInterrupt:\n'
        '    pass\n'
        'finally:\n'
        '    open(sys.argv[1], "w").write("cleaned")\n')

    class SigbreakOnWindowsTests(unittest.TestCase):
        def setUp(self):
            self.saved = signal.getsignal(signal.SIGBREAK)
            self.addCleanup(signal.signal, signal.SIGBREAK, self.saved)

        def test_the_handler_is_installed_and_stops_like_ctrl_c_once(self):
            previous = stopsignal.install_sigbreak()
            self.assertIsNotNone(previous)
            self.assertIs(signal.getsignal(signal.SIGBREAK), stopsignal._stop_on_sigbreak)
            with self.assertRaises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGBREAK)
            signal.raise_signal(signal.SIGBREAK)   # a second CTRL_BREAK during the cleanup does not interrupt it
            with mock.patch.object(stopsignal.time, 'monotonic', return_value=stopsignal._stopped_at + stopsignal.IGNORE_SECONDS + 1):
                with self.assertRaises(KeyboardInterrupt):   # after the window another CTRL_BREAK stops again
                    signal.raise_signal(signal.SIGBREAK)

        def test_a_new_install_arms_the_handler_again(self):
            stopsignal.install_sigbreak()
            with self.assertRaises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGBREAK)
            stopsignal.install_sigbreak()
            with self.assertRaises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGBREAK)

        def test_a_child_in_its_own_process_group_stops_cleanly_on_ctrl_break_event(self):
            import ctypes
            if not ctypes.windll.kernel32.GetConsoleProcessList((ctypes.c_ulong * 4)(), 4):   # 0 = proces bez konsoli (ConPTY ma konsolę, także bez okna)
                self.skipTest('tylko na Windows z konsolą: brak konsoli w procesie testowym (CTRL_BREAK_EVENT wymaga wspólnej konsoli)')
            with tempfile.TemporaryDirectory(prefix='stop-signals-') as tmp:
                marker = Path(tmp) / 'cleaned.txt'
                child = subprocess.Popen([sys.executable, '-c', CHILD, str(marker)], cwd=str(ROOT), stdout=subprocess.PIPE, text=True,
                                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                self.addCleanup(lambda: child.poll() is None and child.kill())
                self.addCleanup(child.stdout.close)
                self.assertEqual(child.stdout.readline().strip(), 'ready')
                os.kill(child.pid, signal.CTRL_BREAK_EVENT)
                self.assertEqual(child.wait(timeout=20), 0)
                self.assertEqual(marker.read_text(), 'cleaned')


if __name__ == '__main__':
    unittest.main()
