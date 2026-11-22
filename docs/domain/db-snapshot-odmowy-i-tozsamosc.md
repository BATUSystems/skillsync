---
title: 'Migawka bazy testowej (db_snapshot): odmowy, tożsamość serwera i kopii, kody wyjścia. Niezweryfikowane na silniku'
tags: [test-env, sql-server, backup, restore, sqlcmd, safety, identity, exit-codes]
paths: ['library/skills/test-env/scripts/db_snapshot.py', 'library/skills/test-env/references/db-snapshot.md', 'library/tools/index.yaml', 'tests/test_skillsync_db_snapshot.py']
source: 'plan zadania (poza rdzeniem); library/skills/test-env/scripts/db_snapshot.py:1-34 (nagłówek, granice, bezpieczeństwo), :115-209 (walidatory i tożsamość serwera), :705-786 (backup), :911-1049 (restore), :1025-1033 (krok destrukcyjny i stan po porażce)'
verified: 2026-11-22
---
**Status: niezweryfikowane na silniku.** Test na żywo (S7) nie został wykonany (NOT_RUN). Reguły poniżej
są potwierdzone kodem i testami z atrapą `sqlcmd`, nie prawdziwym SQL Serverem. Lista kontrolna S7 jest w
`library/skills/test-env/references/db-snapshot.md`.

Reguła: narzędzie robi kopię i przywraca wyłącznie lokalną bazę testową. Każda odmowa (kod 2) następuje przed
jakąkolwiek zmianą bazy i bez wywołania polecenia zmieniającego (`BACKUP`, `ALTER`, `RESTORE`).

Odmowy przed zmianą (kod 2):
- Nazwa bazy spoza `[A-Za-z0-9_]{1,128}` albo baza systemowa (`master`, `model`, `msdb`, `tempdb`, `distribution`, `mssqlsystemresource`), bez względu na wielkość liter.
- Etykieta spoza `[a-z0-9-]{1,40}`.
- Host spoza listy lokalnej: lokalne są tylko `localhost`, `.`, `(local)`, `127.0.0.1`, `::1`, `[::1]`, `(localdb)\instancja` (z portem i instancją). `localhost.evil.example` i `127.0.0.2` są obce.
- Zdalny host przy backup bez dokładnego `--allow-host` albo bez `--dir` i `--server-os`. Restore na hoście nielokalnym jest zawsze odmawiany, także z `--allow-host`.
- Katalog kopii w drzewie z `.git` (także worktree i submoduł) albo ze znakami spoza białej listy ścieżek.
- Restore bez `--confirm` równego nazwie bazy.
- Plik kopii nieistniejący, nie będący zwykłym plikiem albo poza katalogiem kopii.
- Restore przy braku pliku pomocniczego `<plik>.json`, chyba że `--allow-missing-sidecar`.
- Sidecar z inną bazą, innym plikiem, innym serwerem albo innym rozmiarem pliku.
- Nagłówek kopii (`HEADERONLY`) z inną bazą, inną liczbą zestawów albo innym typem niż pełna kopia.
- Restore, gdy baza nie istnieje (odtworzenie wymagałoby `MOVE`) albo nie jest `ONLINE`.
- Nazwa maszyny z silnika różna od tej maszyny (bez wielkości liter, do 15 znaków), chyba że `--allow-machine-mismatch`.

Tożsamość kopii: kopia wiąże się z serwerem przez nazwę serwera z silnika, `MachineName` i `InstanceName` z silnika
(zapisywane w sidecarze) oraz z `BackupSetGUID`, `FirstLSN` i `LastLSN` z `HEADERONLY`. Restore porównuje
je z sidecarem i z aktualnym nagłówkiem; zgodność jest warunkiem wykonania.

Dane logowania: tylko `SQLCMDUSER` i `SQLCMDPASSWORD` (tryb `env`, domyślnie poza Windows) albo logowanie
Windows (`integrated`). Nigdy w argumentach procesu, w wyjściu, w sidecarze ani w komunikatach. Wyjście
jest maskowane. Login nie jest maskowany w komunikatach serwera.

Kody wyjścia (decyzja Dyspozytora, 22.11): 0 PASS; 1 FAIL, operacja się nie udała, stan bazy mógł się zmienić
(błąd SQL, brak pliku po `backup`, wyjątek po zmianie); 2 BLOCKED, odmowa przed zmianą; 3 NOT_RUN, dry-run
albo brak `sqlcmd`. Różnica względem `ui-verify`, gdzie 1 oznacza wykrytą różnicę, jest zamierzona.

Znana rozbieżność do rozstrzygnięcia: błąd `RESTORE VERIFYONLY` i `HEADERONLY` (przed zmianą bazy) daje kod 1
(`library/skills/test-env/scripts/db_snapshot.py:1009-1012`), a spec wymaga dla `VERIFYONLY` kodu 1 bez
dotykania bazy. Semantyka kodów mówi, że 2 to odmowa przed zmianą. Kod pozostaje jak jest, do decyzji orkiestratora.

Kolejność restore: stan bazy, tożsamość silnika, `HEADERONLY`, `VERIFYONLY`, kopia bezpieczeństwa z własnym
`VERIFYONLY` (`pre-restore`, nigdy nie nadpisuje), ścieżka kopii wypisana na stderr, batch `sqlcmd -b`
z `TRY/CATCH` (`SINGLE_USER`, `RESTORE … WITH REPLACE`, `MULTI_USER`), stan po (`ONLINE` i `MULTI_USER`).
Po porażce kroku destrukcyjnego lub przerwaniu narzędzie odczytuje stan bazy i nie wykonuje ślepo `MULTI_USER`.

Granice: nie odtwarza skasowanej bazy; `FULL` recovery przerywa łańcuch kopii dziennika (ostrzeżenie);
`CHECKSUM` wymaga kopii z sumami; `VERIFYONLY` nie dowodzi możliwości `RESTORE` na innej wersji silnika;
brak retencji; kopie nie są szyfrowane i mogą zawierać dane osobowe.

Gdzie w kodzie: `library/skills/test-env/scripts/db_snapshot.py:115` (walidatory nazw), `:166` (`parse_server`),
`:198` (`check_host_allowed`), `:212` (ścieżki serwera), `:705` (`run_backup`), `:939` (`run_restore`).
