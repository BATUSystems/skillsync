"""Niezależne testy Dostawy 2 (CORE-1142-T6): `db_snapshot.py restore --move existing|new` i podpowiedź A6.
Źródło oczekiwań: spec docs/specs/2026-11-23-db-snapshot-move-i-retencja.md (rewizja 8: A0-A6, K1, K1b, K2, K3, K4) i dowody sond S1
(docs/probes/t6-s1/evidence-2026-11-23.md). Atrapy silnika i systemu plików: wzorce z tests/test_skillsync_db_snapshot.py. Bez silnika, zegara
i losowości (token i czas wstrzykiwane). Złoty wzorzec K1 (`--move none`) pilnuje osobny plik tests/test_skillsync_db_snapshot_restore_golden.py."""
import contextlib
import io
import itertools
import json
import subprocess
import unittest

from tests.test_skillsync_db_snapshot import (Base, BATCH_ERROR, ERROR_OUTPUT, FIXED_NOW, FIXED_TOKEN, LOCAL_DIR, NO_SIDECAR, RestoreBase, RestoreFs,
                                              RestoreRunner, SAFETY_PATH, SRC_PATH, STATE_COLUMNS, WIN_DIR, WIN_SAFETY, WIN_SRC, restore_header_out,
                                              state_out, table)

FL_COLUMNS = ['LogicalName', 'PhysicalName', 'Type', 'FileGroupName', 'Size', 'MaxSize', 'FileId']
DB_COLUMNS = ['name', 'type_desc', 'physical_name']
DEFAULT_COLUMNS = ['data_path', 'log_path']
COPY_MARK = 'from_the_copy'
NOT_THERE = table(STATE_COLUMNS, [])
UNSAFE_PATH_HINT = ('--move-dir', '--move new')


def fl(name, kind, file_id, group='PRIMARY'):
    """One row of RESTORE FILELISTONLY; the physical name is the path stored in the copy (it must never reach MOVE)."""
    return [name, 'C:\\old\\' + COPY_MARK + '_' + str(file_id).strip() + '.dat', kind, group, '8388608', '35184372080640', str(file_id)]


def filelist_out(rows=None, columns=None):
    return table(columns or FL_COLUMNS, BACKUP_ROWS if rows is None else rows)


def dbfiles_out(rows=None, columns=None):
    return table(columns or DB_COLUMNS, DB_ROWS if rows is None else rows)


def defaults_out(data_path, log_path):
    return table(DEFAULT_COLUMNS, [[data_path, log_path]])


# The layout of the S1 probe: FileId 1, 3, 2 in the order of the rows = data, data (FG2), log; so by FileId: data, log, data2.
BACKUP_ROWS = [fl('appdb_data', 'D', 1), fl('appdb_data2', 'D', 3, 'FG2'), fl('appdb_log', 'L', 2, 'NULL')]
DB_ROWS = [['appdb_data', 'ROWS', '/var/sql/data/appdb.mdf'], ['appdb_log', 'LOG', '/var/sql/log/appdb_log.ldf'],
           ['appdb_data2', 'ROWS', '/var/sql/data/appdb_2.ndf']]
EXISTING_MOVES = ("MOVE N'appdb_data' TO N'/var/sql/data/appdb.mdf', MOVE N'appdb_log' TO N'/var/sql/log/appdb_log.ldf', "
                  "MOVE N'appdb_data2' TO N'/var/sql/data/appdb_2.ndf'")
EXISTING_BATCH = ("BEGIN TRY ALTER DATABASE [appdb] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; RESTORE DATABASE [appdb] FROM DISK = N'" + SRC_PATH
                  + "' WITH REPLACE, " + EXISTING_MOVES + ", FILE = 1, CHECKSUM; ALTER DATABASE [appdb] SET MULTI_USER; END TRY BEGIN CATCH "
                  "BEGIN TRY ALTER DATABASE [appdb] SET MULTI_USER; END TRY BEGIN CATCH DECLARE @i int; END CATCH; THROW; END CATCH")
FILELIST_SQL = "RESTORE FILELISTONLY FROM DISK = N'" + SRC_PATH + "' WITH FILE = 1"
WIN_ENV = {'LOCALAPPDATA': 'D:\\h\\local'}
SET_MULTI_USER = 'ALTER DATABASE [appdb] SET MULTI_USER'


def new_moves(directory, token=FIXED_TOKEN, log_directory=None, sep='/'):
    """MOVE clauses of the S1 layout for --move new: by FileId data=1 (mdf _1), log=2 (ldf _2), data2=3 (ndf _3)."""
    log_directory = directory if log_directory is None else log_directory
    return ("MOVE N'appdb_data' TO N'" + directory + sep + 'appdb_1_' + token + ".mdf', MOVE N'appdb_log' TO N'" + log_directory + sep + 'appdb_2_'
            + token + ".ldf', MOVE N'appdb_data2' TO N'" + directory + sep + 'appdb_3_' + token + ".ndf'")


def derived_db_rows(rows):
    """Files of the target database that fit the given backup rows one to one (so only the validation of the copy can refuse)."""
    result = []
    for index, row in enumerate(rows):
        extension = 'ldf' if row[2] == 'L' else 'mdf'
        result.append([row[0], 'LOG' if row[2] == 'L' else 'ROWS', '/var/sql/f' + str(index) + '.' + extension])
    return result


def classify_move(sql):
    if sql.startswith('RESTORE FILELISTONLY'):
        return 'filelist'
    if 'sys.master_files' in sql:
        return 'dbfiles'
    if 'InstanceDefaultDataPath' in sql:
        return 'defaults'
    if sql.startswith('RESTORE DATABASE'):
        return 'restore'
    if sql.startswith('SELECT state_desc'):
        return 'state'
    if sql.startswith('SELECT @@SERVERNAME'):
        return 'identity'
    if sql.startswith('BEGIN TRY'):
        return 'restore'
    if sql.startswith('BACKUP DATABASE'):
        return 'backup'
    if sql.startswith('RESTORE VERIFYONLY'):
        return 'verify'
    if sql.startswith('RESTORE HEADERONLY'):
        return 'header'
    raise AssertionError('an unexpected call to the engine: ' + sql)


class MoveRunner(RestoreRunner):
    """RestoreRunner that also answers FILELISTONLY, sys.master_files and the default paths (the last item of a script repeats)."""

    def __init__(self, m, script=None):
        merged = {'filelist': [(0, filelist_out(), '')], 'dbfiles': [(0, dbfiles_out(), '')],
                  'defaults': [(0, defaults_out('/var/opt/data/', '/var/opt/log/'), '')]}
        merged.update(script or {})
        super().__init__(m, merged)

    def __call__(self, sql, *, mutating, timeout, table=False):
        kind = classify_move(sql)
        self.calls.append({'kind': kind, 'sql': sql, 'mutating': mutating, 'timeout': timeout, 'table': table})
        index = self.seen.get(kind, 0)
        self.seen[kind] = index + 1
        items = self.script[kind]
        item = items[min(index, len(items) - 1)]
        if isinstance(item, BaseException):
            raise item
        rc, out, err = item
        return self.m.RunResult(rc, out, err)

    def sql_of(self, kind):
        return [c['sql'] for c in self.calls if c['kind'] == kind]


class MoveFs(RestoreFs):
    def __init__(self, dirs=(), **kw):
        super().__init__(**kw)
        self.dirs = set(dirs)

    def isdir(self, path):
        self.checked.append(('isdir', path))
        return path in self.dirs


class MoveBase(RestoreBase):
    def engine(self, move, **script):
        """A runner for the mode: for --move new the database does not exist at first (the later state reads say ONLINE)."""
        merged = {key: value if isinstance(value, list) else [value] for key, value in script.items()}
        if move == 'new' and 'state' not in merged:
            merged['state'] = [(0, NOT_THERE, ''), (0, state_out(), '')]
        return MoveRunner(self.m, merged)

    def go(self, runner=None, fs=None, platform='linux', token_fn=None, environ=None, state=None, **kw):
        runner = runner or MoveRunner(self.m)
        fs = fs or MoveFs()
        state = state or self.m.RunState()
        env = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state'} if environ is None else environ
        self.runner, self.fs, self.state = runner, fs, state
        return self.m.run_restore(self.opts(**kw), runner=self.m.guard_runner(runner, state), fs=fs, clock=lambda: FIXED_NOW,
                                  token_fn=token_fn or (lambda: FIXED_TOKEN), state=state, environ=env, platform=platform, machine_name='LAB01',
                                  emit=lambda text: None)

    def code(self, **kw):
        state = self.m.RunState()
        err = io.StringIO()
        code = self.m.run_guarded(lambda: self.go(state=state, **kw), state, err, self.m.Masker([]))
        return code, err.getvalue()

    def windows(self, **kw):
        values = dict(platform='win32', dir=WIN_DIR, file=WIN_SRC, environ=WIN_ENV, fs=MoveFs(src=WIN_SRC))
        values.update(kw)
        return values

    def assert_refused_without_calls(self, **kw):
        """Refusal (exit code 2) from the validation of the arguments: no call to the engine at all, nothing marked as changed."""
        runner = MoveRunner(self.m)
        with self.assertRaises(self.m.Refusal):
            self.go(runner=runner, **kw)
        self.assertEqual(runner.calls, [])
        self.assertIs(self.state.mutated, False)
        again = MoveRunner(self.m)
        code, _ = self.code(runner=again, **kw)
        self.assertEqual(code, 2)
        self.assertEqual(again.calls, [])

    def restore_sql(self, runner):
        batches = [c for c in runner.calls if c['kind'] == 'restore']
        self.assertEqual(len(batches), 1)
        return batches[0]['sql']

    def assert_refused_before_the_backup(self, move, runner, **kw):
        """Exit code 2, no backup and no RESTORE, the state never marked as changed; returns the text of the refusal."""
        with self.assertRaises((self.m.Refusal, self.m.SqlFailure)) as ctx:
            self.go(runner=runner, move=move, **kw)
        self.assertNotIn('backup', runner.kinds)
        self.assertNotIn('restore', runner.kinds)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        again = self.engine(move, **{key: value for key, value in runner.script.items() if key != 'state'})
        code, _ = self.code(runner=again, move=move, **kw)
        self.assertEqual(code, 2)
        return str(ctx.exception)


