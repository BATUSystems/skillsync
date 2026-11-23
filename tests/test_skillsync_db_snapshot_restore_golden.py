"""Złoty wzorzec K1 (CORE-1142-T6, Dostawa 1): zachowanie `restore` (tryb domyślny) z kodu 1.14.1, zapisane jako LITERAŁ przed jakąkolwiek zmianą.
Testy 1.14.1 liczą oczekiwane polecenia przez `build_*`, więc porównywałyby kod z samym sobą; ten plik porównuje z tekstem zapisanym raz z kodu bazowego
(release/1.14.1 @ 66302cd): lista wywołań silnika (rodzaj, znacznik zmiany, tryb tabeli, SQL), klucze i treść wyniku, linie `emit`, plan `--dry-run`,
`coordinator_notice` i teksty `_way_back`. Zmiana któregokolwiek elementu w trybie domyślnym ma wymagać świadomej decyzji, nie przypadku."""
import json
import unittest

from tests.test_skillsync_db_snapshot import RestoreBase, SAFETY_PATH

GOLDEN = json.loads(r'''
{
 "calls": [
  [
   "state",
   false,
   true,
   "SELECT state_desc, user_access_desc, recovery_model_desc FROM sys.databases WHERE name = N'appdb'"
  ],
  [
   "identity",
   false,
   true,
   "SELECT @@SERVERNAME AS server_name, CAST(SERVERPROPERTY('MachineName') AS nvarchar(256)) AS machine_name, CAST(SERVERPROPERTY('InstanceName') AS nvarchar(256)) AS instance_name"
  ],
  [
   "header",
   false,
   true,
   "RESTORE HEADERONLY FROM DISK = N'/h/bk/appdb-snap-20261121-090000-d4e5f6.bak'"
  ],
  [
   "verify",
   false,
   false,
   "RESTORE VERIFYONLY FROM DISK = N'/h/bk/appdb-snap-20261121-090000-d4e5f6.bak' WITH CHECKSUM, FILE = 1"
  ],
  [
   "identity",
   false,
   true,
   "SELECT @@SERVERNAME AS server_name, CAST(SERVERPROPERTY('MachineName') AS nvarchar(256)) AS machine_name, CAST(SERVERPROPERTY('InstanceName') AS nvarchar(256)) AS instance_name"
  ],
  [
   "backup",
   true,
   false,
   "BACKUP DATABASE [appdb] TO DISK = N'/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak' WITH COPY_ONLY, INIT, CHECKSUM"
  ],
  [
   "verify",
   false,
   false,
   "RESTORE VERIFYONLY FROM DISK = N'/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak' WITH CHECKSUM, FILE = 1"
  ],
  [
   "header",
   false,
   true,
   "RESTORE HEADERONLY FROM DISK = N'/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak'"
  ],
  [
   "restore",
   true,
   false,
   "BEGIN TRY ALTER DATABASE [appdb] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; RESTORE DATABASE [appdb] FROM DISK = N'/h/bk/appdb-snap-20261121-090000-d4e5f6.bak' WITH REPLACE, FILE = 1, CHECKSUM; ALTER DATABASE [appdb] SET MULTI_USER; END TRY BEGIN CATCH BEGIN TRY ALTER DATABASE [appdb] SET MULTI_USER; END TRY BEGIN CATCH DECLARE @i int; END CATCH; THROW; END CATCH"
  ],
  [
   "state",
   false,
   true,
   "SELECT state_desc, user_access_desc, recovery_model_desc FROM sys.databases WHERE name = N'appdb'"
  ]
 ],
 "constants": {
  "SAFETY_PATH": "/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak",
  "SRC_PATH": "/h/bk/appdb-snap-20261121-090000-d4e5f6.bak"
 },
 "coordinator_notice": "Przywrócono bazę appdb na serwerze localhost z pliku /h/bk/appdb-snap-20261121-090000-d4e5f6.bak (restore).\nCzas (UTC): 2026-11-22T10:20:30Z\nKopia bezpieczeństwa sprzed przywrócenia: /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak\nCofnięcie (przywrócenie kopii bezpieczeństwa): db_snapshot.py restore --file /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak --database appdb --confirm appdb --dir /h/bk\nUwaga: baza testowa jest współdzielonym stanem; poinformuj innych użytkowników środowiska o przywróceniu.\nUwaga: kopia może zawierać dane osobowe i nie jest szyfrowana.",
 "dry_run": {
  "database": "appdb",
  "file": "/h/bk/appdb-snap-20261121-090000-d4e5f6.bak",
  "plan": [
   "read the database state",
   "check the engine identity",
   "read the backup header (HEADERONLY)",
   "verify the backup (VERIFYONLY)",
   "make a safety backup (label pre-restore)",
   "restore with REPLACE (SINGLE_USER, then MULTI_USER)",
   "read the final database state"
  ],
  "status": "NOT_RUN",
  "warnings": []
 },
 "dry_run_calls": 0,
 "emitted": [
  "safety backup: /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak",
  "restoring database appdb from the backup file"
 ],
 "result": {
  "database": "appdb",
  "file": "/h/bk/appdb-snap-20261121-090000-d4e5f6.bak",
  "previous_state": {
   "recovery_model_desc": "SIMPLE",
   "state_desc": "ONLINE",
   "user_access_desc": "MULTI_USER"
  },
  "restored_utc": "2026-11-22T10:20:30Z",
  "rollback_command": "db_snapshot.py restore --file /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak --database appdb --confirm appdb --dir /h/bk",
  "safety_backup_path": "/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak",
  "server": "localhost",
  "status": "PASS",
  "warnings": []
 },
 "result_keys": [
  "coordinator_notice",
  "database",
  "file",
  "previous_state",
  "restored_utc",
  "rollback_command",
  "safety_backup_path",
  "server",
  "status",
  "warnings"
 ],
 "way_back": [
  "The way back: restore the safety backup /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak (command: CMD).",
  "No safety backup was made, so there is no way back.",
  "The way back: restore the safety backup /h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak (command: CMD). The database is not ONLINE and the tool refuses a restore from that state; restore it by hand: RESTORE DATABASE [appdb] FROM DISK = N'/h/bk/appdb-pre-restore-20261122-102030-a1b2c3.bak' WITH REPLACE, RECOVERY, FILE = 1"
 ]
}
''')


