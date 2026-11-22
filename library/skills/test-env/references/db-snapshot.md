# Migawka bazy testowej przed zapisem (db_snapshot)

> **Status weryfikacji na silniku: niezweryfikowane na silniku.** Test na żywo (S7) nie został wykonany
> (wymaga SQL Servera, `sqlcmd`, zgody i LEASE), więc wyniki S7 są NOT_RUN. Narzędzie jest sprawdzone
> testami z atrapą `sqlcmd` (logika, kolejność wywołań, odmowy, maskowanie). Zachowanie prawdziwego silnika
> opisuje lista kontrolna na końcu przepisu.

Narzędzie: `skills/test-env/scripts/db_snapshot.py` (Python 3.11+, sama biblioteka standardowa, wymaga `sqlcmd` w `PATH`).
Przykłady poniżej zakładają, że komendy uruchamiasz z katalogu skryptu; z innego katalogu podaj pełną ścieżkę do pliku.

## Kiedy użyć

- Przed zapisem testowym w **lokalnej** bazie: migracja, poprawka danych, test e2e, który zapisuje.
- Gdy trzeba móc cofnąć stan bazy do chwili sprzed zapisu.
- Nie dla serwerów zdalnych jako „cofnięcia”: zdalny `backup` jest dozwolony tylko jawnie (patrz model bezpieczeństwa), a `restore` na zdalnym hoście jest zawsze odmawiany.

## Polecenia

### backup: kopia przed zapisem

```
python3 db_snapshot.py backup --database AppDb --label pre-migration
```

Robi `BACKUP DATABASE … WITH COPY_ONLY, INIT, CHECKSUM`, potem `RESTORE VERIFYONLY … WITH CHECKSUM` (chyba że
`--no-verify`), potem `RESTORE HEADERONLY` i zapis pliku pomocniczego. Nie zmienia łańcucha kopii DBA
(`COPY_ONLY`) i nigdy nie nadpisuje istniejącego pliku.

Nazwa pliku: `<baza>-<etykieta>-<RRRRMMDD>-<GGMMSS>-<6 znaków hex>.bak`, czas w UTC, np.
`AppDb-pre-migration-20261122-102030-a1b2c3.bak`. Obok powstaje `<plik>.bak.json` (sidecar: serwer,
baza, etykieta, czas, rozmiar, GUID zestawu, LSN, czas zakończenia).