# ---------------------------------------------------------------- K1 / K1b: the default mode and the hint of A6

class DefaultModeStaysTheSame(MoveBase):
    def test_none_never_reads_the_file_layout_and_adds_no_move_clause(self):
        result = self.go()
        self.assertEqual(result['status'], 'PASS')
        for kind in ('filelist', 'dbfiles', 'defaults'):
            self.assertNotIn(kind, self.runner.kinds)
        self.assertNotIn('MOVE', self.restore_sql(self.runner))
        self.assertNotIn('created', result)

    def test_explicit_none_is_the_same_as_the_default(self):
        self.go()
        default_calls = [(c['kind'], c['mutating'], c['table'], c['sql']) for c in self.runner.calls]
        self.go(move='none')
        self.assertEqual([(c['kind'], c['mutating'], c['table'], c['sql']) for c in self.runner.calls], default_calls)


class WayBackHintA6(RestoreBase):
    def failing_batch(self, access, state='ONLINE', **kw):
        runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out(state, access), '')]})
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner, **kw)
        self.assertIs(self.state.mutated, True)
        return str(ctx.exception)

    def test_a_restricted_online_database_gets_set_multi_user_before_the_restore_command(self):
        for access in ('SINGLE_USER', 'RESTRICTED_USER'):
            with self.subTest(access=access):
                message = self.failing_batch(access)
                self.assertIn(SET_MULTI_USER, message)
                self.assertLess(message.index(SET_MULTI_USER), message.index(SAFETY_PATH))
                self.assertIn('restore --file ' + SAFETY_PATH, message)

    def test_without_a_safety_backup_the_hint_is_there_and_no_restore_command_is_invented(self):
        message = self.failing_batch('SINGLE_USER', no_safety_backup=True)
        self.assertIn(SET_MULTI_USER, message)
        self.assertNotIn('restore --file', message)
        self.assertIn('no way back', message)

    def test_exit_code_is_1_and_the_hint_reaches_stderr(self):
        runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('ONLINE', 'SINGLE_USER'), '')]})
        code, text = self.do_code(runner=runner)
        self.assertEqual(code, 1)
        self.assertIn(SET_MULTI_USER, text)
        self.assertLess(text.index(SET_MULTI_USER), text.index(SAFETY_PATH))

    def test_the_other_states_get_no_hint(self):
        cases = {'online multi user': ('MULTI_USER', 'ONLINE'), 'restoring': ('MULTI_USER', 'RESTORING'),
                 'restoring and single user': ('SINGLE_USER', 'RESTORING')}
        for name, (access, state) in cases.items():
            for safety in (True, False):
                with self.subTest(case=name, safety=safety):
                    message = self.failing_batch(access, state, no_safety_backup=not safety)
                    self.assertNotIn('SET MULTI_USER', message)
                    if safety:
                        self.assertIn('The way back: restore the safety backup ' + SAFETY_PATH + ' (command: ', message)
                    else:
                        self.assertIn('no way back', message)

    def test_an_unreadable_state_gets_no_hint(self):
        for item in ((1, '', ERROR_OUTPUT), (0, table(STATE_COLUMNS, []), ''), subprocess.TimeoutExpired('sqlcmd', 30.0)):
            with self.subTest(item=repr(item)):
                runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), item]})
                with self.assertRaises(self.m.SqlFailure) as ctx:
                    self.do_restore(runner=runner)
                self.assertNotIn('SET MULTI_USER', str(ctx.exception))
                self.assertIn('unknown', str(ctx.exception))

    def test_after_a_successful_batch_a_single_user_state_gets_the_hint_too(self):
        runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), (0, state_out('ONLINE', 'SINGLE_USER'), '')]})
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner)
        message = str(ctx.exception)
        self.assertIn(SET_MULTI_USER, message)
        self.assertLess(message.index(SET_MULTI_USER), message.index(SAFETY_PATH))

    def test_after_a_successful_batch_other_bad_states_get_no_hint(self):
        for state in (state_out('RESTORING'), state_out('OFFLINE', 'SINGLE_USER')):
            with self.subTest(state=state):
                runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), (0, state, '')]})
                with self.assertRaises(self.m.SqlFailure) as ctx:
                    self.do_restore(runner=runner)
                self.assertNotIn('SET MULTI_USER', str(ctx.exception))

    def test_the_hint_applies_to_a_failed_move_existing_batch_as_well(self):
        runner = MoveRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('ONLINE', 'SINGLE_USER'), '')]})
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner, move='existing')
        message = str(ctx.exception)
        self.assertIn(SET_MULTI_USER, message)
        self.assertLess(message.index(SET_MULTI_USER), message.index(SAFETY_PATH))

    def test_way_back_unit(self):
        plain = self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'ONLINE')
        hinted = self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'ONLINE', access='SINGLE_USER')
        self.assertTrue(hinted.endswith(plain))
        self.assertIn(SET_MULTI_USER, hinted)
        self.assertEqual(self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'ONLINE', access='MULTI_USER'), plain)
        self.assertEqual(self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'ONLINE', access=None), plain)
        self.assertEqual(self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'RESTORING', access='SINGLE_USER'),
                         self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'RESTORING'))
        self.assertIn(SET_MULTI_USER, self.m._way_back(None, 'appdb', None, 'ONLINE', access='SINGLE_USER'))

    def test_access_of_reads_the_mode_out_of_the_state_text(self):
        self.assertEqual(self.m._access_of('state_desc=ONLINE, user_access_desc=SINGLE_USER'), 'SINGLE_USER')
        self.assertEqual(self.m._access_of('state_desc=ONLINE, user_access_desc=MULTI_USER'), 'MULTI_USER')
        self.assertIsNone(self.m._access_of('unknown'))


# ---------------------------------------------------------------- K2: the matrix of modes and flags (A1)

