# Migawka bazy testowej przed zapisem (db_snapshot)

> **Status weryfikacji na silniku: zweryfikowane na SQL Server LocalDB 2019 (Windows, sqlcmd 15.0, ODBC 17, uwierzytelnianie zintegrowane); niezweryfikowane: pełne instancje, Linux/kontener (konto serwera, katalog kopii), go-sqlcmd, tryb `--auth env` z logowaniem SQL, zdalny backup, `-C`, stabilność tożsamości LocalDB po restarcie instancji.**
> Wyniki testu na żywo (S7) i wnioski są w sekcji „Status weryfikacji na silniku” na końcu przepisu.
> Logika, kolejność wywołań, odmowy i maskowanie są dodatkowo sprawdzone testami z atrapą `sqlcmd`.
> `restore --move existing|new`: fakty o silniku z sond S1 (LocalDB 2019) są w sekcji „Sondy S1”; test nowego kodu na żywo (K8(b)) wykonano 23.11.2026 na LocalDB 2019 (wszystkie scenariusze PASS, sekcja „K8(b)” niżej), a wymienione tam granice (NOT_RUN) są niezweryfikowane.
> `prune` nie dotyka silnika i jest sprawdzony wyłącznie testami (system plików w pamięci i prawdziwy katalog tymczasowy); niezweryfikowane: Windows (blokady plików), kopie utworzone przez konto usługi SQL, uprawnienia do ich usunięcia.

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

Przykład ogólny dla LocalDB na Windows (baza zawsze jawnie; nazwy to przykład, preset projektu — serwer, baza, etykiety — zostaje w dokumentacji projektu):

```
python -I db_snapshot.py backup --server "(localdb)\MSSQLLocalDB" --database AppDb --label pre-migration --sha256
python -I db_snapshot.py restore --server "(localdb)\MSSQLLocalDB" --database AppDb --confirm AppDb --file <plik.bak>
```

`--sha256` zapisuje skrót kopii w pliku pomocniczym; bez niego `restore` ostrzega `no sha256 recorded in the sidecar` i nie ma czym potwierdzić integralności pliku poza kontrolą nagłówka. Kopię bez pliku pomocniczego (np. zrobioną innym narzędziem) przywraca się z `--allow-missing-sidecar` i `--dir <katalog kopii>`.

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
- Baza docelowa: dla `--move none` (domyślnie) i `--move existing` musi istnieć i być `ONLINE`; dla `--move new` nie może istnieć
  (patrz „restore --move”). Bez `--move` restore używa ścieżek plików zapisanych w kopii.
- Przed krokiem destrukcyjnym powstaje kopia bezpieczeństwa `<baza>-pre-restore-…bak` (ta sama nazwa i sidecar jak przy
  `backup`, z własnym `VERIFYONLY`). Pomija ją tylko `--no-safety-backup`. Jej ścieżka jest wypisana na stderr jako
  `safety backup: <ścieżka>` przed przywracaniem, a w wyniku jako `safety_backup_path`; `rollback_command` to polecenie powrotu.

Wynik zawiera `previous_state` (stan sprzed przywrócenia; przy `--move new` `null`, bo bazy nie było), `warnings`, `restored_utc`, `rollback_command`
i `coordinator_notice`. Stan `FULL` w trybie odzyskiwania daje ostrzeżenie: `RESTORE … WITH REPLACE` przerywa łańcuch
kopii dziennika, a decyzja należy do administratora.

Podgląd bez zmian: dodaj `--dry-run` (też wtedy wymagane jest `--confirm <baza>`; kod 3, wypisany plan, zero wywołań `sqlcmd`, brak plików i katalogów).

Powrót do stanu sprzed `restore`: uruchom polecenie `rollback_command` z wyniku (przywraca kopię bezpieczeństwa).
Polecenie zaczyna się od nazwy skryptu, więc uruchamiaj je tak jak `restore_command` (z `python3`).

### Kolejność kontroli restore

1. Walidacja bez zapytań do silnika: nazwa, host lokalny, `--confirm`, katalog poza repozytorium, plik w katalogu kopii,
   zwykły plik (nie dowiązanie), sidecar (baza, plik, serwer, rozmiar, obecność lub `--allow-missing-sidecar`), sha256 z sidecara.
2. Stan bazy (`sys.databases`, z `-d master`): baza istnieje i jest `ONLINE`. Brak bazy albo inny stan = odmowa
   (tryb `--move new` odwrotnie: baza nie może istnieć). Tryb `FULL` = ostrzeżenie.
3. Tożsamość silnika: `ServerName` i `MachineName` z nagłówka kopii oraz z sidecara muszą odpowiadać tożsamości silnika celu, a
   `MachineName` silnika także nazwie tej maszyny (bez wielkości liter; obcięcie do 15 znaków tylko wtedy, gdy któraś
   nazwa ma dokładnie 15 znaków, jak nazwa NetBIOS). Rozbieżność = odmowa, chyba że `--allow-machine-mismatch`.
