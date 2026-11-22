# Migawka bazy testowej przed zapisem (db_snapshot)

> **Status weryfikacji na silniku: niezweryfikowane na silniku.** Test na żywo (S7) nie został wykonany
> (wymaga SQL Servera, `sqlcmd`, zgody i LEASE), więc wyniki S7 są NOT_RUN. Narzędzie jest sprawdzone
> testami z atrapą `sqlcmd` (logika, kolejność wywołań, odmowy, maskowanie). Zachowanie prawdziwego silnika
> opisuje lista kontrolna na końcu przepisu.

Narzędzie: `skills/test-env/scripts/db_snapshot.py` (Python 3.11+, sama biblioteka standardowa, wymaga `sqlcmd` w `PATH`).
Skrypt nie ma linii shebang, więc uruchamiaj go przez `python3`. Przykłady zakładają, że komendy uruchamiasz
z katalogu skryptu; z innego katalogu podaj pełną ścieżkę do pliku.

## Kiedy użyć

- Przed zapisem testowym w **lokalnej** bazie: migracja, poprawka danych, test e2e, który zapisuje.
- Gdy trzeba móc cofnąć stan bazy do chwili sprzed zapisu.
- Nie dla serwerów zdalnych jako „cofnięcia”: zdalny `backup` jest dozwolony tylko jawnie (patrz model bezpieczeństwa),
  a `restore` na hoście nielokalnym jest zawsze odmawiany.

## Polecenia

### backup: kopia przed zapisem

```
python3 db_snapshot.py backup --database AppDb --label pre-migration --dir /srv/h/db-copies
```

Kolejność działania:

1. Walidacja: nazwa bazy, etykieta, host, katalog kopii poza repozytorium, ścieżka widziana przez serwer. Odmowa = kod 2, zero wywołań `sqlcmd`.
2. Tożsamość silnika (`@@SERVERNAME`, `SERVERPROPERTY('MachineName')`, `SERVERPROPERTY('InstanceName')`), odczyt tylko do odczytu.
3. `BACKUP DATABASE … WITH COPY_ONLY, INIT, CHECKSUM`. Od tego wywołania każda porażka to kod 1.
4. `RESTORE VERIFYONLY … WITH CHECKSUM, FILE = 1` (chyba że `--no-verify`).
5. `RESTORE HEADERONLY`: dokładnie jeden zestaw, `BackupType = 1` (pełna kopia), nazwa bazy zgodna bez względu na wielkość liter.
6. Sprawdzenie pliku (istnieje, niepusty), opcjonalnie `sha256`, zapis pliku pomocniczego `<plik>.json`.

`COPY_ONLY` nie zmienia łańcucha kopii DBA. Narzędzie nigdy nie nadpisuje istniejącego pliku.

Nazwa pliku: `<baza>-<etykieta>-<RRRRMMDD>-<GGMMSS>-<6 znaków hex>.bak`, czas w UTC, np.
`AppDb-pre-migration-20261122-102030-a1b2c3.bak`. Obok powstaje `<plik>.bak.json` (sidecar) z polami:
`schema`, `database`, `label`, `file`, `size`, `created_utc`, `server` (`raw`, `canonical`, `engine_server_name`,
`machine_name`, `instance_name`), `backup_set_guid`, `first_lsn`, `last_lsn`, `backup_finish_date`, `remote`,
`server_path`, `copy_only`, a przy `--sha256` (kopia lokalna) także `sha256`. Sidecar zapisuje się atomowo, z trybem 0600.

Opcje: `--server` (domyślnie `localhost`), `--dir <katalog>` (ścieżka widziana przez serwer), `--auth auto|integrated|env`,
`--trust-server-certificate` (dodaje `-C`, tylko lokalny serwer), `--timeout <s>` (domyślnie 600, patrz „Timeouty”),
`--allow-host <host>` (powtarzalne, tylko zdalny backup), `--server-os windows|linux` (wymagane dla zdalnego hosta z `--dir`),
`--sha256`, `--no-verify`, `--dry-run`, `--json`.

Wynik tekstowy: pierwsza linia to status, potem pola `klucz: wartość` posortowane po kluczu. Kształt
(wartości ilustracyjne):

```
PASS
coordinator_notice: Kopia bazy AppDb na serwerze localhost (backup, etykieta: pre-migration).
…
created_utc: 2026-11-22T10:20:30Z
database: AppDb
label: pre-migration
path: /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak
remote: False
restore_command: db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb --dir /srv/h/db-copies
server: localhost
sidecar: /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak.json
size: 1048576
verified_locally: True
verify: passed
```

