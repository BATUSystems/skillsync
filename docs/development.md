# Rozwój generatora

Instrukcja zmieniania `skillsync` i pakietu `skillsync_workflow/`: środowisko, testy, kontrole składni, bezpieczne próby na
projekcie testowym, usługa watch, Windows i pułapki. Czytaj przed pierwszą edycją kodu.
Zasady udziału zewnętrznego (zgłoszenia, `leakcheck` przed PR): [CONTRIBUTING](../CONTRIBUTING.md).

## Układ repo

| Element | Rola |
|---|---|
| `skillsync` | wrapper uruchamiający pakiet z checkoutu (skrypt bez rozszerzenia) |
| `skillsync_workflow/` | pakiet generatora: polecenia w `cli.py`, komendy workflow (rejestracja w `__init__.py:register_subcommands`), `selfcmd.py` |
| `skillsync-ctl` | sterowanie usługą watch (bash, tylko Linux) |
| `skillsync-windows.ps1` | bootstrapper dla Windows |
| `tests/` | testy `unittest` i zasoby testowe (`tests/fixtures/`, `tests/e2e/prototype/`) |
| `tools/claude-headless/` | wspólny rdzeń opakowań `claude` i zadania nieinteraktywne `claude -p` (audyt wydania, dryf dokumentacji), patrz „Zadania nieinteraktywne” |
| `tools/claude-eval/` | opakowanie `plugin eval` Claude Code dla skilli biblioteki i przypadki eval (`evals/`), patrz „Eval zachowania skilli” |
| `tools/d28/` | narzędzie pomocnicze do porównywania plików (podobieństwo rezydualne po odjęciu źródeł kontraktu), uruchamiane ręcznie |
| `scripts/` | skrypty kontrolne i pomocnicze CI oraz wydań (`check_skips.py`, `check_release.py`, `check_wheel.py`, `check_core_en.py`, `merge_modules.py`, `platform_sensitive.py`, `smoke.sh`/`smoke.ps1`, `windows_shim_check.py`, generatory `sync_renderer_defaults.py` i `sync_proto_i18n.py`) |
| `library/` | warstwa `core`: skille, role, instrukcje i workflow rdzenia |
| `layers/core-en/` | warstwa tłumaczenia `core-en` (angielskie przekłady biblioteki), patrz „Tłumaczenie biblioteki” |
| `docs/` | dokumentacja użytkownika (ta strona i pozostałe) |

## Środowisko

- Python 3.11 lub nowszy (`tomllib`) i PyYAML. Testy używają wyłącznie `unittest`, bez pytest, żeby działały na Windows bez dodatkowych instalacji.
- Kod poleceń leży w `skillsync_workflow/cli.py`; testy importują go przez `tests/cli_module.py`. Plik `skillsync` to tylko
  wrapper (bez PyYAML kończy się komunikatem `PyYAML is required`).
- Uruchamianie z drzewa źródeł: `./skillsync ...` w katalogu repo albo `python3 -m skillsync_workflow ...`.
- Artefakty, które wywołują Skillsync (zadanie VS Code, launchery, hooki strażnika, `{skillsync}` w poleceniach), dostają
  postać z `selfcmd.self_argv()`: z checkoutu `python <checkout>/skillsync`, z pakietu `python -P -m skillsync_workflow`.
  Po przeniesieniu checkoutu uruchom `skillsync link` w projektach.

Instalacja deweloperska: `uv tool install -e .`. Jeśli `~/.local/bin/skillsync` jest już dowiązaniem, usuń je przed instalacją (inaczej uv odmówi nadpisania), potem sprawdź `which skillsync`.

## Testy

```bash
python3 -m unittest discover -s tests -p 'test_skillsync*.py'      # pełny zestaw
python3 -m unittest tests/test_skillsync_dispatch.py               # jeden plik
python3 -m unittest tests.test_skillsync_dispatch.ExpandSkillsTests # jedna klasa
python3 -m py_compile skillsync skillsync_workflow/*.py             # składnia Pythona
bash -n skillsync-ctl                                               # składnia skryptu
```

Po każdej zmianie Pythona uruchom `py_compile`, po zmianie `skillsync-ctl` uruchom `bash -n`. Każda zmiana zachowania wymaga testu;
nie osłabiaj asercji, żeby dopasować je do błędu.

### Testy w przeglądarce (`ui-verify`, `css-scope`)

`discover` nie dowodzi, że testy w Chrome pobiegły: bez zmiennych zestaw `node:test` pomija je, a skipów Node nie widać w logu `unittest`. Przed wydaniem
i po zmianie strony statusu uruchom je osobno, jak zadanie `e2e` w `.github/workflows/ci.yml` (wynik: 0 `fail`; pominięte wolno tylko `needs-network`
i `needs-windows`):

```bash
export UI_VERIFY_CACHE_DIR=<katalog-cache> UI_VERIFY_TEST_CACHE=<katalog-cache> UI_VERIFY_CHROME=<ścieżka-chrome>
node library/skills/ui-verify/scripts/install.mjs
node --test 'tests/ui_verify/*.test.mjs' 'tests/css_scope/*.test.mjs'
```

### Skipy

Skip bez powodu albo poza listą dozwolonych to błąd, nie zielony wynik. Same skipy nie dowodzą zaliczenia: sprawdź liczbę
uruchomionych testów.

- Dekoratory z powodem są w `tests/layers_helpers.py`. Sprawdzają **system** (np. `needs_os_symlinks`, `needs_posix`, `needs_flock`),
  nigdy tryb linkowania: `SKILLSYNC_NO_SYMLINK=1` nie może pomijać testu, który tylko podkłada dowiązanie.
- Test, który sprawdza wynik trybu symlink, przypina go dekoratorem `@link_mode('symlink')`; odpowiednik trybu kopii to `@link_mode('copy')`.
- Test nie wraca po cichu tam, gdzie niczego nie sprawdza (`if os.name == 'nt': return` raportuje PASS i omija baseline): mechanizm, którego system nie ma, to `self.skipTest(powód)` albo `@unittest.skipUnless`; pilnuje tego `tests/test_skillsync_no_silent_return.py`.
- `tests/skip-baseline.json` opisuje dozwolone pominięcia per system i tryb (klucz `macos-uv/*`: lokalna macierz macOS na interpreterach uv, które nie mają `time.tzset`; CI używa `macos-latest/*`); `scripts/check_skips.py` sprawdza log biegu (klucz najwyższego poziomu `environment_categories` wymienia kategorie zależne od maszyny, np. `needs-git-2-48` dla testu z `git worktree add --relative-paths`; takie pominięcie jest dozwolone na każdym kluczu bez wpisu):

```bash
python3 -m unittest discover -s tests -p 'test_skillsync*.py' -v 2>&1 | tee run.log
python3 scripts/check_skips.py run.log --system ubuntu-latest --mode copy
```

### Kontrola izolacji stanu (`real_state_snapshot`)

Kontrola „test touched real Skillsync state dirs” porównuje stan prawdziwych katalogów użytkownika (`REAL_STATE_DIRS`) przed testem i po nim
(`IsolatedTestCase`, `guard_real_state`, cleanup w `test_skillsync_workflow.py`). Porażka podaje do 10 zmienionych ścieżek w formie
`<ścieżka>: <przed> -> <po>` (`brak` = nie istniało; reszta jako `i N innych`), więc zaczynasz od nazwy pliku, nie od zrzutu słowników.

- Porównywane są rozmiar i czas modyfikacji każdego pliku, a katalogi razem z nimi, z wyjątkami: pliki odświeżane przez `git` tylko do odczytu
  (`FETCH_HEAD`, `.git/index`, `*.lock` w `.git`) i **metadane katalogów pod `composed`** (`cache/composed` na Windows) razem z potomkami:
  nowy i usunięty katalog oraz każdy plik w nich nadal są widoczne, sam czas i rozmiar katalogu nie.