class FlagMatrix(MoveBase):
    def test_none_with_move_dir_is_refused_before_any_call(self):
        self.assert_refused_without_calls(move='none', move_dir='/data/sql')
        self.assert_refused_without_calls(move_dir='/data/sql')

    def test_existing_with_move_dir_is_refused_before_any_call(self):
        self.assert_refused_without_calls(move='existing', move_dir='/data/sql')

    def test_new_with_no_safety_backup_is_refused_before_any_call(self):
        self.assert_refused_without_calls(move='new', no_safety_backup=True)
        self.assert_refused_without_calls(move='new', no_safety_backup=True, move_dir='/data/sql')

    def test_an_unknown_mode_is_refused_before_any_call(self):
        for mode in ('', 'NEW', 'create', 'replace', None):
            with self.subTest(mode=mode):
                self.assert_refused_without_calls(move=mode)

    def test_new_needs_the_confirmation_like_every_restore(self):
        for confirm in (None, 'other', 'APPDB', ''):
            with self.subTest(confirm=confirm):
                self.assert_refused_without_calls(move='new', confirm=confirm)
                self.assert_refused_without_calls(move='existing', confirm=confirm)

    def test_new_on_an_existing_database_is_refused_before_any_change(self):
        runner = MoveRunner(self.m)  # the database exists (ONLINE)
        with self.assertRaises(self.m.Refusal):
            self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        self.assertEqual(runner.mutating, [])
        self.assertNotIn('backup', runner.kinds)
        self.assertNotIn('restore', runner.kinds)
        self.assertIs(self.state.mutated, False)
        again = MoveRunner(self.m)
        code, _ = self.code(runner=again, move='new', fs=MoveFs())
        self.assertEqual(code, 2)
        self.assertEqual(again.mutating, [])

    def test_existing_on_a_missing_database_is_refused_with_the_new_hint(self):
        runner = self.engine('new')  # the database does not exist
        with self.assertRaises(self.m.Refusal) as ctx:
            self.go(runner=runner, move='existing')
        self.assertIn('non-existent', str(ctx.exception))
        self.assertIn('--move new', str(ctx.exception))
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        code, _ = self.code(runner=self.engine('new'), move='existing')
        self.assertEqual(code, 2)

    def test_none_on_a_missing_database_keeps_the_text_of_1_14_1(self):
        runner = MoveRunner(self.m, {'state': [(0, NOT_THERE, '')]})
        with self.assertRaises(self.m.Refusal) as ctx:
            self.go(runner=runner)
        self.assertEqual(str(ctx.exception), 'restoring a non-existent database is not supported')
        with self.assertRaises(self.m.Refusal) as ctx2:
            self.go(runner=MoveRunner(self.m, {'state': [(0, NOT_THERE, '')]}), move='none')
        self.assertEqual(str(ctx2.exception), 'restoring a non-existent database is not supported')

    def test_the_allowed_combinations_run(self):
        cases = {
            'none': dict(move='none', runner=MoveRunner(self.m)),
            'none without a safety backup': dict(move='none', no_safety_backup=True, runner=MoveRunner(self.m)),
            'existing': dict(move='existing', runner=MoveRunner(self.m)),
            'existing without a safety backup': dict(move='existing', no_safety_backup=True, runner=MoveRunner(self.m)),
            'new': dict(move='new', runner=self.engine('new')),
            'new with a directory': dict(move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']), runner=self.engine('new')),
            'new with the connection and timeout flags': dict(move='new', server='(local)', trust_server_certificate=True, allow_machine_mismatch=True,
                                                              restore_timeout=99.0, timeout=5.0, runner=self.engine('new')),
            'new without a sidecar when allowed': dict(move='new', allow_missing_sidecar=True, fs=MoveFs(sidecar=NO_SIDECAR), runner=self.engine('new')),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.go(**kw)['status'], 'PASS')

    def test_timeout_limits_the_reads_of_the_file_list_and_the_header(self):
        for move, timeout, expected in (('new', 5.0, 5.0), ('new', 600.0, 120.0)):
            with self.subTest(timeout=timeout):
                runner = self.engine(move)
                self.go(move=move, timeout=timeout, runner=runner)
                for kind in ('filelist', 'header'):
                    seen = [call['timeout'] for call in runner.calls if call['kind'] == kind]
                    self.assertEqual(seen, [expected], kind)

    def test_every_refusal_of_the_matrix_also_holds_for_a_dry_run(self):
        for kw in (dict(move='none', move_dir='/data/sql'), dict(move='existing', move_dir='/data/sql'), dict(move='new', no_safety_backup=True),
                   dict(move='new', confirm='other'), dict(move='bogus')):
            with self.subTest(kw=kw):
                runner = MoveRunner(self.m)
                with self.assertRaises(self.m.Refusal):
                    self.go(runner=runner, dry_run=True, **kw)
                self.assertEqual(runner.calls, [])


# ---------------------------------------------------------------- K3: --move existing

class ExistingMoveClauses(MoveBase):
    def test_the_batch_moves_every_file_onto_the_files_of_the_target_database_in_fileid_order(self):
        result = self.go(move='existing')
        self.assertEqual(self.restore_sql(self.runner), EXISTING_BATCH)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['safety_backup_path'], SAFETY_PATH)
        self.assertEqual(self.runner.mutating, ['backup', 'restore'])
        self.assertNotIn('created', result)

    def test_the_paths_stored_in_the_copy_never_reach_the_batch(self):
        self.go(move='existing')
        for call in self.runner.calls:
            if call['kind'] in ('restore', 'backup'):
                self.assertNotIn(COPY_MARK, call['sql'])
                self.assertNotIn('C:\\old', call['sql'])

    def test_the_reads_are_plain_table_reads_with_the_documented_statements(self):
        self.go(move='existing')
        self.assertEqual(self.runner.sql_of('filelist'), [FILELIST_SQL])
        sql = self.runner.sql_of('dbfiles')
        self.assertEqual(len(sql), 2)
        self.assertEqual(sql[0], sql[1])
        self.assertIn('sys.master_files', sql[0])
        self.assertIn('database_id', sql[0])
        self.assertTrue(sql[0].endswith("N'appdb'"), sql[0])
        self.assertNotIn(' OR ', sql[0].upper())
        for call in self.runner.calls:
            if call['kind'] in ('filelist', 'dbfiles'):
                self.assertIs(call['mutating'], False)
                self.assertIs(call['table'], True)

    def test_the_order_of_the_calls_reads_before_the_safety_backup_and_a_second_read_right_before_the_batch(self):
        self.go(move='existing')
        kinds = self.runner.kinds
        header, verify, filelist = kinds.index('header'), kinds.index('verify'), kinds.index('filelist')
        backup = kinds.index('backup')
        first_read, last_read = kinds.index('dbfiles'), len(kinds) - 1 - kinds[::-1].index('dbfiles')
        self.assertLess(header, filelist)
        self.assertLess(verify, filelist)
        self.assertLess(filelist, backup)
        self.assertLess(first_read, backup)
        self.assertGreater(last_read, backup)
        self.assertEqual(kinds.index('restore'), last_read + 1)
        self.assertEqual(kinds.count('dbfiles'), 2)

    def test_every_order_of_the_rows_gives_the_same_batch(self):
        for backup_rows in itertools.permutations(BACKUP_ROWS):
            for db_rows in itertools.permutations(DB_ROWS):
                runner = MoveRunner(self.m, {'filelist': [(0, filelist_out(list(backup_rows)), '')], 'dbfiles': [(0, dbfiles_out(list(db_rows)), '')]})
                self.go(runner=runner, move='existing')
                self.assertEqual(self.restore_sql(runner), EXISTING_BATCH, (backup_rows, db_rows))

    def test_file_ids_are_ordered_as_numbers_not_as_text(self):
        rows = [fl('f_a', 'D', 2), fl('f_b', 'L', 10), fl('f_c', 'D', 9)]
        db_rows = [['f_a', 'ROWS', '/v/a.mdf'], ['f_b', 'LOG', '/v/b.ldf'], ['f_c', 'ROWS', '/v/c.ndf']]
        for ordering in ([0, 1, 2], [1, 2, 0], [2, 1, 0]):
            runner = MoveRunner(self.m, {'filelist': [(0, filelist_out([rows[i] for i in ordering]), '')],
                                         'dbfiles': [(0, dbfiles_out(db_rows), '')]})
            self.go(runner=runner, move='existing')
            self.assertIn("MOVE N'f_a' TO N'/v/a.mdf', MOVE N'f_c' TO N'/v/c.ndf', MOVE N'f_b' TO N'/v/b.ldf', FILE = 1", self.restore_sql(runner))

    def test_the_names_match_without_regard_to_case(self):
        db_rows = [['APPDB_DATA', 'ROWS', '/var/sql/data/appdb.mdf'], ['AppDb_Log', 'LOG', '/var/sql/log/appdb_log.ldf'],
                   ['appdb_DATA2', 'ROWS', '/var/sql/data/appdb_2.ndf']]
        self.go(runner=MoveRunner(self.m, {'dbfiles': [(0, dbfiles_out(db_rows), '')]}), move='existing')
        sql = self.restore_sql(self.runner).lower()
        for path in ('/var/sql/data/appdb.mdf', '/var/sql/log/appdb_log.ldf', '/var/sql/data/appdb_2.ndf'):
            self.assertIn("to n'" + path + "'", sql)

    def test_a_name_with_inner_spaces_is_accepted(self):
        rows = [fl('my data', 'D', 1), fl('my log', 'L', 2)]
        db_rows = [['my data', 'ROWS', '/v/a.mdf'], ['my log', 'LOG', '/v/a.ldf']]
        self.go(runner=MoveRunner(self.m, {'filelist': [(0, filelist_out(rows), '')], 'dbfiles': [(0, dbfiles_out(db_rows), '')]}), move='existing')
        self.assertIn("MOVE N'my data' TO N'/v/a.mdf', MOVE N'my log' TO N'/v/a.ldf', FILE = 1", self.restore_sql(self.runner))

    def test_an_uppercase_extension_of_an_engine_path_is_accepted(self):
        db_rows = [['appdb_data', 'ROWS', '/var/sql/A.MDF'], ['appdb_log', 'LOG', '/var/sql/A.LDF'], ['appdb_data2', 'ROWS', '/var/sql/A.NDF']]
        self.go(runner=MoveRunner(self.m, {'dbfiles': [(0, dbfiles_out(db_rows), '')]}), move='existing')
        self.assertIn("N'/var/sql/A.MDF'", self.restore_sql(self.runner))

    def test_windows_paths_of_the_engine_with_a_drive_root_are_moved_as_they_are(self):
        db_rows = [['appdb_data', 'ROWS', 'D:\\Data\\appdb.mdf'], ['appdb_log', 'LOG', 'E:\\Logs\\appdb_log.ldf'], ['appdb_data2', 'ROWS', 'D:\\appdb_2.ndf']]
        runner = MoveRunner(self.m, {'dbfiles': [(0, dbfiles_out(db_rows), '')]})
        self.go(runner=runner, move='existing', **self.windows())
        self.assertIn("MOVE N'appdb_data' TO N'D:\\Data\\appdb.mdf', MOVE N'appdb_log' TO N'E:\\Logs\\appdb_log.ldf', MOVE N'appdb_data2' TO N'D:\\appdb_2.ndf', "
                      "FILE = 1", self.restore_sql(runner))
        self.assertIn(WIN_SAFETY, [c['sql'] for c in runner.calls if c['kind'] == 'backup'][0])

    def test_without_a_safety_backup_the_batch_is_the_same_and_there_is_no_backup(self):
        self.go(move='existing', no_safety_backup=True)
        self.assertEqual(self.restore_sql(self.runner), EXISTING_BATCH)
        self.assertNotIn('backup', self.runner.kinds)
        self.assertEqual(self.runner.mutating, ['restore'])
        self.assertEqual(self.runner.kinds.count('dbfiles'), 2)

    def test_the_final_state_is_still_checked_and_failures_keep_the_old_contract(self):
        runner = MoveRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]})
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.go(runner=runner, move='existing')
        self.assertIn('RESTORING', str(ctx.exception))
        self.assertIn(SAFETY_PATH, str(ctx.exception))
        self.assertEqual(runner.mutating, ['backup', 'restore'])
        code, text = self.code(runner=MoveRunner(self.m, {'restore': [(1, BATCH_ERROR, '')]}), move='existing')
        self.assertEqual(code, 1)
        self.assertIn('stan bazy mógł się zmienić', text)


