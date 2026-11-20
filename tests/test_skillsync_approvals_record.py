"""`skillsync approvals record` / `status.record_approval`: the only writer of `.skillsync/runs/<run-id>/approvals.jsonl`
(entry format, refusal of a second entry, validation, inter-process lock, compatibility with `approvals wait`) and the
`cp-ready` dashboard fixture after a recorded CP (docs/dashboard-contract.md, `approvals record`)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from skillsync_workflow import prototype_server as server  # noqa: E402
from skillsync_workflow import quality, status  # noqa: E402
from tests.test_skillsync_dashboard import FIXTURES, DashboardFixture, git_commit_all, normalized  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402

CLI = ROOT / 'skillsync'
RUN = 'r1'
HEAD = 'f27ef059c38902e6beef347221a4c0b7aaf8837a'
NOW = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)


class RecordFixture(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.root = self.base / 'demo'
        (self.root / '.github').mkdir(parents=True)
        (self.root / '.github' / '.skillsync.yaml').write_text('delivery:\n  mode: direct\n  base: main\n', encoding='utf-8')
        (self.root / '.skillsync' / 'runs' / RUN).mkdir(parents=True)
        self.log = self.root / '.skillsync' / 'runs' / RUN / 'approvals.jsonl'

    def record(self, word='CP', run=RUN, **kwargs):
        kwargs.setdefault('sha', HEAD)
        kwargs.setdefault('by', 'user')
        kwargs.setdefault('now', NOW)
        return status.record_approval(self.root, word, run, **kwargs)

    def lines(self, path=None):
        path = path or self.log
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=60,
                              env={**self.env, **(env or {})}, cwd=self.base)

    def record_cli(self, *extra, word='CP', run=RUN, source='cli', code=0, env=None):
        result = self.run_cli('approvals', 'record', self.root, '--word', word, '--run', run, '--source', source, '--json',
                              *extra, env=env)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def doc(self, folder, name, text='# Doc\n\nStatus: draft\n'):
        path = self.root / 'docs' / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path


class RecordEntry(RecordFixture):
    def test_writes_one_json_line_with_the_contract_fields(self):
        report = self.record(target='main', branch='feat/demo', plan='docs/plans/p.md', ahead=2, mode='direct')
        self.assertEqual((report['schema'], report['status']), (1, 'PASS'))
        self.assertEqual(report['path'], '.skillsync/runs/r1/approvals.jsonl')
        expected = {'schema': 1, 'at': '2026-01-12T12:00:00Z', 'word': 'CP', 'sha': HEAD, 'by': 'user', 'source': 'cli',
                    'run': RUN, 'plan': 'docs/plans/p.md', 'target': 'main', 'branch': 'feat/demo', 'ahead': 2,
                    'mode': 'direct'}
        self.assertEqual(report['entry'], expected)
        self.assertEqual(self.lines(), [expected])
        raw = self.log.read_bytes()
        self.assertEqual(raw.count(b'\n'), 1)
        self.assertTrue(raw.endswith(b'\n'))
        self.assertFalse((self.log.parent / 'approvals.jsonl.lock').exists(), 'the lock file is removed after the write')

    def test_optional_fields_are_absent_when_not_given_and_at_is_utc(self):
        local = datetime(2026, 1, 12, 13, 30, tzinfo=timezone.utc).astimezone()
        entry = self.record(now=local)['entry']
        self.assertEqual(set(entry), {'schema', 'at', 'word', 'sha', 'by', 'source', 'run'})
        self.assertEqual(entry['at'], '2026-01-12T13:30:00Z')

    def test_second_entry_of_the_same_word_sha_and_run_is_refused(self):
        self.assertEqual(self.record()['status'], 'PASS')
        refused = self.record()
        self.assertEqual((refused['status'], refused['entry']), ('BLOCKED', None))
        self.assertIn('already recorded', refused['reason'])
        self.assertEqual(self.record(sha='a' * 40)['status'], 'PASS', 'another HEAD is a new consent')
        self.assertEqual(self.record(word='READY')['status'], 'PASS', 'another word for the same HEAD is a new consent')
        other = self.root / '.skillsync' / 'runs' / 'r2'
        other.mkdir()
        self.assertEqual(self.record(run='r2')['status'], 'PASS', 'another run is a new consent')
        self.assertEqual([(row['word'], row['sha']) for row in self.lines()], [('CP', HEAD), ('CP', 'a' * 40), ('READY', HEAD)])

    def test_go_sha_defaults_to_the_blob_sha_of_the_file(self):
        spec = self.doc('specs', 's.md')
        entry = self.record('GO plan', sha=None, file='docs/specs/s.md')['entry']
        self.assertEqual((entry['sha'], entry['file']), (status.git_blob_sha(spec.read_bytes()), 'docs/specs/s.md'))
        self.assertEqual(self.record('GO plan', sha=None, file='docs/specs/s.md')['status'], 'BLOCKED',
                         'the same file version is approved once')
        self.doc('plans', 'p.md')
        for word, name in (('GO plan', 'docs/plans/p.md'), ('GO implement', 'docs/specs/s.md'), ('CP', 'docs/plans/p.md'),
                           ('GO implement', 'docs/plans/../../x.md'), ('GO implement', 'docs/plans/missing.md')):
            with self.subTest(word=word, file=name):
                self.assertEqual(self.record(word, file=name)['status'], 'BLOCKED')

    def test_go_sha_after_is_stored_as_sha_after(self):
        spec = self.doc('specs', 's.md')
        shown = status.git_blob_sha(spec.read_bytes())
        entry = self.record('GO plan', sha=shown, file='docs/specs/s.md', sha_after=HEAD, source='dashboard')['entry']
        self.assertEqual((entry['sha'], entry['shaAfter'], entry['source']), (shown, HEAD, 'dashboard'))
        self.assertEqual(json.loads(self.log.read_text(encoding='utf-8'))['shaAfter'], HEAD)
        bad = self.record('GO implement', sha=shown, file=None, sha_after='NOT-HEX')
        self.assertEqual(bad['status'], 'BLOCKED')
        self.assertIn('sha_after', bad['reason'])

    def test_default_sha_is_head_and_without_git_the_record_is_blocked(self):
        blocked = self.record(sha=None)
        self.assertEqual(blocked['status'], 'BLOCKED')
        self.assertIn('HEAD', blocked['reason'])
        self.assertEqual(self.lines(), [])
        head = git_commit_all(self.root, self.env)
        self.assertEqual(self.record(sha=None)['entry']['sha'], head)

    def test_a_torn_last_line_does_not_swallow_the_new_entry(self):
        self.log.write_text('{"word": "CP", "at": "2026-01-1', encoding='utf-8')
        self.assertEqual(self.record()['status'], 'PASS')
        self.assertEqual([row['word'] for row in status.read_approvals(self.root)], ['CP'])

    def test_file_size_limit(self):
        self.log.write_text('x' * (status.MAX_APPROVALS_BYTES - 100) + '\n', encoding='utf-8')
        report = self.record()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('KiB', report['reason'])
        self.assertNotIn(b'"CP"', self.log.read_bytes())

    def test_by_is_redacted(self):
        secret = 'Zx9-' + 'q7Lm' * 3
        with mock.patch.dict(os.environ, {'SKILLSYNC_TEST_TOKEN': secret}):
            entry = self.record(by=f'user-{secret}')['entry']
        self.assertEqual(entry['by'], 'user-[REDACTED]')
        self.assertNotIn(secret, self.log.read_text(encoding='utf-8'))

    def test_by_falls_back_to_unknown(self):
        with mock.patch('getpass.getuser', side_effect=OSError('no user')):
            self.assertEqual(self.record(by=None)['entry']['by'], 'unknown')

    def test_run_id_pattern_equals_quality(self):
        self.assertEqual(status.RUN_ID.pattern, quality._RUN_ID.pattern)


class RecordValidation(RecordFixture):
    def assert_blocked(self, report, fragment):
        self.assertEqual((report['schema'], report['status'], report['entry']), (1, 'BLOCKED', None))
        self.assertIn(fragment, report['reason'])
        self.assertEqual(self.lines(), [])

    def test_bad_inputs_are_blocked_and_write_nothing(self):
        cases = (({'word': 'MAYBE'}, 'Unknown consent word'), ({'source': 'web'}, 'Unknown source'),
                 ({'run': '../x'}, 'Invalid run id'), ({'run': '.hidden'}, 'Invalid run id'), ({'run': ''}, 'Invalid run id'),
                 ({'run': '..'}, 'Invalid run id'), ({'run': '.'}, 'Invalid run id'), ({'run': 'a/b'}, 'Invalid run id'),
                 ({'run': '../' + RUN}, 'Invalid run id'), ({'sha_after': HEAD}, 'sha_after'),
                 ({'run': 'r2'}, 'Run directory not found'), ({'sha': 'XYZ'}, '7-64'), ({'sha': 'abc12'}, '7-64'),
                 ({'target': '-x'}, 'Invalid --target'), ({'branch': 'a b'}, 'Invalid --branch'),
                 ({'target': 'do ustalenia'}, 'Invalid --target'), ({'target': 'a..b'}, 'Invalid --target'), ({'target': 'x/'}, 'Invalid --target'),
                 ({'target': 'main.lock'}, 'Invalid --target'), ({'branch': 'a@{b}'}, 'Invalid --branch'))
        for kwargs, fragment in cases:
            with self.subTest(**kwargs):
                self.assert_blocked(self.record(**kwargs), fragment)

    def test_missing_runs_directory(self):
        (self.root / '.skillsync' / 'runs' / RUN).rmdir()
        self.assert_blocked(self.record(), 'Run directory not found')

    @needs_os_symlinks
    def test_symlinked_run_directory_is_refused(self):
        (self.root / '.skillsync' / 'runs' / 'link').symlink_to(self.root / '.skillsync' / 'runs' / RUN,
                                                                target_is_directory=True)
        self.assert_blocked(self.record(run='link'), 'Symlink')

    @needs_os_symlinks
    def test_symlinked_runs_or_skillsync_directory_is_refused(self):
        for part in ('.skillsync/runs', '.skillsync'):
            with self.subTest(part=part):
                project = self.base / part.replace('/', '-')
                elsewhere = self.base / f'elsewhere-{part.replace("/", "-")}'
                # the link target holds a valid run directory: `<elsewhere>/runs/r1` or `<elsewhere>/r1`
                (elsewhere / ('runs' if part == '.skillsync' else '') / RUN).mkdir(parents=True)
                link = project / part
                link.parent.mkdir(parents=True)
                link.symlink_to(elsewhere, target_is_directory=True)
                report = status.record_approval(project, 'CP', RUN, sha=HEAD, by='user')
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertIn('Symlink', report['reason'])
                self.assertEqual(list(elsewhere.rglob('approvals.jsonl')), [])

    @needs_os_symlinks
    def test_symlinked_approvals_file_is_refused(self):
        outside = self.base / 'outside.jsonl'
        outside.write_text('', encoding='utf-8')
        self.log.symlink_to(outside)
        report = self.record()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('symlink', report['reason'])
        self.assertEqual(outside.read_text(encoding='utf-8'), '')


class RecordLock(RecordFixture):
    def test_parallel_processes_write_the_same_key_exactly_once(self):
        args = [sys.executable, str(CLI), 'approvals', 'record', str(self.root), '--word', 'CP', '--run', RUN, '--sha', HEAD,
                '--source', 'cli', '--json']
        procs = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.base)
                 for _ in range(6)]
        results = [(proc.wait(timeout=60), proc.communicate()[0]) for proc in procs]
        codes = sorted(code for code, _ in results)
        self.assertEqual(codes, [0, 2, 2, 2, 2, 2], results)
        self.assertEqual(len(self.lines()), 1)
        for code, out in results:
            self.assertEqual(json.loads(out)['status'], 'PASS' if code == 0 else 'BLOCKED')

    def test_parallel_processes_with_different_keys_do_not_interleave(self):
        procs = [subprocess.Popen([sys.executable, str(CLI), 'approvals', 'record', str(self.root), '--word', 'CP', '--run', RUN,
                                   '--sha', f'{index:x}' * 40, '--source', 'cli'], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.base) for index in range(1, 7)]
        self.assertEqual([proc.wait(timeout=60) for proc in procs], [0] * 6, [proc.communicate() for proc in procs])
        self.assertEqual(sorted(row['sha'][0] for row in self.lines()), list('123456'))

    def test_a_held_lock_blocks_after_the_timeout_and_is_free_again_after_release(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        holder = subprocess.Popen(  # another PROCESS holds the system lock (an in-process holder would stop on the thread lock)
            [sys.executable, '-c',
             'import sys\nfrom skillsync_workflow import prototype_server as ps\n'
             'with ps.FileLock(sys.argv[1], 30, 0o600):\n    print("ready", flush=True)\n    sys.stdin.readline()\n', str(lock)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT, env={**self.env, 'PYTHONPATH': str(ROOT)})
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'ready')
            with mock.patch.object(status, 'APPROVAL_LOCK_TIMEOUT', 0.3):
                report = self.record()
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertIn('locked', report['reason'])
            self.assertEqual(self.lines(), [], 'nothing is written without the lock')
        finally:
            holder.stdin.write('\n')
            holder.stdin.close()
            holder.wait(timeout=30)
            holder.stdout.close()
        self.assertEqual(self.record()['status'], 'PASS')

    def test_a_leftover_lock_file_without_an_owner_never_blocks(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        for index, (content, age) in enumerate((('1', 0), ('token-from-an-old-writer', 3600), ('', 0)), 1):
            with self.subTest(content=content, age=age):
                lock.write_text(content, encoding='utf-8')
                old = time.time() - age
                os.utime(lock, (old, old))
                started = time.monotonic()
                self.assertEqual(self.record(sha=f'{index:x}' * 40)['status'], 'PASS')
                self.assertLess(time.monotonic() - started, 2, 'no waiting for a "stale" lock')
        self.assertEqual(sorted(p.name for p in lock.parent.iterdir() if '.stale-' in p.name), [])

    def test_the_lock_file_is_removed_after_the_write_and_taken_again_by_the_next(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        self.assertEqual(self.record()['status'], 'PASS')
        self.assertFalse(lock.exists(), 'transient: removed while still holding the lock')
        self.assertEqual(self.record(sha='a' * 40)['status'], 'PASS')
        self.assertFalse(lock.exists())

    def test_unlinking_the_lock_file_never_breaks_mutual_exclusion_between_processes(self):
        counter = self.log.parent / 'counter.txt'
        counter.write_text('0', encoding='utf-8')
        lock = self.log.parent / 'approvals.jsonl.lock'
        code = (
            'import sys, time\nfrom pathlib import Path\nfrom skillsync_workflow import prototype_server as ps\n'
            'counter, lock = Path(sys.argv[1]), sys.argv[2]\n'
            'for _ in range(25):\n'
            '    with ps.FileLock(lock, 60, 0o600, unlink=True):\n'
            '        value = int(counter.read_text())\n        time.sleep(0.002)\n        counter.write_text(str(value + 1))\n')
        procs = [subprocess.Popen([sys.executable, '-c', code, str(counter), str(lock)], cwd=ROOT,
                                  env={**self.env, 'PYTHONPATH': str(ROOT)}, stderr=subprocess.PIPE, text=True)
                 for _ in range(4)]
        errors = [proc.communicate(timeout=120)[1] for proc in procs]
        self.assertEqual([proc.returncode for proc in procs], [0] * 4, errors)
        self.assertEqual(int(counter.read_text(encoding='utf-8')), 4 * 25, 'no lost update across unlink/recreate')
        self.assertFalse(lock.exists())

    def test_unlinking_the_lock_file_never_breaks_mutual_exclusion_with_a_real_flock_between_threads(self):
        import threading
        counter = self.log.parent / 'counter.txt'
        counter.write_text('0', encoding='utf-8')
        lock = self.log.parent / 'approvals.jsonl.lock'
        errors = []

        def work():
            try:
                for _ in range(15):
                    with server.FileLock(str(lock), 30, 0o600, unlink=True):
                        value = int(counter.read_text(encoding='utf-8'))
                        time.sleep(0.001)
                        counter.write_text(str(value + 1), encoding='utf-8')
            except BaseException as error:  # noqa: BLE001
                errors.append(repr(error))

        # a fresh thread lock per instance: the threads no longer serialise on it, so the system lock and the inode check decide
        with mock.patch.object(server, '_process_lock', side_effect=lambda path: threading.Lock()):
            threads = [threading.Thread(target=work) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        self.assertEqual(errors, [])
        self.assertEqual(int(counter.read_text(encoding='utf-8')), 8 * 15, 'no lost update across unlink/recreate')

    @unittest.skipIf(os.name == 'nt', 'Windows nie pozwala usunąć ani podmienić otwartego pliku')
    def test_a_lock_file_replaced_between_open_and_flock_is_locked_again_on_the_new_file(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        real, calls = server._try_lock_fd, []

        def replace_then_lock(fd):
            if not calls:  # the first writer unlinks and recreates the file after we opened it, before our flock
                os.unlink(lock)
                lock.write_text('', encoding='utf-8')
            calls.append(fd)
            return real(fd)

        with mock.patch.object(server, '_try_lock_fd', side_effect=replace_then_lock):
            held = server.FileLock(str(lock), 5, 0o600, unlink=True)
            held.__enter__()
            try:
                self.assertEqual(len(calls), 2, 'locked once on the old inode, then again on the new one')
                self.assertTrue(held._same_file(held._fd))
            finally:
                held.__exit__(None, None, None)
        self.assertFalse(lock.exists())

    def test_a_failed_unlink_on_release_still_frees_the_lock(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        with mock.patch.object(server.os, 'unlink', side_effect=OSError('denied')):
            with server.FileLock(str(lock), 5, 0o600, unlink=True):
                pass
        self.assertTrue(lock.exists(), 'the file stays when it cannot be removed')
        with server.FileLock(str(lock), 1, 0o600, unlink=True):  # and it is not stuck
            pass



class RecordLockSystem(RecordFixture):
    """The approvals lock is a system lock (`flock` on POSIX, `msvcrt.locking` on Windows) shared with the prototype
    server's `FileLock`: no stale-lock takeover, a crashed writer frees it in the kernel."""

    def test_two_threads_and_a_leftover_lock_file_write_exactly_once(self):
        import threading
        lock = self.log.parent / 'approvals.jsonl.lock'
        lock.write_text('1', encoding='utf-8')
        old = time.time() - 600
        os.utime(lock, (old, old))
        barrier = threading.Barrier(2)
        reports = []

        def write():
            barrier.wait(timeout=10)
            reports.append(self.record())

        threads = [threading.Thread(target=write) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(sorted(report['status'] for report in reports), ['BLOCKED', 'PASS'], reports)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual([p.name for p in lock.parent.iterdir() if '.stale-' in p.name], [])

    def test_many_threads_with_different_keys_lose_no_entry(self):
        import threading
        shas = [f'{index:x}' * 40 for index in range(1, 9)]
        reports = []

        def write(sha):
            reports.append(self.record(sha=sha))

        threads = [threading.Thread(target=write, args=(sha,)) for sha in shas]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual([report['status'] for report in reports], ['PASS'] * 8, reports)
        self.assertEqual(sorted(row['sha'] for row in self.lines()), sorted(shas))

    def test_a_file_system_without_locks_is_blocked_with_a_clear_reason(self):
        import errno
        with mock.patch.object(server, '_try_lock_fd', side_effect=OSError(errno.ENOTSUP, 'Operation not supported')):
            started = time.monotonic()
            report = self.record()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('not supported', report['reason'])
        self.assertNotIn('locked by another writer', report['reason'])
        self.assertLess(time.monotonic() - started, 5, 'no waiting for the timeout')
        self.assertEqual(self.lines(), [])

    def test_contention_errors_still_wait_for_the_timeout(self):
        import errno
        with mock.patch.object(server, '_try_lock_fd', side_effect=OSError(errno.EAGAIN, 'busy')), \
                mock.patch.object(status, 'APPROVAL_LOCK_TIMEOUT', 0.2):
            report = self.record()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('locked by another writer', report['reason'])

    def test_importing_the_lock_module_has_no_side_effects(self):
        code = (
            "import signal, threading, socket\n"
            "before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM), threading.active_count())\n"
            "opened = []\n"
            "real = socket.socket.bind\n"
            "socket.socket.bind = lambda self, *a, **k: (opened.append(a), real(self, *a, **k))[1]\n"
            "from skillsync_workflow import status\n"
            "status._lock_module()\n"
            "after = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM), threading.active_count())\n"
            "assert before == after, (before, after)\n"
            "assert not opened, opened\n"
            "print('ok')\n")
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, cwd=ROOT,
                                env={**self.env, 'PYTHONPATH': str(ROOT)})
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr), (0, 'ok', ''), result)

    def test_approvals_record_works_without_a_configured_prototype_server(self):
        report = self.record_cli('--sha', HEAD)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.record_cli('--sha', HEAD, code=2)['status'], 'BLOCKED', 'a repeat is still refused')

    def test_a_duplicate_entry_in_the_file_is_harmless_for_the_readers(self):
        entry = {'schema': 1, 'at': '2026-01-12T12:00:00Z', 'word': 'CP', 'sha': HEAD, 'by': 'user', 'source': 'cli', 'run': RUN}
        self.log.write_text((json.dumps(entry) + '\n') * 2, encoding='utf-8')
        rows = status.read_approvals(self.root)
        self.assertEqual([(row['word'], row['sha']) for row in rows], [('CP', HEAD)], 'collapsed to the latest per (run, word)')
        from skillsync_workflow import dashboard
        self.assertEqual(len(dashboard.run_approvals(self.root, RUN)), 2, 'the trail lists what was written, without a crash')