- Rejestr `composed/.projects.json` jest porównywany **tylko we wpisach projektów spod katalogu tymczasowego** (`tempfile.gettempdir()`
  z chwili importu, po `resolve`); plik tymczasowy rejestru (`.projects.json.tmp-*`) jest pomijany, zły JSON liczy się jako pusty rejestr.
  Rejestr jest rozpoznawany po miejscu pod `REAL_STATE_DIRS`, nie przez `composed_root()`, bo cleanup biegnie przy podstawionym środowisku.
- Powód: `skillsync link` dowolnej innej sesji przepisuje rejestr i zmienia czas katalogu `composed`, co nie jest wyciekiem testu.
- Cena: kontrola nie wykryje przejściowego zapisu pliku w katalogu pod `composed` (utworzony i usunięty, zmienia tylko czas katalogu) ani testu,
  który linkuje PRAWDZIWY projekt spoza katalogu tymczasowego (zmiana jego wpisu w rejestrze). Nie wykryje też usunięcia i odtworzenia pustego katalogu pod `composed`. Zapisy poza `composed` i każdy plik (z wyjątkiem pliku rejestru i `.projects.json.tmp-*`) są wykrywane.
- Przy porażce nadal obowiązuje procedura z lekcji `pelny-bieg-testow-a-git-na-prawdziwych-warstwach.md`: ustal sprawcę z różnicy, powtórz moduł.

### Tłumaczenie biblioteki (`layers/core-en`)

Zmiana pliku Markdown w `library/skills/**`, `library/agents/**` albo `library/instructions/**`, który ma przekład w `layers/core-en/`, wymaga w tym samym
commicie: (1) aktualizacji przekładu (te same nagłówki, ten sam frontmatter poza `description`; tokeny protokołu po angielsku wg `protocol_tokens.py`),
(2) `python3 scripts/check_core_en.py --update` (odświeża `translations.lock.yaml`; bez flagi skrypt tylko raportuje). Inaczej pada
`tests/test_skillsync_core_en.py` (`translation-stale`, `translation-structure`). Nowy plik `.md` w skillu, który ma przekład, musi dostać przekład (skill w warstwie
jest kompletny). Opis i reguły: `docs/domain/layers.md`, „Warstwa tłumaczenia”.

### Zestaw modułów przy scalaniu

`python3 scripts/merge_modules.py --base <baza> --head <sha> [--json | --names]` wylicza z diffu `<baza>..<sha>` moduły testowe do uruchomienia
przy scalaniu, z uzasadnieniem na moduł. Reguły: zmieniony test; test importujący zmieniony moduł produktu, skryptu lub pomocnika
`tests/` (statycznie, bezpośrednio albo przez inne moduły); test nazywający zmieniony plik w napisie (uruchamia kod podprocesem);
literał komunikatu (min. 12 znaków i spacja, więc identyfikatory, ścieżki i przedrostki modułów się nie liczą; skutek uboczny: jednowyrazowe kody wyniku, np. `plan_not_found`, i komunikaty pocięte przez placeholdery na kawałki poniżej 12 znaków nie są wykrywane) z dodanego lub usuniętego wiersza zmienionego pliku produktu w teście albo w fiksturze `*.json` nazwanej w teście; zmieniona
fikstura i testy czytające `expected*` przy zmianie w `skillsync_workflow/`; zmiana plików, z których `link` generuje artefakty (`ARTEFACT_SOURCES`: generator `cli.py`, `layers.py`, `guardhook.py`, `common.py`, `selfcmd.py`, `fsutil.py`, `pathfmt.py`, `library/hooks|instructions|skills|agents`) dobiera testy, które uruchamiają `link` podprocesem i porównują jego wynik (`ARTEFACT_TESTS`: baseline w `layers_equivalence`, śledzone linki w `worktree_links`), bo nie nazywają żadnego pliku produktu; zmiana silnika prototypów (`library/skills/prototype/assets/repo/prototype.js`) albo jego kopii w `tests/fixtures/prototype/engine-v2/` dobiera `test_skillsync_prototype_engine` (reguła `engine`), który porównuje kopię ze źródłem tylko przy ustawionym `SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` (bez niego test świeżości się pomija), więc przy takim wyborze skrypt ostrzega na stderr; stały zestaw strażników (`GUARDS` w skrypcie). Przykład uruchomienia:
`python3 -m unittest $(python3 scripts/merge_modules.py --base <baza> --head <sha> --names)`. Analiza czyta drzewo robocze, więc checkout musi być `--head` (inaczej kod 2); diff liczony od merge-base. Usunięty lub przemianowany moduł produktu wybiera testy, które go importują albo nazywają. Zmiany samych dokumentów (`docs/`, `CHANGELOG.md`) wybierają tylko strażników, bo nie mają odwzorowania na moduły; ich treści pilnują strażnicy dokumentacji z `GUARDS`. Zestaw to dolna granica, nie zastępuje pełnego
biegu przed wydaniem; nie widzi importów dynamicznych ani poleceń znanych tylko z nazwy (np. `skillsync status` w napisie). Szeroko
importowany moduł (`cli.py`, `common.py`) daje szeroki zestaw, co jest zamierzone.

### Pomiary w dokumentacji

Tabela, która mówi „zmierzone”, ma kolumnę „Pomiar” z odesłaniem do zapisu w `docs/measurements/<id>.md` (pola: Data, System, Wersja,
Polecenie, Wynik; system w formie „Windows 11, Python 3.12.16”, bez nazw maszyn i osób). Wiersz bez zapisu jest oznaczony
„niezmierzone” albo „oczekiwane”. Pilnuje tego `tests/test_skillsync_measurements.py`. Nowy pomiar: dodaj zapis i odsyłacz w tabeli.

### Macierz przed pushem: ostrzeżenie o kodzie zależnym od systemu

`python3 scripts/platform_sensitive.py --base <baza> --head <sha> [--json]` czyta diff `<baza>...<sha>` i **ostrzega** (nigdy nie blokuje,
kod wyjścia 0; 2 tylko przy błędzie wejścia), gdy zmiana dotyka kodu zależnego od systemu (także samego hooka hosta: `library/hooks/`, `guardhook.py` wymagają dowodu z Windows i macOS modułu tego hooka przed scaleniem, kategoria `host-hook`): `os.name`/`sys.platform`, sygnały
(`signal.SIG*`, `CTRL_*_EVENT`, `os.killpg`, `start_new_session`), `ctypes`/`windll`, symlinki i `core.symlinks`, `rmtree`/usuwanie plików,
końce linii (`\r\n`, `newline=`, `splitlines`, `file_lines`), ścieżki z backslashem, opcje gniazd `SO_*` oraz `<katalog tmp> / <zmienna>` w
testach. Dla każdej kategorii podaje systemy, z których potrzebny jest dowód (windows/macos/linux), a na końcu moduły testowe do transzy
(ten sam zestaw co `merge_modules.py`). Dopasowanie jest tekstowe (docstringi pomijane po obu stronach diffu; usunięty plik jest skanowany po stronie usuniętej): to wskazówka, gdzie spojrzeć, nie wynik. Przed pushem
uruchom skrypt na swojej gałęzi i zdecyduj, które systemy sprawdzasz lokalnie, a które zostają dla CI; brak dowodu z systemu wpisz do „Wynik”
planu jako NOT_RUN.

### Nazwy z danych pod katalogiem tymczasowym

