# Kontrakt JSON dashboardu (`schema: 1`)

Dashboard to strona statusu projektu: ten sam stan, który widzi człowiek w przeglądarce, jest dostępny jako JSON.
Kontrakt jest wspólny dla renderera strony, innych konsumentów (np. IDE) i testów. Przykłady wejścia i oczekiwanego
wyniku: `tests/fixtures/dashboard/<przypadek>/input/` i `expected.json`.

## Komendy

| Komenda | Zachowanie |
|---|---|
| `skillsync dashboard show <projekt> [--json] [--plan <rel>]` | drukuje kontrakt `schema: 1` (JSON; bez `--json` ten sam JSON, formatowany); tylko odczyt, bez zapisu do projektu; `--plan` wybiera plan (ścieżka względna, np. `docs/plans/<plik>.md`), bez niego plan wybierany jest automatycznie (`planAuto: true`) |
| `skillsync dashboard serve <projekt> [--port N] [--host H] [--approve-host H]` | serwer HTTP nad tym samym JSON (`/api/status`, strumień zmian `/events`, przyciski zgód); port wybiera się per projekt, by strony nie kolidowały |
| `skillsync dashboard metrics <projekt> <run-id> [--json]` | ogólne metryki przebiegu: czas podkroków `Fn.k` z checkpointów, liczby rund i porażek; podkrok bez danych jest oznaczony jawnie |
| `skillsync approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> [--sha <git sha>] [--target X] [--branch Y] --source <cli\|dashboard>` | zapisuje zgodę użytkownika (patrz niżej) |

Kody wyjścia (jak pozostałe komendy workflow): `0` = PASS, `2` = BLOCKED (błąd danych wejściowych, odmowa zapisu, brak
projektu lub przebiegu). Raport komend zapisujących i `metrics --json` to JSON z polami `schema`, `status` (`PASS` albo
`BLOCKED`) i `reason` przy odmowie. `approvals wait` (odczyt zgód) kończy się `3` przy przekroczeniu czasu.

### `approvals record`

- Dopisuje jedną linię JSON do `.skillsync/runs/<run-id>/approvals.jsonl` pod blokadą plikową (jedyne źródło, które czyta
  `skillsync approvals wait`). Pola wpisu: `schema`, `at` (UTC), `word`, `sha`, `by`, `source` (`cli` albo `dashboard`),
  opcjonalnie `file`, `run`, `plan`, `target`, `branch`, `ahead`, `mode`.
- `--sha` domyślnie HEAD repozytorium projektu; dla `CP`/`READY` sha oznacza HEAD gałęzi roboczej, którą zgoda wypchnie.
- Odmawia (kod `2`) drugiego zapisu tego samego słowa dla tego samego HEAD i run-id; nieznane słowo, zły `--source` albo
  brak katalogu przebiegu też kończą się `BLOCKED`.
- Zgodę wyraża wyłącznie użytkownik (wpisuje komendę sam albo naciska przycisk strony, którą woła serwer). Agent tej komendy
  nie wywołuje: wiadomość agenta nie jest zgodą.

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
| `planAuto` | bool | `true`, gdy plan wybrano automatycznie (brak `--plan` albo nieznana ścieżka) |
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
| `substeps` | tablica | NOWE: podkroki `Fn.k` z czasem |

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
| `branchPushed` | bool lub null | czy `origin/<gałąź>` istnieje |

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

Wpisy zgód (`given`, `last`) pochodzą z `approvals.jsonl` bieżącego przebiegu w kolejności pliku, w postaci zapisanej; linie
spoza reguł `skillsync status` (nieznane słowo, nieczytelne `at`, sha spoza 7–64 znaków hex, plik ponad 256 KiB) są pomijane.

### `delivery`

`mode` (`pr`, `direct`, `invalid`), `base` (string lub null), `source` (`manifest`, `default`), `branch`, `target` (dokąd
oddaje „CP”; w `direct` z linii planu `Oddanie:`), `remote`, `pushed` (bool lub null), `ahead` (int lub null; commity
niewypchnięte), `words[]`: `{word: "CP"|"READY", active, tip, target}` (`READY` tylko w trybie `pr`; przy `invalid` pusta
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
- Linie ze znacznikiem innym niż `[ZAŁOŻENIE]` (np. `[ROZSTRZYGNIĘTE …]`) i linie spoza sekcji są pomijane.
- Założenia nie są akcją „Odpowiedz na pytania”; tej akcji (`waiting`) dotyczą tylko pytania blokujące zgłoszone przez sesję.
- Separator przed `przyjęte:` może być półpauzą, pauzą, `--` albo `-` (otoczonymi spacjami); znacznik bez rozróżnienia
  wielkości liter, także bez polskich znaków (`[ZALOZENIE]`). Założenie bez `przyjęte:` ma `accepted` = `""`.
- Wiersz tabeli `| Q<n> | <pytanie> | … |` (jak w szkielecie speca) jest pytaniem blokującym; `question` = pierwsza niepusta
  komórka po id. Id pogrubione (`**Q4**`) jest rozpoznawane; powtórzone id liczy się raz (pierwsze wystąpienie); bloki kodu
  są pomijane.
- Bez speca: `{count: 0, items: []}`.

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