class RecordCli(RecordFixture):
    def test_exit_codes_and_json(self):
        ok = self.record_cli('--sha', HEAD, '--target', 'main', '--branch', 'feat/demo')
        self.assertEqual((ok['schema'], ok['status'], ok['entry']['source'], ok['entry']['target']), (1, 'PASS', 'cli', 'main'))
        self.assertEqual(self.record_cli('--sha', HEAD, code=2)['status'], 'BLOCKED')
        for extra, kwargs in ((('--sha', HEAD), {'word': 'MAYBE'}), (('--sha', HEAD), {'source': 'web'}),
                              (('--sha', HEAD), {'run': '../x'}), (('--sha', 'NOT-HEX'), {}), ((), {'word': 'READY'})):
            with self.subTest(extra=extra, **kwargs):
                report = self.record_cli(*extra, code=2, **kwargs)
                self.assertEqual((report['status'], report['entry']), ('BLOCKED', None))
        self.assertEqual(len(self.lines()), 1)

    def test_source_dashboard_is_refused_by_the_cli(self):
        for extra in (('--sha', HEAD), ()):
            with self.subTest(extra=extra):
                report = self.record_cli(*extra, source='dashboard', code=2)
                self.assertEqual((report['status'], report['entry']), ('BLOCKED', None))
                self.assertIn('only by the dashboard server', report['reason'])
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.record_cli('--sha', HEAD, source='ide')['entry']['source'], 'ide')
        self.assertEqual(self.record(source='dashboard', sha='b' * 40)['entry']['source'], 'dashboard',
                         'the in-process call of the server keeps the source')

    def test_go_needs_file_or_sha(self):
        git_commit_all(self.root, self.env)  # a readable HEAD must not become the sha of a GO consent
        plan = self.doc('plans', 'p.md')
        for word in ('GO plan', 'GO implement'):
            with self.subTest(word=word):
                report = self.record_cli(word=word, code=2)
                self.assertEqual((report['status'], report['entry']), ('BLOCKED', None))
                self.assertIn('needs --file or --sha', report['reason'])
        self.assertEqual(self.lines(), [])
        sha = status.git_blob_sha(plan.read_bytes())
        self.assertEqual(self.record_cli('--sha', sha, word='GO implement')['entry']['sha'], sha)
        blocked = self.record('GO plan', sha=None)
        self.assertIn('needs --file or --sha', blocked['reason'], 'the core function refuses it as well')

    def test_missing_project_is_blocked(self):
        result = self.run_cli('approvals', 'record', self.base / 'nope', '--word', 'CP', '--run', RUN, '--source', 'cli')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')

    def test_by_comes_from_the_login_name_and_is_redacted(self):
        fake = 'abcdef' + '123456'  # atrapa składana w czasie wykonania, by skaner sekretów nie widział literału
        env = {'LOGNAME': 'demo ' + 'to' + 'ken=' + fake, 'USER': 'demo', 'USERNAME': 'demo'}
        report = self.record_cli('--sha', HEAD, env=env)
        self.assertEqual(report['entry']['by'], 'demo ' + 'to' + 'ken=[REDACTED]')
        self.assertNotIn(fake, self.log.read_text(encoding='utf-8'))

    def test_round_trip_with_approvals_wait_and_status(self):
        self.record_cli('--sha', HEAD)
        waited = self.run_cli('approvals', 'wait', self.root, '--word', 'CP', '--run', RUN, '--sha', HEAD, '--timeout', '0')
        self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
        approval = json.loads(waited.stdout)['approval']
        self.assertEqual((approval['kind'], approval['run'], approval['sha'], approval['source']), ('entry', RUN, HEAD, 'cli'))
        self.assertEqual(status.find_approval(self.root, 'CP', RUN, HEAD)['word'], 'CP')

    def test_source_ide_passes_the_readers(self):
        plan = self.doc('plans', 'p.md')
        report = self.record_cli('--file', 'docs/plans/p.md', word='GO implement', source='ide')
        sha = status.git_blob_sha(plan.read_bytes())
        self.assertEqual((report['entry']['sha'], report['entry']['source']), (sha, 'ide'))
        waited = self.run_cli('approvals', 'wait', self.root, '--word', 'GO implement', '--sha', sha, '--timeout', '0')
        self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
        self.assertEqual(json.loads(waited.stdout)['approval']['source'], 'ide')
        rows = status.read_approvals(self.root)
        self.assertEqual([(row['word'], row['source'], row['file']) for row in rows],
                         [('GO implement', 'ide', 'docs/plans/p.md')])

    def test_wait_is_unchanged(self):
        result = self.run_cli('approvals', 'wait', self.root, '--word', 'CP', '--timeout', '0')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'TIMEOUT')