`tests/tmp_paths.py` (`child`, `make_dir`, `write_file`) składa ścieżkę pod katalogiem tymczasowym z nazwy pochodzącej z danych i odmawia
nazwy z separatorem, dyskiem, `..`, znakiem sterującym, końcową kropką lub spacją oraz nazwy zastrzeżonej Windows, także z rozszerzeniem
(`CON`, `NUL`, `COM1`, `nul.json`…); wynik sprawdza `relative_to`. Ścieżka względna z wieloma członami składa się przez `descend(base, "a/b")`
(bez `..`, bez ścieżki bezwzględnej, z wynikiem w bazie). Powód: na Windows nazwa `\n` (backslash, `n`) jest ścieżką od korzenia dysku, więc `tmp / nazwa` zapisuje poza
katalogiem tymczasowym. `tests/test_skillsync_tmp_path_guard.py` jest strażnikiem ścisłym (`STRICT = True`): każde wyrażenie
`<ścieżka tmp> / <zmienna lub wywołanie>` w testach (poza `tmp_paths.py` i samym strażnikiem) kończy test błędem, chyba że stoi w `ALLOWED`.
Klucz wpisu to `(plik, tekst wyrażenia z ast.unparse)`, wartość to uzasadnienie; wpis bez trafienia też jest błędem (test „stale”).
Strażnik rozpoznaje ścieżkę tmp po nazwie (`tmp`, `temp`, `tmp_dir`…) i pochodzeniu z `tempfile` (`mkdtemp`, `TemporaryDirectory`, `with … as nazwa`)
albo z innej ścieżki tmp. Zasięg skażenia: nazwa lokalna jest tmp w całej funkcji, która ją przypisuje (kolejność instrukcji bez znaczenia),
oraz w funkcjach zagnieżdżonych, lambdach i składaniach; nazwa modułowa w całym module; nazwa z ciała klasy w samym ciele klasy (nie w metodach).
Atrybuty (`self.root`) i każda skażona nazwa tworzą wspólny zbiór `attrs`, który działa w całym pliku: dla wyrażeń z atrybutem
(fikstura w jednej klasie obsługuje mixin w innej) i dla parametrów (parametr jest tmp, gdy w pliku jest skażona nazwa o tej samej nazwie, np. pomocnik
`write(root, rel)`). Przy skanowaniu repozytorium strażnik buduje najpierw rejestr klas ze wszystkich `tests/**/*.py` (klasa → atrybuty `self.x`
skażone w jej metodach i w jej ciele oraz nazwy baz). Podklasa dziedziczy atrybuty skażone klas bazowych z innych plików: po prostej nazwie
klasy (`Name` albo ostatni człon `modul.Klasa`), przechodnio przez bazy baz; kolizja nazw klas z różnych plików daje sumę atrybutów i baz,
a cykl dziedziczenia nie zapętla skanu. Odziedziczony atrybut działa tylko dla wyrażeń z atrybutem w zasięgu klasy podrzędnej i jej podklas.
Cel pochodny od atrybutu odziedziczonego (`self.out = self.work / "o"`, `base = self.work / "x"`) ma zasięg: nazwa w funkcji, atrybut w klasie;
nie wchodzi do wspólnego zbioru `attrs` pliku ani do rejestru. `scan(tree)` bez rejestru nie dziedziczy (kontrakt dla
`scripts/platform_sensitive.py`). Strażnik nie widzi przepływu przez wywołania (`self.root = make_project(self.tmp)`), fabryk, `global`/`nonlocal`,
`joinpath`, `os.path.join` ani `Path(a, b)`; atrybut o tej samej nazwie w niezwiązanej klasie skazi cały plik. Wyrażenie `X.name` jest ścieżką tmp, gdy `X` jest nazwą lub atrybutem
związanym z `TemporaryDirectory(...)` (np. `temp = TemporaryDirectory(...)`); stąd `self.root = Path(temp.name).resolve()` w klasie bazowej
`IsolatedTestCase` (`tests/layers_helpers.py`) skaża `self.root` i `self.work` w podklasach z innych plików. `NamedTemporaryFile` i `mkdtemp`
nie dają takiego `.name`, a `Path(x).name` jest tylko nazwą składnika. Złączenia `os.path.join(x.name, …)` i f-stringi z `.name` nie są
wykrywane, podobnie jak obiekt `TemporaryDirectory` podany jako argument innego wywołania (`x = wrap(TemporaryDirectory())`, `self.enterContext(TemporaryDirectory())`) (znane ograniczenia). Klasy rozpoznaje prosta nazwa, bez rozwiązywania importów. Nowy test, który składa ścieżkę z danych,
używa pomocnika. Trafienie naprawia się domyślnie (`child` dla jednej nazwy, `descend` dla ścieżki z `/`, w helperze u źródła; stała, liczba lub pętla po nie-literale też jest naprawą, nie wyjątkiem). Wpis do `ALLOWED` jest wyjątkiem i wolno go dodać tylko wtedy, gdy WSZYSTKIE wystąpienia klucza (plik, tekst wyrażenia) spełniają jeden z warunków: (a) nazwy są celowo wrogie i test jest zabezpieczony przed Windows albo `child` odrzuciłby dokładnie to, co test sprawdza, (b) prawa strona jest wynikiem walidatora z `tmp_paths` lub testu, nie kodu produktu, (c) `descend` zmieniłby semantykę testu; uzasadnienie wpisu wskazuje dowód dla każdego wystąpienia. Gdy wpisów robi się więcej niż kilkanaście, reguła skanera jest za szeroka i trzeba ją przeanalizować, zamiast dopisywać.

### Serwery w testach

Testy serwerów łączą się po adresie liczbowym (`127.0.0.1`), bez rozwiązywania nazw: bez `localhost` w `create_connection`, `urlopen` i `HTTPConnection`, a gniazdo klienta w testach niskiego poziomu otwieranie przez `connect(('127.0.0.1', port))`. Serwer testowy nie woła `socket.getfqdn` (podklasa `ThreadingHTTPServer` z `server_bind` bez odwrotnego DNS, jak serwery produkcyjne). Na runnerze macOS rozwiązywanie nazw potrafi sporadycznie stanąć na ok. 35 s, a pomiary czasu w testach mierzymy po stronie serwera, nie po stronie klienta.

Port z `free_port()` był wolny tylko w chwili próby. Serwer w wątku binduje przez `tests/serve_start.py:bind_free(fabryka, free_port)` (ponowienie na kolejnym porcie przy „adres zajęty”), podproces `dashboard serve` startuje przez `start_serve` (gotowość dopiero po pliku stanu własnego serwera; ponowienie tylko po utraconym bindzie) i zatrzymuje się przez `stop_serve`. Na Windows produkcja obsługuje tylko SIGTERM, więc `stop_serve` zabija drzewo procesu (`taskkill /T` po własnym PID) i zwraca `graceful=False`: testy sprawdzają wtedy start i odpowiedzi, a asercje łagodnego stopu (kod 0, raport, usunięty plik stanu) tylko na POSIX. Ponowienie po utraconym bindzie (`lost_bind`) rozpoznaje polskie słowo „nasłuchiwać” w komunikacie produkcji po zdekodowaniu stdout: bieg Windows bez `PYTHONUTF8=1` (CI go ustawia) może je zdekodować błędnie i wyłączyć ponowienie (start wtedy pada od razu: bezpiecznie, ale nieoczywiście).

### Zmienne testów

| Zmienna | Skutek |
|---|---|
| `SKILLSYNC_TEST_LIBRARY` | katalog warstwy `core` z szablonami i skillami (`library/` w repo); część testów pomija się bez niej |
| `SKILLSYNC_TEST_NODE` | ścieżka do Node ≥ 18 (testy silnika prototypów) |
| `SKILLSYNC_E2E_CHROME` | binarka Chrome/Chromium dla testów E2E; bez niej przypadki z przeglądarką się pomijają |
| `SKILLSYNC_TEST_DASHBOARD_PORTS` | zakres portów testów dashboardu, `<od>-<do>` w granicach 5100-5199 (domyślnie 5101-5199 poza 5140): osobny wycinek na bieg pozwala puścić kilka biegów testów naraz na jednym hoście (inaczej `free_port` zgłasza „brak wolnego portu”) |
| `SKILLSYNC_TEST_CLI_SUBPROCESS` | `1` wymusza podprocesy w testach, które wołają CLI w procesie przez `tests/cli_inprocess.py` (dziś `test_skillsync_retro_scan.py`): porównanie obu trybów i pomiar; bez zmiennej te testy oszczędzają start interpretera |
| `SKILLSYNC_E2E_PORTS` | porty E2E `<chrome>,<serwer1>,<serwer2>` (domyślnie `9333,4445,4446`) |
| `SKILLSYNC_E2E_RESULTS_DIR`, `SKILLSYNC_E2E_ONLY` | katalog wyników JSON; lista ID scenariuszy (tylko do debugowania) |
| `SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH` | plik silnika prototypów ładowany przez testy modułu silnika (domyślnie kopia z `tests/fixtures/prototype/engine-v2/`) |
| `SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` | źródło silnika w warstwie `core`; test sprawdza świeżość kopii |
| `SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1` | jawne pominięcie kontroli świeżości (gdy `V2_PATH` wskazuje inny silnik niż kopia) |
| `SKILLSYNC_PROTOTYPE_NOW` | wstrzyknięty zegar `prototype init` (pole `created`); tylko testy |
| `SKILLSYNC_TEST_DOC_TOPICS` | plik `tematy-dokumentow.md` dla testu tematów (opcjonalny; bez niego test czyta plik z `docs/prototype-contract/`) |
| `SKILLSYNC_CONFIG` | config maszyny; w testach ustawiaj konfigurację słownikiem `config`, a nie zmienną po imporcie |

