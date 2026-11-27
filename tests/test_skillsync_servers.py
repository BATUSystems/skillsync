"""CORE-1200-SERVERS: `skillsync servers list|stop` (spec docs/specs/2026-11-26-core-1200-servers-rejestr-i-zatrzymanie.md, K1-K8).
Nothing here touches a real state directory or a real server of the user: the state directories are temporary, the identity, the signals and the clock are fakes,
and the one test with a real process starts and ends its own child."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import dashboard_token, servers  # noqa: E402

SERVER_CMD = 'python -m skillsync_workflow dashboard serve --port 5100'
PROTO_CMD = 'skillsync prototype serve --port 4100'


def dash(pid=4001, port=5100, alive=True, role='both', approvals=True, root='/p/one'):
    return {'kind': 'dashboard', 'pid': pid, 'port': port, 'host': '127.0.0.1', 'role': role, 'root': root, 'project': root, 'startedAt': '2026-11-26T10:00:00+00:00',
            'tabs': 1, 'alive': alive, 'approvals': approvals, '_path': f'/state/{port}.json', '_instance': None, '_recorded': 1000.0}


def proto(pid=4002, port=4100, alive=True, root='/p/one'):
    return {'kind': 'prototype', 'pid': pid, 'port': port, 'host': '127.0.0.1', 'role': None, 'root': root + '/prototypes', 'project': root, 'startedAt': None,
            'tabs': None, 'alive': alive, 'approvals': False, '_path': f'/state/server-{port}.json', '_instance': 'i', '_recorded': 1000.0}


class FakeDeps:
    """A fake system: `records` is the registry; `stops` says which pids obey the gentle stop; the clock moves only in `sleep`."""

    def __init__(self, records, identities=None, stops=(), kill_works=True, terminate_works=True):
        self.recs = list(records)
        self.idents = identities if identities is not None else {r['pid']: {'cmdline': SERVER_CMD if r['kind'] == 'dashboard' else PROTO_CMD, 'started': 900.0} for r in records}
        self.stops = set(stops)
        self.kill_works = kill_works
        self.terminate_works = terminate_works
        self.sent, self.killed, self.removed, self.gone_pids, self.now = [], [], [], set(), 0.0

    def records(self, roots=()):
        return [dict(r) for r in self.recs]

    def identity(self, pid):
        return self.idents.get(pid)

    def terminate(self, pid):
        self.sent.append(pid)
        if pid in self.stops:
            self.gone_pids.add(pid)
        return self.terminate_works

    def kill(self, pid):
        self.killed.append(pid)
        if self.kill_works:
            self.gone_pids.add(pid)
        return self.kill_works

    def gone(self, record):
        return record['pid'] in self.gone_pids

    def remove(self, record):
        self.removed.append(record['_path'])

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class ListTests(unittest.TestCase):
    def test_the_list_has_both_kinds_without_private_fields_or_a_token(self):
        deps = FakeDeps([dash(), proto(), dash(pid=4003, port=5101, alive=False)])
        listed = servers.list_servers(deps=deps)
        self.assertEqual([(r['kind'], r['port'], r['alive']) for r in listed], [('dashboard', 5100, True), ('prototype', 4100, True), ('dashboard', 5101, False)])
        for record in listed:
            self.assertFalse([key for key in record if key.startswith('_') or key == 'token'], record)
        self.assertEqual([r['kind'] for r in servers.list_servers('prototype', deps=deps)], ['prototype'])

    def test_the_registry_reads_state_files_read_only_and_never_copies_a_token(self):
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            directory = Path(tmp)
            alive = {'schema': 2, 'pid': os.getpid(), 'port': 5190, 'host': '127.0.0.1', 'root': '/p/one', 'startedAt': '2026-11-26T10:00:00+00:00',
                     'approvals': True, 'token': 'SECRET-TOKEN-VALUE', 'kind': 'project', 'serverId': 'sid', 'tabs': [{'id': 't', 'path': '/p/one', 'token': 'TAB-SECRET'}]}
            dead = dict(alive, pid=2 ** 22 + 12345, port=5191)
            (directory / 'aaaa-5190.json').write_text(json.dumps(alive), encoding='utf-8')
            (directory / 'bbbb-5191.json').write_text(json.dumps(dead), encoding='utf-8')
            (directory / 'junk.json').write_text('{not json', encoding='utf-8')
            with mock.patch.object(dashboard_token, 'state_dir', return_value=directory), \
                 mock.patch.object(dashboard_token, '_pid_alive', side_effect=lambda pid: pid == os.getpid()), \
                 mock.patch.object(servers, '_windows', return_value=False):
                records = servers._dashboard_records()
            self.assertEqual({(r['port'], r['alive']) for r in records}, {(5190, True), (5191, False)})
            self.assertNotIn('SECRET', json.dumps([servers.public(r) for r in records]))
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ['aaaa-5190.json', 'bbbb-5191.json', 'junk.json'])   # nothing removed by a listing


class StopTests(unittest.TestCase):
    def stop(self, deps, **kw):
        selector = {'root': kw.pop('root', []), 'port': kw.pop('port', None), 'all': kw.pop('all', False), 'kind': kw.pop('kind', None)}
        return servers.stop_servers(selector, deps=deps, **kw)

    def test_nothing_chosen_stops_nothing(self):
        deps = FakeDeps([dash()])
        result = self.stop(deps)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual((deps.sent, deps.killed, deps.removed), ([], [], []))

    def test_a_port_stop_signals_the_right_pid_and_waits_for_it(self):
        deps = FakeDeps([dash(pid=4001, port=5100), dash(pid=4003, port=5101)], stops={4001})
        result = self.stop(deps, port=5100)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual([(i['pid'], i['outcome']) for i in result['items']], [(4001, 'stopped')])
        self.assertEqual((deps.sent, deps.killed), ([4001], []))

    def test_a_server_that_does_not_stop_is_reported_and_not_killed(self):
        deps = FakeDeps([dash()])
        result = self.stop(deps, port=5100, timeout=2.0)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['items'][0]['outcome'], 'still-running')
        self.assertEqual((deps.sent, deps.killed), ([4001], []))

    def test_force_ends_a_stuck_server_only_after_the_identity_check(self):
        deps = FakeDeps([dash()])
        result = self.stop(deps, port=5100, timeout=1.0, force=True)
        self.assertEqual((result['status'], result['items'][0]['outcome']), ('PASS', 'stopped'))
        self.assertEqual(deps.killed, [4001])
        self.assertEqual(deps.removed, ['/state/5100.json'])
        failing = FakeDeps([dash()], kill_works=False)
        again = self.stop(failing, port=5100, timeout=1.0, force=True)
        self.assertEqual((again['status'], again['items'][0]['outcome']), ('FAIL', 'still-running'))

    def test_a_reused_pid_is_never_signalled(self):
        for name, ident in (('another program', {'cmdline': 'vim notes.txt', 'started': 900.0}), ('wrong kind', {'cmdline': PROTO_CMD, 'started': 900.0}),
                            ('started after the record', {'cmdline': SERVER_CMD, 'started': 5000.0}), ('no such process', None)):
            with self.subTest(name):
                deps = FakeDeps([dash()], identities={4001: ident})
                result = self.stop(deps, port=5100, timeout=1.0, force=True)
                self.assertEqual(result['items'][0]['outcome'], 'refused')
                self.assertEqual((deps.sent, deps.killed, deps.removed), ([], [], []))
                self.assertEqual(result['status'], 'BLOCKED')

    def test_a_dead_entry_is_swept_without_a_signal_and_a_dry_run_changes_nothing(self):
        deps = FakeDeps([dash(alive=False)])
        dry = self.stop(deps, all=True, dry_run=True)
        self.assertEqual((dry['items'][0]['outcome'], deps.removed, deps.sent), ('dead-swept', [], []))
        real = self.stop(deps, all=True)
        self.assertEqual((real['items'][0]['outcome'], deps.removed, deps.sent), ('dead-swept', ['/state/5100.json'], []))

    def test_a_dry_run_signals_nothing_and_the_selection_filters(self):
        deps = FakeDeps([dash(), proto(), dash(pid=4003, port=5101, root='/p/two')], stops={4001, 4002, 4003})
        dry = self.stop(deps, all=True, dry_run=True)
        self.assertEqual(len(dry['items']), 3)
        self.assertEqual((deps.sent, deps.killed), ([], []))
        only = self.stop(FakeDeps([dash(), proto()], stops={4001, 4002}), all=True, kind='prototype')
        self.assertEqual([i['kind'] for i in only['items']], ['prototype'])
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            root = str(Path(tmp).resolve())
            by_root = self.stop(FakeDeps([dash(root=root, pid=4001), dash(root='/p/other', pid=4003, port=5101)], stops={4001}), root=[root])
            self.assertEqual([i['pid'] for i in by_root['items']], [4001])

    def test_the_result_warns_about_servers_that_accept_consents_and_the_gentle_stop_that_cannot_be_sent_is_a_refusal(self):
        deps = FakeDeps([dash(approvals=True, role='both'), dash(pid=4003, port=5101, approvals=False)], stops={4001, 4003})
        result = self.stop(deps, all=True)
        self.assertTrue(result['items'][0]['warnings'])
        self.assertFalse(result['items'][1]['warnings'])
        no_send = FakeDeps([dash()], terminate_works=False)
        refused = self.stop(no_send, port=5100)
        self.assertEqual((refused['items'][0]['outcome'], refused['status']), ('refused', 'BLOCKED'))
        self.assertEqual(no_send.killed, [])

    def test_a_server_with_two_state_files_is_signalled_once_and_both_report_the_same_outcome(self):
        class Dying(FakeDeps):   # Windows: after the process ended, nothing is known about its pid
            def identity(self, pid):
                return None if pid in self.gone_pids else super().identity(pid)
        for fake in (FakeDeps, Dying):
            with self.subTest(fake=fake.__name__):
                deps = fake([dash(port=5100, role='read', approvals=False), dash(port=5101, role='approve')], stops={4001})
                result = self.stop(deps, all=True)
                self.assertEqual((result['status'], [i['outcome'] for i in result['items']]), ('PASS', ['stopped', 'stopped']))
                self.assertEqual(deps.sent, [4001])
                self.assertEqual([bool(i['warnings']) for i in result['items']], [False, True])
        stuck = FakeDeps([dash(port=5100, role='read'), dash(port=5101, role='approve')])
        result = self.stop(stuck, all=True, timeout=0)
        self.assertEqual((result['status'], [i['outcome'] for i in result['items']], stuck.sent), ('FAIL', ['still-running', 'still-running'], [4001]))

    def test_sweep_dead_removes_only_dead_entries(self):
        deps = FakeDeps([dash(alive=False), dash(pid=4003, port=5101), proto(alive=False)])
        result = servers.sweep_dead(kind='dashboard', deps=deps)
        self.assertEqual([(i['kind'], i['port']) for i in result['items']], [('dashboard', 5100)])
        self.assertEqual((deps.removed, deps.sent), (['/state/5100.json'], []))


@unittest.skipIf(os.name == 'nt', 'POSIX signals; on Windows the stop is covered by WindowsRealConsoleTests')
class RealProcessTests(unittest.TestCase):
    def test_a_real_child_is_stopped_gently_and_a_stranger_is_left_alone(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', 'skillsync', 'dashboard', 'serve'])
        stranger = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
                directory = Path(tmp)
                record = {'schema': 1, 'pid': child.pid, 'port': 5192, 'host': '127.0.0.1', 'root': '/p/one', 'startedAt': '2999-01-01T00:00:00+00:00', 'approvals': False}
                (directory / 'cccc-5192.json').write_text(json.dumps(record), encoding='utf-8')
                (directory / 'dddd-5193.json').write_text(json.dumps(dict(record, pid=stranger.pid, port=5193)), encoding='utf-8')

                class Deps(servers.Deps):
                    def gone(self, rec):
                        return (child.poll() is not None) if rec['pid'] == child.pid else stranger.poll() is not None

                with mock.patch.object(dashboard_token, 'state_dir', return_value=directory), mock.patch.object(servers, 'project_roots', return_value=[]):
                    result = servers.stop_servers({'all': True, 'root': [], 'port': None, 'kind': None}, timeout=10.0, deps=Deps())
            outcomes = {item['pid']: item['outcome'] for item in result['items']}
            self.assertEqual(outcomes[child.pid], 'stopped')
            self.assertEqual(outcomes[stranger.pid], 'refused')
            self.assertIsNotNone(child.poll())
            self.assertIsNone(stranger.poll())
        finally:
            for process in (child, stranger):
                if process.poll() is None:
                    process.kill()
                process.wait()

    def test_the_identity_of_a_real_process_reads_the_command_line_and_the_start_time(self):
        identity = servers.process_identity(os.getpid())
        self.assertIsNotNone(identity)
        self.assertIn('python', identity['cmdline'].lower())
        self.assertIsNone(servers.process_identity(2 ** 22 + 54321))
        self.assertTrue(servers.identity_matches('dashboard', {'cmdline': 'skillsync dashboard serve', 'started': 10.0}, 20.0))
        self.assertFalse(servers.identity_matches('dashboard', {'cmdline': 'skillsync dashboard serve', 'started': 100.0}, 20.0))


class IdentityTests(unittest.TestCase):
    def test_a_missing_time_or_a_loose_command_line_is_a_refusal(self):
        good = {'cmdline': SERVER_CMD, 'started': 900.0}
        self.assertTrue(servers.identity_matches('dashboard', good, 1000.0))
        self.assertFalse(servers.identity_matches('dashboard', dict(good, started=None), 1000.0))
        self.assertFalse(servers.identity_matches('dashboard', good, None))
        self.assertFalse(servers.identity_matches('dashboard', {'cmdline': 'skillsyncfoo dashboard serve', 'started': 900.0}, 1000.0))
        self.assertFalse(servers.identity_matches('dashboard', {'cmdline': 'python -m http.server --directory skillsync', 'started': 900.0}, 1000.0))
        self.assertFalse(servers.identity_matches('dashboard', {'cmdline': 'skillsync dashboard observe', 'started': 900.0}, 1000.0))
        self.assertFalse(servers.identity_matches('dashboard', {'cmdline': 'skillsync prototype serve', 'started': 900.0}, 1000.0))
        with mock.patch.object(servers, '_windows', return_value=True):
            self.assertTrue(servers.identity_matches('prototype', {'cmdline': 'C:\\tools\\skillsync.exe prototype serve --port 1', 'started': 5.0}, 5.0))

    def run_ident(self, platform, windows, stdout, rc=0):
        done = subprocess.CompletedProcess([], rc, stdout=stdout, stderr=b'')
        with mock.patch.object(sys, 'platform', platform), mock.patch.object(servers, '_windows', return_value=windows), \
             mock.patch.object(subprocess, 'run', return_value=done):
            return servers.process_identity(4242)

    def test_the_output_of_ps_and_powershell_is_read(self):
        mac = self.run_ident('darwin', False, b'Thu Nov 26 13:00:00 2026 /usr/local/bin/skillsync dashboard serve --port 5100\n')
        self.assertIn('skillsync dashboard serve', mac['cmdline'])
        self.assertIsInstance(mac['started'], float)
        win = self.run_ident('win32', True, b'C:\\Python\\python.exe -m skillsync_workflow dashboard serve\r\n1795000000\r\n')
        self.assertEqual(win['started'], 1795000000.0)
        self.assertIn('dashboard serve', win['cmdline'])
        self.assertIsNone(self.run_ident('win32', True, b'', rc=1))
        self.assertIsNone(self.run_ident('darwin', False, b'', rc=1))


class WindowsTests(unittest.TestCase):
    def test_a_server_that_does_not_answer_but_is_the_server_of_the_record_is_not_dead(self):
        ident = {'cmdline': SERVER_CMD, 'started': 900.0}
        with mock.patch.object(servers, 'process_identity', return_value=ident):
            self.assertTrue(servers._windows_alive('dashboard', False, 4001, 1000.0))
        with mock.patch.object(servers, 'process_identity', return_value=None):
            self.assertFalse(servers._windows_alive('dashboard', False, 4001, 1000.0))
        with mock.patch.object(servers, 'process_identity', return_value={'cmdline': 'notepad', 'started': 900.0}):
            self.assertFalse(servers._windows_alive('dashboard', False, 4001, 1000.0))
        self.assertTrue(servers._windows_alive('dashboard', True, 4001, 1000.0))

    def test_the_registry_uses_the_answer_and_the_identity_on_windows(self):
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            directory = Path(tmp)
            record = {'schema': 2, 'pid': 4001, 'port': 5195, 'host': '127.0.0.1', 'root': '/p/one', 'startedAt': '1970-01-01T00:16:40+00:00', 'approvals': False,
                      'kind': 'project', 'serverId': 'sid', 'tabs': []}
            (directory / 'eeee-5195.json').write_text(json.dumps(record), encoding='utf-8')
            ident = {'cmdline': SERVER_CMD, 'started': 900.0}
            with mock.patch.object(dashboard_token, 'state_dir', return_value=directory), mock.patch.object(servers, '_windows', return_value=True), \
                 mock.patch.object(servers, 'process_identity', return_value=ident):
                hung = servers._dashboard_records(healthy=lambda *a: False)
                self.assertTrue(hung[0]['alive'])
            with mock.patch.object(dashboard_token, 'state_dir', return_value=directory), mock.patch.object(servers, '_windows', return_value=True), \
                 mock.patch.object(servers, 'process_identity', return_value=None):
                self.assertFalse(servers._dashboard_records(healthy=lambda *a: False)[0]['alive'])

    def test_the_break_helper_reports_its_exit_code_and_leaves_the_signal_handler_alone(self):
        for rc, expected in ((0, True), (1, False)):
            with mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], rc, b'', b'')) as run:
                self.assertIs(servers._send_ctrl_break(4001), expected)
                self.assertEqual(run.call_args.args[0][-1], '4001')
        with mock.patch.object(subprocess, 'run', side_effect=OSError('no')):
            self.assertFalse(servers._send_ctrl_break(4001))

    def test_the_break_is_ignored_by_the_stopping_process_only_while_it_is_sent(self):
        calls = []

        class FakeSignal:
            SIGBREAK = 21
            SIG_IGN = 'ignore'

            @staticmethod
            def signal(number, handler):
                calls.append((number, handler))
                return 'previous-handler'

        with mock.patch.object(servers, 'signal', FakeSignal), mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            self.assertTrue(servers._send_ctrl_break(4001))
        self.assertEqual(calls, [(21, 'ignore'), (21, 'previous-handler')])
        calls.clear()
        with mock.patch.object(servers, 'signal', FakeSignal), mock.patch.object(subprocess, 'run', side_effect=OSError('no')):
            self.assertFalse(servers._send_ctrl_break(4001))
        self.assertEqual(calls, [(21, 'ignore'), (21, 'previous-handler')])   # restored also when the helper could not start

    def test_deps_on_windows_use_the_break_the_taskkill_and_the_identity(self):
        deps = servers.Deps()
        with mock.patch.object(servers, '_windows', return_value=True), mock.patch.object(servers, '_send_ctrl_break', return_value=True) as brk:
            self.assertIs(deps.terminate(4001), True)
            brk.assert_called_once_with(4001)
        with mock.patch.object(servers, '_windows', return_value=True), mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            self.assertTrue(deps.kill(4001))
            self.assertEqual(run.call_args.args[0], ['taskkill', '/F', '/PID', '4001'])
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            record = {'pid': 4001, '_path': str(Path(tmp) / 'gone.json')}
            with mock.patch.object(servers, '_windows', return_value=True):
                with mock.patch.object(servers, 'process_identity', return_value={'cmdline': 'x', 'started': 1.0}):
                    self.assertFalse(deps.gone(record))   # the file is gone, the process is not
                with mock.patch.object(servers, 'process_identity', return_value=None):
                    self.assertTrue(deps.gone(record))


@unittest.skipUnless(os.name == 'nt', 'windows-only: CTRL_BREAK needs a real Windows console')
class WindowsRealConsoleTests(unittest.TestCase):
    """The real helper against real processes, each in its own console (the one thing the fakes cannot show)."""

    CHILD = ("import signal, sys, time\n"
             "from pathlib import Path\n"
             "def got(*_):\n    Path(sys.argv[1]).write_text('break')\n    sys.exit(0)\n"
             "signal.signal(signal.SIGBREAK, got)\n"
             "Path(sys.argv[2]).write_text('ready')\n"
             "for _ in range(600):\n    time.sleep(0.1)\n")

    def test_the_break_reaches_only_that_console_and_the_helper_reports_success(self):
        with tempfile.TemporaryDirectory(prefix='servers-', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            children = [subprocess.Popen([sys.executable, '-c', self.CHILD, str(base / f'{name}.got'), str(base / f'{name}.ready')],
                                         creationflags=subprocess.CREATE_NEW_CONSOLE) for name in ('server', 'bystander')]
            try:
                deadline = time.monotonic() + 30
                while not all((base / f'{name}.ready').exists() for name in ('server', 'bystander')) and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertIs(servers._send_ctrl_break(children[0].pid), True)
                self.assertEqual(children[0].wait(timeout=15), 0)
                self.assertTrue((base / 'server.got').exists())
                self.assertIsNone(children[1].poll(), 'the other console must not get the event')
                self.assertFalse((base / 'bystander.got').exists())
            finally:
                for child in children:
                    if child.poll() is None:
                        subprocess.run(['taskkill', '/F', '/T', '/PID', str(child.pid)], capture_output=True, check=False)
                    child.wait(timeout=15)


class PosixDepsTests(unittest.TestCase):
    def test_terminate_tells_a_vanished_process_from_a_refused_signal(self):
        deps = servers.Deps()
        with mock.patch.object(servers, '_windows', return_value=False):
            with mock.patch.object(os, 'kill', side_effect=ProcessLookupError()):
                self.assertEqual(deps.terminate(4001), 'gone')
            with mock.patch.object(os, 'kill', side_effect=PermissionError()):
                self.assertIs(deps.terminate(4001), False)
            with mock.patch.object(os, 'kill') as kill:
                self.assertIs(deps.terminate(4001), True)
                kill.assert_called_once()

    def test_remove_deletes_only_the_file_that_was_read(self):
        deps = servers.Deps()
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            path = Path(tmp) / 'x-5100.json'
            path.write_text(json.dumps({'pid': 4001, 'port': 5100}), encoding='utf-8')
            deps.remove({'pid': 9999, '_path': str(path), '_instance': None})   # another server started on this port meanwhile
            self.assertTrue(path.exists())
            deps.remove({'pid': 4001, '_path': str(path), '_instance': None})
            self.assertFalse(path.exists())
            deps.remove({'pid': 4001, '_path': str(path), '_instance': None})   # already gone: no error


class MoreStopTests(unittest.TestCase):
    def stop(self, deps, **kw):
        selector = {'root': kw.pop('root', []), 'port': kw.pop('port', None), 'all': kw.pop('all', False), 'kind': kw.pop('kind', None)}
        return servers.stop_servers(selector, deps=deps, **kw)

    def test_a_process_that_vanished_before_the_signal_is_swept_not_refused(self):
        class Vanished(FakeDeps):
            def terminate(self, pid):
                self.sent.append(pid)
                return 'gone'
        deps = Vanished([dash()])
        result = self.stop(deps, port=5100)
        self.assertEqual((result['status'], result['items'][0]['outcome'], deps.removed), ('PASS', 'dead-swept', ['/state/5100.json']))

    def test_mixed_results_give_the_stronger_status(self):
        stuck_and_refused = FakeDeps([dash(), dash(pid=4003, port=5101)], identities={4001: {'cmdline': SERVER_CMD, 'started': 900.0}, 4003: None})
        self.assertEqual(self.stop(stuck_and_refused, all=True, timeout=1.0)['status'], 'FAIL')
        stopped_and_refused = FakeDeps([dash(), dash(pid=4003, port=5101)], identities={4001: {'cmdline': SERVER_CMD, 'started': 900.0}, 4003: None}, stops={4001})
        self.assertEqual(self.stop(stopped_and_refused, all=True)['status'], 'BLOCKED')
        dry_refused = FakeDeps([dash(pid=4003, port=5101)], identities={4003: None})
        result = self.stop(dry_refused, all=True, dry_run=True)
        self.assertEqual((result['status'], result['items'][0]['outcome']), ('BLOCKED', 'refused'))

    def test_force_goes_on_when_the_gentle_stop_cannot_be_sent(self):
        deps = FakeDeps([dash()], terminate_works=False)
        result = self.stop(deps, port=5100, timeout=1.0, force=True)
        self.assertEqual((result['status'], result['items'][0]['outcome']), ('PASS', 'stopped'))
        self.assertEqual(deps.killed, [4001])

    def test_the_selection_by_port_root_and_kind(self):
        both = FakeDeps([dash(pid=4001, port=5100), proto(pid=4002, port=5100)], stops={4001, 4002})
        self.assertEqual(sorted(i['kind'] for i in self.stop(both, port=5100)['items']), ['dashboard', 'prototype'])   # the port alone does not choose a kind
        kind = FakeDeps([dash(pid=4001, port=5100), proto(pid=4002, port=5100)], stops={4001, 4002})
        self.assertEqual([i['kind'] for i in self.stop(kind, port=5100, kind='dashboard')['items']], ['dashboard'])
        with tempfile.TemporaryDirectory(prefix='servers-') as tmp:
            root = str(Path(tmp).resolve())
            by_root = FakeDeps([proto(pid=4002, port=4100, root=root), dash(pid=4001, port=5100, root='/p/other')], stops={4002})
            result = self.stop(by_root, root=[root])
            self.assertEqual([(i['kind'], i['outcome']) for i in result['items']], [('prototype', 'stopped')])
        nothing = self.stop(FakeDeps([dash()]), port=9999)
        self.assertEqual((nothing['status'], nothing['items']), ('PASS', []))


class CommandTests(unittest.TestCase):
    def run_cli(self, argv, deps):
        import contextlib
        import io
        from skillsync_workflow import cli
        out = io.StringIO()
        with mock.patch.object(servers, 'Deps', return_value=deps), mock.patch.object(sys, 'argv', ['skillsync'] + argv), contextlib.redirect_stdout(out):
            try:
                code = cli.main()
            except SystemExit as exit_:
                code = 0 if exit_.code is None else exit_.code
            else:
                code = 0 if code is None else code
        return code, out.getvalue()

    def test_codes_and_json(self):
        deps = FakeDeps([dash(), proto()], stops={4001})
        code, out = self.run_cli(['servers', 'list', '--json'], deps)
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)['servers']), 2)
        self.assertNotIn('token', out)
        self.assertEqual(self.run_cli(['servers', 'stop'], deps)[0], 2)
        self.assertEqual(self.run_cli(['servers', 'stop', '--all', '--dry-run'], deps)[0], 3)
        self.assertEqual(self.run_cli(['servers', 'stop', '--port', '5100', '--json'], deps)[0], 0)
        self.assertEqual(self.run_cli(['servers', 'stop', '--port', '4100', '--timeout', '1'], deps)[0], 1)


if __name__ == '__main__':
    unittest.main()