def normalized_after(report, head, entry):
    """`normalized` plus the recorded entry: its `at`, `by` and the HH:MM of its tip become fixed values (fixture
    `cp-ready/expected-after.json`)."""
    data = json.loads(json.dumps(report))
    stable = {**entry, 'at': '2026-01-12T12:00:00Z', 'by': 'user'}
    hhmm = entry['at'][11:16]  # the CLI runs with TZ=UTC (DashboardFixture), so the tip shows the UTC time
    for action in data['approvals']['actions']:
        if action['given'] == entry:
            action['given'] = stable
            for key in ('reason', 'blocked'):
                action[key] = action[key].replace(f'dana {hhmm}', 'dana 12:00')
    if data['approvals']['last'] == entry:
        data['approvals']['last'] = stable
    # a waiting window closed by the consent ends at the real recording time: pin it to the fixed `at` above
    timing = data.get('timing') or {}
    for window in timing.get('windows', []):
        if window.get('source') == 'approval':
            started = datetime.fromisoformat(window['startedAt'])
            ended = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)
            seconds = int((ended - started).total_seconds())
            timing['totals']['waitSeconds'] += seconds - window['seconds']
            window['endedAt'], window['seconds'] = ended.isoformat(), seconds
    return normalized(data, head)


class CpReadyFixture(DashboardFixture):
    def test_waiting_and_after_a_recorded_cp(self):
        root = self.project('cp-ready')
        before = self.cli('dashboard', 'show', root, '--json')
        self.assertEqual(normalized(before, self.head), (FIXTURES / 'cp-ready' / 'expected.json').read_text(encoding='utf-8'))
        self.assertEqual([(a['kind'], a['state']) for a in before['approvals']['actions']],
                         [('spec', 'after'), ('plan', 'after'), ('cp', 'waiting'), ('ready', 'waiting')])
        cp = next(a for a in before['approvals']['actions'] if a['kind'] == 'cp')
        # `source: dashboard` is written only in-process (the server's call); the CLI refuses that value
        recorded = status.record_approval(root, 'CP', cp['run'], source='dashboard', target=cp['target'])
        self.assertEqual((recorded['status'], recorded['entry']['sha']), ('PASS', self.head))
        after = self.cli('dashboard', 'show', root, '--json')
        states = {a['kind']: a for a in after['approvals']['actions']}
        self.assertEqual((states['cp']['state'], states['cp']['given'], states['ready']['state']),
                         ('after', recorded['entry'], 'waiting'))
        self.assertIsNotNone(states['cp']['given']['at'])
        self.assertEqual(normalized_after(after, self.head, recorded['entry']),
                         (FIXTURES / 'cp-ready' / 'expected-after.json').read_text(encoding='utf-8'))
        self.assertEqual(status.record_approval(root, 'CP', cp['run'], source='dashboard')['status'], 'BLOCKED')
        refused = self.cli('approvals', 'record', root, '--word', 'CP', '--run', cp['run'], '--source', 'cli', code=2)
        self.assertIn('already recorded', refused['reason'], 'the same HEAD from another source is the same consent')


