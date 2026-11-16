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
        ENV.record_members(self.env)   # as a long run does after the helpers exist (the record then names the one that left the group)
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
        self.assertEqual(sorted(record), ['members', 'pgid', 'pid', 'start', 'uid'])
        self.assertEqual(record['pgid'], pids['main'])   # the group of the leader
        recorded = {item['pid'] for item in record['members']}
        self.assertTrue({pids['A'], pids['B'], pids['C'], pids['D']} <= recorded, record)   # incl. the helper that left the group
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
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertIn('not the recorded process', err.getvalue())   # a living process behind a removed file is said out loud
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
        record = json.loads((self.work / 'chrome.pid').read_text())
        self.assertEqual(record['pgid'], os.getpgrp())   # the recorded group IS the group of this runner: it must not be swept either
        bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # a later process of the same group
        self.spawned.append(bystander)
        self.wait_listed(bystander.pid)
        with mock.patch.object(ENV, '_signal_group', side_effect=AssertionError('the group of this process must never be signalled')):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertIsNone(bystander.poll())   # a neighbour in the runner's group is not ours
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

    def test_the_start_writes_the_helper_list_into_the_record(self):
        with mock.patch.object(ENV, 'record_members', wraps=ENV.record_members) as record:
            ENV.start_chrome(self.env, self.fake)
        record.assert_called_once_with(self.env)
        self.assertEqual(ENV.stop_all(self.env), {})

    def test_helpers_are_stopped_when_the_leader_was_killed_before_the_stop(self):
        sentinel = self.sentinel()
        pids = self.start_fake_chrome()
        os.kill(pids['main'], signal.SIGKILL)   # Chrome closed from outside: the helpers live on, the tree below the leader is gone
        deadline = time.time() + 5
        while alive(pids['main']) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pids['main']))
        self.assertTrue(all(alive(pids[tag]) for tag in 'ABCD'))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertFalse(any(alive(pids[tag]) for tag in 'ABCD'), 'a helper survived the death of its leader')
        self.assertIn('leader', err.getvalue())   # said out loud, not silently
        self.assertTrue(alive(sentinel.pid))
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_a_dead_leader_without_helpers_removes_the_file_quietly(self):
        sentinel = self.sentinel()
        row = ENV.process_table()[sentinel.pid]
        sentinel.kill()
        sentinel.wait()
        (self.work / 'chrome.pid').write_text(json.dumps(dict(ENV.identity_of(row), pgid=row['pgid'], members=[])))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertEqual(err.getvalue(), '')
        self.assertFalse((self.work / 'chrome.pid').exists())

    def fake_ps(self, returncode, keep_lines=None, extra=''):
        real = ENV.subprocess.run
        def run(argv, *args, **kwargs):
            if argv and argv[0] == 'ps' and '-A' in argv:
                out = real(argv, *args, **kwargs)
                lines = out.stdout.splitlines()
                lines = lines[:keep_lines] if keep_lines else lines
                return subprocess.CompletedProcess(argv, returncode, '\n'.join(lines) + '\n' + extra, '')
            return real(argv, *args, **kwargs)
        return mock.patch.object(ENV.subprocess, 'run', run)

    def test_a_partial_ps_table_is_not_taken_for_the_whole_one(self):
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(ENV._pid_record(sentinel.pid))
        for label, patcher in (('exit status 1 and a cut output', self.fake_ps(1, keep_lines=5)),
                               ('a line that cannot be read', self.fake_ps(0, extra='garbage that is not a ps row\n'))):
            with self.subTest(label), patcher, mock.patch.object(ENV, '_table_from_proc', return_value={}), \
                    contextlib.redirect_stderr(io.StringIO()):
                survivors = ENV.stop_all(self.env)
            self.assertEqual(set(survivors), {sentinel.pid})
            self.assertTrue(pid_file.exists(), label)   # nothing was proved: the file stays, nobody was signalled
            self.assertTrue(alive(sentinel.pid))

    def test_pid_files_naming_pid_zero_one_or_negative_are_junk_and_nothing_is_signalled_for_them(self):
        for text in ('0', '1', '-5', json.dumps({'pid': 0, 'start': 1, 'uid': os.getuid(), 'pgid': 0}),
                     json.dumps({'pid': 1, 'start': 1, 'uid': os.getuid(), 'pgid': 1})):
            with self.subTest(text):
                pid_file = self.work / 'chrome.pid'
                pid_file.write_text(text)
                self.assertIsNone(ENV.read_pid_file(pid_file))
        with mock.patch.object(os, 'kill', side_effect=AssertionError('kill')), mock.patch.object(os, 'killpg', side_effect=AssertionError('killpg')):
            for number in (0, 1, -1):
                ENV._signal(number, signal.SIGKILL)
                ENV._signal_group(number, signal.SIGKILL)
        record = {'pid': 4242, 'start': 1, 'uid': os.getuid(), 'pgid': 1, 'members': [{'pid': 0, 'start': 1, 'uid': os.getuid()}]}
        (self.work / 'chrome.pid').write_text(json.dumps(record))
        self.assertIsNone(ENV.read_pid_file(self.work / 'chrome.pid').get('pgid'))   # a group number <= 1 is dropped
        self.assertEqual(ENV.read_pid_file(self.work / 'chrome.pid')['members'], [])

    def test_the_group_of_this_process_and_of_its_ancestors_is_never_a_group_to_signal(self):
        protected = ENV._protected()
        self.assertTrue({os.getpid(), os.getppid()} <= protected)
        own = {'pid': os.getpgrp(), 'pgid': os.getpgrp()}
        self.assertFalse(ENV.own_group_leader(own))                                   # the group of the runner
        with mock.patch.object(ENV, '_protected', return_value=set()):                # on its own, without the ancestors rule
            self.assertFalse(ENV.own_group_leader(own))
        if os.getppid() > 1:
            self.assertFalse(ENV.own_group_leader({'pid': os.getppid(), 'pgid': os.getppid()}))   # a shell above us leads its own group
        self.assertTrue(ENV.own_group_leader({'pid': 2 ** 22 - 3, 'pgid': 2 ** 22 - 3}))         # a stranger process leading its own group
        self.assertFalse(ENV.own_group_leader({'pid': 1, 'pgid': 1}))
        table = ENV.process_table()
        members = ENV.collect_members(os.getppid(), table)
        self.assertEqual(set(members) & protected, set())

    def test_a_process_of_another_user_is_never_ours_in_a_plain_number_file(self):
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(str(sentinel.pid))
        record = ENV.read_pid_file(pid_file)
        row = ENV.process_table()[sentinel.pid]
        self.assertTrue(ENV.is_ours(pid_file, record, row))
        self.assertFalse(ENV.is_ours(pid_file, record, dict(row, uid=row['uid'] + 1)))

    def test_after_the_leader_died_only_the_listed_helpers_are_signalled_and_no_group_is_swept(self):
        pids = self.start_fake_chrome()
        record = json.loads((self.work / 'chrome.pid').read_text())
        os.kill(pids['main'], signal.SIGKILL)
        time.sleep(0.5)
        record.update(members=[])   # nothing listed: nothing to prove, so nothing is signalled (real helpers end by themselves)
        (self.work / 'chrome.pid').write_text(json.dumps(record))
        with mock.patch.object(ENV, '_signal', side_effect=AssertionError('nothing may be signalled')), \
                mock.patch.object(ENV, '_signal_group', side_effect=AssertionError('no group may be signalled')):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertTrue(all(alive(pids[tag]) for tag in 'ABCD'))
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_a_stranger_that_took_the_number_of_the_dead_leader_and_its_group_are_left_alone(self):
        stranger = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)', 'chrome', '--remote-debugging-port=6'],
                                    process_group=0, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(stranger)
        self.wait_listed(stranger.pid)
        row = ENV.process_table()[stranger.pid]
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], process_group=stranger.pid,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(child)
        self.wait_listed(child.pid)
        old_leader = {'pid': stranger.pid, 'start': row['start'] - 3600, 'uid': row['uid'], 'pgid': stranger.pid, 'members': []}
        (self.work / 'chrome.pid').write_text(json.dumps(old_leader))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertTrue(alive(stranger.pid) and alive(child.pid), 'a process of a stranger was killed')
        self.assertIn('not the recorded process', err.getvalue())

    def test_what_lives_below_a_listed_helper_is_stopped_with_it_and_nothing_else(self):
        pids = self.start_fake_chrome()
        record = json.loads((self.work / 'chrome.pid').read_text())
        os.kill(pids['main'], signal.SIGKILL)
        time.sleep(0.5)
        record['members'] = [item for item in record['members'] if item['pid'] == pids['A']]   # only A is listed; B is A's child
        (self.work / 'chrome.pid').write_text(json.dumps(record))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertFalse(alive(pids['A']) or alive(pids['B']))
        self.assertTrue(alive(pids['C']) and alive(pids['D']))   # not listed, not below A: not ours to touch

    def test_the_identity_proved_for_a_plain_number_file_travels_into_the_stop(self):
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(str(sentinel.pid))
        real = ENV.process_table
        calls = []
        def table():
            result = real()
            calls.append(1)
            if len(calls) > 1 and sentinel.pid in result:   # the number now belongs to a stranger (another start time)
                result = dict(result)
                result[sentinel.pid] = dict(result[sentinel.pid], start=result[sentinel.pid]['start'] + 5000)
            return result
        sent = []
        with mock.patch.object(ENV, 'process_table', side_effect=table), mock.patch.object(ENV, '_signal', lambda pid, sig: sent.append(pid)), \
                mock.patch.object(ENV, '_signal_group', lambda pgid, sig: sent.append(-pgid)), \
                mock.patch.object(ENV, 'GRACE', 0.1), mock.patch.object(ENV, 'KILL_WAIT', 0.1), contextlib.redirect_stderr(io.StringIO()):
            ENV.stop_all(self.env)
        self.assertEqual([item for item in sent if abs(item) == sentinel.pid], [], 'the process behind a replaced number was signalled')
        self.assertTrue(alive(sentinel.pid))

    def synthetic(self, rows):
        """A process table made of `rows` ({pid: (ppid, pgid, uid, state, start)})."""
        return {pid: {'pid': pid, 'ppid': ppid, 'pgid': pgid, 'uid': uid, 'state': state, 'start': start}
                for pid, (ppid, pgid, uid, state, start) in rows.items()}

    def test_orphans_are_only_the_listed_helpers_with_the_matching_identity_and_the_own_user(self):
        me = os.getuid()
        table = self.synthetic({
            1000001: (1, 1, me, 'S', 100), 1000002: (1, 1000001, me, 'S', 105), 1000003: (1, 1000001, me + 1, 'S', 105),
            1000004: (1, 1000001, me, 'Z', 105), 1000005: (1, 1000001, me, 'S', 105), 1000006: (1, 1000001, me, 'S', 109)})
        # the leader (1000001) is gone from the table; 1000002 is in its group and listed, the others are in the group but not (or wrongly) listed
        listed = lambda pid, start, uid=me: {'pid': pid, 'start': start, 'uid': uid}
        record = {'pid': 999999, 'start': 100, 'uid': me, 'pgid': 1000001, 'members': [
            listed(1000002, 105), listed(1000003, 105, me + 1),   # another user's process with a matching identity
            listed(1000004, 105),                                  # a zombie
            listed(1000006, 100)]}                                 # a process of the number whose start time differs
        self.assertEqual(set(ENV.orphans_of(record, table)), {1000002})   # 1000005 is in the group but not listed: not ours

    def test_a_zombie_with_the_number_of_the_leader_does_not_bring_its_group_into_play(self):
        me = os.getuid()
        table = self.synthetic({777777: (1, 777777, me, 'Z', 5000), 777778: (777777, 777777, me, 'S', 5001)})   # a stranger zombie and its child
        record = {'pid': 777777, 'start': 100, 'uid': me, 'pgid': 777777, 'members': []}
        self.assertEqual(ENV.orphans_of(record, table), {})

    def test_this_process_its_parent_and_its_grandparent_are_protected(self):
        me = os.getuid()
        table = self.synthetic({os.getpid(): (4000002, 4000002, me, 'S', 10), 4000002: (4000001, 4000002, me, 'S', 5),
                                4000001: (1, 4000001, me, 'S', 1), 1: (0, 1, 0, 'S', 0)})
        with mock.patch.object(ENV, 'process_table', return_value=table):
            protected = ENV._protected()
        self.assertTrue({os.getpid(), 4000002, 4000001} <= protected)   # the grandparent too, not only the parent
        record = {'pid': 999999, 'start': 1, 'uid': me, 'pgid': None, 'members': [{'pid': 4000001, 'start': 1, 'uid': me}]}
        with mock.patch.object(ENV, 'process_table', return_value=table):
            self.assertEqual(ENV.orphans_of(record, table), {})

    def test_a_record_that_names_an_ancestor_of_the_runner_signals_nothing(self):
        row = ENV.process_table()[os.getpid()]   # this very process (a record naming it or an ancestor is junk)
        (self.work / 'chrome.pid').write_text(json.dumps(ENV.identity_of(row)))
        err = io.StringIO()
        with mock.patch.object(ENV, '_signal', side_effect=AssertionError('kill')), \
                mock.patch.object(ENV, '_signal_group', side_effect=AssertionError('killpg')), contextlib.redirect_stderr(err):
            self.assertEqual(ENV.stop_all(self.env), {})
        self.assertIn('ancestors', err.getvalue())
        self.assertFalse((self.work / 'chrome.pid').exists())

    def test_a_root_replaced_between_the_tables_is_neither_signalled_nor_taken_with_its_group(self):
        me = os.getuid()
        leader = {'pid': 888888, 'start': 100, 'uid': me}
        first = self.synthetic({888888: (1, 888888, me, 'S', 100), 888889: (888888, 888888, me, 'S', 101)})
        replaced = self.synthetic({888888: (1, 888888, me, 'S', 5000), 888889: (888888, 888888, me, 'S', 5001)})   # the number now means a stranger
        tables = iter([first, replaced])
        last = [replaced]
        def table():
            last[0] = next(tables, last[0])
            return last[0]
        sent = []
        with mock.patch.object(ENV, 'process_table', side_effect=table), mock.patch.object(ENV, '_signal', lambda pid, sig: sent.append(pid)), \
                mock.patch.object(ENV, '_signal_group', lambda pgid, sig: sent.append(-pgid)), \
                mock.patch.object(ENV, 'GRACE', 0.1), mock.patch.object(ENV, 'KILL_WAIT', 0.1):
            ENV.stop_tree(888888, root=leader)
        self.assertEqual([item for item in sent if abs(item) in (888888, 888889)], [], 'the stranger behind the old number was signalled')

    def test_the_start_time_tolerance_is_a_few_seconds_not_an_hour(self):
        row = ENV.process_table()[os.getpid()]
        record = ENV.identity_of(row)
        self.assertTrue(ENV.same_identity(row, dict(record, start=record['start'] + ENV.START_TOLERANCE)))
        self.assertFalse(ENV.same_identity(row, dict(record, start=record['start'] + ENV.START_TOLERANCE + 1)))
        sentinel = self.sentinel()
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(str(sentinel.pid))
        plain = ENV.read_pid_file(pid_file)
        live = ENV.process_table()[sentinel.pid]
        edge = dict(plain, mtime=live['start'] - ENV.START_TOLERANCE)       # the process started exactly the tolerance after the file
        beyond = dict(plain, mtime=live['start'] - ENV.START_TOLERANCE - 1)  # one second more: the number was reused
        self.assertTrue(ENV.is_ours(pid_file, edge, live))
        self.assertFalse(ENV.is_ours(pid_file, beyond, live))

    def test_a_plain_number_file_needs_the_expected_command_line_too(self):
        plain = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # nothing Chrome-like in its command
        self.spawned.append(plain)
        self.wait_listed(plain.pid)
        pid_file = self.work / 'chrome.pid'
        pid_file.write_text(str(plain.pid))
        self.assertFalse(ENV.is_ours(pid_file, ENV.read_pid_file(pid_file), ENV.process_table()[plain.pid]))

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
