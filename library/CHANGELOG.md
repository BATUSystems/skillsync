# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), wersje: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Added
- `skills/prototype/SKILL.md`: sekcja „Zapis ekranów: marker »agent pisze«”: `prototype writing <projekt> <slug> start` przed edycją ekranów (po `iterate`), odnowienie przy większych partiach albo `--ttl 1800`, `done` po `ids --check` i sprawdzeniu slugu w galerii, dopiero potem „prototyp gotowy”; obsługa `lock_timeout` i `needs_migration`; marker to sygnał koordynacji z wygasaniem; agent nadal nie uruchamia `accept`.
- `skills/skillsync/SKILL.md`: komenda `prototype writing`, kody BLOCKED (`needs_migration`, `lock_timeout`, `invalid_args`), `writing_in_progress` przy `accept`, pole `writing` w `list`.

### Changed
- `skills/prototype/references/decisions.md` i `SKILL.md` (procedura po rundzie): po `iterate` marker `writing start`, po zmianach HTML `ids --check`, sprawdzenie slugu i `writing done`.
- `skills/prototype/SKILL.md`: `prototype wait` nie zgłasza `accepted` przy ważnym markerze; `gate` ma powód `writing`.
- `skills/feature/references/plan-details.md`: lista powodów FAIL bramki o `writing`.
- `skills/prototype/assets/repo/prototype.js`: baner „Agent jeszcze pisze ekrany”, wyłączone przyciski akceptacji i przywracania, powrót po zniknięciu markera bez przeładowania; istniejące prototypy dostają silnik po `skillsync prototype migrate`.
- `skills/prototype/assets/repo/.gitignore`: linia `.writing.json`.

## [1.2.1] - 2026-11-12

### Changed
- `skills/skillsync`: pytania do eksperta są w `<root>/<slug>/questions.json` (osobny plik per przebieg), strony `/_experts` i `/_experts/<slug>`; `prototype question` z `--slug` (przy `add` bez niego przebieg wybierany ze speca z `--origin`), nowe `question migrate` i `question purge`, wynik `add` z `created` i `notes`, `add --multi` (pytanie wielokrotnego wyboru).
- `layer.yaml`: `version` i `core_min` 1.2.1 (warstwa opisuje komendy rdzenia 1.2.1).

## [1.2.0] - 2026-11-12

### Added
- `skills/feature/references/spec.md`: klasa wiersza `[EKSPERT]` (cykl: czekające `(ekspert: qn-…)`, odpowiedź z autorem i czasem, powrót po `withdraw`), opt-in programisty w akcie I, opcja „Nie wiem, zapytaj eksperta” w zwykłym pytaniu, wykrycie zdolności przez `question url` (BLOCKED = pytanie zwykłe).
- `skills/skillsync`: komendy `prototype question add|list|show|export|record|withdraw|url|wait|merge` i `prototype user roles`, plik `questions.json`, strona `/_experts`.

### Changed
- `skills/prototype` (`SKILL.md`, `references/decisions.md`): komentarz użytkownika z rolą `expert` jest głosem doradczym; `authorRoles`/`whoRoles` obok `authorRole`/`whoRole`.
- `skills/feature/references/spec.md`: reguła usuwania blokujących `Q` po odpowiedzi ma wyjątek dla wierszy `(ekspert: qn-…)`.
- `layer.yaml`: `version` i `core_min` 1.2.0 (warstwa wymaga rdzenia z komendami `question`). Warstwa `core` jest wydawana razem z rdzeniem, więc podniesienie jej `core_min` do wersji tego samego wydania nie jest zmianą MAJOR warstwy.

## [1.1.2] - 2026-11-12