class RestoreDefaultModeGolden(RestoreBase):
    def test_calls_result_emit_and_notice_equal_the_1_14_1_literal(self):
        result = self.do_restore()
        self.assertEqual([[c['kind'], c['mutating'], c['table'], c['sql']] for c in self.runner.calls], GOLDEN['calls'])
        self.assertEqual(sorted(result.keys()), GOLDEN['result_keys'])
        self.assertEqual({k: result[k] for k in GOLDEN['result']}, GOLDEN['result'])
        self.assertEqual(result['coordinator_notice'], GOLDEN['coordinator_notice'])
        self.assertEqual([line for line, _ in self.emitted], GOLDEN['emitted'])

    def test_dry_run_plan_and_zero_engine_calls_equal_the_literal(self):
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_restore(dry_run=True)
        self.assertEqual(ctx.exception.result, GOLDEN['dry_run'])
        self.assertEqual(len(self.runner.calls), GOLDEN['dry_run_calls'])

    def test_way_back_texts_equal_the_literal(self):
        self.assertEqual([self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'ONLINE'), self.m._way_back(None, 'appdb', None, None),
                          self.m._way_back(SAFETY_PATH, 'appdb', 'CMD', 'RESTORING')], GOLDEN['way_back'])

    def test_the_literal_was_taken_from_the_1_14_1_constants(self):
        self.assertTrue(GOLDEN['calls'][8][3].startswith('BEGIN TRY ALTER DATABASE [appdb] SET SINGLE_USER'))
        self.assertNotIn('MOVE', GOLDEN['calls'][8][3])
        self.assertNotIn('FILELISTONLY', ' '.join(call[3] for call in GOLDEN['calls']))


if __name__ == '__main__':
    unittest.main()
