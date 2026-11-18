# Plan: chip katalogu na stronie statusu i wybór katalogu roboczego przy „GO plan” (CORE-WORKDIR)

Status: done
Spec: docs/specs/2026-11-18-core-workdir.md
Spec-sha: f2a1f2b05e752260956c772076b8aed5eee1d143
Przebieg: 2026-11-18-core-workdir
Katalog: worktree CORE-WORKDIR

## Streszczenie dla człowieka

**Zgody.** „GO plan” i „GO implement” od użytkownika przekazane przez Dyspozytora (delegacja z 16.11; cytaty w sesji, 18.11 ok. 14:50 i 15:05). Push `feat/core-workdir` tylko po PUSH SLOT od Dyspozytora, bez draft PR (integracja przez `release/1.10.0`).
**Co zmieniamy.** (1) Strona statusu dostaje w nagłówku chip „worktree: <nazwa>” albo „główny checkout” (pole `project.worktree` w `dashboard show`, pełna ścieżka w opisie, stopka bez zmian). (2) Przy „GO plan” sesja pyta: „własny worktree” (domyślnie) czy „główny checkout” (praca na bieżącej gałęzi, także bazowej); wybór zapisuje linia planu `Katalog:`, wznowienie ją czyta. Reguła „od GO plan osobny worktree” zmienia się w rdzeniu instrukcji i w skillu `feature` oraz w pochodnych. Wersja 1.10.0.
**Czego nie robimy.** Kopiowania ścieżki kliknięciem (decyzja: bez). Zmian Skippstera (zgłaszamy skutek). Odczytu `Katalog:` przez `status`/`dashboard`. Pytania w kartach pod Dyspozytorem (decyduje `ASSIGN`). Wydania, tagu i wersji ostatecznej.
**Ryzyka.** Praca na gałęzi bazowej w `główny` (commity i `CP` na bazę, w `pr` odmowa); druga sesja w tym samym katalogu; budżet `instructions/_base.md` (zapas 5 B); rozjazd reguły w ok. 16 plikach.
**Decyzje do podjęcia.** Wersja 1.10.0 (rekomendacja: MINOR, zlecenie Dyspozytora; ostatecznie właściciel); bez prototypu chipu, dowodem wzrokowym są zrzuty obu motywów i wąskiej kolumny z podglądu; faza F2 bez znacznika `[UI]` (rekomendacja, patrz „Otwarte pytania”).

## Cel

Wdrożyć w rdzeniu 1.10.0 zmiany opisane w [specu](../specs/2026-11-18-core-workdir.md): pole `project.worktree` i chip w nagłówku strony statusu (A1–A3) oraz pytanie o katalog roboczy przy „GO plan” z linią `Katalog:` (B1–B2).
Rdzeń jest publiczny: bez nazw i ścieżek klienta, ludzi i maszyn. Bez stopek i trailerów narzędzi AI w commitach. Wejście: `origin/main` = `2594fed` (release 1.9.1); gałąź `feat/core-workdir` w worktree `CORE-WORKDIR`.

## Kryteria akceptacji (mapowanie)

