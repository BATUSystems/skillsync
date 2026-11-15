"""Zakres portów testów dashboardu (MAC-141): `free_port()` bierze porty z 5100–5199 poza 5140 albo z zakresu
w zmiennej SKILLSYNC_TEST_DASHBOARD_PORTS (np. `5110-5124`), żeby kilka biegów testów na jednym hoście nie kolidowało
(każdy bieg dostaje swój wycinek). Produkt bez zmian: serwer dashboardu nadal przyjmuje tylko 5100–5199.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_test_ports.py`.
"""
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import test_skillsync_dashboard_server as ds  # noqa: E402

ENV = 'SKILLSYNC_TEST_DASHBOARD_PORTS'


class FreePortRangeTests(unittest.TestCase):
    def free(self, value):
        env = {key: val for key, val in os.environ.items() if key != ENV}
        if value is not None:
            env[ENV] = value
        with mock.patch.dict(os.environ, env, clear=True):
            return ds.free_port()

    def test_default_range_is_unchanged(self):
        port = self.free(None)
        self.assertTrue(5101 <= port <= 5199 and port != 5140, port)

    def test_the_range_from_the_variable_is_used(self):
        base = self.free(None)  # a port that is free on this host right now (the station may hold servers on 5130-5150)
        for low, high in ((base, base), (base, min(base + 5, 5199)), (max(5101, base - 3), base)):
            with self.subTest(low=low, high=high):
                self.assertTrue(low <= self.free(f'{low}-{high}') <= high)

    def test_the_default_port_5140_stays_excluded_inside_a_range(self):
        with self.assertRaises(RuntimeError) as caught:
            self.free('5140-5140')
        self.assertIn('5140-5140', str(caught.exception))

    def test_a_busy_range_reports_which_range_was_exhausted(self):
        port = self.free(None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        if os.name != 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # the port may be in TIME_WAIT (free_port allows it)
        sock.bind(('127.0.0.1', port))
        sock.listen(1)
        with self.assertRaises(RuntimeError) as caught:
            self.free(f'{port}-{port}')
        self.assertIn(f'{port}-{port}', str(caught.exception))

    def test_an_invalid_range_is_refused_with_a_clear_message(self):
        for value in ('abc', '5100', '5200-5210', '5099-5110', '5150-5140', '5110-5124-5130', ''):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                self.free(value)
            self.assertIn(ENV, str(caught.exception))


    @unittest.skipIf(os.name == 'nt', 'SO_REUSEADDR na Windows pozwala przejąć port z aktywnym nasłuchem')
    def test_a_port_in_time_wait_counts_as_free_like_for_the_server_that_will_bind_it(self):
        """A test server that stops leaves its port in TIME_WAIT; `free_port` must not burn the whole range on those
        (macOS: a slice of 29 ports ran dry in one run), because the server binds with SO_REUSEADDR."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        client = socket.create_connection(('127.0.0.1', port))
        served, _ = listener.accept()
        served.close()  # the active close on the server side leaves `port` in TIME_WAIT
        client.close()
        listener.close()
        with mock.patch.object(ds, 'port_range', return_value=range(port, port + 1)):
            self.assertEqual(ds.free_port(), port)

    @unittest.skipIf(os.name == 'nt', 'SO_REUSEADDR na Windows pozwala przejąć port z aktywnym nasłuchem')
    def test_a_port_with_an_active_listener_is_never_free(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        with mock.patch.object(ds, 'port_range', return_value=range(port, port + 1)):
            with self.assertRaises(RuntimeError):
                ds.free_port()


if __name__ == '__main__':
    unittest.main()