PLAN_RUN = 'r1'


class PlanStateRecord(RecordFixture):
    """K13: `CP`/`READY` entries carry `planState` (status of the plan whose `Przebieg:` names the run, at the moment of consent)."""
    SHA2 = 'e' * 40

    def setUp(self):
        super().setUp()
        self.plan_path = self.root / 'docs' / 'plans' / 'p.md'
        self.plan_path.parent.mkdir(parents=True)

    def set_plan(self, state, run=PLAN_RUN):
        self.plan_path.write_text(f'# Plan: demo\n\nStatus: {state}\nPrzebieg: {run}\n', encoding='utf-8')

    def test_the_entry_carries_the_plan_state_of_the_run_and_keeps_the_plan_path(self):
        self.set_plan('in_progress')
        entry = self.record(plan='docs/plans/p.md')['entry']
        self.assertEqual((entry['planState'], entry['plan']), ('in_progress', 'docs/plans/p.md'))
        self.assertEqual(self.lines()[0]['planState'], 'in_progress')

    def test_the_state_comes_from_the_run_not_from_the_plan_path_argument(self):
        self.set_plan('done')
        other = self.root / 'docs' / 'plans' / 'other.md'
        other.write_text('# Plan: other\n\nStatus: draft\nPrzebieg: r2\n', encoding='utf-8')
        self.assertEqual(self.record(plan='docs/plans/other.md')['entry']['planState'], 'done')

    def test_no_plan_for_the_run_or_an_unknown_status_leaves_the_field_out(self):
        self.assertNotIn('planState', self.record()['entry'])
        self.set_plan('weird', run=PLAN_RUN)
        self.assertNotIn('planState', self.record(sha=self.SHA2)['entry'])

    def test_go_words_have_no_plan_state(self):
        self.set_plan('draft')
        self.assertNotIn('planState', self.record('GO plan')['entry'])

    def test_the_duplicate_key_is_word_sha_and_plan_state(self):
        self.set_plan('in_progress')
        self.assertEqual(self.record()['status'], 'PASS')
        self.assertEqual(self.record()['status'], 'BLOCKED', 'same sha, same plan state')
        self.set_plan('done')
        again = self.record()
        self.assertEqual((again['status'], again['entry']['planState']), ('PASS', 'done'), 'CP after the closing at the same HEAD')
        refused = self.record()
        self.assertEqual(refused['status'], 'BLOCKED')
        self.assertIn('already recorded', refused['reason'])
        self.assertEqual(self.record(word='READY')['status'], 'PASS', 'another word is another key')

    def test_the_duplicate_is_compared_with_the_last_entry_of_the_word_scenario_a(self):
        self.set_plan('in_progress')
        self.assertEqual(self.record()['status'], 'PASS')
        self.set_plan('done')
        self.assertEqual(self.record()['status'], 'PASS')
        self.set_plan('in_progress')                                       # reopened without a commit: the button is waiting again
        again = self.record()
        self.assertEqual((again['status'], again['entry']['planState']), ('PASS', 'in_progress'), 'not a duplicate of the LAST entry (done)')
        self.assertEqual(self.record()['status'], 'BLOCKED', 'a real duplicate of the last entry stays refused')

    def test_the_duplicate_is_compared_with_the_last_entry_of_the_word_scenario_b(self):
        self.set_plan('done')
        self.assertEqual(self.record()['status'], 'PASS')
        self.set_plan('in_progress')
        self.assertEqual(self.record()['status'], 'PASS')
        self.set_plan('done')
        self.assertEqual(self.record()['status'], 'PASS')
        self.assertEqual(self.record()['status'], 'BLOCKED')
        self.assertEqual([row['planState'] for row in self.lines()], ['done', 'in_progress', 'done'])

    def test_the_plan_state_is_read_under_the_file_lock_unless_given(self):
        self.set_plan('in_progress')
        seen = []
        real = status.plan_state_for_run

        def spy(root, run_id):
            seen.append((self.log.parent / 'approvals.jsonl.lock').exists())
            return real(root, run_id)
        with mock.patch.object(status, 'plan_state_for_run', spy):
            self.assertEqual(self.record()['status'], 'PASS')
            self.assertEqual(seen, [True], 'read once, while the lock file is held')
            self.assertEqual(self.record(sha=self.SHA2, plan_state='done')['entry']['planState'], 'done')
            self.assertEqual(seen, [True], 'an explicit plan_state is not read again')

    def test_an_old_entry_without_the_field_does_not_block_the_same_sha_with_a_plan_state(self):
        self.log.write_text(json.dumps({'schema': 1, 'at': '2026-01-12T10:00:00Z', 'word': 'CP', 'sha': HEAD, 'by': 'u', 'source': 'cli'}) + '\n',
                            encoding='utf-8')
        self.assertEqual(self.record()['status'], 'BLOCKED', 'no plan: the key is (word, sha) as before')
        self.set_plan('in_progress')
        self.assertEqual(self.record()['status'], 'PASS')

    def test_the_cli_fills_the_state_and_read_approvals_carries_it(self):
        self.set_plan('in_progress')
        written = self.record_cli('--sha', HEAD)['entry']
        self.assertEqual(written['planState'], 'in_progress')
        (row,) = status.read_approvals(self.root)
        self.assertEqual(row['planState'], 'in_progress')

    def test_read_approvals_drops_a_foreign_plan_state(self):
        self.log.write_text(json.dumps({'schema': 1, 'at': '2026-01-12T10:00:00Z', 'word': 'CP', 'sha': HEAD, 'planState': 'x' * 500}) + '\n',
                            encoding='utf-8')
        (row,) = status.read_approvals(self.root)
        self.assertNotIn('planState', row)