| Kryterium speca | Kroki | Dowód |
|---|---|---|
| K1–K3 (kontrakt, rozpoznanie bez gita, `path`/`mainPath`) | F1 | testy `tests/test_skillsync_dashboard_contract.py` i nowy `test_skillsync_dashboard_workdir.py` (katalog, plik `gitdir`, `commondir`, submoduł, uszkodzony worktree, dowiązanie) PASS; K2: test z podmienionym `subprocess`/`os.system` i pustym `PATH` (rozpoznanie nie wywołuje procesu); test, że `read_git` daje ten sam wynik co przed zmianą |
| K7 (fixtures) | F1 | `expected.json` wszystkich przypadków (`null`; `cp-ready` = `main`), przypadek `worktree` budowany w teście; test kontraktu fixtures PASS; normalizacja w `docs/dashboard-contract.md` |
| K4, K5, K6 (chip, opis, brak schowka) | F2 | `test_skillsync_dashboard_renderer.py` (chip dla `main`/`worktree`/`null`/brak pola, kolejność: po chipie gałęzi, przed „N niewypchniętych”, obcięcie nazwy, `data-desc-extra`), `…_a11y.py`, `…_theme_tokens.py`, `test_no_copy_buttons_anywhere` bez zmian PASS; zrzuty obu motywów i wąskiej kolumny (brak poziomego przewijania) z podglądu (port 5162); nowe testy najpierw czerwone (red-first), testy behawioralne rendererera wymagają `node` ≥ 18, a `check_skips` na logu nie dopuszcza skipów |
| K8, K9 (pytanie, `Katalog:`, warunki `główny`) | F3 | testy treści: `test_skillsync_feature_demo.py` (k10-* zaktualizowane) i nowy `test_skillsync_workdir_rule.py`: `Katalog:` (sam napis) w `_base.md`, a oba warianty (`worktree <nazwa>`, `główny`) w `workdir.md`, `plan-details.md`, `SKILL.md`; warunki K9 (bieżąca gałąź, baza w `pr` = odmowa, odłączony HEAD = STOP, brudne drzewo = pytanie z listą), Q12 (`direct` na bazie bez `Oddanie:`), żywa dzierżawa w `.skillsync/claims` = STOP, zniknięcie worktree z linii planu = STOP, brak odpowiedzi = brak GO plan (każda ścieżka ma asercję na dosłowną frazę w `workdir.md`) PASS |
| K10 (spójność reguł, budżet) | F3 | `wc -c library/instructions/_base.md` ≤ 8192 i netto ≤ 0 względem 8187 (przed i po w „Wyniku”), `tests/test_skillsync_attribution.py::RuleTests::test_base_fits_the_budget`, `test_skillsync_library_smoke.py`, `test_skillsync_delivery_mode.py` PASS |
| K11 (dokumentacja; wersje bez zmian, wydanie robi Dyspozytor) | F1 (sekcja kontraktu), F2 (`dashboard/SKILL.md`), F4 | grep dosłownych nazw; CHANGELOG-i (`[Unreleased]`), `upgrading.md`; `python3 scripts/check_release.py` rc=0 (wersje nietknięte), `test_skillsync_domain_size.py`, `test_skillsync_changelog_breaking.py` PASS; `leakcheck --target core --require full` PASS |
| K12 | F5 | pętla `kod` końcowa PASS, pełny bieg po LEASE, `leakcheck` i gitleaks osobno |

## Zakres dodany po „GO implement” (18.11, za zgodą użytkownika przekazaną przez Dyspozytora)

Błąd strony statusu: rundy review liczone podwójnie (prawdziwy przebieg: 1 runda speca = „Review ×2”, 5 rund kodu = „Review kodu ×10”). `compute_acts` (`skillsync_workflow/dashboard.py`, `spec-review`/`plan-review`/`code-review` jako podciąg nazwy pliku) liczy też `<krok>-N-decyzje.md` (wymagany przez `review-loop.md`, „Zapis”) i inne pliki obok raportu (m.in. dowody checkpointu `.log`). Reguła docelowa: rundą jest tylko zamrożony raport `<krok>-N.md`; `*-decyzje.md` i pliki o innym rozszerzeniu się nie liczą. Krok **F1.5** w fazie serwera: poprawka, test regresji na fixturze z raportem i plikiem decyzji dla spec, planu i kodu, opis reguły w `docs/dashboard-contract.md`, wpis CHANGELOG `[Unreleased]` → Fixed (F4). To zakres spoza zatwierdzonego speca (spec zamrożony po wdrożeniu): odnotowany tu i w „Wyniku”.

## Terminologia domenowa

Nowe terminy (źródło: spec, K1 i K9, decyzje z 18.11) trafiają do NOWEGO rekordu `docs/domain/workdir.md` (`load: on-demand`, wpis w `docs/domain/index.yaml`), nie do glosariusza: `glossary.md` jest `load: bootstrap` i ma 11662 B przy limicie 12 KB z marginesem 5% (zapas ok. 12 B), a `test_skillsync_domain_size.py` go pilnuje.
Definicje zgodne z K1: **powiązany worktree** (`kind: worktree`): `.git` jest plikiem `gitdir:` do katalogu z `commondir`; **główny checkout** (`kind: main`): `.git` jest katalogiem albo plikiem `gitdir:` do katalogu bez `commondir` (też submoduł, `--separate-git-dir`); **katalog roboczy przebiegu**: wartość linii planu `Katalog:` (`worktree <nazwa>` albo `główny`), wybierana przy „GO plan”. Każda definicja ze źródłem (spec, kod `read_git`) i datą 2026-11-18. Bez kandydatów do wyjaśnienia.