4. `RESTORE HEADERONLY` i sprawdzenie nagłówka (patrz wyżej). Błąd odczytu nagłówka przed zmianą = kod 2.
5. `RESTORE VERIFYONLY … WITH CHECKSUM, FILE = 1` (limit `--restore-timeout`). Błąd = kod 2, baza nietknięta.
6. Sprawdzenie `sha256` z sidecara, jeśli jest (niezgodność = kod 2).
6c. Tylko `--move existing` i `--move new`: odczyt listy plików kopii i budowa `MOVE` (patrz „restore --move”); każda odmowa jest przed krokiem 7.
7. Kopia bezpieczeństwa z własnym `VERIFYONLY` (chyba że `--no-safety-backup`). Jej błąd zatrzymuje cofnięcie; po rozpoczęciu jej
   `BACKUP` kod to 1, a błąd przed tym (drugi odczyt tożsamości, istniejący plik, katalog) to 2.
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
RESTORE DATABASE [AppDb] FROM DISK = N'/srv/h/db-copies/AppDb-pre-restore-….bak' WITH REPLACE, RECOVERY, FILE = 1
```

Wariant `FILE = 1` potwierdzono na SQL Server LocalDB 2019 (test S7, 22.11.2026); wersja bez `FILE = 1` nie była sprawdzona.

Wykonaj ją na serwerze z kontem, które ma uprawnienia do `RESTORE` tej bazy. Nie zakładaj, że baza jest sprawna, dopóki nie odczytasz stanu.

**Podpowiedź `SET MULTI_USER` (dotyczy też trybu `none`).** Batch w bloku `CATCH` próbuje przywrócić `MULTI_USER`, ale błąd tej próby jest
połykany, więc narzędzie **nie gwarantuje** powrotu do `MULTI_USER`: po porażce odczytuje stan i go raportuje (`user_access_desc`). Gdy odczytany stan to
`ONLINE`, a `user_access_desc` jest inny niż `MULTI_USER` (np. `SINGLE_USER`), komunikat podaje **najpierw** najtańszą drogę
`ALTER DATABASE [AppDb] SET MULTI_USER` (ręcznie, narzędzie go nie uruchamia), a dopiero potem restore z kopii bezpieczeństwa (jeśli powstała).
Dla pozostałych stanów (`ONLINE` i `MULTI_USER`, `RESTORING`, nieznany) teksty drogi powrotu są takie jak opisano wyżej.

### restore --move: inna ścieżka plików, nowa baza

`--move none|existing|new` (domyślnie `none`) wybiera, skąd restore bierze ścieżki plików bazy. `none` działa tak jak opisano wyżej: te same kontrole,
ten sam batch, **bez** `RESTORE FILELISTONLY` i bez odczytu `sys.master_files`. Pozostałe tryby powstają tylko po jawnym wyborze.

| Tryb | Baza docelowa | Kopia bezpieczeństwa | `REPLACE` i `SINGLE_USER` | `--move-dir` |
|---|---|---|---|---|
| `none` | musi istnieć i być `ONLINE` | tak | tak, ścieżki z kopii | odmowa (kod 2) |
| `existing` | musi istnieć i być `ONLINE` | tak | tak, `MOVE` na obecne pliki **tej** bazy | odmowa (kod 2) |
| `new` | **nie może istnieć** (istnieje = odmowa kodem 2) | nie ma (brak czego kopiować); `--no-safety-backup` = odmowa | **nie** (bez `REPLACE`, bez `ALTER`) | opcjonalny |

`--confirm` (równe nazwie bazy) jest wymagane w każdym trybie; `new` akceptuje te same flagi połączenia i wyjścia co reszta, `--timeout` ogranicza odczyty `FILELISTONLY` i `HEADERONLY` (min(`--timeout`, 120 s); kopii bezpieczeństwa nie ma). Nazwa bazy w kopii musi być taka jak `--database`: zmiany nazwy przy odtwarzaniu nie ma.

**`--move existing`.** Po `HEADERONLY` i `VERIFYONLY` narzędzie czyta `RESTORE FILELISTONLY` kopii i pliki bazy z `sys.master_files` (po `database_id`), wiąże je 1:1
po nazwie logicznej (bez względu na wielkość liter) i typie (`D` ↔ `ROWS`, `L` ↔ `LOG`) i buduje `RESTORE … WITH REPLACE, MOVE <nazwa logiczna> TO <obecna ścieżka>, …, FILE = 1, CHECKSUM`
w tej samej strukturze `TRY/CATCH` co `none`. `MOVE` jest emitowany dla każdego pliku, w kolejności rosnącego `FileId` z kopii; ścieżki z kopii nie są używane. Kolejność wierszy
`FILELISTONLY` ani `sys.master_files` nie ma znaczenia. Niezgodne nazwy lub typy, plik innego rodzaju niż dane i log, albo ścieżka spoza whitelisty = odmowa (kod 2).
Pliki bazy są czytane **ponownie** tuż przed krokiem niszczącym (kopia bezpieczeństwa trwa): zmiana układu po kopii bezpieczeństwa = **kod 1** (komunikat ze ścieżką kopii bezpieczeństwa
i drogą powrotu; kopia już powstała); z `--no-safety-backup` nic jeszcze nie zapisano, więc to kod 2.

**`--move new`.** Warunki: bazy nie ma, kopia przeszła wszystkie kontrole z tabeli „Kolejność kontroli restore”, a lista plików przeszła walidację. Wykonuje jeden
`RESTORE DATABASE … WITH MOVE …, FILE = 1, CHECKSUM` (bez `REPLACE`: silnik sam odmówi nadpisania istniejących plików w użyciu; niezweryfikowane, fakt 5). Katalog docelowy:
`--move-dir` albo, bez niego, domyślne ścieżki silnika (`InstanceDefaultDataPath` i `InstanceDefaultLogPath`; wartość pusta lub `NULL` = odmowa z podpowiedzią `--move-dir`).
Silnik zwraca te ścieżki z końcowym separatorem; narzędzie obcina jeden separator przed walidacją (korzenia dysku, np. `D:\`, nie obcina).

**`--move-dir`.** Tylko z `--move new`. Katalog musi **już istnieć**: narzędzie go nie tworzy. Sprawdzenie `isdir` po stronie klienta jest tylko ostrzeżeniem (serwer może widzieć inny system
plików, np. kontener); o istnieniu decyduje silnik, a jego błąd to kod 1. Katalog musi leżeć poza repozytorium i składać się wyłącznie ze znaków whitelisty ścieżek (ASCII: litery, cyfry,
spacja i `_ . - \ / :`; bez `..`, bez końcowej kropki i spacji, bez nazw urządzeń). Znak spoza whitelisty, w tym nawiasy (`Program Files (x86)`), tyldy, przecinki i polskie litery, daje odmowę z podpowiedzią
`--move-dir`. Dotyczy to także ścieżek zwróconych przez silnik: gdy profil użytkownika ma znaki spoza whitelisty, domyślne ścieżki dla `new` i `--move existing` są odmawiane, a wyjściem jest
`--move new --move-dir <katalog ze znaków whitelisty>` (nadpisanie istniejącej bazy z przeniesieniem plików nie jest wspierane: usuń bazę ręcznie).

**Nazwy plików nowej bazy** nie zawierają tekstu z kopii: `<baza>_<numer>_<6 hex>.<mdf|ndf|ldf>`. `<numer>` (1, 2, …) nadaje narzędzie po wszystkich plikach w kolejności rosnącego `FileId` z kopii,
`<6 hex>` jest jeden na przebieg. Plik danych o najniższym `FileId` to `mdf`, kolejne dane `ndf`, logi `ldf`. `FileId` z kopii musi być liczbą 1–5 cyfr, unikalną numerycznie
(`007` i `7` to duplikat). Nazwa logiczna z kopii (tylko do klauzuli `MOVE`): `A-Z a-z 0-9 _ . $ # @ -` i spacje wewnątrz, 1–128 znaków, bez spacji brzegowych, bez duplikatów po złożeniu wielkości liter.
Typy inne niż dane i log (FILESTREAM, pamięć operacyjna), brak plików danych albo ponad 64 pliki = odmowa (kod 2).

