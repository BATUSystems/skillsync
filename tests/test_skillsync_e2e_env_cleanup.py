"""Cleanup of the prototype E2E environment (`tests/e2e/prototype/env.py`): the processes of a run (Chrome and its helpers, the servers) are
stopped by identity (pid, start time, uid) and by the tree listed before the first signal, never by name, on Linux and macOS, without
`/proc` as the only source and without a real Chrome (a stand-in binary with helpers that leave the group or are re-parented)."""
import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ENV_PATH = Path(__file__).resolve().parent / 'e2e' / 'prototype' / 'env.py'

# The stand-in "Chrome": keeps the arguments of the real one, writes the PIDs of its helpers to the file named in FAKE_CHROME_PIDS and then
# sleeps. Helpers: A same group, B grandchild of A, C leaves the group and the session (like a detached handler), D double fork (re-parented).
FAKE_CHROME = textwrap.dedent('''
    import json, os, sys, time
    pids = {'main': os.getpid()}
    def sleeper():
        time.sleep(600)
        os._exit(0)
    def spawn(tag, new_session=False, double=False):
        read, write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read)
            if new_session:
                os.setsid()
            if double:
                if os.fork() != 0:
                    os._exit(0)
                os.write(write, str(os.getpid()).encode())
                os.close(write)
                sleeper()
            if tag == 'A':
                grandchild = os.fork()
                if grandchild == 0:
                    os.close(write)
                    sleeper()
                os.write(write, ('%d,%d' % (os.getpid(), grandchild)).encode())
                os.close(write)
                sleeper()
            os.write(write, str(os.getpid()).encode())
            os.close(write)
            sleeper()
        os.close(write)
        data = os.read(read, 64).decode()
        os.close(read)
        if double:
            os.waitpid(child, 0)
        return data
    a, b = spawn('A').split(',')
    pids.update(A=int(a), B=int(b), C=int(spawn('C', new_session=True)), D=int(spawn('D', double=True)))
    open(os.environ['FAKE_CHROME_PIDS'] + '.tmp', 'w').write(json.dumps(pids))
    os.rename(os.environ['FAKE_CHROME_PIDS'] + '.tmp', os.environ['FAKE_CHROME_PIDS'])
    time.sleep(600)
''')