## Kroki

Kolejność: F0, F1, F2, F3, F4, F5. F1 (serwer) i F2 (renderer) współdzielą kontrakt i fixtures, więc idą szeregowo (F2 po commicie F1). F3 (biblioteka i dokumenty reguły) jest rozłączna plikami z F1 i F2, ale jej testy treści i `library_smoke` czytają całą bibliotekę, więc szczebel testów F3 uruchamia orkiestrator po zakończeniu pisania F2; pisanie F3 może iść równolegle z F2 (dwóch piszących, rozłączne pliki).
F4 (scribe) startuje po commitach F1–F3. Kategorie pętli `kod`: F1 jedna runda (pole addytywne, odczyt bez gita; bez strażnika wycieków, migracji i danych wrażliwych; „odczyt poza projektem” z `review-loop.md` nie zachodzi ponad stan dzisiejszy: `read_git` już czyta katalog wskazany przez `gitdir:`/`commondir`, a nowy kod wystawia tylko `mainPath`), F2 jedna runda, F3 pętla pełna (reguła zgód i commitów w rdzeniu instrukcji: granica uprawnień), F5 końcowa jedna runda plus verifier.
Faza F2 nie ma znacznika `[UI]` (spec w „Planie wykonania” oznacza ją jako `[UI]`: odstępstwo świadome, do zapisu w „Wyniku”): to jeden chip w istniejącym nagłówku, bez nowego ekranu; prototypu nie zamawiano, a dowodem wzrokowym są zrzuty obu motywów i wąskiej kolumny z podglądu (zob. „Otwarte pytania”).
Instrukcje, skille i lekcje do odczytu przed pracą: `route` rdzenia (lekcje `budzet-instrukcji-bazowej-nowa-regula-wymusza-ciecie`, `zmiana-head-w-trakcie-biegu-uniewaznia-bieg`, `pelny-bieg-testow-a-git-na-prawdziwych-warstwach`), `feature/references/{plan-details,plan-large,review-loop,tests-baseline,worker-mode,pr-after-approval}.md`, `docs/dashboard-contract.md`; `ticket-intake.md` (l.41 „gałąź bazową i pierwszą roboczą”) do sprawdzenia: wniosek „bez zmian” albo poprawka w F3.