**Pochodzenie kopii (ważne przy `new`).** Utworzenie bazy z kopii nie jest sprawdzeniem jej zawartości: kopia może nieść obiekty CLR i ustawienia `TRUSTWORTHY`. Wynik zawiera
ostrzeżenie o tym. Kopia z innej maszyny wymaga `--allow-machine-mismatch`, a przy innej wartości `--server` niż w sidecarze także `--allow-missing-sidecar` (osobna odmowa „the backup comes from a
different server”); te flagi zdejmują kontrolę tożsamości kopii, więc w tym scenariuszu pochodzenie potwierdza tylko nagłówek (i sidecar, jeśli jest). Zalecenie: rób `backup` z `--sha256` i przepisuj kopię z sidecarem.

**Tryb dostępu nowej bazy.** Nowa baza dziedziczy tryb dostępu z kopii. Gdy po utworzeniu nie jest `MULTI_USER`, to nie jest porażka: wynik ma ostrzeżenie `the new database is in access mode <tryb> (taken over from the copy)`; zmiana trybu to decyzja użytkownika (`ALTER DATABASE … SET MULTI_USER` ręcznie).

**Wynik `new`.** `created: true`, `undo_note` i `coordinator_notice` bez kopii bezpieczeństwa (`safety_backup_path`, `previous_state` i `rollback_command` mają wartość `null`). `undo_note` to ręczne
`DROP DATABASE [AppDb]` (dla bazy `ONLINE` z połączeniami najpierw `ALTER DATABASE [AppDb] SET SINGLE_USER WITH ROLLBACK IMMEDIATE`) i katalogi plików; **narzędzie go nie uruchamia i niczego nie usuwa**.

