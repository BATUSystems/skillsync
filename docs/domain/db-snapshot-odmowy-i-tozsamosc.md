---
title: 'Migawka bazy testowej (db_snapshot): odmowy, tożsamość serwera i kopii, kody wyjścia'
tags: [test-env, sql-server, backup, restore, sqlcmd, safety, identity, exit-codes]
paths: ['library/skills/test-env/scripts/db_snapshot.py', 'library/skills/test-env/references/db-snapshot.md', 'library/tools/index.yaml', 'tests/test_skillsync_db_snapshot.py', 'tests/test_skillsync_db_snapshot_prune.py', 'tests/test_skillsync_db_snapshot_restore_golden.py']
source: 'library/skills/test-env/scripts/db_snapshot.py: docstring modułu (granice, bezpieczeństwo, kody wyjścia), build_parser, exit_code_for, run_backup, run_restore, _way_back, parse_sqlcmd_table, _restore_command; test na żywo S7 (22.11.2026, SQL Server LocalDB 2019)'
verified: 2026-11-23
---
**Status: zweryfikowane na SQL Server LocalDB 2019 (Windows, sqlcmd 15.0, ODBC 17, uwierzytelnianie zintegrowane); niezweryfikowane: pełne instancje, Linux/kontener (konto serwera, katalog kopii), go-sqlcmd, tryb `--auth env` z logowaniem SQL, zdalny backup, `-C`, stabilność tożsamości LocalDB po restarcie instancji.** Reguły poniżej są potwierdzone kodem, testami z atrapą `sqlcmd` i testem na żywo (22.11.2026). Wyniki punktów i wnioski: `library/skills/test-env/references/db-snapshot.md`, sekcja „Status weryfikacji na silniku”.

Reguła: narzędzie robi kopię i przywraca wyłącznie lokalną bazę testową. Każda odmowa (kod 2) następuje przed
jakąkolwiek zmianą bazy i bez wywołania polecenia zmieniającego (`BACKUP`, `ALTER`, `RESTORE`).

Odmowy przed zmianą (kod 2):
- Nazwa bazy spoza `[A-Za-z0-9_]{1,128}` albo baza systemowa (`master`, `model`, `msdb`, `tempdb`, `distribution`, `mssqlsystemresource`), bez względu na wielkość liter.
- Etykieta spoza `[a-z0-9-]{1,40}`.
- Host spoza listy lokalnej: lokalne są tylko `localhost`, `.`, `(local)`, `127.0.0.1`, `::1`, `[::1]`, `(localdb)\instancja` (z portem i instancją). `localhost.evil.example` i `127.0.0.2` są obce.
- Zdalny host przy backup bez dokładnego `--allow-host` albo bez `--dir` i `--server-os`. Restore na hoście nielokalnym jest zawsze odmawiany, także z `--allow-host`.
- Katalog kopii w drzewie z `.git` (także worktree i submoduł) albo ze znakami spoza białej listy ścieżek.
- Restore bez `--confirm` równego nazwie bazy.
- Plik kopii nieistniejący, nie będący zwykłym plikiem, dowiązaniem symbolicznym albo poza katalogiem kopii.
- Restore przy braku pliku pomocniczego `<plik>.json`, chyba że `--allow-missing-sidecar`.
- Sidecar z inną bazą, innym plikiem, innym serwerem albo innym rozmiarem pliku; sidecar z nieznanym kluczem albo większy niż 64 KB.
- Sha256 z sidecara różny od sumy pliku.
- Nagłówek kopii (`HEADERONLY`) z inną bazą, inną liczbą zestawów, innym typem niż pełna kopia, innym `ServerName`/`MachineName` niż silnik celu, albo niezgodny z sidecarem (`BackupSetGUID`, LSN, `BackupFinishDate`).
- Restore, gdy baza nie istnieje (odtworzenie wymagałoby `MOVE`) albo nie jest `ONLINE`.
- Nazwa maszyny z silnika różna od tej maszyny (bez wielkości liter; obcięcie do 15 znaków tylko dla nazwy 15-znakowej), chyba że `--allow-machine-mismatch`.
- Błąd `RESTORE VERIFYONLY` kopii przed przywróceniem (kod 2, baza nietknięta). Tak samo odczyt stanu, tożsamości lub nagłówka, który nie powiódł się przed pierwszym wywołaniem zmieniającym.

Tożsamość serwera i kopii: kopia wiąże się z serwerem przez nazwę serwera z silnika (`@@SERVERNAME`), `MachineName` i `InstanceName`
z silnika (zapisane w sidecarze przy `backup`), oraz z `BackupSetGUID`, `FirstLSN`, `LastLSN` i `BackupFinishDate`
z `HEADERONLY`. Restore porównuje je z sidecarem i z aktualnym nagłówkiem; zgodność jest warunkiem wykonania. Gdy
sidecara brak (`--allow-missing-sidecar`), nagłówek jest porównywany z tożsamością silnika celu, a wynik ma ostrzeżenie.
Sha256 z sidecara (gdy jest) liczy się przed krokiem destrukcyjnym; brak pola daje ostrzeżenie `no sha256 recorded`.