| Id | Krok | Rola | Exec | Ryzyko kroku | Pliki |
|---|---|---|---|---|---|
| F0 | Baseline: moduły punktowe przed zmianą (dashboard contract/renderer/a11y/theme_tokens, feature_demo, delivery_mode, library_smoke, leakguard), `wc -c library/instructions/_base.md`; zapis liczb w „Wyniku” | test-runner | dispatch:cheap | low | (brak zapisu) |
| F1 | Serwer: wspólny pomocnik `gitdir`/`commondir` w `skillsync_workflow/dashboard.py` (z `read_git`, bez zmiany jego wyniku), `project.worktree` w `build_status`, testy rozpoznania (nowy plik), fixtures (`null`, `cp-ready` = `main`, przypadek `worktree` budowany w teście), sekcja i normalizacja w `docs/dashboard-contract.md` | implementer | dispatch:standard | medium | `skillsync_workflow/dashboard.py`, `tests/test_skillsync_dashboard_workdir.py`, `tests/test_skillsync_dashboard_contract.py`, `tests/fixtures/dashboard/**`, `docs/dashboard-contract.md` |
| F2 | Renderer: chip (`span.m` z `i-folder` i wewnętrznym `span` nazwy), `desc()`/`data-desc-extra`, CSS (wielokropek na wewnętrznym `span`), `descriptions.json` (`workdir_chip`), opis w `library/skills/dashboard/SKILL.md`, testy renderera i dostępności, zrzuty obu motywów (podgląd port 5162, izolowany XDG, PID) | implementer | dispatch:standard | medium | `library/skills/dashboard/assets/dashboard-render.js`, `app.css`, `library/skills/dashboard/descriptions.json`, `library/skills/dashboard/SKILL.md`, `tests/test_skillsync_dashboard_renderer.py`, `tests/test_skillsync_dashboard_a11y.py` |
| F3 | Procedura B: nowa referencja `feature/references/workdir.md` (pytanie, `Katalog:`, warunki K9, Q11–Q13, wznowienie, ścieżki porażek); `instructions/_base.md` tylko reguła i odesłanie (budżet: tabela niżej); `feature/SKILL.md`, `plan-large.md`, `plan-details.md` (`Katalog:`), `spec.md`, `pr-run/SKILL.md`, `pr-after-approval.md`, `retro-quality.md`, `worker-mode.md`, `ticket-intake.md` (sprawdzenie), `library/skills/skillsync/SKILL.md`, `docs/workflow.md`, `docs/workflow-details.md`; testy treści | implementer | group:procedura:capable | high | `library/instructions/_base.md`, `library/skills/feature/**`, `library/skills/pr-run/SKILL.md`, `library/skills/skillsync/SKILL.md`, `docs/workflow.md`, `docs/workflow-details.md`, `tests/test_skillsync_feature_demo.py`, `tests/test_skillsync_delivery_mode.py`, `tests/test_skillsync_workdir_rule.py` |
| F4 | Dokumentacja i wiedza (BEZ bumpu wersji, odstępstwo wg zlecenia Dyspozytora z 18.11): `CHANGELOG.md` i `library/CHANGELOG.md` tylko w `[Unreleased]` (addytywnie), `docs/upgrading.md`, rekord `docs/domain/workdir.md` + `index.yaml`; wpis Fixed o liczeniu rund review w `[Unreleased]`; wpis o `LISTEN_NOTE_UNTIL` NIE w CHANGELOG (opis w raporcie DONE, bump i wpis robi wydanie) | scribe | dispatch:cheap | low | `CHANGELOG.md`, `library/CHANGELOG.md`, `docs/upgrading.md`, `docs/domain/**` |
| F5 | Pętla końcowa `kod` (jedna runda + verifier), pełny bieg i macierz po LEASE, `leakcheck`, gitleaks, `scripts/check_release.py`, „Wynik”, retro-quality, `--final`, zamknięcie | orkiestrator | inline | medium | plan, spec, `.skillsync/runs/` |

## TESTY

- cwd: worktree `CORE-WORKDIR`; środowisko: `python3`, `node` ≥ 18 (testy behawioralne rendererera mają `skipUnless`; skip = NOT_RUN, `check_skips` na logu), bez sieci i usług współdzielonych. Moduły importują się nawzajem (lekcja `pelny-bieg-testow-a-git-na-prawdziwych-warstwach`), więc zestawy punktowe przez `python3 -m unittest discover -s tests -p '<wzorzec>'`, rc w zmiennej, log do pliku `<krok>-<próba>.log` w `.skillsync/runs/2026-11-18-core-workdir/`.
- Zestaw F1: `test_skillsync_dashboard_contract*`, `test_skillsync_dashboard_workdir*`, `test_skillsync_dashboard.py`, `…_server.py`, `…_forecast.py`, `…_metrics.py`, `…_stage_state.py` (kopiują fixtures). Zestaw F2: `…_renderer.py`, `…_a11y.py`, `…_theme_tokens.py`, `…_server.py` (opisy). Zestaw F3: `test_skillsync_feature_demo.py`, `…_delivery_mode.py`, `…_workdir_rule.py`, `…_attribution.py` (budżet), `…_library_smoke.py`, `…_leakguard.py`. Zestaw F4: `…_domain_size.py`, `…_changelog_breaking.py`, `…_dashboard_ports.py`, `…_dashboard_listen_defaults.py`, `python3 scripts/check_release.py`.
- Do dostosowania: k10-* w `test_skillsync_feature_demo.py` (l.86–96), `test_skillsync_delivery_mode.py` (l.273–281, fraza „Słowa zgody: `CP`” zostaje), fixtures `expected*.json`, ports i listen_defaults zależą od wersji przez `_version_tuple`: uruchomić po bumpie, bez edycji, o ile nie padną.
- Nowe (najpierw czerwone, red-first): `test_skillsync_dashboard_workdir.py`, `test_skillsync_workdir_rule.py`, przypadek fixtures `worktree` budowany w teście, testy chipu w rendererze.
- Baseline (F0): stare porażki oznaczone przed zmianą (liczby per moduł), po zmianie porównanie; `py_compile` po każdej zmianie Pythona.
- Pełny bieg i macierz (`prepush-matrix`) tylko po LEASE GRANT od Dyspozytora (`flock` `heavy-run.lock`, pamięć ≥ 10 GiB, `MemoryMax`); podgląd dashboardu: port 5162, `XDG_*` w katalogu worktree, procesy zabijane po PID.