Kompletny bieg prototypów kończy się bez skipów: wymaga Node, `SKILLSYNC_TEST_LIBRARY`, `SKILLSYNC_E2E_CHROME` i node ≥ 22 dla E2E.
Ciężkie biegi uruchamiaj pod limitem pamięci, np. `systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0`.

### Katalog tymczasowy

Testy tworzą repozytoria git w katalogu tymczasowym. Gdy `TMPDIR` leży w repozytorium, test sprawdzający `map` poza repo git nie przechodzi.
Ustaw prywatny katalog tymczasowy spoza repo, a nie współdzielone `/tmp`:

```bash
export TMPDIR="$(mktemp -d -p <katalog spoza repo>)"
```

`TMPDIR` ustawiają same testy: `tests/test_skillsync_leakguard.py`, `tests/test_skillsync_closure.py`,
`tests/test_skillsync_prototype_server.py` i `tests/e2e/prototype/env.py`.

### Środowisko E2E prototypu (`tests/e2e/prototype/env.py`)

`python3 env.py prepare|chrome|stop` przygotowuje projekt testowy, uruchamia Chrome w trybie headless i sprząta po biegu.
Sprzątanie kończy wyłącznie własne procesy: te z plików `*.pid` w katalogu roboczym i ich potomków wypisanych przed pierwszym
sygnałem, każdy po tożsamości (pid, czas startu, właściciel), nigdy po nazwie. Plik PID znika dopiero po potwierdzonej śmierci
procesu; nieczytelny plik jest usuwany z komunikatem na stderr. Plik z samym numerem i nazwą spoza `chrome*`, `static-*`, `serve-*`
zostaje na miejscu z komunikatem i nic nie jest wtedy sygnalizowane. Czas startu z `ps` (`lstart`, czas lokalny) jest rozstrzygany
przez `etime`, więc godzina zmiany czasu nie przesuwa go o godzinę. Windows nie jest obsługiwany: każde polecenie kończy się od razu
komunikatem `BLOCKED` i kodem wyjścia 2, bez startu Chrome.

### CI

Workflow `ci` (`.github/workflows/ci.yml`) ma zadania:

- `test (<system>, <python>, <tryb>)`: systemy `ubuntu-latest`, `windows-latest`, `macos-latest` × Python 3.11, 3.12, 3.13 i 3.14 × tryby `symlink`, `copy`
  (`SKILLSYNC_NO_SYMLINK=1`). Windows biegnie tylko w trybie kopii i bez 3.14, Python 3.14 tylko na Ubuntu. Zadania Windows i Python 3.14 mają
  `continue-on-error` (widoczne w macierzy, nie blokują); blokują Ubuntu i macOS na Pythonie 3.11-3.13. Limity czasu: Ubuntu 90, macOS 90, Windows 120 minut.
  Biblioteka testowa to `library/` z checkoutu, silnik prototypu z `library/skills/prototype/assets/repo/prototype.js`. Kroki: składnia (`py_compile`, `bash -n`),
  `tests/k1_grep.py` (K1), kontrola K5 (zakaz ścieżki obok pakietu poza `selfcmd.py`: użyj `selfcmd.self_argv()`), zależności `ui-verify` (npm),
  testy jednostkowe, skipy wobec `tests/skip-baseline.json` (`scripts/check_skips.py`) i smoke (`scripts/smoke.sh`, na Windows `scripts/smoke.ps1`);
- `install-smoke (<system>)`: budowa koła (`uv build`), `scripts/check_wheel.py`, instalacja jako narzędzie i `skillsync --version`, `--help`, `leakcheck`, `self_argv` poza checkoutem, `install.sh`;
- `e2e (ubuntu-latest)`: E2E prototypu i `ui-verify` w Chrome (pominięty test jest błędem zadania);
- `release-check`: `scripts/check_release.py` (sekcja CHANGELOG dla `__version__`; w PR tylko gdy zmienia `__version__`);
- `leakcheck`: strażnik na drzewie roboczym i commitach PR-a; działa tylko w pull requestach repo `BATUSystems/skillsync`.

Workflow `release-smoke` (`.github/workflows/release-smoke.yml`) po tagu `v*` sprawdza CHANGELOG względem tagu i instalację z tagu na trzech systemach.

## Teksty interfejsu (i18n)

Teksty strony statusu, serwera prototypów i komunikatów leżą w katalogu `skillsync_workflow/data/i18n/`: `<język>.json` (np. `pl.json`, `en.json`)
oraz fragmenty `<język>.<przestrzeń>.json` (np. `pl.proto.json`); ten sam klucz w dwóch plikach jednego języka to błąd ładowania (`skillsync_workflow/i18n.py`).
Dwa miejsca trzymają wygenerowane kopie katalogu i nie edytuje się ich ręcznie:

```bash
python3 scripts/sync_renderer_defaults.py [--check]   # blok DEFAULT_TEXTS w library/skills/dashboard/assets/dashboard-render.js (z pl.json)
python3 scripts/sync_proto_i18n.py [--check]          # blok proto-i18n w skillsync_workflow/prototype_server.py (z <język>.proto.json)
```

`--check` nie zapisuje niczego i kończy się kodem 1 przy rozjeździe; pilnują tego testy `tests/test_skillsync_dashboard_renderer_i18n.py`
i `tests/test_skillsync_proto_i18n.py`. Reguły i granice: [domain/i18n-ui](domain/i18n-ui.md).

## Próba na projekcie testowym

Nigdy nie sprawdzaj nowego zachowania `link` najpierw na prawdziwym projekcie. Receptura z izolowanym configiem (nic nie jest zapisywane):

```bash
tmp=$(mktemp -d); mkdir -p "$tmp/proj/.github"
cat > "$tmp/config.yaml" <<EOF
layers:
  - {id: core, kind: core, path: <ścieżka do checkoutu repo>, subdir: library}
layer_rules:
  - match: ["**"]
    use: [core]
discover_paths: []
python_command: python3
EOF
cat > "$tmp/proj/.github/.skillsync.yaml" <<EOF
version: 1
project: {name: Org/Projekt, type: local}
skills: [feature]
instructions: {merge: [_base], template: Org-Projekt}
sync: {method: symlink}
tests: {command: "make test"}
EOF
SKILLSYNC_CONFIG="$tmp/config.yaml" ./skillsync link "$tmp/proj" --dry-run
SKILLSYNC_CONFIG="$tmp/config.yaml" ./skillsync instructions "$tmp/proj" --dry-run
```

Prawdziwy `link` na projekcie testowym (też w `$tmp`) pokazuje pełny efekt. Potem `SKILLSYNC_CONFIG=... ./skillsync doctor "$tmp/proj"`
powinien dać PASS. Przed pierwszym `link` doctor zwraca FAIL z powodu braku zainstalowanych skilli i `CLAUDE.md`; to oczekiwane.
`skillsync link` bez ścieżki działa na wszystkich projektach z `discover_paths`.