def bad_filelists():
    """name -> rows (lists) of a backup that must be refused whatever the mode."""
    good_log = fl('lg', 'L', 9)
    cases = {
        'a file of type S': [fl('dd', 'D', 1), fl('fs', 'S', 2), good_log],
        'a file of type F': [fl('dd', 'D', 1), fl('fs', 'F', 2), good_log],
        'a lowercase type': [fl('dd', 'd', 1), good_log],
        'an unknown type': [fl('dd', 'D', 1), fl('xx', 'X', 2)],
        'more than 64 files': [fl('d' + str(i), 'D', i) for i in range(1, 66)],
        'a quote in a logical name': [fl("a'b", 'D', 1), good_log],
        'a semicolon in a logical name': [fl('a;b', 'D', 1), good_log],
        'a bracket in a logical name': [fl('a]b', 'D', 1), good_log],
        'a slash in a logical name': [fl('a/b', 'D', 1), good_log],
        'a backslash in a logical name': [fl('a\\b', 'D', 1), good_log],
        'a non-ASCII logical name': [fl('za\u017c\u00f3\u0142w', 'D', 1), good_log],
        'an empty logical name': [fl('', 'D', 1), good_log],
        'a trailing space in a logical name': [fl('dd ', 'D', 1), good_log],
        'a logical name of 129 characters': [fl('a' * 129, 'D', 1), good_log],
        'names repeating without regard to case': [fl('Data', 'D', 1), fl('data', 'D', 2), good_log],
        'names repeating without regard to case, lower first': [fl('data', 'D', 1), fl('DATA', 'D', 2), good_log],
        'a repeated name': [fl('dd', 'D', 1), fl('dd', 'D', 2), good_log],
        'a FileId that is not a number': [fl('dd', 'D', 'x1'), good_log],
        'a FileId with SQL in it': [fl('dd', 'D', '1;DROP'), good_log],
        'a FileId of 6 digits': [fl('dd', 'D', '123456'), good_log],
        'a negative FileId': [fl('dd', 'D', '-1'), good_log],
        'a signed FileId': [fl('dd', 'D', '+1'), good_log],
        'a decimal FileId': [fl('dd', 'D', '1.0'), good_log],
        'an empty FileId': [fl('dd', 'D', ''), good_log],
        'a non-ASCII digit as FileId': [fl('dd', 'D', '\u0661'), good_log],
        'a repeated FileId': [fl('dd', 'D', 1), fl('d2', 'D', 1), good_log],
        'FileId 007 and 7': [fl('dd', 'D', '007'), fl('d2', 'D', '7'), good_log],
        'FileId 1 and 01': [fl('dd', 'D', '1'), fl('d2', 'D', '01'), good_log],
        'no rows': [],
        'no data file': [fl('lg', 'L', 1)],
        'logs only': [fl('l1', 'L', 1), fl('l2', 'L', 2)],
    }
    return cases


class FilelistValidation(MoveBase):
    """A4 / K3 / K4: the data of the copy is untrusted; every refusal comes before the safety backup and any change (exit code 2)."""

    def test_every_bad_file_list_is_refused_before_the_backup_in_both_modes(self):
        for name, rows in bad_filelists().items():
            for move in ('existing', 'new'):
                with self.subTest(case=name, move=move):
                    script = {'filelist': [(0, filelist_out(rows), '')]}
                    if move == 'existing':
                        script['dbfiles'] = [(0, dbfiles_out(derived_db_rows(rows) or DB_ROWS), '')]
                    kw = {'move_dir': '/data/sql', 'fs': MoveFs(dirs=['/data/sql'])} if move == 'new' else {}
                    self.assert_refused_before_the_backup(move, self.engine(move, **script), **kw)

    def test_a_missing_required_column_is_refused_in_both_modes(self):
        for dropped in ('LogicalName', 'Type', 'FileId'):
            keep = [i for i, name in enumerate(FL_COLUMNS) if name != dropped]
            columns = [FL_COLUMNS[i] for i in keep]
            rows = [[row[i] for i in keep] for row in BACKUP_ROWS]
            for move in ('existing', 'new'):
                with self.subTest(dropped=dropped, move=move):
                    script = {'filelist': [(0, filelist_out(rows, columns), '')]}
                    kw = {'move_dir': '/data/sql', 'fs': MoveFs(dirs=['/data/sql'])} if move == 'new' else {}
                    self.assert_refused_before_the_backup(move, self.engine(move, **script), **kw)

    def test_a_row_that_does_not_fit_the_table_is_refused(self):
        broken = table(FL_COLUMNS, BACKUP_ROWS).replace('|8388608|35184372080640|1\n', '|8388608|35184372080640|1|extra\n', 1)
        for move in ('existing', 'new'):
            with self.subTest(move=move):
                kw = {'move_dir': '/data/sql', 'fs': MoveFs(dirs=['/data/sql'])} if move == 'new' else {}
                self.assert_refused_before_the_backup(move, self.engine(move, filelist=(0, broken, '')), **kw)

    def test_a_failing_filelistonly_read_stops_before_the_backup(self):
        for item in ((1, '', ERROR_OUTPUT), subprocess.TimeoutExpired('sqlcmd', 120.0), OSError('gone')):
            for move in ('existing', 'new'):
                with self.subTest(item=repr(item), move=move):
                    runner = self.engine(move, filelist=item)
                    kw = {'move_dir': '/data/sql', 'fs': MoveFs(dirs=['/data/sql'])} if move == 'new' else {}
                    with self.assertRaises((self.m.Refusal, self.m.SqlFailure, OSError, subprocess.TimeoutExpired)):
                        self.go(runner=runner, move=move, **kw)
                    self.assertEqual(runner.mutating, [])
                    self.assertNotIn('restore', runner.kinds)
                    self.assertIs(self.state.mutated, False)

    def test_exactly_64_files_are_accepted(self):
        rows = [fl('d' + str(i), 'D', i) for i in range(1, 64)] + [fl('lg', 'L', 64)]
        runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
        self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        sql = self.restore_sql(runner)
        self.assertEqual(sql.count('MOVE N'), 64)
        self.assertIn("N'/data/sql/appdb_64_a1b2c3.ldf'", sql)

    def test_the_boundaries_of_the_file_id_are_accepted(self):
        rows = [fl('dd', 'D', '00007'), fl('lg', 'L', '99999')]
        runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
        self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        self.assertIn("MOVE N'dd' TO N'/data/sql/appdb_1_a1b2c3.mdf', MOVE N'lg' TO N'/data/sql/appdb_2_a1b2c3.ldf'", self.restore_sql(runner))

    def test_validate_filelist_refuses_a_leading_space_that_the_table_parser_would_have_cut(self):
        rows = [{'LogicalName': ' lead', 'Type': 'D', 'FileId': '1'}]
        with self.assertRaises(self.m.Refusal):
            self.m.validate_filelist(rows)
        with self.assertRaises(self.m.Refusal):
            self.m.validate_filelist([{'LogicalName': 'ok', 'Type': 'D', 'FileId': '1'}, {'LogicalName': ' ', 'Type': 'L', 'FileId': '2'}])
        self.m.validate_filelist([{'LogicalName': 'in ner', 'Type': 'D', 'FileId': '1'}])

    def test_validate_filelist_refusals_are_refusals_not_crashes(self):
        for name, rows in bad_filelists().items():
            if not rows:
                continue
            with self.subTest(case=name):
                parsed = self.m.parse_sqlcmd_table(filelist_out(rows))
                with self.assertRaises(self.m.Refusal):
                    self.m.validate_filelist(parsed)
        with self.assertRaises(self.m.Refusal):
            self.m.validate_filelist([])