### Budżet `instructions/_base.md` (decyzja planu)

Plik: 8187 B, limit 8192 B (`base_max_bytes`). Rdzeń niesie tylko regułę i odesłanie; warunki K9, Q11–Q13 i ścieżki porażek mieszkają w `feature/references/workdir.md`. Rachunek (F3.1 mierzy `wc -c` po każdej edycji, cel netto ≤ 0):

| Zmiana w `_base.md` | Bajty |
|---|---|
| l.61: „osobny worktree i gałąź robocza;” → „katalog roboczy (pytanie, `Katalog:` w planie) i gałąź robocza;” | +31 |
| zdanie „Push na bazę”: bez „z worktree” i bez klauzuli o `--ff-only` głównego checkoutu (przechodzi do `workdir.md`), z odesłaniem „szczegóły w `feature`” | −62 (212 → 150) |
| l.72: „kopia przebiegu poza worktree; worktree sprzątasz po `CP` albo zamknięciu PR-a” → „kopia przebiegu i sprzątanie worktree (tylko `Katalog: worktree`) po `CP` albo zamknięciu PR-a” | +16 (policzone) |
| razem | -15 B → ok. 8172 B (pomiar `wc -c` w F3.1 rozstrzyga) |

Zasady: cudzych zdań nie zagęszczamy; konflikt ograniczeń = przerwanie i zgłoszenie (lekcja o budżecie). Safety pozostaje w rdzeniu („baza rozbieżna = STOP, rebase/merge tylko za osobną zgodą”).

## Zasoby

Port podglądu 5162 (przydział z zlecenia), izolowany `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_STATE_HOME` w `CORE-WORKDIR/.preview-state`, zakaz dotykania `~/.local/share/skillsync`, `~/.config/skillsync`, głównych checkoutów, Skippstera, pliku stanu dyspozycji i repo ops. Baza danych: brak. Równoległość: tylko F2 ‖ F3 (rozłączne pliki: F2 = `library/skills/dashboard/**`, F3 = `library/skills/feature/**`, `pr-run`, `skillsync`, `instructions`).

## Dokumentacja i wiedza

Dezaktualizowane przez zmianę: `docs/dashboard-contract.md` (nowa sekcja i normalizacja), `docs/workflow.md`, `docs/workflow-details.md`, `library/skills/dashboard/SKILL.md`, `library/skills/skillsync/SKILL.md`, `instructions/_base.md` i skille `feature`/`pr-run`. Wiedza domenowa: nowy rekord `docs/domain/workdir.md` (rozpoznawanie worktree/main, wybór katalogu, terminy; `index.yaml`, właściciel: scribe w F4); glosariusz bez zmian (limit rozmiaru). Lekcja tylko, gdy zdarzy się potwierdzona awaria (np. budżet `_base.md`, zachowanie `pr` na bazie).

## Wersja i changelog