**Kolejność i kody.** Wszystko nowe (lista plików kopii, odczyt `sys.master_files`, domyślne ścieżki silnika, budowa `MOVE`) dzieje się przed kopią bezpieczeństwa i krokiem niszczącym, więc odmowa
to kod 2. Kod 1 oznacza porażkę po rozpoczęciu zmiany; jedyny wyjątek od „kod 2 przed zmianą” opisuje `existing` wyżej (różnica układu plików po kopii bezpieczeństwa = 1).
Porażka `new` po rozpoczęciu `RESTORE`: kod 1, narzędzie odczytuje stan i rozróżnia „odczyt się udał i bazy nie ma” od „odczyt nie wyszedł (stan nieznany)”; komunikat mówi, że baza mogła
zostać utworzona (stan: …), podaje `DROP` dla stanu `RESTORING` lub innego i katalogi plików.

**`--dry-run`** zachowuje zero wywołań `sqlcmd`: plan wymienia kroki (odczyt układu plików, porównanie, budowa `MOVE`), ale nie zawiera listy `MOVE` (ta wymaga silnika).

Przykłady:

```
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb --dir /srv/h/db-copies --move existing
python3 db_snapshot.py restore --file /srv/h/db-copies/AppDb-pre-migration-20261122-102030-a1b2c3.bak --database AppDb --confirm AppDb --dir /srv/h/db-copies --move new --move-dir /srv/h/db-files
```

### prune: retencja kopii

```
python3 db_snapshot.py prune --database AppDb --confirm AppDb --dir /srv/h/db-copies --keep 5
```

Usuwa stare kopie **tylko z lokalnego katalogu** i **bez kontaktu z silnikiem** (nie używa `sqlcmd`, `--server` ani `--auth`). `backup` i
`restore` niczego nie usuwają; usuwanie to zawsze świadoma decyzja z `--confirm` (nazwa bazy), także z `--dry-run`.

**Co jest „nasze” (jedyne, co wolno usunąć).** Plik bezpośrednio w katalogu (bez rekursji; ponad 10 000 wpisów = odmowa), który jest zwykłym plikiem
(nie dowiązaniem ani katalogiem), ma nazwę zgodną ze wzorcem `<baza>-<etykieta>-<RRRRMMDD>-<GGMMSS>-<6 hex>.bak` i **lokalny** sidecar
(`<plik>.bak.json`, też zwykły plik, `remote: false`), zgodny z nazwą: baza (bez względu na wielkość liter), etykieta, `file`, `size` równy rozmiarowi pliku
i `created_utc` w formacie `RRRR-MM-DDTGG:MM:SSZ` równy znacznikowi z nazwy (data musi istnieć w kalendarzu). Wszystko inne (obce pliki,
`.bak` bez sidecara, sidecar innej bazy, sidecary zdalnych kopii, `.tmp`, dowiązania, katalogi) jest **pomijane z powodem i nigdy nie usuwane**.

**Reguła.** Grupa to (baza bez względu na wielkość liter, etykieta). W grupie zostaje N najnowszych wg `created_utc` (remis rozstrzyga nazwa pliku):
`--keep N` (1–1000), domyślnie 5, a dla etykiety `pre-restore` 3; jawny `--keep` nadpisuje domyślne. Reszta jest usuwana, a przy `--older-than-days D`
(1–36500) tylko te, które są jednocześnie poza N najnowszymi **i** starsze niż D dni (liczone od `max(created_utc, mtime pliku)`). Znacznik `created_utc`
z przyszłości (ponad 5 minut) = pominięcie. **Twarda blokada:** nic, czego `max(created_utc, mtime)` jest młodszy niż 1 godzina (kopia mogła być
właśnie zapisywana). `--label` zawęża do jednej etykiety; bez niego plan obejmuje wszystkie etykiety bazy, także nazwane ręcznie (`--dry-run` ostrzega).

