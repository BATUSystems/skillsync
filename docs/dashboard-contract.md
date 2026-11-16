# Kontrakt JSON dashboardu (`schema: 1`)

Dashboard to strona statusu projektu: ten sam stan, który widzi człowiek w przeglądarce, jest dostępny jako JSON.
Kontrakt jest wspólny dla renderera strony, innych konsumentów (np. IDE) i testów. Przykłady wejścia i oczekiwanego
wyniku: `tests/fixtures/dashboard/<przypadek>/input/` i `expected.json`.

## Komendy

| Komenda | Zachowanie |
|---|---|
| `skillsync dashboard show <projekt> [--json] [--plan <rel>]` | drukuje kontrakt `schema: 1` (JSON; bez `--json` ten sam JSON, formatowany); tylko odczyt, bez zapisu do projektu; `--plan` wybiera plan (ścieżka względna, np. `docs/plans/<plik>.md`), bez niego plan wybierany jest automatycznie (`planAuto: true`) |
| `skillsync dashboard serve <projekt> [--port N] [--read-port N --approve-port M] [--host H] [--approve-host H] [--read-host H]` | serwer HTTP nad tym samym JSON (`/api/status`, strumień zmian `/events`, przyciski zgód); jedno gniazdo (`--port`) albo dwa: odczyt i zgody (sekcja „Serwer”); port wybiera się per projekt, by strony nie kolidowały |
| `skillsync dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | ogólne metryki przebiegu: czas podkroków `Fn.k` z checkpointów, liczby rund i porażek; podkrok bez danych jest oznaczony jawnie (sekcja „Metryki przebiegu”) |
| `skillsync approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> [--sha <git sha>] [--target X] [--branch Y] [--file docs/…] --source <cli\|ide> [--json]` | zapisuje zgodę użytkownika (patrz niżej; `dashboard` ustawia tylko serwer) |

Kody wyjścia (jak pozostałe komendy workflow): `0` = PASS, `2` = BLOCKED (błąd danych wejściowych, odmowa zapisu, brak
projektu lub przebiegu). Raport komend zapisujących i `metrics --json` to JSON z polami `schema`, `status` (`PASS` albo
`BLOCKED`) i `reason` przy odmowie. `approvals wait` (odczyt zgód) kończy się `3` przy przekroczeniu czasu.

### `approvals record`

- Dopisuje jedną linię JSON do `.skillsync/runs/<run-id>/approvals.jsonl` pod blokadą plikową (jedyne źródło, które czyta
  `skillsync approvals wait`). Pola wpisu: `schema`, `at` (UTC), `word`, `sha`, `by`, `source` (`cli`, `dashboard` albo
  `ide`), opcjonalnie `file`, `run`, `plan`, `target`, `branch`, `ahead`, `mode`, `via` (tylko zgody z gniazda zgód trybu dwóch gniazd, zapisuje
  je serwer; obiekt `{port, host, peer}`, zob. „Ślad zgody”). Wpis z `record` ma zawsze `at`
  (`YYYY-MM-DDTHH:MM:SSZ`), `run` (= `--run`) i `by` (nazwa użytkownika systemu, `unknown`, gdy jej nie da się odczytać);
  pola opcjonalne bez wartości są pominięte (nie `null`). Wartości tekstowe przechodzą przez maskowanie sekretów.
- `--sha` (7–64 znaków hex, małe litery): dla `GO plan`/`GO implement` git blob sha zatwierdzanego pliku (domyślnie: sha
  pliku z `--file`), dla `CP`/`READY` HEAD gałęzi roboczej, którą zgoda wypchnie (domyślnie HEAD repozytorium projektu,
  odczytany z `.git` bez uruchamiania git). Bez `--sha` i bez czytelnego HEAD (projekt bez `.git`): `BLOCKED`. Słowo GO
  bez `--file` i bez `--sha` = `BLOCKED` (sha zgody GO to zawsze sha pliku, nigdy HEAD).
- `--source`: komenda przyjmuje `cli` albo `ide` (panel IDE woła ją bez terminala, więc TTY nie jest wymagane).
  `--source dashboard` z komendy = `BLOCKED` (kod `2`): tę wartość ustawia wyłącznie serwer dashboardu, wołając
  `status.record_approval(..., source="dashboard")` we własnym procesie.
- `--file` tylko dla słów GO: `docs/specs/*.md` dla `GO plan`, `docs/plans/*.md` dla `GO implement` (plik zwykły, nie
  dowiązanie, nie `README.md`); dla `CP`/`READY` → `BLOCKED`. `--target`/`--branch`: nazwa gałęzi.
- Odmawia (kod `2`) drugiego zapisu tego samego słowa z tym samym `sha` w tym samym przebiegu (dla GO: ta sama wersja pliku,
  dla CP/READY: ten sam HEAD); nieznane słowo, zły `--source`, run-id spoza wzorca `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`, brak
  katalogu przebiegu, dowiązanie na drodze `.skillsync/runs/<run-id>` lub dowiązany `approvals.jsonl`, plik, który
  przekroczyłby 256 KiB (czytelnicy pomijają większy plik w całości), albo blokada trzymana dłużej niż 10 s też kończą się
  `BLOCKED`.
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
- bez repozytorium git (wejście to same pliki tekstowe): `git.*` ma wartości `null`, `delivery.ahead` = `null`;
- wyjątek: przypadek `cp-ready` wymaga repozytorium; test zakłada w kopii `input/` repozytorium git (gałąź `feat/demo`,
  jeden commit, neutralna tożsamość, bez `origin`), a sha HEAD (pełne, 12 i 8 znaków) zastępuje `<head>`. Wariant
  `expected-after.json` to wynik po zapisie zgody `CP` przez `record_approval` (źródło `dashboard`, w procesie testu): w zapisanym wpisie `at` = `2026-01-12T12:00:00Z`,
  `by` = `user`, a godzina w `reason`/`blocked` = `12:00`;
- JSON: `sort_keys`, wcięcie 2, `ensure_ascii=false`, końcowy znak nowej linii.

## Pola najwyższego poziomu

| Klucz | Typ | Opis |
|---|---|---|
| `schema` | int | wersja kontraktu, obecnie `1` |
| `app` | obiekt | `name` (`"dashboard"`), `version` (wersja generatora, string) |
| `project` | obiekt | `name` (nazwa katalogu projektu), `path` (ścieżka bezwzględna) |
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
| `session` | obiekt | adres sesji agenta (opcjonalny) |
| `prototype` | obiekt | prototyp powiązany ze specem |
| `docs` | tablica | dokumenty do przeglądania, pogrupowane |
| `readiness` | obiekt | NOWE: brief gotowości przebiegu |
| `assumptions` | obiekt | NOWE: założenia ze speca |
| `blockingQuestions` | obiekt | NOWE: pytania blokujące ze speca |
| `expertQuestions` | obiekt | NOWE: otwarte pytania do eksperta ze wszystkich przebiegów |
| `prototypes` | tablica | wszystkie prototypy projektu (do 50, wg `slug`): `{slug, iteration, screens, accepted, stale, threads, commentsAfterAcceptance, writing}`; `threads` = liczby wątków wg statusu (open, awaiting, decided, closed…), `writing` jak w `prototype.writing`; odnośnik buduje host z `slug` (`deps.prototypeUrl`) |
| `result` | obiekt albo null | `{text, path}`: sekcja `## Wynik` wybranego planu (do 4 KiB, tekst), gdy plan jest `in_progress`, `done` albo `implemented`; null bez planu, bez sekcji albo dla planu, który nie ruszył |
| `eta` | obiekt albo null | NOWE (addytywnie, `schema: 1`): szacowany koniec bieżącej operacji z najnowszego checkpointu (`eta:`) |
| `substeps` | tablica | NOWE: podkroki `Fn.k` z czasem |
| `timing` | obiekt | NOWE (addytywnie, `schema: 1`): czas pracy i czas czekania na człowieka per krok i per faza planu |

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
(ścieżka speca lub null), `run` (id przebiegu lub null). Plan wiąże się z przebiegiem tylko linią `Przebieg: <run-id>` w nagłówku planu: sam `checkpoint --run-id X` tworzy `.skillsync/runs/X`, ale bez tej linii `plans[].run` i `run.id` strony statusu zostają `null` (strona nie zgaduje powiązania).

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
`trwa: <klucz etapu>[, …]`.

Składnia segmentów: `;` kończy segment tylko poza nawiasami `()`, `[]`, `{}` (opis w nawiasie może zawierać `;`); linia z
niezbalansowanym nawiasem dzieli się po każdym `;`, więc id po takim `;` może zginąć z odczytu; `parseWarnings` zgłasza zawsze to dzielenie wpisem
`unbalanced-bracket` (segment `raport`, fragment od elementu z niezamkniętym nawiasem, raz na checkpoint), także gdy żadne id nie zginęło. Każdy element listy „w toku” i „zrobione” zaczyna się od id kroku;
elementy rozdziela przecinek, a id w jednym elemencie łączą `/`, `+`, `&`, `‖`, `||`, „i” i „oraz”. Element bez id na początku
(`no-step-id`) i id dalej w elemencie niż na początku (`later-id`) nie wchodzą do `progress.active`; zgłasza je `parseWarnings`.
Segment `trwa:` nazywa etapy, które trwają teraz (klucz etapu bez `stage_` albo jego etykieta, np. `spec_review`, `prototyp`); liczy się
tylko najnowszy checkpoint, a `czeka:` w tej samej linii wygrywa (sesja czeka, nie pracuje). Suffix `(kto: <rola>[ <login>])` w
wartości `czeka:` wskazuje adresata (role: `acceptor`, `developer`, `expert`, `commenter`, `agent`; po polsku też „akceptujący”,
„programista”, „ekspert”, „komentujący”, „odbiór”).

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
założenia nigdy nie są akcją „Odpowiedz na pytania”.

Dwie pozycje powstają z plików, bez znacznika w checkpoincie:

- `source: "writing"`: prototyp przebiegu ma ważny znacznik zapisu (`prototype.writing` nie jest null). `what` = „agent pisze ekrany (do HH:MM)”
  (czas lokalny hosta strony z `expiresAt`), `since` = `HH:MM` początku pisania, `link` = `{kind: "prototype", slug}`. Pozycja jest pierwsza
  i zastępuje każdą pozycję akceptacji prototypu (też tę z `czeka:` w checkpoincie), bo nie ma czego akceptować, dopóki agent pisze.
  Renderer pisze „Sesja pracuje od HH:MM: …”, nie „Sesja czeka na Ciebie”.
- `source: "expert"`: `expertQuestions.blocking > 0`, a najnowszy checkpoint nie ma znacznika `czeka:`. `what` = „odpowiedź eksperta (N blokujących)”,
  `link` = `{kind: "expert", slug, url}`: przy jednym przebiegu z otwartymi pytaniami jego `slug` i `/_experts/<slug>`, w pozostałych
  przypadkach `slug: null` i `/_experts`. Renderer składa adres jak w toaście (`deps.prototypeOrigin(port)` plus ścieżka; bez adresu serwera
  prototypów pozycja nie ma odnośnika). Pytania bez `blocking` nie tworzą pozycji.

### `silence`

`{since, thresholdMin}` (czas ostatniego raportu i próg w minutach, 35), gdy plan jest `in_progress`, są raporty i sesja
nie czeka jawnie (żaden element `waiting` ze źródłem `checkpoint` ani `expert`); w przeciwnym razie null. Minuty liczy konsument.

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
`cp`/`ready` id przebiegu), `sha` (git blob sha pliku do zgody GO albo HEAD dla CP/READY; pusty string, gdy brak), `given`
(wpis lub null), `target` (tylko `cp`/`ready`; dokąd oddać), `run` (id przebiegu, do którego trafi wpis zgody, gdy katalog
`.skillsync/runs/<run-id>` istnieje; inaczej null; pole dodane addytywnie, `path` dla `cp`/`ready` bez zmian).

`given` w stanie `after`: wpis zgody w postaci zapisanej w `approvals.jsonl` (wpis z `approvals record` ma zawsze `at`).
Dla `spec`/`plan` stan `after` wynika ze statusu pliku (`approved` i dalej), więc zgoda mogła zostać dana poza
`approvals.jsonl` (np. `spec approve`/`plan approve` w IDE albo słowo wpisane w sesji): wtedy `given` = `{"at": null}`, a
`reason` mówi „zgoda … już dana”. Dla `cp`/`ready` stan `after` jest tylko przy wpisie, którego `sha` = `git.headFull`.

Wpisy zgód (`given`, `last`) pochodzą z `approvals.jsonl` bieżącego przebiegu w kolejności pliku, w postaci zapisanej; linie
spoza reguł `skillsync status` (nieznane słowo, nieczytelne `at`, sha spoza 7–64 znaków hex, plik ponad 256 KiB) są pomijane.

### `delivery`

`mode` (`pr`, `direct`, `invalid`), `base` (string lub null), `source` (`manifest`, `default`), `branch`, `target` (dokąd
oddaje „CP”; w `direct` z linii planu `Oddanie:`), `remote`, `pushed` (bool lub null), `ahead` (int lub null; commity
niewypchnięte; renderer pokazuje je przy gałęzi w nagłówku jako „N niewypchniętych” (neutralnie w trybie `direct`, do zakończenia planu)), `words[]`: `{word: "CP"|"READY", active, tip, target}` (`READY` tylko w trybie `pr`; przy `invalid` pusta
tablica).

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

## Pola dodane do kontraktu

### `readiness`

Źródło: `.skillsync/runs/<run-id>/readiness.md` (produkuje `skillsync readiness --run-id`): pierwsza linia
`Readiness: PASS|BLOCKED`, potem `Data:` i `Plan-sha:`.

| Pole | Typ | Opis |
|---|---|---|
| `status` | `"PASS"`, `"BLOCKED"` albo null | null = brak pliku (stan „brak danych”) albo nierozpoznana pierwsza linia |
| `date` | string lub null | `Data:` |
| `planSha` | string lub null | `Plan-sha:`; konsument porównuje z `plan.fileSha` (różnica = brief nieaktualny) |
| `source` | string lub null | ścieżka pliku względem projektu; null bez pliku |

Pierwsza niepusta linia decyduje o `status`; `Data:` i `Plan-sha:` są czytane także przy nierozpoznanej pierwszej linii
(wtedy `status` = null, a `source` wskazuje plik). Bez katalogu przebiegu wszystkie pola są null.

### `assumptions` i `blockingQuestions`

Źródło: sekcja `## Pytania otwarte` bieżącego speca (`spec.path`).

- Założenie: `- Q<n> [ZAŁOŻENIE]: <pytanie> — przyjęte: <wartość>` → `assumptions.items[]` = `{id, question, accepted}`,
  `assumptions.count` = długość listy.
- Pytanie blokujące: `- Q<n>: <pytanie>` (bez znacznika w nawiasie kwadratowym) → `blockingQuestions.items[]` =
  `{id, question}`, `count`.
- Linie ze znacznikiem innym niż `[ZAŁOŻENIE]` (np. `[ROZSTRZYGNIĘTE …]`, `[EKSPERT]`) i linie spoza sekcji są pomijane.
- Założenia nie są akcją „Odpowiedz na pytania”; tej akcji (`waiting`) dotyczą tylko pytania blokujące zgłoszone przez sesję.
- Pola zostają w kontrakcie dla innych konsumentów, ale renderer strony ich nie rysuje: lista pytań i założeń w Akcie I została
  zastąpiona toastem opartym na `expertQuestions`.
- Separator przed `przyjęte:` może być półpauzą, pauzą, `--` albo `-` (otoczonymi spacjami); znacznik bez rozróżnienia
  wielkości liter, także bez polskich znaków (`[ZALOZENIE]`). Założenie bez `przyjęte:` ma `accepted` = `""`.
- Wiersz tabeli `| Q<n> | <pytanie> | … |` (jak w szkielecie speca) jest pytaniem blokującym; `question` = pierwsza niepusta
  komórka po id. Id pogrubione (`**Q4**`) jest rozpoznawane; powtórzone id liczy się raz (pierwsze wystąpienie); bloki kodu
  są pomijane.
- Bez speca: `{count: 0, items: []}`.

### `expertQuestions`

Źródło: pliki `docs/prototypes/<slug>/questions.json` WSZYSTKICH przebiegów projektu (format `expert-questions/1`), a nie
wiersze widocznego speca. Czytane tak samo jak w serwerze prototypów (bez jego importu), tylko do odczytu: katalog i pliki
bez dowiązań symbolicznych, `prototype.json` obok, plik do 2 MiB (serwer zapisuje do 1 MiB plus jedna operacja), uszkodzony albo nieznany plik jest pomijany.

- Pytanie jest otwarte od pierwszego `ask` do pierwszego `answer` albo `withdraw` (wycofane i odpowiedziane się nie liczą);
  blokujące = `blocking: true` w `ask`.
- `open`, `blocking`: sumy po wszystkich przebiegach; zawsze obecne, `0` bez pytań. Pytania do eksperta nie zależą od
  wybranego planu ani speca, więc pytanie z przebiegu A nie znika po starcie przebiegu B.
- `runs[]`: tylko przebiegi z otwartymi pytaniami, w kolejności `slug`: `{slug, open, blocking, url, addressees}`; `url` to ścieżka
  strony przebiegu na serwerze prototypów (`/_experts/<slug>`), host dokłada konsument. `addressees` (opcjonalne): `"all"`, gdy któreś otwarte
  pytanie przebiegu nie ma adresatów, inaczej posortowana suma loginów adresatów otwartych pytań.
- `items[]` (opcjonalne): do 50 najnowszych pytań wszystkich przebiegów, najnowsze pierwsze: `{id, runSlug, text (do 160 znaków), blocking,
  addressees (lista loginów, pusta = wszyscy eksperci), state (open, answered, withdrawn), at, url}`.
- Widok widza (`approvals.viewer`): ta sama odpowiedź co dla operatora (loginy, nazwiska, treści pytań, plany i teksty checkpointów są widoczne; strona jest wewnątrzfirmowa). Różni się tylko flaga `approvals.viewer` (bez zapisu zgód) i `approvals.here`; w trybie dwóch gniazd obie flagi (i `approvals.role`) pochodzą z gniazda, nie z nagłówka `Host`, a treść pozostaje bez filtra także na gnieździe odczytu (decyzja właściciela z 15.11.2026).
- Renderer pokazuje toast „Pytania do eksperta: N otwartych (M blokujących)” w banerze, gdy `open > 0`: to stan, nie wynik
  akcji, więc bez timera i bez „×”; znika, gdy `open` spadnie do `0`. Odnośnik „Odpowiedz na pytania” prowadzi do
  `/_experts/<slug>` przy jednym przebiegu albo do `/_experts` przy kilku, o ile host zna adres serwera prototypów
  (`deps.prototypeOrigin(port)`); bez niego toast jest samym tekstem. Pole nie zależy od `approvals.here` ani `viewer`.

### `eta`

Źródło: segment `eta: <operacja> do HH:MM` NAJNOWSZEGO checkpointu przebiegu (`.skillsync/runs/<run>/checkpoints.md`), w tej
samej linii co `zrobione: …; w toku: …` (segmenty rozdziela `;`):

```
- 14:05 w toku: F1.2; eta: rysowanie 12 ekranów prototypu do 15:10
```

Agent pisze segment PRZED długą operacją (spec, prototyp i jego iteracje, plan, krok implementacji), powtarza go w każdej linii
raportu aż do końca operacji (czytana jest tylko najnowsza linia) i po przekroczeniu podaje w nowej linii nowy termin (bez zatrzymywania pracy); czas bierze z `date`, nie z szacunku zegara.

- `{operation, until, untilIso, since}`: `operation` to tekst przed ` do `, `until` to `HH:MM`, `since` to czas checkpointu,
  `untilIso` to pełna data z godziną rozwiązana jak w `timing` (start przebiegu `RUN=` plus przejścia przez północ;
  szacunek o ponad 12 h wcześniejszy niż checkpoint liczy się jako następny dzień); bez startu przebiegu `untilIso` jest null.
- `eta` jest null, gdy najnowszy checkpoint nie ma segmentu `eta:` (albo ma `eta: -`, nieczytelną godzinę, pustą operację),
  ma `czeka:` (sesja czeka na człowieka, nie pracuje) albo plan jest `done`/`implemented`. Nowszy checkpoint zastępuje
  wcześniejsze szacunki.
- Serwer nie czyta zegara: „przekroczono” liczy konsument z `untilIso`. Renderer pokazuje toast „<operacja>, koniec ok.
  HH:MM”, a po przekroczeniu „<operacja>: przekroczono ETA o N min” (od 60 min „o H h M min”) (stan, nie wynik akcji: bez timera i bez „×”; widz widzi
  go tak samo). Pole nie zależy od `approvals.here` ani `viewer`.

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
| `totals` | obiekt | `{workSeconds, waitSeconds, windows, unknownWindows, unknownSteps, complete}`; `waitSeconds` = suma okien znanych, `workSeconds` = suma faz z czasem pracy (null, gdy żadna faza nie ma znanego czasu pracy: „brak danych”, nie zero), `complete` ⇔ brak okien i kroków `unknown` i `available` |

Renderer pokazuje w akcie III rozwijaną sekcję „Czas pracy i czekania”: sumy na fazę (praca, czekanie, znacznik „niepełne” przy
`complete: false`) oraz sumę przebiegu, a w dymku okna czekania (`what`, od–do, źródło albo „nieznane”). Brak danych to
„brak danych”, nie zero. Wartości wstawiane są jako tekst.

## Metryki przebiegu (`dashboard metrics`)

`skillsync dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` liczy ogólne metryki jednego przebiegu (bez
formatów warstw). Tylko odczyt; bez `--json` drukuje ten sam JSON. Wejście:

- dziennik `.skillsync/runs/<run-id>/checkpoints.md` (te same znaczniki „w toku”/„zrobione” co `substeps[]`; czasy liczy ta
  sama funkcja co `show`, więc podkrok z czasem w obu wynikach ma identyczne `seconds`, `startedAt`, `finishedAt`);
- rekordy checkpointów `.skillsync/runs/<run-id>.json` (`skillsync checkpoint`), jeśli plik jest;
- plan: `--plan` (ścieżka względna planu projektu; nieznany plan = `BLOCKED`), bez niego plan z `Przebieg: <run-id>` (wybór
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
  (bez `by`, `schema`). Dashboard pokazuje wpisy jednego przebiegu w postaci zapisanej (`given`, `last`), więc ma własny
  odczyt pliku z tymi samymi regułami przyjęcia wpisu.
- Status planu: `_status_fields` czyta pierwszą linię statusu z pierwszych 4000 znaków (także `**Status:**`, `- Status:`);
  słowo statusu to litery i `_` (zapis `in-progress` daje `in`, nie `in_progress`).

## Serwer (`dashboard serve`)

`skillsync dashboard serve <projekt> [--port 5140] | [--read-port N] [--approve-port M] [--read-bind A] [--approve-bind A] [--allow-proxy-headers]
[--prototype-port N] [--host H] [--approve-host H ...] [--read-host H ...] [--no-approvals] [--plan <rel>]
[--session-url https://…] [--layer id=ścieżka ...]` (moduł `skillsync_workflow/dashboard_http.py`, ładowany leniwie; tryb
dwóch gniazd: `skillsync_workflow/dashboard_ports.py`) działa na pierwszym planie; Ctrl+C albo SIGTERM kończy go raportem
`{"status": "PASS", "url", "stopped": true}` (kod `0`; w trybie dwóch gniazd `{"status": "PASS", "stopped": true, "ports": {"read": N, "approve": M}}`, bez pól „read”/„approve” dla pustego gniazda).
Odmowa startu = `BLOCKED` (kod `2`): port spoza 5100–5199 albo zakazany (4444–4448, 9222, 9333), brak manifestu, brak
`skills/dashboard/assets/index.html` w aktywnych warstwach, `--session-url` inny niż https bez danych logowania, `--plan`
spoza dozwolonych dokumentów, port zajęty, kombinacja flag z tabeli niżej. Port wybiera się per projekt (domyślnie 5140).

### Tryb dwóch gniazd

`--read-port N` i/lub `--approve-port M` uruchamia po jednym gnieździe HTTP na podany port. Numer podaje się jawnie (flagi
nie mają wartości domyślnej); przyjęte numery to odczyt 5140 i zgody 5141, a samo pojedyncze gniazdo nie dostaje drugiego
numeru. Oba gniazda dzielą jeden stan projektu (jeden watcher, jeden strumień SSE). Rola wynika z **gniazda**, nie z nagłówka `Host`:

| Gniazdo | Rola (`approvals.role`) | Zachowanie |
|---|---|---|
| odczyt (`--read-port`) | `read` | `GET`/`HEAD` jak na stronie; każda metoda zapisu (`POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS`) = 404, przed innymi sprawdzeniami; `approvals`: `here` false, `viewer` true, `tokenRequired` false; `/healthz` bez `tokenId`; brak tokenu w odpowiedziach |
| zgód (`--approve-port`) | `approve` | `GET`/`HEAD` jak na odczycie oraz `POST /api/approve` z kolejnością odmów poniżej; `here` true, `tokenRequired` true (gdy są zgody); `/healthz` z `tokenId` |

Tryb `--port` (jedno gniazdo) ma rolę z nagłówka `Host` (akapit „Kontrola `Host`” w sekcji „Serwer”), nie ma
pola `approvals.role`, a kolejność odmów to: `Host`, trasa, `X-Status-Board`, `Origin` (porównanie samej nazwy),
`Content-Type`, token. Wywołanie `serve` bez nowych flag jest tym trybem.

**Nasłuch.** W trybie dwóch gniazd oba domyślnie na `0.0.0.0`; `--host H` ustawia oba na `H`; `--read-bind A` i
`--approve-bind A` nadpisują `--host` dla swojego gniazda (`127.0.0.1` zawęża do tej maszyny). W trybie `--port` nasłuch
to `127.0.0.1`, chyba że podano `--host`. Przy każdym gnieździe poza loopbackiem konsola wypisuje ostrzeżenie; dla gniazda
zgód jest ono osobne: port zgód widzi każde urządzenie w sieci, a token idzie nieszyfrowanym HTTP.

**Konsola przy starcie** (jedna linia na gniazdo): `odczyt (do tunelu i linków): <adres>  [nasłuch …, bez zgód i tokenu]`
(bez tokenu) oraz `zgody (przyciski, tylko dla Ciebie, nigdy do tunelu): <adres z tokenem>  [nasłuch …]`.

**Start atomowy.** Oba gniazda wiązane są przed wątkami, plikami stanu i wpisami w konsoli. Błąd drugiego wiązania zamyka
pierwsze i kończy `BLOCKED` (`nie można nasłuchiwać …; porty … muszą być wolne`) bez śladu stanu. Ctrl+C albo SIGTERM
zamyka oba gniazda i usuwa oba pliki stanu.

**Jak zatrzymać serwer na Windows** (`dashboard serve` i `prototype serve`; na Linuksie i macOS bez zmian: Ctrl+C albo SIGTERM). Zmierzone na Windows 11 z Pythonem 3.12:

| Sposób | Skutek | Plik stanu po zatrzymaniu |
|---|---|---|
| Ctrl+C w konsoli serwera | łagodnie: raport `PASS`/`stopped`, kod wyjścia 0 | usunięty |
| `CTRL_BREAK_EVENT` do serwera uruchomionego z `CREATE_NEW_PROCESS_GROUP` (`os.kill(pid, signal.CTRL_BREAK_EVENT)` z launchera, wspólna konsola) | łagodnie jak Ctrl+C (sygnał `SIGBREAK`), kod wyjścia 0; drugi CTRL_BREAK w ciągu 10 s po pierwszym jest ignorowany (nie przerywa sprzątania), późniejszy zatrzymuje ponownie | usunięty (w trybie dwóch gniazd oba pliki) |
| `Stop-Process`, `taskkill /F`, `Popen.terminate()`, Menedżer zadań | twardo (`TerminateProcess`), bez sprzątania | **zostaje** (z tokenem zgód) |
| `taskkill /PID` bez `/F` | nie działa: „This process can only be terminated forcefully (with /F option).” | – |
| zamknięcie okna konsoli | twardo (nie obsługujemy `CTRL_CLOSE_EVENT`) | zostaje |

Po twardym zakończeniu port jest wolny od razu, a następny start na tym samym porcie zastępuje plik stanu; plik innego portu zostaje, aż ten port zostanie użyty ponownie. Ctrl+C dziedziczy ustawienie „ignoruj CTRL+C” po procesie rodzicu (np. skrypt uruchomiony z sesji ssh): wtedy serwer uruchomiony z takiego procesu go nie odbierze, a CTRL_BREAK tak. Dziecko uruchomione z `CREATE_NEW_PROCESS_GROUP` w ogóle nie dostaje Ctrl+C (system wyłącza go w nowej grupie), więc launcher zatrzyma je tylko CTRL_BREAKiem.

**Kombinacje flag** (wszystkie walidacje przed bindem; `BLOCKED`, kod `2`):

| Kombinacja | Wynik |
|---|---|
| `--port` z `--read-port` albo `--approve-port` | BLOCKED (niejednoznaczne) |
| `--read-port` i `--approve-port` z tym samym numerem | BLOCKED |
| `--no-approvals` z `--approve-port` | BLOCKED (port zgód nie startuje bez tokenu) |
| `--no-approvals` z `--read-port` | dozwolone: tylko odczyt |
| `--read-bind` bez `--read-port`; `--approve-bind` bez `--approve-port` | BLOCKED |
| `--allow-proxy-headers` bez `--approve-port` (także z `--port` albo samym `--read-port`) | BLOCKED |
| `--approve-host '*'` | BLOCKED |

`--approve-host` z samym `--read-port` jest dozwolone: jego nazwy otwierają również odczyt. `--read-host '*'` (dowolna nazwa)
działa tylko na gnieździe odczytu; na gnieździe zgód nie daje dostępu.

### Kolejność odmów na gnieździe zgód

Każda odmowa kończy żądanie; odpowiedź nie mówi, które warstwy przeszły.

| Krok | Warunek | Odpowiedź |
|---|---|---|
| 1 | `Host` spoza: loopback, literał IP tej maszyny, nazwa z `--approve-host` | 403 `HOST_REFUSED` („Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host).”) |
| 2 | trasa inna niż `POST /api/approve` | 404 |
| 3 | brak `X-Status-Board: 1` | 403 „Brak nagłówka X-Status-Board.” |
| 4 | `Origin` niezgodny (tabela niżej) | 403 „Obcy Origin.” |
| 5 | nagłówek pośrednika bez `--allow-proxy-headers` | 403 „Zapis przez pośrednika … jest odrzucony …” |
| 6 | `Content-Type` inny niż `application/json` | 403 „Oczekiwano application/json.” |
| 7 | token `X-Approve-Token` (porównanie stałoczasowe, ostatni krok) | 403 `code: approve_token`, jedna treść dla braku, pustego, złego i nie-ASCII tokenu |
| 8 | długość ciała 1–4096, obiekt JSON, potem `Board.approve` | 400, potem 409/403 z `Board.approve` |

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
Gniazdo odczytu dodatkowo przyjmuje nazwy z `--read-host`. Nazwa spoza listy dostaje 403, więc DNS rebinding nie działa.
Granica zaufania: nazwa maszyny jest rozwiązywana lokalnie (`/etc/hosts`, DNS), więc kto kontroluje to rozwiązywanie, decyduje, które adresy `.255` uchodzą za lokalne. To nie osłabia ochrony, bo na gnieździe zgód barierą jest token, a nie `Host`; wynik rozwiązania nazwy służy do oceny nagłówka `Host` i do podpowiedzi adresów na konsoli, i nie wolno go użyć jako dowodu do niczego innego.

**Plik stanu i `dashboard url`.** Każde gniazdo ma własny plik stanu (po numerze portu), z polami `role` (`read` albo
`approve`) i `peer` (port drugiego gniazda); brak `role` oznacza tryb `--port`. Rekord odczytu ma `approvals: false` i nie ma
tokenu. `dashboard url <projekt>` (bez `--port`) wybiera rekord zgód; `ambiguous_server` dotyczy tylko kilku serwerów zgód,
więc dwa gniazda jednego procesu nie kolidują. `dashboard url <projekt> --read` wypisuje adres odczytu bez fragmentu, do
przekazania osobie z zewnątrz; bez gniazda odczytu = `server_not_running`. `--json` zwraca `{schema, status, url, port, pid}`;
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
| `GET /api/status[?plan=<rel>]` | kontrakt `schema: 1` (to samo co `show`, z tym samym maskowaniem sekretów, z żywym `version`, `session.url`, `prototype.up`) plus `approvals.here` (bool: nagłówek `Host` żądania jest na liście hostów zgód, czyli loopback albo `--approve-host`; `false` dla hosta z `--read-host`) i `approvals.viewer` (bool: host wyłącznie do odczytu z `--read-host`; wtedy `here` = `false`) oraz `approvals.tokenRequired` (bool: zapis zgody wymaga nagłówka `X-Approve-Token`; `false` przy `--no-approvals`; tylko HTTP, `show` go nie ma; wartość tokenu nigdy nie jest w odpowiedzi); w trybie dwóch gniazd także `approvals.role` (`read`/`approve`), a `here`, `viewer` i `tokenRequired` są z gniazda, nie z `Host` |
| `GET /api/doc?path=<rel>` | `{path, title, status, mtime, size, truncated, html}`: podgląd `*.md` z `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` i `.skillsync/runs/<run>/`; bez `..`, ścieżek absolutnych, ukrytych katalogów i dowiązań poza listę; limit 2 MB (`truncated`); inne = 404; dokument i cała odpowiedź (także `path` i `title`) są maskowane (`redact`), tak jak `/api/status`; zasady odczytu tekstu niżej |
| `GET /events` | SSE: `hello {version, heartbeat}` (z `retry: 3000`), `change {version}` po zmianie obserwowanych plików, `ping {version, t}` co 15 s; najwyżej 64 połączenia (dalej 503; w trybie dwóch gniazd osobne udziały: 48 na gnieździe odczytu i 16 na gnieździe zgód, więc urządzenie zapełniające odczyt nie odbierze odświeżania stronie zatwierdzającego); `HEAD /events` = same nagłówki (200, `text/event-stream`), bez strumienia i bez zajmowania miejsca |
| `GET /api/descriptions` | `descriptions.json` skilla (`{klucz: {title, text}}`) |
| `GET /healthz` | `{ok: true, version}`; `tokenId` tylko na gnieździe zgód (lub w trybie `--port` z zgodami) |
| `POST /api/approve` | tylko na gnieździe zgód (na gnieździe odczytu każda metoda zapisu = 404); `{kind: spec\|plan\|cp\|ready, path, sha[, plan]}`; tylko akcja, którą `/api/status` właśnie oferuje w stanie `waiting`, z tym samym `sha`; odpowiedź 200: `{ok, word, file, next}`, dla GO także `trail` (ścieżka `approvals.jsonl` przebiegu albo null bez przebiegu) |

W trybie `--port` kontrola `Host` na wszystkich endpointach (`GET`, `HEAD`, SSE `/events`, `POST`; w trybie dwóch gniazd zob. „Reguła `Host` w trybie dwóch gniazd”): nagłówek `Host` musi wskazywać loopback
(`localhost`, `127.0.0.1`, `[::1]`, z dowolnym portem; bez rozróżniania wielkości liter), nazwę z `--approve-host` albo
nazwę z `--read-host` (obie listy: port pomijany, bez rozróżniania wielkości liter, flagi powtarzalne). Inny albo
brakujący `Host` (DNS rebinding, tunel lub nazwa LAN spoza list) = 403
`{"error": "Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host)."}` bez treści strony,
dokumentu ani statusu; przy `--verbose` odmowa trafia do logu stderr (metoda, ścieżka, host).

| Klasa `Host` (tryb `--port`) | Odczyt (`GET`, `HEAD`, `/events`, `/api/*`, `/healthz`, assety) | Zapis (`POST /api/approve`) | `approvals.here` / `approvals.viewer` |
|---|---|---|---|
| loopback | tak | tak (chyba że `--no-approvals`) | `true` / `false` |
| `--approve-host` | tak | tak (chyba że `--no-approvals`) | `true` / `false` |
| `--read-host` | tak | w przeglądarce widza nigdy: 403 z tym samym komunikatem co obcy `Host`, także z poprawnymi `Origin`, `X-Status-Board` i `Content-Type` | `false` / `true` |
| inny albo brak | 403 | 403 | (brak odpowiedzi z danymi) |

`--read-host` służy pokazowi przez tunel: połączenie przychodzi z loopbacku, ale przeglądarka widza wysyła `Host:
<nazwa>:5140`; widzowie widzą wszystko (także `assumptions`, `blockingQuestions`, `waiting`, `silence`), a zgody daje
tylko prowadzący pod loopbackiem albo `--approve-host`. Nazwa obecna na obu listach (albo loopback podany w
`--read-host`) zostaje hostem zgód, bo serwer nie odróżni prowadzącego od widza pod tym samym `Host`; serwer ostrzega o
tym przy starcie. Przy starcie serwer wypisuje osobno hosty odczytu i zgód (`--approve-host`) i hosty tylko do odczytu
(`--read-host`). Widok tylko do odczytu dla wszystkich = `--no-approvals`. Tunel `ssh -R`/`-L` na `127.0.0.1`, w którym
przeglądarka wysyła `Host: localhost:<port>`, działa bez flagi (i daje zgody).

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

- Akt I nie ma sekcji z pytaniami: ani listy blokujących, ani założeń (także z zerem, dla prowadzącego i widza). Pytania do
  eksperta pokazuje toast w banerze z pola `expertQuestions` (sekcja `expertQuestions`).
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
`approvals wait --file`). „CP”/„READY” = `status.record_approval(..., source="dashboard")` z `run`, `plan`, `target` (tylko
CP), `branch`, `ahead`, `mode`; odmowa rdzenia po przejściu kontroli serwera (blokada pliku, limit 256 KiB, zła nazwa
gałęzi, wyścig z drugim zapisem) = 500 z powodem. Id przebiegu: wzorzec `status.RUN_ID` (`[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`):
pierwszy znak alfanumeryczny (więc ani `.`, ani `..` jako całe id) i bez `/`; ciągu `..` wewnątrz id (`a..b`) wzorzec nie
wyklucza, a ponieważ `/` jest zabronione, taki człon nie wychodzi poza `.skillsync/runs/`. Dowiązanie na drodze
`.skillsync`, `.skillsync/runs` albo katalogu przebiegu wyłącza odczyt przebiegu w `show` i `/api/status` (`run.exists:
false`, akcje bez `run`) i odmowę zapisu w `record`; serwer przyjmuje z żądania tylko id, które `/api/status` właśnie oferuje.

Zabezpieczenia `POST /api/approve` (odmowa = 403, bez wywołania `set_doc_status`/`record_approval`): `Host` z loopbacku
albo `--approve-host` (sprawdzany przed długością treści; `--read-host` nie wystarcza); nagłówek `X-Status-Board: 1`; `Origin`, jeśli jest, z tej samej listy; `Content-Type:
application/json`; na końcu nagłówek `X-Approve-Token` z tokenem serwera (porównanie stałoczasowe; brak albo zły token = ta sama odmowa 403 „Brak albo zły token zgody…”, sprawdzany jako ostatni, więc każda wcześniejsza odmowa jest taka sama z tokenem i bez). Kontrola `Origin` porównuje tylko nazwę hosta (`localhost`, `127.0.0.1`, `[::1]` albo nazwę z
`--approve-host`; nazwa z `--read-host` to obcy `Origin`); schematu i portu nie porównuje, więc inna strona na loopbacku (inny port) przechodzi ten krok. Przed
obcą stroną w przeglądarce chroni przede wszystkim własny nagłówek `X-Status-Board` i `Content-Type: application/json`:
oba wymuszają preflight CORS, na który serwer nie odpowiada; brak `Origin` (klient spoza przeglądarki) jest dopuszczony.

**Token zgód.** Serwer tworzy go na proces (`secrets.token_urlsafe(24)`); nie ma go w `/api/status`, assetach, logu ani raporcie błędu. Użytkownik dostaje go w adresie z fragmentem
`http://127.0.0.1:<port>/#zgoda=<token>` (fragment nie trafia do serwera ani do proxy, więc klient z podrobionym `Host` go nie zna): z linii konsoli `dashboard serve`
albo z `skillsync dashboard url <projekt> [--port N] [--host NAZWA] [--json]`, który czyta plik stanu `<katalog danych maszyny>/dashboard/<skrót katalogu projektu>-<port>.json`
(poza drzewem projektu; 0600 w katalogu 0700, na Windows ACL właściciela przez wspólny pomocnik, gdy jest; zapis atomowy, ACL na pliku tymczasowym przed `os.replace`; usuwany przy zamknięciu serwera; serwer z `--no-approvals` zapisuje ten sam plik BEZ tokenu (`"approvals": false`), żeby `dashboard url` mógł powiedzieć, że zgody są wyłączone; katalog stanu musi być prawdziwym katalogiem użytkownika (dowiązanie albo cudzy katalog na POSIX = pliku nie zapisano, jedno ostrzeżenie), a start serwera sprząta osierocone `.tmp-<skrót>-<port>-*.json` swojego projektu i portu; pole `root` pliku i skrót, który nazywa plik, liczone są z jednej postaci ścieżki (`pathfmt.posix`: `/` także na Windows, jak w innych polach ścieżek raportów), więc serwer i `dashboard url` trafiają w ten sam plik; serwer przy zamknięciu usuwa plik tylko wtedy, gdy zapisany w nim `pid` jest jego własny (stary serwer nie skasuje pliku nowego na tym samym porcie). Znane ograniczenie: na Windows (gdzie `servers` nie sprawdza `pid`, decyduje `/healthz`) rekord serwera `--no-approvals` z obcym procesem odpowiadającym na `/healthz` tego samego portu może dać fałszywe `approvals_disabled` zamiast `server_not_running` (tylko komunikat; token nigdy nie jest ujawniany);
pozostałość po zabitym procesie jest ignorowana albo usuwana: serwer musi odpowiadać `/healthz` z tym samym skrótem tokenu `tokenId` (8 znaków hex, nie token); plik zawiera też adres bindu, pod którym sonduje się serwer, i nazwy z `--approve-host`; adres z tokenem używa loopbacku albo nazwy z `--approve-host`, bo strona odpowiada tylko pod nimi). `dashboard url`: `PASS {url, port, pid}` albo `BLOCKED` (exit 2) z kodem
`server_not_running` (brak żywego serwera z plikiem stanu), `approvals_disabled` (serwer działa z `--no-approvals`: nie ma tokenu), `ambiguous_server` (kilka serwerów projektu, podaj `--port`), `host_not_allowed` (`--host` spoza loopbacku i nazw z `--approve-host` tego serwera), `invalid_project`. Odmowa tokenu ma w treści `code: "approve_token"` (strona po nim, nie po tekście, zapomina nieaktualny token). `/healthz` ma `tokenId` przy serwerze ze zgodami. Strona (`app.js`) czyta fragment `#zgoda=`,
zapisuje token w `localStorage` swojego originu, czyści pasek adresu (`history.replaceState`) i wysyła `X-Approve-Token`; bez tokenu (albo po jego odrzuceniu po restarcie serwera)
przyciski zgód są nieaktywne z jednym powodem, a odczyt działa w pełni. Skutek przechowywania w `localStorage`: token przeżywa zamknięcie przeglądarki (do restartu serwera) i jest czytelny dla skryptów tej strony, więc ewentualny XSS na stronie statusu mógłby go użyć; CSP `script-src 'self'` i sanitizacja treści dokumentów to ograniczają (decyzja o `sessionStorage` do potwierdzenia przez właściciela). Token jest drukowany na stderr `dashboard serve` przy starcie: stderr przekierowany do pliku, dziennika usługi (systemd) albo logów CI zachowa adres z tokenem, więc w takich uruchomieniach nie polegaj na linii startowej i podawaj adres przez `skillsync dashboard url <projekt>`. Przez `--approve-host` token wędruje w nagłówku zwykłego HTTP (bez szyfrowania): używaj w zaufanej sieci albo przez tunel. `--approve-host`: zdalna przeglądarka potrzebuje `Host` na liście i adresu z tokenem (`dashboard url --host NAZWA`).
Dalej `Content-Length` 1–4096 (inaczej 400), treść JSON-obiekt (inaczej 400), `--no-approvals` = 403, akcja
nieoferowana, nieaktywna albo z innym `sha` = 409 (także drugi zapis CP/READY dla tego samego HEAD). Każda odpowiedź ma
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache,
no-store`. Połączenie bez kompletnego żądania jest zamykane po 30 s (timeout gniazda obsługi). Strona HTML ma także CSP
`default-src 'self'; script-src 'self'; …; frame-ancestors 'none'`. Przy `--host 0.0.0.0` inne urządzenia dostają 403,
dopóki ich adres z paska przeglądarki nie trafi do `--approve-host` albo `--read-host` (serwer wypisuje to przy starcie).