Pole `restore_command` to gotowe polecenie cofnięcia; zapisz je przed zapisem testowym. Zawiera `--server`, gdy
serwer jest inny niż `localhost`, `--dir`, gdy podano `--dir`, i `--trust-server-certificate`, gdy go użyto.
Zaczyna się od nazwy skryptu: uruchom je jako `python3 db_snapshot.py restore …` z katalogu skryptu albo z pełną
ścieżką do pliku. Polecenie nie zawiera `--auth` ani `--allow-machine-mismatch`. Gdy backup użył `--auth` innego
niż `auto`, dopisz tę samą wartość; gdy restore wymaga `--allow-machine-mismatch`, dopisz też tę flagę.

### restore: cofnięcie do kopii

```
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb --dir /srv/h/db-copies
```

Warunki, które muszą być spełnione przed krokiem destrukcyjnym:

- `--confirm` musi być dokładnie nazwą bazy (`AppDb`); inaczej odmowa (kod 2).
- Plik leży w katalogu kopii (domyślnym albo z `--dir`), jest zwykłym plikiem i nie jest dowiązaniem symbolicznym.
- Plik pomocniczy `<plik>.json` istnieje i zgadza się z plikiem: baza, nazwa pliku, serwer (po normalizacji) i rozmiar.
  Brak sidecara = odmowa (kod 2), chyba że podano `--allow-missing-sidecar`; wtedy wynik ma ostrzeżenie, a tożsamość
  nagłówka jest sprawdzana tylko z silnikiem celu.
- Sidecar bez pola `sha256` daje ostrzeżenie `no sha256 recorded in the sidecar`. Gdy `sha256` jest, narzędzie liczy go
  przed krokiem destrukcyjnym; niezgodność = odmowa (kod 2).
- Nagłówek kopii (`HEADERONLY`): dokładnie jeden zestaw, pełna kopia, ta sama baza, `ServerName` i `MachineName`
  zgodne z silnikiem celu. Gdy jest sidecar, także `BackupSetGUID`, `FirstLSN`, `LastLSN` i `BackupFinishDate` zgadzają się
  z sidecarem, a `ServerName`/`MachineName` z sidecara odpowiadają silnikowi celu. Niezgodność = odmowa, chyba że
  `--allow-machine-mismatch` (wtedy ostrzeżenie w wyniku).
- Baza docelowa musi istnieć i być `ONLINE`. Restore nie odtwarza skasowanej bazy (wymagałoby `MOVE` i ścieżek plików).
- Przed krokiem destrukcyjnym powstaje kopia bezpieczeństwa `<baza>-pre-restore-…bak` (ta sama nazwa i sidecar jak przy
  `backup`, z własnym `VERIFYONLY`). Pomija ją tylko `--no-safety-backup`. Jej ścieżka jest wypisana na stderr jako
  `safety backup: <ścieżka>` przed przywracaniem, a w wyniku jako `safety_backup_path`; `rollback_command` to polecenie powrotu.

Wynik zawiera `previous_state` (stan sprzed przywrócenia), `warnings`, `restored_utc`, `rollback_command`
i `coordinator_notice`. Stan `FULL` w trybie odzyskiwania daje ostrzeżenie: `RESTORE … WITH REPLACE` przerywa łańcuch
kopii dziennika, a decyzja należy do administratora.

Podgląd bez zmian: dodaj `--dry-run` (kod 3, wypisany plan, zero wywołań `sqlcmd`, brak plików i katalogów).

Powrót do stanu sprzed `restore`: uruchom polecenie `rollback_command` z wyniku (przywraca kopię bezpieczeństwa).
Polecenie zaczyna się od nazwy skryptu, więc uruchamiaj je tak jak `restore_command` (z `python3`).

### Kolejność kontroli restore

1. Walidacja bez zapytań do silnika: nazwa, host lokalny, `--confirm`, katalog poza repozytorium, plik w katalogu kopii,
   zwykły plik (nie dowiązanie), sidecar (baza, plik, serwer, rozmiar, obecność lub `--allow-missing-sidecar`), sha256 z sidecara.
2. Stan bazy (`sys.databases`, z `-d master`): baza istnieje i jest `ONLINE`. Brak bazy albo inny stan = odmowa.
   Tryb `FULL` = ostrzeżenie.