Wersja docelowa: **1.10.0**, ale wydanie (bump, tag, publikacja) robi Dyspozytor na gałęzi integracyjnej `release/1.10.0` (dwie paczki 1.10.0 równolegle). **Odstępstwo od wcześniejszego planu (zlecenie z 18.11):** w tej paczce NIE zmieniamy `skillsync_workflow/__init__.py`, `install.sh`, `library/layer.yaml` (`version`, `core_min`), linii „Stan opisu” w `docs/workflow*.md` ani `scripts/check_release.py`. `CHANGELOG.md` i `library/CHANGELOG.md`: wyłącznie sekcja `[Unreleased]`, addytywnie (EN, Added/Changed; Breaking: None w wydaniu).
Skutek uboczny bumpu, do raportu DONE (bez edycji w tej paczce): `LISTEN_NOTE_UNTIL = (1, 10)` (`dashboard_ports.py`) wyłączy zdanie konsoli „od 1.8.0 …” w `dashboard serve` dopiero po bumpie; testy `test_skillsync_dashboard_ports.py` i `…_listen_defaults.py` zależą od wersji przez `_version_tuple`; wpis Changed i bump robi wydanie. Tag i publikacja: osobne polecenie właściciela.

## Ryzyka

Rejestr w [specu](../specs/2026-11-18-core-workdir.md) (R1–R8). Dodatkowo plan: (a) `_base.md` ma 5 B zapasu: F3 mierzy `wc -c` przed i po każdej edycji i przerywa przy konflikcie ograniczeń zamiast zagęszczać cudze zdania; (b) testy treści przypinają dosłowne frazy: zmiana frazy i testu w jednym commicie; (c) F2 dotyka wspólnego renderera: Skippster dostaje chip po swojej synchronizacji, bez naszej zmiany wtyczki.

## Otwarte pytania

- [ZAŁOŻENIE] F2 bez znacznika `[UI]` i bez prototypu: dowodem są zrzuty obu motywów; gdyby użytkownik chciał bramki prototypu, trzeba dodać linię `Prototyp:` i fazę `[UI]` (zmiana planu). Przyjęte: bez.
- [ZAŁOŻENIE] Wersja 1.10.0 (zob. wyżej), do potwierdzenia przy „GO implement”.

## Progress

Pole puste (`- [ ]`) = do zrobienia, zaznaczone (`- [x]`) = zrobione; po wylądowaniu kroku dopisz jego sha po ` — `, a id ani tytułów nie zmieniaj.
Szacunek całości: ok. 5–6 h pracy agenta (cztery zdolności małe do średnich: serwer, renderer, procedura, dokumentacja).

### Faza 0: Baseline
- [x] F0.1 Baseline zestawów punktowych (liczby per moduł) i `wc -c _base.md` (test-runner)

### Faza 1: Serwer
- [x] F1.1 Kod: pomocnik `gitdir`/`commondir`, `project.worktree`, testy rozpoznania i fixtures (implementer)
- [x] F1.2 Szczebel testów i commit kodu
- [x] F1.5 Poprawka liczenia rund review w `compute_acts` (`<krok>-N.md` tak, `*-decyzje.md` i pozostałe pliki nie), test regresji (spec, plan, kod), reguła w `docs/dashboard-contract.md`, commit
- [x] F1.3 Review `kod` fazy (jedna runda) i poprawki
- [x] F1.4 `--fast` końca fazy i checkpoint fazy

### Faza 2: Renderer
- [x] F2.1 Kod: chip, opis, CSS, `descriptions.json`, `SKILL.md` dashboardu, testy (implementer)
- [x] F2.2 Zrzuty obu motywów z podglądu (port 5162) i szczebel testów
- [x] F2.3 Commit kodu
- [x] F2.4 Review `kod` fazy (jedna runda) i poprawki
- [x] F2.5 `--fast` końca fazy i checkpoint fazy

### Faza 3: Procedura katalogu roboczego
- [x] F3.1 Teksty reguły (`workdir.md`, `_base.md` z budżetem wg tabeli, `feature`, `plan-*`, `spec`, `pr-run`, `pr-after-approval`, `retro-quality`, `worker-mode`, `skillsync`, docs) i testy treści (implementer)
- [x] F3.2 Szczebel testów i commit
- [x] F3.3 Pętla pełna `kod` (reguła zgód i commitów) i poprawki
- [x] F3.4 `--fast` końca fazy i checkpoint fazy

### Faza 4: Dokumentacja i wiedza
- [x] F4.1 CHANGELOG-i (`[Unreleased]`, bez bumpu wersji), `upgrading.md`, rekord `docs/domain/workdir.md` (scribe), `check_release.py` i commit