Opcje: `--server` (domyślnie `localhost`), `--dir <katalog>` (ścieżka widziana przez serwer), `--auth auto|integrated|env`,
`--trust-server-certificate` (tylko lokalny serwer), `--timeout <s>` (domyślnie 600), `--sha256` (suma w sidecarze),
`--no-verify`, `--dry-run`, `--json`.

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
restore_command: db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb
server: localhost
sidecar: /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak.json
size: 1048576
verified_locally: True
verify: passed
```

Pole `restore_command` to gotowe polecenie cofnięcia; zapisz je przed zapisem testowym.

### restore: cofnięcie do kopii

```
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb
```

- `--confirm` musi być dokładnie nazwą bazy (`AppDb`); inaczej odmowa (kod 2).
- Plik musi leżeć w katalogu kopii (domyślnym albo z `--dir`) i mieć sidecar `<plik>.json` o zgodnej bazie, serwerze i rozmiarze.
- Przed krokiem destrukcyjnym domyślnie powstaje kopia bezpieczeństwa `<baza>-pre-restore-…bak` (nazwa jak w `backup`, z sidecarem i własnym `VERIFYONLY`); pomija ją tylko `--no-safety-backup`.
  Jej ścieżka jest wypisana na stderr przed przywracaniem i w wyniku jako `safety_backup_path`, a `rollback_command` to polecenie powrotu.
- Wynik zawiera `previous_state` (stan sprzed przywrócenia), `warnings` i `coordinator_notice`.

Podgląd bez zmian: dodaj `--dry-run` (kod 3, wypisany plan, zero wywołań `sqlcmd`, brak plików).

Powrót do stanu sprzed `restore`: uruchom polecenie `rollback_command` z wyniku (przywraca kopię bezpieczeństwa).

## Kody wyjścia

| Kod | Status | Znaczenie |
|---|---|---|
| 0 | PASS | Operacja wykonana i sprawdzona. |
| 1 | FAIL | Operacja się nie udała, **stan bazy mógł się zmienić**. |
| 2 | BLOCKED | Odmowa **przed zmianą** (walidacja, host, `--confirm`, kontrola kopii, stan bazy, błąd wewnętrzny przed zmianą). |
| 3 | NOT_RUN | `--dry-run` albo brak `sqlcmd` w `PATH`. |

Różnica względem `ui-verify`: tam 1 oznacza wykrytą różnicę, tu 1 oznacza porażkę operacji. Czytaj kod razem ze stderr.

Przykład odmowy (kod 2, baza nietknięta):

```
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb
```

stderr: `Refusal: rule confirm: --confirm must repeat the database name exactly`, kod 2.

Przykład porażki (kod 1): stderr zawiera `SqlFailure: RESTORE DATABASE did not finish cleanly (…)`, stan odczytany po
błędzie, ścieżkę kopii bezpieczeństwa i linię `stan bazy mógł się zmienić`. Najpierw przeczytaj stan bazy i opis,
potem użyj kopii bezpieczeństwa z komunikatu. Nie zakładaj, że baza jest sprawna.

Przykład NOT_RUN (kod 3): `--dry-run` wypisuje `NOT_RUN` i plan; brak `sqlcmd` daje `NotRun: sqlcmd was not found on PATH; …`.

Uwaga o rozbieżności (do rozstrzygnięcia przez orkiestratora): błąd `VERIFYONLY` i `HEADERONLY` (kroki przed
zmianą bazy) kończy się kodem 1, nie 2. Spec (H1) opisuje dla `VERIFYONLY` kod 1 bez dotykania bazy, a semantyka
kodów mówi, że 1 oznacza „stan mógł się zmienić”. Tak działa kod; przy wyniku 1 po `VERIFYONLY` baza nie została
jeszcze przywrócona.

## Model bezpieczeństwa

- **Tylko host lokalny dla restore.** Hosty lokalne: `localhost`, `.`, `(local)`, `127.0.0.1`, `::1`, `[::1]`, `(localdb)\<instancja>`,
  także z portem i instancją (`localhost\SQLEXPRESS`, `localhost,1433`). Inne wartości są obce. `localhost.evil.example` i `127.0.0.2` są obce.
- **Zdalny backup tylko jawnie.** Wymaga `--allow-host <host>` (dokładne dopasowanie, powtarzalne), `--dir` (ścieżka na serwerze)
  i `--server-os windows|linux`. Sidecar zdalnej kopii zapisuje się lokalnie, a wynik oznacza „zdalny host; ścieżka nie zweryfikowana lokalnie”.
- **Odmowa restore na zdalnym hoście** jest zawsze, także z `--allow-host` (brak takiej opcji dla restore).
- **Baza systemowa** (`master`, `model`, `msdb`, `tempdb`, `distribution`, `mssqlsystemresource`) jest odrzucana. Nazwa: `[A-Za-z0-9_]{1,128}`, etykieta: `[a-z0-9-]{1,40}`.
- **Dane logowania** tylko ze zmiennych `SQLCMDUSER` i `SQLCMDPASSWORD` (`--auth env`, domyślnie poza Windows) albo z logowania Windows (`--auth integrated`, domyślnie na Windows).
  Hasło nigdy nie trafia do argumentów procesu, wyjścia ani sidecara. Wyjście jest maskowane (także warianty URL, JSON, base64, UTF-16LE).
  Brak zmiennej w trybie `env` to odmowa (kod 2) z nazwą zmiennej. `--auth integrated` czyści zmienne `SQLCMD*` z procesu potomnego.
- **`--confirm`** musi równać się nazwie bazy. Chroni przed pomyłką, nie przed złośliwym wywołaniem.
- **Kopia bezpieczeństwa** (`pre-restore`) jest domyślna. `--no-safety-backup` jest ryzykowne: brak drogi powrotu po pomyłce. Wynik zawiera ostrzeżenie.
- **`--allow-missing-sidecar`**: restore bez pliku pomocniczego. Tożsamość serwera jest wtedy sprawdzana tylko z silnikiem celu, a wynik ma ostrzeżenie.
- **`--allow-machine-mismatch`**: zgoda na kopię z innej maszyny (np. kontener, tunel). Bez tej flagi odmowa.
- **Kopie poza repozytorium.** Katalog nie może leżeć w drzewie z `.git` (także w worktree). Domyślny katalog to
  Linux `$XDG_STATE_HOME/db-copies` lub `~/.local/state/db-copies`, macOS `~/Library/Application Support/db-copies`,
  Windows `%LOCALAPPDATA%\db-copies`. Ścieżki: tylko ASCII, bezwzględne, bez `..`, bez znaków spoza `[A-Za-z0-9 _.\-\\/:]`.
  Domyślny katalog z apostrofem w nazwie użytkownika jest odrzucany; wtedy podaj `--dir`.
- **Kopie zawierają dane osobowe i nie są szyfrowane.** Trzymaj je poza repozytorium; narzędzie ich nie usuwa.
- **Sidecar jest danymi niezaufanymi**: limit 64 KB, ścisły schemat, żadne ścieżki z niego nie są czytane.

## Kolejność kontroli restore

1. Walidacja (bez zapytań do silnika): nazwa, host lokalny, `--confirm`, katalog poza repozytorium, plik w katalogu kopii, istnienie pliku, sidecar (zgodna baza, plik, serwer, rozmiar).
2. Stan bazy (`sys.databases`): baza musi istnieć i być `ONLINE`. Brak bazy = odmowa (restore nie odtwarza skasowanej bazy); inny stan = odmowa z instrukcją. Tryb `FULL` = ostrzeżenie.
3. Tożsamość silnika: nazwa maszyny z silnika musi odpowiadać tej maszynie (bez wielkości liter, do 15 znaków).
4. `RESTORE HEADERONLY`: dokładnie jeden zestaw, `BackupType = 1`, nazwa bazy bez wielkości liter, zgodność `ServerName`, `MachineName`, GUID i LSN z sidecarem (bez sidecara: z silnikiem celu).
5. `RESTORE VERIFYONLY … WITH CHECKSUM, FILE = 1`: błąd = kod 1 (patrz uwaga w sekcji kodów).
6. Kopia bezpieczeństwa z własnym `VERIFYONLY` (chyba że `--no-safety-backup`). Jej błąd zatrzymuje cofnięcie.
7. Ścieżka kopii bezpieczeństwa wypisana na stderr, potem krok destrukcyjny: jeden batch `sqlcmd -b`:
   `BEGIN TRY` → `ALTER DATABASE … SET SINGLE_USER WITH ROLLBACK IMMEDIATE` → `RESTORE DATABASE … WITH REPLACE, FILE = 1, CHECKSUM` → `ALTER DATABASE … SET MULTI_USER` → `END TRY`; `BEGIN CATCH` próbuje `MULTI_USER` i rzuca `THROW`.
8. Stan po: baza musi być `ONLINE` i `MULTI_USER`. Inaczej kod 1 z odczytanym stanem i drogą powrotu.

Przy timeoucie albo przerwaniu klienta narzędzie **nie** wykonuje ślepo `MULTI_USER`: odczytuje stan (krótki timeout 30 s) i kończy kodem 1.

## Ścieżka widziana przez serwer

Ścieżka kopii jest ścieżką **serwera**, nie klienta. Dla lokalnej instancji na tej samej maszynie to ten sam katalog.
Serwer w kontenerze, pod innym kontem (np. konto usługi SQL) albo z innym widokiem systemu plików nie zobaczy katalogu domowego klienta.
Katalog tworzony jest z trybem `0700`, więc takie konto go nie odczyta. Wtedy przygotuj katalog dostępny dla procesu serwera
i podaj go w `--dir`. Brak pliku po `backup` daje kod 1, a nie ciche powodzenie.

## Granice

- Nie odtwarza skasowanej bazy (wymagałoby `MOVE` i ścieżek plików): odmowa kodem 2 z instrukcją ręczną.
- Obsługuje tylko pojedynczy pełny zestaw kopii. Plik z wieloma zestawami to odmowa przy restore i błąd przy backup.
- `RESTORE … WITH REPLACE` w trybie `FULL` przerywa łańcuch kopii dziennika. Ostrzeżenie w wyniku; decyzja należy do administratora.
- `CHECKSUM` wymaga kopii z sumami kontrolnymi. Kopia bez sum (obca) daje kod 1 przed zmianą.
- `VERIFYONLY` nie dowodzi, że `RESTORE` uda się na innej wersji silnika.
- Brak retencji: narzędzie nie usuwa `.bak`, nie planuje kopii, nie robi kopii innych silników ani plików poza bazą.
- `--no-verify` pomija `VERIFYONLY` przy backup; wynik ma `verify: skipped`.

## Mapowanie z lokalnych skryptów projektowych

Skrypty projektowe (PowerShell) przepinasz na narzędzie po wydaniu. Parametry mapują się tak:

| Skrypt projektowy | db_snapshot.py |
|---|---|
| `-Label <etykieta>` | `--label <etykieta>` (tylko `[a-z0-9-]`, do 40 znaków) |
| `-Restore <plik> -Force` | `restore --file <plik> --confirm <baza>` (bez `--confirm` odmowa) |
| `-WhatIf` | `--dry-run` (kod 3, nic nie robi) |

Różnice: `--confirm` musi być nazwą bazy, a nie samym `-Force`. Kopia bezpieczeństwa domyślnie.
Kod wyjścia 1 w tym narzędziu to „stan mógł się zmienić”, nie „wykryta różnica”.

## Informacja dla koordynatora

Wynik `backup` i `restore` zawiera pole `coordinator_notice`: po polsku, z bazą, serwerem, etykietą, ścieżką,
rozmiarem, czasem UTC, poleceniem cofnięcia (bez sekretów) i ostrzeżeniami: baza testowa jest współdzielonym
stanem, kopia może zawierać dane osobowe i nie jest szyfrowana. Wklej ten blok koordynatorowi środowiska
testowego przed zapisem i przed cofnięciem. Flaga `--json` zwraca te same pola maszynowo.

Polecenie cofnięcia w bloku uruchamiaj z katalogu skryptu albo z pełną ścieżką do `db_snapshot.py`.

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
pozycje NOT_RUN do czasu wykonania testu na prawdziwym SQL Serverze i zgody):

1. [ ] Zachowanie `sqlcmd -Q` z `-x` i z `-X`: czy polecenia `sqlcmd` (`:r`, `:!!`) w ścieżce lub zapytaniu są wykonywane; wybór flagi.
2. [ ] Czy `-X` wyłącza odczyt `SQLCMDPASSWORD` (decyzja o fladze).
3. [ ] Dokładny format `RESTORE HEADERONLY` (nazwy i kolejność kolumn) w użytej wersji silnika; prawdziwe wzorce do fixtur.
4. [ ] Stan bazy po przerwanym `RESTORE` (timeout, zabicie klienta) i po porażce środkowego kroku batcha z `TRY/CATCH`.
5. [ ] Zachowanie batcha po błędzie poziomu 16 przy `ALTER DATABASE … SINGLE_USER` i `-b` (czy batch kontynuuje).
6. [ ] Uprawnienia konta usługi do katalogu kopii (LocalDB, pełna instancja, Linux, kontener).
7. [ ] Treść komunikatów błędów logowania (czy zawierają login) i `-C` na Linuksie.

Do czasu PASS punktów 3, 4 i 5 ten przepis i rekord domenowy `db-snapshot-odmowy-i-tozsamosc.md` są
**niezweryfikowane na silniku**. Raport testu na żywo podaje wersję `sqlcmd` i silnika, maszynę i wynik każdego punktu.