class ExistingLayoutRefusals(MoveBase):
    def refused(self, filelist=None, dbfiles=None, **kw):
        script = {}
        if filelist is not None:
            script['filelist'] = [(0, filelist, '')]
        if dbfiles is not None:
            script['dbfiles'] = [(0, dbfiles, '')]
        return self.assert_refused_before_the_backup('existing', self.engine('existing', **script), **kw)

    def test_a_different_logical_name_is_refused(self):
        rows = [list(row) for row in DB_ROWS]
        rows[0][0] = 'appdb_other'
        self.refused(dbfiles=dbfiles_out(rows))

    def test_a_different_type_is_refused(self):
        rows = [list(row) for row in DB_ROWS]
        rows[1][1] = 'ROWS'  # the log of the copy is a data file of the database
        self.refused(dbfiles=dbfiles_out(rows))
        rows = [list(row) for row in DB_ROWS]
        rows[0][1] = 'LOG'
        self.refused(dbfiles=dbfiles_out(rows))

    def test_a_different_number_of_files_is_refused_in_both_directions(self):
        self.refused(dbfiles=dbfiles_out(DB_ROWS[:2]))
        self.refused(dbfiles=dbfiles_out(DB_ROWS + [['appdb_extra', 'ROWS', '/var/sql/data/extra.ndf']]))
        self.refused(filelist=filelist_out(BACKUP_ROWS + [fl('appdb_more', 'D', 4)]))

    def test_the_database_without_files_or_columns_is_refused(self):
        self.refused(dbfiles=dbfiles_out([]))
        for dropped in ('name', 'type_desc', 'physical_name'):
            keep = [i for i, name in enumerate(DB_COLUMNS) if name != dropped]
            self.refused(dbfiles=dbfiles_out([[row[i] for i in keep] for row in DB_ROWS], [DB_COLUMNS[i] for i in keep]))

    def test_a_file_of_the_database_that_is_not_data_or_log_is_refused(self):
        for kind in ('FILESTREAM', 'FULLTEXT', 'MEMORY_OPTIMIZED_DATA', ''):
            with self.subTest(kind=kind):
                rows = [list(row) for row in DB_ROWS]
                rows[2][1] = kind
                self.refused(dbfiles=dbfiles_out(rows))

    def test_database_files_repeating_a_name_without_regard_to_case_are_refused(self):
        rows = [list(row) for row in DB_ROWS]
        rows[2][0] = 'APPDB_DATA'
        self.refused(dbfiles=dbfiles_out(rows))

    def test_an_engine_path_outside_the_whitelist_is_refused_with_the_hint(self):
        paths = ['/var/sql (x)/a.mdf', '/var/sql/za\u017c\u00f3\u0142\u0107.mdf', '/var/sql/a~1.mdf', "/var/sql/o'x.mdf", '/var/sql/a,b.mdf',
                 '/var/sql/a&b.mdf', '/var/sql/a+b.mdf']
        for path in paths:
            with self.subTest(path=path):
                rows = [list(row) for row in DB_ROWS]
                rows[0][2] = path
                text = self.refused(dbfiles=dbfiles_out(rows))
                self.assertTrue(any(hint in text for hint in UNSAFE_PATH_HINT), text)
                self.assertFalse(any(path in c['sql'] for c in self.runner.calls))

    def test_an_engine_path_that_is_not_a_data_file_path_is_refused(self):
        for path in ('/var/sql/a.bin', '/var/sql/a', 'data/a.mdf', '/var/../a.mdf', '/var/./a.mdf', '/var//a.mdf', 'C:\\sql\\a.mdf', '/var/sql/a.mdf.'):
            with self.subTest(path=path):
                rows = [list(row) for row in DB_ROWS]
                rows[0][2] = path
                self.refused(dbfiles=dbfiles_out(rows))

    def test_a_posix_path_with_a_colon_or_backslash_is_refused(self):
        for path in ('/var/sql/a:b.mdf', '/var/sql\\a.mdf'):
            with self.subTest(path=path):
                rows = [list(row) for row in DB_ROWS]
                rows[0][2] = path
                self.refused(dbfiles=dbfiles_out(rows))

    def test_a_windows_path_with_a_stream_a_device_or_a_trailing_dot_is_refused_for_a_windows_client(self):
        for path in ('D:\\Data\\a.mdf:stream', 'D:\\Data\\NUL.mdf', 'D:\\Data.\\a.mdf', 'D:\\Data \\a.mdf', '\\\\server\\share\\a.mdf', 'D:a.mdf'):
            with self.subTest(path=path):
                rows = [['appdb_data', 'ROWS', path], ['appdb_log', 'LOG', 'D:\\Data\\a.ldf'], ['appdb_data2', 'ROWS', 'D:\\Data\\b.ndf']]
                runner = self.engine('existing', dbfiles=(0, dbfiles_out(rows), ''))
                self.assert_refused_before_the_backup('existing', runner, **self.windows())

    def test_two_files_of_the_database_with_one_path_are_refused(self):
        rows = [list(row) for row in DB_ROWS]
        rows[2][2] = rows[0][2]
        self.refused(dbfiles=dbfiles_out(rows))


class ExistingRereadAfterTheSafetyBackup(MoveBase):
    """A2: sys.master_files is read again right before the destructive batch. After the safety backup a difference is exit code 1 with the
    path of the copy; without a safety backup (nothing written yet) it is exit code 2."""

    def second_read(self, rows=None, item=None):
        item = item if item is not None else (0, dbfiles_out(rows), '')
        return MoveRunner(self.m, {'dbfiles': [(0, dbfiles_out(), ''), item]})

    def changed(self):
        moved = [list(row) for row in DB_ROWS]
        moved[0][2] = '/var/other/appdb.mdf'
        return {
            'a path changed': dict(rows=moved),
            'a file added': dict(rows=DB_ROWS + [['appdb_x', 'ROWS', '/var/sql/data/x.ndf']]),
            'a file removed': dict(rows=DB_ROWS[:2]),
            'a name changed': dict(rows=[['appdb_data', 'ROWS', DB_ROWS[0][2]], ['appdb_log', 'LOG', DB_ROWS[1][2]], ['appdb_renamed', 'ROWS', DB_ROWS[2][2]]]),
            'a type changed': dict(rows=[DB_ROWS[0], ['appdb_log', 'ROWS', DB_ROWS[1][2]], DB_ROWS[2]]),
            'a path became unusable': dict(rows=[['appdb_data', 'ROWS', '/var/sql (x)/appdb.mdf'], DB_ROWS[1], DB_ROWS[2]]),
            'the read failed': dict(item=(1, '', ERROR_OUTPUT)),
            'no rows': dict(rows=[]),
        }

    def test_a_change_after_the_safety_backup_is_a_failure_with_the_path_of_the_copy_and_code_1(self):
        for name, kw in self.changed().items():
            with self.subTest(case=name):
                runner = self.second_read(**kw)
                with self.assertRaises(self.m.SqlFailure) as ctx:
                    self.go(runner=runner, move='existing')
                self.assertIn(SAFETY_PATH, str(ctx.exception))
                self.assertEqual(runner.mutating, ['backup'])
                self.assertNotIn('restore', runner.kinds)
                self.assertIs(self.state.mutated, True)
                code, text = self.code(runner=self.second_read(**kw), move='existing')
                self.assertEqual(code, 1)
                self.assertIn(SAFETY_PATH, text)

    def test_without_a_safety_backup_a_change_is_a_refusal_with_code_2(self):
        for name, kw in self.changed().items():
            with self.subTest(case=name):
                runner = self.second_read(**kw)
                with self.assertRaises(self.m.Refusal if 'failed' not in name else Exception) as ctx:
                    self.go(runner=runner, move='existing', no_safety_backup=True)
                self.assertNotIn(SAFETY_PATH, str(ctx.exception))
                self.assertEqual(runner.mutating, [])
                self.assertNotIn('restore', runner.kinds)
                self.assertIs(self.state.mutated, False)
                code, _ = self.code(runner=self.second_read(**kw), move='existing', no_safety_backup=True)
                self.assertEqual(code, 2)

    def test_an_exception_of_the_second_read_stops_before_the_batch(self):
        for item in (subprocess.TimeoutExpired('sqlcmd', 30.0), OSError('gone')):
            with self.subTest(item=repr(item)):
                runner = self.second_read(item=item)
                with self.assertRaises(self.m.SqlFailure):
                    self.go(runner=runner, move='existing')
                self.assertEqual(runner.mutating, ['backup'])
                self.assertNotIn('restore', runner.kinds)
                code, text = self.code(runner=self.second_read(item=item), move='existing')
                self.assertEqual(code, 1)
                self.assertIn('/h/bk/appdb-pre-restore-', text)   # the way back (path of the safety backup) is named
                runner = self.second_read(item=item)
                code, _ = self.code(runner=runner, move='existing', no_safety_backup=True)
                self.assertEqual(code, 2)
                self.assertEqual(runner.mutating, [])
                self.assertNotIn('restore', runner.kinds)

    def test_the_same_layout_in_another_row_order_is_not_a_change(self):
        runner = self.second_read(rows=list(reversed(DB_ROWS)))
        self.go(runner=runner, move='existing')
        self.assertEqual(self.restore_sql(runner), EXISTING_BATCH)


# ---------------------------------------------------------------- K4: --move new