### Faza 5: Zamknięcie
- [x] F5.1 Pętla końcowa `kod` (jedna runda + verifier) i poprawki
- [x] F5.2 Pełny bieg i macierz (po LEASE), `leakcheck --target core`, gitleaks
- [x] F5.3 Wynik (orkiestrator): odstępstwa od speca i planu (F2 bez `[UI]`; wersja nie podbita w paczce, wydanie robi Dyspozytor; zdanie „Push na bazę” zostaje w rdzeniu skrócone, a szczegóły idą do `workdir.md`, nie do `feature/SKILL.md`), retro-quality, `--final`, `Status: done` planu i `implemented` speca, commit

## Wynik

### Zmiany i dowody

Gałąź `feat/core-workdir` (baza `2594fed`, release 1.9.1), commity: serwer (`3903e15`, `0e8a43c`, poprawki `b5cf2fb`), renderer (`21f2745`, poprawki `e26e03d`), procedura (`8afc7ab`, poprawki `c17c260`, `55ae837`), dokumentacja (`bf32c05`), poprawki pętli końcowej (`28ee76d`). Wersja NIE podbita (wydanie robi dyspozytor na `release/1.10.0`).
- A: `project.worktree` w `dashboard show` i `GET /api/status` (`{kind, name, path, mainPath}` albo `null`, rozpoznanie z plików `.git`/`commondir`, bez procesu git), chip w nagłówku („worktree: <nazwa>” / „główny checkout”, opis i ścieżki w dymku, bez kopiowania), nowa ikona `i-folder`.
- B: pytanie o katalog roboczy przy „GO plan” (własny worktree domyślnie / główny checkout = bieżąca gałąź, także bazowa), linia planu `Katalog:`, nowa referencja `feature/references/workdir.md`, reguła w `instructions/_base.md` (8187 → 8186 B) i skillach.
- Dodane po „GO implement” (za zgodą użytkownika): liczenie rund review na stronie statusu (F1.5): rundą jest tylko raport `<krok>-N.md`.
- Dowody: logi `.skillsync/runs/2026-11-18-core-workdir/` (F0.1 baseline, F1.*, F2.*, F3.*, F4.2, F5.1, F5.2); pełny bieg Linux (stacja, tag `workdir-28ee76d`, HEAD `28ee76d`): 6712 testów, 2 FAIL (higiena rdzenia, naprawione: spec i plan wyjęte ze śledzenia, ścieżka w rekordzie domenowym usunięta), 26 skipów, wszystkie w baseline (`check_skips`); stan użytkownika bez zapisów.

### Uwagi z review

| Uwaga (tytuł) | Waga | Co zrobiono |
|---|---|---|
| Spec: kopiowanie kliknięciem vs celowe usunięcie schowka z renderera | major | decyzja użytkownika: bez kopiowania (K6) |
| Spec: fixtures `cp-ready` i przypadek `worktree` budowany w teście | major | naniesione w specu i F1 |
| Plan: budżet `_base.md` (8187/8192), pliki wydania, terminologia w glosariuszu bootstrap, imię właściciela w treści publicznej | major ×4 | tabela bajtów, nowa referencja `workdir.md`, rekord `docs/domain/workdir.md`, redakcja |
| F1: przenośność testów (posix, symlinki, skipy w baseline) | major | poprawione (`b5cf2fb`) |
| F3: zdanie o kopii przebiegu w `_base.md` zmieniło sens; brak ponownych warunków przy wznowieniu `główny` | major ×2 | poprawione (`c17c260`), runda 2 PASS |
| Pętla końcowa: brak wpisu w baseline skipów dla testu dowiązania | major | dodany (`28ee76d`) |
| Pozostałe uwagi (minor/nit) wszystkich rund | minor/nit | naniesione, z wyjątkami w „Ograniczeniach” |

### Pokrycie kryteriów