3. Tożsamość silnika: `@@SERVERNAME` i `MachineName` z silnika muszą odpowiadać temu, co zapisano w sidecarze, oraz tej maszynie
   (bez wielkości liter, `MachineName` do 15 znaków). Rozbieżność = odmowa, chyba że `--allow-machine-mismatch`.
4. `RESTORE HEADERONLY` i sprawdzenie nagłówka (patrz wyżej). Błąd odczytu nagłówka przed zmianą = kod 2.
5. `RESTORE VERIFYONLY … WITH CHECKSUM, FILE = 1` (limit `--restore-timeout`). Błąd = kod 2, baza nietknięta.
6. Sprawdzenie `sha256` z sidecara, jeśli jest (niezgodność = kod 2).
7. Kopia bezpieczeństwa z własnym `VERIFYONLY` (chyba że `--no-safety-backup`). Jej błąd zatrzymuje cofnięcie; ponieważ
   `BACKUP` już się zaczął, kod to 1.
8. Ścieżka kopii bezpieczeństwa wypisana na stderr (`safety backup: …`), potem krok destrukcyjny: jeden batch `sqlcmd -b`:
   `BEGIN TRY` → `ALTER DATABASE … SET SINGLE_USER WITH ROLLBACK IMMEDIATE` → `RESTORE DATABASE … WITH REPLACE, FILE = 1, CHECKSUM`
   → `ALTER DATABASE … SET MULTI_USER` → `END TRY`; `BEGIN CATCH` próbuje `MULTI_USER` i rzuca `THROW`.
9. Stan po: baza musi być `ONLINE` i `MULTI_USER`. Inaczej kod 1 z odczytanym stanem i drogą powrotu.

Każde wywołanie `sqlcmd` idzie z `-d master` (login z domyślną bazą docelową nie trzyma połączenia z bazą, którą zmieniamy),
`-b` i `-x`.

### Droga powrotu po porażce restore

Po porażce kroku destrukcyjnego, przerwaniu albo timeoucie narzędzie odczytuje stan bazy (krótki limit 30 s) i nie
wykonuje ślepo `MULTI_USER`. Komunikat ma postać:

```
SqlFailure: RESTORE DATABASE did not finish cleanly (…). Database state read afterwards: state_desc=…, user_access_desc=…. …
The way back: restore the safety backup /srv/h/db-copies/AppDb-pre-restore-….bak (command: db_snapshot.py restore … ).
```

Gdy stan jest inny niż `ONLINE` (np. `RESTORING`), narzędzie odmawia `restore` z takiego stanu, więc komunikat podaje
ręczną drogę powrotu:

```
RESTORE DATABASE [AppDb] FROM DISK = N'/srv/h/db-copies/AppDb-pre-restore-….bak' WITH REPLACE, RECOVERY
```

Wykonaj ją na serwerze z kontem, które ma uprawnienia do `RESTORE` tej bazy. Nie zakładaj, że baza jest sprawna, dopóki nie odczytasz stanu.

## Timeouty

- `--timeout <s>` (backup i restore, domyślnie 600 s): każde wywołanie zapisujące kopię (`BACKUP`, kopia bezpieczeństwa)
  i `VERIFYONLY` przy `backup`. Odczyt nagłówka (`HEADERONLY`) ma limit min(`--timeout`, 120 s).
- `--restore-timeout <s>` (restore, domyślnie 3600 s): `VERIFYONLY` przywracanej kopii i batch destrukcyjny.
- Odczyty tożsamości i stanu mają limit 30 s (krótszy z limitów).
- Po przekroczeniu czasu (`TimeoutExpired`) albo przerwaniu klienta stan bazy jest niepewny, nie wynik. Narzędzie kończy kodem 1
  z odczytanym stanem. Przerwanie w trakcie odczytu stanu (drugi Ctrl-C) daje stan `unknown`; komunikat zawiera ścieżkę
  kopii bezpieczeństwa.

## Kody wyjścia

| Kod | Status | Znaczenie |
|---|---|---|
| 0 | PASS | Operacja wykonana i sprawdzona. |
| 1 | FAIL | Operacja **rozpoczęła zmianę** (pierwsze wywołanie `BACKUP`, `ALTER` lub `RESTORE`) i się nie udała; **stan bazy mógł się zmienić**. Stderr zawiera linię `stan bazy mógł się zmienić`. |
| 2 | BLOCKED | **Baza nietknięta.** Odmowa (walidacja, host, `--confirm`, plik, sidecar, tożsamość, nagłówek, sha256, stan bazy) albo błąd wykonania **przed** pierwszym wywołaniem zmieniającym (odczyt stanu, tożsamości lub nagłówka, `VERIFYONLY` kopii przed przywróceniem). Także błąd składni argumentów. |
| 3 | NOT_RUN | `--dry-run` albo brak `sqlcmd` w `PATH`. |

