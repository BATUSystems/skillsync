"""CORE-1190-DBVERIFY: `db_snapshot.py verify` checks a copy WITHOUT restoring it and changes nothing.
Spec: docs/specs/2026-11-26-core-1190-dbverify-db-snapshot-verify.md (ops). Exit codes: 0 PASS, 1 the copy is damaged or does not match its sidecar,
2 it could not be checked, 3 a dry run or no sqlcmd. Fakes of the engine and of the file system: the patterns of tests/test_skillsync_db_snapshot.py."""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_skillsync_db_snapshot import (BAK_SIZE, BATCH_ERROR, DROP, FAKE_SHA, LOCAL_DIR, NO_SIDECAR, SRC_NAME, SRC_PATH, Base, RestoreFs,
                                              RESTORE_HEADER_COLUMNS, LinkFs, RestoreRunner, ShaFs, restore_header_out, source_sidecar, table)

ENV = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state'}
CHECKS_ALL = ['location', 'sidecar', 'size', 'sha256', 'engine', 'header', 'verifyonly']


class VerifyBase(Base):
    def run_verify(self, runner=None, fs=None, guard=True, **kw):
        runner = runner or RestoreRunner(self.m)
        fs = fs or RestoreFs()
        self.state = self.m.RunState()
        self.runner, self.fs = runner, fs
        values = {'file': SRC_PATH, 'dir': LOCAL_DIR}
        values.update(kw)
        call = self.m.guard_runner(runner, self.state) if guard else runner
        return self.m.run_verify(self.m.VerifyOptions(**values), runner=call, fs=fs, environ=ENV, platform='linux')

    def assert_nothing_changed(self):
        self.assertEqual(self.runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual((self.fs.made, self.fs.written), ([], {}))

    def results(self, result):
        return {c['check']: c['result'] for c in result['checks']}


class Passes(VerifyBase):
    def test_a_good_copy_passes_every_check_and_changes_nothing(self):
        result = self.run_verify(fs=ShaFs())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual([c['check'] for c in result['checks']], CHECKS_ALL)
        self.assertTrue(all(c['result'] == 'PASS' for c in result['checks']))
        self.assertEqual(self.runner.kinds, ['identity', 'header', 'verify'])
        self.assertEqual(result['database'], 'appdb')
        self.assert_nothing_changed()

    def test_the_database_option_must_match_the_sidecar_and_can_be_left_out(self):
        self.assertEqual(self.run_verify(fs=ShaFs(), database='appdb')['status'], 'PASS')
        with self.assertRaises(self.m.Refusal):
            self.run_verify(fs=ShaFs(), database='otherdb')
        self.assertEqual(self.runner.calls, [])

    def test_a_sidecar_without_a_digest_passes_with_a_warning(self):
        fs = ShaFs(sidecar=source_sidecar(sha256=DROP))
        result = self.run_verify(fs=fs)
        self.assertEqual(result['status'], 'PASS')
        self.assertNotIn('sha256', self.results(result))
        self.assertTrue(any('sha256' in w for w in result['warnings']))

    def test_offline_never_touches_the_engine(self):
        result = self.run_verify(fs=ShaFs(), offline=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(self.runner.calls, [])
        self.assertEqual([c['check'] for c in result['checks']], ['location', 'sidecar', 'size', 'sha256'])
        self.assertTrue(any('offline' in w for w in result['warnings']))

    def test_a_missing_sidecar_is_allowed_only_with_the_flag_and_then_only_the_engine_checks_run(self):
        with self.assertRaises(self.m.Refusal):
            self.run_verify(fs=RestoreFs(sidecar=NO_SIDECAR))
        result = self.run_verify(fs=RestoreFs(sidecar=NO_SIDECAR), allow_missing_sidecar=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual([c['check'] for c in result['checks']], ['location', 'engine', 'header', 'verifyonly'])
        self.assertTrue(any('sidecar' in w for w in result['warnings']))
        self.assert_nothing_changed()


class Fails(VerifyBase):
    def test_a_changed_size_fails_before_the_engine(self):
        result = self.run_verify(fs=ShaFs(bak_size=BAK_SIZE + 1))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(self.results(result)['size'], 'FAIL')
        self.assertEqual(self.runner.calls, [])

    def test_a_changed_digest_fails_before_the_engine(self):
        result = self.run_verify(fs=ShaFs(digest='cd' * 32))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(self.results(result)['sha256'], 'FAIL')
        self.assertEqual(self.runner.calls, [])

    def test_a_rejected_verifyonly_fails(self):
        result = self.run_verify(fs=ShaFs(), runner=RestoreRunner(self.m, {'verify': (1, '', BATCH_ERROR)}))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(self.results(result)['verifyonly'], 'FAIL')
        self.assert_nothing_changed()

    def test_a_header_that_does_not_fit_fails_and_verifyonly_is_not_run(self):
        for name, change in (('other database', {'DatabaseName': 'otherdb'}), ('not a full backup', {'BackupType': '5'}),
                             ('guid differs from the sidecar', {'BackupSetGUID': '99999999-2222-3333-4444-555555555555'}),
                             ('lsn differs from the sidecar', {'LastLSN': '999'}), ('finish date differs', {'BackupFinishDate': '2026-01-01 00:00:00.000'}),
                             ('hostile value', {'DatabaseName': "x'; DROP"})):
            with self.subTest(name):
                result = self.run_verify(fs=ShaFs(), runner=RestoreRunner(self.m, {'header': (0, restore_header_out(**change), '')}))
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(self.results(result)['header'], 'FAIL')
                self.assertEqual(self.runner.kinds, ['identity', 'header'])
                self.assert_nothing_changed()

    def test_an_empty_header_fails(self):
        runner = RestoreRunner(self.m, {'header': (0, table(RESTORE_HEADER_COLUMNS, []), '')})
        result = self.run_verify(fs=ShaFs(), runner=runner)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(self.runner.kinds, ['identity', 'header'])


class Blocked(VerifyBase):
    def refused(self, fs=None, **kw):
        runner = RestoreRunner(self.m)
        with self.assertRaises(self.m.Refusal) as ctx:
            self.run_verify(runner=runner, fs=fs or ShaFs(), **kw)
        self.assertEqual(runner.calls, [])
        return str(ctx.exception)

    def test_rules_of_the_location_and_the_sidecar_block(self):
        self.assertIn('file-missing', self.refused(file=LOCAL_DIR + '/missing.bak'))
        self.assertIn('file-location', self.refused(file='/h/other/' + SRC_NAME))
        self.assertIn('sidecar', self.refused(fs=ShaFs(sidecar=source_sidecar(database='otherdb')), database='appdb'))
        self.assertIn('sidecar', self.refused(fs=ShaFs(sidecar=source_sidecar(file='another.bak'))))
        self.assertIn('sidecar', self.refused(fs=ShaFs(sidecar=b'not json')))
        self.assertIn('server', self.refused(fs=ShaFs(sidecar=source_sidecar(server={**source_sidecar()['server'], 'canonical': 'remote'}))))

    def test_a_symbolic_link_and_a_copy_inside_a_repository_are_refused(self):
        self.assertIn('file-symlink', self.refused(fs=LinkFs({SRC_PATH: '/elsewhere/' + SRC_NAME})))
        self.refused(fs=ShaFs(git_dirs={LOCAL_DIR + '/.git'}))

    def test_a_sidecar_without_a_size_fails_before_the_engine_and_offline_needs_a_sidecar(self):
        result = self.run_verify(fs=ShaFs(sidecar=source_sidecar(size=None)))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(self.runner.calls, [])
        self.assertIn('offline-needs-sidecar', self.refused(fs=RestoreFs(sidecar=NO_SIDECAR), offline=True, allow_missing_sidecar=True))

    def test_a_remote_server_is_refused(self):
        self.assertIn('verify-local-only', self.refused(server='db.example.com'))

    def test_an_engine_that_does_not_answer_is_blocked_not_failed(self):
        runner = RestoreRunner(self.m, {'identity': (1, '', BATCH_ERROR)})
        with self.assertRaises(self.m.SqlFailure):
            self.run_verify(fs=ShaFs(), runner=runner)
        self.assertEqual(self.runner.kinds, ['identity'])
        self.assertEqual(self.m.exit_code_for(self.m.SqlFailure('x'), self.state), 2)
        self.assert_nothing_changed()

    def test_a_header_that_cannot_be_read_is_blocked(self):
        runner = RestoreRunner(self.m, {'header': (1, '', BATCH_ERROR)})
        with self.assertRaises(self.m.SqlFailure):
            self.run_verify(fs=ShaFs(), runner=runner)
        self.assertEqual(self.runner.kinds, ['identity', 'header'])


class DryRun(VerifyBase):
    def test_a_dry_run_lists_the_plan_and_calls_nothing(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                with self.assertRaises(self.m.NotRun) as ctx:
                    self.run_verify(fs=ShaFs(), dry_run=True, offline=offline)
                result = ctx.exception.result
                self.assertEqual(result['status'], 'NOT_RUN')
                self.assertEqual(any('VERIFYONLY' in step for step in result['plan']), not offline)
                self.assertEqual(self.runner.calls, [])
                self.assert_nothing_changed()


class CommandLine(Base):
    """The whole command with the real file system (a temporary copy directory outside any repository), without an engine (--offline, --dry-run)."""

    def call_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = self.m.main(argv, environ={'HOME': self.tmp}, stdout=out, stderr=err, platform=sys.platform)
        return code, out.getvalue(), err.getvalue()

    def make_copy(self, directory, content=b'bak-bytes', **changes):
        path = str(Path(directory) / SRC_NAME)
        Path(path).write_bytes(content)
        side = source_sidecar(path, size=len(content), sha256=hashlib.sha256(content).hexdigest(), **changes)
        Path(path + '.json').write_text(json.dumps(side), encoding='utf-8')
        return path

    def test_exit_codes_and_json_without_an_engine(self):
        with tempfile.TemporaryDirectory(prefix='db-verify-') as tmp:
            self.tmp = tmp
            path = self.make_copy(tmp)
            code, out, _ = self.call_main(['verify', '--file', path, '--dir', tmp, '--offline', '--json'])
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)['status'], 'PASS')
            code, out, _ = self.call_main(['verify', '--file', path, '--dir', tmp, '--dry-run'])
            self.assertEqual(code, 3, out)
            Path(path).write_bytes(b'tampered bytes!')
            code, out, _ = self.call_main(['verify', '--file', path, '--dir', tmp, '--offline', '--json'])
            self.assertEqual(code, 1, out)
            self.assertEqual(json.loads(out)['status'], 'FAIL')
            Path(path + '.json').unlink()
            code, _, err = self.call_main(['verify', '--file', path, '--dir', tmp, '--offline'])
            self.assertEqual(code, 2, err)
            self.assertFalse(Path(path + '.json').exists())
            self.assertEqual(Path(path).read_bytes(), b'tampered bytes!')

    def test_the_help_lists_the_subcommand_and_the_flags(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.m.build_parser().parse_args(['verify', '--help'])
        for word in ('--file', '--offline', '--allow-missing-sidecar', '--dry-run', '--json'):
            self.assertIn(word, out.getvalue())


if __name__ == '__main__':
    unittest.main()
