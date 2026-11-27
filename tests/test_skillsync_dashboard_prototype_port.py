"""Port originu prototypów w dashboardzie: flaga `dashboard serve --prototype-port`, domyślnie port strony + 2 (CORE-2000-UNIFIED U-3a);
`prototype serve` bierze klucz manifestu `prototype.port` jako domyślny port (TECH-140, D2).
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_dashboard_prototype_port.py`.
"""
import json
import os
import socket
import subprocess
import threading
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from tests import test_skillsync_dashboard_server as base  # noqa: E402
from tests.serve_start import kill_tree, start_serve, stop_serve  # noqa: E402

CLI, free_port, srv = base.CLI, base.free_port, base.srv


class SettingsTests(unittest.TestCase):
    def settings(self, value):
        return pt.resolve_prototype_settings({'port': value}, ROOT)

    def test_valid_port_is_kept_and_default_is_none(self):
        self.assertEqual(self.settings(5130)['port'], 5130)
        self.assertIsNone(pt.resolve_prototype_settings({}, ROOT)['port'])
        self.assertIsNone(pt.resolve_prototype_settings(None, ROOT)['port'])
        self.assertEqual([w for w in self.settings(5130)['warnings'] if 'unknown key' in w], [])

    def test_invalid_ports_name_the_key(self):
        for value in (0, 80, 1023, 65536, -1, '5130', 5130.0, True, None):
            errors = self.settings(value)['errors']
            self.assertTrue(errors and errors[0].startswith('prototype.port'), (value, errors))


class DashboardPortTests(base.ServeCommandTests):
    """Dziedziczy setUp (projekt, warstwa); własne testy poniżej, odziedziczone pomijamy."""

    def status_of(self, *args, manifest_extra=''):
        """`(port of the page, prototype.port of /api/status, was page + 2 free before the start)`; the origin must listen while the server runs."""
        if manifest_extra:
            path = self.root / srv.MANIFEST
            path.write_text(path.read_text(encoding='utf-8') + manifest_extra, encoding='utf-8')
        seen = {}

        def prepare(port):
            seen['free'] = not srv.ports.port_taken('127.0.0.1', port + 2)

        proc, port = start_serve(self, CLI, self.root, self.layer, self.env, free_port, *args, prepare=prepare)
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=5) as response:
            data = json.load(response)
        self.assertTrue(data['prototype']['up'], data['prototype'])
        socket.create_connection(('127.0.0.1', data['prototype']['port']), timeout=5).close()
        stop_serve(proc)   # Windows: the whole tree, not only the launcher of the venv
        return port, data['prototype']['port'], seen['free']

    def assert_default_origin(self, page, found, was_free):
        """Kontrakt U-3a: port strony + 2, a zajęty = następny wolny z 5100-5199 (bez 5141)."""
        if was_free:
            self.assertEqual(found, page + 2)
        else:
            self.assertTrue(5100 <= found <= 5199 and found not in (page, 5141), (page, found))

    def test_default_is_the_page_port_plus_two(self):
        # kontrakt U-3a (spec §1): drugie gniazdo tego samego procesu, nie osobny serwer na 5130
        self.assert_default_origin(*self.status_of())

    def test_flag_sets_the_port(self):
        self.assertEqual(self.status_of('--prototype-port', '5131')[1], 5131)

    def test_manifest_port_does_not_move_the_origin_and_flag_wins(self):
        # kontrakt U-3a (D4): `prototype.port` manifestu należy do `prototype serve` i innych zakładek, nie do originu tego serwera
        page, found, was_free = self.status_of(manifest_extra='prototype:\n  port: 5132\n')
        self.assertNotEqual(found, 5132)
        self.assert_default_origin(page, found, was_free)
        self.assertEqual(self.status_of('--prototype-port', '5133')[1], 5133)

    def test_prototype_serve_listens_on_the_manifest_port_by_default(self):
        from skillsync_workflow import prototype_server as ps
        (self.root / 'docs' / 'prototypes').mkdir(parents=True, exist_ok=True)
        users = ps.UsersFile(str(self.root / '.skillsync' / 'prototype' / 'users.json'), iterations=1000)
        ps.add_user(users, 'dev@example.com', roles=['developer'], password='Pyt-' + 'Haslo-' + '5531', name='Dev')
        path = self.root / srv.MANIFEST
        manifest = path.read_text(encoding='utf-8')
        for _attempt in range(4):   # `free_port()` only says a port WAS free: a lost bind ends the server before it announces itself
            port = free_port()
            path.write_text(manifest + 'prototype:\n  port: %d\n' % port, encoding='utf-8')
            proc = subprocess.Popen([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), *self.layer],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
            self.addCleanup(lambda proc=proc: kill_tree(proc))
            watchdog = threading.Timer(30, kill_tree, args=(proc,))
            watchdog.start()
            lines = []
            try:
                for line in proc.stderr:  # the announcement comes after the bind, so it proves the server listens (no polling race)
                    lines.append(line)
                    if line.startswith('Serwer prototypów:'):
                        break
                else:
                    continue   # no announcement: the port was lost (or the server died), try another one
                self.assertIn(':%d/' % port, lines[-1])
                socket.create_connection(('127.0.0.1', port), timeout=5).close()
                return
            finally:
                watchdog.cancel()
                kill_tree(proc)
                proc.communicate(timeout=15)
        self.fail('the server did not announce its address: ' + ''.join(lines))

    def test_help_lists_the_flag(self):
        result = self.cli('dashboard', 'serve', '--help')
        self.assertIn('--prototype-port', result.stdout)
        self.assertIn('5130', result.stdout)

    def test_bad_flag_is_blocked(self):
        for value in ('0', '80', '70000'):
            result = self.cli('dashboard', 'serve', str(self.root), '--port', str(free_port()), '--prototype-port', value,
                              *self.layer)
            self.assertEqual(result.returncode, 2, (value, result.stderr))
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertIn('--prototype-port', report['reason'])


for _name in [n for n in dir(base.ServeCommandTests) if n.startswith('test_') and n not in DashboardPortTests.__dict__]:
    setattr(DashboardPortTests, _name, None)  # odziedziczone testy chodzą w swoim module


if __name__ == '__main__':
    unittest.main()