**Usuwanie.** Trwałe (`unlink`, nie do kosza; usuwa plik z katalogu i zwalnia miejsce, nie wymazuje zawartości z nośnika). Kolejność: `.bak`, potem sidecar
(przerwanie zostawia co najwyżej sidecar-sierotę, którą następny `prune` usunie po godzinie; sierota to lokalny, poprawny sidecar, dla którego w katalogu nie
ma **żadnego** wpisu `X.bak`, także dowiązania, katalogu ani `X.BAK`). Tuż przed usunięciem każdego pliku ponowne `lstat`: zmiana rozmiaru, czasu lub
rodzaju = pominięcie (`changed-since-planned`). Błąd jednego pliku (np. brak uprawnień do kopii utworzonej przez konto usługi SQL) nie przerywa reszty, a sidecar
kopii, której `.bak` nie udało się usunąć, zostaje. Zniknięcie `.bak` w trakcie (równoległy `prune`) nie jest porażką.

**Wynik** (tekst: pierwsza linia to status; `--json`): `status`, `database`, `dir`, `label`, `older_than_days`, `deleted` (nazwy usuniętych plików, także sidecarów),
`kept` i `skipped` (elementy `{file, reason}`; `skipped` ograniczone do 50, resztę podsumowuje `skipped_total`), `failed` (`{file, reason}`, tekst błędu systemu bez ścieżek),
`dry_run`. `--dry-run` zamiast usuwać wypisuje `would_delete` i `warnings` (kod 3).

| Kod `prune` | Znaczenie |
|---|---|
| 0 PASS | Plan wykonany (pusta lista usunięć też jest PASS). |
| 1 FAIL | Co najmniej jeden plik **usunięto**, a potem usunięcie się nie udało albo wyjątek lub przerwanie zatrzymały przebieg. Stderr: `pliki kopii zostały częściowo usunięte` (nie ma tu linii o stanie bazy). |
| 2 BLOCKED | Odmowa przed pierwszym usunięciem (nazwa, `--confirm`, katalog, limit wpisów, liczby poza zakresem) **albo** nic nie usunięto mimo błędów usuwania (stderr: `żaden plik kopii nie został usunięty`; przy wyjątku lub przerwaniu przed pierwszym usunięciem stderr ma tylko `<Typ wyjątku>: komunikat`). Dla `prune` kod 2 znaczy „katalog bez zmian”. |
| 3 NOT_RUN | `--dry-run`. |

**Ryzyko rezydualne.** `prune` i równoległy `restore` starej kopii: blokada 1 h nie chroni kopii starszej niż godzina przed usunięciem między sprawdzeniem
a `VERIFYONLY`; nie uruchamiaj `prune` równolegle z `backup` ani `restore`. Grupowanie bez względu na wielkość liter scala `App` i `app` (na serwerze
rozróżniającym wielkość liter to różne bazy: retencja jednej wpływa na drugą), a grupa (baza, etykieta) **nie rozróżnia serwerów**: dwie instancje lokalne z tą samą nazwą bazy i etykietą dzielą N. Przeniesienie `.bak` do archiwum zostawia jego sidecar, który po godzinie `prune` usunie jako sierotę (a sidecar bywa potrzebny do `restore`): przenoś oba pliki. Sierota nie jest sprawdzana ponownie tuż przed usunięciem (okno planu); przerwanie dokładnie między `unlink` a zapisem wyniku może zgubić informację o usunięciu. Kopie **zdalne** (sidecar w katalogu stanu, `.bak` na serwerze) nie są usuwane.

## Timeouty

- `--timeout <s>` (backup i restore, domyślnie 600 s): każde wywołanie zapisujące kopię (`BACKUP`, kopia bezpieczeństwa)
  i `VERIFYONLY` przy `backup`. Odczyt nagłówka (`HEADERONLY`) ma limit min(`--timeout`, 120 s).
- `--restore-timeout <s>` (restore, domyślnie 3600 s): `VERIFYONLY` przywracanej kopii i batch destrukcyjny.
- Odczyty tożsamości i stanu mają limit 30 s (krótszy z limitów).
- Po przekroczeniu czasu (`TimeoutExpired`) albo przerwaniu klienta PO rozpoczęciu zmiany stan bazy jest niepewny, nie wynik (przekroczenie czasu odczytu przed zmianą daje 2). Narzędzie kończy kodem 1
  z odczytanym stanem. Przerwanie w trakcie odczytu stanu (drugi Ctrl-C) daje stan `unknown`; komunikat zawiera ścieżkę
  kopii bezpieczeństwa.

## Kody wyjścia

| Kod | Status | Znaczenie |
|---|---|---|
| 0 | PASS | Operacja wykonana i sprawdzona. |
| 1 | FAIL | Operacja **rozpoczęła zmianę** (pierwsze wywołanie `BACKUP`, `ALTER` lub `RESTORE`) i się nie udała; **stan bazy mógł się zmienić**. Stderr zawiera linię `stan bazy mógł się zmienić`. |
| 2 | BLOCKED | **Baza nietknięta** (przy `restore --move`: także odmowy listy plików, ścieżek i `--move-dir`; jedyny wyjątek to różnica układu plików po kopii bezpieczeństwa w `existing`, kod 1). Odmowa (walidacja, host, `--confirm`, plik, sidecar, tożsamość, nagłówek, sha256, stan bazy) albo błąd wykonania **przed** pierwszym wywołaniem zmieniającym (odczyt stanu, tożsamości lub nagłówka, `VERIFYONLY` kopii przed przywróceniem). Także błąd składni argumentów. |
| 3 | NOT_RUN | `--dry-run` albo brak `sqlcmd` w `PATH`. |