Reguła jest jedna: kod 1 tylko po rozpoczęciu zmiany, kod 2 przed nią. Porażka `BACKUP` (plik kopii mógł powstać) to kod 1.

Różnica względem `ui-verify`: tam 1 oznacza wykrytą różnicę, tu 1 oznacza porażkę operacji po rozpoczęciu zmiany bazy.
Czytaj kod razem ze stderr.

Przykład odmowy (kod 2, baza nietknięta):

```
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb
```

stderr: `Refusal: rule confirm: --confirm must repeat the database name exactly`, kod 2.

Przykład błędu przed zmianą (kod 2, baza nietknięta): `SqlFailure: RESTORE VERIFYONLY failed (rc 1): …`. Kopia jest
uszkodzona lub obca, więc przywracanie nie startuje. Brak linii `stan bazy mógł się zmienić`.

Przykład porażki po zmianie (kod 1): stderr zawiera `SqlFailure: RESTORE DATABASE did not finish cleanly (…)`, stan odczytany
po błędzie, ścieżkę kopii bezpieczeństwa i linię `stan bazy mógł się zmienić`. Najpierw przeczytaj stan bazy i opis,
potem użyj kopii bezpieczeństwa z komunikatu (patrz „Droga powrotu”).

Przykład NOT_RUN (kod 3): `--dry-run` wypisuje `NOT_RUN` i plan; brak `sqlcmd` daje `NotRun: sqlcmd was not found on PATH; …`.

## Model bezpieczeństwa

- **Tylko host lokalny dla restore.** Hosty lokalne: `localhost`, `.`, `(local)`, `127.0.0.1`, `::1`, `[::1]`, `(localdb)\<instancja>`,
  także z portem i instancją (`localhost\SQLEXPRESS`, `localhost,1433`). Składnia pełna: `host(\instancja)?(,port)?`, instancja
  `[A-Za-z0-9_$#-]{1,16}`, port 1–65535. Prefiksy (`tcp:`, `np:`, `lpc:`), `0.0.0.0`, `127.1` i końcowa kropka są odmową.
  `localhost.evil.example` i `127.0.0.2` są obce. `.\SQLEXPRESS` różni się od `.` (instancja jest częścią tożsamości).
- **Zdalny backup tylko jawnie.** Wymaga `--allow-host <host>` (dokładne dopasowanie, powtarzalne), `--dir` (ścieżka na serwerze)
  i `--server-os windows|linux`. Platforma ścieżek to `--server-os`; `realpath` i kontrola `.git` są pomijane, bo ścieżka
  jest na serwerze. Sidecar zapisuje się lokalnie w katalogu stanu, a wynik oznacza „zdalny host; ścieżka nie zweryfikowana lokalnie”.
- **Odmowa restore na zdalnym hoście** jest zawsze, także z `--allow-host` (dla restore takiej opcji nie ma).
- **Baza systemowa** (`master`, `model`, `msdb`, `tempdb`, `distribution`, `mssqlsystemresource`, bez względu na wielkość liter) jest odrzucana.
  Nazwa bazy: `[A-Za-z0-9_]{1,128}` (ASCII). Etykieta: `[a-z0-9-]{1,40}` (ASCII).
- **Dane logowania** tylko ze zmiennych `SQLCMDUSER` i `SQLCMDPASSWORD` (`--auth env`, domyślnie poza Windows) albo z logowania Windows
  (`--auth integrated`, domyślnie na Windows). Hasło nigdy nie trafia do argumentów procesu, wyjścia ani sidecara. Wyjście jest
  maskowane (także warianty URL, JSON, base64, UTF-16LE). Brak zmiennej w trybie `env` to odmowa (kod 2) z nazwą zmiennej.
  `--auth integrated` przekazuje procesowi `sqlcmd` tylko zmienne z białej listy (`PATH`, systemowe, `LC_*`), bez żadnej `SQLCMD*`.