| Kryterium | Test(y) | Wynik |
|---|---|---|
| K1–K3 | `test_skillsync_dashboard_workdir.py` (21), `…_contract.py`, `…_dashboard.py`, `/api/status` w `…_server.py` | PASS |
| K4–K6 | `test_skillsync_dashboard_renderer.py` (73), `…_a11y.py`, `…_theme_tokens.py`; podgląd (ciemny, jasny, 380 px; `F2.2-podglad.md`) | PASS |
| K7 | `expected*.json` (11 plików), `…_contract.py`, przypadek `worktree` w teście | PASS |
| K8–K9 | `test_skillsync_workdir_rule.py` (10), `…_feature_demo.py` | PASS |
| K10 | `wc -c` 8186 B, `test_skillsync_attribution.py` (budżet), `…_delivery_mode.py`, `…_library_smoke.py` | PASS |
| K11 | `…_domain_size.py`, `…_changelog_breaking.py`, `scripts/check_release.py` (wersje bez zmian) | PASS |
| K12 | pełny bieg Linux 6712 testów | PASS po poprawce higieny; macierz Windows i macOS NOT_RUN (zleca dyspozytor przed PUSH SLOT) |

### Stare porażki i ograniczenia

- Znane ograniczenia: (1) opisy i tooltipy etapów review mówią o podciągach nazw plików (tekst przypięty w 10 fixtures współdzielonych z wtyczką edytora; zapisane w CHANGELOG); (2) chip ma `tabindex=0`, a `render()` co 5 s przebudowuje nagłówek: fokus klawiatury ginie przy aktualizacji (wzorzec zastany dla elementów `[data-desc]`); (3) test długiej nazwy chipu grepuje CSS (zachowanie obcięcia potwierdza podgląd, nie test); (4) Skippster: jego `src/data/dashboard.ts` buduje `project` z białej listy i gubi `worktree`, więc chip pojawi się dopiero po dodaniu pola do jego parsera (korekta założenia ze speca), plus wymagany symbol `i-folder` przy własnym sprite; (5) `read_git` ignoruje `commondir` w `.git` będącym katalogiem (nierealny przypadek, pinuje test).
- Skutek bumpu do raportu DONE: `LISTEN_NOTE_UNTIL = (1, 10)` (`dashboard_ports.py`) wyłączy zdanie konsoli „od 1.8.0 …” w `dashboard serve`; testy `…_ports.py` i `…_listen_defaults.py` zależą od wersji przez `_version_tuple`; wpis Changed i bump robi wydanie (tak samo „Stan opisu”, `__init__.py`, `install.sh`, `layer.yaml`).
- Odstępstwa od speca i planu: F2 bez znacznika `[UI]` i bez prototypu (dowód: podgląd obu motywów i wąskiej kolumny); wersja nie podbita w paczce; F1.5 spoza speca (za zgodą); zdanie „Push na bazę” w `_base.md` skrócone, a szczegóły w `workdir.md` (nie w `feature/SKILL.md`); linia `Katalog:` niesie nazwę katalogu zamiast ścieżki; pełny bieg poszedł na HEAD `28ee76d` (dzierżawa wskazywała `bf32c05`, różnica to poprawki testów i tekstów po pętli końcowej).
- Weryfikator (świeży) nie był uruchomiony dla poprawek F1.3 i F5.1: dowodem są testy punktowe i pełny bieg.
- Wniosek dla skryptu stacji (ops): `run.sh` drukuje „flock-exit=1” także wtedy, gdy rc≠0 pochodzi z testów.
- Retrospektywa: sygnały 3 (korekta użytkownika, rozjazd procedury z higieną rdzenia, nadmiar ceremonii do potwierdzenia), propozycja P1 (`skills/feature/SKILL.md`: „GO plan” nie commituje speców i planów tam, gdzie repozytorium tego zabrania) `deferred` (worktree); szczegóły w `retro-review.md`.

### Zdobyta wiedza

`docs/domain/workdir.md` (nowy rekord: terminy, reguły wyboru katalogu, rozpoznanie bez procesu git), `docs/dashboard-contract.md` (sekcja `project.worktree`, reguła rund review, normalizacja fixtures), `library/skills/feature/references/workdir.md`, `docs/upgrading.md` (1.9.1 → 1.10.0), CHANGELOG-i (`[Unreleased]`). Lekcja kandydująca (do zapisu przy wydaniu): spec i plan w publicznym rdzeniu wychodzą ze śledzenia przed pełnym biegiem (P1).
