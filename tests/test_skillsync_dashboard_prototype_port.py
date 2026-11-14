"""Port serwera prototypów w dashboardzie (TECH-140, D2): flaga `dashboard serve --prototype-port`, klucz manifestu
`prototype.port`, domyślnie 5130; `prototype serve` bierze ten sam klucz jako domyślny port.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_dashboard_prototype_port.py`.
"""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from tests import test_skillsync_dashboard_server as base  # noqa: E402

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
        if manifest_extra:
            path = self.root / srv.MANIFEST
            path.write_text(path.read_text(encoding='utf-8') + manifest_extra, encoding='utf-8')
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(CLI), 'dashboard', 'serve', str(self.root), '--port', str(port),
                                 *self.layer, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.time() + 20
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=2) as response:
                    data = json.load(response)
                proc.terminate()
                proc.communicate(timeout=15)
                return data['prototype']['port']
            except OSError:
                time.sleep(0.1)
        proc.kill()
        self.fail(proc.stderr.read())

    def test_default_is_5130(self):
        self.assertEqual(self.status_of(), 5130)

    def test_flag_sets_the_port(self):
        self.assertEqual(self.status_of('--prototype-port', '5131'), 5131)

    def test_manifest_sets_the_port_and_flag_wins(self):
        self.assertEqual(self.status_of(manifest_extra='prototype:\n  port: 5132\n'), 5132)
        self.assertEqual(self.status_of('--prototype-port', '5133'), 5133)

    def test_prototype_serve_listens_on_the_manifest_port_by_default(self):
        import socket
        from skillsync_workflow import prototype_server as ps
        (self.root / 'docs' / 'prototypes').mkdir(parents=True, exist_ok=True)
        users = ps.UsersFile(str(self.root / '.skillsync' / 'prototype' / 'users.json'), iterations=1000)
        ps.add_user(users, 'dev@example.com', roles=['developer'], password='Pyt-' + 'Haslo-' + '5531', name='Dev')
        port = free_port()
        path = self.root / srv.MANIFEST
        path.write_text(path.read_text(encoding='utf-8') + 'prototype:\n  port: %d\n' % port, encoding='utf-8')
        proc = subprocess.Popen([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), *self.layer],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline, listening = time.time() + 20, False
        while time.time() < deadline and proc.poll() is None and not listening:
            try:
                socket.create_connection(('127.0.0.1', port), timeout=1).close()
                listening = True
            except OSError:
                time.sleep(0.1)
        proc.kill()
        _out, err = proc.communicate(timeout=15)
        self.assertTrue(listening, err)
        self.assertIn(':%d' % port, err)

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
