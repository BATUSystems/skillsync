# Wersje i aktualizacje

Rdzeń, Skippster i każda warstwa mają wersję [SemVer](https://semver.org/lang/pl/) i tagi `vX.Y.Z`.
Warstwa deklaruje `core_min`: minimalną wersję rdzenia, z którą działa. `link` i `layers check` zwracają BLOCKED (2),
a `doctor` zgłasza błąd (FAIL, 1), gdy rdzeń jest za stary.

## Kiedy podbić wersję

| | Rdzeń | Warstwa org/personal |
|---|---|---|
| **MAJOR** | niezgodna zmiana CLI, kodów wyjścia, schematów JSON, kluczy manifestu/configu, formatu artefaktów; usunięcie lub zmiana nazwy skilla, roli, szablonu | usunięcie lub zmiana nazwy skilla, szablonu, roli; zmiana ścieżki pliku wskazywanego przez inne warstwy; podniesienie `core_min` o MAJOR |
| **MINOR** | nowe komendy, klucze, skille, role zgodne wstecz; rozszerzenie formatu pliku o pole, które starszy rdzeń ignoruje bez eskalacji uprawnień | nowy skill, szablon, fragment; podniesienie `core_min` o MINOR |
| **PATCH** | poprawki, dokumentacja | poprawki tekstu, aktualizacja danych |

## Co oznacza `core_min`

- Wersja rdzenia to wersja generatora.
- `layers check` i `link` zwracają BLOCKED (2), gdy `core_min` warstwy jest wyższe niż wersja generatora.
- `doctor` zgłasza to jako błąd (FAIL, 1).
- Rozjazd wersji generatora i warstwy `core`, który nie narusza `core_min`, to ostrzeżenie.
- Warstwa, która podnosi `core_min` o MAJOR, sama wymaga wpisu **Breaking** w `CHANGELOG.md`.

Wersję generatora pokazuje `skillsync --version`.

Pakiet aktualizujesz ponownym zainstalowaniem z tagu (`uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z`); zobacz [installation.md](installation.md#aktualizacja).

## Przejście na nową wersję MAJOR

1. Przeczytaj sekcję **Breaking** w `CHANGELOG.md` (zmiany niezgodne są tam wypisane).
2. Zaktualizuj rdzeń, potem warstwy.
3. `skillsync layers check` i `skillsync doctor` w projektach.
4. `skillsync link <projekt>` w każdym projekcie z `discover_paths` (albo `skillsync link` bez ścieżki).

## Przejście 1.3.0 → 1.4.0

Wydanie 1.4.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Zbiera pięć zmian: szacunek czasu (`eta`) na stronie statusu, drobne poprawki techniczne (publiczny adres prototypów, JSON w `show`/`wait`, port dashboardu), znacznik pisania z odnowieniem i zwolnieniem, polecenia w komentarzach i decyzje programisty, oznaczenie niezaufanej treści w komendach `question`. Warstwa `core` ma `core_min: 1.4.0`:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.4.0`, potem `skillsync layers update core --ref v1.4.0`.
2. Istniejące prototypy dostają nowy arkusz i silnik (stan przełączników, mniejsze pole komentarza, baner zapisu z godziną i „Zwolnij”) przez `skillsync prototype migrate`.
3. Pole `reason` w pytaniach do eksperta (`question withdraw --reason`): starsza wersja odrzuci plik `questions.json` z tym polem, więc zaktualizuj rdzeń u wszystkich pracujących na wspólnym projekcie, zanim ktokolwiek go użyje.
4. Raporty `prototype comments`, `prototype wait` i `question list|show|export|wait` mają dwa nowe klucze na początku (`untrusted`, `notice`); konsument pomijający nieznane klucze nie jest dotknięty.
5. Uruchom ponownie działający `prototype serve` i serwer strony statusu.

## Przejście 1.4.0 → 1.4.1

Wydanie 1.4.1 jest PATCH (zgodne wstecz w kontraktach: sekcja **Breaking** w `CHANGELOG.md` to „None.”), ale zmienia mechanizm blokady pliku zgód `approvals.jsonl` (zob. [dashboard-contract](dashboard-contract.md), „Blokada”).

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.4.1`, potem `skillsync layers update core --ref v1.4.1`.
2. **Uruchom ponownie działający `skillsync dashboard serve` PRZED pierwszą zgodą po aktualizacji i zaktualizuj każdą kopię `skillsync` (też z innego środowiska wirtualnego), z której wołasz `approvals record`.** Działający proces ma w pamięci stary kod i zapisuje zgody starą blokadą (plik `approvals.jsonl.lock` tworzony wyłącznie, `O_EXCL`), a nowy kod bierze blokadę systemową na tym samym pliku. Stary pisarz respektuje nowego (plik `.lock` istnieje przez cały czas jego trzymania, więc `O_EXCL` starego zawodzi i stary czeka), ale nowy nie widzi starego: otwiera plik `.lock` starego pisarza i blokuje go bez czekania, więc w tej samej chwili piszą obaj, a przy zwolnieniu nowy usuwa plik, który mógł być starego. Oba dopisują linię `O_APPEND` z `fsync`, więc nie uszkodzą pliku, ale mogą zapisać dwa razy tę samą parę słowo + sha. Czytelnicy zgód są na zdublowany wpis odporni (liczy się ostatni wpis na parę przebieg-słowo, test `test_a_duplicate_entry_in_the_file_is_harmless_for_the_readers`), więc skutek jest kosmetyczny. Po restarcie serwera i aktualizacji wszystkich kopii problem znika. Plik `approvals.jsonl.lock` pozostały po starym zapisie lub po awarii niczemu nie szkodzi: nowy kod go ignoruje.
3. Systemy plików bez blokad systemowych (niektóre udziały sieciowe, sterowniki FUSE): `approvals record` i zapis zgody ze strony statusu kończą się `BLOCKED` z powodem „file locks are not supported”, zamiast czekać (to jedyna zmiana zgodności tej wersji: stara blokada plikowa działała na każdym systemie plików z atomowym tworzeniem pliku). Trzymaj `.skillsync/` na dysku lokalnym. To samo rozróżnienie dotyczy blokad pytań do eksperta, komentarzy i użytkowników serwera prototypów (błąd 503 `lock_unsupported` zamiast `lock_timeout` po 5 s; polecenia `prototype user` zgłaszają `lock_unsupported`). Błąd `ENOLCK` (brak menedżera blokad) jest przez pierwszą połowę czasu oczekiwania ponawiany, bo bywa chwilowy.

## Przejście 1.5.0 → 1.6.0

Wydanie 1.6.0 jest MINOR z jednym wpisem **Breaking** w `CHANGELOG.md`: konto zalogowane na serwerze prototypów bez roli eksperta dostaje przy `GET /_api/v1/questions`, `/_api/v1/questions/<id>` i trasach przebiegu `GET /_api/v1/prototypes/<slug>/questions[/<id>]` odpowiedź `200` z podglądem tylko do odczytu (`preview: true`) zamiast `403`; klient, który po `403` poznawał brak roli, ma czytać `preview`. Zapis nadal kończy się `403`. Reszta jest zgodna wstecz: nowe klucze `dashboard show` i `/api/status` są opcjonalne (`schema: 1`), a stary renderer je pomija.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.6.0`, potem `skillsync layers update core --ref v1.6.0`.
2. Tryb prezentacji prototypu dostają tylko prototypy po `skillsync prototype migrate` (zmiana arkusza stylów może unieważnić akceptację ekranów; lista jest w polu `acceptance` wyniku).
3. Strona statusu na dwóch portach (opcjonalnie): jedno wywołanie `skillsync dashboard serve <projekt> --read-port 5140 --approve-port 5141` zastępuje dwie instancje (jedną z `--no-approvals`). Najpierw zatrzymaj stare procesy; oba porty muszą być wolne, inaczej start kończy się `BLOCKED` bez skutków ubocznych. Token zgód idzie zwykłym HTTP: w sieci nieznanej użyj `--approve-bind 127.0.0.1` albo tunelu SSH, a tunele kieruj na port odczytu. Port odczytu nie wymaga logowania i pokazuje treść pytań oraz loginy adresatów każdemu, kto ma do niego dostęp w sieci (świadoma decyzja właściciela): w sieci niezaufanej zawęź go przez `--read-bind 127.0.0.1`.
4. Warstwa, która dodaje pozycje do `workflow.review.prototype`, wymaga `core_min` 1.6.0 (starszy rdzeń nie ma profilu `prototyp`, `layers check` blokuje warstwę).
5. Konsument `skillsync status` ma przyjąć nieznaną wartość `acceptance` w `plans[].needsHuman`.
6. Uruchom ponownie działający `skillsync dashboard serve` i `prototype serve`.

## Przejście 1.4.1 → 1.5.0

Wydanie 1.5.0 jest MINOR z wpisem **Breaking** w `CHANGELOG.md` (decyzja właściciela): zgody ze strony statusu wymagają tokenu, więc własne skrypty i klienty, które wołają `POST /api/approve`, przestają działać bez dostosowania. Strona statusu i wtyczka edytora tego samego wydania nie wymagają zmian.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.5.0`, potem `skillsync layers update core --ref v1.5.0`.
2. **Token zgód (Breaking).** `POST /api/approve` bez nagłówka `X-Approve-Token` dostaje `403` (`code: "approve_token"`). Własny skrypt albo klient bierze adres poleceniem `skillsync dashboard url <projekt>` (token to fragment `#zgoda=<token>`) i wysyła jego wartość w nagłówku. Otwarta karta ze starą stroną dostaje to samo `403`, dopóki nie zostanie otwarta ponownie z adresu z fragmentem (przeglądarka zapamiętuje token w `localStorage`).
3. Uruchom ponownie działający `skillsync dashboard serve` i `prototype serve`: działający proces ma w pamięci stary kod (bez tokenu zgód i bez ACL plików).
4. Windows: pliki, które POSIX trzyma w trybie 0600 (plik użytkowników, stan i tokeny logowania jednorazowego, `audit.jsonl`, config maszyny z `prototype.default_password`, plik stanu tokenu zgód), są prywatne przez ACL; plik zapisany przez starszą wersję jest naprawiany przy pierwszej komendzie, która go czyta lub zapisuje (jedna linia `ACL Windows: ...`). Ostrzeżenie „kontrola uprawnień 0600 pominięta na Windows” znika: konsument filtrujący jego tekst musi to uwzględnić.
5. Windows: pola ścieżek we wszystkich raportach CLI mają `/` (zob. lista w `CHANGELOG.md`); konsument porównujący je z natywną ścieżką (`C:\...`) musi porównywać z `/` albo normalizować (`Path(wartość)`). Linux i macOS bez zmian.
6. Nowy klucz `targets.<id>.committer_allow` w configu maszyny (strażnik wycieków; domyślnie pusta lista, bez klucza zachowanie bez zmian).
7. Maskowanie sekretów ze zmiennych środowiska działa od 8 znaków: sekret krótszy niż 8 znaków nie jest już maskowany w wyjściu (maskowanie wzorcowe bez zmian).

## Przejście 1.2.x → 1.3.0

Wydanie 1.3.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Dodaje marker „agent pisze ekrany” (`skillsync prototype writing`, blokada akceptacji i przywracania), czas pracy i czekania na stronie statusu, `skillsync leakcheck --text` i profil przeglądu `docs`; repozytorium rdzenia przestaje śledzić `docs/specs/**` i `docs/plans/**`. Warstwa `core` ma `core_min: 1.3.0`:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.3.0`, potem `skillsync layers update core --ref v1.3.0`.
2. Istniejące prototypy dostają nowy silnik (baner zapisu) przez `skillsync prototype migrate`; do tego czasu starszy silnik pokazuje ogólny błąd przy zablokowanej akceptacji.
3. Uruchom ponownie działający `prototype serve`.

## Przejście 1.2.0 → 1.2.1

Wydanie 1.2.1 jest PATCH (sekcja **Breaking** w `CHANGELOG.md` opisuje zmianę lokalizacji pliku pytań). Pytania do eksperta są osobne dla każdego przebiegu:
`<root>/<slug>/questions.json` zamiast jednego `<root>/questions.json`.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.2.1`, potem `skillsync layers update core --ref v1.2.1`.
2. Projekt z pytaniami i starym plikiem `<root>/questions.json`: `skillsync prototype question migrate . --dry-run` (plan), potem `skillsync prototype question migrate .`.
   BLOCKED `unassigned` oznacza pytania bez jednoznacznego przebiegu; rozstrzygnij je przez `--slug-for SPEC=SLUG`. Do czasu migracji serwer pokazuje stare pytania tylko do odczytu.
3. Uruchom ponownie działający `prototype serve`. Rejestracja sterownika `skillsync-questions` nie zmienia się, ale `merge` z ręczną formą projektową wymaga `--slug`.

## Przejście 1.1.x → 1.2.0

Wydanie 1.2.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Dodaje pytania do eksperta domenowego: rolę `expert`,
konta z wieloma rolami, plik pytań przebiegu (`questions.json`), stronę `/_experts` i komendy `skillsync prototype question …` oraz `prototype user roles`.
Kolejność jest obowiązkowa, bo warstwa `core` ma `core_min: 1.2.0`. Warstwa `core` jest wydawana razem z rdzeniem, więc podniesienie jej `core_min` do wersji tego samego wydania nie jest zmianą MAJOR warstwy (reguły w tabeli i w sekcji „Co oznacza `core_min`” wyżej dotyczą rozjazdu między osobno wydawanymi warstwami):

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.2.0`, potem `skillsync layers update core --ref v1.2.0`.
2. Pozostałe warstwy: `skillsync layers update <id>` albo `skillsync pull`, potem `skillsync link`; uruchom ponownie działające `prototype serve`.

Plik kont i starszy rdzeń:

- konta ze starym polem `role` czytasz bez migracji, a odczyt niczego nie zapisuje;
- zapis jest zgodny wstecz: pole `role` zawiera najwyższą rolę ze zbioru rozumianego przez starszy rdzeń (`commenter`, `acceptor`, `developer`; kolejność `developer` > `acceptor` > `commenter`; konto tylko z `expert`
  ma `commenter`), a pole `roles` dochodzi obok tylko dla kont z rolą `expert` albo z kilkoma rolami;
- starszy rdzeń (1.1.x) czyta ten sam plik: ignoruje `roles`, więc widzi eksperta jako komentującego, bez eskalacji uprawnień;
  komendy `question` ma tylko rdzeń 1.2.0, dlatego `core_min` warstwy `core` blokuje jej linkowanie na starszym rdzeniu;
- `comments.json` i silnik prototypu nie zmieniają się; nowa funkcja `questions` w `GET /_api/v1/session` (`features`) to dodatek, a nie
  zmiana `server.version`.

Ręcznie, jeśli projekt używa pytań do eksperta: zarejestruj sterownik scalania `skillsync-questions`
([prototypes](prototypes.md#pytania-do-eksperta-w-gicie)) i uruchamiaj `serve` z `--login-to-view`.

## Przejście 1.0.x → 1.1.0

Wydanie 1.1.0 jest MINOR (zgodne wstecz): nowe komendy i klucze, bez zmian niezgodnych (sekcja **Breaking** w `CHANGELOG.md` to „None.”).
Gdy któraś warstwa ma `core_min: 1.1.0`, kolejność jest obowiązkowa:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.1.0`, potem `skillsync layers update core --ref v1.1.0`.
   Warstwa z `core_min` wyższym niż wersja generatora jest odrzucana (`layers check` i `link` zwracają BLOCKED).
2. Pozostałe warstwy: `skillsync layers update <id>` albo `skillsync pull`, potem `skillsync link`.

Co się zmienia dla użytkownika:

- nowe komendy: `skillsync dashboard show|serve|metrics`, `skillsync approvals record`, `skillsync prototype login-link`;
- nowy klucz manifestu `workflow.ceremony` (`full` | `demo`), bramka prototypu per faza (`[UI]` w tabeli kroków planu), klasy pytań `Q` w specu;
- alias `status-board` → `dashboard`: wpis `status-board` w `skills:` rozwija się do `dashboard` tylko wtedy, gdy żadna warstwa nie dostarcza skilla
  `status-board`. Warstwa, która nadal go ma, zachowuje własny skill; po jego usunięciu z warstwy alias zaczyna działać, a skrypty projektów
  odwołujące się do ścieżki `status-board/scripts/status_board.py` trzeba zmienić na `skillsync dashboard serve <projekt>`.

## Przejście maszyny na warstwy (wersja 1.0.0)

Generator 1.0.0 nie czyta dwóch wcześniejszych postaci configu i dla obu zwraca BLOCKED z instrukcją
`skillsync layers migrate`:

- config z kluczem `library:`;
- config z mostem: tymczasowa warstwa `core` oznaczona `synthetic` (tylko do odczytu).

Procedura jest dla obu taka sama; przy moście `layers migrate` zastępuje tymczasowy wpis prawdziwą warstwą `core`.
Config jest per maszyna: wszystkie kroki wykonujesz osobno na każdej maszynie.

### Warstwa `core`

Repo rdzenia ma katalog `library/` z warstwą `core` i jej `layer.yaml` (`version`, `core_min`). Jeśli warstwa `core` leży
w innym katalogu (np. własna kopia biblioteki), podaj jego ścieżkę w `--layer-source core=…`.

Katalog bez `layer.yaml` wymaga w kroku 3 `--kind core=core`. Przed kontrolą w kroku 7 dodaj w nim `layer.yaml` z polami `schema: 1`,
`id: core`, `kind: core`, `version` i `core_min`. Bez tego pliku `layers check` i `link` zwracają BLOCKED (2,
`layer-yaml-missing`), a `doctor` zgłasza błąd (FAIL, 1); `layers migrate` tylko ostrzega.

### Kroki

1. **Kopia.** Zrób kopię configu (`~/.config/skillsync/config.yaml` albo plik wskazany przez `SKILLSYNC_CONFIG`) oraz kopie
   manifestów projektów i definicji usługi watch.
2. **Przepięcie generatora** na wersję 1.0.0 (dowiązanie albo pakiet). Nie restartuj jeszcze usługi watch i nie wykonuj `link`:
   nowy generator czyta config dopiero po migracji (kroki 3–4).

   Linux z checkoutu: usuń dowiązanie `~/.local/bin/skillsync`, potem `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.0.0` i sprawdź `skillsync --version`. `skillsync-ctl` zostaje w checkoucie (nie wchodzi do pakietu).
3. **Dry-run.** Sprawdź plan migracji:

   ```bash
   skillsync layers migrate --layer-source core=<lokalny klon repo skillsync>/library
   ```

   Dla warstw `org` i `personal`, które mają lokalne checkouty, dodaj `--layer-source <id>=<ścieżka>`. Dla każdej warstwy bez
   `layer.yaml` (także `core`) dodaj `--kind <id>=<rodzaj>` (`core`, `org` albo `personal`). Dla warstw `org` dodaj
   `--match <id>=<glob>`.
4. **Zapis.** Powtórz polecenie z `--apply`. Generator zapisuje kopię zapasową configu przed zmianą.
5. **Manifesty (opcjonalnie).** Usuń z manifestów projektów ignorowane ustawienie źródła biblioteki:

   ```bash
   skillsync layers migrate --manifests          # podgląd zmian
   skillsync layers migrate --manifests --apply  # zapis
   ```

   Kopie zmienionych manifestów trafiają do `~/.local/share/skillsync/backups/manifests/<czas>/`. Bez tego kroku manifesty
   zostają bez zmian (ustawienie jest ignorowane z ostrzeżeniem).
6. **Hooki warstw zapisywalnych.** Dla każdej warstwy `org` i `personal` z lokalnym checkoutem uruchom
   `skillsync leakguard install-hook <ścieżka warstwy>`, aby hooki `pre-commit` i `pre-push` działały bez zmian w repo.
7. **Kontrola.** `skillsync layers check` (PASS wymaga `layer.yaml` warstwy `core`, patrz „Warstwa `core`”, i hooków
   z kroku 6: bez nich FAIL `hook-missing`).
8. **Restart usługi watch** i `skillsync link` wszystkich projektów z `discover_paths`.
   Na Linuksie restart: `skillsync-ctl restart` w checkoucie repo.

   Na macOS i Windows usługi watch nie ma: zatrzymaj ręcznie uruchomione `skillsync watch` i uruchom je ponownie.
9. **Doctor.** `skillsync doctor <projekt>` w każdym projekcie: oczekiwany PASS.

### Zakończenie migracji

`legacy_library` to ścieżka starej biblioteki zapisana przez `layers migrate`. Służy do rozpoznawania dowiązań
utworzonych przez starą konfigurację.

```bash
skillsync layers migrate --finish          # podgląd
skillsync layers migrate --finish --apply  # usuwa legacy_library z configu tej maszyny
```

Wykonaj `--finish --apply`, gdy `skillsync layers check --isolation` daje PASS (`--finish` wymaga też
`isolation.allowed_layers` w configu).

Często `legacy_library` to ten sam katalog co warstwa `core`: wtedy go nie usuwaj, `--finish` przechodzi. Gdy warstwa `core`
wskazuje inny katalog, `--finish` zwraca BLOCKED (2), dopóki stary klon spod `legacy_library` istnieje: przenieś z niego
lokalne zmiany i usuń go.

### Cofnięcie

1. Przywróć poprzednią wersję generatora (odwrotność kroku 2): generator 1.0.0 zwraca BLOCKED dla przywróconego configu
   z `library:`.
2. Przywróć kopię configu i kopie manifestów oraz dowiązania i definicję usługi watch.

### Znane ograniczenie

Przed `layers migrate` config zawiera najwyżej jedną warstwę `personal`.

Przy kilku warstwach `personal` `layers migrate` kończy się BLOCKED (`More than one personal layer: <id>, <id>`) i niczego
nie zapisuje; w regułach (`layer_rules`) każda reguła może mieć najwyżej jedną warstwę `personal`.

### Reguła domyślna

Reguła `**` dla projektów bez innej reguły tworzy `layers migrate` albo ręcznie `skillsync layers use <personal> --match '**'`.
`layers add` i `layers init` tylko ostrzegają o jej braku.

### Warstwy zdalne

`--layer-source` przyjmuje wyłącznie ścieżki lokalne. Warstwę zdalną dodaj po migracji:
`skillsync layers add <id> --kind <rodzaj> --source <URL>`.

## Windows z instalacji uv

Aktualizacja maszyny z Windows, na której rdzeń stoi jako narzędzie uv (sprawdzona na przejściu z 1.0.1 do 1.6.0). Polecenia z katalogu projektu, nie z katalogu domowego.

1. `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.6.0`.
2. `skillsync layers update core --ref v1.6.0` (zapisuje kopię configu `.bak-<czas>`).
3. W katalogu projektu `skillsync layers check`. Dla każdej warstwy zapisywalnej: `skillsync leakguard install-hook <ścieżka warstwy>`; bez hooków `layers check` kończy się FAIL `hook-missing`.
4. `skillsync layers update <id warstwy org>`.
5. `skillsync link <projekt> --dry-run`, potem `skillsync link <projekt>`, na końcu `skillsync doctor <projekt>`.

Co zobaczysz po drodze:

- **`layers list` i `layers check` zależą od katalogu.** Pokazują warstwy według reguł dla bieżącego katalogu: poza projektem tylko `core`, w katalogu projektu także warstwę org. Sprawdzaj z katalogu projektu.
- **Projekt dowiązany jeszcze do starej biblioteki.** `doctor` przed `link` zwraca BLOCKED „Path escapes project: .github/skills/…”. `link` przepina dowiązania na warstwy (część skilli wskazuje wtedy katalog cache `composed`), usuwa przestarzały plik roli i odświeża zadanie startowe edytora.
- **Zadanie startowe edytora** (`.vscode/tasks.json`, `runOn: folderOpen`): stara definicja wołała `python <klon generatora>/skillsync link …` i po migracji configu na warstwy kończyła się `KeyError: 'library'`. `link` z nowej wersji przepisuje ją na plik wykonywalny z instalacji uv; edytor trzyma starą definicję do przeładowania okna.
- **Dwie komendy `skillsync` na PATH.** Obok pliku z instalacji uv może leżeć stary skrypt-opakowanie klonu; obowiązuje pierwsza. Sprawdź `Get-Command skillsync -All` (PowerShell) albo `where skillsync`.
- **Manifest.** `doctor` ostrzega o ignorowanym `sync: {library: …}` i o przemianowanej roli `Explore` → `explore`. `skillsync layers migrate --manifests` obejmuje tylko projekty z `discover_paths`; w projekcie spoza listy usuń klucz ręcznie.
- **Rekord domenowy z dwukropkiem w nagłówku.** `doctor` zgłasza błąd YAML przy niecytowanej wartości zawierającej „: ”; ujmij ją w apostrofy.
- **Warstwa prywatna** wymaga dostępu do jej repozytorium z tej maszyny; bez niego generowane instrukcje nie zawierają części pochodzących z tej warstwy.

## Ograniczenia na poszczególnych systemach

Zakres wsparcia systemów opisuje tabela w [README](../README.pl.md#wymagania).

Aktualizacja maszyny Windows z instalacji uv: sekcja „Windows z instalacji uv” wyżej (natywny Windows jest poza drogą instalatora: WSL2). Procedura przejścia maszyny macOS na warstwy oraz ręczne testy na tym systemie powstaną w późniejszym wydaniu.

## Co dalej

Zmiany między wersjami opisuje wyłącznie `CHANGELOG.md`.