Reguła jest jedna: kod 1 tylko po rozpoczęciu zmiany, kod 2 przed nią. Porażka `BACKUP` (plik kopii mógł powstać) to kod 1. To dotyczy `backup` i `restore`; `prune` zmienia pliki, nie bazę, i ma własną tabelę kodów w sekcji „prune”.

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
- **Kopie zawierają dane osobowe i nie są szyfrowane.** Trzymaj je poza repozytorium; `backup` i `restore` ich nie usuwają, a stare kopie usuwa ręcznie wywołany `prune` (z `--confirm`).
- **Sidecar jest danymi niezaufanymi**: limit 64 KB, ścisły schemat (nieznane klucze = odmowa, duplikat klucza = odmowa),
  żadne ścieżki z niego nie są czytane. Pole `sha256` musi mieć postać 64 znaków hex.
- **Plik kopii nie może być dowiązaniem symbolicznym** (`realpath` pliku musi wskazywać ten plik w katalogu kopii).

## Ścieżka widziana przez serwer

Ścieżka kopii jest ścieżką **serwera**, nie klienta. Dla lokalnej instancji na tej samej maszynie to ten sam katalog.
Serwer w kontenerze, pod innym kontem (np. konto usługi SQL) albo z innym widokiem systemu plików nie zobaczy katalogu domowego klienta.
Katalog tworzony jest z trybem `0700`, więc takie konto go nie odczyta. Wtedy przygotuj katalog dostępny dla procesu serwera
i podaj go w `--dir`. Brak pliku po `backup` daje kod 1, a nie ciche powodzenie.

## Granice

- Nową bazę (której nie ma) tworzy tylko jawne `restore --move new`; bez `--move` restore wymaga istniejącej bazy i używa ścieżek z kopii. Nazwy bazy przy odtwarzaniu nie zmienia.
- Pliki FILESTREAM, pamięci operacyjnej i inne niż dane i log nie są obsługiwane w trybach `existing` i `new`.
- Obsługuje tylko pojedynczy pełny zestaw kopii. Plik z wieloma zestawami to odmowa przy restore i błąd przy backup.
- `RESTORE … WITH REPLACE` w trybie `FULL` przerywa łańcuch kopii dziennika. Ostrzeżenie w wyniku; decyzja należy do administratora.
- `CHECKSUM` wymaga kopii z sumami kontrolnymi. Kopia bez sum (obca) daje błąd `VERIFYONLY` przed zmianą, czyli kod 2.
- `VERIFYONLY` nie dowodzi, że `RESTORE` uda się na innej wersji silnika.
- Retencja tylko ręczna (`prune`): bez harmonogramu, tylko lokalne kopie tego narzędzia; narzędzie nie robi kopii innych silników ani plików poza bazą.
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

Test na żywo (S7), 22.11.2026. Środowisko: SQL Server LocalDB 2019 (15.0.4382.1) pod Windows, `sqlcmd` 15.0 z ODBC Driver 17,
uwierzytelnianie zintegrowane (`--auth integrated`). Testowano tylko świeże bazy testowe, kopie w katalogu użytkownika.
Odbiór przewidywał `sqlcmd` 18 albo go-sqlcmd; ten test wykonano na 15.0, więc te wersje pozostają niezweryfikowane.