W recepturze warstwa ma `path` (istniejący checkout); sam `source` bez `layers add` daje warstwę nieobecną
(`[info] Layer core is not present`).

## Tryb symlink i kopia

- Domyślnie generator tworzy dowiązania. `SKILLSYNC_NO_SYMLINK=1` albo `sync.method: copy` wymuszają kopie ze stanem w `.github/.skillsync-state.json`.
- Testy na obu trybach: dowiązania przypinane do trybu symlink, odpowiedniki kopii do trybu copy (zob. sekcja „Skipy”).
- Przed zmianą logiki linkowania uruchom testy w obu trybach.

## Usługa watch

- Usługa `skillsync-watch.service` (Linux, systemd user) uruchamia `watch` i trzyma kod z chwili startu. Po zmianie generatora: `skillsync-ctl restart`.
- Plik unitu leży poza repo, w katalogu użytkownika systemd; `ExecStart` wskazuje dowiązanie `skillsync` do watch.
- Watch linkuje automatycznie przy każdej zmianie w warstwach. Przy wieloetapowej zmianie (rename skilla, nowa wersja protokołu) zatrzymaj usługę
  (`skillsync-ctl stop`), wykonaj zmiany, sprawdź `link --dry-run`, potem `skillsync-ctl start`.
- Stan i logi: `skillsync-ctl status`, `skillsync-ctl logs`.

Na macOS i Windows usługi watch nie ma; `skillsync watch` uruchamia się tam ręcznie.

## Windows

- Skopiuj razem `skillsync`, katalog `skillsync_workflow/` i `skillsync-windows.ps1`. Sam `skillsync` bez pakietu nie działa (to wrapper).
- Bootstrapper `skillsync-windows.ps1` z Pythonem 3 i plikiem `skillsync` obok woła `skillsync link <projekt>` i tworzy config, jeśli go brak.
- Nieudany `git pull` warstwy nie przerywa synchronizacji, ale skrypt kończy się kodem 3 i ostrzeżeniem o starych artefaktach. Nieudany `git clone` daje kod 1.
- Bez Developer Mode dowiązania nie działają: generator przechodzi w tryb kopii; wymusisz go `SKILLSYNC_NO_SYMLINK=1` lub `sync.method: copy`.
- Ścieżki w wyjściach JSON i w zapisywanym stanie przechodzą przez `.as_posix()` (nie `str(relative_to(...))`); wywołanie `redact` na kluczu słownika przekazuje `True` (próg sekretu z env: 8 znaków dla kluczy i tekstu). Szczegóły: `docs/lessons/windows-sciezki-json-i-redact-kluczy.md`.
- Test środowiska: `tests/windows/skillsync-windows-check.ps1` (raport `[PASS]/[FAIL]/[SKIP]/[INFO]`, praca na kopii w katalogu tymczasowym).
  Uruchomienie z korzenia repo: `powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\windows\skillsync-windows-check.ps1`.
- Windows nie ma backendu własności procesów: `env up` i `env down` zwracają BLOCKED.

Zakres obsługi Windows to tryb kopii, CLI, `link` i `doctor` (bez usługi, bez `flock` i TTY), zgodnie z tabelą wsparcia. Windows i macOS są w macierzy CI (zob. „CI”); na Windows zadanie testowe nie blokuje scalenia, więc dowód z Windows zbierz własnym biegiem (`tests/windows/skillsync-windows-check.ps1`, `scripts/windows_shim_check.py`).

## Reguły zmian w kodzie

- **Rozwijanie skilli ma dwie implementacje** (`skillsync_workflow/cli.py:expand_skills` i `skillsync_workflow/common.py:expand_project_skills`) oraz zduplikowane stałe. Zmieniaj obie; `tests/test_skillsync_dispatch.py` pilnuje zgodności.
  `skillsync_workflow/cli.py` importuje inne moduły pakietu tolerancyjnie, wewnątrz funkcji (`except ModuleNotFoundError`,
  np. `_layers_module`, `expand_skills`); nowy import z pakietu w `cli.py` rób według tego wzorca, nie na poziomie modułu.
- **Nowa komenda workflow:** moduł w `skillsync_workflow/` z `register(subparsers)` i handlerem zwracającym kod wyjścia (`emit(report)`); wpis w `register_subcommands`; `help` komendy, jej flag i `description` (grupy także) po angielsku, w ASCII; wpis w `EXIT_CLASS` w `skillsync_workflow/cli_help.py` (klasę kodu potwierdź w handlerze, nie z pamięci); testy; opis w [cli](cli.md) (wiersz w spisie i sekcja).
- **Komenda synchronizacji:** parser w `skillsync_workflow/cli.py:main` i funkcja `cmd_*` w tym samym pliku; `help` każdej flagi i `help` komendy, wpis w `EXIT_CLASS` (`cli_help.py`), wiersz w spisie i sekcja w [cli](cli.md).

- **Nowy artefakt hosta:** builder wywoływany w `link_project`, obsługa `--dry-run`, reguła własności (nie nadpisuj ręcznych plików), wpisy gitignore, sprzątanie osieroconych plików, testy i opis w [architecture](architecture.md).
- Komendy wykonujące polecenia (`--run`) pobierają je tylko z zaufanego manifestu lub warstwy. Ścieżki stanu przechodzą przez kontrolę `storage.py:state_path`
  (bez dowiązań i wyjścia z projektu). Wynik przechodzi przez `common.py:redact`.
- **Nowe polecenie z dwoma argumentami pozycyjnymi albo opcjonalnym pozycyjnym** (`nargs='?'`/`'*'`): argparse Pythona przed 3.12 dopasowuje opcjonalny pozycyjny pusto przed opcjami, więc `polecenie <a> --json <b>` kończyło się `unrecognized arguments`. Parsery liściowe rdzenia czytają argumenty przemieszane (`skillsync_workflow/argtools.py:IntermixedParser`, włączane w `cli.main` tylko na Pythonie < 3.12; od 3.12 nic się nie zmienia). Nie używaj w takim poleceniu `nargs=REMAINDER` ani grupy wzajemnie wykluczającej z argumentem pozycyjnym (parser zostaje wtedy standardowy, a `tests/test_skillsync_argparse_positionals.py` zgłasza polecenie). Tabela poleceń w tym teście jest generowana z definicji parserów; dowód na 3.11: `python3.11 -m unittest tests.test_skillsync_argparse_positionals` musi przejść.
- Po zmianie kontraktu (nazwy skilli, ścieżki artefaktów, klucze manifestu, kody wyjścia): testy oraz aktualizacja [cli](cli.md), [manifest](manifest.md),
  [architecture](architecture.md) i `skills/skillsync/SKILL.md` w warstwie `core`.
- Zmiana `docs/upgrading.md`: nagłówki `## Przejście …` muszą być unikalne, a sekcja przejścia na bieżącą wersję generatora musi istnieć; pilnuje tego `tests/test_skillsync_upgrading_headings.py`.
- Istotna zmiana generatora lub CLI: wpis w `CHANGELOG.md` w sekcji `[Unreleased]`. Zmiana sekcji **Breaking** wymaga opisu zmiany niezgodnej.

### Help i kody wyjścia w `--help`

`tests/test_skillsync_cli_help_guard.py` sprawdza `skillsync --help` na całym drzewie parsera. Wspólny odczyt drzewa jest w
`tests/parser_tree.py`: to jedyne miejsce testów, które używa prywatnego API argparse. Reguły:

- G1: każdy węzeł i każdy argument ma `help`. G2: każda komenda liściowa i grupa ma `description`.
- G3: każda komenda liściowa ma wpis w `EXIT_CLASS`, a jej epilog `Exit codes:` podaje dokładnie kody klasy (tabela klas: [statuses-and-exit-codes](domain/statuses-and-exit-codes.md)). G3b uruchamia naprawdę reprezentantów klas. Znana granica: `serve` nie jest uruchamiany do kodu 0.
- G4: domyślna wartość jest w wyrenderowanym tekście `help`.
- G5: każda komenda najwyższego poziomu jest w tabeli „Spis komend” w `docs/cli.md` z poprawnym linkiem i kotwicą.
- G6: flagi wymagane występują w opisie komendy w `docs/cli.md`, a flagi wymienione obok komendy istnieją w jej parserze (uproszczenie: jednostka tekstu zakotwiczona w komendzie).
- G7: teksty są po angielsku, w czystym ASCII.
- G8: wyjątki od G1–G7 (zob. niżej). G9: pełny rendering `--help` przy szerokości 80 i 200 kolumn.

Tekst `EXIT_CLASS`, `EXIT_CLASSES` i `CUSTOM_EPILOG` jest w `skillsync_workflow/cli_help.py`. Epilog ustawia `finish(parser)`, które `cli.main` wywołuje raz po zarejestrowaniu wszystkich komend.

Wyjątki: `tests/data/cli_help_exceptions/<paczka>.yaml` (paczki to litery reguł, np. `G.yaml`). Każdy wpis ma klucz `path` (ścieżka komendy), `rule` i `dest` oraz niepusty `reason`. Wpis, który nie pasuje już do żadnego znaleziska, jest błędem: usuń go, gdy luka jest usunięta. Górna granica `MAX_EXCEPTIONS` w strażniku tylko spada. Nowy wyjątek dopisuje się tylko z uzasadnieniem, a najpierw poprawia się tekst.

Uruchomienie:

```bash
python3 -m unittest discover -s tests -p test_skillsync_cli_help_guard.py
```

## Kontrola przed commitem

```bash
python3 -m py_compile skillsync skillsync_workflow/*.py
bash -n skillsync-ctl
python3 -m unittest discover -s tests -p 'test_skillsync*.py'
```

Dla zmiany linkowania dodatkowo: `link --dry-run` na projekcie testowym (receptura wyżej).

**Rdzeń nie przechowuje specyfikacji ani planów zadań.** Repozytorium jest publiczne: pliki pod `docs/specs/` i `docs/plans/` nie są śledzone, a w plikach poza `tests/` nie wskazujemy konkretnych (datowanych) specyfikacji ani planów; piszemy opis bez ścieżki. Pilnuje tego `tests/test_skillsync_repo_hygiene.py` (w CI na każdym PR). Konwencja katalogów projektu użytkownika (`docs/specs/*.md`, `docs/plans/*.md`), z której korzysta generator, pozostaje opisana normalnie.