def load_env_module():
    spec = importlib.util.spec_from_file_location('skillsync_e2e_env_under_test', ENV_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def alive(pid):
    """A process that exists and is not a zombie (a zombie has died; only its parent has not collected it yet)."""
    try:
        table = ENV.process_table()
    except RuntimeError:
        return False
    row = table.get(pid)
    return row is not None and not row['state'].startswith('Z')


ENV = load_env_module() if os.name != 'nt' else None


@unittest.skipIf(os.name == 'nt', 'needs POSIX process groups and ps')
class E2eEnvCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'w'
        self.work.mkdir()
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.env = {'workdir': str(self.work), 'chromePort': 0}
        self.spawned = []
        self.addCleanup(self.reap)
        patch = mock.patch.dict(os.environ, {'HOME': str(self.home), 'FAKE_CHROME_PIDS': str(self.work / 'helpers.json')})
        patch.start()
        self.addCleanup(patch.stop)
        wait = mock.patch.object(ENV, 'wait_port', lambda *args, **kwargs: True)   # the stand-in opens no port
        wait.start()
        self.addCleanup(wait.stop)
        fake = self.work / 'fake-chrome'
        fake.write_text('#!%s\n%s' % (sys.executable, FAKE_CHROME))
        fake.chmod(0o755)
        self.fake = str(fake)

    def reap(self):
        """Test cleanup only: the processes this test itself started (by the PIDs it holds), so a failing test leaves nothing behind."""
        for proc in self.spawned:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        helpers = self.work / 'helpers.json'
        if helpers.exists():
            for pid in json.loads(helpers.read_text()).values():
                if alive(pid):
                    os.kill(pid, signal.SIGKILL)
        ENV.CHILDREN.clear()

    def sentinel(self):
        """A process OUTSIDE the tree whose command line looks like Chrome's: a name match would kill it, an identity match must not."""
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)', 'chrome', '--remote-debugging-port=1'],
                                start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(proc)
        self.wait_listed(proc.pid)
        return proc

    def wait_listed(self, pid):
        deadline = time.time() + 5
        while time.time() < deadline:
            if pid in ENV.process_table():
                return
            time.sleep(0.05)
        self.fail('process %d not listed' % pid)

    def start_fake_chrome(self):
        ENV.start_chrome(self.env, self.fake)
        helpers = self.work / 'helpers.json'
        deadline = time.time() + 10
        while not helpers.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(helpers.exists(), 'the stand-in did not start its helpers')
        return json.loads(helpers.read_text())

    def test_the_whole_tree_dies_and_a_chrome_looking_stranger_survives(self):
        sentinel = self.sentinel()
        pids = self.start_fake_chrome()
        self.assertEqual(sorted(pids), ['A', 'B', 'C', 'D', 'main'])
        self.assertTrue(all(alive(pid) for pid in pids.values()))
        table = ENV.process_table()
        self.assertNotEqual(table[pids['C']]['pgid'], pids['main'])   # C really left the group
        self.assertEqual(table[pids['D']]['ppid'] in (pids['main'], pids['A']), False)   # D is re-parented
        record = json.loads((self.work / 'chrome.pid').read_text())
        self.assertEqual(sorted(record), ['pid', 'start', 'uid'])
        self.assertEqual(ENV.stop_all(self.env), {})
        self.assertEqual({tag: alive(pid) for tag, pid in pids.items()}, {tag: False for tag in pids})
        self.assertTrue(alive(sentinel.pid), 'a process outside the tree was killed')
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_it_works_without_proc_and_with_ps_only(self):
        pids = self.start_fake_chrome()
        with mock.patch.object(ENV, '_table_from_proc', side_effect=AssertionError('/proc must not be needed')):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertFalse(any(alive(pid) for pid in pids.values()))

    @unittest.skipUnless(os.path.isdir('/proc/self'), 'process ownership backend on Linux only (the /proc fallback)')
    def test_the_proc_fallback_works_when_ps_is_missing(self):
        pids = self.start_fake_chrome()
        with mock.patch.object(ENV, '_table_from_ps', side_effect=FileNotFoundError('ps')):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertFalse(any(alive(pid) for pid in pids.values()))

    def test_a_reused_pid_is_left_alone_and_its_stale_file_removed(self):
        sentinel = self.sentinel()
        row = ENV.process_table()[sentinel.pid]
        stale = dict(ENV.identity_of(row), start=row['start'] - 3600)   # the recorded process started an hour earlier: not this one
        (self.work / 'chrome.pid').write_text(json.dumps(stale))
        self.assertEqual(ENV.stop_all(self.env), {})
        self.assertTrue(alive(sentinel.pid))
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_a_plain_number_file_written_before_the_process_started_does_not_match(self):
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(str(sentinel.pid))
        old = time.time() - 3600
        os.utime(pid_file, (old, old))   # the file is older than the process: the PID was reused
        self.assertEqual(ENV.stop_all(self.env), {})
        self.assertTrue(alive(sentinel.pid))

    def test_a_plain_number_file_of_our_own_server_is_stopped(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)', 'http.server', '--bind', '127.0.0.1'],
                                start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(proc)
        self.wait_listed(proc.pid)
        (self.work / ('static-%d.pid' % 1)).write_text(str(proc.pid))   # written after the spawn, as a server script does
        self.assertEqual(ENV.stop_all(self.env), {})
        proc.wait(timeout=5)   # our own child: stopped, so it can be collected
        self.assertFalse((self.work / 'static-1.pid').exists())

    def test_the_file_stays_and_the_survivor_is_reported_when_a_process_does_not_die(self):
        pids = self.start_fake_chrome()
        stubborn = pids['C']
        real = ENV._signal
        err = io.StringIO()
        with mock.patch.object(ENV, '_signal', lambda pid, sig: None if pid == stubborn else real(pid, sig)), \
                mock.patch.object(ENV, 'GRACE', 0.5), mock.patch.object(ENV, 'KILL_WAIT', 0.5), contextlib.redirect_stderr(err):
            survivors = ENV.stop_all(self.env)
        self.assertEqual(set(survivors), {stubborn})
        self.assertTrue((self.work / 'chrome.pid').exists())   # the proof of what was ours stays while somebody lives
        self.assertIn(str(stubborn), err.getvalue())
        self.assertTrue(alive(stubborn))
        self.assertFalse(any(alive(pid) for tag, pid in pids.items() if tag != 'C'))
        self.assertEqual(ENV.stop_all(self.env), {})   # a later stop (nothing is blocking the signal now) finishes the job
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_a_process_in_our_own_group_is_signalled_alone_never_through_its_group(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)', 'chrome', '--remote-debugging-port=2'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # same group as this test
        self.spawned.append(proc)
        self.wait_listed(proc.pid)
        self.assertEqual(os.getpgid(proc.pid), os.getpgrp())
        ENV.CHILDREN[proc.pid] = proc
        (self.work / 'chrome.pid').write_text(ENV._pid_record(proc.pid))
        with mock.patch.object(ENV, '_signal_group', side_effect=AssertionError('the group of this process must never be signalled')):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertIsNotNone(proc.poll())
        self.assertTrue(alive(os.getpid()))

    def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)',
                                 'chrome', '--remote-debugging-port=3'], start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(proc)
        self.wait_listed(proc.pid)
        time.sleep(0.5)   # the handler is installed
        ENV.CHILDREN[proc.pid] = proc
        (self.work / 'chrome.pid').write_text(ENV._pid_record(proc.pid))
        with mock.patch.object(ENV, 'GRACE', 0.5):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_a_member_whose_identity_changed_before_the_signal_is_not_signalled(self):
        sentinel = self.sentinel()
        row = ENV.process_table()[sentinel.pid]
        stale = {sentinel.pid: dict(ENV.identity_of(row), start=row['start'] - 3600)}   # the PID of the snapshot now belongs to another process
        ENV._send(stale, signal.SIGKILL)
        time.sleep(0.3)
        self.assertTrue(alive(sentinel.pid))

    def test_when_no_process_list_is_available_nothing_is_signalled_and_the_files_stay(self):
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(ENV._pid_record(sentinel.pid))
        with mock.patch.object(ENV, 'process_table', side_effect=RuntimeError('cannot list processes')), \
                mock.patch.object(ENV, '_signal', side_effect=AssertionError('nothing may be signalled')):
            survivors = ENV.stop_all(self.env)
        self.assertEqual(set(survivors), {sentinel.pid})
        self.assertTrue(pid_file.exists())
        self.assertTrue(alive(sentinel.pid))

    def test_the_identity_of_a_live_process_matches_itself_only(self):
        row = ENV.process_table()[os.getpid()]
        record = ENV.identity_of(row)
        self.assertTrue(ENV.same_identity(row, record))
        self.assertFalse(ENV.same_identity(row, dict(record, uid=record['uid'] + 1)))
        self.assertFalse(ENV.same_identity(row, dict(record, start=record['start'] - 60)))
        self.assertFalse(ENV.same_identity(None, record))


if __name__ == '__main__':
    unittest.main()