| # | Punkt | Wynik | Opis |
|---|---|---|---|
| 1 | `-x` i `-X` a polecenia `sqlcmd` (`:!!`, `:r`) | PASS (narzędzie); INFO (sqlcmd) | Pod `-X` sqlcmd wypisuje błąd „ED and !!<command> commands, startup script, and environment variables are disabled”. Pod `-x` próba `:!! echo` w `-Q` nie dała wyjścia polecenia (rc 0), więc zachowanie `-x` jest niejednoznaczne. Narzędzie nie polega na `-x`: zapytanie to jedna linia zbudowana z zwalidowanych identyfikatorów i ścieżek (znaki sterujące i nowa linia odrzucone), więc `:` poleceń sqlcmd nie da się wstrzyknąć. |
| 2 | Czy `-X` wyłącza odczyt `SQLCMDPASSWORD` | INFO (potwierdzone) | Pod `-X` sqlcmd pyta o hasło z konsoli mimo zmiennej; bez `-X` użył zmiennej. Dlatego narzędzie nie używa `-X`. |
| 3 | Format `RESTORE HEADERONLY` w `-W -s "\|" -w 65535` | PASS | Jeden wiersz, 56 kolumn, wszystkie wymagane kolumny obecne, linia `(1 rows affected)` obecna, `BackupType` = 1. Ten sam parser radzi sobie też bez `-w` na tej wersji, więc `-w 65535` jest zabezpieczeniem, nie wymogiem. |
| 4a | Stan bazy po przerwanym `RESTORE` | INFO (zachowanie sqlcmd) | Baza ok. 480 MB; klient zabity po 0,4 s w batchu destrukcyjnym (pełny restore trwa ok. 1,3 s). Baza zostaje `RESTORING` z `SINGLE_USER` i nie wraca sama (sprawdzone po 2 s i po 8 s). |
| 4b | Ręczny powrót z kopii bezpieczeństwa | PASS | `RESTORE DATABASE … FROM DISK = N'<kopia>' WITH REPLACE, RECOVERY, FILE = 1` przywraca `ONLINE` i `MULTI_USER`. Potwierdza, że narzędzie słusznie nie wykonuje ślepo `MULTI_USER`. |
| 4c | Porażka środkowego kroku batcha z `TRY/CATCH` (brak pliku kopii; błędy 3201 i 3013) | PASS | `sqlcmd -b` zwraca kod 1, na wyjściu są oba komunikaty, baza po porażce jest `ONLINE` i `MULTI_USER`. |
| 5 | Batch z `TRY/CATCH/THROW` kompiluje się i działa jak opisano | PASS | Blok `CATCH` z `THROW` kompiluje się i działa. Błąd poziomu 16 przy `ALTER … SINGLE_USER` nie był osobnym przypadkiem testu (zob. wnioski). |
| 6 | Uprawnienia do katalogu kopii | PASS (LocalDB, konto użytkownika); NOT_RUN (pełna instancja, Linux, kontener) | LocalDB zapisuje i odczytuje kopie w katalogu użytkownika. |
| 7a | Treść komunikatu błędu logowania | PASS | Komunikat zawiera nazwę logowania (`Login failed for user '<login>'`); hasła nie wypisano. |
| 7b | `-C` na Linuksie | NOT_RUN | Test nie działał na Linuksie. |
| 7c | Stabilność tożsamości LocalDB po restarcie instancji | NOT_RUN | Instancji nie wolno było zatrzymywać. |
| — | Odbiór: backup → zmiana → restore przez narzędzie | PASS | Stan wrócił do pierwotnego, kopia bezpieczeństwa powstała, baza `ONLINE` i `MULTI_USER`; polecenia cofnięcia z `--server`/`--dir` są w wyniku. |

Sprzątanie: bazy testowe usunięte; po teście brak baz testowych i procesów `sqlcmd`; katalog testowy usunięty.

### Wnioski dla użytkownika

- **Nie używaj `-X` z `--auth env`.** Pod `-X` sqlcmd nie czyta `SQLCMDPASSWORD` i pyta o hasło z konsoli. Narzędzie `-X` nie używa; nie dopisuj go ręcznie.
- **Po przerwaniu `restore` baza zostaje `RESTORING` z `SINGLE_USER`** i nie wraca sama. Narzędzie tego nie naprawi (odmawia `restore` z innego stanu niż `ONLINE`). Przywróć ją ręcznie z kopii bezpieczeństwa (ścieżka z komunikatu albo wiersz `safety backup:` na stderr), na serwerze kontem z uprawnieniem `RESTORE`:

  ```
  RESTORE DATABASE [AppDb] FROM DISK = N'<katalog kopii>\AppDb-pre-restore-….bak' WITH REPLACE, RECOVERY, FILE = 1
  ```

  Domyślny katalog kopii na Windows to `%LOCALAPPDATA%\db-copies`. Potem odczytaj `state_desc` i `user_access_desc` bazy; oczekiwane wartości to `ONLINE` i `MULTI_USER`.
- **`-w 65535` zostaje.** Szeroki tryb tabelaryczny chroni parser `HEADERONLY` przed zawinięciem wiersza, choć na tej wersji parser działa też bez niego.
- **Błąd poziomu 16 przy `ALTER … SINGLE_USER` z `-b`** pozostaje do osobnego sprawdzenia (punkt 5 z poprzedniej listy nie był testowany osobno).
- Zmiana wersji `sqlcmd`, silnika lub systemu wymaga nowego testu na żywo; zakres weryfikacji opisuje tabela wyżej.

### Sondy S1: silnik a `restore --move` (23.11.2026)

Środowisko: SQL Server LocalDB 2019 (15.0.4382.1, Express), `sqlcmd` 15.0, uwierzytelnianie zintegrowane, bazy i katalogi tymczasowe sprzątane po biegu. Dowody: `docs/probes/t6-s1/evidence-2026-11-23.md`.