class PlanStateButton(unittest.TestCase):
    """K13: the `after` state of a CP/READY button needs an entry with the HEAD sha and `planState` equal to the current plan status."""
    HEAD = 'c' * 40
    WORDS = [{'word': 'CP', 'active': True, 'tip': 'wypchnij', 'target': 'main'}]

    def cp(self, plan_status, log, tracked=True):
        from skillsync_workflow import dashboard
        plan = {'status': plan_status, 'statusText': '', 'path': 'p'}
        found = [b for b in dashboard.consent_buttons({'exists': False}, plan, [], self.WORDS, log, self.HEAD, True,
                                                      run_plan_state=plan_status, tracked=tracked) if b['kind'] == 'cp']
        return found[0]

    def entry(self, **extra):
        return {'word': 'CP', 'at': '2026-01-12T10:00:00Z', 'sha': self.HEAD, **extra}

    def test_a_matching_plan_state_is_after_and_a_stale_one_is_waiting_in_both_modes(self):
        for tracked in (True, False):
            with self.subTest(tracked=tracked):
                self.assertEqual(self.cp('done', [self.entry(planState='done')], tracked)['state'], 'after')
                self.assertEqual(self.cp('done', [self.entry(planState='in_progress')], tracked)['state'], 'waiting')
                self.assertEqual(self.cp('in_progress', [self.entry(planState='in_progress')], tracked)['state'], 'after')

    def test_an_entry_without_the_field_counts_as_before_except_untracked_done(self):
        self.assertEqual(self.cp('in_progress', [self.entry()])['state'], 'after')
        self.assertEqual(self.cp('done', [self.entry()], tracked=True)['state'], 'after')
        self.assertEqual(self.cp('in_progress', [self.entry()], tracked=False)['state'], 'after')
        self.assertEqual(self.cp('done', [self.entry()], tracked=False)['state'], 'waiting')

    def test_the_last_entry_of_the_word_decides_as_before(self):
        log = [self.entry(planState='in_progress'), self.entry(at='2026-01-12T11:00:00Z', planState='done')]
        self.assertEqual(self.cp('done', log)['state'], 'after')
        self.assertEqual(self.cp('done', log[::-1])['state'], 'waiting')


if __name__ == '__main__':
    unittest.main()