- **Proces potomny `sqlcmd`:** `shell=False`, minimalne środowisko, katalog roboczy to prywatny pusty katalog 0700, tworzony
  na czas przebiegu i usuwany po nim (nie katalog projektu, kopii ani wspólny `/tmp`).
- **`--confirm`** musi równać się nazwie bazy. Chroni przed pomyłką, nie przed złośliwym wywołaniem.
- **Kopia bezpieczeństwa** (`pre-restore`) jest domyślna. `--no-safety-backup` jest ryzykowne: brak drogi powrotu po pomyłce.
  Wynik zawiera ostrzeżenie.
- **`--allow-missing-sidecar`**: restore bez pliku pomocniczego. Tożsamość nagłówka jest sprawdzana z silnikiem celu, a wynik ma ostrzeżenie.
- **`--allow-machine-mismatch`**: zgoda na kopię z innej maszyny albo na silnik na innej maszynie (np. kontener, tunel).
  Bez tej flagi odmowa. Flaga dotyczy zarówno nagłówka i sidecara, jak i nazwy maszyny silnika.
- **Kopie poza repozytorium.** Katalog nie może leżeć w drzewie z `.git` (także w worktree i submodule). Domyślny katalog to
  Linux `$XDG_STATE_HOME/db-copies` lub `~/.local/state/db-copies`, macOS `~/Library/Application Support/db-copies`,
  Windows `%LOCALAPPDATA%\db-copies`. Ścieżki: tylko ASCII, bezwzględne, bez `..`, bez znaków spoza `[A-Za-z0-9 _.\-\\/:]`,
  bez końcowej spacji lub kropki w segmencie, bez strumieni NTFS, bez nazw urządzeń. Domyślny katalog z apostrofem w nazwie
  użytkownika jest odrzucany. Odmowy `dir-outside-repo` i `server-path-charset` podpowiadają użycie `--dir`.
- **Kopie zawierają dane osobowe i nie są szyfrowane.** Trzymaj je poza repozytorium; narzędzie ich nie usuwa.
- **Sidecar jest danymi niezaufanymi**: limit 64 KB, ścisły schemat (nieznane klucze = odmowa, duplikat klucza = odmowa),
  żadne ścieżki z niego nie są czytane. Pole `sha256` musi mieć postać 64 znaków hex.
- **Plik kopii nie może być dowiązaniem symbolicznym** (`realpath` pliku musi wskazywać ten plik w katalogu kopii).

## Ścieżka widziana przez serwer

Ścieżka kopii jest ścieżką **serwera**, nie klienta. Dla lokalnej instancji na tej samej maszynie to ten sam katalog.
Serwer w kontenerze, pod innym kontem (np. konto usługi SQL) albo z innym widokiem systemu plików nie zobaczy katalogu domowego klienta.
Katalog tworzony jest z trybem `0700`, więc takie konto go nie odczyta. Wtedy przygotuj katalog dostępny dla procesu serwera
i podaj go w `--dir`. Brak pliku po `backup` daje kod 1, a nie ciche powodzenie.

## Granice

- Nie odtwarza skasowanej bazy (wymagałoby `MOVE` i ścieżek plików): odmowa kodem 2 z instrukcją ręczną.
- Obsługuje tylko pojedynczy pełny zestaw kopii. Plik z wieloma zestawami to odmowa przy restore i błąd przy backup.
- `RESTORE … WITH REPLACE` w trybie `FULL` przerywa łańcuch kopii dziennika. Ostrzeżenie w wyniku; decyzja należy do administratora.
- `CHECKSUM` wymaga kopii z sumami kontrolnymi. Kopia bez sum (obca) daje błąd `VERIFYONLY` przed zmianą, czyli kod 2.
- `VERIFYONLY` nie dowodzi, że `RESTORE` uda się na innej wersji silnika.
- Brak retencji: narzędzie nie usuwa `.bak`, nie planuje kopii, nie robi kopii innych silników ani plików poza bazą.
- `--no-verify` pomija `VERIFYONLY` przy backup; wynik ma `verify: skipped`.
- Wyjście narzędzia: stdout i stderr są przestawiane na `errors=replace`, a wyjście `sqlcmd` dekodowane z `errors=replace`,
  więc znaki spoza kodowania konsoli nie przerywają przebiegu.
- Parser tabeli `HEADERONLY` jest ścisły: po danych może być tylko `(N rows affected)` z liczbą równą liczbie wierszy;
  inny tekst = odmowa przed zmianą. Tryb tabelaryczny ma szerokość wiersza `-w 65535`.