### Changed
- `skills/dashboard` (`assets/dashboard-render.js`, `assets/app.js`, `SKILL.md`): pozycja „sesja czeka na Ciebie” i tytuł karty znikają po zapisaniu zgody, a komunikat o wyniku zgody gaśnie sam (10 s, błąd 30 s, odliczanie stoi przy ukrytej karcie).
- `skills/feature` (`SKILL.md`, `references/ceremony-demo.md`, `plan-details.md`, `retro-quality.md`, `review-loop.md`) i `skills/prototype/SKILL.md`: postępowanie przy `BLOCKED tty_required` przy akceptacji prototypu (poproś osobę o kliknięcie Akceptuj na stronie, czekaj dalej); w ceremonii `demo` przegląd `kod` ma najwyżej 2 rundy; cięcie zakresu dopiero po przekroczeniu progu planu albo za zgodą prowadzącego.
- `skills/agent-runtime-pitfalls`: cztery nowe pułapki (`npm ci` z niewłaściwą wersją npm, potok gubiący kod wyjścia runnera testów, zegar sesji odbiegający od maszyny, `=====` w zsh jako argument i odwrotne apostrofy w `echo`).

### Fixed
- `skills/prototype` (`assets/repo/prototype.js`, `prototype.css`, `references/screen-patterns.md`): nawigacja ekranów zawsze ma odnośnik „domek” do listy prototypów, także w prostym pasku (`data-proto-nav="strip"`); pasek działa tylko do 5 ekranów, przy większej liczbie atrybut jest ignorowany i powstaje rozwijany przełącznik z wyszukiwarką (nowy eksport silnika `screenNavMode`). Istniejące prototypy dostają nowy silnik przez `skillsync prototype migrate` albo `sync`.

## [1.1.1] - 2026-11-12

### Added
- `skills/dispatch/references/codex.md`: sekcja o protokole BRIDGE/1 (koperta, ACK, rejestr, skrzynki, granica zaufania).
- `skills/agent-runtime-pitfalls`: wpisy o jawnej liście ścieżek do `git add` i o zrzutach przeglądarki w osobnym profilu bez rozszerzeń.

### Changed
- `skills/dashboard` (`assets/dashboard-render.js`, `assets/app.js`, `assets/app.css`, `assets/index.html`, `SKILL.md`): widok strony statusu to wspólny renderer
  JSON → DOM (ten sam plik ładuje panel hosta), `app.js` to adapter strony; układ zależy od szerokości kontenera (`.dash-wrap`, progi 1100/640/500/420 px, tryb zwarty), a opis w `SKILL.md`
  zawiera kontrakt renderera i obowiązek hosta (wrapper z określoną szerokością).

### Fixed
- `skills/feature/references/ceremony-demo.md`: sekcja „Po specu i review: następny krok to prototyp” (ceremonia `demo` nie czeka biernie na „GO plan”).

## [1.1.0] - 2026-11-11

### Added
- `skills/feature/references/ceremony-demo.md`: ceremonia `demo` (klucz manifestu `workflow.ceremony`), `schema.json` z kluczem `workflow.ceremony` (`full` | `demo`).
- `skills/dashboard`: strona statusu projektu (plan, przebieg, zgody, metryki podkroków) nad kontraktem JSON `schema: 1`; komendy `skillsync dashboard show|serve|metrics` i `skillsync approvals record`. Wpis `dashboard: {scope: workflow}` w `workflow/skill-catalog.yaml`; `skills/skillsync` opisuje nowe komendy.
- `skills/dashboard` (`assets/app.js`, `assets/app.css`, `descriptions.json`): sekcja „Pytania” w kafelku Aktu I (pytania blokujące i założenia do korekty przy prototypie, „brak” przy pustej liście, widoczna dla każdego widza), kafelek „Readiness” w Akcie II (PASS / BLOCKED / brak danych, data, skrót Plan-sha, znacznik nieaktualnego briefu, link do `readiness.md` w podglądzie) i rozwijana lista podkroków `Fn.k` z czasem w Akcie III; nowe klucze dymków `stage_readiness`, `questions_blocking`, `questions_assumptions`, `substeps_time`.