| Fakt | Wynik | Zakres |
|---|---|---|
| 1. `RESTORE … WITH REPLACE` bez `MOVE` na istniejącą bazę | PASS w zakresie sondy | Kopia **innej** bazy (ta sama struktura plików, inne ścieżki) odtwarzana na istniejącą bazę używa ścieżek z kopii, nie z bazy docelowej, i gdy ich katalog nie istnieje, pada w fazie planowania (błędy 5133, 3156, 3119, 3013); baza zostaje `ONLINE` z oryginalnymi plikami. Sonda wykonała goły `RESTORE`, **nie** batch narzędzia. |
| 2. `REPLACE, MOVE` na obecne ścieżki | PASS | Pliki (`.mdf`, `.ndf` w osobnej `FILEGROUP`, log) są nadpisywane w miejscu: `physical_name` bez zmian, brak sierot, dane w obu grupach niezmienione. Dane nie były zmieniane między kopią a restore, więc to nie dowodzi, że `RESTORE` coś odtworzył (tego dotyczy K8(b)). |
| 3. `RESTORE FILELISTONLY` w trybie `-W -s "\|" -w 65535` | PASS | 22 kolumny (`LogicalName`, `PhysicalName`, `Type`, `FileGroupName`, `Size`, `MaxSize`, `FileId`, … `TDEThumbprint`, `SnapshotUrl`), `Type` `D` i `L`. Kolejność wierszy **nie jest** kolejnością `FileId` (np. 1, 3, 2) i różni się od `sys.master_files`; parser wskazuje kolumny po nazwie nagłówka i nie zależy od kolejności wierszy. |
| 4. `InstanceDefaultDataPath`, `InstanceDefaultLogPath` | PASS | Wartości niepuste, z końcowym separatorem; na LocalDB wskazują katalog profilu użytkownika. |
| 7. Ścieżka z polskimi literami w `sqlcmd` | PASS (częściowo) | `sqlcmd` i silnik przyjęły taką ścieżkę (`BACKUP`, `RESTORE VERIFYONLY`), ale komunikaty silnika dochodzą do konsoli zniekształcone. Whitelista ASCII zostaje bez zmian. |
| 10. Spacja brzegowa w nazwie logicznej | PASS (część) | Silnik przycina ją już przy `CREATE DATABASE` (zbadano tylko ten przypadek, nie `ADD FILE` ani `MODIFY NAME`). Walidator „bez spacji brzegowych” chroni przed niezaufaną kopią. |

**NOT_RUN (niezweryfikowane):** LocalDB innego użytkownika (nazwa serwera i maszyny w nagłówku kopii z innego konta); login ograniczony (uprawnienia `FILELISTONLY`, widoczność `sys.master_files`); TDE i kopie z nowszej wersji silnika;
obce pliki albo osierocone pliki w katalogu docelowym przy `new`; baza w stanie `RESTORING` po nieudanym `new` (`ALTER`, `DROP`), baza z migawką; kopie utworzone przez konto usługi SQL; kopia **tej samej** bazy po przeniesieniu jej plików
(fakt 1 sprawdzono na kopii innej bazy; `existing`, który zawsze emituje `MOVE`, jest na tę różnicę odporny z konstrukcji); różne rozmiary plików w kopii i bazie; kopia z innego układu plików.
**K8(b), test nowego kodu na żywo (LocalDB 2019, 23.11.2026; dowody `docs/probes/t6-k8/evidence-2026-11-23.md`), PASS:** `existing` ze zmianą danych po kopii: dane wracają (1/1), `physical_name` bez zmian, brak sierot, kopia bezpieczeństwa powstaje; `new` z `--move-dir`: kod 0, pliki tylko w podanym katalogu; `new` bez `--move-dir`: kod 0, pliki w katalogach domyślnych silnika (profil użytkownika), po `DROP` bez plików; `new` z nieistniejącym `--move-dir`: kod 1, baza nie powstała, komunikat „No database was left”; `none` na kopii z nieistniejącymi ścieżkami: kod 1 (błąd 5133), po porażce baza `ONLINE`/`MULTI_USER` i dane bez zmian (w tym przypadku `ALTER … SET MULTI_USER` z bloku `CATCH` batcha się powiodło; że zawsze tak będzie, nie jest dowiedzione); `none` bez zmian (kod 0, dane wracają). Nie zbadano: pozostałych NOT_RUN z tabeli (LocalDB innego użytkownika, ograniczony login, TDE i nowsza wersja silnika, obce pliki w katalogu docelowym, baza `RESTORING` po nieudanym `new`, kopie z konta usługi SQL, kopia tej samej bazy po przeniesieniu plików).