## Mapowanie z lokalnych skryptów projektowych

Skrypty projektowe (PowerShell) przepinasz na narzędzie po wydaniu. Parametry mapują się tak:

| Skrypt projektowy | db_snapshot.py |
|---|---|
| `-Label <etykieta>` | `--label <etykieta>` (tylko `[a-z0-9-]`, do 40 znaków) |
| `-Restore <plik> -Force` | `restore --file <plik> --confirm <baza>` (bez `--confirm` odmowa) |
| `-WhatIf` | `--dry-run` (kod 3, nic nie robi) |

Różnice: `--confirm` musi być nazwą bazy, a nie samym `-Force`. Kopia bezpieczeństwa domyślnie.
Kod wyjścia 1 w tym narzędziu to „stan mógł się zmienić”, nie „wykryta różnica”. Kod 2 to „baza nietknięta”.

## Informacja dla koordynatora

Wynik `backup` i `restore` zawiera pole `coordinator_notice`: po polsku, z bazą, serwerem, etykietą, ścieżką,
rozmiarem, czasem UTC, poleceniem cofnięcia (bez sekretów) i ostrzeżeniami: baza testowa jest współdzielonym
stanem, kopia może zawierać dane osobowe i nie jest szyfrowana. Dla `restore` blok zawiera też ścieżkę kopii
bezpieczeństwa i polecenie powrotu. Wklej ten blok koordynatorowi środowiska testowego przed zapisem i przed cofnięciem.
Flaga `--json` zwraca te same pola maszynowo.

Polecenie cofnięcia w bloku uruchamiaj z katalogu skryptu albo z pełną ścieżką do `db_snapshot.py`, z prefiksem `python3`.

## Hasło bez śladu w historii powłoki

Nie wpisuj hasła w argumentach ani w `export SQLCMDPASSWORD=…` na linii polecenia. Ustaw je z pytaniem bez echa:

```
read -r SQLCMDUSER
read -rs SQLCMDPASSWORD
export SQLCMDUSER SQLCMDPASSWORD
python3 db_snapshot.py backup --database AppDb --label pre-migration --auth env
unset SQLCMDPASSWORD SQLCMDUSER
```

Alternatywa: menedżer sekretów, który wstawia zmienne do procesu. Na Windows użyj `--auth integrated`
(bez zmiennych) i tego samego interpretera Pythona.

## Status weryfikacji na silniku

Test na żywo (S7) **nie został wykonany**: NOT_RUN. Warunki odbioru S7 (lista kontrolna, wszystkie
pozycje NOT_RUN do czasu wykonania testu na prawdziwym SQL Serverze i zgody). Do odbioru przyjmuje się jedną wersję
`sqlcmd` (ODBC sqlcmd 18 albo go-sqlcmd); flagi, które się różnią, opisze wynik testu.

1. [ ] Zachowanie `sqlcmd -Q` z `-x` i z `-X`: czy polecenia `sqlcmd` (`:r`, `:!!`) w ścieżce lub zapytaniu są wykonywane; wybór flagi.
2. [ ] Czy `-X` wyłącza odczyt `SQLCMDPASSWORD` (decyzja o fladze).
3. [ ] Dokładny format `RESTORE HEADERONLY` (nazwy i kolejność kolumn) w użytej wersji silnika; prawdziwe wzorce do fixtur.
   Sprawdź też, czy tryb tabelaryczny `-W -s "|" -w 65535` nie łamie wiersza `HEADERONLY` i czy ścisły parser przyjmuje
   prawdziwe wyjście bez fałszywej odmowy.
4. [ ] Stan bazy po przerwanym `RESTORE` (timeout, zabicie klienta) i po porażce środkowego kroku batcha z `TRY/CATCH`.
5. [ ] Zachowanie batcha po błędzie poziomu 16 przy `ALTER DATABASE … SINGLE_USER` i `-b` (czy batch kontynuuje).
6. [ ] Uprawnienia konta usługi do katalogu kopii (LocalDB, pełna instancja, Linux, kontener).
7. [ ] Treść komunikatów błędów logowania (czy zawierają login) i `-C` na Linuksie.

Do czasu PASS punktów 3, 4 i 5 ten przepis i rekord domenowy `db-snapshot-odmowy-i-tozsamosc.md` są
**niezweryfikowane na silniku**. Raport testu na żywo podaje wersję `sqlcmd` i silnika, maszynę i wynik każdego punktu.
