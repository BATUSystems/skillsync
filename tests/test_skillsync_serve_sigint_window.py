"""Okno startu `prototype serve` i `dashboard serve` (MAC-141, M4): SIGINT (KeyboardInterrupt) między instalacją handlera,
ogłoszeniem adresu a `serve_forever` kończy serwer czysto (PASS, `stopped`), bez tracebacku i rc=-2.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_serve_sigint_window.py`.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype as pt  # noqa: E402


class FakeInstance:
    url = 'http://127.0.0.1:1/'

    def __init__(self, events):
        self.events = events
        self.app = Namespace(warnings=[])
        self.closed = False

    def serve_forever(self):
        self.events.append('serve_forever')
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True
        self.events.append('server_close')


class PrototypeServeWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='serve-window-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / 'docs' / 'prototypes').mkdir(parents=True)
        self.events = []
        self.args = Namespace(no_source_writes=True, users=None, host='127.0.0.1', port=0, secure_cookies=False,
                              trust_proxy=False, login_to_view=False)

    def serve(self, handler):
        instance = FakeInstance(self.events)
        err = io.StringIO()
        real_write = err.write

        def write(text):
            if text.startswith('Serwer prototypów:'):
                self.events.append('announce')
            return real_write(text)

        err.write = write
        with mock.patch.object(pt.server, 'make_server', return_value=instance), \
                mock.patch.object(pt, '_stop_on_sigterm', side_effect=handler):
            report = pt.serve_prototypes(self.repo, {}, self.args, err)
        return report, instance, err.getvalue()

    def test_sigint_while_the_handler_is_installed_stops_cleanly(self):
        def interrupted():
            self.events.append('handler')
            raise KeyboardInterrupt

        report, instance, _text = self.serve(interrupted)
        self.assertEqual((report['status'], report['stopped']), ('PASS', True), report)
        self.assertTrue(instance.closed, 'serwer zamknięty mimo przerwania w oknie startu')

    def test_the_handler_is_installed_before_the_address_is_announced(self):
        def install():
            self.events.append('handler')

        report, instance, _text = self.serve(install)
        self.assertEqual(report['status'], 'PASS')
        self.assertLess(self.events.index('handler'), self.events.index('announce'), self.events)
        self.assertLess(self.events.index('announce'), self.events.index('serve_forever'), self.events)

    def test_the_previous_sigterm_handler_is_restored(self):
        sentinel = object()
        with mock.patch.object(pt.signal, 'signal') as set_signal:
            report, _instance, _text = self.serve(lambda: (sentinel,))
        self.assertEqual(report['status'], 'PASS')
        set_signal.assert_called_with(pt.signal.SIGTERM, sentinel)


class DashboardServeWindowTests(unittest.TestCase):
    def test_sigint_while_logging_the_start_stops_cleanly(self):
        from tests import test_skillsync_dashboard_server as base  # noqa: E402

        case = base.ServeCommandTests('test_help_lists_serve_and_its_flags')
        case.setUp()
        self.addCleanup(case.tearDown)
        calls = []

        def interrupted(message):
            calls.append(message)
            raise KeyboardInterrupt

        for _attempt in range(4):   # `free_port()` only says a port WAS free: a lost bind ends with BLOCKED before the first log line
            args = Namespace(path=str(case.root), layers=[f'core={ROOT / "library"}'], port=base.free_port(), plan=None,
                             host='127.0.0.1', approve_host=[], read_host=[], no_approvals=False, session_url=None, poll=0.2,
                             verbose=False, prototype_port=None)
            out = io.StringIO()
            with mock.patch.object(dh, 'log', side_effect=interrupted), contextlib.redirect_stdout(out):
                code = dh.serve_command(args)
            if calls or 'BLOCKED' not in out.getvalue():
                break
        self.assertEqual(code, 0, out.getvalue())
        report = json.loads(out.getvalue())
        self.assertEqual((report['status'], report['stopped']), ('PASS', True), report)
        self.assertTrue(calls, 'log został wywołany (okno startu)')


if __name__ == '__main__':
    unittest.main()
