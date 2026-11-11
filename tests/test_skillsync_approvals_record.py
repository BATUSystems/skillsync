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
                 ({'target': '-x'}, 'Invalid --target'), ({'branch': 'a b'}, 'Invalid --branch'))
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

    def test_a_held_lock_blocks_after_the_timeout_and_a_stale_lock_is_removed(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        lock.write_text('1', encoding='utf-8')
        with mock.patch.object(status, 'APPROVAL_LOCK_TIMEOUT', 0.1):
            report = self.record()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('locked', report['reason'])
        self.assertTrue(lock.exists(), 'a live lock of another writer is not removed')
        old = time.time() - status.APPROVAL_LOCK_STALE - 5
        os.utime(lock, (old, old))
        self.assertEqual(self.record()['status'], 'PASS')
        self.assertFalse(lock.exists())


class RecordLockTakeOver(RecordFixture):
    """m1: a stale lock is taken over by an atomic rename; a fresh lock of another writer is never removed."""

    def stale_lock(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        lock.write_text('1', encoding='utf-8')
        old = time.time() - status.APPROVAL_LOCK_STALE - 5
        os.utime(lock, (old, old))
        return lock

    def test_a_late_take_over_does_not_remove_the_fresh_lock_of_another_writer(self):
        lock = self.stale_lock()
        seen = lock.lstat()  # waiter B judged this lock stale ...
        first = status._ApprovalLock(lock)
        first.__enter__()  # ... but waiter A took it over first and holds a fresh lock
        fresh = lock.read_bytes()
        late = status._ApprovalLock(lock, timeout=0)
        late._take_over(seen)  # B's rename now catches A's lock and has to put it back
        self.assertEqual(lock.read_bytes(), fresh)
        self.assertTrue(first._owned(), 'A still owns its lock')
        with self.assertRaises(ValueError):
            late.__enter__()  # a fresh lock is waited for, not taken over
        late.__exit__(None, None, None)
        self.assertTrue(lock.exists(), 'a writer that never got the lock does not remove it')
        first.__exit__(None, None, None)
        self.assertFalse(lock.exists())
        self.assertEqual(sorted(path.name for path in lock.parent.iterdir() if '.stale-' in path.name), [])

    def test_exit_keeps_a_lock_that_is_no_longer_ours(self):
        lock = self.log.parent / 'approvals.jsonl.lock'
        held = status._ApprovalLock(lock)
        held.__enter__()
        lock.unlink()
        lock.write_text('other-writer', encoding='utf-8')
        held.__exit__(None, None, None)
        self.assertEqual(lock.read_text(encoding='utf-8'), 'other-writer')

    def test_two_threads_and_a_stale_lock_write_exactly_once(self):
        import threading
        self.stale_lock()
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
        self.assertEqual([path.name for path in self.log.parent.iterdir() if 'lock' in path.name], [])


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


if __name__ == '__main__':
    unittest.main()
