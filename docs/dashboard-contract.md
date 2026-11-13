# Kontrakt JSON dashboardu (`schema: 1`)

Dashboard to strona statusu projektu: ten sam stan, który widzi człowiek w przeglądarce, jest dostępny jako JSON.
Kontrakt jest wspólny dla renderera strony, innych konsumentów (np. IDE) i testów. Przykłady wejścia i oczekiwanego
wyniku: `tests/fixtures/dashboard/<przypadek>/input/` i `expected.json`.

## Komendy

| Komenda | Zachowanie |
|---|---|
| `skillsync dashboard show <projekt> [--json] [--plan <rel>]` | drukuje kontrakt `schema: 1` (JSON; bez `--json` ten sam JSON, formatowany); tylko odczyt, bez zapisu do projektu; `--plan` wybiera plan (ścieżka względna, np. `docs/plans/<plik>.md`), bez niego plan wybierany jest automatycznie (`planAuto: true`) |
| `skillsync dashboard serve <projekt> [--port N] [--host H] [--approve-host H] [--read-host H]` | serwer HTTP nad tym samym JSON (`/api/status`, strumień zmian `/events`, przyciski zgód); port wybiera się per projekt, by strony nie kolidowały |
| `skillsync dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | ogólne metryki przebiegu: czas podkroków `Fn.k` z checkpointów, liczby rund i porażek; podkrok bez danych jest oznaczony jawnie (sekcja „Metryki przebiegu”) |
| `skillsync approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> [--sha <git sha>] [--target X] [--branch Y] [--file docs/…] --source <cli\|ide> [--json]` | zapisuje zgodę użytkownika (patrz niżej; `dashboard` ustawia tylko serwer) |

Kody wyjścia (jak pozostałe komendy workflow): `0` = PASS, `2` = BLOCKED (błąd danych wejściowych, odmowa zapisu, brak
projektu lub przebiegu). Raport komend zapisujących i `metrics --json` to JSON z polami `schema`, `status` (`PASS` albo
`BLOCKED`) i `reason` przy odmowie. `approvals wait` (odczyt zgód) kończy się `3` przy przekroczeniu czasu.

### `approvals record`

- Dopisuje jedną linię JSON do `.skillsync/runs/<run-id>/approvals.jsonl` pod blokadą plikową (jedyne źródło, które czyta
  `skillsync approvals wait`). Pola wpisu: `schema`, `at` (UTC), `word`, `sha`, `by`, `source` (`cli`, `dashboard` albo
  `ide`), opcjonalnie `file`, `run`, `plan`, `target`, `branch`, `ahead`, `mode`. Wpis z `record` ma zawsze `at`
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
- Blokada: plik `approvals.jsonl.lock` w katalogu przebiegu z unikalnym tokenem, tworzony wyłącznie (`O_EXCL`; działa
  na POSIX i Windows) i usuwany po zapisie tylko wtedy, gdy to nadal ta sama blokada (i-węzeł i token). Blokada starsza
  niż 60 s jest uznana za porzuconą i przejmowana atomowym `os.rename` pod unikalną nazwę: z dwóch czekających przejmuje
  ją jeden, a świeża blokada innego zapisu, złapana między sprawdzeniem wieku a przemianowaniem, wraca na miejsce
  (dowiązanie twarde, bez nadpisania). Kontrola duplikatu, dopisanie linii i
  `fsync` odbywają się pod blokadą; urwana ostatnia linia pliku nie łączy się z nowym wpisem.
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
(ścieżka speca lub null), `run` (id przebiegu lub null).

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

Format checkpointu: `- HH:MM <tekst>`; segmenty `w toku: F1.2, F1.3`, `zrobione: F1.1`, `czeka: <na co>`.

### `progress`

`total`, `done`, `percent`, `pending` (zrobione wg checkpointu, jeszcze nie zaznaczone w planie), `pendingPercent`,
`active` (id kroków w toku), `activeSource` (`checkpoint`, `first-unchecked` lub null), `current` (`{index, id, title}` lub
null), `parallel` (liczba kroków w toku ponad pierwszy), `reported` (id), `phases[]`.

`phases[]`: `name`, `label` (część przed `:`), `short`, `total`, `done`, `percent`, `pending`, `pendingPercent`, `state`
(`done`, `active`, `planned`), `steps[]`: `id`, `title`, `done`, `note`, `sha` (sha commita z notatki lub null), `index`,
`state` (`done`, `active`, `reported`, `planned`).

### `acts[]`

Trzy obiekty o `key` `spec` (I), `plan` (II), `real` (III). Wspólne: `key`, `roman`, `name`, `state` (`planned`, `active`,
`done`), `status` (status speca lub planu, null), `detail` (string lub null), `stages[]`.

Kafelek etapu: `label`, `done` (bool), `tip` (dymek), `key` (klucz opisu w `descriptions.json` skilla lub null).

Pola szczególne: `warning` (tylko akt I; ostrzeżenie o braku pre-implement przed „GO plan” przy sekcjach
Kontrakty/Migracja/Dane), `percent` (tylko akt III; = `progress.percent`).

### `waiting[]`

`what`, `source` (`checkpoint`, `plan`, `spec`, `prototype`), `since` (`HH:MM` lub null), `action` (podpowiedź dla
człowieka), `link` (`{kind: "doc", path}` albo `{kind: "prototype", slug}` lub null); opcjonalnie `word` (np. `GO plan`) i
`approve` (`spec`, `plan`, `cp`, `ready`), gdy czekanie odpowiada przyciskowi zgody.

Pytania blokujące (`blockingQuestions`) generują czekanie tylko, gdy sesja zgłosi je w checkpoincie (`czeka: odpowiedź Q3`);
założenia nigdy nie są akcją „Odpowiedz na pytania”.

### `silence`

`{since, thresholdMin}` (czas ostatniego raportu i próg w minutach, 35), gdy plan jest `in_progress`, są raporty i sesja
nie czeka jawnie; w przeciwnym razie null. Minuty liczy konsument.

### `approvals`

`{enabled, actions[], last}`; `last` = ostatni wpis `approvals.jsonl` lub null; `enabled` = false (z `--no-approvals`) daje
`actions: []`.
Opcjonalne pola tylko z serwera (`/api/status`, nie w `show`): `here` (bool, host zgód) i `viewer` (bool, host wyłącznie
do odczytu z `--read-host`); opis w sekcji „Serwer”.

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

`{slug: string|null, port: int, up: bool}`; `up` bez serwera (`show`) = false.

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
- `runs[]`: tylko przebiegi z otwartymi pytaniami, w kolejności `slug`: `{slug, open, blocking, url}`; `url` to ścieżka
  strony przebiegu na serwerze prototypów (`/_experts/<slug>`), host dokłada konsument.
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

Agent pisze segment PRZED długą operacją (spec, prototyp i jego iteracje, plan, krok implementacji) i nowym checkpointem
aktualizuje go po przekroczeniu (bez zatrzymywania pracy); czas bierze z `date`, nie z szacunku zegara.

- `{operation, until, untilIso, since}`: `operation` to tekst przed ` do `, `until` to `HH:MM`, `since` to czas checkpointu,
  `untilIso` to pełna data z godziną rozwiązana jak w `timing` (start przebiegu `RUN=` plus przejścia przez północ;
  szacunek o ponad 12 h wcześniejszy niż checkpoint liczy się jako następny dzień); bez startu przebiegu `untilIso` jest null.
- `eta` jest null, gdy najnowszy checkpoint nie ma segmentu `eta:` (albo ma `eta: -`, nieczytelną godzinę, pustą operację),
  ma `czeka:` (sesja czeka na człowieka, nie pracuje) albo plan jest `done`/`implemented`. Nowszy checkpoint zastępuje
  wcześniejsze szacunki.
- Serwer nie czyta zegara: „przekroczono” liczy konsument z `untilIso`. Renderer pokazuje toast „<operacja>, koniec ok.
  HH:MM”, a po przekroczeniu „<operacja>: przekroczono ETA o N min” (stan, nie wynik akcji: bez timera i bez „×”; widz widzi
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
bez czasu (także zaplanowanego).

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

`skillsync dashboard serve <projekt> [--port 5140] [--host 127.0.0.1] [--approve-host H ...] [--read-host H ...] [--no-approvals] [--plan <rel>]
[--session-url https://…] [--layer id=ścieżka ...]` (moduł `skillsync_workflow/dashboard_http.py`, ładowany leniwie) działa
na pierwszym planie; Ctrl+C albo SIGTERM kończy go raportem `{"status": "PASS", "url", "stopped": true}` (kod `0`). Odmowa
startu = `BLOCKED` (kod `2`): port spoza 5100–5199 albo zakazany (4444–4448, 9222, 9333), brak manifestu, brak
`skills/dashboard/assets/index.html` w aktywnych warstwach, `--session-url` inny niż https bez danych logowania, `--plan`
spoza dozwolonych dokumentów, port zajęty. Port wybiera się per projekt (domyślnie 5140).

Endpointy:

| Endpoint | Odpowiedź |
|---|---|
| `GET /`, `/index.html` | strona (`assets/index.html` skilla `dashboard`) |
| `GET /assets/<plik>` | tylko nazwy `[\w-]+.(html\|css\|js\|svg)` z `skills/dashboard/assets/` aktywnych warstw (`LayerSet.skill_files`: późniejsza warstwa wygrywa per plik, dowiązania pomijane); inne = 404 |
| `GET /api/status[?plan=<rel>]` | kontrakt `schema: 1` (to samo co `show`, z tym samym maskowaniem sekretów, z żywym `version`, `session.url`, `prototype.up`) plus `approvals.here` (bool: nagłówek `Host` żądania jest na liście hostów zgód, czyli loopback albo `--approve-host`; `false` dla hosta z `--read-host`) i `approvals.viewer` (bool: host wyłącznie do odczytu z `--read-host`; wtedy `here` = `false`) |
| `GET /api/doc?path=<rel>` | `{path, title, status, mtime, size, truncated, html}`: podgląd `*.md` z `docs/specs`, `docs/plans`, `docs/design`, `docs/domain` i `.skillsync/runs/<run>/`; bez `..`, ścieżek absolutnych, ukrytych katalogów i dowiązań poza listę; limit 2 MB (`truncated`); inne = 404; to celowy podgląd surowy: treść nie przechodzi przez `redact` (inaczej niż `/api/status`) |
| `GET /events` | SSE: `hello {version, heartbeat}` (z `retry: 3000`), `change {version}` po zmianie obserwowanych plików, `ping {version, t}` co 15 s; najwyżej 64 połączenia (dalej 503); `HEAD /events` = same nagłówki (200, `text/event-stream`), bez strumienia i bez zajmowania miejsca |
| `GET /api/descriptions` | `descriptions.json` skilla (`{klucz: {title, text}}`) |
| `GET /healthz` | `{ok: true, version}` |
| `POST /api/approve` | `{kind: spec\|plan\|cp\|ready, path, sha[, plan]}`; tylko akcja, którą `/api/status` właśnie oferuje w stanie `waiting`, z tym samym `sha`; odpowiedź 200: `{ok, word, file, next}`, dla GO także `trail` (ścieżka `approvals.jsonl` przebiegu albo null bez przebiegu) |

Kontrola `Host` na wszystkich endpointach (`GET`, `HEAD`, SSE `/events`, `POST`): nagłówek `Host` musi wskazywać loopback
(`localhost`, `127.0.0.1`, `[::1]`, z dowolnym portem; bez rozróżniania wielkości liter), nazwę z `--approve-host` albo
nazwę z `--read-host` (obie listy: port pomijany, bez rozróżniania wielkości liter, flagi powtarzalne). Inny albo
brakujący `Host` (DNS rebinding, tunel lub nazwa LAN spoza list) = 403
`{"error": "Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host)."}` bez treści strony,
dokumentu ani statusu; przy `--verbose` odmowa trafia do logu stderr (metoda, ścieżka, host).

| Klasa `Host` | Odczyt (`GET`, `HEAD`, `/events`, `/api/*`, `/healthz`, assety) | Zapis (`POST /api/approve`) | `approvals.here` / `approvals.viewer` |
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
Przy nieufnych widzach użyj `--no-approvals`. Model dwóch instancji dla nieufnych widzów: prowadzący na loopbacku ze zgodami, widzowie na osobnej instancji `skillsync dashboard serve <projekt> --port <inny port> --no-approvals --read-host <host>` (bez zapisu, ten sam projekt; wspólny jest tylko `approvals.jsonl`). Widz z odczytem widzi też podgląd dokumentów `/api/doc` bez maskowania
sekretów (surowy podgląd). Wartości `--read-host` i `--approve-host` to same nazwy hostów: z portem, nawiasami, `/` albo
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
application/json`. Kontrola `Origin` porównuje tylko nazwę hosta (`localhost`, `127.0.0.1`, `[::1]` albo nazwę z
`--approve-host`; nazwa z `--read-host` to obcy `Origin`); schematu i portu nie porównuje, więc inna strona na loopbacku (inny port) przechodzi ten krok. Przed
obcą stroną w przeglądarce chroni przede wszystkim własny nagłówek `X-Status-Board` i `Content-Type: application/json`:
oba wymuszają preflight CORS, na który serwer nie odpowiada; brak `Origin` (klient spoza przeglądarki) jest dopuszczony.
Dalej `Content-Length` 1–4096 (inaczej 400), treść JSON-obiekt (inaczej 400), `--no-approvals` = 403, akcja
nieoferowana, nieaktywna albo z innym `sha` = 409 (także drugi zapis CP/READY dla tego samego HEAD). Każda odpowiedź ma
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache,
no-store`. Połączenie bez kompletnego żądania jest zamykane po 30 s (timeout gniazda obsługi). Strona HTML ma także CSP
`default-src 'self'; script-src 'self'; …; frame-ancestors 'none'`. Przy `--host 0.0.0.0` inne urządzenia dostają 403,
dopóki ich adres z paska przeglądarki nie trafi do `--approve-host` albo `--read-host` (serwer wypisuje to przy starcie).