Dane logowania: tylko `SQLCMDUSER` i `SQLCMDPASSWORD` (tryb `env`, domyślnie poza Windows) albo logowanie
Windows (`integrated`). Nigdy w argumentach procesu, w wyjściu, w sidecarze ani w komunikatach. Wyjście
jest maskowane. Login nie jest maskowany w komunikatach serwera. Proces `sqlcmd` dostaje tylko zmienne z białej listy.
Narzędzie nie używa `-X`: pod `-X` sqlcmd 15.0 ignoruje `SQLCMDPASSWORD` i pyta o hasło z konsoli (test S7, 22.11.2026), więc tryb `env` działa bez tej flagi.

Kody wyjścia: 0 PASS; 1 FAIL po rozpoczęciu zmiany (pierwsze wywołanie `BACKUP`, `ALTER` lub `RESTORE` już się zaczęło,
stan bazy mógł się zmienić: błąd SQL, brak pliku po `backup`, błąd kopii bezpieczeństwa, wyjątek po zmianie); 2 BLOCKED,
odmowa albo błąd wykonania przed pierwszą zmianą, baza nietknięta; 3 NOT_RUN, dry-run albo brak `sqlcmd`. Różnica względem
`ui-verify`, gdzie 1 oznacza wykrytą różnicę, jest zamierzona.

Kolejność restore: stan bazy (`ONLINE`), tożsamość silnika, `HEADERONLY` i zgodność nagłówka z sidecarem, `VERIFYONLY`
(błąd = kod 2), sha256 z sidecara, kopia bezpieczeństwa z własnym `VERIFYONLY` (`pre-restore`, nigdy nie nadpisuje; jej błąd
po rozpoczęciu `BACKUP` = kod 1, wcześniejszy = 2), ścieżka kopii bezpieczeństwa wypisana na stderr, batch `sqlcmd -b` z `TRY/CATCH` (`SINGLE_USER`,
`RESTORE … WITH REPLACE`, `MULTI_USER`), stan po (`ONLINE` i `MULTI_USER`). Po porażce kroku destrukcyjnego, timeoucie
lub przerwaniu narzędzie odczytuje stan bazy i nie wykonuje ślepo `MULTI_USER`. Droga powrotu to kopia bezpieczeństwa:
`rollback_command` w wyniku, a gdy stan nie jest `ONLINE`, ręczny `RESTORE DATABASE … WITH REPLACE, RECOVERY, FILE = 1` (wariant z `FILE = 1` potwierdzony na LocalDB 2019). Po przerwanym `RESTORE` baza zostaje `RESTORING` z `SINGLE_USER` i nie wraca sama; narzędzie nie odtwarza jej ślepo, decyzja i powrót są ręczne.

Granice: nie odtwarza skasowanej bazy; `FULL` recovery przerywa łańcuch kopii dziennika (ostrzeżenie);
`CHECKSUM` wymaga kopii z sumami (kopia obca = kod 2 przed zmianą); `VERIFYONLY` nie dowodzi możliwości `RESTORE`
na innej wersji silnika; retencja tylko ręczna (`prune`); kopie nie są szyfrowane i mogą zawierać dane osobowe.

Gdzie w kodzie (`library/skills/test-env/scripts/db_snapshot.py`): `validate_database` i `validate_label` (nazwy),
`parse_server` i `check_host_allowed` (hosty), `validate_server_path` (ścieżki serwera), `check_dir_outside_repo`,
`read_sidecar` i `machine_names_match` (sidecar i tożsamość), `run_backup` (backup), `run_restore` (restore),
`_check_restore_header` (nagłówek), `_way_back` (droga powrotu), `exit_code_for` (kody), `parse_sqlcmd_table` (parser),
`_restore_command` (polecenia cofnięcia).

**Retencja (`prune`).** Osobne podpolecenie, które nie kontaktuje się z silnikiem i usuwa wyłącznie pliki rozpoznane jako własne: zwykły `.bak` o nazwie zgodnej ze wzorcem narzędzia z lokalnym sidecarem zgodnym z nazwą, etykietą, rozmiarem i czasem (`created_utc` równy znacznikowi z nazwy). Sidecary zdalnych kopii, pliki bez sidecara, dowiązania, katalogi i obce pliki są pomijane z powodem i nigdy nie usuwane. Zostaje N najnowszych na (baza, etykieta), nic młodszego niż 1 godzina, usuwanie w kolejności `.bak` → sidecar, ponowne `lstat` tuż przed usunięciem. Kody `prune` są osobne od `backup`/`restore`: 1 po usunięciu choć jednego pliku i błędzie lub przerwaniu, 2 przed pierwszym usunięciem albo gdy nic nie usunięto (kod 2 znaczy „katalog bez zmian”), 3 `--dry-run`; nie ma tu linii „stan bazy mógł się zmienić”. Wyjątek wokół całego `prune` zastępuje `run_guarded`. W kodzie: `parse_snapshot_name`, `plan_prune` (reguła „nasz plik” i retencja), `execute_prune`, `run_prune`, `run_prune_guarded`, `_main_prune`. Szczegóły i ryzyko rezydualne: `references/db-snapshot.md`, sekcja „prune”.