### Changed
- `skills/feature` (`SKILL.md`, `spec`, `plan-details`, `plan-large`, `review-loop`, `measurement`): klasy pytań `Q` (`[ZAŁOŻENIE]`), „GO plan” = jeden lokalny commit (spec, plan-szkic, prototyp), znacznik `[UI]` i bramka prototypu per faza, kontrakt najpierw, test-writer równolegle z review R1, przy ≥ 5 zdolnościach dwa plany, szacunek 75–105 min na zdolność, raport „jedna linia na podkrok”.
- `instructions/_base.md`: pierwszy commit przy „GO plan”, jawny wyjątek `ceremony: demo` w regule „Done”.
- `skills/skillsync`: `workflow.ceremony`, `prototype gate --phase`, pola `readiness` dla faz `[UI]`.
- Alias warunkowy: `status-board` w `skills:` rozwija się do `dashboard` tylko wtedy, gdy żadna warstwa nie dostarcza skilla `status-board`.
- Skill `skillsync`: tabela komend opisuje pole `preImplement` w `status`, `warnings[]` w `plan approve` i flagę `--run-id` w `readiness` (zapis `readiness.md`).

### Fixed
- `skills/prototype` (`assets/repo/components.css`, `references/screen-patterns.md`): lista rozwijana otwarta w oknie lub szufladzie nie jest już obcinana przez `overflow` ciała okna ani chowana pod stopką — klasa `popover-float` (rodzeństwo okna) i wzorzec z regułą „okno mieści się bez paska przewijania”.

## [1.0.2] - 2026-11-11

### Removed
- `skills/go-jira` (przestarzały); wpis `go-jira` w manifeście rozwija się do `acli`. Usunięty z `workflow/skill-catalog.yaml`.

### Changed
- `skills/acli`: sekcja „Pola własne” (tworzenie: `create --from-json` z `additionalAttributes`; edycja istniejącego zgłoszenia: REST API na jawne polecenie, token nigdy w argumencie polecenia ani w logu), bez odesłań do go-jira; `skills/skillsync` bez `go-jira` w przykładzie.
- `skills/dispatch` (Krok 0, `references/claude.md`): bramka modelu dyspozytora to Opus 5.5 lub Fable 5.1 (najmocniejszy model rodziny 5); podział modeli kart ustala użytkownik.
- `skills/feature/references/ticket-intake.md`: nowy punkt 9 „Pytania w trakcie przebiegu” — przed pytaniem produktowym odczytaj ponownie zgłoszenie i komentarze, niepotwierdzone przesłanki oznacz jako założenie (retro, P1).
- `skills/feature/references/plan-large.md`: liczba rund review wynika z kategorii pętli w `review-loop.md` (pętla pełna tylko dla strażnika wycieków, migracji i bezpieczeństwa) (retro, P2).
- `skills/agent-runtime-pitfalls`: nie uruchamiaj skryptu pomocniczego po samej nazwie; wynik „0 testów” to brak dowodu (retro, P3).

## [1.0.1] - 2026-11-09

### Changed
- `skills/prototype` (silnik prototypów: `assets/repo/prototype.js` i `prototype.css`): fragment adresu ramki widoku to `#view-sN` zamiast `#sN`;
  ładowanie ramki nie przewija strony nadrzędnej (V-23, N-R1), a dokładne dopasowanie fragmentu ma pierwszeństwo.
- `skills/agent-runtime-pitfalls`: mutant po zawieszonym `cp -i` zostaje na dysku (sprawdź `diff -q`/`git diff` przed dalszą pracą);
  na Windows `write_text` zamienia LF na CRLF i psuje skróty liczone w testach, a ścieżki w JSON idą przez `as_posix()`.

## [1.0.0] - 2026-11-08

### Changed
- Tryb dostarczania (`delivery.mode`): `instructions/_base.md`, `feature`, `pr-run`, `worker-mode`, `multi-session`, `dispatch`
  i `workflow/references/release.md` opisują `pr` (domyślny) i `direct`, słowa zgody `CP`, `CPD`, `READY` (z wariantami `!`),
  commity po każdym sprawdzonym kroku planu i zakaz merge/complete PR. `skills/skillsync/SKILL.md` opisuje klucz `delivery` i pole `route`.

## [0.9.0]

### Added
- Pierwsze wydanie warstwy core po podziale biblioteki na warstwy (core, org, personal).
- Lekcje rdzenia w `workflow/lessons/`: uogólniona wiedza o testowaniu (zawężone biegi, Playwright i
  `TestServer`, Angular, kontrakty API, coverage wycinka, django-tenants, izolacja zasobów).
