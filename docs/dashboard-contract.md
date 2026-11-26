# Kontrakt JSON dashboardu (`schema: 1`)

Dashboard to strona statusu projektu: ten sam stan, który widzi człowiek w przeglądarce, jest dostępny jako JSON.
Kontrakt jest wspólny dla renderera strony, innych konsumentów (np. IDE) i testów. Przykłady wejścia i oczekiwanego
wyniku: `tests/fixtures/dashboard/<przypadek>/input/` i `expected.json`.

## Komendy

| Komenda | Zachowanie |
|---|---|
| `skillsync dashboard show <projekt> [--json] [--plan <rel>]` | drukuje kontrakt `schema: 1` (JSON; bez `--json` ten sam JSON, formatowany); tylko odczyt, bez zapisu do projektu; `--plan` wybiera plan (ścieżka względna, np. `docs/plans/<plik>.md`), bez niego plan wybierany jest automatycznie (`planAuto: true`) |
| `skillsync dashboard serve <projekt> [--port N] [--read-port N --approve-port M] [--read-bind A] [--approve-bind A] [--host H] [--approve-host H] [--read-host H] [--standalone] [--all-tabs] [--json] […]` | serwer HTTP nad tym samym JSON (`/api/status`, strumień zmian `/events`, przyciski zgód); bez flag portów: dwa gniazda (odczyt 5140 na wszystkich interfejsach, zgody 5141 tylko na tej maszynie), `--port N`: jedno gniazdo, `--read-port`/`--approve-port`: jawne numery (sekcja „Serwer”); port wybiera się per projekt, by strony nie kolidowały; pełna składnia (m.in. `--plan`, `--no-approvals`, `--allow-proxy-headers`, `--prototype-port`, `--session-url`) w sekcji „Serwer” |
| `skillsync dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | ogólne metryki przebiegu: czas podkroków `Fn.k` z checkpointów, liczby rund i porażek; podkrok bez danych jest oznaczony jawnie (sekcja „Metryki przebiegu”) |
| `skillsync dashboard url <projekt> [--port N] [--host H] [--read] [--lan] [--all-tabs] [--json]` | drukuje adres działającego serwera (zgody, `--read`: odczyt, `--lan`: adresy dla innych urządzeń; sekcja „Plik stanu i `dashboard url`”) |
| `skillsync approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> [--sha <git sha>] [--target X] [--branch Y] [--file docs/…] [--expect-plan-state <status>] --source <cli\|ide> [--json]` | zapisuje zgodę użytkownika (patrz niżej; `dashboard` ustawia tylko serwer) |

Kody wyjścia (jak pozostałe komendy workflow): `0` = PASS, `2` = BLOCKED (błąd danych wejściowych, odmowa zapisu, brak
projektu lub przebiegu). Raport komend zapisujących i `metrics --json` to JSON z polami `schema`, `status` (`PASS` albo
`BLOCKED`) i `reason` przy odmowie. `skillsync approvals wait` (odczyt zgód, zob. [cli](cli.md)) kończy się `3` przy przekroczeniu czasu.

### `approvals record`

- Dopisuje jedną linię JSON do `.skillsync/runs/<run-id>/approvals.jsonl` pod blokadą plikową (jedyne źródło, które czyta
  `skillsync approvals wait`). Pola wpisu: `schema`, `at` (UTC), `word`, `sha`, `by`, `source` (`cli`, `dashboard` albo
  `ide`), opcjonalnie `file`, `run`, `plan`, `planState`, `target`, `branch`, `ahead`, `mode`, `via` (tylko zgody z gniazda zgód trybu dwóch gniazd, zapisuje
  je serwer; obiekt `{port, host, peer}`, zob. „Ślad zgody”), `worktree` (tylko zgody z serwera projektu: nazwa katalogu zakładki, która dała zgodę, 1–128 drukowalnych znaków; wyłącznie do audytu, czytelnicy ją pomijają; zob. „Serwer projektu i zakładki”). Wpis z `record` ma zawsze `at`
  (`YYYY-MM-DDTHH:MM:SSZ`), `run` (= `--run`) i `by` (nazwa użytkownika systemu, `unknown`, gdy jej nie da się odczytać);
  pola opcjonalne bez wartości są pominięte (nie `null`). `planState` (tylko `CP`/`READY`): status planu, którego linia `Przebieg:`
  wskazuje ten przebieg (`draft`, `approved`, `in_progress`, `done`, `archived`), w chwili zgody; wypełnia go sam zapis
  (`approvals record`, przycisk strony) z planu przebiegu, nie z planu oglądanego na stronie; bez planu przebiegu albo ze statusem
  spoza listy pole jest pominięte. Pole `plan` nadal jest ścieżką planu. Wartości tekstowe przechodzą przez maskowanie sekretów.
- `--sha` (7–64 znaków hex, małe litery): dla `GO plan`/`GO implement` git blob sha zatwierdzanego pliku (domyślnie: sha
  pliku z `--file`), dla `CP`/`READY` HEAD gałęzi roboczej, którą zgoda wypchnie (domyślnie HEAD repozytorium projektu,
  odczytany z `.git` bez uruchamiania git). Bez `--sha` i bez czytelnego HEAD (projekt bez `.git`): `BLOCKED`. Słowo GO
  bez `--file` i bez `--sha` = `BLOCKED` (sha zgody GO to zawsze sha pliku, nigdy HEAD).
- `--source`: komenda przyjmuje `cli` albo `ide` (panel IDE woła ją bez terminala, więc TTY nie jest wymagane).
  `--source dashboard` z komendy = `BLOCKED` (kod `2`): tę wartość ustawia wyłącznie serwer dashboardu, wołając
  `status.record_approval(..., source="dashboard")` we własnym procesie.
- `--file` tylko dla słów GO: `docs/specs/*.md` dla `GO plan`, `docs/plans/*.md` dla `GO implement` (plik zwykły, nie
  dowiązanie, nie `README.md`); dla `CP`/`READY` → `BLOCKED`. `--target`/`--branch`: nazwa gałęzi.
- Odmawia (kod `2`) drugiego zapisu tego samego słowa z tym samym `sha` i tym samym `planState` w tym samym przebiegu (dla GO:
  ta sama wersja pliku, dla CP/READY: ten sam HEAD przy tym samym statusie planu; porównanie dotyczy OSTATNIEGO wpisu tego słowa w przebiegu
  (jak `after` przycisku), więc po sekwencji `CP`(`in_progress`, S), `CP`(`done`, S), `CP`(`in_progress`, S) trzeci zapis jest dozwolony; `CP` zapisane w trakcie realizacji nie blokuje `CP`
  po zamknięciu planu przy tym samym HEAD; wpis bez pola ma `planState` pusty, więc nie zmienia wyniku); nieznane słowo, zły `--source`, run-id spoza wzorca `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`, brak
  katalogu przebiegu, dowiązanie na drodze `.skillsync/runs/<run-id>` lub dowiązany `approvals.jsonl`, plik, który
  przekroczyłby 256 KiB (czytelnicy pomijają większy plik w całości), albo blokada trzymana dłużej niż 10 s też kończą się
  `BLOCKED`.
- `--expect-plan-state <status>` (tylko `CP`/`READY`; `draft`, `approved`, `in_progress`, `done` albo `archived`): zapis odczytuje status planu przebiegu pod tą samą blokadą pliku, co zapis, porównuje go z podanym przed testem duplikatu i odmawia (kod `2`, `status: BLOCKED`, dodatkowe pole raportu `code: plan_state_changed`, nic nie zapisane), gdy status jest inny albo nieznany (brak planu z `Przebieg:`, brak linii `Status:`, status spoza listy, plan ponad limit rozmiaru). Komunikat: `approvals record: plan state changed (expected <X>, current <Y>); consent not recorded`, `<Y>` = `unknown` przy nieznanym. Zapisany wpis ma `planState` równy podanemu. Wartość spoza listy, flaga ze słowem GO i flaga razem z jawnym `plan_state` (API) to zwykłe `BLOCKED` bez `code`. Odmowa z powodu rozmiaru pliku albo zajętej blokady poprzedza porównanie i też nie ma `code`. Serwer wywołuje tę samą funkcję (`expect_plan_state`), zob. `POST /api/approve`.
- Blokada: blokada systemowa na pliku `approvals.jsonl.lock` w katalogu przebiegu (`fcntl.flock` na POSIX,
  `msvcrt.locking` na Windows), ta sama implementacja (`prototype_server.FileLock`) co blokady pliku pytań, komentarzy
  i użytkowników serwera prototypów. Nie ma pojęcia „porzuconej” blokady ani jej przejmowania: o zajętości decyduje
  jądro, a zakończenie procesu (także awaria) zwalnia blokadę. Plik `.lock` jest tymczasowy: zapis usuwa go jeszcze
  pod blokadą, a kolejny pisarz sprawdza po `flock`, że ścieżka nadal wskazuje plik, który zablokował (inaczej
  otwiera nowy), więc usunięcie nie narusza wzajemnego wykluczania. Treść i wiek istniejącego pliku `.lock` (resztka po
  starszym zapisie albo po awarii) nie mają znaczenia. Czekanie najwyżej 10 s (`BLOCKED`, powód „locked by another
  writer”). System plików bez blokad systemowych (część udziałów sieciowych i sterowników FUSE: ENOTSUP, ENOLCK)
  daje od razu `BLOCKED` z powodem „file locks are not supported”, bez czekania; `.skillsync/` trzymaj wtedy na dysku
  lokalnym. Kontrola duplikatu, dopisanie linii i `fsync` odbywają się pod blokadą; urwana ostatnia linia pliku nie
  łączy się z nowym wpisem. Czytelnicy zgód (`read_approvals`, strona statusu) nie biorą blokady, a zdublowany wpis tego samego
  słowa i sha jest dla nich nieszkodliwy (ostatni wpis na parę przebieg-słowo wygrywa; ślad na stronie pokazuje
  wpisy tak, jak zapisano).
- Raport (JSON na stdout, także bez `--json`): `schema`, `status` (`PASS`/`BLOCKED`), `reason`, `entry` (zapisany wpis albo
  `null` przy odmowie), przy `PASS` także `path` (ścieżka pliku względem projektu). Ta sama funkcja rdzenia
  (`status.record_approval`) służy serwerowi dashboardu; polecenie nie uruchamia podprocesów.
- Zgodę wyraża wyłącznie użytkownik (wpisuje komendę sam albo naciska przycisk strony, którą woła serwer). Agent tej komendy
  nie wywołuje: wiadomość agenta nie jest zgodą. To reguła tekstowa (skill `dashboard`, `feature`), nie zabezpieczenie
  techniczne: proces tego samego użytkownika systemu może wywołać komendę albo `POST /api/approve` na loopbacku. Ślad
  zostawia `source` (`cli`/`ide` z komendy, `dashboard` tylko z serwera) i `by`; granicą zaufania jest maszyna.

## Zasady zmian

- Po publikacji kontrakt zmienia się wyłącznie addytywnie: nowe klucze, nowe dozwolone wartości wyliczeń, nowe pola
  opcjonalne. Konsument ignoruje nieznane klucze.
- Zmiana łamiąca (usunięcie lub zmiana typu/semantyki klucza) podnosi `schema` i wymaga nowego opisu oraz fixtures.
- Pola `null` są zawsze obecne (klucz nie znika), chyba że opis mówi „opcjonalne”: wtedy klucz może być nieobecny.
- Tablice zachowują kolejność z opisu; obiekty w fixtures mają klucze posortowane alfabetycznie (kolejność kluczy nie jest
  znacząca).

## Normalizacja w fixtures

`expected.json` to wynik `dashboard show` dla `input/` po normalizacji, aby wynik nie zależał od maszyny i czasu:

- `input/` jest kopiowany do katalogu o nazwie `demo`, więc `project.name` = `demo`; `project.path` zastąpione `<root>`;
- wszystkie pliki wejściowe mają czas modyfikacji `2026-01-11T10:00:00Z`, strefa `UTC` (stąd `mtime`, `lastUpdate`,
  `run.lastReportAt` = `2026-01-11T10:00:00+00:00`);
- `generatedAt` zastąpione `2026-01-12T12:00:00+00:00`, `app.version` zastąpione `<version>`;
- bez repozytorium git (wejście to same pliki tekstowe): `git.*` ma wartości `null`, `delivery.ahead` = `null`, `delivery.commits` = `null`, `delivery.commitsMore` = `0`;
- wyjątek: przypadek `cp-ready` wymaga repozytorium; test zakłada w kopii `input/` repozytorium git (gałąź `feat/demo`,
  jeden commit, neutralna tożsamość, bez `origin`), a sha HEAD (pełne, 12, 8 i 7 znaków) zastępuje `<head>`. Wariant
  `expected-after.json` to wynik po zapisie zgody `CP` przez `record_approval` (źródło `dashboard`, w procesie testu): w zapisanym wpisie `at` = `2026-01-12T12:00:00Z`,
  `by` = `user`, a godzina w `reason`/`blocked` = `12:00`;
- `project.worktree`: bez repozytorium `null`; `cp-ready` (repozytorium zakładane w teście) ma `{kind: "main", name: "demo", path: "<root>", mainPath: null}`
  w `expected.json` i `expected-after.json`. Przypadek `worktree` nie ma katalogu w fixtures (`input/` nie może zawierać `.git`):
  test `test_skillsync_dashboard_workdir.py` buduje w katalogu tymczasowym repozytorium `demo` i jego powiązany worktree
  (`git worktree add`) i porównuje po normalizacji: `path` → `<root>`, `mainPath` (gdy nie `null`) → `<main>`, `name` = `demo`;
- JSON: `sort_keys`, wcięcie 2, `ensure_ascii=false`, końcowy znak nowej linii.

## `project.worktree`

Pole addytywne `schema: 1`: `project.worktree` to obiekt `{kind, name, path, mainPath}` albo `null`; klucz jest zawsze obecny.

| Pole | Typ | Opis |
|---|---|---|
| `kind` | `"main"` \| `"worktree"` | `main`: `.git` jest katalogiem albo plikiem `gitdir:` do katalogu bez `commondir` (submoduł, `--separate-git-dir`); `worktree`: `.git` jest plikiem `gitdir:` do katalogu z `commondir` (powiązany worktree). Nowe wartości addytywnie |
| `name` | string | nazwa katalogu projektu (`project.name`) |
| `path` | string | `project.path`, w postaci podanej serwerowi (bez rozwiązywania dowiązań) |
| `mainPath` | string lub `null` | dla `worktree`: katalog nadrzędny wspólnego katalogu git, gdy ten nazywa się `.git`; ścieżka w postaci rozwiązanej, więc przy dowiązaniu (np. `/var` → `/private/var`) `path` i `mainPath` mogą mieć różne prefiksy. Dla `main` i dla wspólnego katalogu o innej nazwie `null` |

Rozpoznanie czyta wyłącznie `.git` (plik albo katalog) i, dla powiązanego worktree, plik `commondir` w katalogu z `gitdir:`, z limitem rozmiaru; względny `gitdir:` jest liczony od katalogu projektu, względny `commondir` od katalogu git (tego z `gitdir:`); proces `git` nie jest uruchamiany. Odczyt `gitdir`/`commondir` jest wspólny z polami `git.*`.

`project.worktree` = `null`: brak `.git`, katalog podrzędny repozytorium (`.git` leży wyżej, nie jest szukany) oraz każdy błąd odczytu, w tym uszkodzony powiązany worktree (brak katalogu wskazanego przez `gitdir:`, nieczytelny `commondir`); uszkodzony worktree nie jest `main`. `commondir` liczy się tylko przy `.git` będącym plikiem `gitdir:`: `.git` będący katalogiem to zawsze `main`, także z plikiem `commondir` w środku. `commondir` wskazujący nieistniejący katalog to świadomie nadal `worktree`, a `mainPath` (gdy ten katalog nazywa się `.git`) wskazuje nieistniejącą ścieżkę: nic nie jest sprawdzane poza odczytem plików.

Zgodność wsteczna: starszy serwer nie ma pola, konsument traktuje brak klucza jak `null` (pole opcjonalne dla konsumenta, w fixtures zawsze obecne).

## `worktrees[]`

Pole opcjonalne `schema: 1`: lista zakładek serwera projektu (zob. „Serwer projektu i zakładki”). Dodaje je wyłącznie `GET /api/status`, i tylko gdy serwer ma więcej niż jedną zakładkę; serwer z jedną zakładką, serwer `standalone` i `dashboard show` pola nie mają (brak klucza = jedna zakładka). Lista jest jednakowa dla każdej zakładki (to lista serwera, nie zakładki, o którą pytano) i nie zawiera tokenów.

| Pole | Typ | Opis |
|---|---|---|
| `id` | string | `main` (główny katalog) albo 12 znaków szesnastkowych (skrót ścieżki worktree); klucz zakładki w `?tab=` |
| `name` | string | nazwa katalogu zakładki |
| `path` | string | ścieżka katalogu po `realpath`, w postaci posix |
| `kind` | `"main"` \| `"worktree"` | główny katalog albo powiązany worktree |
| `branch` | string lub `null` | gałąź z `HEAD` zakładki (odczyt z plików, bez `git`); `null` przy odłączonym HEAD |
| `head` | string lub `null` | pierwsze 12 znaków HEAD zakładki, z plików gita |
| `current` | bool | `true` dla zakładki domyślnej serwera (katalog, w którym uruchomiono `dashboard serve`; bez `?tab=` otwiera ją strona i API) |
| `lastActivity` | string lub `null` | najnowszy plik przebiegu, planu lub checkpointu zakładki, ISO 8601 UTC z dokładnością do sekundy; `null` = brak śladu |
| `waiting` | bool | czy sesja tej zakładki czeka na użytkownika (wskaźnik „czeka” na pasku zakładek); semantyka niżej |

`branch`, `head` i `lastActivity` są odświeżane razem z listą zakładek (co 2 s, bez uruchamiania `git`).

`waiting` liczy obserwator serwera przy każdym odświeżeniu listy zakładek (co 2 s), dla **każdej** zakładki, także tej, której nikt nie ogląda, wyłącznie z plików zakładki (bez `git` i bez budowania pełnego statusu): wybiera plan tak jak status, czyta tekst `czeka na …` ze statusu planu lub speca i najnowszy `czeka:` z checkpointów, po czym odejmuje to, co `approvals.jsonl` przebiegu już zawiera (GO plan i GO implement: jakikolwiek wpis tego słowa; CP i READY: wpis z sha zaczynającym się od HEAD zakładki). Jest to lżejszy odpowiednik `waiting[]` pełnego statusu: nie widzi oczekiwania na prototyp ani na pytania eksperta, więc może dać `false` tam, gdzie pełny status ma niepuste `waiting[]`, ale po udzielonej zgodzie nie da `true`. Do pierwszego odświeżenia po starcie (ok. 2 s) i przy błędzie odczytu zakładki wartość to `false` albo ostatnia znana. Dokładny stan zakładki, którą oglądasz, to jej własne `waiting[]`.

## Język i teksty interfejsu

- Język interfejsu projektu bierze się z klucza manifestu `language` (`pl` domyślnie, `en`; zob. [manifest](manifest.md)). Klucz nie zmienia języka skilli.
- Status (`dashboard show --json`, `dashboard serve`) zawsze ma pole `lang` (`pl` albo `en`). Pole `texts` jest tylko przy `lang` ≠ `pl`: to katalog `ui.*` języka (napis albo obiekt form liczby mnogiej `{one, few, many, other}` dla pl, `{one, other}` dla en). Brak `texts` = renderer używa wbudowanego polskiego.
- Teksty `srv.*` (zdania budowane przez serwer) nie są w `texts`: trafiają do pól JSON-a już przetłumaczone (`waiting[].action`, etykiety etapów, powody zgód, ostrzeżenia, błędy HTTP).
- Pierwszeństwo tekstów renderera: `texts` ze statusu > wbudowany polski katalog (`DEFAULT_TEXTS`). Wyjątek: `deps.texts` hosta nadpisuje tylko `live`, `liveTip` i `noConnection`; to obiekt albo funkcja `(lang) => obiekt`, wołana przy każdym odczycie z aktualnym językiem (host zmienia teksty po zmianie języka); wyjątek funkcji lub wynik inny niż obiekt = brak nadpisania. Obiekt renderera ma `t(key, params, n)` (`board.t`), `lang()` i `DashboardRenderer.defaultTexts` (polski katalog). `DEFAULT_TEXTS` jest generowany z `skillsync_workflow/data/i18n/pl.json` przez `scripts/sync_renderer_defaults.py` (`--check`: kod 1 przy rozjeździe; nie edytuj bloku ręcznie).
- Reguły wartości `texts`: renderer wstawia je wyłącznie przez `textContent`; wartości nie zawierają `<` ani `>`; fragment w odwrotnych apostrofach to kod (`mono`); `{nazwa}` podstawiane jest raz. Wartość nie może wyglądać jak sekret dla maskowania (`common.redact`: słowo `token`, `password`, `secret` i podobne, po którym idzie `:` albo `=`), bo zostanie zamaskowana.
- Starsze renderery (zamrożone kopie w testach) ignorują pola `lang` i `texts`; nowe pola są addytywne. Funkcji `deps.texts` nie znają zamrożone kopie (czytają `texts.live` z funkcji jako brak nadpisania): host obsługujący starsze kopie podaje obiekt.
- Opisy dymków: `descriptions.json` (pl) i obok `descriptions.en.json` w tej samej warstwie skilla `dashboard`. `GET /api/descriptions` wybiera plik wg `lang`; brak pliku = polski `descriptions.json`. Host, który kopiuje assety skilla (np. wtyczka), musi skopiować też `descriptions.<lang>.json`, inaczej dostanie polskie dymki.
- Strona HTML: dla `en` atrybut `<html lang="en">` i `<title>` z katalogu; `app.js` aktualizuje `lang` wg pola `lang` statusu. Nagłówek „Wczytuję status…” przed pierwszym statusem zostaje po polsku.
- Ostrzeżenie o nieznanej wartości `language` (np. `de`) pojawia się w `findings` `skillsync status`; status JSON wtedy ma `lang: "pl"` i nie ma `texts`.

Szczegóły i granice: [domain/i18n-ui](domain/i18n-ui.md).

## Pola najwyższego poziomu

| Klucz | Typ | Opis |
|---|---|---|
| `schema` | int | wersja kontraktu, obecnie `1` |
| `lang` | string | język tekstów interfejsu projektu: `pl` (brak klucza `language` w manifeście albo nieznana wartość) albo `en`; zdania budowane przez serwer (`waiting[].action`, etykiety etapów, powody zgód, ostrzeżenia, błędy HTTP) są już w tym języku. Pole addytywne |
| `texts` | obiekt | tylko gdy `lang` ≠ `pl`: katalog tekstów renderera (klucze `ui.*`, napis albo obiekt form liczby mnogiej); brak pola = renderer używa wbudowanego polskiego |
| `app` | obiekt | `name` (`"dashboard"`), `version` (wersja generatora, string) |
| `project` | obiekt | `name` (nazwa katalogu projektu), `path` (ścieżka bezwzględna), `worktree` (obiekt albo `null`, niżej: „`project.worktree`”) |
| `generatedAt` | string | czas wygenerowania, ISO 8601 z przesunięciem strefy |
| `version` | int | licznik zmian obserwatora (serwer: rośnie przy każdej zmianie plików); `show` zwraca `0` |
| `lastUpdate` | string lub null | najnowsza modyfikacja pliku bieżącego planu, speca lub checkpointów; null = brak plików |
| `git` | obiekt | stan repozytorium (niżej) |
| `plans` | tablica | wszystkie plany projektu (niżej) |
| `planAuto` | bool | `true`, gdy plan wybrano automatycznie (brak `--plan` albo nieznana ścieżka). Wybór: najniższa ranga statusu (`in_progress`, `approved`, `draft`, ostatni `done`), przy czym plan `in_progress`/`approved` z kompletem kroków `[x]` i bez katalogu przebiegu (sierota) liczy się jak `done`; w obrębie rangi pierwszeństwo ma plan z istniejącym przebiegiem i najświeższą aktywnością (`checkpoints.md`, `approvals.jsonl`, czas planu), potem data w nazwie |
| `mode` | string | `plan`, `spec`, `run` albo `none` (niżej) |
| `plan` | obiekt lub null | wybrany plan |
| `spec` | obiekt | spec bieżącego zadania (zawsze obecny) |
| `run` | obiekt | przebieg `.skillsync/runs/<run-id>` |
| `progress` | obiekt | postęp z sekcji `## Progress` planu |
| `acts` | tablica | trzy akty (Spec, Plan, Realizacja) z kafelkami etapów |
| `waiting` | tablica | na co czeka sesja |
| `silence` | obiekt lub null | cisza raportów |
| `approvals` | obiekt | zgody i przyciski zgód |
| `delivery` | obiekt | tryb dostarczania i stan gałęzi |
| `feedback` | obiekt | stany szkiców zgłoszeń zmian do warstw (tylko pliki lokalne) |
| `session` | obiekt | adres sesji agenta (opcjonalny) |
| `prototype` | obiekt | prototyp powiązany ze specem |
| `docs` | tablica | dokumenty do przeglądania, pogrupowane |
| `readiness` | obiekt | brief gotowości przebiegu |
| `assumptions` | obiekt | założenia ze speca |
| `blockingQuestions` | obiekt | pytania blokujące ze speca |
| `expertQuestions` | obiekt | otwarte pytania do eksperta (projekt: `open`, `blocking`, `runs[]`, `items[]`; bieżący przebieg: `current`) |
| `prototypes` | tablica | wszystkie prototypy projektu (do 50, wg `slug`): `{slug, iteration, screens, accepted, stale, threads, commentsAfterAcceptance, writing}`; `threads` = liczby wątków wg statusu (open, awaiting, decided, closed…), `writing` jak w `prototype.writing`; odnośnik buduje host z `slug` (`deps.prototypeUrl`) |
| `result` | obiekt albo null | `{text, path}` (w `show` i w `GET /api/status`): sekcja `## Wynik` wybranego planu (do 4 KiB, tekst Markdown), gdy plan jest `in_progress`, `done` albo `implemented`; null bez planu, bez sekcji albo dla planu, który nie ruszył. Tylko z `GET /api/status` (nie w `show`) dochodzą, addytywnie, pola `html` (widoczna część wyrenderowana tym samym bezpiecznym rendererem Markdown co `/api/doc`: nagłówki, listy, pogrubienie, kod w linii, tabele, akapity; surowy HTML źródła escapowany, linki tylko bezpieczne), `moreHtml` i `moreLines` (zwinięta reszta i liczba jej linii źródła; brak, gdy nie ma czego zwijać). Tekst jest maskowany z sekretów w całości przed podziałem i renderem. Podział widoczne/zwinięte przebiega tylko między blokami Markdown (nigdy w środku tabeli, listy, bloku kodu ani akapitu): ostatnia granica bloku nie dalej niż po 12 liniach, której część widoczna zawiera coś więcej niż nagłówki; gdy takiej nie ma, pierwsza granica za 12 liniami; bez granicy nic się nie zwija. Klient bez `html` (stary serwer, `show`) pokazuje `text` jako zwykły tekst. Wtyczka edytora używa kopii renderera z rdzenia (`npm run sync:dashboard`) i czyta `dashboard show`, który nie zawiera `html`/`moreHtml`/`moreLines`, więc pokazuje `text` jako zwykły tekst (renderer bez `deps.mountHtml`). Host może dodać `mountHtml` i własne renderowanie Markdown, ale nie musi |
| `eta` | obiekt albo null | szacowany koniec bieżącej operacji z najnowszego checkpointu (`eta:`) |
| `forecast` | obiekt | PROGNOZA pozostałej pracy do końca kroków planu z tempa bieżącego przebiegu, osobna od `eta` (deklaracji agenta) i od `timing` (pomiaru); zob. sekcja `forecast` |
| `substeps` | tablica | podkroki `Fn.k` z czasem |
| `agents` | obiekt (opcjonalne) | trwające i niedawno zakończone biegi podumysłów i rundy review z dziennika `.skillsync/runs/<run-id>/agents.jsonl`; brak dziennika = brak pola (zob. sekcja `agents`) |
| `estimates` | tablica (opcjonalne) | wiersze szacunków z linii `eta:` checkpointów, jeden na serię (brak pola = żaden checkpoint nie ma `eta:`; zob. sekcja `estimates`) |
| `tests` | obiekt (opcjonalne) | wyniki testów kroków planu z `.skillsync/runs/<run-id>/tests/` (brak pola = zakładka „Testy” niewidoczna; zob. sekcja `tests`) |
| `timing` | obiekt | czas pracy i czas czekania na człowieka per krok i per faza planu |
| `worktrees` | tablica (opcjonalne) | zakładki serwera projektu, tylko z `GET /api/status` serwera, który ma więcej niż jedną zakładkę; `dashboard show` jej nie ma (zob. „`worktrees[]`”) |

**Formatowanie w linii** (tylko `GET /api/status`, nie `show`; addytywnie, `schema: 1`): teksty jednoliniowe mają obok pola tekstowego pole `…Html` z bezpiecznym HTML-em w linii (pogrubienie, kursywa, kod, przekreślenie, link bezpiecznego schematu; bez bloków, nagłówków, list i tabel; surowy HTML źródła escapowany; ten sam renderer co `/api/doc`). Pola: `progress.phases[].steps[].titleHtml`, `progress.current.titleHtml`, `substeps[].titleHtml` (od `title`), `run.checkpoints[].textHtml` (od `text`; serwer wyróżnia w nim id kroków i frazy kluczowe tak jak strona w wierszu bez formatowania: `<span class=\"sidc\">`, `<span class=\"ph\">`, tylko w tekście poza `<code>` i `<a>`), `waiting[].whatHtml` i `waiting[].actionHtml` (od `what` i `action`), `expertQuestions.items[].textHtml` i `expertQuestions.current.items[].textHtml` (od `text`, przycięte do tych samych 160 znaków co zwykły tekst; przycięcie na źródle PRZED renderem, nigdy w środku znacznika, a zakres kodu, linku albo pogrubienia, który przecięłaby granica, odpada w całości). Pole `…Html` jest tylko wtedy, gdy tekst ma formatowanie; bez niego klient pokazuje zwykły tekst. Pola tekstowe (`title`, `what`, `action`, `text`) zostają bez zmian, a dymki, `title` i `aria-*` używają wyłącznie ich (z zdjętymi znacznikami po stronie renderera). Tekst jest maskowany z sekretów przy odczycie planu i checkpointów, przed renderem. Strona wstawia HTML wyłącznie przez `deps.mountHtml`. Pola `…Html` (jak `result.html`) dochodzą tylko z `GET /api/status`, nie z `dashboard show`. Wtyczka edytora używa kopii renderera z rdzenia (`npm run sync:dashboard`) i czyta `show`, więc ich nie dostaje i pokazuje pola tekstowe jako zwykły tekst (znaczniki Markdown widoczne), a renderer bez `deps.mountHtml` ignoruje `…Html` także wtedy. `forecast` i `timing.acts` są w `show`, więc dochodzą do wtyczki bez zmian.


### `mode`

| Wartość | Znaczenie |
|---|---|
| `plan` | wybrano plan; `plan` jest obiektem |
| `spec` | akt I: spec `draft`/`approved`, którego nie wskazuje żaden plan (czeka na „GO plan”); `plan` = null |
| `run` | akt I przed specem: jest przebieg z `checkpoints.md`, brak speca i planu; `spec.preparing` = true |
| `none` | brak planów, specyfikacji i przebiegów |

W trybach innych niż `plan` pola `plan`, `progress.phases` itd. są puste (`plan` = null, `progress.total` = 0).

### `git`

| Pole | Typ | Opis |
|---|---|---|
| `branch` | string lub null | bieżąca gałąź |
| `head` | string lub null | skrót HEAD (12 znaków) |
| `headFull` | string lub null | pełne sha HEAD |
| `remote` | obiekt lub null | `{host: "azure"\|"github", repo}` rozpoznany z `origin`; null gdy nieznany host |
| `branchUrl` | string lub null | adres gałęzi na hoście |
| `branchPushed` | bool lub null | czy `origin/<gałąź>` istnieje (nie znaczy „wszystko wypchnięte”: liczba niewypchniętych commitów to `delivery.ahead`) |

### `plans[]`

`path` (string), `title` (string), `status` (`draft`, `approved`, `in_progress`, `done`, `implemented` albo null), `spec`
(ścieżka speca lub null), `run` (id przebiegu lub null). Plan wiąże się z przebiegiem linią `Przebieg: <run-id>` albo `Run: <run-id>` w nagłówku planu, czyli przed pierwszym `## ` (`parse_header`); gdy występują obie, wygrywa linia położona wcześniej. `skillsync status` (`status.py`) czyta polskie `Przebieg:` w całym tekście planu, a angielskie `Run:` tylko w nagłówku (`protocol_tokens.RUN_LINE`). Sam `checkpoint --run-id X` tworzy `.skillsync/runs/X`, ale bez tej linii `plans[].run` i `run.id` strony statusu zostają `null` (strona nie zgaduje powiązania). Tę samą regułę stosują wszystkie czytniki linii przebiegu (`quality._PLAN_RUN`, `test_gate._RUN_LINE`, a przez nie `status.plan_state_for_run`, czyli `planState` zgód): `Przebieg:` w całym tekście, `Run:` tylko w nagłówku; zob. [domain/i18n-ui](domain/i18n-ui.md).

### `plan`

`path`, `title`, `status` (jw.; znormalizowany do małych liter, `-` → `_`), `statusText` (surowy tekst z nagłówka, np.
`draft (czeka na GO implement)`), `date` (string lub null), `mtime` (ISO), `fileSha` (git blob sha pliku; wersja, którą
zatwierdza zgoda), `pr` (`{number, url}` z linii `PR: #N (link: https://…)` lub null).

### `spec`

| Pole | Typ | Opis |
|---|---|---|
| `path` | string lub null | ścieżka speca |
| `exists` | bool | czy plik istnieje i jest czytelny |
| `preparing` | bool | `true` w trybie `run` (spec w przygotowaniu) |
| `sha` | string lub null | `Spec-sha:` z planu |
| `prototype` | string lub null | katalog prototypu |
| `title`, `status`, `statusText`, `mtime`, `fileSha` | | tylko gdy `exists`; jak w `plan` (opcjonalne) |
| `prototypeAcceptance` | obiekt lub null | `{screens, accepted, stale}` (opcjonalne; gdy jest prototyp) |

### `run`

| Pole | Typ | Opis |
|---|---|---|
| `id` | string lub null | id przebiegu |
| `exists` | bool | czy katalog `.skillsync/runs/<id>` istnieje |
| `start`, `startIso` | string lub null | `RUN=` z `checkpoints.md` (surowo i znormalizowane) |
| `checkpoints` | tablica | `{time: "HH:MM", text}`, najnowszy pierwszy |
| `files` | tablica | pliki `*.md` przebiegu (ścieżki względne, posortowane) |
| `lastReportAt` | string | opcjonalne; czas modyfikacji `checkpoints.md` |
| `inProgressFrom` | string | opcjonalne; czas checkpointu z ostatnim „w toku” |
| `parseWarnings` | tablica | opcjonalne (brak klucza = brak ostrzeżeń); do 10 najnowszych: `{time, segment: "w toku"\|"zrobione"\|"raport", fragment (do 80 znaków), reason: "no-step-id"\|"later-id"\|"unbalanced-bracket"}` |

Format checkpointu: `- HH:MM <tekst>`; segmenty `w toku: F1.2, F1.3`, `zrobione: F1.1`, `czeka: <na co>`, `eta: <operacja> do HH:MM`,
`trwa: <klucz etapu>[, …]`. Angielskie odpowiedniki, czytane przez parser obok polskich:

| Polski | Angielski | Reguła w parserze |
|---|---|---|
| `w toku:` | `in progress:` | angielska forma wymaga dwukropka i działa tylko na początku segmentu (`;` dzieli segmenty); polska forma działa w dowolnym miejscu segmentu i ma pierwszeństwo: angielska liczy się dopiero, gdy w liniach brak polskiej |
| `zrobione:` | `done:` | j.w.; forma polska działa też bez dwukropka |
| `czeka:` | `waiting:` | tylko na początku segmentu; w tej samej linii wygrywa nad `trwa:` |
| `eta: <operacja> do HH:MM` | `eta: <operacja> until HH:MM` | na początku segmentu; czas `HH:MM` na końcu |
| `trwa:` | `running:`, `ongoing:` | tylko na początku segmentu |
| `(kto: …)` | `(who: …)` | suffix wartości `czeka:` |

Przykład angielski: `- 14:05 in progress: F1.2; eta: rendering 12 prototype screens until 15:10`.

Składnia segmentów: `;` kończy segment tylko poza nawiasami `()`, `[]`, `{}` (opis w nawiasie może zawierać `;`); linia z
niezbalansowanym nawiasem dzieli się po każdym `;`, więc id po takim `;` może zginąć z odczytu; `parseWarnings` zgłasza zawsze to dzielenie wpisem
`unbalanced-bracket` (segment `raport`, fragment od elementu z niezamkniętym nawiasem, raz na checkpoint), także gdy żadne id nie zginęło. Każdy element listy „w toku” i „zrobione” zaczyna się od id kroku;
elementy rozdziela przecinek, a id w jednym elemencie łączą `/`, `+`, `&`, `‖`, `||`, „i” i „oraz”. Element bez id na początku
(`no-step-id`) i id dalej w elemencie niż na początku (`later-id`) nie wchodzą do `progress.active`; zgłasza je `parseWarnings`.
Id kroku (`STEP_ID`) to 1-3 wielkie litery, opcjonalny `-`, cyfry, opcjonalna litera i opcjonalny podkrok `.cyfry[litera]` (`S1`, `S2a`, `A-1`, `F1.2`); ten sam wzorzec czyta krok planu (`- [ ] <id>` w sekcji `## Progress`, a w starszych planach `## Kroki` albo `## Steps`) i checkpoint. Zapis spoza wzorca (`FT.1`, `1`, `**0.1**`) pomija `plan-fresh` i strona statusu (strona czyta tylko sekcję `## Progress`, `plan-fresh` także `## Kroki` i `## Steps`); nie są zgłaszane zwykłe zdania w polu wyboru (`- [ ] Napisać testy`) ani id z samych liter (`- [ ] A foo`). `skillsync plan-fresh` zgłasza (informacyjnie, `blocking: false`, jedno znalezisko na kod i plan z licznikiem `count` i trzema fragmentami, kod wyjścia bez zmian): `step-id-unparsed` (linia kroku planu bez czytelnego id, także gdy plan nie ma żadnego rozpoznanego kroku), `checkpoint-id-skipped` (element najnowszej linii `w toku`/`zrobione` z `no-step-id` albo `later-id`) i `checkpoint-id-unknown` (id na początku elementu, które pasuje do wzorca, ale nie jest krokiem żadnego planu `in_progress` tego przebiegu, np. `K2`, `O113`). `skillsync doctor` pokazuje je jako `warning` (jedno na kod i plan `in_progress` z `Przebieg:`; kody `checkpoint-*` przy pierwszym planie przebiegu), a `skillsync readiness <plan>` jako wpis w `warnings` (bez zmiany `status`).
Segment `trwa:` nazywa etapy, które trwają teraz (klucz etapu bez `stage_` albo jego etykieta, np. `spec_review`, `prototyp`); liczy się
tylko najnowszy checkpoint, a `czeka:` w tej samej linii wygrywa (sesja czeka, nie pracuje). Suffix `(kto: <rola>[ <login>])` w
wartości `czeka:` wskazuje adresata (role: `acceptor`, `developer`, `expert`, `commenter`, `agent`; po polsku też „akceptujący”,
„programista”, „ekspert”, „komentujący”, „odbiór”); angielskie aliasy `approver` i `acceptance` dają `acceptor`.

Tokeny w drugim języku, też czytane przez parser (poza checkpointem):

- plan: `Verdict:` (jak `Werdykt:`); sekcje `## Result` (jak `## Wynik`) i `## Summary for humans` (jak `## Streszczenie dla człowieka`); krok `Acceptance` w `## Progress` (jak `Odbiór`); nagłówki `Prototype:` i `Handoff:` (jak `Prototyp:`, `Oddanie:`);
- status w nagłówku planu lub speca (`statusText`): `czeka na <słowo zgody>` albo `waiting for <słowo zgody>` daje pozycję czekania na zgodę, gdy status to `draft`, `approved` albo `in_progress` i zgoda nie jest jeszcze dana (`dashboard.py`, `STATUS_WAIT_RE`);
- plan szkicowy (akt I, „GO implement” jeszcze nie): `szkic` albo `sketch` w tekście planu, `czeka na spec` albo `waiting for spec` (podciągi tekstu planu, `dashboard.py` przy `sketch`);
- gotowość przebiegu: `Date:` (jak `Data:`); `Readiness:` i `Plan-sha:` bez wariantu angielskiego (nazwy są wspólne);
- decyzja retro: pozycja `what`, w której występują `TAK`/`YES` i `NIE`/`NO` (albo `P<n>`), dostaje akcję retro w obu językach.

Sekcje ryzyka `Kontrakty`, `Migracja` i `Dane` parsery rdzenia czytają także po angielsku (`Contracts`, `Migration`, `Data`; sekcją ryzyka jest nagłówek, którego tytuł zaczyna się tym słowem). Ostrzeżenie `warning` aktu I (`acts[0]`) rozpoznaje tylko polskie tytuły.

### `progress`

`total`, `done`, `percent`, `pending` (zrobione wg checkpointu, jeszcze nie zaznaczone w planie), `pendingPercent`,
`activePercent` (opcjonalne; kroki w toku liczone jako połowa kroku, różnica zaokrągleń, więc `percent` + `pendingPercent` +
`activePercent` ≤ 100), `active` (id kroków w toku), `activeSource` (`checkpoint`, `first-unchecked` lub null), `current` (`{index, id, title}` lub
null), `parallel` (liczba kroków w toku ponad pierwszy), `reported` (id), `phases[]`.

`phases[]`: `name`, `label` (część przed `:`), `short`, `total`, `done`, `percent`, `pending`, `pendingPercent`, `state`
(`done`, `active`, `planned`), `steps[]`: `id`, `title`, `done`, `note`, `sha` (sha commita z notatki lub null), `index`,
`state` (`done`, `active`, `reported`, `planned`).

### `acts[]`

Trzy obiekty o `key` `spec` (I), `plan` (II), `real` (III). Wspólne: `key`, `roman`, `name`, `state` (`planned`, `active`,
`done`), `status` (status speca lub planu, null), `detail` (string lub null), `stages[]`.

Kafelek etapu: `label`, `done` (bool), `tip` (dymek), `key` (klucz opisu w `descriptions.json` skilla lub null), `state`
(opcjonalne: `done`, `active`, `planned`; `done: true` ⇔ `state: "done"`), `since` (opcjonalne, `HH:MM`; od kiedy etap trwa). Etap jest
`active`, gdy najnowszy checkpoint ma `trwa:` z jego kluczem (bez `czeka:` w tej linii), spec jest w przygotowaniu (etap „Szkic”)
albo znacznik pisania prototypu jest ważny (etap „Prototyp”); etap zrobiony zostaje `done`.
Etykieta rund review (`Review ×N`, `Review kodu ×N`) liczy tylko zamrożone raporty w katalogu przebiegu: pliki `.md` o nazwie
`spec-review-N.md`, `plan-review-N.md`, `code-review-N.md`, `code-review-<krok>-N.md` (numer rundy na końcu, dopuszczalne `rN`) oraz
starsze `final-review[-<słowo>...][-N].md` (np. `final-review-guard-1.md`; jako review kodu). Rundą nie jest plik, którego nazwa zawiera segment `decisions` lub `decyzje` w dowolnym miejscu (np. `code-review-final-decisions-1.md`), dowody
checkpointu (`*.log`), `*.json` ani inne dopiski obok raportu; jedna runda to jeden raport.

Rundę review w toku pokazuje etykieta `Review ×N` zieloną pulsującą kropką (kolor z tokenu `--done`, jak etykieta zrobionego etapu) i dopiskiem „runda N trwa od HH:MM”, gdy `agents.running` ma bieg `kind: review` o `profile` zgodnym z kafelkiem (`spec` → „Review” aktu I, `plan` → „Review” aktu II, `code` i `final` → „Review kodu”); numer rundy pochodzi z pola `round` zdarzenia, nie z licznika raportów. Gdy kafelek nie ma jeszcze żadnego raportu (runda 1), bieg ustawia w **rendererze** stan `active` kafelka (`since` = start biegu; serwer nie zmienia `acts[].stages[].state`), a przy ręcznym `trwa:` i biegu naraz wygrywa bieg z dziennika. W rundach 2 i dalszych kafelek jest `done` (reguła wyżej), więc dochodzi sama kropka i dopisek. Bieg przeterminowany (reguła z sekcji `agents`) gasi kropkę, a dopisek mówi „runda nie zakończona”.

Stan `active` zostaje w danych także po śmierci sesji (serwer nie czyta zegara); renderer gasi tylko pulsowanie i pisze „brak aktywności od N min”, gdy przebieg jest cichy: wg `silence` (`since`, `thresholdMin`), a bez niego wg `run.lastReportAt` i tego samego progu 35 minut.

Pola szczególne: `warning` (tylko akt I; ostrzeżenie o braku pre-implement przed „GO plan” przy sekcjach
Kontrakty/Migracja/Dane), `linkWarning` (tylko akt I, opcjonalne; zdanie o przerwanym powiązaniu spec → przebieg → prototyp: brak `Przebieg:`, brak katalogu
przebiegu albo prototypu, którego nie ma; null przy kompletnym powiązaniu), `percent` (tylko akt III; = `progress.percent`).

### `waiting[]`

`what`, `source` (`checkpoint`, `plan`, `spec`, `prototype`, `writing`, `expert`), `since` (`HH:MM` lub null), `action` (podpowiedź dla
człowieka), `link` (`{kind: "doc", path}`, `{kind: "prototype", slug}`, `{kind: "expert", slug, url}` lub null); opcjonalnie `word` (np. `GO plan`) i
`approve` (`spec`, `plan`, `cp`, `ready`), gdy czekanie odpowiada przyciskowi zgody.

`who` (opcjonalne): `{role, login, label}`, na kogo czeka sesja. `role`: `acceptor`, `developer`, `expert`, `commenter`, `agent` albo null
(nieznana rola z sufiksu: `label` to jej tekst); `login` tylko dla jednego adresata, inaczej null; `label` po polsku („akceptujący”,
„programista”, „ekspert”, „komentujący”, „agent”, „odbiór”). Źródła: sufiks `(kto: …)` w `czeka:` (tekst `what` jest bez sufiksu) albo
domyślnie `source` (`expert` → ekspert, `writing` → agent, `prototype`, `plan`, `spec` i pozycje zgód → akceptujący); bez źródła pola nie ma.

Pytania blokujące (`blockingQuestions`) generują czekanie tylko, gdy sesja zgłosi je w checkpoincie (`czeka: odpowiedź Q3`);
założenia nigdy nie są akcją „Odpowiedz na pytania”. Pozycja `waiting[]` ze `source: "checkpoint"` oznacza w zakładce „Pytania” wiersz, którego id wymienia (znacznik „sesja czeka”).

Dwie pozycje powstają z plików, bez znacznika w checkpoincie:

- `source: "writing"`: prototyp przebiegu ma ważny znacznik zapisu (`prototype.writing` nie jest null). `what` = „agent pisze ekrany (do HH:MM)”
  (czas lokalny hosta strony z `expiresAt`), `since` = `HH:MM` początku pisania, `link` = `{kind: "prototype", slug}`. Pozycja jest pierwsza
  i zastępuje każdą pozycję akceptacji prototypu (też tę z `czeka:` w checkpoincie), bo nie ma czego akceptować, dopóki agent pisze.
  Renderer pisze „Sesja pracuje od HH:MM: …”, nie „Sesja czeka na Ciebie”.
- `source: "expert"`: `expertQuestions.blocking > 0`, a najnowszy checkpoint nie ma znacznika `czeka:`. `what` = „odpowiedź eksperta (N blokujących)”,
  `link` = `{kind: "expert", slug, url}`: przy jednym przebiegu (tu: prototypie) z otwartymi pytaniami jego `slug` i `/_experts/<slug>`, w pozostałych
  przypadkach `slug: null` i `/_experts`. Renderer składa adres jak w toaście (`deps.prototypeOrigin(port)` plus ścieżka; bez adresu serwera
  prototypów pozycja nie ma odnośnika). Pytania bez `blocking` nie tworzą pozycji.

### `silence`

`{since, thresholdMin}` (czas ostatniego raportu i próg w minutach, 35), gdy plan jest `in_progress`, są raporty i sesja
nie czeka jawnie (żaden element `waiting` ze źródłem `checkpoint` ani `expert`); w przeciwnym razie null. Minuty liczy konsument.

Pole zostaje bez zmian także wtedy, gdy trwa bieg podumysła z dziennika `agents`: ciszę gasi **renderer** (kafelek aktywnego etapu i baner ciszy nie liczą jej, dopóki `agents.running` ma bieg niezestarzały według reguły z sekcji `agents`; w to miejsce „Trwa: <rola>, runda N, od HH:MM”). Po `end` albo po przeterminowaniu biegu cisza wraca.

### `approvals`

`{enabled, actions[], last}`; `last` = ostatni wpis `approvals.jsonl` lub null; `enabled` = false (z `--no-approvals`) daje
`actions: []`.
Opcjonalne pola tylko z serwera (`/api/status`, nie w `show`): `here` (bool, host zgód) i `viewer` (bool, host wyłącznie
do odczytu z `--read-host`); w trybie dwóch gniazd także `role` (`read`, `approve`), a `here`/`viewer` są wtedy z gniazda;
opis w sekcji „Serwer”.

`actions[]`: `kind` (`spec`, `plan`, `cp`, `ready`), `word` (`GO plan`, `GO implement`, `CP`, `READY`), `state`:

| `state` | Znaczenie |
|---|---|
| `waiting` | przycisk aktywny: sesja czeka na tę zgodę |
| `running` | widoczny, nieaktywny: akt trwa, `reason` mówi „czeka na…” |
| `after` | zgoda już dana; drugiego zapisu nie ma; `given` to wpis zgody |

Pola: `reason` (string lub null), `blocked` (powód nieaktywności; null dla `waiting`), `path` (plik speca/planu; dla
`cp`/`ready` id przebiegu), `planState` (tylko `cp`/`ready`, gdy plan przebiegu istnieje: jego status w chwili budowy statusu; klient odsyła go w `POST /api/approve` jako `planState`, a serwer odpowiada `409` (ciało `{error, code: "plan_state_changed"}`), gdy status planu odczytany przez zapis pod blokadą pliku jest inny, np. plan zamknięto po narysowaniu strony przy tym samym HEAD; brak pola w żądaniu = zachowanie bez tej kontroli), `sha` (git blob sha pliku do zgody GO albo HEAD dla CP/READY; pusty string, gdy brak), `given`
(wpis lub null), `target` (tylko `cp`/`ready`; dokąd oddać), `run` (id przebiegu, do którego trafi wpis zgody, gdy katalog
`.skillsync/runs/<run-id>` istnieje; inaczej null; pole dodane addytywnie, `path` dla `cp`/`ready` bez zmian).

`given` w stanie `after`: wpis zgody w postaci zapisanej w `approvals.jsonl` (wpis z `approvals record` ma zawsze `at`).
Dla `spec`/`plan` stan `after` wynika ze statusu pliku (`approved` i dalej), więc zgoda mogła zostać dana poza
`approvals.jsonl` (np. `spec approve`/`plan approve` w IDE albo słowo wpisane w sesji): wtedy `given` = `{"at": null}`, a
`reason` mówi „zgoda … już dana”. Dla `cp`/`ready` stan `after` jest tylko przy ostatnim wpisie słowa, którego `sha` = `git.headFull` i którego `planState` równa się
bieżącemu statusowi planu przebiegu (zmiana statusu planu po zgodzie ponownie otwiera przycisk, także bez nowego commitu). Wpis bez
`planState` liczy się po samym `sha`, z wyjątkiem repozytorium z `workflow.specs.tracked: false` (plany poza gitem) i planu `done`:
taki wpis nie daje `after`, bo stara zgoda blokowałaby nową.

Wpisy zgód (`given`, `last`) pochodzą z `approvals.jsonl` bieżącego przebiegu w kolejności pliku, w postaci zapisanej; linie
spoza reguł `skillsync status` (nieznane słowo, nieczytelne `at`, sha spoza 7–64 znaków hex, plik ponad 256 KiB) są pomijane.

### `delivery`

`mode` (`pr`, `direct`, `invalid`), `base` (string lub null), `source` (`manifest`, `default`), `branch`, `target` (dokąd
oddaje „CP”; w `direct` z linii planu `Oddanie:`, gdy cała wartość jest jedną poprawną nazwą gałęzi; inny tekst linii jest ignorowany, a podpowiedź słowa `CP` mówi „Oddanie: wartość pominięta, to nie nazwa gałęzi”; jednowyrazowe słowa zastępcze (`brak`, `TBD`, `todo`, `none`, `n/a`, `null`, `nie`, `nic`, także pusta wartość, `-` i `—`; wielkość liter nie ma znaczenia) są ignorowane tak jak inny tekst (słowa byłyby poprawnymi nazwami gałęzi, ale nikt nie oddaje pracy na gałąź o takiej nazwie; pusta wartość i `-` niczego nie oddają i nie dają podpowiedzi „wartość pominięta”)), `remote`, `pushed` (bool lub null), `ahead` (int lub null; commity
niewypchnięte; renderer pokazuje je przy gałęzi w nagłówku jako „N niewypchniętych” (neutralnie w trybie `direct`, do zakończenia planu)), `commits` (lista `{sha, subject}` tych commitów, najnowsze pierwsze, do 20; `sha` to krótki skrót, `subject` to pierwsza linia opisu po zamaskowaniu sekretów, jedna linia do 80 znaków, obcięta kończy się „…”; `null`, gdy liczba niewypchniętych jest nieznana, `[]` gdy ich nie ma), `commitsMore` (int; ile starszych niewypchniętych commitów nie weszło do `commits`), `words[]`: `{word: "CP"|"READY", active, tip, target}` (`READY` tylko w trybie `pr`; przy `invalid` pusta
tablica).

Renderer pokazuje w nagłówku (po bloku gałęzi, przed chipem PR) chip trybu dostarczania z `delivery.mode`, `base` i `source`: „tryb: pr”, „tryb: direct, baza: develop”; dymek podaje tryb, bazę i źródło (z manifestu albo domyślny, gdy manifest nie ma `delivery.mode`). Zły tryb (`mode: invalid`, a także `source: invalid`) to ostrzegawczy chip „tryb dostarczania: błąd”, nie cichy `pr`; serwer zapisuje zły manifest jako `{mode: "invalid", base: null, source: "manifest"}`, a `skillsync route` jako `source: "invalid"`; do renderera dociera tylko `/api/status` (wystarcza `mode: "invalid"`), a ostrzeżenie przy `source: "invalid"` jest obroną na zapas. Brak pola `delivery` albo `mode` niebędący niepustym tekstem: bez chipu. Wartość `base` jest wstawiana jako tekst.

### `feedback`

`{enabled, total, drafts[]}`: stany szkiców `skillsync feedback` liczone wyłącznie z plików lokalnych (szkice, `F<n>.preview.json`, `.skillsync/feedback/approvals.jsonl`,
rekordy wyniku, konfiguracja maszyny i sekcje `feedback` warstw); bez sieci, bez `az`, bez zapisu. `enabled` = istnieje szkic albo warstwa proponuje cel;
bez szkiców i bez sekcji `{enabled: false, total: 0, drafts: []}`. `total` liczy wszystkie szkice, `drafts` zawiera do 50 najnowszych (malejąco po numerze).
Uszkodzony plik szkicu lub podglądu daje stan `stale` z powodem `unreadable`, uszkodzony plik zgód nie daje zgody.

Stany w kolejności sprawdzania: `sent` (rekord `F<n>.sent*.json` z sha podglądu albo zużyta zgoda), `stale` (brak podglądu lub jego `view`, podgląd starszy niż 24 h, cel
niezaufany, szkic zmieniony po podglądzie), `blocked` (skan nie PASS albo limit 5 wysyłek na 24 h), `after` (ważna, niezużyta zgoda dla bieżącego sha), `waiting`.

Rola z zgodami (gniazdo zgód albo `approvals.here`) dostaje pełny element: `{draft, sha, sha12, state, reason, given, generic[, view]}`; `given` = `{at, source}`
(`cli`, `ide`, `dashboard`) tylko w stanie `after`, `generic` = pokrycie skanu `generic` (nazwy klientów nie są sprawdzane), `view` = migawka podglądu (bez niej przy `sent`).
Element `stale` ma tylko `{draft, state, reason[, sha12]}`. Rola tylko do odczytu (gniazdo odczytu, `--read-host`) dostaje wyłącznie `{draft, state}`: bez sha, treści i celu.
Rola jest ustalana dla każdego żądania, wynik nie jest współdzielony między rolami.

### `session`

`{url: string|null}`; https, bez danych logowania w adresie. `show` zwraca null.

### `prototype`

`{slug: string|null, port: int, up: bool, writing: object|null}`; `up` bez serwera (`show`) = false. `writing` = `{since, expiresAt, renewedAt[, screensPlanned], screensDone}`
(UTC, milisekundy; nigdy `by`; `screensPlanned` (1–200) tylko gdy agent podał `writing start --screens N`, `screensDone` = liczba ekranów `index.html` bez przykładowego, liczona jak akceptacja) ważnego znacznika zapisu ekranów `docs/prototypes/<slug>/.writing.json`, inaczej null (brak, wygasły, uszkodzony,
dowiązanie symboliczne). Odczyt jest tym samym kodem co w serwerze prototypów, tylko do odczytu; serwer strony śledzi plik znacznika i jego ważność,
więc strona odświeża się też przy wygaśnięciu.

`commentsAfterAcceptance` (opcjonalne w `prototype` i w `prototypes[]`): `{count, screens: [{id, count}]}`; wiadomości (nowy wątek albo odpowiedź, bez wycofanych)
późniejsze niż ostatnia WAŻNA akceptacja ekranu, w wątkach niezałatwionych (`open`, `awaiting`; nie `decided` ani `closed`). Bezstanowe: bez znacznika
„przeczytane”, znika po załatwieniu wątku albo nowej akceptacji. W `prototype` tylko gdy `count > 0`.

### `docs[]`

Grupy `{key, label, items[]}`; `key`: `task` (bieżący spec i plan), `run` (pliki przebiegu), `specs`, `plans`, `design`,
`domain`. Element: `path`, `title`, `status` (lub null), `mtime`.

Renderer rysuje wszystkie grupy jako `<details class="doc-group">`: `task` i `run` domyślnie otwarte (można je zwinąć), `specs`, `plans`,
`design`, `domain` domyślnie zwinięte. Nagłówek grupy to `<summary>` z etykietą i liczbą dokumentów. Stan zwinięcia żyje w stanie renderera (bez `localStorage` i bez callbacka hosta): przeżywa
odświeżenie, nie przeżywa ponownego wejścia na stronę. Wybór dokumentu (kliknięcie, adres, odnośnik) rozwija jego grupę jeden raz;
odświeżenie bez zmiany wyboru jej nie rozwija, gdy użytkownik ją zwinął. Domyślnie wybrany pierwszy dokument rozwija swoją grupę, gdy lista nie zaczyna się od `task`. Przyciski dokumentów zostają w DOM także w zwiniętej grupie; `docs[]` w JSON bez zmian.

## Pola dodane do kontraktu

### `readiness`

Źródło: `.skillsync/runs/<run-id>/readiness.md` (produkuje `skillsync readiness --run-id`): pierwsza linia
`Readiness: PASS|BLOCKED`, potem `Data:` (albo `Date:`) i `Plan-sha:`.

| Pole | Typ | Opis |
|---|---|---|
| `status` | `"PASS"`, `"BLOCKED"` albo null | null = brak pliku (stan „brak danych”) albo nierozpoznana pierwsza linia |
| `date` | string lub null | `Data:` (albo `Date:`) |
| `planSha` | string lub null | `Plan-sha:`; konsument porównuje z `plan.fileSha` (różnica = brief nieaktualny) |
| `source` | string lub null | ścieżka pliku względem projektu; null bez pliku |

Pierwsza niepusta linia decyduje o `status`; `Data:` i `Plan-sha:` są czytane także przy nierozpoznanej pierwszej linii
(wtedy `status` = null, a `source` wskazuje plik). Bez katalogu przebiegu wszystkie pola są null.

### `assumptions` i `blockingQuestions`

Źródło: sekcja `## Pytania otwarte` albo `## Open questions` bieżącego speca (`spec.path`).

- Założenie: `- Q<n> [ZAŁOŻENIE]: <pytanie> — przyjęte: <wartość>` albo `- Q<n> [ASSUMPTION]: <pytanie> — assumed: <wartość>`
  (także `— accepted: <wartość>`) → `assumptions.items[]` = `{id, question, accepted}`, `assumptions.count` = długość listy.
- Pytanie blokujące: `- Q<n>: <pytanie>` (bez znacznika w nawiasie kwadratowym) → `blockingQuestions.items[]` =
  `{id, question}`, `count`.
- Linie ze znacznikiem innym niż `[ZAŁOŻENIE]`/`[ASSUMPTION]` (np. `[ROZSTRZYGNIĘTE …]`, `[EKSPERT]`) i linie spoza sekcji są pomijane.
- Założenia nie są akcją „Odpowiedz na pytania”; tej akcji (`waiting`) dotyczą tylko pytania blokujące zgłoszone przez sesję.
- Renderer rysuje te pola w zakładce „Pytania”, w grupie „Pytania do realizacji” (pytania blokujące, potem założenia jako osobny stan „założenie” z „przyjęte: …”);
  zakładka i jej liczniki: sekcja `expertQuestions` niżej. Kafelek Aktu I nadal nie ma listy pytań. Liczniki renderera biorą długość `items[]`, nie `count`.
- Separator przed `przyjęte:` (także `assumed:`, `accepted:`) może być półpauzą, pauzą, `--` albo `-` (otoczonymi spacjami); znacznik bez rozróżnienia
  wielkości liter, także bez polskich znaków (`[ZALOZENIE]`, `[assumption]`). Założenie bez `przyjęte:`/`assumed:`/`accepted:` ma `accepted` = `""`.
- Wiersz tabeli `| Q<n> | <pytanie> | … |` (jak w szkielecie speca) jest pytaniem blokującym; `question` = pierwsza niepusta
  komórka po id. Id pogrubione (`**Q4**`) jest rozpoznawane; powtórzone id liczy się raz (pierwsze wystąpienie); bloki kodu
  są pomijane.
- Bez speca: `{count: 0, items: []}`.

### `expertQuestions`

Źródło: pliki `docs/prototypes/<slug>/questions.json` wszystkich prototypów projektu (format `expert-questions/1`), a nie
wiersze widocznego speca. Czytane tak samo jak w serwerze prototypów (bez jego importu), tylko do odczytu: katalog i pliki
bez dowiązań symbolicznych, `prototype.json` obok, plik do 2 MiB (serwer zapisuje do 1 MiB plus jedna operacja), uszkodzony albo nieznany plik jest pomijany.

- Pytanie jest otwarte od pierwszego `ask` do pierwszego `answer` albo `withdraw` (wycofane i odpowiedziane się nie liczą);
  blokujące = `blocking: true` w `ask`.
- Słownik: „przebieg” (przebieg pracy) to praca nad jednym specem i planem; w nazwach pól pytań do eksperta „przebieg” oznacza
  prototyp (`runSlug`, `runs[]`, `/_experts/<slug>`). Dotyczy to też `runs[]`, `items[]`, toastu i pozycji `waiting` z `source: "expert"`
  (zdanie „przy jednym przebiegu” = przy jednym prototypie z otwartymi pytaniami). Dwa widoki: **projektowy** (`open`, `blocking`, `runs[]`, `items[]`; toast i baner
  czekania) i **przebiegu** (`current`).
- `open`, `blocking`: sumy po wszystkich prototypach projektu (widok projektowy); zawsze obecne, `0` bez pytań. Nie zależą od
  wybranego planu ani speca, więc pytanie z prototypu A nie znika po starcie przebiegu B. `runs[]` i `items[]` też są projektowe.
- `current`: `null` albo `{spec, prototypes, open, blocking, items}`; zawsze obecne (także `null`), identyczne w `dashboard show` i
  `GET /api/status`. Widok bieżącego przebiegu (wybranego speca). `spec` = ścieżka speca; `prototypes` = lista jak `prototypes[]` (do 50, wg `slug`);
  `open`, `blocking` = liczby otwartych i blokujących pytań przebiegu (bez limitu); `items` = jak `items[]` (do 50 najnowszych, także odpowiedziane i wycofane).
  Wiązanie po ścieżce speca (porównanie po znormalizowanej ścieżce względnej): prototypy przebiegu to te, których `prototype.json`
  ma `requirements` równe ścieżce speca, plus prototyp z linii `Prototyp:` planu albo speca (gdy istnieje); pytania przebiegu to pytania ze
  WSZYSTKICH prototypów, których `ask` ma `origin.spec` równe ścieżce speca. `ask` bez `origin` jest tylko w widoku projektowym.
  `origin.spec` służy wyłącznie jako filtr i nie trafia do odpowiedzi. Pytania przebiegu zapisane pod cudzym prototypem są w
  `current.items`, a ten prototyp nie wchodzi do `current.prototypes`.

  | Stan | `current` |
  |---|---|
  | brak speca przebiegu (tryb `none`/`run`), brak katalogu `docs/prototypes`, katalog będący dowiązaniem | `null` |
  | spec jest, ale ani prototypu, ani pytań przebiegu | `null` |
  | spec jest i są prototypy albo pytania przebiegu | obiekt |
  | `Prototyp:` wskazuje nieistniejący katalog albo nazwę spoza wzorca slugu | nazwa pominięta; wynik z `requirements` i `origin.spec` |
  | plan z `Spec:` bez pliku speca | `null` |
  | uszkodzony albo za duży `questions.json` jednego prototypu | plik pominięty, reszta liczona |
  | ponad 50 prototypów w projekcie | `current.prototypes` liczone po `spec.path`, do 50 wierszy |

  `current.items` przechodzą to samo maskowanie co `items[]`. `textHtml` (przycięcie tekstu do 160 znaków przed renderem) jest
  tylko w `GET /api/status`; `dashboard show` go nie ma.
- `runs[]`: tylko przebiegi z otwartymi pytaniami, w kolejności `slug`: `{slug, open, blocking, url, addressees}`; `url` to ścieżka
  strony przebiegu na serwerze prototypów (`/_experts/<slug>`), host dokłada konsument. `addressees` (opcjonalne): `"all"`, gdy któreś otwarte
  pytanie przebiegu nie ma adresatów, inaczej posortowana suma loginów adresatów otwartych pytań.
- `items[]` (opcjonalne): do 50 najnowszych pytań wszystkich przebiegów, najnowsze pierwsze: `{id, runSlug, text (do 160 znaków), blocking,
  addressees (lista loginów, pusta = wszyscy eksperci), state (open, answered, withdrawn), at, url}`.
- Widok widza (`approvals.viewer`): ta sama odpowiedź co dla operatora (loginy, nazwiska, treści pytań, plany i teksty checkpointów są widoczne; strona jest wewnątrzfirmowa). Różni się tylko flaga `approvals.viewer` (bez zapisu zgód) i `approvals.here`; w trybie dwóch gniazd obie flagi (i `approvals.role`) pochodzą z gniazda, nie z nagłówka `Host`, a treść pozostaje bez filtra także na gnieździe odczytu.
- Renderer pokazuje toast „Pytania do eksperta: N otwartych (M blokujących)” w banerze, gdy `open > 0`: to stan, nie wynik
  akcji, więc bez timera i bez „×”; znika, gdy `open` spadnie do `0`. Odnośnik „Odpowiedz na pytania” prowadzi do
  `/_experts/<slug>` przy jednym przebiegu (tu: prototypie) albo do `/_experts` przy kilku, o ile host zna adres serwera prototypów
  (`deps.prototypeOrigin(port)`); bez niego toast jest samym tekstem. Pole nie zależy od `approvals.here` ani `viewer`. Toast i
  baner czekania (`source: expert`) liczą po całym projekcie.
- Zakładka „Pytania” (`tab-questions`, `view-questions`, `qCount`) ma dwie grupy (`section.q-group[data-group]`), w tej kolejności:
  „Pytania do realizacji” (`realize`: `blockingQuestions.items[]` i `assumptions.items[]` widocznego speca; wiersz = id, tekst obcięty do 160 znaków, pełny w `title`;
  znacznik „sesja czeka” przy pytaniu, na które czeka sesja według `waiting[]` ze `source: "checkpoint"`, z dopasowaniem po granicy identyfikatora: `Q1` nie oznacza `Q10`;
  pytanie z `(ekspert: qn-<hex>)` (w specu angielskim `(expert: qn-<hex>)`) w tekście dostaje znacznik „u eksperta” i nie jest liczone) i „Pytania do eksperta domenowego” (`expert`: dotychczasowa treść,
  `current`: prototypy i pytania bieżącego przebiegu). Każda grupa ma limit 50 wierszy (w grupie realizacji łączny: najpierw pytania, potem założenia), nadmiar to jedna notka „+N więcej” (licznik grupy liczy wszystkie).
  Licznik grupy realizacji = pytania blokujące bez oddanych ekspertowi (z `items[]`); licznik grupy eksperta = `current.open`; `qCount` = suma obu, a dymek karty rozbija ją
  („N do realizacji, M do eksperta”); założenia nie wchodzą do żadnego licznika. Zakładka jest widoczna, gdy którakolwiek grupa ma treść (pytanie blokujące, założenie,
  prototyp albo pytanie eksperta); przy `current: null` bywa więc widoczna samą grupą realizacji. Grupa bez treści przy drugiej z treścią ma nagłówek i komunikat „Brak …”;
  obie puste ukrywają zakładkę. Pola `blockingQuestions` i `assumptions` mogą nie istnieć (starszy serwer) albo mieć zły typ: pozycje bez `id` i `question` są pomijane.
  Przy BRAKU klucza `current` (odpowiedź starszego serwera) grupa eksperta filtruje lokalnie po `prototype.slug`: prototypy po `slug`, pytania po `runSlug`,
  licznik z `runs[]` tego sluga; to przybliżenie (nie zna `origin.spec` ani `requirements`). Bez `prototype.slug` grupa eksperta jest pusta; ze slugiem bez dopasowań pokazuje puste listy „Brak …”.

### `agents`

Opcjonalne pole najwyższego poziomu (`show` i `GET /api/status`; obie drogi budują je jedną funkcją, `dashboard_tests.add_to_status`, więc są zgodne). Źródło: dziennik `.skillsync/runs/<run-id>/agents.jsonl`, który zapisują `skillsync agent-run` i `skillsync review-round` (jedna linia = jedno zdarzenie, UTF-8, tylko wartości z list zamkniętych, nic z treści sesji ani raportów). **Brak dziennika = brak pola** (`show` bez dziennika jest bajt w bajt taki jak przed wprowadzeniem pola); stary renderer ignoruje pole.

Zdarzenia (`v: 1`): `start` (`agent` `a-<n>`, `role`, `kind`, `profile`, `step`, `round`, `eta`), `eta` (`agent`, `eta`: nowy termin, przesunięcie), `end` (`agent`, `result`, `report`); `t` to czas zdarzenia ISO 8601 ze strefą. `role` ∈ `reviewer`, `verifier`, `implementer`, `test-writer`, `test-runner`, `scribe`, `mechanic`, `explore`; `kind` ∈ `review`, `run`; `profile` (tylko `review`) ∈ `spec`, `plan`, `code`, `final`, `docs`, `prototype`, `retro`, `pre-implement`; `step` to id kroku (`[A-Za-z0-9._-]{1,32}`) albo null; `round` 1–99 albo null; `eta` to `HH:MM` albo null; `result` ∈ `PASS`, `FAIL`, `CHANGES_REQUIRED`, `BLOCKED`, `NOT_RUN`, `DONE`, `CONFIRMED`, `REFUTED`, `UNDETERMINED`, `ABORTED`. Linia uszkodzona albo z wartością spoza listy jest pomijana.

```
agents: {
  running: [{agent, role, kind, profile|null, step|null, round|null, startedAt, eta|null, etaFirst|null, etaIso|null, etaShifts}],
  recent:  [{agent, role, kind, profile|null, step|null, round|null, startedAt, endedAt, seconds, etaIso|null, result, report|null}],
  omitted: {running, recent}
}
```

- `running` = `start` bez `end`, od najstarszego; najwyżej 6 (najstarsze do `omitted.running`). `recent` = tylko biegi z `end`, najnowsze pierwsze, najwyżej 8 (reszta do `omitted.recent`); biegi `kind: run` krótsze niż 2 min albo bez żadnego terminu (`eta`) nie wchodzą do `recent` (szum, nie liczy się do `omitted`), rundy `review` wchodzą zawsze. Pełna lista jest tylko w `dashboard metrics`.
- `etaFirst` = termin ze `start` (null, gdy go nie miał), `etaShifts` = liczba zdarzeń `eta`, `eta` = ostatni termin (`HH:MM`), `etaIso` = ostatni termin rozwiązany z `t` zdarzenia, które go niosło (data i strefa zdarzenia; termin o ponad 12 h wcześniejszy niż zdarzenie to następny dzień, jak w `eta`). `startedAt` i `endedAt` to ISO 8601 ze strefą, `seconds` = `endedAt − startedAt`.
- `report` to nazwa pliku w katalogu przebiegu i jest linkiem tylko wtedy, gdy plik jest w `run.files` (raport `.log` albo `.json` nie dostaje linku); w pozostałych przypadkach null.
- Czytane jest najwyżej ostatnie 500 linii i 256 KiB pliku (jedno otwarcie); osierocone `end` i `eta` (ich `start` poza ogonem) są ignorowane, powtórzone id `start` nie otwiera biegu drugi raz. Dziennik innego przebiegu niż bieżący nie jest czytany, dowiązanie w miejscu pliku lub katalogu przebiegu to brak pola.
- **Serwer nie czyta zegara i nie ma pola `stale`.** Przeterminowanie liczy konsument: bieg `running` jest przeterminowany, gdy teraz > `max(startedAt + 45 min, etaIso + 15 min)` (bez `etaIso` samo `startedAt + 45 min`). Przeterminowany bieg renderer pokazuje przygaszony („nie zakończono”), nie liczy go do „Trwa”, nie świeci jego kropki i nie pozwala mu tłumić ciszy.
- Odświeżanie: `agents.jsonl` jest w obserwowanych ścieżkach i w `signature()` serwera, więc nowa linia podbija wersję SSE.

Renderer: sekcja „Biegi podumysłów” poza aktami (jedna, widoczna w każdym akcie; lista `ul`, rozwinięta, gdy `running` jest niepusty; domyślnie tylko trwające biegi): wiersz `rola · runda N · krok S · od HH:MM · koniec ok. HH:MM`, pasek upływu (`role="progressbar"`, `aria-valuenow`, tekstowy odpowiednik w `aria-valuetext`), „ETA przesunięte ×N” przy `etaShifts > 0`, po terminie „przekroczono ETA o N min” (jak wiersz `eta` w kafelku aktu); zakończone (lista `recent`): wynik i czas trwania; lista jest ukryta za przyciskiem „pokaż zakończone (N)” / „ukryj zakończone” (`aria-expanded`, `aria-controls`; stan to wybór użytkownika z kliknięcia przełącznika, zapamiętany między odświeżeniami danych na żywo, nie przeładowaniem strony; sam przełącznik jest też, gdy nic nie trwa), a raport otwiera kliknięcie tekstu wiersza (natywny `button`, `aria-label` „Otwórz raport <plik>: <tekst wiersza>”; bez raportu zwykły tekst; osobnego przycisku „raport” nie ma). Notka „Pominięto starsze: N” liczy pominięte trwające zawsze, a pominięte zakończone tylko przy rozwiniętej liście zakończonych. Wartości z dziennika są wstawiane wyłącznie jako tekst; brak albo uszkodzone pole = brak sekcji.

Znacznik „N rund review kroku” przy kroku planu (renderer) liczy zamrożone raporty `code-review-<krok>-N.md` z `run.files` (wielkość liter bez znaczenia, także `-rN`, bez `-decyzje` i `-verifier`), tak jak etykieta `Review ×N`, a nie biegi widoczne w `agents` (te są ucięte limitami).

### `eta`

Źródło: segment `eta: <operacja> do HH:MM` albo `eta: <operacja> until HH:MM` (`ETA_AT_RE`) NAJNOWSZEGO checkpointu przebiegu
(`.skillsync/runs/<run>/checkpoints.md`), w tej samej linii co `zrobione: …; w toku: …` (segmenty rozdziela `;`):

```
- 14:05 w toku: F1.2; eta: rysowanie 12 ekranów prototypu do 15:10
- 14:05 in progress: F1.2; eta: rendering 12 prototype screens until 15:10
```

Agent pisze segment PRZED długą operacją (spec, prototyp i jego iteracje, plan, krok implementacji), powtarza go w każdej linii
raportu aż do końca operacji (czytana jest tylko najnowsza linia) i po przekroczeniu podaje w nowej linii nowy termin (bez zatrzymywania pracy); czas bierze z `date`, nie z szacunku zegara.

- `{operation, until, untilIso, untilOriginalIso, since, steps, paused, shiftSeconds}`: `operation` to tekst przed ` do `, `until` to ORYGINALNE `HH:MM`
  z checkpointu, `since` to czas checkpointu niosącego szacunek, `untilOriginalIso` to pełna data z godziną rozwiązana jak w `timing` (start
  przebiegu `RUN=` plus przejścia przez północ; szacunek o ponad 12 h wcześniejszy niż checkpoint, który go niesie, liczy się jako następny dzień),
  `untilIso` = `untilOriginalIso` + `shiftSeconds` (czas zamkniętych okien oczekiwania NA UŻYTKOWNIKA od chwili ustawienia terminu; okno z `(kto: agent)`
  nie przesuwa), `steps` = id podkroków z „w toku” tej samej linii (`[]` dla spec, plan, review), `paused` = otwarte okno oczekiwania na użytkownika
  przy szacunku z krokami. Bez startu przebiegu `untilIso` i `untilOriginalIso` są null, a `shiftSeconds` 0. Konsument pokazuje godzinę z `untilIso`
  (czas lokalny), a bez niej z `until`.
- **Koniec szacunku: jedna tabela, kolejność od góry.** Seria szacunku = ciąg linii z `eta:` tej samej operacji (tekst bez różnicy wielkości liter
  i spacji) i tych samych kroków. 1) plan `done`/`implemented`: brak `eta`. 2) `eta: -` kończy. 3) `zrobione` wszystkich kroków serii kończy (także w linii
  z `czeka:`). 4) linia z `czeka:`: seria Z KROKAMI trwa (z `eta:` w tej linii dalej wg 5–6), seria BEZ kroków kończy się (np. „czeka: GO plan” po
  specu kasuje szacunek). 5) `eta:` tej samej operacji i kroków: trwa (inny termin przesuwa i zeruje `shiftSeconds`; ten sam termin niczego nie zmienia).
  6) `eta:` innej operacji albo kroków: kończy poprzednią, zaczyna nową. 7) pierwsza linia bez `czeka:` po linii z `czeka:`, bez `eta:`, z „w toku”
  któregoś kroku serii: seria z krokami trwa jeden raz. 8) każda inna linia bez `eta:` kończy szacunek. `eta` jest więc null, gdy seria nie jest otwarta
  po najnowszej linii.
- Serwer nie czyta zegara: „przekroczono” liczy konsument z `untilIso` (po przesunięciu). Renderer NIE pokazuje szacunku w pasku powiadomień: wiersz
  „<operacja>: koniec ok. HH:MM” z żywym odliczaniem („zostało MM:SS”, od godziny „H h MM min”; po terminie „przekroczono o MM:SS”) jest w kafelku
  aktywnego aktu (`data-eta-line="act"`) i przy aktywnym kroku z `eta.steps` (`data-eta-line="step"`). `tick()` aktualizuje tekst odliczania W MIEJSCU
  (`data-eta-countdown`, `role="timer"`, `aria-live="off"`, etykieta „<operacja>: koniec ok. HH:MM, <odliczanie>” odnawiana raz na minutę i w chwili przekroczenia; bez `untilIso` jest sama godzina, bez timera). Godzina „koniec ok.” z `untilIso` jest czasem lokalnym przeglądarki, więc u widza w innej strefie niż sesja różni się od `HH:MM` z checkpointu, bez przebudowy strony. `paused` pokazuje „wstrzymane: czeka
  na <co>” zamiast odliczania; `shiftSeconds` > 0 daje dymek o przesunięciu. Przy krokach serii zakończonej z `estimates[]` (`late`/`early`) dymek
  „szacunek X, było Y, czekanie na użytkownika Z”. Wartości wstawiane są jako tekst; widz widzi to samo (stan, nie wynik akcji). Renderer bez obsługi pola
  pokazuje toast z `until`/`untilIso`. Pole nie zależy od `approvals.here` ani `viewer`.

### `estimates`

Opcjonalne pole (brak = żaden checkpoint nie ma `eta:`; `schema: 1`, addytywnie), też w `dashboard metrics` (wraz z `estimateTotals`). Wiersz na SERIĘ szacunku
(patrz „Koniec szacunku” w sekcji `eta`), nie na krok: `{steps, operation, estimatedAt, etaFirst, etaLast, etaShifts, endedAt, estimatedSeconds, actualSeconds,
userWaitSeconds, errorSeconds, adjustedErrorSeconds, state}`. `estimatedAt` = chwila pierwszego `eta:` serii, `etaFirst`/`etaLast` = pierwszy i ostatni termin
(bez przesunięcia o czekanie), `etaShifts` = liczba zmian terminu w serii, `endedAt` = chwila linii kończącej serię, `estimatedSeconds = etaFirst − estimatedAt`,
`actualSeconds = endedAt − estimatedAt` (z czekaniem), `errorSeconds = endedAt − etaFirst` (dodatni = opóźnienie), `userWaitSeconds` = suma okien oczekiwania
na użytkownika z `timing.windows` (okno, którego każda część ma `(kto: agent)`, nie liczy się) przyciętych do `[estimatedAt, endedAt]`. **Asymetria:**
przy `errorSeconds > 0` `adjustedErrorSeconds = max(0, errorSeconds − userWaitSeconds)` (opóźnienie z oczekiwania na użytkownika nie jest błędem szacunku),
przy `errorSeconds ≤ 0` `adjustedErrorSeconds = errorSeconds` bez korekt (szacunek za duży liczy się zawsze). `state`: `late` (adjusted > 0), `early` (< 0),
`ontime` (== 0), `open` (seria niezakończona: tylko `estimatedAt`, `etaFirst`, `etaLast`, `etaShifts`), `unknown` (okno oczekiwania otwarte w przedziale:
`userWaitSeconds`, `adjustedErrorSeconds` null). Bez `RUN=` albo z nieczytelną godziną brak wierszy. `estimateTotals` (metryki): `{series, late, early, ontime, open,
unknown, lateSeconds, earlySeconds, medianAbsoluteErrorSeconds}` (sumy i mediana po seriach zamkniętych `late`/`early`/`ontime`; bez kalibracji stawek).

### `substeps[]`

Podkroki o id `Fn.k` z planu (każdy krok z kropką), w kolejności planu. Czas z checkpointów (rozdzielczość minuty).

| Pole | Typ | Opis |
|---|---|---|
| `id`, `title` | string | krok planu |
| `phase` | string | etykieta fazy (część przed `:` w nagłówku `### Faza n: …`) |
| `state` | string | jak `progress.phases[].steps[].state` |
| `done` | bool | zaznaczone `[x]` w planie |
| `startedAt` | `"HH:MM"` lub null | pierwszy checkpoint z id w „w toku” |
| `finishedAt` | `"HH:MM"` lub null | pierwszy checkpoint z id w „zrobione” |
| `seconds` | int lub null | `finishedAt − startedAt` w sekundach (przejście przez północ: +24 h); null, gdy brak któregoś znacznika |
| `missingData` | bool | `true`, gdy podkrok jest zrobiony (zaznaczony albo zgłoszony), a `seconds` = null |
| `noData` | bool | `true` ⇔ `seconds` = null (także dla podkroku zaplanowanego), to samo znaczenie co `noData` w `metrics`; pole addytywne |

### `tests`

Opcjonalne pole najwyższego poziomu (brak = zakładka „Testy” niewidoczna; `schema: 1`, addytywnie): wyniki testów kroków planu z plików `.skillsync/runs/<run-id>/tests/<krok>/<n>.jsonl` (zapisuje je `skillsync test-run`). Postać: `{sections: [...], failedTotal, runningTotal, skipped?}` (`skipped`: do 20 wpisów `{path, reason}` plików i katalogów, które pominięto, bo nie dało się ich złożyć; stały powód, bez treści; brak pola = nic nie pominięto; zły plik nie psuje reszty strony); sekcja: `{step: {id, title}|null, state, attempts: [...]}` (najnowsza sekcja pierwsza, do 5 ostatnich biegów kroku, najnowszy bieg pierwszy); bieg: `n`, `state` (`running`, `passed`, `failed`, `interrupted`), `startedAt`, `endedAt`, `durationMs`, `command` (zawsze zamaskowane), `tool`, `levels` (`command`, `counts`, `tests`), `source`, `exit`, `reason`, `counts {total, passed, failed, skipped, running, waiting}`, `truncated`, `groups[]`: `{name, state, total, counts, tests[]}`; w `tests[]` tylko testy czerwone i uruchomione (do 100 na grupę): `{id, name, state, group, ms, msg}`; resztę grupy daje `GET /api/tests?run=&step=&attempt=&group=&limit=&offset=` (tylko do odczytu, na obu gniazdach jak `/api/status`; `limit` domyślnie 500, maks. 5000).
Próba zawiera najwyżej 200 grup (`MAX_GROUPS`): najpierw grupy z porażkami, potem w toku, reszta po nazwie; ucięcie następuje po sortowaniu. Pola `groupsTotal` (liczba wszystkich grup próby) i `groupsOmitted` (liczba uciętych; 0, gdy nic nie ucięto) są zawsze obecne w próbie. Liczniki próby `counts` obejmują wszystkie testy, także w uciętych grupach; testy uciętej grupy są w `GET /api/tests` tak jak reszta grup (`group=` podaje nazwę grupy). Zmiana jest addytywna, `schema` zostaje 1.

Stan „polecenie zawiodło”: w nagłówku sekcji (widocznym przy zwiniętej treści) renderer pokazuje czerwony znacznik `kod N, żaden test nie zgłosił porażki`, gdy pokazany bieg nie jest `running`, ma `levels` `tests` lub `counts`, `exit` liczbowy różny od 0 i `counts.failed = 0` (wyjątek poza raportowanymi testami; wyniki ok dotyczą tylko testów, które zdążyły się wykonać). Stan wynika z istniejących pól `exit` i `counts`: zapis biegu i `failedTotal` (licznik zakładki) się nie zmieniają. Bez znacznika: `exit` 0, brak `exit`, porażki w `counts.failed`, bieg trwający.

Pominięte pliki (lista `tests.skipped`, opisana wyżej): przy niepustej liście renderer pokazuje w zakładce „Testy” notkę `role="note"`: „Pominięto N plików lub katalogów wyników testów (nie dało się ich uwzględnić): ścieżka (powód), …” (do 5 ścieżek, potem „i N więcej”; „co najmniej 20” przy 20 wpisach, bo serwer ucina listę do 20 i nie podaje sumy; powody mają polskie opisy: katalog spoza kroków, nieczytelny; wpis całego katalogu `tests` daje zdanie „Nie udało się odczytać wyników testów.”). Przy pustych sekcjach widać samą notkę i „Brak odczytanych wyników.”.

Przycisk „na górę” (haczyk `deps.scroll.toTop`, te same progi i histereza co w dokumencie) działa też w widoku Testy; bez haczyka go nie ma. Po ukryciu lub kliknięciu fokus wraca na zakładkę bieżącego widoku (`tab-<widok>`), a widok Testy ma zapas pod treścią (`padding-bottom` 4,5 rem).

Krok planu z testami ma w `progress.phases[].steps[]` opcjonalne `tests` (`{state, attempt, counts}`; pasek przy kroku i link do sekcji) i `test: true` dla kroku oznaczonego `[T]` (tytuł jest podawany bez znacznika). Stary renderer ignoruje oba pola.

**Maska:** odpowiedź strony przechodzi przez jedną funkcję (`dashboard_tests.finalize_status`): wszystko jest maskowane, z wyjątkiem liści będących wynikiem testu (`id`, `name`, `group`, `msg`, nazwa grupy), które są surowe, dopóki w manifeście nie ma `tests.mask: true`. Dotyczy to obu gniazd, także gdy port odczytu jest widoczny w sieci lokalnej (zob. `docs/security.md`).

### `timing`

Czas pracy i czas czekania na człowieka, liczone z dziennika `.skillsync/runs/<run-id>/checkpoints.md` (znaczniki „w toku”,
„zrobione”, `czeka:`) i z `.skillsync/runs/<run-id>/approvals.jsonl` (zgody). Nic nie jest szacowane: czego nie da się
policzyć, jest jawnie oznaczone `unknown`. Ta sama funkcja (`build_timing`) daje `timing` w `show` i w `dashboard metrics`.

**Czas bezwzględny.** Checkpoint ma tylko `HH:MM`, więc pierwszy odczytuje się względem `RUN=<iso>` (start przebiegu), a zegar
cofający się względem poprzedniego checkpointu to następna doba. `HH:MM` jest czasem w strefie z `RUN=`, rozdzielczość to minuta
(zgody z `approvals.jsonl` mają sekundy, więc różnica do 59 s jest możliwa). Bez `RUN=`, przy nieczytelnym czasie albo gdy pierwszy
checkpoint jest wcześniejszy niż `RUN=` (zegar innej strefy, błędny start: nie zgadujemy doby) `available: false` z `reason`, a listy są puste.

**Okno czekania** (`windows[]`) otwiera checkpoint z `czeka: <co>`. Zamyka je:

- zgoda z `approvals.jsonl` o słowie wymienionym w `<co>` (`GO plan`, `GO implement`, `CP`, `READY`), pierwsza o `at` nie
  wcześniejszym niż otwarcie okna (`source: "approval"`); albo
- następny checkpoint bez `czeka:` (`source: "checkpoint"`), gdy `<co>` nie nazywa zgody (np. odpowiedź na pytanie) albo zgodę
  dano w sesji, a nie przyciskiem (brak wpisu w pliku zgód: dokładność do kolejnego ruchu sesji).

Kolejny checkpoint z `czeka:`, gdy poprzednie okno nie jest jeszcze zamknięte zgodą, jest tym samym oknem (teksty `what` łączone
`; `); po zgodzie otwiera nowe okno. Okno, którego nic nie zamyka (ostatni checkpoint przebiegu to `czeka:` i brak zgody), ma
`unknown: true`, `seconds: null`, `endedAt: null`, `source: null`; trwające czekanie nie jest liczone do bieżącej chwili.

**Czas pracy kroku** = pierwszy checkpoint z id w „w toku” do pierwszego z id w „zrobione” minus okna czekania w tym przedziale.
Krok bez któregoś znacznika (albo zrobiony `[x]` bez żadnego), z końcem przed początkiem albo obejmujący okno `unknown` ma
`unknown: true`, `workSeconds: null` i `reason` (`no-start`, `no-finish`, `order`, `open-window`). Krok zaplanowany bez znaczników
nie występuje w liście.

| Pole | Typ | Opis |
|---|---|---|
| `available` | bool | `false`, gdy nie da się ustalić czasu bezwzględnego (brak `RUN=`) |
| `reason` | string lub null | powód `available: false` |
| `windows[]` | tablica | `{what, startedAt, endedAt, seconds, source, unknown}`; czasy ISO 8601 ze strefą startu przebiegu; `unknown` ⇔ `seconds` = null ⇔ `source` = null |
| `steps[]` | tablica | `{id, phase, startedAt, finishedAt, workSeconds, waitSeconds, unknown, reason}`; `waitSeconds` = części okien w przedziale kroku (null przy `unknown`); `phase` = etykieta fazy (część przed `:` w nagłówku `### Faza n: …`, jak `progress.phases[].label`; ta sama w `show` i w `metrics`) |
| `stages[]` | tablica | per faza planu z danymi: `{phase, name, workSeconds, waitSeconds, unknownSteps, complete}`; `workSeconds` = suma czasów pracy znanych kroków fazy (kroki równoległe sumują się), `waitSeconds` = część okien w przedziale od pierwszego startu do ostatniego końca znanych kroków fazy (okno liczone raz); `complete` ⇔ nie ma kroków `unknown` i `waitSeconds` jest znane |
| `acts[]` | tablica | POMIAR czasu aktów I i II: `{key: "spec"\|"plan", startedAt, endedAt, workSeconds, waitSeconds, unknown, reason, endSource}`; akt I = od `RUN=` do pierwszej zgody `GO plan` z `approvals.jsonl`, akt II = od niej do pierwszego `GO implement`; czas pracy = trwanie minus okna czekania (jak krok). Bez zgody w pliku (zgodę dano w sesji albo akt trwa) `endedAt: null`, `unknown: true`, `reason: "no-approval"`; okno nic nie zamykające w akcie: `reason: "open-window"`; koniec przed początkiem: `"order"`. Akt II jest na liście tylko, gdy znany jest `GO plan`. Bez `RUN=` lista jest pusta. Nic nie jest szacowane |
| `totals` | obiekt | `{workSeconds, waitSeconds, windows, unknownWindows, unknownSteps, complete}`; `waitSeconds` = suma okien znanych, `workSeconds` = suma faz z czasem pracy (null, gdy żadna faza nie ma znanego czasu pracy: „brak danych”, nie zero), `complete` ⇔ brak okien i kroków `unknown` i `available` |

Renderer pokazuje w akcie III rozwijaną sekcję „Czas pracy i czekania”: sumy na fazę (praca, czekanie, znacznik „niepełne” przy
`complete: false`) oraz sumę przebiegu, a w dymku okna czekania (`what`, od–do, źródło albo „nieznane”). Brak danych to
„brak danych”, nie zero. Wartości wstawiane są jako tekst.

### `forecast`

PROGNOZA, nie pomiar: ile jeszcze PRACY do końca kroków planu, jeśli nikt na nic nie będzie czekał. Czysta funkcja `timing` i `progress` (`dashboard_forecast.build_forecast`), bez czytania plików i zegara; jest w `show` i `GET /api/status` (nie w `dashboard metrics`, który nie ma `progress`). Osobne pole od `eta` (deklaracja agenta o końcu jednej operacji), które zostaje bez zmian.

**Model (tempo przebiegu).** Odstępy między kolejnymi zakończeniami kroków (kroki z oboma znacznikami „w toku” i „zrobione”, w kolejności zakończenia; pierwszy odstęp od pierwszego „w toku”; odstęp dzielą po równo kroki zakończone w tym samym momencie, bo sesja często zgłasza kilka kroków jednym checkpointem), okna czekania wewnątrz odstępu odjęte (noc albo restart sesji BEZ markera `czeka:` wchodzi do próbki jako praca i zawyża tempo); odstęp, którego nie da się policzyć (okno bez końca przed jego końcem), jest pomijany. Wynik: `zostało kroków × kwartyl` (zakres) i `× mediana` (wartość środkowa), kwartyle metodą włączającą. Zakres kwartylowy nie jest gwarancją pokrycia (zob. wynik powtórki niżej). Tempo jest całego przebiegu, więc kroki równoległe są uwzględnione; rozmiaru kroku, zmiany tempa ani czekania na ludzi nie modelujemy. „Zostało kroków” = kroki planu bez `[x]` i bez zgłoszonych jako zrobione.

**Minimum danych:** 4 zakończone kroki z czasem w bieżącym przebiegu (stała `MIN_FINISHED_STEPS`), zakończone w co najmniej 3 różnych momentach (`MIN_FINISH_MOMENTS`; kroki zgłoszone jednym checkpointem to jedna obserwacja, nie cztery). Brak danych albo słabe dane = brak liczby z powodem, nigdy zgadywanie. Rzetelność: zakres kwartylowy pokrywa rzeczywisty czas słabiej niż nominalne 50%, a środek bywa mylny. Wniosek: zakres jest rzędem wielkości, nie obietnicą, więc strona opisuje go jako orientacyjny („orientacyjnie”, „Rząd wielkości, nie obietnica.”), a dymek podaje wzór i zastrzeżenie. Domyślnie strona pokazuje zakres z orientacyjną godziną końca; wariant samego tempa zostaje jako opcja.

| Pole | Typ | Opis |
|---|---|---|
| `scope` | string | `"plan"` |
| `reason` | string | tylko bez liczby: `plan-not-running` (plan nie jest `in_progress`), `no-run-start` (brak czasu bezwzględnego, jak `timing.available: false`), `no-remaining` (wszystkie kroki zrobione), `not-enough-data` (z `have` i `need`: zakończone kroki z czasem i minimum), `few-moments` (z `have` i `need`: różne momenty zakończenia i minimum) |
| `remaining` | int | kroków do końca |
| `samples` | int | liczba odstępów w próbce |
| `basis` | string | `"run"` (tempo bieżącego przebiegu; historia międzyprzebiegowa nie jest używana) |
| `paused` | bool | najnowszy checkpoint ma `czeka:`: zakres to praca bez czekania, strona nie podaje godziny końca |
| `lowSeconds`, `medianSeconds`, `highSeconds` | int | pierwszy kwartyl, mediana i trzeci kwartyl × `remaining`, w sekundach pracy |

**Widok na stronie.** Pole `forecast` w JSON niesie zakres (`lowSeconds`, `medianSeconds`, `highSeconds`); domyślnie strona pokazuje zakres pracy do końca kroków i orientacyjną godzinę końca (`orientacyjnie: do końca kroków ok. A–B pracy (zostało M kroków, tempo z K próbek…), bez czekania na zgody; koniec ok. HH:MM–HH:MM. Rząd wielkości, nie obietnica.`; godzina liczona z `deps.now` w chwili odświeżenia strony, bez godziny przy `paused`). Opcja `tempo` pokazuje samo tempo: „orientacyjnie, tempo: ok. N min pracy na krok, z K zakończonych kroków; zostało M kroków” (`medianSeconds / remaining`), bez sumy i godziny. Widok wybiera stała `FORECAST_VIEW` renderera (`"range"` domyślnie) albo opcja hosta `deps.forecastView` (`"range"` / `"tempo"`); zmiana nie zmienia JSON-a. Renderer pokazuje prognozę inaczej niż pomiar: etykieta „Prognoza:”, przerywana ramka i kursywa, zawsze przedział, a bez liczby powód (domyślny widok `range` pokazuje przedział, opcja `tempo` samo tempo); pomiar („Pomiar:”, ciągła ramka) to `timing.acts` w kafelkach aktów I i II. Klient bez `forecast` albo z uszkodzonym polem nie pokazuje wiersza.

## Metryki przebiegu (`dashboard metrics`)

`skillsync dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` liczy ogólne metryki jednego przebiegu (bez
formatów warstw). Tylko odczyt; bez `--json` drukuje ten sam JSON. Wejście:

- dziennik `.skillsync/runs/<run-id>/checkpoints.md` (te same znaczniki „w toku”/„zrobione” co `substeps[]`; czasy liczy ta
  sama funkcja co `show`, więc podkrok z czasem w obu wynikach ma identyczne `seconds`, `startedAt`, `finishedAt`);
- rekordy checkpointów `.skillsync/runs/<run-id>.json` (`skillsync checkpoint`), jeśli plik jest;
- plan: `--plan` (ścieżka względna planu projektu; nieznany plan = `BLOCKED`), bez niego plan z linią `Przebieg: <run-id>` albo `Run: <run-id>` (zob. `plans[].run`; wybór
  jak dla `show`, zob. `planAuto`). Bez planu podkroki to id `Fn.k` z dziennika w
  kolejności pierwszego wystąpienia (`title` = null).

| Pole | Typ | Opis |
|---|---|---|
| `schema` | int | `1` |
| `status` | `"PASS"` | odmowa ma kształt `{schema, status: "BLOCKED", reason}` |
| `run` | string | id przebiegu |
| `plan` | string lub null | plan, z którego pochodzą podkroki; null = bez planu |
| `generatedAt` | string | czas wygenerowania, ISO 8601 z przesunięciem strefy |
| `substeps[]` | tablica | podkroki `Fn.k` w kolejności planu (patrz niżej) |
| `phases[]` | tablica | `{id, seconds}`: `id` = część id podkroku przed kropką (`F1`), w kolejności pierwszego podkroku; `seconds` = suma czasów podkroków fazy albo null, gdy choć jeden podkrok fazy nie ma danych |
| `totals` | obiekt | `substeps` (liczba), `withData`, `withoutData` (int), `seconds` (suma wszystkich podkroków albo null, gdy któryś nie ma danych lub nie ma podkroków), `measuredSeconds` (suma podkroków z danymi albo null, gdy żaden ich nie ma; wartość częściowa, zawsze czytana razem z `withoutData`) |
| `rounds` | obiekt | `records` (liczba rekordów), `failed` (`FAIL`+`BLOCKED`), `notRun` (`NOT_RUN`), `repeatedSteps` (rekordy kroku zapisanego już wcześniej); liczone jak w `skillsync metrics` i `skillsync retro`; bez pliku rekordów wszystkie cztery = null (brak danych, nie zero) |
| `failures[]` | tablica | `{step, status, causeKey}` dla rekordów `FAIL`/`BLOCKED` w kolejności pliku; `causeKey` = `cause_key` rekordu lub null; bez rekordów pusta |
| `interventions` | null | zarezerwowane; rdzeń nie mierzy interwencji człowieka |
| `estimates[]` | tablica (opcjonalne) | wiersze serii szacunku jak w polu `estimates` strony statusu (sekcja `estimates`); brak pola = żaden checkpoint nie ma linii `eta:` |
| `estimateTotals` | obiekt (opcjonalne) | sumy po seriach szacunku, tylko razem z `estimates[]` (kształt w sekcji `estimates`) |
| `agents[]` | tablica (opcjonalne) | tylko gdy przebieg ma dziennik `agents.jsonl`: surowe wiersze WSZYSTKICH biegów w kolejności `start`, bez limitów strony i bez filtra szumu (czytane najwyżej 8 MiB od ogona): `{agent, role, kind, profile, step, round, startedAt, etaFirst, etaShifts, etaMoves[], endedAt, seconds, result}`; `etaFirst` = pierwszy termin ze `start`, `etaMoves[]` = każde przesunięcie `{at, eta}`, `endedAt`/`seconds`/`result` = null dla biegu bez `end`. Bez statystyk i bez kalibracji (źródło dla N17) |
| `timing` | obiekt | czas pracy i czekania per krok i faza (jak w `show`, sekcja `timing`); plan z `--plan`/`Przebieg:`, zgody z `approvals.jsonl` przebiegu |

`substeps[]`:

| Pole | Typ | Opis |
|---|---|---|
| `id` | string | id podkroku |
| `title` | string lub null | tytuł z planu; null bez planu |
| `startedAt`, `finishedAt` | `"HH:MM"` lub null | pierwszy checkpoint z id w „w toku” / „zrobione” (także, gdy brakuje drugiego znacznika) |
| `seconds` | int lub null | `finishedAt − startedAt` (przejście przez północ: +24 h); bez obu znaczników suma `duration_seconds` rekordów kroku (zaokrąglona do sekundy), o ile każdy rekord ma czas; w przeciwnym razie null |
| `noData` | bool | `true` ⇔ `seconds` = null; czas nigdy nie jest szacowany |
| `source` | `"checkpoints.md"`, `"records"` albo null | skąd pochodzi `seconds`; null przy `noData` |

Różnica wobec `substeps[].missingData` w `show`: `missingData` dotyczy tylko podkroków zrobionych, `noData` każdego podkroku
bez czasu (także zaplanowanego). `show` ma oba pola.

Kody wyjścia: `0` = PASS; `2` = BLOCKED: brak projektu, id przebiegu spoza wzorca (`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`, np.
`../x`), brak katalogu `.skillsync/runs/<run-id>`, dowiązanie (`.skillsync`, `.skillsync/runs`, katalog przebiegu,
`checkpoints.md`, plik rekordów), nieczytelny plik rekordów, nieznany `--plan`.

Fixtures: `tests/fixtures/dashboard/<przypadek>/expected-metrics.json` (przypadki `plan-in-progress` z plikiem rekordów,
`silence`, `run-mode`); normalizacja: `generatedAt` = `2026-01-12T12:00:00+00:00`, JSON jak w `expected.json`.

## Zgodność z `skillsync status`

`skillsync status` (`skillsync_workflow/status.py`) jest wąskim, stabilnym raportem planów/przebiegów/zgód (`schema: 1`
własnego kontraktu) i pozostaje bez zmian. Dashboard (`skillsync_workflow/dashboard.py`) używa jego funkcji tam, gdzie
semantyka jest ta sama:

| Funkcja `status.py` | Użycie w dashboardzie |
|---|---|
| `git_blob_sha` | `plan.fileSha`, `spec.fileSha`, sha akcji GO |
| `_status_fields` | `status` i `statusText` planów, speców i dokumentów (słowo statusu jak w pliku, bez listy dozwolonych; brak linii = null) |
| `APPROVAL_WORDS`, `_approval_at`, `_APPROVAL_SHA`, `MAX_APPROVALS_BYTES`, `APPROVALS_FILE` | reguły przyjęcia wpisu zgody |
| `delivery_mode.delivery_info` | `delivery.mode/base/source` (ta sama wartość, którą zwraca `route`; błąd walidacji = `mode: invalid`, `source: manifest`) |

Różnice rozstrzygnięte świadomie:

- Postęp: `status._progress` liczy każdą linię `- [ ]`/`- [x]` sekcji `## Progress` (także bez id i w blokach kodu), a dla
  starszych planów listę z `## Kroki` / `## Steps`. Dashboard (`parse_progress`) liczy tylko kroki z id (`S1`, `F1.2`, `A-1`)
  poza blokami kodu, grupuje je w fazy `### …` i nie czyta `## Kroki`. Dla planów z `## Progress`, w których każdy krok ma id
  (format skilla `feature`), `progress.total`/`progress.done` = `plans[].steps` z `status`; test zgodności porównuje oba
  polecenia na wszystkich fixtures i przypina różnicę na przykładzie.
- Zgody: `status.read_approvals` zwraca najnowszy wpis na parę (przebieg, słowo) ze wszystkich przebiegów, znormalizowany
  (bez `by`, `schema`; `planState` jest przenoszone, gdy ma status z listy). Dashboard pokazuje wpisy jednego przebiegu w postaci zapisanej (`given`, `last`), więc ma własny
  odczyt pliku z tymi samymi regułami przyjęcia wpisu.
- Odbiór planu (`needsHuman: acceptance`, `status.acceptance_pending`): przy `tracked: true` o zamknięciu rządzi git (commit ze zgodą,
  w którym plan jest `done`). Przy `tracked: false` plany nie mają historii, więc odbiór jest zamknięty, gdy ostatni wpis (przebieg,
  słowo zamykające trybu: `READY` w `pr`, `CP` w `direct`) ma `planState: done`, jego sha jest commitem repozytorium (`_commit_exists`) i plan jest teraz `done`; późniejszy wpis z innym
  stanem otwiera odbiór ponownie, zmiana innych linii planu (np. `Oddanie:`) nie. Wpis bez `planState` w tym trybie niczego nie zamyka.
- Status planu: `_status_fields` czyta pierwszą linię statusu z pierwszych 4000 znaków (także `**Status:**`, `- Status:`);
  słowo statusu to litery i `_` (zapis `in-progress` daje `in`, nie `in_progress`).

## Serwer (`dashboard serve`)

`skillsync dashboard serve <projekt> [--port N] | [--read-port N] [--approve-port M] [--read-bind A] [--approve-bind A] [--allow-proxy-headers]
[--prototype-port N (domyślnie `prototype.port` z manifestu, inaczej 5130)] [--host H] [--approve-host H ...] [--read-host H ...] [--no-approvals] [--plan <rel>]
[--session-url https://…] [--poll SEK] [--verbose] [--layer id=ścieżka ...] [--standalone] [--all-tabs] [--json]` (moduł `skillsync_workflow/dashboard_http.py`, ładowany leniwie; tryb
dwóch gniazd: `skillsync_workflow/dashboard_ports.py`) działa na pierwszym planie; Ctrl+C albo SIGTERM kończy go raportem
`{"status": "PASS", "url", "stopped": true}` (kod `0`; w trybie dwóch gniazd `{"status": "PASS", "stopped": true, "ports": {"read": N, "approve": M}}`, bez pól „read”/„approve” dla pustego gniazda).
Odmowa startu = `BLOCKED` (kod `2`): port spoza 5100–5199 albo zakazany (4444–4448, 9222, 9333), brak manifestu, brak
`skills/dashboard/assets/index.html` w aktywnych warstwach, `--session-url` inny niż https bez danych logowania, `--plan`
spoza dozwolonych dokumentów, port zajęty (z numerem portu i właścicielem, zob. „Port zajęty”), nieznany klucz albo zła
wartość w `dashboard:` (zob. „Ustawienia `dashboard:`”), kombinacja flag z tabeli niżej. Port wybiera się per projekt.
Bez flag portów (`serve <projekt>`) działa tryb dwóch gniazd z portami domyślnymi: odczyt 5140, zgody 5141.

### Tryb dwóch gniazd

Bez `--port` serwer uruchamia dwa gniazda HTTP: domyślnie odczyt na `5140` i zgody na `5141`. Jawne `--read-port N` i/lub
`--approve-port M` uruchamia po jednym gnieździe HTTP na podany port (flagi nie mają wartości domyślnej; numer podaje się
sam). Samo pojedyncze gniazdo nie dostaje drugiego numeru. Oba gniazda dzielą jeden stan projektu (jeden watcher, jeden strumień SSE). Rola wynika z **gniazda**, nie z nagłówka `Host`:

| Gniazdo | Rola (`approvals.role`) | Zachowanie |
|---|---|---|
| odczyt (`--read-port`) | `read` | `GET`/`HEAD` jak na stronie; każda metoda zapisu (`POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS`) = 404, przed innymi sprawdzeniami; `approvals`: `here` false, `viewer` true, `tokenRequired` false; `/healthz` `{ok, version, serverId}` (bez `tokenId`); brak tokenu w odpowiedziach |
| zgód (`--approve-port`) | `approve` | `GET`/`HEAD` jak na odczycie oraz `POST /api/approve` i `POST /api/feedback-approve` z kolejnością odmów poniżej; `here` true, `tokenRequired` true (gdy są zgody); `/healthz` `{ok, version, serverId[, tokenId]}` |

Tryb `--port` (jedno gniazdo) ma rolę z nagłówka `Host` (akapit „Kontrola `Host`” w sekcji „Serwer”), nie ma
pola `approvals.role`, a kolejność odmów to: `Host`, trasa, `X-Status-Board`, `Origin` (porównanie samej nazwy),
`Content-Type`, token. Tryb wybiera się jawnie (`--port N`); `serve` bez flag portów to tryb dwóch gniazd.

**Nasłuch.** W trybie dwóch gniazd odczyt domyślnie na `0.0.0.0` (każdy interfejs, także LAN), zgody domyślnie na `127.0.0.1`.
Kolejność źródeł dla gniazda odczytu: flaga `--read-bind A`, potem `--host H` (tylko odczyt; `--host ''` = wszystkie
interfejsy), potem `dashboard:` w manifeście projektu, potem `dashboard:` w configu maszyny, na końcu domyślne `0.0.0.0`.
Dla gniazda zgód: flaga `--approve-bind A`, potem `dashboard:` (manifest, config maszyny), na końcu `127.0.0.1`. `--host`
NIE dotyczy gniazda zgód: zgody z innego urządzenia wymagają `--approve-bind 0.0.0.0` albo `approve_bind` w `dashboard:`.
Jawny bind wygrywa ze skrótem `local_only` w tym samym źródle. Adres: literał IP, `localhost`, `0.0.0.0` albo `::`. Gdy
`--no-approvals` bez `--approve-port`, startuje tylko gniazdo odczytu (5140). W trybie `--port` nasłuch to `127.0.0.1`, chyba że podano `--host`.
Przy każdym gnieździe poza loopbackiem konsola wypisuje ostrzeżenie; dla gniazda zgód jest ono osobne: port zgód widzi każde
urządzenie w sieci, a token idzie nieszyfrowanym HTTP.

**Ustawienia `dashboard:`.** Sekcja `dashboard:` jest w configu maszyny (`~/.config/skillsync/config.yaml`) i w manifeście projektu.
Klucze: `local_only: true|false` (`true`: oba gniazda na `127.0.0.1`; `false`: brak preferencji), `read_bind`, `approve_bind`
(literał IP, `localhost`, `0.0.0.0` albo `::`). Pierwszeństwo: flaga CLI (`--read-bind`/`--approve-bind`, dla odczytu potem `--host`) >
manifest > config maszyny > domyślne; manifest może tylko ZAWĘŻAĆ (loopback albo `local_only: true`), szerszy adres w nim blokuje `serve`, a uszkodzony config maszyny blokuje `serve` także przy flagach pokrywających oba gniazda. Nieznany klucz: `doctor` ostrzega, `serve` BLOCKED z nazwą klucza. Zła wartość: błąd
z nazwą klucza, nigdy z wartością. Dla tego ustawienia nie ma zmiennej środowiskowej: tylko config i manifest.

**Port zajęty.** Przed bindem każde gniazdo jest sondowane połączeniem `connect` na loopback (`dashboard_ports.port_taken`)
na każdym systemie: gdy coś już przyjmuje połączenia na tym porcie, start kończy się `BLOCKED` (exit 2) z numerem portu,
flagami do zmiany (`--read-port` / `--approve-port`) i właścicielem portu: projekt z plików stanu albo „inny proces”. Sonda
jest potrzebna, bo na macOS/BSD (`SO_REUSEADDR`) i na Windows węższy adres może związać się obok cudzego `*:P`, a sam bind
tego nie wykryje. Serwer nie przechodzi na inny port. Na Windows gniazda dwóch portów dostają `SO_EXCLUSIVEADDRUSE`:
drugi proces nie przejmie węższego adresu.

**Konsola przy starcie.** Przy odczycie poza loopbackiem jedno ostrzeżenie o dostępności strony dla każdego urządzenia w sieci
lokalnej (plany, specyfikacje, wyniki, sekrety zredagowane heurystycznie, ale wyniki testów jawne, bez maski; ruch nieszyfrowany), z podpowiedzią `--read-bind 127.0.0.1`
albo `dashboard.local_only: true`. Adresy IPv4 maszyny (sieć tylko-IPv6 ich nie
dostanie) są wypisane po jednym w linii, z adresem po nazwie maszyny (`http://<nazwa>.local:5140/`); nazwa maszyny (`socket.gethostname()`)
pojawia się w komunikacie startowym. Linia o zgodach tylko lokalnych podaje `--approve-bind 0.0.0.0` (token idzie nieszyfrowanym HTTP).
Gdy oba gniazda słuchają na loopbacku, konsola pisze „strona tylko na tej maszynie”. Na Windows, przy pierwszym nasłuchu na
`0.0.0.0`, jest linia o Zaporze Windows: „Zezwól” w sieci prywatnej daje podgląd z telefonu, odmowa zostawia stronę lokalnie,
w sieci publicznej nie zezwalaj. Na macOS zapora aplikacji może też zapytać o dostęp przy pierwszym nasłuchu.

**Linie konsoli** (jedna linia na gniazdo): `odczyt (do tunelu i linków): <adres>  [nasłuch …, bez zgód i tokenu]`
(bez tokenu) oraz `zgody (przyciski, tylko dla Ciebie, nigdy do tunelu): <adres z tokenem>  [nasłuch …]`.

**Start atomowy.** Oba gniazda wiązane są przed wątkami, plikami stanu i wpisami w konsoli. Błąd drugiego wiązania zamyka
pierwsze i kończy `BLOCKED` (`nie można nasłuchiwać …; porty … muszą być wolne`, z numerem portu i właścicielem) bez śladu
stanu. Ctrl+C albo SIGTERM zamyka oba gniazda i usuwa oba pliki stanu.

**Jak zatrzymać serwer na Windows** (`dashboard serve` i `prototype serve`; na Linuksie i macOS bez zmian: Ctrl+C albo SIGTERM). Kolumna „Pomiar” odsyła do zapisu w [docs/measurements/](measurements/README.md) (data, system, wersja, polecenie, wynik) albo mówi, że wiersz jest tylko oczekiwany z dokumentacji systemu i nie był mierzony:

| Sposób | Skutek | Plik stanu po zatrzymaniu | Pomiar |
|---|---|---|---|
| Ctrl+C w konsoli serwera | łagodnie: raport `PASS`/`stopped`, kod wyjścia 0 | usunięty | zmierzone: [win-stop-ctrl-c](measurements/win-stop-ctrl-c.md) |
| `CTRL_BREAK_EVENT` do serwera uruchomionego z `CREATE_NEW_PROCESS_GROUP` (`os.kill(pid, signal.CTRL_BREAK_EVENT)` z launchera, wspólna konsola) | łagodnie jak Ctrl+C (sygnał `SIGBREAK`), kod wyjścia 0; drugi CTRL_BREAK w ciągu 10 s po pierwszym jest ignorowany (nie przerywa sprzątania; zmierzono drugi sygnał po 0,15 s, okno 10 s wynika z kodu), późniejszy zatrzymuje ponownie | usunięty (w trybie dwóch gniazd oba pliki) | zmierzone: [win-stop-ctrl-break](measurements/win-stop-ctrl-break.md) |
| `Stop-Process`, `taskkill /F`, `Popen.terminate()` | twardo (`TerminateProcess`), bez sprzątania | **zostaje** (z tokenem zgód) | zmierzone: [win-stop-hard-kill](measurements/win-stop-hard-kill.md) |
| `taskkill /PID` bez `/F` | nie działa: „This process can only be terminated forcefully (with /F option).” | – | zmierzone: [win-stop-taskkill-no-f](measurements/win-stop-taskkill-no-f.md) |
| zamknięcie okna konsoli (przycisk „Zamknij”) | łagodnie: system wysyła `CTRL_CLOSE_EVENT`, serwer sprząta (handler czeka najwyżej `min(4 s, limit systemowy − 1 s)`, w pomiarze limit 5000 ms), potem system kończy proces (kod wyjścia niezmierzony); poza limitem albo bez handlera twardo | usunięty | zmierzone w klasycznej konsoli (`ConsoleWindowClass`): [win-stop-console-close](measurements/win-stop-console-close.md); Windows Terminal (pseudokonsola) niezmierzony |
| Menedżer zadań („Zakończ zadanie”) dla aplikacji konsolowej | oczekiwane: według dokumentacji systemu to samo zdarzenie `CTRL_CLOSE_EVENT` co przy zamknięciu okna, więc łagodnie jak wyżej; dla procesu bez okna możliwe zakończenie przez `TerminateProcess` (twardo) | oczekiwane: usunięty albo zostaje | niezmierzone (oczekiwane) |

Po twardym zakończeniu port jest wolny od razu, a następny start na tym samym porcie zastępuje plik stanu; plik innego portu zostaje, aż ten port zostanie użyty ponownie. Ctrl+C dziedziczy ustawienie „ignoruj CTRL+C” po procesie rodzicu (np. skrypt uruchomiony z sesji ssh): wtedy serwer uruchomiony z takiego procesu go nie odbierze, a CTRL_BREAK tak. Dziecko uruchomione z `CREATE_NEW_PROCESS_GROUP` w ogóle nie dostaje Ctrl+C (system wyłącza go w nowej grupie), więc launcher zatrzyma je tylko CTRL_BREAKiem.

**Kombinacje flag** (wszystkie walidacje przed bindem; `BLOCKED`, kod `2`):

| Wywołanie | Wynik |
|---|---|
| `serve <projekt>` | dwa gniazda: odczyt 5140 na `0.0.0.0`, zgody 5141 na `127.0.0.1` |
| `serve --no-approvals` | tylko odczyt 5140 na `0.0.0.0` |
| `serve --port N` (z `--host`, `--approve-host`, `--read-host`) | jawny tryb jednego portu, `127.0.0.1` (chyba że `--host`) |
| `serve --host A` | BLOCKED z podpowiedzią: tryb jednego portu `--port N --host A`, tryb dwóch gniazd `--read-bind A` (odczyt) i `--approve-bind A` (zgody) |
| `serve --approve-host H` | BLOCKED z podpowiedzią: tryb jednego portu (także za tunelem) `--port N --approve-host H` albo jawne `--approve-port M --approve-host H` |
| `serve --read-host H` | dwa gniazda; `H` przyjęty na odczycie, gniazdo zgód bez zmian |
| `serve --read-bind A` / `--approve-bind A` | dwa gniazda z podanym bindem |
| `serve --allow-proxy-headers` | dwa gniazda; dotyczy gniazda zgód |
| `serve --read-port N --approve-port M` | odczyt na `0.0.0.0`, zgody na `127.0.0.1` |
| `serve --read-port N --approve-port M --host A` | odczyt na `A`, zgody na `127.0.0.1` |
| `serve --approve-port M --host A` (bez `--read-port`) | BLOCKED: `--host` dotyczy odczytu |
| `serve --port N` z `--read-bind`, `--approve-bind`, `--allow-proxy-headers`, `--read-port` albo `--approve-port` | BLOCKED |
| `--port N` z `--read-port` i `--approve-port` razem | BLOCKED (niejednoznaczne) |
| `--read-port` i `--approve-port` z tym samym numerem | BLOCKED |
| `--no-approvals` z `--approve-port` albo z `--approve-bind` | BLOCKED (port zgód nie startuje bez tokenu) |
| `--no-approvals` z `--read-port` | dozwolone: tylko odczyt |
| `--allow-proxy-headers` z `--no-approvals`, z samym `--read-port` albo z `--port` | BLOCKED (działa tylko z gniazdem zgód) |
| `--approve-host '*'` | BLOCKED |

Bez portów w trybie domyślnym dozwolone są `--read-host`, `--read-bind`, `--approve-bind` i `--allow-proxy-headers` (ostatnie
dwa dotyczą gniazda zgód; `--approve-bind` nie łączy się z `--no-approvals`). Gdy `--host` albo `--approve-host` jest podane
bez portów, start kończy się BLOCKED z podpowiedzią: zapis trybu jednego portu (`--port N --host A`, `--port N --approve-host H`) i trybu dwóch gniazd (`--read-bind`, `--approve-bind`).

`--approve-host` z samym `--read-port` jest dozwolone: jego nazwy otwierają również odczyt. `--read-host '*'` (dowolna nazwa)
działa tylko na gnieździe odczytu; na gnieździe zgód nie daje dostępu.

Jawne `--read-port N` / `--approve-port M` bez bindów: odczyt na `0.0.0.0`, zgody na `127.0.0.1`. Zgody z innego urządzenia
wymagają `--approve-bind 0.0.0.0` albo ustawienia `approve_bind` w `dashboard:`. `--host X` przesuwa tylko bind odczytu
(`--host ''` = wszystkie interfejsy); gniazdo zgód zostaje na `127.0.0.1`, dopóki nie ma jawnego `--approve-bind` albo `approve_bind`.

### Serwer projektu i zakładki

Jeden projekt git (wspólny katalog git, `commondir` po `realpath`) ma jeden serwer rodzaju `project` na jednym zestawie portów; obsługuje główny katalog (jeśli istnieje) i powiązane worktree jako **zakładki**. Serwer rodzaju `standalone` (start z `--standalone`, `--no-approvals`, `--layer`, z flagą ustawień obok działającego serwera projektu albo z katalogu, który nie przechodzi walidacji zakładki) ma jedną zakładkę katalogu startu, nie przyjmuje innych i nie jest celem dopisania. Rodzaj ustala start i nie zmienia się w trakcie życia serwera. Komendy (`serve`, dopisanie, `url`, flagi `--standalone`, `--all-tabs`, `--json`, kody `BLOCKED`): [cli.md](cli.md). Reguły rejestru i pułapki: [domain/dashboard-server](domain/dashboard-server.md).

**Zakładka** to katalog roboczy z własnym `Board` (korzeń, cache, blokada zgód, ścieżki feedbacku). Wspólne dla serwera są gniazda i opcje nasłuchu, sekret procesu, `serverId` i limity: najwyżej 16 widocznych zakładek, jeden wątek obserwatora, jeden licznik SSE (64 łącznie, 48 na gnieździe odczytu i 16 na gnieździe zgód, niezależnie od liczby zakładek) i jeden licznik nieudanych prób tokenu. Id zakładki: `main` (główny katalog) albo pierwsze 12 znaków szesnastkowych `sha256` znormalizowanej ścieżki (na Windows i macOS bez rozróżniania wielkości liter); musi pasować do `^(main|[0-9a-f]{12})$` i jest wyłącznie kluczem słownika rejestru, nigdy częścią ścieżki pliku. Gdy dwie ścieżki dają to samo 12-znakowe id, druga zakładka jest pominięta z ostrzeżeniem w logu.

**Adresowanie.** Strona: `/?tab=<id>`; widok zakończonych planów to `/?tab=history` (`history` nie jest id zakładki katalogu). Trasy `GET /api/status`, `/api/doc`, `/api/tests` i `/events` przyjmują `?tab=<id>`; bez `tab` odpowiada zakładka domyślna (katalog uruchomienia serwera). Id spoza wzorca, nieznane, zakładka, której już nie ma, i zakładka domyślna wykluczona znacznikiem albo kluczem manifestu dają 404 `{error, code: "unknownTab"}`; `/api/doc` rozwiązuje ścieżkę wyłącznie pod korzeniem wskazanej zakładki. `/api/tabs`, `/api/history`, `/api/descriptions`, `/healthz` i assety nie biorą `tab`. Strona ukrywa w menu planów zakładki plany `done` i `archived` (są w Historii) tylko wtedy, gdy serwer ma więcej niż jedną zakładkę; serwer z jedną zakładką pokazuje menu jak dotąd. Wykluczona zakładka domyślna nie jest wymieniana w `GET /api/tabs` (zachowanie docelowe). Pole `worktrees[]` w statusie: [sekcja wyżej](#worktrees).

**`GET /api/tabs`** (każde gniazdo, które czyta; bez tokenów): `{tabs: [{id, name, path, kind, branch, head, current, history}], current[, hidden]}`; `tabs[]` ma te same pola co `worktrees[]` poza `lastActivity` i `waiting`, a `history` to liczba planów `done` i `archived` zakładki z ostatniego odświeżenia listy (0 do pierwszego odświeżenia; licznik na pasku „Historia”); `current` to id zakładki domyślnej. `hidden[]` jest tylko przy serwerze projektu: worktree, które ostatnie odświeżenie pominęło, od najnowszej aktywności, najwyżej 64, `{name, lastActivity, reason}` bez ścieżki i tokenu; `reason`: `trace` (brak śladu pracy skillsync), `inactive` (nieaktywny dłużej niż `dashboard.worktrees.inactive_days`, domyślnie 7 dni) albo `limit` (ponad limit 16). Wykluczone worktree nie są tam wymieniane. Strona bez `/api/tabs` (starszy serwer, odpowiedź 404) jest jednozakładkowa.

**Token zakładki.** Nagłówek `X-Approve-Token` ma postać `<id>.<token>`; token to HMAC-SHA256 (sekret procesu, tekst `<id>\n<znormalizowana ścieżka>`) w zapisie szesnastkowym. Adres zakładki: `/?tab=<id>#zgoda=<id>.<token>`. Serwer, który ma więcej niż jedną zakładkę, nie przyjmuje gołego tokenu (403); serwer z jedną zakładką przyjmuje oba kształty. Tokenów nie ma w `/api/status`, `/api/tabs`, assetach, `/healthz` ani w logu; `serve` i `url` drukują wyłącznie parę własnej zakładki (`--all-tabs`, tylko dla człowieka, drukuje pary wszystkich). Plik stanu (0600 w katalogu 0700) zawiera tokeny wszystkich zakładek: kto czyta pliki użytkownika, zna je wszystkie (poza modelem zagrożeń).

**Kolejność odmów zapisu bez zmian** (tabela „Kolejność odmów na gnieździe zgód”): token zostaje ostatnim krokiem przed odczytem ciała. Brak, zły, nieznany, wykluczony, bez nagrobka i goły na serwerze wielozakładkowym token daje tę samą odmowę 403 `approve_token`; czas porównania nie zależy od tego, czy zakładka istnieje (nieznane id porównywane jest z atrapą tej samej długości). Dopiero po przejściu tokenu:

| Sytuacja | Odpowiedź |
|---|---|
| token poprawny dla zakładki, która zniknęła (nagrobek) | 410 `tabGone`, przed odczytem ciała, bez zapisu |
| `tab` w ciele inny niż zakładka tokenu | 400 `tabMismatch` |
| zakładka zniknęła między tokenem a zapisem | 410 `tabGone` |
| powiązanie zwrotne (R2) albo korzeń zakładki zmienione, wykluczenie (R3) dodane od rejestracji, nieczytelne `dashboard.worktrees` albo serwer bez projektu i z wieloma zakładkami (re-walidacja tuż przed zapisem, pod blokadą zakładki) | 409 `tabChanged`, bez zapisu |
| `sha` inne niż oferta zakładki | 409, jak dotąd |

Nagrobek (id, znormalizowana ścieżka, czas) zostaje po wyrejestrowaniu zakładki na 10 minut, najwyżej 64 naraz (najstarszy wypada); ponowna rejestracja tej samej ścieżki go kasuje. Zakładka usunięta przez wykluczenie nagrobka nie dostaje, więc jej token jest jak token nieznanej zakładki (403), a 410 nie jest wyrocznią istnienia: zwracane jest wyłącznie po poprawnym HMAC liczonym z nagrobka. Wyrejestrowanie następuje po dwóch kolejnych odświeżeniach listy (co 2 s) bez zakładki; zakładka domyślna nigdy nie jest usuwana (po wykluczeniu odpowiada jak nieznana, a zapisy odmawia re-walidacja). Re-walidacja jest ochroną najlepszego wysiłku (zawęża okno podmiany ścieżki, nie eliminuje go). Dla zgód GO/CP/READY rewalidacja następuje przed czekaniem na blokady plików (do 5-10 s), więc to również ochrona najlepszego wysiłku: zakładka zmieniona w trakcie tego czekania może jeszcze dostać zapis.

**Które zapisy trafiają do zakładki.** CP, READY, GO plan, GO implement i zmiana statusu planu zapisują wyłącznie w katalogu zakładki z tokenu, pod blokadą jej zgód; dwa worktree o identycznym HEAD mają identyczne `sha`, więc izolację daje wtedy token i `Board` zakładki, nie `sha`. Zgoda feedbacku (`POST /api/feedback-approve`) wymaga tokenu zakładki, ale zapisuje w magazynie feedbacku **głównego katalogu** projektu (`.skillsync/feedback`, wspólnym dla projektu) z polem `tab` (id zakładki, tylko audyt); nie dotyka `runs` ani `docs` żadnej zakładki. Wpis `approvals.jsonl` z serwera projektu ma pole `worktree` (nazwa katalogu zakładki).

**`POST /api/plan-status`** (tylko gniazdo zgód; to akcja zapisu na własnej trasie, nie zgoda GO i nie dotyka `approvals.jsonl`): ciało `{tab, path, sha, action}`, wszystkie cztery pola obowiązkowe (tekst), `action` to `archive` albo `unarchive`, `path` plan z `docs/plans`, `sha` to blob sha pliku z listy. Łańcuch odmów i token zakładki właściciela pliku jak przy `/api/approve`; `tab` musi równać się zakładce tokenu. Serwer sam wylicza dozwoloną zmianę (`done` → `archived`, `archived` → `done`). Zapis wykonuje, pod blokadą pliku `<korzeń>/.skillsync/locks/plan-status.lock` (czekanie do 3 s, potem 409 `locked`; blokada chroni zapisy skillsync, w tym CLI `plan archive|approve`, nie edytor), po ponownej walidacji zakładki: (1) wiersz audytu `intent`, (2) zmiana linii `Status`, (3) wiersz audytu `done`. Audyt to `<korzeń>/.skillsync/audit/plan-status.jsonl` (JSON Lines, limit 1 MiB bez rotacji; zapis jak `record_approval`: bez dowiązań na drodze, blokada pliku, `O_NOFOLLOW`): `{at, by, via, tab, path, from, to, sha, phase}`, gdzie `phase` to `intent`, `done` (z `sha` po zmianie i `shaBefore`) albo `failed` (zmiana nie powiodła się po zapisanej intencji), `by` to użytkownik systemu, `via` to `{port, host, peer}` gniazda zgód. Porażka zapisu audytu PRZED zmianą (także wyczerpany limit) = 500 `auditRefused` i brak zmiany; porażka po zmianie = 200 z `auditFailed: true` i wpis w logu serwera. Odpowiedź 200: `{ok, tab, path, from, to, sha, auditFailed}`. Odmowy `{error, code}`: 403 `approve_token` i `approvalsOff` (`--no-approvals`), 400 `badRequest` i `tabMismatch`, 404 `planGone`, 409 `shaMismatch` (plik zmieniony od wyświetlenia), `statusNotAllowed`, `tabChanged`, `locked`, 410 `tabGone`, 500 `auditRefused` i `serverError`. Zmiana linii `Status` w pliku śledzonym zabrudza `git status` tego worktree i może dać konflikt przy scaleniu; wiersz audytu leży w katalogu zakładki i znika razem z worktree.

**`GET /api/history`** (każde gniazdo, które czyta; strona czyta ją przy otwarciu widoku Historia i po udanym `POST /api/plan-status`, a w tle, dla licznika na pasku, tylko przy widocznej karcie i nie częściej niż co 60 s): zakończone plany wszystkich zakładek, czytane wyłącznie z korzeni zarejestrowanych zakładek (nigdy ze ścieżki z żądania), zakładka domyślna pierwsza. Zakładki wykluczone (R3) i takie, które nie przechodzą R2/R3 w chwili odczytu, nie mają tu planów. Wiersz to para (`path`, blob `sha`): ten sam plik w kilku zakładkach jest pokazany raz, z `copies` i `copyTabs` (prezentacja, nie zapis między zakładkami), a przyciski archiwizacji działają na kopii zakładki właściciela wiersza. `abandoned` oznacza plan `approved` bez zmiany pliku od 7 dni i nie ma akcji. Pola i limity: wiersz tabeli tras. Podgląd planu: `GET /api/doc?tab=<zakładka właściciela>&path=…`. Odczyt używa stanu rejestru zakładek z ostatniego odświeżenia listy i krótkiego TTL (2 s) własnego wyniku; pełna rewalidacja zakładek (R2/R3) odbywa się tylko na zapisach, więc lista może do kilku sekund pokazywać plany zakładki, która właśnie przestała spełniać R2/R3.

### Model zagrożeń zakładek

Chroniony zasób: zapis zgody (CP, READY, GO) i zmiany statusu planu do konkretnego katalogu i `sha`. Rozważani przeciwnicy: (a) strona albo klient z tokenem jednej zakładki, który próbuje zapisać w innej; (b) proces w sieci bez tokenu (gniazdo zgód jest domyślnie lokalne); (c) stara karta z cudzym adresem; (d) fałszywy lub podmieniony wpis worktree. Obrony: token niosący zakładkę (jednolita odmowa 403, token ostatnim krokiem), `sha` jako druga warstwa, `Board` i blokada per zakładka, powiązanie zwrotne i `realpath` przy rejestracji i przy zapisie, `tab` tylko jako klucz słownika z walidacją wzorcem, `/api/doc` zamknięte w korzeniu zakładki, wydruk wyłącznie własnego tokenu, wykluczenie worktree (znacznik `.skillsync/no-dashboard` w worktree albo `dashboard.worktrees.exclude` w manifeście głównego katalogu).

Poza modelem, jawnie: człowiek albo agent z dostępem do plików użytkownika (czyta stan z tokenami wszystkich zakładek; granicą zaufania zgód pozostaje użytkownik: karta nie powinna sama wysyłać CP ani READY); ktoś, kto może dopisać worktree do repozytorium (ma już w nim zapis). Znacznik wykluczenia może usunąć każdy z zapisem w worktree: to wygoda, nie kontrola dostępu.

**Odczyt jest wspólny.** Gniazdo odczytu (także z `--read-bind 0.0.0.0`) udostępnia status, dokumenty, testy, `/api/tabs` i `/api/history` wszystkich zakładek projektu: ścieżki, gałęzie, HEAD i treści planów worktree widzi każdy, kto dziś widzi stronę. Nie ma ustawień per zakładka; ograniczeniem jest wykluczenie worktree. Ustawienia sieciowe (`--read-bind`, `--read-host`, `--approve-host` i pozostałe) są ustawieniami całego serwera.

### Model zagrożeń (dwa gniazda)

- **Widz w sieci bez logowania** widzi wszystko, co pokazuje gniazdo odczytu: plany, specyfikacje, wyniki, checkpointy i raporty
  przebiegów. Maskowanie sekretów (`redact`) jest heurystyczne, nie gwarancją. Gdy przebieg ma wyniki testów (pole `tests`),
  wyniki są na porcie odczytu bez maski, także w sieci.
- Widz **nie widzi i nie robi zgód**: gniazdo odczytu nie ma trasy zgód ani tokenu.
- **Ruch jest nieszyfrowany** (HTTP bez TLS). Token zgód przechodzi w jawnym tekście, gdy gniazdo zgód słucha poza loopbackiem.
- **Nieufne sieci** (hotel, konferencja, hotspot, sieć gościnna): `dashboard.local_only: true` albo `--read-bind 127.0.0.1`
  (oba gniazda zostają na tej maszynie; zgody przez tunel SSH).
- `prototype serve` ma własny nasłuch i model zaufania, opisane w [prototypes](prototypes.md); ten model dotyczy tylko `dashboard serve`.

### Kolejność odmów na gnieździe zgód

Każda odmowa kończy żądanie; odpowiedź nie mówi, które warstwy przeszły.

| Krok | Warunek | Odpowiedź |
|---|---|---|
| 1 | `Host` spoza: loopback, literał IP tej maszyny, nazwa z `--approve-host` | 403 `{"error": <tekst gniazda>}`: nazywa flagę właściwą dla gniazda i `Host` z żądania (niżej) |
| 2 | trasa inna niż `POST /api/approve`, `POST /api/feedback-approve` i `POST /api/plan-status` | 404 |
| 3 | brak `X-Status-Board: 1` | 403 „Brak nagłówka X-Status-Board.” |
| 4 | `Origin` niezgodny (tabela niżej) | 403 „Obcy Origin.” |
| 5 | nagłówek pośrednika bez `--allow-proxy-headers` | 403 „Zapis przez pośrednika … jest odrzucony …” |
| 6 | `Content-Type` inny niż `application/json` | 403 „Oczekiwano application/json.” |
| 7 | token `X-Approve-Token` (porównanie stałoczasowe, ostatni krok; na serwerze projektu postać `<id>.<token>`) | 403 `code: approve_token`, jedna treść dla braku, pustego, złego, nie-ASCII, nieznanej zakładki i gołego tokenu na serwerze wielozakładkowym |
| 8 | token poprawny dla zakładki, której już nie ma (nagrobek) | 410 `tabGone` |
| 9 | długość ciała 1–4096, obiekt JSON, `tab` w ciele zgodny z tokenem (inaczej 400 `tabMismatch`), potem `Board.approve` zakładki tokenu | 400, potem 409/403 z `Board.approve` (także 409 `tabChanged`) |

Rola `both` (`--port`) ma tę samą kolejność bez kroku 5 i z `Origin` porównywanym tylko po nazwie. Token jest sprawdzany
przed odczytem ciała, a licznik nieudanych prób tokenu liczy odmowy kroku 7 na gnieździe zgód.

**Origin na gnieździe zgód** (ścisły: schemat, nazwa i port, bez rozróżniania wielkości liter):

| Przypadek | Wynik |
|---|---|
| brak nagłówka `Origin` (klient spoza przeglądarki) | dalej (decyduje token) |
| dokładnie `http://<wartość Host>`, np. `http://192.168.1.5:5141` albo `http://[::1]:5141` | dalej |
| `null`, pusty, `file://`, `https://…` | 403 |
| bez portu, gdy `Host` ma port (`http://127.0.0.1` przy `127.0.0.1:5141`) | 403 |
| inny port, np. `http://127.0.0.1:5140` (strona z gniazda odczytu) | 403 |
| inna nazwa, ścieżka, ukośnik na końcu, dane logowania, dwa nagłówki `Origin` | 403 |

**Nagłówki pośrednika** (lista zamknięta; liczy się sama obecność, także pusta wartość i duplikat): `Forwarded`,
`Forwarded-For`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-Port`, `X-Forwarded-Server`,
`X-Forwarded-Scheme`, `X-Forwarded-Prefix`, `X-Original-Forwarded-For`, `X-Original-Host`, `X-Real-IP`, `X-Client-IP`,
`X-Cluster-Client-IP`, `True-Client-IP`, `CF-Connecting-IP`, `Fastly-Client-IP`, `X-Envoy-External-Address`, `Via`. To
heurystyka: pośrednik może nie dodać żadnego z nich, więc barierą są token i ścisłe `Origin`, nie ta lista.
`--allow-proxy-headers` wyłącza tylko krok 5; `Host`, `Origin`, typ i token zostają. Adres z `via.peer` to zawsze adres gniazda, nigdy wartość nagłówka (za odwrotnym proxy: adres proxy).

**Reguła `Host` w trybie dwóch gniazd.** Oba gniazda akceptują `localhost`, `127.0.0.1`, `::1` (bez portu, bez rozróżniania
wielkości liter), nazwy z `--approve-host` oraz literały IP interfejsów tej maszyny. Literał IP jest sprawdzany przy każdym
żądaniu próbą `bind` gniazda UDP na tym adresie (udaje się dla adresu własnego interfejsu, ale też dla multicastu i
broadcastu, więc te są odrzucane wprost: multicast, adresy zarezerwowane, `0.0.0.0`, `::`, `255.255.255.255` i każdy adres
IPv4 kończący się na `.255`, bo maska nie jest znana, chyba że to adres wyliczony z nazwy maszyny lub trasy, np. `10.0.1.255` w sieci
/23; jeśli prawdziwy adres maszyny na `.255` mimo to dostaje 403, wpisz go w `--approve-host <IP>`). Nazwa maszyny jest rozwiązywana w tle z limitem 2 s (jedno rozwiązanie naraz, wynik zapamiętany na 60 s, brak odpowiedzi w limicie na 15 s, przy czym wcześniejsza dobra odpowiedź obsługuje wolny resolver najwyżej 5 minut od ostatniego UDANEGO rozwiązania, potem zbiór jest pusty): gdy resolver nie zdąży, adres `.255` niewyliczony inną drogą (trasa) jest ODRZUCANY (fail closed), a start i zamykanie serwera nie czekają na resolver; gdzie `bind` nic nie dowodzi (Linux z `ip_nonlocal_bind`), liczą się
tylko adresy wyliczone z nazwy maszyny i trasy. Nagłówek `Host` nie jest barierą dostępu przy nasłuchu w sieci: klient spoza
przeglądarki ustawia go dowolnie, a na gnieździe zgód chroni wyłącznie token. Dzięki temu telefon w sieci wchodzi na `http://<IP maszyny>:5141/` bez wpisywania go w `--approve-host`.
Gniazdo odczytu dodatkowo przyjmuje nazwy z `--read-host` oraz własną nazwę maszyny: `socket.gethostname()` (małymi literami),
jej krótką formę i `<krótka>.local`. To dopasowanie tekstowe, bez DNS, bez rozróżniania wielkości liter, i tylko dla odczytu (gniazdo zgód go nie ma). Pusta nazwa
i nazwy podobne do `localhost` są odrzucane. Ograniczenie: pełna nazwa z innym sufiksem (np. `laptop.lan`, gdy maszyna zna się
jako `laptop`) nie jest jej częścią i dostaje 403; wtedy dodaj ją w `--read-host`. Nazwa maszyny jest w komunikacie startowym.
Uzasadnienie wobec DNS rebinding: nazwa, którą maszyna sama sobie nadała, nie jest domeną atakującego, a odczyt i tak nie
ma trasy zgód ani tokenu. Nazwa spoza listy dostaje 403, więc DNS rebinding nie działa.
Granica zaufania: nazwa maszyny jest rozwiązywana lokalnie (`/etc/hosts`, DNS), więc kto kontroluje to rozwiązywanie, decyduje, które adresy `.255` uchodzą za lokalne. To nie osłabia ochrony, bo na gnieździe zgód barierą jest token, a nie `Host`; wynik rozwiązania nazwy służy do oceny nagłówka `Host` i do podpowiedzi adresów na konsoli, i nie wolno go użyć jako dowodu do niczego innego.

**Plik stanu i `dashboard url`.** Każde gniazdo ma własny plik stanu (po numerze portu), z polami `role` (`read` albo
`approve`), `peer` (port drugiego gniazda) i `bind` (rzeczywisty adres nasłuchu gniazda, z którego `dashboard url --lan` liczy
adresy dla innych urządzeń); brak `role` oznacza tryb `--port`. Rekord odczytu ma `approvals: false` i nie ma
tokenu. Plik jest `schema: 2` i dodaje: `kind` (`project` albo `standalone`), `serverId` (to samo, co zwraca `/healthz`), `commonDir` (wspólny katalog gita, gdy katalog jest w projekcie git), `tabs[]` (`{id, path[, current][, token]}`; `current: true` ma zakładka katalogu uruchomienia; `token` w postaci `<id>.<token>` tylko w pliku gniazda, które ma token) i `terminal` (`TMUX`, `TERM_SESSION_ID` procesu serwera, do komunikatu, gdzie serwer działa). Nazwa pliku (skrót katalogu `root` i port) pozostaje; w projekcie git `root` to główny katalog (gdy istnieje), niezależnie od katalogu, który serwer uruchomił, więc plik jest jeden na projekt i port. `tabs[]` odświeża serwer atomowo przy każdej zmianie listy zakładek. `dashboard url` i dopisanie czytają pliki schema 1 i 2; serwer projektu znajdują wyszukiwaniem po `commonDir` we wszystkich plikach (tylko schema 2, `kind: project`), nie po nazwie pliku, a serwer schema 1 nigdy nie jest celem dopisania. Zachowanie `dashboard url` (bez `--port`: tylko serwer `project`; `--port N`: dowolny; `--all-tabs`) opisuje [cli.md](cli.md). `dashboard url <projekt>` (bez `--port`) wybiera rekord zgód; `ambiguous_server` dotyczy tylko kilku serwerów zgód (różnych `serverId`),
więc dwa gniazda jednego procesu nie kolidują. `dashboard url <projekt> --read` wypisuje adres odczytu bez fragmentu, do
przekazania osobie z zewnątrz; bez gniazda odczytu = `server_not_running`. `dashboard url <projekt> --lan` wypisuje adresy
odczytu dla innych urządzeń, jeden na linię (`http://<IP>:<port>/`); w `--json` pole `lanUrls`. Gdy gniazdo odczytu słucha
tylko na loopbacku, BLOCKED `read_not_exposed` (exit 2). `--json` zwraca `{schema, status, url, port, pid}`;
dla gniazda zgód dodaje `role: "approve"` i `readUrl` (adres odczytu, gdy znany), a dla `--read` zwraca `role`.

**Ślad zgody (`via`).** Zgoda z gniazda zgód trybu dwóch gniazd dopisuje do wpisu `approvals.jsonl` pole
`via: {port, host, peer}`: port gniazda, nazwa z `Host` (do 80 znaków) i adres klienta z połączenia (do 64 znaków). Wpisy bez
`via` (także z trybu `--port`) czyta się bez zmian; `sha == HEAD` i pozostałe warunki zgody są te same. Zgoda z adresu
spoza loopbacku daje linię w konsoli (`zgoda z sieci: <adres> (port N, Host H)`); nieudane próby tokenu są zliczane i
zgłaszane najwyżej raz na 60 s jedną linią bez treści tokenu.

Endpointy:

| Endpoint | Odpowiedź |
|---|---|
| `GET /`, `/index.html` | strona (`assets/index.html` skilla `dashboard`) |
| `GET /assets/<plik>` | tylko nazwy `[\w-]+.(html\|css\|js\|svg)` z `skills/dashboard/assets/` aktywnych warstw (`LayerSet.skill_files`: późniejsza warstwa wygrywa per plik, dowiązania pomijane); inne = 404 |
| `GET /api/status[?plan=<rel>][&tab=<id>]` | kontrakt `schema: 1` (to samo co `show`, z tym samym maskowaniem sekretów, z żywym `version`, `session.url`, `prototype.up`) plus `approvals.here` (bool: nagłówek `Host` żądania jest na liście hostów zgód, czyli loopback albo `--approve-host`; `false` dla hosta z `--read-host`) i `approvals.viewer` (bool: host wyłącznie do odczytu z `--read-host`; wtedy `here` = `false`) oraz `approvals.tokenRequired` (bool: zapis zgody wymaga nagłówka `X-Approve-Token`; `false` przy `--no-approvals`; tylko HTTP, `show` go nie ma; wartość tokenu nigdy nie jest w odpowiedzi); w trybie dwóch gniazd także `approvals.role` (`read`/`approve`), a `here`, `viewer` i `tokenRequired` są z gniazda, nie z `Host` |
| `GET /api/doc?path=<rel>[&tab=<id>]` | `{path, title, status, mtime, size, truncated, html}`: podgląd `*.md` z `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` i `.skillsync/runs/<run>/`; bez `..`, ścieżek absolutnych, ukrytych katalogów i dowiązań poza listę; limit 2 MB (`truncated`); inne = 404; dokument i cała odpowiedź (także `path` i `title`) są maskowane (`redact`), tak jak `/api/status`; zasady odczytu tekstu niżej |
| `GET /events[?tab=<id>]` | SSE zakładki (domyślnej bez `tab`): `hello {version, heartbeat}` (z `retry: 3000`), `change {version}` po zmianie obserwowanych plików, `ping {version, t}` co 15 s; najwyżej 64 połączenia (dalej 503; w trybie dwóch gniazd osobne udziały: 48 na gnieździe odczytu i 16 na gnieździe zgód, więc urządzenie zapełniające odczyt nie odbierze odświeżania stronie zatwierdzającego); `HEAD /events` = same nagłówki (200, `text/event-stream`), bez strumienia i bez zajmowania miejsca |
| `GET /api/descriptions` | `descriptions.json` skilla (`{klucz: {title, text}}`) |
| `GET /healthz` | `{ok: true, version, serverId[, tokenId]}`; `serverId` to losowy identyfikator procesu serwera (nie sekret, niezależny od tokenów), po którym `dashboard url` i dopisanie odróżniają ten serwer od obcego procesu na porcie; `tokenId` (8 znaków hex skrótu tokenu zakładki domyślnej, jak dotąd, żeby klient 1.17 rozpoznał serwer; to odejście od pierwotnego zapisu specu, który dawał w `/healthz` tylko `serverId`) jest tylko na gnieździe zgód serwera ze zgodami; tokenu nie ma w odpowiedzi |
| `POST /api/approve` | tylko na gnieździe zgód (na gnieździe odczytu każda metoda zapisu = 404); `{kind: spec\|plan\|cp\|ready, path, sha[, plan][, planState]}`; tylko akcja, którą `/api/status` właśnie oferuje w stanie `waiting`, z tym samym `sha`; odpowiedź 200: `{ok, word, file, next}`, dla GO także `trail` (ścieżka `approvals.jsonl` przebiegu albo null bez przebiegu) |
| `POST /api/feedback-approve` | tylko na gnieździe zgód (na gnieździe odczytu 404; `--no-approvals` = 403 jak dla `/api/approve`); `{draft: "F<n>", sha: "<64 znaki hex>"}`, inne ciało = 400 bez wpisu; te same zabezpieczenia co `/api/approve` (`Host`, `Origin`, `X-Status-Board`, JSON, token); zapis przez `feedback.approve_from_preview(..., source='dashboard')` w głównym checkoutcie, bez wywołania trackera; 200 `{ok, draft, sha12, approval, already}` (ponowny klik = 200 z `already: true`, jeden wiersz także przy równoległych żądaniach); odmowa `{error, code}`: `card` 403, `no_preview`, `content_changed`, `stale` (też podgląd bez `view`), `sent`, `scan` i inne błędy = 409, błąd zapisu = 500; tekst z katalogu `srv.feedback.<code>`, bez tytułu, treści i sha; log: tylko szkic, sha12 i `via` |
| `GET /api/tabs` | każde gniazdo, które czyta; 200 `{tabs: [{id, name, path, kind, branch, head, current, history}], current[, hidden]}`, bez tokenów; `history` = liczba planów `done` i `archived` w katalogu zakładki, bez limitów i bez planów porzuconych, więc to wyłącznie wskazówka licznika, nie liczba wierszy `GET /api/history` (licznik na pasku strony pochodzi z `count` odpowiedzi `/api/history` czytanej w tle co najwyżej raz na 60 s przy widocznej karcie; do tego czasu strona pokazuje największą wskazówkę z `tabs[].history`) (ta ma limity, planów porzuconych i scala kopie; wykluczona zakładka znika z obu po odświeżeniu listy; wykluczona zakładka domyślna nie jest wymieniana w `tabs`), liczona przy odświeżeniu listy zakładek z planów, które odświeżenie i tak wylicza (bez dodatkowego otwarcia pliku i bez pracy w odczycie); 0 do pierwszego odświeżenia |
| `GET /api/history` | każde gniazdo, które czyta (`Host` jak inne GET), bez parametru `tab`; czytana przy otwarciu Historii, po zmianie statusu i w tle co ≥60 s przy widocznej karcie; z korzeni zarejestrowanych zakładek (bez wykluczonych, R3); 200 `{schema: 1, here, truncated, count, tabs: [{id, name, branch, main}], items: [{tab, tabName, path, title, status: done\|archived\|approved, sha, copies, copyTabs, abandoned, action: archive\|unarchive\|null, mtime}]}`; wiersz = para (`path`, blob `sha`), właściciel = pierwsza zakładka z pary, `copies`/`copyTabs` (w kolejności `tabs`) liczą kopie; `abandoned` = plan `approved` bez zmiany pliku od 7 dni (bez akcji; strona pokazuje takie plany jako „porzucony”); do 200 zakończonych planów na zakładkę (z co najwyżej 3000 przejrzanych plików) i 1000 wierszy; ucięcie którymkolwiek limitem = `truncated`, `count` = liczba wierszy przed limitem wierszy |
| `POST /api/plan-status` | tylko na gnieździe zgód; `{tab, path, sha, action: archive\|unarchive}` z tokenem zakładki właściciela pliku (`tab` obowiązkowe i równe zakładce tokenu); łańcuch odmów jak `/api/approve`; pod blokadą pliku `<root>/.skillsync/locks/plan-status.lock`: audyt `intent` w `<root>/.skillsync/audit/plan-status.jsonl`, zmiana wiersza `Status` (`done`↔`archived`), audyt `done`; nie dotyka `approvals.jsonl`; 200 `{ok, tab, path, from, to, sha, auditFailed}`; odmowa `{error, code}`: 403 `approve_token` i `approvalsOff`, 400 `badRequest` i `tabMismatch`, 404 `planGone`, 409 `shaMismatch`, `statusNotAllowed`, `tabChanged`, `locked`, 410 `tabGone`, 500 `auditRefused` (nic nie zmieniono) i `serverError` |

W trybie `--port` kontrola `Host` na wszystkich endpointach (`GET`, `HEAD`, SSE `/events`, `POST`; w trybie dwóch gniazd zob. „Reguła `Host` w trybie dwóch gniazd”): nagłówek `Host` musi wskazywać loopback
(`localhost`, `127.0.0.1`, `[::1]`, z dowolnym portem; bez rozróżniania wielkości liter), nazwę z `--approve-host` albo
nazwę z `--read-host` (obie listy: port pomijany, bez rozróżniania wielkości liter, flagi powtarzalne). Inny albo
brakujący `Host` (DNS rebinding, tunel lub nazwa LAN spoza list) = 403 `{"error": "<tekst>"}` bez treści strony,
dokumentu ani statusu. Tekst wskazuje, która flaga otwiera TO gniazdo: odczyt (`--read-port`) `--read-host` (oraz adres IP i własną nazwę maszyny),
zgody (`--approve-port`) `--approve-host`, tryb jednego portu (`--port`) obie (`--approve-host`: odczyt i zgody, `--read-host`: tylko odczyt); kończy go
`(nagłówek Host: <nazwa>)`. Nazwa pochodzi z żądania i przed wpisaniem do odpowiedzi jest oczyszczona (małe litery bez portu, tylko litery, cyfry, `.`, `:`, `-`, `_`,
reszta `?`, najwyżej 64 znaki, brak = `(brak)`), więc nie niesie znaków sterujących ani znaczników; odpowiedź niesie tylko nazwę flagi i oczyszczony `Host` z żądania (nic, czego żądający sam nie wysłał).
Odmowa zapisu z hosta, który może tylko czytać (`POST /api/approve` z `--read-host`), ma stały tekst „Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host).”; przy `--verbose` odmowa trafia do logu stderr (metoda, ścieżka, host).

| Klasa `Host` (tryb `--port`) | Odczyt (`GET`, `HEAD`, `/events`, `/api/*`, `/healthz`, assety) | Zapis (`POST /api/approve`) | `approvals.here` / `approvals.viewer` |
|---|---|---|---|
| loopback | tak | tak (chyba że `--no-approvals`) | `true` / `false` |
| `--approve-host` | tak | tak (chyba że `--no-approvals`) | `true` / `false` |
| `--read-host` | tak | w przeglądarce widza nigdy: 403 ze stałym tekstem („Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host).”, bez nazwy z żądania), także z poprawnymi `Origin`, `X-Status-Board` i `Content-Type` | `false` / `true` |
| inny albo brak | 403 | 403 | (brak odpowiedzi z danymi) |

`--read-host` służy pokazowi przez tunel: połączenie przychodzi z loopbacku, ale przeglądarka widza wysyła `Host:
<nazwa>:5140`; widzowie widzą wszystko (także `assumptions`, `blockingQuestions`, `waiting`, `silence`), a zgody daje
tylko prowadzący pod loopbackiem albo `--approve-host`. Nazwa obecna na obu listach (albo loopback podany w
`--read-host`) zostaje hostem zgód, bo serwer nie odróżni prowadzącego od widza pod tym samym `Host`; serwer ostrzega o
tym przy starcie. Przy starcie serwer wypisuje osobno hosty odczytu i zgód (`--approve-host`) i hosty tylko do odczytu
(`--read-host`). Widok tylko do odczytu dla wszystkich = `--no-approvals`. W trybie `--port` tunel `ssh -L` na `127.0.0.1`, w którym
przeglądarka wysyła `Host: localhost:<port>`, działa bez flagi (i daje zgody), także `ssh -R`, gdy przeglądarka po drugiej stronie otwiera `localhost`; widz otwierający stronę pod nazwą tunelu potrzebuje `--read-host <nazwa tunelu>` (tylko odczyt) albo `--approve-host <nazwa tunelu>` (odczyt i zgody). W trybie dwóch gniazd port 5140 to odczyt bez
trasy zgód: tunel `ssh -L M:127.0.0.1:M` na zgody wymaga jawnego `--approve-port M` (`--approve-host` tylko dla nazwy z tunelu odwrotnego albo bramki).

Granica `--read-host`: gwarancja dotyczy zapisu z przeglądarki widza. Serwer odróżnia widza tylko po nagłówku `Host`,
więc klient, który go podrobi (np. `curl -H 'Host: localhost'` przez tunel), zapisze zgodę; tunel przepisujący `Host` na
`localhost` daje zapis każdemu widzowi. Serwer ostrzega o tym przy starcie (gdy są hosty `--read-host`, a zgody włączone).
**Odczyt tekstu i maskowanie.** Tekst planów, speców, checkpointów, nagłówków i dokumentów podglądu czyta `dashboard.read_masked`: bajty są dekodowane (`decode_text`: UTF-16 z BOM jako UTF-16, reszta jako UTF-8 z zastępnikiem dla błędnych bajtów; znaki NUL są zdejmowane, bo renderer też je zdejmuje), cały tekst jest maskowany (`common.redact`), dopiero potem parsowany, renderowany i przycinany do limitu bajtów (czytnik bierze zapas 4 KiB ponad limit, żeby sekret na granicy był maskowany w całości). Odpowiedź `/api/doc` jest maskowana jeszcze raz w całości, tak jak `/api/status`. Maskowanie to heurystyka, nie gwarancja: obejmuje wzorce kluczowe (`password`, `token`, `secret`, `api_key`, `secret_key`, `access_key`, `private_key`, `authorization`, `bearer`, dane w adresach URL, także pary JSON w cudzysłowach) i wartości zmiennych środowiska o nazwach z tych słów, co najmniej 8 znaków; sekretów nie trzyma się w dokumentach projektu.

Przy nieufnych widzach użyj `--no-approvals`. Model dwóch instancji dla nieufnych widzów: prowadzący na loopbacku ze zgodami, widzowie na osobnej instancji `skillsync dashboard serve <projekt> --port <inny port> --no-approvals --read-host <host>` (bez zapisu, ten sam projekt; wspólny jest tylko `approvals.jsonl`). Widz z odczytem widzi też podgląd dokumentów `/api/doc`; jest maskowany jak `/api/status`, ale maskowanie jest
heurystyką (patrz wyżej), więc nieufni widzowie dostają `--no-approvals`. Wartości `--read-host` i `--approve-host` to same nazwy hostów: z portem, nawiasami, `/` albo
białym znakiem start kończy się `BLOCKED` (exit 2).

Pola `approvals.here` i `approvals.viewer` dodaje wyłącznie serwer; `dashboard show` z wiersza poleceń ich nie ma (brak
klucza = `false`). Renderer pokazuje przy hoście z `viewer: true` informację, że zgody daje prowadzący na komputerze z
sesją, zamiast instrukcji wpisania słowa.

Renderer strony (`assets/app.js`) rysuje pola dodane do kontraktu w kafelkach aktów; każda wartość z danych przechodzi
przez escapowanie HTML, a widoczność nie zależy od `approvals.here` ani `approvals.viewer`:

- Akt I nie ma sekcji z pytaniami: ani listy blokujących, ani założeń (także z zerem, dla prowadzącego i widza); pytania blokujące i założenia są w zakładce „Pytania”
  (grupa „Pytania do realizacji”). Pytania do eksperta pokazuje toast w banerze z pola `expertQuestions` (sekcja `expertQuestions`) i grupa „Pytania do eksperta domenowego”.
- Akt II, kafelek „Readiness”: `readiness.status` PASS (zielony), BLOCKED (ostrzeżenie), null (neutralny „brak danych”);
  obok `readiness.date`, pierwsze 8 znaków `readiness.planSha`, znacznik „brief dla innej wersji planu”, gdy `planSha` różni
  się od `plan.fileSha`, i przycisk otwierający `readiness.source` w podglądzie dokumentów (`/api/doc`); bez `source`
  przycisku nie ma.
- Akt III, rozwijana lista „Podkroki i czas (N)” z `substeps[]`: `seconds` jako czas, `missingData: true` jako „brak danych”,
  podkrok niezrobiony pokazuje swój stan. Pusta tablica = brak listy.
- Akt III, rozwijana sekcja „Czas pracy i czekania” z `timing`: wiersz na fazę (`stages[]`: praca, czekanie, „niepełne” przy `complete: false`), wiersz sumy (`totals`), dymek z oknami czekania (`windows[]`). `available: false` albo brak okien i faz = brak sekcji.

Dymki tych elementów mają klucze `stage_readiness` i `substeps_time` w `descriptions.json`.

Zgody in-process (bez podprocesu `skillsync`, bez własnego zapisu serwera): „GO plan”/„GO implement” =
`status.set_doc_status(root, path, "approve", specs|plans, expect_sha=<sha z żądania>)` (plik zmieniony od wyświetlenia =
409), a potem, gdy katalog przebiegu akcji (`actions[].run`) istnieje, `status.record_approval(..., source="dashboard",
file=path, sha_after=<sha po zmianie>)`: wpis `{schema, at, word, sha, by, source: "dashboard", file, shaAfter, run}`.
Bez katalogu przebiegu zgoda GO zmienia tylko linię `Status:` (`trail: null`; sesja czyta zgodę ze statusu pliku, jak
`approvals wait --file`). „CP”/„READY” = `status.record_approval(..., source="dashboard")` z `run`, `plan`, `planState` (z żądania, przekazany do zapisu jako `expect_plan_state`; przy innym niż odczytany pod blokadą = 409 z `code: plan_state_changed`), `target` (tylko
CP), `branch`, `ahead`, `mode`; odmowa rdzenia po przejściu kontroli serwera (blokada pliku, limit 256 KiB, zła nazwa
gałęzi, wyścig z drugim zapisem) = 500 z powodem; wyjątek: odmowa z `code: plan_state_changed` (stan planu inny niż oczekiwany, także plan nieznany) = 409, bo to rdzeń zapisu porównuje stan pod blokadą pliku, a serwer nie ma własnego porównania. Id przebiegu: wzorzec `status.RUN_ID` (`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`):
pierwszy znak alfanumeryczny (więc ani `.`, ani `..` jako całe id) i bez `/`; ciągu `..` wewnątrz id (`a..b`) wzorzec nie
wyklucza, a ponieważ `/` jest zabronione, taki człon nie wychodzi poza `.skillsync/runs/`. Dowiązanie na drodze
`.skillsync`, `.skillsync/runs` albo katalogu przebiegu wyłącza odczyt przebiegu w `show` i `/api/status` (`run.exists:
false`, akcje bez `run`) i odmowę zapisu w `record`; serwer przyjmuje z żądania tylko id, które `/api/status` właśnie oferuje.

Zabezpieczenia `POST /api/feedback-approve` są te same i w tej samej kolejności (odmowa = 403 albo 409 z `{error, code}`, bez zapisu); zamiast `set_doc_status`/`record_approval` zapis robi `feedback.approve_from_preview`, a kody odmów są w wierszu tabeli tras.

Zabezpieczenia `POST /api/approve` (odmowa = 403, bez wywołania `set_doc_status`/`record_approval`): `Host` z loopbacku
albo `--approve-host` (sprawdzany przed długością treści; `--read-host` nie wystarcza); nagłówek `X-Status-Board: 1`; `Origin`, jeśli jest, z tej samej listy; `Content-Type:
application/json`; na końcu nagłówek `X-Approve-Token` z tokenem serwera (porównanie stałoczasowe; brak albo zły token = ta sama odmowa 403 „Brak albo zły token zgody…”, sprawdzany jako ostatni, więc każda wcześniejsza odmowa jest taka sama z tokenem i bez). Kontrola `Origin` porównuje tylko nazwę hosta (`localhost`, `127.0.0.1`, `[::1]` albo nazwę z
`--approve-host`; nazwa z `--read-host` to obcy `Origin`); schematu i portu nie porównuje, więc inna strona na loopbacku (inny port) przechodzi ten krok. Przed
obcą stroną w przeglądarce chroni przede wszystkim własny nagłówek `X-Status-Board` i `Content-Type: application/json`:
oba wymuszają preflight CORS, na który serwer nie odpowiada; brak `Origin` (klient spoza przeglądarki) jest dopuszczony.

**Token zgód.** Serwer `standalone` i serwer z jedną zakładką tworzą go na proces (`secrets.token_urlsafe(24)`); serwer projektu z wieloma zakładkami ma token per zakładka w postaci `<id>.<token>` (zob. „Serwer projektu i zakładki”). Tokenu nie ma w `/api/status`, assetach, logu ani raporcie błędu. Użytkownik dostaje go w adresie z fragmentem: serwer projektu `http://127.0.0.1:<port>/?tab=<id>#zgoda=<id>.<token>`, serwer `standalone` i plik stanu schema 1 `http://127.0.0.1:<port>/#zgoda=<token>` (fragment nie trafia do serwera ani do proxy, więc klient z podrobionym `Host` go nie zna): z linii konsoli `dashboard serve`
albo z `skillsync dashboard url <projekt> [--port N] [--host NAZWA] [--json]`, który czyta plik stanu `<katalog danych maszyny>/dashboard/<skrót katalogu projektu>-<port>.json`
(poza drzewem projektu; 0600 w katalogu 0700, na Windows ACL właściciela przez wspólny pomocnik, gdy jest; zapis atomowy, ACL na pliku tymczasowym przed `os.replace`; usuwany przy zamknięciu serwera; serwer z `--no-approvals` zapisuje ten sam plik BEZ tokenu (`"approvals": false`), żeby `dashboard url` mógł powiedzieć, że zgody są wyłączone; katalog stanu musi być prawdziwym katalogiem użytkownika (dowiązanie albo cudzy katalog na POSIX = pliku nie zapisano, jedno ostrzeżenie), a start serwera sprząta osierocone `.tmp-<skrót>-<port>-*.tmp` swojego projektu i portu; plik tymczasowy nie ma sufiksu `.json`, więc czytelnik katalogu stanu, który wybiera `*.json`, nie trafia na pusty plik w trakcie zapisu; pole `root` pliku i skrót, który nazywa plik, liczone są z jednej postaci ścieżki (`pathfmt.posix`: `/` także na Windows, jak w innych polach ścieżek raportów), więc serwer i `dashboard url` trafiają w ten sam plik; serwer przy zamknięciu usuwa plik tylko wtedy, gdy zapisany w nim `pid` jest jego własny (stary serwer nie skasuje pliku nowego na tym samym porcie). Znane ograniczenie: na Windows (gdzie `servers` nie sprawdza `pid`, decyduje `/healthz`) rekord serwera `--no-approvals` z obcym procesem odpowiadającym na `/healthz` tego samego portu może dać fałszywe `approvals_disabled` zamiast `server_not_running` (tylko komunikat; token nigdy nie jest ujawniany);
pozostałość po zabitym procesie jest ignorowana albo usuwana: serwer musi odpowiadać `/healthz` z tym samym `serverId` (16 znaków hex, nie token; w pliku jest też `tokenId`, 8 znaków hex skrótu tokenu); plik zawiera też adres bindu, pod którym sonduje się serwer, i nazwy z `--approve-host`; adres z tokenem używa loopbacku albo nazwy z `--approve-host`, bo strona odpowiada tylko pod nimi). `dashboard url`: `PASS {url, port, pid}` albo `BLOCKED` (exit 2) z kodem
`server_not_running` (brak żywego serwera z plikiem stanu), `approvals_disabled` (serwer działa z `--no-approvals`: nie ma tokenu), `ambiguous_server` (kilka serwerów projektu, podaj `--port`), `unknown_tab` (katalog nie ma zakładki na tym serwerze albo zakładka nie ma jeszcze tokenu), `host_not_allowed` (`--host` spoza loopbacku i nazw z `--approve-host` tego serwera), `invalid_project`. Odmowa tokenu ma w treści `code: "approve_token"` (strona po nim, nie po tekście, zapomina nieaktualny token). Strona (`app.js`) czyta fragment `#zgoda=`,
zapisuje token w `localStorage` swojego originu, czyści pasek adresu (`history.replaceState`) i wysyła `X-Approve-Token`; bez tokenu (albo po jego odrzuceniu po restarcie serwera)
przyciski zgód są nieaktywne z jednym powodem, a odczyt działa w pełni. Skutek przechowywania w `localStorage`: token przeżywa zamknięcie przeglądarki (do restartu serwera) i jest czytelny dla skryptów tej strony; serwer projektu z wieloma zakładkami ma tam pary `<id>.<token>` wielu zakładek (strona trzyma ich do 16), więc ewentualny XSS na stronie statusu mógłby użyć wszystkich zapisanych par naraz, nie jednego tokenu; CSP `script-src 'self'` i sanitizacja treści dokumentów to ograniczają (decyzja o `sessionStorage` do potwierdzenia przez właściciela). Token jest drukowany na stderr `dashboard serve` przy starcie: stderr przekierowany do pliku, dziennika usługi (systemd) albo logów CI zachowa adres z tokenem, więc w takich uruchomieniach nie polegaj na linii startowej i podawaj adres przez `skillsync dashboard url <projekt>`. Przez `--approve-host` token wędruje w nagłówku zwykłego HTTP (bez szyfrowania): używaj w zaufanej sieci albo przez tunel. `--approve-host`: zdalna przeglądarka potrzebuje `Host` na liście i adresu z tokenem (`dashboard url --host NAZWA`).
Dalej `Content-Length` 1–4096 (inaczej 400), treść JSON-obiekt (inaczej 400), `--no-approvals` = 403, akcja
nieoferowana, nieaktywna albo z innym `sha` = 409 (także drugi zapis CP/READY dla tego samego HEAD i tego samego `planState`: `BLOCKED`); CP/READY ze zmienionym od wyświetlenia statusem planu (`planState` żądania inny niż bieżący) = 409; `planState` spoza listy statusów planu (`draft`, `approved`, `in_progress`, `done`, `archived`) albo nie-tekst = 400. Każda odpowiedź ma
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache,
no-store`. Połączenie bez kompletnego żądania jest zamykane po 30 s (timeout gniazda obsługi). Strona HTML ma także CSP
`default-src 'self'; script-src 'self'; …; frame-ancestors 'none'`. W trybie `--port` z `--host 0.0.0.0` inne urządzenia dostają 403,
dopóki ich adres z paska przeglądarki nie trafi do `--approve-host` albo `--read-host` (serwer wypisuje to przy starcie). W trybie dwóch gniazd literały IP tej maszyny są przyjmowane bez tej listy.