class NewBatch(MoveBase):
    def new_run(self, directory='/data/sql', runner=None, fs=None, **kw):
        runner = runner or self.engine('new')
        fs = fs or MoveFs(dirs=[directory] if directory else [])
        if directory:
            kw['move_dir'] = directory
        return self.go(runner=runner, fs=fs, move='new', **kw)

    def test_the_batch_has_no_replace_no_alter_and_no_safety_backup(self):
        result = self.new_run()
        sql = self.restore_sql(self.runner)
        self.assertEqual(sql, "RESTORE DATABASE [appdb] FROM DISK = N'" + SRC_PATH + "' WITH " + new_moves('/data/sql') + ', FILE = 1, CHECKSUM')
        for word in ('REPLACE', 'ALTER', 'SINGLE_USER', 'MULTI_USER', 'TRY', 'CATCH'):
            self.assertNotIn(word, sql)
        self.assertEqual(self.runner.mutating, ['restore'])
        self.assertNotIn('backup', self.runner.kinds)
        self.assertIs(self.runner.calls[-1]['mutating'], False)
        self.assertEqual(self.runner.calls[-1]['kind'], 'state')
        self.assertEqual(result['status'], 'PASS')
        self.assertIsNone(result['safety_backup_path'])

    def test_the_paths_stored_in_the_copy_and_text_of_the_copy_never_reach_the_batch(self):
        self.new_run()
        sql = self.restore_sql(self.runner)
        self.assertNotIn(COPY_MARK, sql)
        self.assertNotIn('C:\\old', sql)

    def test_the_names_are_numbered_over_all_files_by_file_id(self):
        # FileId 5 (data), 2 (data), 1 (log): the log has the lowest FileId, so it is number 1; mdf is the data file of the lowest FileId.
        rows = [fl('x_five', 'D', 5), fl('x_two', 'D', 2), fl('x_one', 'L', 1)]
        runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
        self.new_run(runner=runner)
        self.assertIn("WITH MOVE N'x_one' TO N'/data/sql/appdb_1_a1b2c3.ldf', MOVE N'x_two' TO N'/data/sql/appdb_2_a1b2c3.mdf', "
                      "MOVE N'x_five' TO N'/data/sql/appdb_3_a1b2c3.ndf', FILE = 1", self.restore_sql(runner))

    def test_file_ids_are_ordered_as_numbers(self):
        rows = [fl('f_b', 'D', 10), fl('f_c', 'D', 9), fl('f_a', 'D', 2), fl('f_l', 'L', 100)]
        runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
        self.new_run(runner=runner)
        self.assertIn("WITH MOVE N'f_a' TO N'/data/sql/appdb_1_a1b2c3.mdf', MOVE N'f_c' TO N'/data/sql/appdb_2_a1b2c3.ndf', "
                      "MOVE N'f_b' TO N'/data/sql/appdb_3_a1b2c3.ndf', MOVE N'f_l' TO N'/data/sql/appdb_4_a1b2c3.ldf', FILE = 1", self.restore_sql(runner))

    def test_every_order_of_the_rows_gives_the_same_names(self):
        for rows in itertools.permutations(BACKUP_ROWS):
            runner = self.engine('new', filelist=(0, filelist_out(list(rows)), ''))
            self.new_run(runner=runner)
            self.assertIn('WITH ' + new_moves('/data/sql') + ', FILE = 1', self.restore_sql(runner), rows)

    def test_one_token_per_run_for_all_files(self):
        tokens = iter(['aaaaaa', 'bbbbbb', 'cccccc', 'dddddd', 'eeeeee', 'ffffff', '111111', '222222'])
        self.new_run(token_fn=lambda: next(tokens))
        self.assertIn('WITH ' + new_moves('/data/sql', token='aaaaaa') + ', FILE = 1', self.restore_sql(self.runner))
        for other in ('bbbbbb', 'cccccc'):
            self.assertNotIn(other, self.restore_sql(self.runner))

    def test_the_token_changes_between_runs(self):
        self.new_run(token_fn=lambda: '0a0a0a')
        first = self.restore_sql(self.runner)
        self.new_run(token_fn=lambda: '0b0b0b')
        self.assertNotEqual(first, self.restore_sql(self.runner))
        self.assertIn('appdb_1_0b0b0b.mdf', self.restore_sql(self.runner))

    def test_hostile_but_valid_logical_names_appear_only_as_the_logical_name(self):
        rows = [fl('..', 'D', 1), fl('CON', 'D', 2), fl('EVIL.mdf', 'L', 3)]
        runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
        self.new_run(runner=runner)
        self.assertIn("WITH MOVE N'..' TO N'/data/sql/appdb_1_a1b2c3.mdf', MOVE N'CON' TO N'/data/sql/appdb_2_a1b2c3.ndf', "
                      "MOVE N'EVIL.mdf' TO N'/data/sql/appdb_3_a1b2c3.ldf', FILE = 1", self.restore_sql(runner))

    def test_hostile_file_ids_are_refused_before_any_change_and_never_reach_a_path(self):
        for file_id in ('x1', '1; DROP DATABASE x', '123456', '-1', '', '1.0', '\u0661'):
            with self.subTest(file_id=file_id):
                rows = [fl('dd', 'D', file_id), fl('lg', 'L', 2)]
                runner = self.engine('new', filelist=(0, filelist_out(rows), ''))
                self.assert_refused_before_the_backup('new', runner, move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
                for call in runner.calls:
                    self.assertNotIn('DROP DATABASE x', call['sql'])

    def test_a_trailing_separator_of_move_dir_is_normalised(self):
        self.new_run(directory='/data/sql/', fs=MoveFs(dirs=['/data/sql/', '/data/sql']))
        sql = self.restore_sql(self.runner)
        self.assertIn('WITH ' + new_moves('/data/sql') + ', FILE = 1', sql)
        self.assertNotIn('//', sql.replace(SRC_PATH, ''))

    def test_a_root_directory_does_not_get_a_double_separator_on_posix(self):
        self.new_run(directory='/', fs=MoveFs(dirs=['/']))
        self.assertIn('WITH ' + new_moves('', sep='/') + ', FILE = 1', self.restore_sql(self.runner))

    def test_a_drive_root_does_not_get_a_double_separator_on_windows(self):
        for directory in ('D:\\', 'D:/'):
            with self.subTest(directory=directory):
                runner = self.engine('new')
                self.go(runner=runner, move='new', move_dir=directory, **self.windows())
                self.assertIn('WITH ' + new_moves('D:', sep='\\') + ', FILE = 1', self.restore_sql(runner))

    def test_windows_move_dir_with_and_without_a_trailing_separator(self):
        for directory in ('D:\\sql\\data', 'D:\\sql\\data\\'):
            with self.subTest(directory=directory):
                runner = self.engine('new')
                self.go(runner=runner, move='new', move_dir=directory, **self.windows())
                self.assertIn('WITH ' + new_moves('D:\\sql\\data', sep='\\') + ', FILE = 1', self.restore_sql(runner))

    def test_the_engine_defaults_are_used_without_move_dir_and_the_trailing_separator_is_cut(self):
        runner = self.engine('new', defaults=(0, defaults_out('/var/opt/data/', '/var/opt/log/'), ''))
        self.new_run(directory=None, runner=runner)
        self.assertIn('WITH ' + new_moves('/var/opt/data', log_directory='/var/opt/log') + ', FILE = 1', self.restore_sql(runner))
        sql = runner.sql_of('defaults')
        self.assertEqual(len(sql), 1)
        self.assertIn('InstanceDefaultDataPath', sql[0])
        self.assertIn('InstanceDefaultLogPath', sql[0])
        self.assertLess(runner.kinds.index('defaults'), runner.kinds.index('restore'))
        self.assertLess(runner.kinds.index('filelist'), runner.kinds.index('restore'))
        for call in runner.calls:
            if call['kind'] == 'defaults':
                self.assertIs(call['mutating'], False)
                self.assertIs(call['table'], True)

    def test_a_windows_profile_default_with_a_trailing_separator(self):
        runner = self.engine('new', defaults=(0, defaults_out('C:\\profile\\', 'C:\\profile\\'), ''))
        self.go(runner=runner, move='new', **self.windows())
        self.assertIn('WITH ' + new_moves('C:\\profile', sep='\\') + ', FILE = 1', self.restore_sql(runner))

    def test_windows_defaults_on_two_drives_and_a_drive_root(self):
        runner = self.engine('new', defaults=(0, defaults_out('D:\\', 'E:\\Logs\\'), ''))
        self.go(runner=runner, move='new', **self.windows())
        self.assertIn('WITH ' + new_moves('D:', log_directory='E:\\Logs', sep='\\') + ', FILE = 1', self.restore_sql(runner))

    def test_a_default_without_a_trailing_separator_is_used_as_it_is(self):
        runner = self.engine('new', defaults=(0, defaults_out('/var/opt/data', '/var/opt/log'), ''))
        self.new_run(directory=None, runner=runner)
        self.assertIn('WITH ' + new_moves('/var/opt/data', log_directory='/var/opt/log') + ', FILE = 1', self.restore_sql(runner))

    def test_a_default_with_two_trailing_separators_is_refused(self):
        runner = self.engine('new', defaults=(0, defaults_out('/var/opt/data//', '/var/opt/log/'), ''))
        self.assert_refused_before_the_backup('new', runner)

    def test_a_null_default_path_is_refused_with_the_hint(self):
        for pair in (('NULL', '/var/opt/log/'), ('/var/opt/data/', 'NULL'), ('NULL', 'NULL')):
            with self.subTest(pair=pair):
                runner = self.engine('new', defaults=(0, defaults_out(*pair), ''))
                text = self.assert_refused_before_the_backup('new', runner)
                self.assertIn('--move-dir', text)
                self.assertNotIn('restore', runner.kinds)

    def test_a_default_path_outside_the_whitelist_is_refused_with_the_hint(self):
        for data in ('/var/opt/(x86)/data/', '/srv/za\u017c\u00f3\u0142\u0107/', '/srv/u~1/'):
            with self.subTest(data=data):
                runner = self.engine('new', defaults=(0, defaults_out(data, '/var/opt/log/'), ''))
                text = self.assert_refused_before_the_backup('new', runner)
                self.assertTrue(any(hint in text for hint in UNSAFE_PATH_HINT), text)
        runner = self.engine('new', defaults=(0, defaults_out('C:\\profile\\Zażółć\\', 'C:\\profile\\Zażółć\\'), ''))
        text = self.assert_refused_before_the_backup('new', runner, **self.windows())
        self.assertTrue(any(hint in text for hint in UNSAFE_PATH_HINT), text)

    def test_unreadable_defaults_stop_before_any_change(self):
        for item in ((1, '', ERROR_OUTPUT), (0, table(DEFAULT_COLUMNS, []), ''), (0, table(['data_path'], [['/x/']]), '')):
            with self.subTest(item=item):
                runner = self.engine('new', defaults=item)
                with self.assertRaises((self.m.Refusal, self.m.SqlFailure, OSError, subprocess.TimeoutExpired)):
                    self.go(runner=runner, move='new')
                self.assertEqual(runner.mutating, [])
                self.assertIs(self.state.mutated, False)

    def test_move_dir_is_checked_before_any_call(self):
        fs = MoveFs(dirs=['/data/sql'], git_dirs=['/repo/.git'])
        bad = {'inside a repository': '/repo/data', 'outside the whitelist': '/data/(x)', 'non-ASCII': '/data/za\u017c\u00f3\u0142\u0107', 'relative': 'data/sql',
               'parent segment': '/data/../sql', 'a double separator': '/data//sql', 'a colon': '/data/a:b'}
        for name, directory in bad.items():
            with self.subTest(case=name):
                runner = self.engine('new')
                with self.assertRaises(self.m.Refusal):
                    self.go(runner=runner, move='new', move_dir=directory, fs=fs)
                self.assertEqual(runner.mutating, [])
                self.assertIs(self.state.mutated, False)
                self.assertEqual(self.fs.written, {})
                code, _ = self.code(runner=self.engine('new'), move='new', move_dir=directory, fs=MoveFs(git_dirs=['/repo/.git']))
                self.assertEqual(code, 2)

    def test_a_directory_not_seen_from_here_is_only_a_warning(self):
        result = self.new_run(directory='/data/sql', fs=MoveFs(dirs=[]))
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(any('/data/sql' in warning for warning in result['warnings']), result['warnings'])
        self.assertIn('/data/sql/appdb_1_a1b2c3.mdf', self.restore_sql(self.runner))
        quiet = self.new_run(directory='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        self.assertFalse(any('/data/sql' in warning and 'not found' in warning for warning in quiet['warnings']), quiet['warnings'])

    def test_the_tool_never_creates_the_directory(self):
        self.new_run(directory='/data/sql', fs=MoveFs(dirs=[]))
        self.assertEqual(self.fs.made, [])

    def test_the_result_says_a_database_was_created_with_an_undo_note_and_the_origin_warning(self):
        result = self.new_run()
        self.assertIs(result['created'], True)
        self.assertIn('DROP DATABASE [appdb]', result['undo_note'])
        self.assertIn('/data/sql', result['undo_note'])
        joined = ' '.join(result['warnings'])
        self.assertIn('TRUSTWORTHY', joined)
        self.assertIn('CLR', joined)
        self.assertNotIn('no way back', joined)
        self.assertNotIn('no safety backup', joined.lower())
        self.assertEqual(json.loads(json.dumps(result))['created'], True)

    def test_the_notice_and_the_way_back_do_not_talk_about_a_missing_safety_backup(self):
        result = self.new_run()
        notice = result['coordinator_notice']
        self.assertNotIn('no way back', notice.lower())
        self.assertNotIn('brak drogi powrotu', notice.lower())
        self.assertNotIn('Kopia bezpieczeństwa: brak', notice)
        self.assertIn('DROP DATABASE [appdb]', notice)
        text = self.m._way_back(None, 'appdb', None, None, True)
        self.assertIn('DROP DATABASE [appdb]', text)
        self.assertNotIn('no way back', text)
        self.assertNotIn('No safety backup was made', text)

    def test_the_default_mode_notice_still_warns_about_a_missing_safety_backup(self):
        result = self.go(no_safety_backup=True)
        self.assertIn('brak drogi powrotu', result['coordinator_notice'])

    def test_the_reads_before_the_create_are_the_documented_ones_and_happen_in_order(self):
        self.new_run()
        kinds = self.runner.kinds
        self.assertEqual(kinds[0], 'state')
        self.assertIn(FILELIST_SQL, self.runner.sql_of('filelist'))
        self.assertLess(kinds.index('header'), kinds.index('filelist'))
        self.assertLess(kinds.index('verify'), kinds.index('filelist'))
        self.assertLess(kinds.index('filelist'), kinds.index('restore'))
        self.assertEqual(kinds.count('restore'), 1)
        self.assertNotIn('dbfiles', kinds)

    def test_the_header_the_sidecar_and_the_digest_are_still_checked(self):
        runner = self.engine('new', header=(0, restore_header_out(DatabaseName='other'), ''))
        with self.assertRaises(self.m.Refusal):
            self.new_run(runner=runner)
        self.assertEqual(runner.mutating, [])
        with self.assertRaises(self.m.Refusal):
            self.new_run(runner=self.engine('new'), fs=MoveFs(sidecar=NO_SIDECAR, dirs=['/data/sql']))
        runner = self.engine('new', verify=(1, '', ERROR_OUTPUT))
        with self.assertRaises(self.m.SqlFailure):
            self.new_run(runner=runner)
        self.assertEqual(runner.mutating, [])
        self.assertNotIn('filelist', runner.kinds)

    def test_a_database_in_a_restricted_access_mode_after_the_create_is_not_a_failure(self):
        runner = self.engine('new', state=[(0, NOT_THERE, ''), (0, state_out('ONLINE', 'SINGLE_USER'), '')])
        result = self.new_run(runner=runner)
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(any('the new database is in access mode SINGLE_USER (taken over from the copy)' in warning for warning in result['warnings']),
                        result['warnings'])


class NewFailures(MoveBase):
    def failing(self, restore_item, state_item, **kw):
        runner = self.engine('new', restore=restore_item, state=[(0, NOT_THERE, ''), state_item])
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']), **kw)
        return runner, str(ctx.exception)

    def test_a_failed_create_reads_the_state_and_advises_a_drop(self):
        for name, state in {'restoring': state_out('RESTORING'), 'online': state_out(), 'single user': state_out('ONLINE', 'SINGLE_USER'),
                            'offline': state_out('OFFLINE')}.items():
            with self.subTest(state=name):
                runner, message = self.failing((1, BATCH_ERROR, ''), (0, state, ''))
                self.assertEqual(runner.kinds[-2:], ['restore', 'state'])
                self.assertEqual(runner.mutating, ['restore'])
                self.assertIn('DROP DATABASE [appdb]', message)
                self.assertIn('/data/sql', message)
                self.assertIn(state.split('\n')[2].split('|')[0], message)
                self.assertNotIn('no way back', message)
                self.assertNotIn('safety backup', message.lower())

    def test_an_unreadable_state_is_reported_as_unknown_with_the_drop_advice(self):
        for name, item in {'rc 1': (1, '', ERROR_OUTPUT), 'timeout': subprocess.TimeoutExpired('sqlcmd', 30.0), 'oserror': OSError('gone')}.items():
            with self.subTest(read=name):
                _, message = self.failing((1, BATCH_ERROR, ''), item)
                self.assertIn('unknown', message)
                self.assertIn('DROP DATABASE [appdb]', message)
                self.assertIn('/data/sql', message)

    def test_a_missing_database_after_a_failed_create_is_not_reported_as_unknown(self):
        _, message = self.failing((1, BATCH_ERROR, ''), (0, NOT_THERE, ''))
        self.assertNotIn('unknown', message)
        self.assertIn('/data/sql', message)

    def test_every_kind_of_failure_of_the_create_is_code_1(self):
        raised = {'rc 1': (1, BATCH_ERROR, ''), 'error line with rc 0': (0, BATCH_ERROR, ''), 'timeout': subprocess.TimeoutExpired('sqlcmd', 3600.0),
                  'interrupt': KeyboardInterrupt(), 'oserror': OSError('pipe broke')}
        for name, item in raised.items():
            with self.subTest(failure=name):
                runner = self.engine('new', restore=item, state=[(0, NOT_THERE, ''), (0, state_out('RESTORING'), '')])
                code, text = self.code(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
                self.assertEqual(code, 1)
                self.assertIn('DROP DATABASE [appdb]', text)
                self.assertIn('stan bazy mógł się zmienić', text)

    def test_the_tool_runs_no_drop_and_no_alter_itself(self):
        runner, _ = self.failing((1, BATCH_ERROR, ''), (0, state_out('RESTORING'), ''))
        for call in runner.calls:
            self.assertNotIn('DROP DATABASE', call['sql'])
            self.assertNotIn('ALTER DATABASE', call['sql'])

    def test_a_failure_to_read_the_final_state_after_a_good_create_advises_a_drop(self):
        runner = self.engine('new', state=[(0, NOT_THERE, ''), (1, '', ERROR_OUTPUT)])
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        self.assertIn('DROP DATABASE [appdb]', str(ctx.exception))
        self.assertNotIn('no way back', str(ctx.exception))

    def test_a_final_state_that_is_not_online_after_a_good_create_is_a_failure_with_a_drop_advice(self):
        for state in (state_out('RESTORING'), state_out('OFFLINE'), table(STATE_COLUMNS, [])):
            with self.subTest(state=state):
                runner = self.engine('new', state=[(0, NOT_THERE, ''), (0, state, '')])
                code, text = self.code(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
                self.assertEqual(code, 1)
                self.assertIn('DROP DATABASE [appdb]', text)
                self.assertNotIn('no way back', text)


# ---------------------------------------------------------------- A5: --dry-run

class MoveDryRun(MoveBase):
    def attempt(self, move, **kw):
        runner, fs = MoveRunner(self.m), MoveFs(dirs=['/data/sql'])
        with self.assertRaises(self.m.NotRun) as ctx:
            self.go(runner=runner, fs=fs, move=move, dry_run=True, **kw)
        return runner, fs, ctx.exception

    def test_no_call_to_the_engine_and_nothing_is_written(self):
        for move, kw in (('existing', {}), ('new', {}), ('new', {'move_dir': '/data/sql'})):
            with self.subTest(move=move, kw=kw):
                runner, fs, _ = self.attempt(move, **kw)
                self.assertEqual(runner.calls, [])
                self.assertEqual((fs.made, fs.written, fs.write_log, fs.sha_calls), ([], {}, [], []))
                self.assertIs(self.state.mutated, False)

    def test_the_plan_names_the_steps_and_has_no_move_list(self):
        for move, kw in (('existing', {}), ('new', {}), ('new', {'move_dir': '/data/sql'})):
            with self.subTest(move=move, kw=kw):
                _, _, exc = self.attempt(move, **kw)
                result = exc.result
                self.assertEqual(result['status'], 'NOT_RUN')
                self.assertEqual(result['database'], 'appdb')
                self.assertGreaterEqual(len(result['plan']), 5)
                text = json.dumps(result)
                self.assertNotIn("MOVE N'", text)
                self.assertNotIn("TO N'", text)
                self.assertNotIn('appdb_data', text)
                self.assertNotIn('RESTORE DATABASE [', text)

    def test_the_existing_plan_has_the_safety_backup_and_the_new_plan_has_none(self):
        _, _, existing = self.attempt('existing')
        self.assertTrue(any('safety backup' in step for step in existing.result['plan']))
        self.assertTrue(any('FILELISTONLY' in step for step in existing.result['plan']))
        _, _, created = self.attempt('new')
        self.assertFalse(any('make a safety backup' in step for step in created.result['plan']))
        self.assertTrue(any('FILELISTONLY' in step for step in created.result['plan']))

    def test_the_exit_code_is_3(self):
        for move in ('existing', 'new'):
            with self.subTest(move=move):
                code, _ = self.code(runner=MoveRunner(self.m), move=move, dry_run=True)
                self.assertEqual(code, 3)

    def test_the_plan_of_the_default_mode_is_unchanged_by_the_new_modes(self):
        with self.assertRaises(self.m.NotRun) as ctx:
            self.go(dry_run=True)
        self.assertNotIn('FILELISTONLY', json.dumps(ctx.exception.result))
        self.assertNotIn('MOVE', json.dumps(ctx.exception.result))


# ---------------------------------------------------------------- D2.4b: the output of the S1 probe

S1_FILELIST_OUTPUT = (
    'LogicalName|PhysicalName|Type|FileGroupName|Size|MaxSize|FileId|CreateLSN|DropLSN|UniqueId|ReadOnlyLSN|ReadWriteLSN|BackupSizeInBytes|SourceBlockSize|'
    'FileGroupId|LogGroupGUID|DifferentialBaseLSN|DifferentialBaseGUID|IsReadOnly|IsPresent|TDEThumbprint|SnapshotUrl\n'
    '-----------|------------|----|-------------|----|-------|------|---------|-------|--------|-----------|------------|-----------------|---------------|'
    '-----------|------------|-------------------|--------------------|----------|---------|-------------|-----------\n'
    'src_data|C:\\Users\\<user>\\AppData\\Local\\Temp\\skillsync-t6-s1-<id>\\a\\skillsync_t6_<id>_data.mdf|D|PRIMARY|8388608|35184372080640|1|0|0|'
    '82871CC2-4400-4CAC-9C69-BED974620FAD|0|0|2883584|4096|1|NULL|0|00000000-0000-0000-0000-000000000000|0|1|NULL|NULL\n'
    'src_data2|C:\\Users\\<user>\\AppData\\Local\\Temp\\skillsync-t6-s1-<id>\\a\\skillsync_t6_<id>_data2.ndf|D|FG2|8388608|35184372080640|3|37000000019200001|0|'
    '0F15F117-A573-47E8-8076-7CE7F8BDFFE2|0|0|196608|4096|2|NULL|0|00000000-0000-0000-0000-000000000000|0|1|NULL|NULL\n'
    'src_log|C:\\Users\\<user>\\AppData\\Local\\Temp\\skillsync-t6-s1-<id>\\a\\skillsync_t6_<id>_log.ldf|L|NULL|8388608|2199023255552|2|0|0|'
    'B31F08E6-4A11-4217-B406-12526078BB60|0|0|0|4096|0|NULL|0|00000000-0000-0000-0000-000000000000|0|1|NULL|NULL\n'
    '\n'
    '(3 rows affected)\n')
S1_COLUMNS = ['LogicalName', 'PhysicalName', 'Type', 'FileGroupName', 'Size', 'MaxSize', 'FileId', 'CreateLSN', 'DropLSN', 'UniqueId', 'ReadOnlyLSN',
              'ReadWriteLSN', 'BackupSizeInBytes', 'SourceBlockSize', 'FileGroupId', 'LogGroupGUID', 'DifferentialBaseLSN', 'DifferentialBaseGUID',
              'IsReadOnly', 'IsPresent', 'TDEThumbprint', 'SnapshotUrl']
S1_DB_ROWS = [['src_data', 'ROWS', '/var/sql/a_data.mdf'], ['src_log', 'LOG', '/var/sql/a_log.ldf'], ['src_data2', 'ROWS', '/var/sql/a_data2.ndf']]


class ProbeFixture(MoveBase):
    def test_the_22_column_output_of_the_probe_parses_with_the_row_order_1_3_2(self):
        rows = self.m.parse_sqlcmd_table(S1_FILELIST_OUTPUT)
        self.assertEqual(len(rows), 3)
        self.assertEqual(list(rows[0].keys()), S1_COLUMNS)
        self.assertEqual([row['FileId'] for row in rows], ['1', '3', '2'])
        self.assertEqual([row['Type'] for row in rows], ['D', 'D', 'L'])
        self.assertEqual(rows[0]['TDEThumbprint'], 'NULL')

    def test_the_parsed_output_passes_the_validation_in_any_row_order(self):
        rows = self.m.parse_sqlcmd_table(S1_FILELIST_OUTPUT)
        for ordering in itertools.permutations(rows):
            self.assertEqual(len(self.m.validate_filelist(list(ordering))), 3)

    def test_the_probe_output_gives_the_move_by_file_id_for_existing(self):
        runner = MoveRunner(self.m, {'filelist': [(0, S1_FILELIST_OUTPUT, '')], 'dbfiles': [(0, dbfiles_out(S1_DB_ROWS), '')]})
        self.go(runner=runner, move='existing')
        self.assertIn("WITH REPLACE, MOVE N'src_data' TO N'/var/sql/a_data.mdf', MOVE N'src_log' TO N'/var/sql/a_log.ldf', "
                      "MOVE N'src_data2' TO N'/var/sql/a_data2.ndf', FILE = 1, CHECKSUM;", self.restore_sql(runner))
        self.assertNotIn('<user>', self.restore_sql(runner))
        self.assertNotIn('skillsync-t6-s1', self.restore_sql(runner))

    def test_the_probe_output_gives_the_names_by_file_id_for_new(self):
        runner = self.engine('new', filelist=(0, S1_FILELIST_OUTPUT, ''))
        self.go(runner=runner, move='new', move_dir='/data/sql', fs=MoveFs(dirs=['/data/sql']))
        self.assertIn("WITH MOVE N'src_data' TO N'/data/sql/appdb_1_a1b2c3.mdf', MOVE N'src_log' TO N'/data/sql/appdb_2_a1b2c3.ldf', "
                      "MOVE N'src_data2' TO N'/data/sql/appdb_3_a1b2c3.ndf', FILE = 1, CHECKSUM", self.restore_sql(runner))
        self.assertNotIn('<user>', self.restore_sql(runner))


# ---------------------------------------------------------------- the parser and the command line

RESTORE_ARGS = ['restore', '--file', '/h/bk/appdb-snap-20261121-090000-d4e5f6.bak', '--database', 'appdb', '--confirm', 'appdb']


class MoveParser(Base):
    def parse(self, *extra):
        return self.m.build_parser().parse_args(RESTORE_ARGS + list(extra))

    def fails(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            self.m.build_parser().parse_args(argv)
        self.assertEqual(ctx.exception.code, 2)
        return err.getvalue()

    def test_the_mode_defaults_to_none_and_move_dir_to_nothing(self):
        args = self.parse()
        self.assertEqual(args.move, 'none')
        self.assertIsNone(args.move_dir)

    def test_the_three_modes_are_accepted(self):
        for mode in ('none', 'existing', 'new'):
            with self.subTest(mode=mode):
                self.assertEqual(self.parse('--move', mode).move, mode)

    def test_move_dir_is_taken_as_text(self):
        self.assertEqual(self.parse('--move', 'new', '--move-dir', '/data/sql').move_dir, '/data/sql')
        self.assertEqual(self.parse('--move=new', '--move-dir=/data/sql').move, 'new')

    def test_a_wrong_mode_is_a_usage_error_with_code_2(self):
        for mode in ('replace', 'NEW', 'Existing', '', 'all', 'none,new'):
            with self.subTest(mode=mode):
                self.assertIn('--move', self.fails(RESTORE_ARGS + ['--move', mode]))
        self.fails(RESTORE_ARGS + ['--move'])

    def test_flags_cannot_be_abbreviated(self):
        for flag in ('--mov', '--mo', '--move-d', '--move-di'):
            with self.subTest(flag=flag):
                self.fails(RESTORE_ARGS + [flag, 'new'])

    def test_the_other_subcommands_have_no_move_flags(self):
        self.fails(['backup', '--database', 'appdb', '--label', 'x', '--move', 'new'])
        self.fails(['prune', '--database', 'appdb', '--confirm', 'appdb', '--move-dir', '/x'])

    def test_the_help_mentions_the_modes(self):
        out = io.StringIO()
        self.assertEqual(self.m.main(['restore', '--help'], stdout=out, stderr=io.StringIO(), environ={}), 0)
        text = out.getvalue()
        for word in ('--move', '--move-dir', 'existing', 'new'):
            self.assertIn(word, text)


class MoveCommandLine(Base):
    def run_main(self, *extra, argv=None):
        out, err = io.StringIO(), io.StringIO()
        code = self.m.main((argv or RESTORE_ARGS) + list(extra), environ={'HOME': '/h/user'}, stdout=out, stderr=err, platform='linux')
        return code, out.getvalue(), err.getvalue()

    def test_the_refused_combinations_exit_with_2_before_the_engine_is_looked_for(self):
        for extra in (['--move-dir', '/data/sql'], ['--move', 'none', '--move-dir', '/data/sql'], ['--move', 'existing', '--move-dir', '/data/sql'],
                      ['--move', 'new', '--no-safety-backup']):
            with self.subTest(extra=extra):
                code, out, err = self.run_main(*extra)
                self.assertEqual(code, 2)
                self.assertNotIn('Traceback', err)
                self.assertNotIn('stan bazy mógł się zmienić', err)

    def test_a_wrong_mode_exits_with_2(self):
        code, _, err = self.run_main('--move', 'bogus')
        self.assertEqual(code, 2)
        code, _, _ = self.run_main('--mov', 'new')
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