Rdzeń ignoruje `/docs/specs/` i `/docs/plans/` w korzeniowym `.gitignore` (wpis zakotwiczony, więc fikstury `tests/fixtures/**/input/docs/{specs,plans}/` pozostają śledzone i nieignorowane). Strażnik higieny (`test_skillsync_repo_hygiene.py`) sprawdza to przez `git check-ignore --no-index -v` i wywołuje `specs_info(REPO, {})`, które musi dać `tracked: false`, `source: gitignore`. Testy `check-ignore` używają środowiska z `skillsync_workflow/specs.py` (`_git_env`: bez zmiennych `GIT_*`, `HOME` i `XDG_CONFIG_HOME` wskazują na nieistniejący katalog, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`), żeby wynik nie zależał od konfiguracji maszyny. Zmiana wpisu w `.gitignore` wymaga aktualizacji tego testu i tej sekcji.

## Strażnik wycieków na maszynie dewelopera

Pełne sprawdzenie (`skillsync leakcheck . --require full`) potrzebuje plików tożsamości innych celów niż publiczny rdzeń.
Strażnik w hooku i w CI patrzy na zakres zmian (`--range`, `--staged`, tekst PR-a), więc plik dodany, zanim jego termin trafił do plików tożsamości, wychodzi dopiero przy skanie całości. Przed wydaniem uruchom `skillsync leakcheck . --target core --worktree --require full` i `skillsync leakcheck . --target core --history` (pełny skan drzewa i historii, na maszynie z plikami tożsamości).
Nie trzymamy ich w tym repo: wskaż katalog w configu maszyny (`~/.config/skillsync/config.yaml`, albo ścieżka z `SKILLSYNC_CONFIG`):

```yaml
leakguard:
  identity_dir: <katalog z plikami tożsamości>/leakguard
```

Z ustawionym `identity_dir` `--require full` działa bez osobnego configu pomocniczego. Szczegóły pola: [configuration](configuration.md#strażnik-wycieków-leakguard).
`skillsync layers check --isolation` szuka plików tożsamości w katalogu biblioteki warstwy (korzeń `layer.yaml`), nie w całym klonie. Pliki w `leakguard.identity_dir` (tożsamości wszystkich celów maszyny, także tych, które nie są warstwami, np. repo wtyczki) są pomijane, gdy ich właściciel jest skonfigurowanym `leakguard.targets`; właściciel spoza `targets` i spoza `isolation.allowed_layers` nadal daje `isolation-foreign-identity`, a ta sama tożsamość w bibliotece warstwy też.

Po sklonowaniu repo do rozwoju zainstaluj hooki strażnika: `skillsync leakguard install-hook <klon>`. Worktree dzielą `.git/hooks` klonu, więc jedna instalacja obejmuje wszystkie; bez niej push nie przechodzi przez strażnika (przy `core.hooksPath` poza `.git` komenda odmówi i trzeba wołać `leakcheck` ręcznie). `skillsync leakcheck` w katalogu roboczym klonu celu `public` ostrzega o braku hooków (ostrzeżenie, nie błąd) i podaje polecenie instalacji.

Treść tytułu i opisu każdego PR do publicznego rdzenia skanuje workflow `.github/workflows/pr-text.yml` (job `pr-text (leakcheck)`, `skillsync leakcheck --text`, narzędzie i dodatki `.leakguard/patterns.yaml` z gałęzi bazowej; uruchamia się też po edycji opisu, bez macierzy testów). To nie jest wymagany check rulesetu (jego dodanie jest osobną decyzją o konfiguracji repozytorium); definicja workflow pochodzi z merge ref PR-a, więc job działa już w PR-ze, który go wprowadza, a narzędzie z bazy musi mieć `--text` (1.3.0+).

## Flagi dla narzędzi integracyjnych

Strażnik ma flagi jawnych wejść dla hooków i narzędzi integracyjnych, nie do pracy ręcznej (działają, ale `--help`
ich nie pokazuje): `leakcheck` i `leakguard install-hook` mają `--identity`, `--targets-json` i `--trailer-allow`,
a `leakcheck` i `leakguard patterns` mają `--target-patterns`. W `leakcheck` każda z flag `--identity`, `--targets-json`, `--trailer-allow` zastępuje wejścia
z configu maszyny w całości: bez `--targets-json` cel jest nieznany, więc `--require full` kończy się BLOCKED.

## Eval zachowania skilli

`tools/claude-eval/run.py` jest jedynym wejściem do `claude plugin eval` dla skilli biblioteki; bezpośrednie wywołanie `claude plugin eval` na bibliotece nie jest wspierane. Sprawdza, czy model z wtyczką wykonuje to, co skill zapisuje (np. czyta referencję `ticket-intake` przy kluczu zgłoszenia), względem ramienia bez wtyczki. Każdy przebieg to prawdziwe wywołanie modelu na koncie użytkownika, więc ma koszt i limit. Zalecane jest osobne konto przeznaczone do testów na żywo; wymagany jest tylko `--config-dir`.

```
python3 tools/claude-eval/run.py --model haiku --max-cost-usd 0.5 --config-dir /bezwzględna/ścieżka/profilu [--runs 1..3] [--case glob] [--out katalog] [--dry-run]
```

- Wymagane: `--model` (prosty identyfikator), `--max-cost-usd` (większy od 0, najwyżej 5.0) i `--config-dir` (istniejący katalog bezwzględny bez `..`; profil Claude, z którego idzie koszt). Brak któregokolwiek kończy się odmową przed startem.
- Wymuszone: `--no-publish`, `--trust-plugin` wyłącznie dla skopiowanego katalogu, `--concurrency 1`, `--ablation with-without`. Nigdy: `--scaffold`, `--allow-tools`, `--allow-real-servers`, `--publish-report`, mocki.
- Środowisko dziecka: tylko `PATH`, `HOME`, `LANG`, `LC_*` i `CLAUDE_CONFIG_DIR` z argumentu; katalog roboczy to pusty katalog tymczasowy poza repozytorium. `--dry-run` drukuje polecenie i środowisko bez uruchamiania.
- Biblioteka nie jest dotykana: skille `feature`, `bugfix`, `pre-implement`, `workflow` (oraz `dispatch`, gdy ma `disable-model-invocation`) trafiają jako kopie do `<out>/stage/plugin` razem z `.claude-plugin/plugin.json`; z przypadków kopiowane są tylko `prompt.md` i `graders/*.md`, a po skopiowaniu przypadki są walidowane ponownie. Dowiązania i twarde linki są odrzucane.
- Przypadek (`evals/<nazwa>/prompt.md` + `graders/*.md`) ma zamkniętą listę kluczy (`description`, `tags`, `runs` do 3, `max_turns` do 10, `timeout_seconds` do 300, `allowed_tools` tylko `Read`, `Glob`, `Grep`, `Skill`) i graderów deterministycznych (`regex`, `tool_used`, `tool_order`, `file_exists`). W `tool_used` pole `min` ma domyślnie 1, więc kontrola „nie wywołano” wymaga `min: 0` i `max: 0`.
- Opakowanie zawsze przekazuje `--runs` (domyślnie 3), a `claude plugin eval` daje mu pierwszeństwo, więc `runs` z `prompt.md` jest ignorowane. Domyślne `--out` to `<katalog bieżący>/.skillsync/claude-eval/<czas>`; wynik: `<out>/aggregate.json`, `<out>/report.html`. Kody wyjścia 1 (wynik poniżej progu) i 2 (przerwanie limitem kosztu) pochodzą z `plugin eval` i są przekazywane bez zmian; opakowanie samo zwraca 2 przy błędzie przypadków, a odmowa argumentów kończy się kodem 2 z argparse. Flagi `--claude-bin`, `--evals-dir` i `--library` służą testom.
- Opis, kryteria adopcji i granice wniosków: [claude-plugin-eval](domain/claude-plugin-eval.md).
- Testy opakowania używają fałszywego `claude` (`--claude-bin`) i nie wywołują modelu: `tests/test_skillsync_claude_eval_wrapper.py`, `tests/test_skillsync_claude_eval_cases.py`. Prawdziwe wywołania wymagają zgody na koszt.

## Zadania nieinteraktywne (`claude -p`)

`tools/claude-headless/run.py` jest jedynym wejściem do uruchamiania prawdziwego `claude -p` na zadaniach **poza** pipeline'em `feature` (skill `feature` zabrania `claude -p` zamiast natywnego podumysła). Zadania są doradcze i tylko do odczytu; wynik nie jest bramką merge ani wydania. Opakowanie uruchamia człowiek (albo sesja główna na jego polecenie), nie podumysł w pipeline'ie `feature`.

```
python3 tools/claude-headless/run.py release-audit --tag-from vA --tag-to vB --model sonnet --max-cost-usd 1.5 --config-dir /bezwzględna/ścieżka/profilu [--max-turns N] [--timeout-seconds S] [--repo katalog] [--out katalog] [--dry-run]
python3 tools/claude-headless/run.py docs-drift --ref release/X.Y.Z --model sonnet --max-cost-usd 1.5 --config-dir /bezwzględna/ścieżka/profilu
```

- **Zadania.** `release-audit` (J1): spójność changelogów (`CHANGELOG.md`, `library/CHANGELOG.md`), numeru wersji i dokumentacji z diffem między dwoma tagami. `docs-drift` (J2): zgodność `docs/cli.md` z definicjami argparse na wskazanym refie. Prompt, schemat wyniku i opis wejść: `tools/claude-headless/jobs/<zadanie>/`.
- **Wymuszone flagi** (stała w kodzie, nie do nadpisania z linii poleceń): `-p --output-format json --no-session-persistence --permission-prompts none --restricted --tools Read,Grep,Glob --strict-mcp-config --disable-slash-commands`, `--json-schema` z treścią schematu, `--max-budget-usd` (limit wywołania, wymagany, najwyżej 5 USD), `--max-turns` (domyślnie 25, czyli wartość pomiarów poniżej; najwyżej 30), `--model`. Prompt idzie przez stdin. Nigdy `--bare` (wymaga klucza API, a pracujemy na subskrypcji), `--dangerously-skip-permissions` ani narzędzia zapisujące. `--restricted` jest podstawą: `--setting-sources user` samo ładuje ustawienia profilu, w tym jego hooki, wtyczki i reguły `permissions.allow`.
- **Dane.** Zadanie czyta kopię **publicznej części** rdzenia zbudowaną z tagu albo gałęzi (`git archive` z allowlistą: `CHANGELOG.md`, `README.md`, `docs/*.md`, `docs/domain`, `docs/lessons`, `library`, `skillsync_workflow`, `skillsync`, `skillsync-ctl`), nigdy żywego drzewa roboczego, repozytoriów spoza rdzenia, warstw org i personal, `.skillsync/`, `docs/plans` ani `docs/specs`. Po zbudowaniu kopii skan odmawia startu, gdy cokolwiek spoza allowlisty zostało; dowiązania i hardlinki nie są kopiowane. Dla `release-audit` host dokłada `_audit/diff.patch` (pełny patch changelogów i `docs/*.md`) i `_audit/diffstat.txt` (statystyki reszty); rozmiar tych plików jest ograniczony do 400 KB.
- **Konto i środowisko.** Profil (`--config-dir`) musi być wymieniony w pliku `~/.local/state/skillsync/claude-headless-profiles.txt` (jedna ścieżka bezwzględna na linię; domyślna odmowa, porównanie po rozwinięciu dowiązań); wpisz tam wyłącznie profil osobnego konta przeznaczonego do testów na żywo. Środowisko dziecka: `PATH`, `LANG`, `LC_*`, tymczasowy `HOME` i `CLAUDE_CONFIG_DIR` z argumentu; `cwd` w tymczasowej kopii. `--ledger` i `--profiles-file` można przekierować (testy tego potrzebują): to ochrona przed pomyłką, nie przed celowym obejściem.
- **Księga kosztów konta** (`~/.local/state/skillsync/claude-headless-ledger.json`, zapis pod blokadą pliku, atomowy): licznik wywołań i suma kosztów; łączny budżet i limit wywołań to stałe w kodzie (`tools/claude-headless/run.py`; `--total-budget-usd` może tylko obniżyć sumę). Start odmawia, gdy suma plus limit wywołania przekroczyłaby budżet. Budżet jest łączny dla konta, bez okna czasowego; odnawia go wyłącznie właściciel konta (ręczne przeniesienie pliku księgi). Koszt nieznany (wynik nie-JSON, NaN, brak pola) albo przerwanie liczy się jako limit wywołania.
- **Wynik.** `result.json` (pole `structured_output` sprawdzone własnym walidatorem podzbioru JSON Schema), `raw.json`, `stderr.txt`, `metadata.json` (wersja Claude Code, czas, koszt) w katalogu poza gitem (`.skillsync/claude-headless/<czas>-<pid>/`). Kody: 0 PASS; 1 zadanie nie powiodło się albo wynik niezgodny ze schematem (także brak `structured_output` przy sukcesie `claude`); 2 odmowa przed startem (argumenty, profil, księga, rozmiar wejścia, niepusty katalog wyniku); 3 przerwanie limitem kosztu.
- **Wspólny rdzeń** z opakowaniem `claude-eval`: `tools/claude-headless/claude_headless_core.py` (walidacja limitu kosztu, katalogu profilu i modelu, środowisko dziecka, czytnik frontmattera). Zmiana rdzenia nie zmienia zachowania `claude-eval` (test porównuje jego `--dry-run` z migawką).

**Granice zadań.** Zadania są doradcze: znaleziska traktuj jak podpowiedzi do sprawdzenia w pliku, nie jak werdykt, a narzędzie nie wchodzi do kontroli przed wydaniem. Zadanie widzi tylko to, co wynika z diffu i z niespójności dokumentów (dokument kontra kod, dokument kontra changelog), a nie braki praktyki (czego dokumentacja w ogóle nie opisuje). Typowe fałszywe alarmy to rutynowe podniesienie `core_min`, pliki spoza kopii i twierdzenia nieweryfikowalne z samego diffstat. Wywołania idą na wspólny limit konta, więc uruchamiaj je poza godzinami intensywnej pracy.

## Ręczna próba: pasek statusu i styl `karta`

Próbę wykonuje właściciel w sesji interaktywnej; agent jej nie uruchamia. Wymaga Claude Code ≥ 2.1.97. Używaj osobnego projektu testowego,
nie projektu produkcyjnego. Wynik (wersja Claude Code, CLI czy VS Code, co widać, zrzut tekstu paska) zapisz w „Wynik” planu wydania.
Nie blokuje wydania. Do czasu próby wynik wyświetlania w UI to `NOT_RUN` z powodem „brak sesji interaktywnej”.

1. W manifeście projektu testowego (`.github/.skillsync.yaml`) włącz klucze:
   ```yaml
   claude:
     statusline: true
     output_styles: true
   ```
2. `skillsync link <projekt-testowy> --dry-run`, potem `skillsync link <projekt-testowy>`. Oczekiwane: w `.claude/hooks/` jest `statusline.py`,
   w `.claude/output-styles/` jest `karta.md`, w `.claude/settings.local.json` jest klucz `statusLine` z `refreshInterval` 15.
   `skillsync doctor <projekt-testowy>` nie zgłasza `error`; ma `info` o wymaganiu 2.1.97 i o niepotwierdzonym VS Code, a przy `output_styles` także `info` o stylu `karta` (`/output-style karta`).
3. CLI: w katalogu projektu uruchom `claude`. Zaakceptuj zaufanie do katalogu (pasek działa dopiero po nim).
4. Nadaj nazwę sesji: `/rename pasek-proba`.
5. Pasek ma pokazać jedną linię, np. `sesja: pasek-proba | <projekt> | <gałąź> | <tryb> | plan <slug> N/M`. Segment bez danych
   jest pominięty. `plan` pojawia się tylko przy planie w statusie `in_progress`.
6. Styl: wpisz `/output-style karta` (wymaga ≥ 2.1.269) i zadaj pytanie o stan pracy. Odpowiedź ma format protokołu kart
   ([multi-session](../library/skills/feature/references/multi-session.md)): pierwsza linia `date: <wynik date>`, potem
   `STATUS <paczka> <akt> <czeka-na>` albo `REPORT DONE|BLOCKED <paczka> commit=… checks=… debt=… retro=…`; dowody z komendą
   i kodem wyjścia, a niewykonane kontrole jawnie jako `NOT_RUN`. Sam plik stylu niczego nie aktywuje; wybór zapisuje się
   w `.claude/settings.local.json` jako `outputStyle`.
7. Brak nazwy sesji (bez `/rename`): pasek nie pokazuje segmentu `sesja:`, reszta działa. To nie jest błąd. Brak paska w ogóle:
   uruchom `claude --debug` (stderr skryptu), sprawdź `skillsync doctor` i istnienie kopii skryptu. Cudzy pasek w jednym z plików
   ustawień zgłasza `doctor` z nazwą pliku.
8. VS Code: otwórz projekt w VS Code z rozszerzeniem Claude Code. Zapisz, czy pasek się pojawił, oraz wersję rozszerzenia.
   Picker stylów jest od 2.1.257. Brak paska w VS Code to wynik zgodny z dokumentacją, bo wyświetlanie tam jest niepotwierdzone.
9. Powrót: w manifeście ustaw `claude.statusline: false` i `claude.output_styles: false`, potem `skillsync link`. `link` usuwa
   wpis `statusLine` i kopię skryptu paska, jeśli są nasze (kopia zmieniona ręcznie znika razem z wpisem), oraz kopię stylu,
   jeśli jest nietknięta albo zgodna z biblioteką i nieśledzona przez git. Kopia stylu zmieniona ręcznie zostaje
   w `.claude/output-styles/` i usuwasz ją ręcznie. Klucz `outputStyle`, jeśli został zapisany przez `/output-style`,
   usuwasz ręcznie z `.claude/settings.local.json`.

## Pułapki

1. **`doctor` przed `link`**: FAIL (brak zainstalowanych skilli i instrukcji Claude). Po `link` ma być PASS. Same ostrzeżenia dają PASS (exit 0).
2. **Martwe wpisy gitignore po zmianie nazwy skilla**: `link` usuwa osierocone dowiązania (`[clean] Removed obsolete`), ale wpisy `skills/<stara>` w `.claude/.gitignore` i `.agents/.gitignore` zostają; są nieszkodliwe.
3. **Task folder-open** (`.vscode/tasks.json`): generator odświeża ścieżkę tylko w tasku o postaci, którą sam zapisał. Task niestandardowy o tej samej etykiecie zostaje bez zmian.
4. **Usługa watch** uruchamia kod z chwili startu; przy zmianach w wielu krokach zatrzymaj ją.
5. **Pliki generowane** (`CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `.codex/*`) nie są do edycji; zmieniaj warstwę i uruchom `link`, albo `push`.
6. **`.github/.gitignore` zawiera `.skillsync.yaml`**: manifest nie jest śledzony przez git (wymaga `git add -f`).
7. **`sync.library` w manifeście** jest ignorowane z ostrzeżeniem; dobór warstw jest tylko w configu maszyny.
8. **`instructions`** buduje też wszystkie artefakty Codexa (skille, role, `AGENTS.md`), nie tylko instrukcje.
9. **`add`** przepisuje YAML manifestu: traci komentarze i sortuje `skills`.
10. **Cursor**: przy włączonych skillach inne pliki `*.mdc` w `.cursor/rules/` są usuwane jako przestarzałe.
11. **`list`** w kolumnie opisu drukuje `---` (pierwsza linia `SKILL.md`).
12. **`checkpoint`** po awarii procesu może zostawić katalog `<run-id>.lock`; usuń go ręcznie po sprawdzeniu, że nic nie zapisuje.
13. **`smart-test`** wymaga, by projekt był korzeniem repo git; ścieżki wspólne i niezmapowane dają fallback do wszystkich testów (zamierzone).
14. **`SKILLSYNC_CONFIG`** jest czytane przy każdym użyciu (`cli.config_path()` → `layers.config_path()`), nie przy imporcie; test może ustawić je w fixture po zaimportowaniu `cli`.
15. **Zapis `tasks.json` przez generator** przepisuje plik jako czysty JSON (usuwa komentarze i CRLF); kopia zapasowa jest nadpisywana przy kolejnym odświeżeniu.
16. **Biegi na maszynach zdalnych** (Windows, macOS): logi kopiowane przyrostowo, sprawdzenie śmierci drzewa procesów po PID-ach przed usunięciem katalogów, jedna pula portów na proces, lista modułów bez aliasów powłoki, start w tle na macOS bez `setsid`: reguły w `library/skills/feature/references/prepush-matrix.md` (sekcja „Biegi na maszynie zdalnej”).
17. **PowerShell 5 (Windows)**: skrypt pomocniczy z polskimi znakami zapisany bez BOM bywa niepoprawnie odczytany przez parser; zapisuj z BOM albo bez polskich znaków. Wyjście natywnej komendy na stderr przy `2>&1` jest pokazywane jako błąd mimo kodu wyjścia 0: oceniaj `$LASTEXITCODE`, nie tekst.
