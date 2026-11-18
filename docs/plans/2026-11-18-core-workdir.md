# Plan: chip katalogu na stronie statusu i wybór katalogu roboczego przy „GO plan” (CORE-WORKDIR)

Status: draft
Spec: docs/specs/2026-11-18-core-workdir.md
Spec-sha: 1a1b32345e61e5adef448efe26ced3fec46a4dcf
Przebieg: 2026-11-18-core-workdir
Katalog: worktree CORE-WORKDIR

## Streszczenie dla człowieka

**Co zmieniamy.** (1) Strona statusu dostaje w nagłówku chip „worktree: <nazwa>” albo „główny checkout” (pole `project.worktree` w `dashboard show`, pełna ścieżka w opisie, stopka bez zmian). (2) Przy „GO plan” sesja pyta: „własny worktree” (domyślnie) czy „główny checkout” (praca na bieżącej gałęzi, także bazowej); wybór zapisuje linia planu `Katalog:`, wznowienie ją czyta. Reguła „od GO plan osobny worktree” zmienia się w rdzeniu instrukcji i w skillu `feature` oraz w pochodnych. Wersja 1.10.0.
**Czego nie robimy.** Kopiowania ścieżki kliknięciem (decyzja: bez). Zmian Skippstera (zgłaszamy skutek). Odczytu `Katalog:` przez `status`/`dashboard`. Pytania w kartach pod Dyspozytorem (decyduje `ASSIGN`). Wydania, tagu i wersji ostatecznej.
**Ryzyka.** Praca na gałęzi bazowej w `główny` (commity i `CP` na bazę, w `pr` odmowa); druga sesja w tym samym katalogu; budżet `instructions/_base.md` (zapas 5 B); rozjazd reguły w ok. 16 plikach.
**Decyzje do podjęcia.** Wersja 1.10.0 (rekomendacja: MINOR, zlecenie Dyspozytora; ostatecznie Barciś); prototyp chipu: nie robimy (rekomendacja: statyczny podgląd w obu motywach jako dowód wzrokowy); `[UI]` faza F2: bez znacznika (rekomendacja, patrz „Otwarte pytania”).

## Cel

Wdrożyć w rdzeniu 1.10.0 zmiany opisane w [specu](../specs/2026-11-18-core-workdir.md): pole `project.worktree` i chip w nagłówku strony statusu (A1–A3) oraz pytanie o katalog roboczy przy „GO plan” z linią `Katalog:` (B1–B2).
Rdzeń jest publiczny: bez nazw i ścieżek klienta, ludzi i maszyn. Bez stopek i trailerów narzędzi AI w commitach. Wejście: `origin/main` = `2594fed` (release 1.9.1); gałąź `feat/core-workdir` w worktree `CORE-WORKDIR`.

## Kryteria akceptacji (mapowanie)

| Kryterium speca | Kroki | Dowód |
|---|---|---|
| K1–K3 (kontrakt, rozpoznanie bez gita, `path`/`mainPath`) | F1 | testy `tests/test_skillsync_dashboard_contract.py` i nowy `test_skillsync_dashboard_workdir.py` (katalog, plik `gitdir`, `commondir`, submoduł, uszkodzony worktree, dowiązanie) PASS; test, że `read_git` daje ten sam wynik co przed zmianą |
| K7 (fixtures) | F1 | `expected.json` wszystkich przypadków (`null`; `cp-ready` = `main`), przypadek `worktree` budowany w teście; test kontraktu fixtures PASS; normalizacja w `docs/dashboard-contract.md` |
| K4, K5, K6 (chip, opis, brak schowka) | F2 | `test_skillsync_dashboard_renderer.py` (chip dla `main`/`worktree`/`null`/brak pola, obcięcie nazwy, `data-desc-extra`), `…_a11y.py`, `…_theme_tokens.py`, `test_no_copy_buttons_anywhere` bez zmian PASS; zrzuty obu motywów z podglądu (port 5162) |
| K8, K9 (pytanie, `Katalog:`, warunki `główny`) | F3 | testy treści `test_skillsync_feature_demo.py` (k10-* zaktualizowane + nowe `Katalog:` w każdym pliku z listy) PASS; nowy test dla warunków K9 (bieżąca gałąź, baza w `pr`, odłączony HEAD, brudne drzewo) |
| K10 (spójność reguł, budżet) | F3 | `wc -c library/instructions/_base.md` ≤ 8192 (przed i po w „Wyniku”), `test_skillsync_library_smoke.py`, `test_skillsync_delivery_mode.py` PASS |
| K11 (dokumentacja, wersje) | F4 | grep dosłownych nazw; CHANGELOG-i, `upgrading.md`, `layer.yaml`; `leakcheck --target core --require full` PASS |
| K12 | F5 | pętla `kod` końcowa PASS, pełny bieg po LEASE, `leakcheck` i gitleaks osobno |

## Terminologia domenowa

Nowe terminy do `docs/domain/glossary.md` (potwierdzone w specu): **katalog roboczy przebiegu** (`Katalog:` w planie: `worktree <nazwa>` albo `główny`), **główny checkout** (katalog z `.git` jako katalogiem; praca na jego bieżącej gałęzi), **powiązany worktree** (`.git` jako plik z `gitdir:` i `commondir`). Bez kandydatów do wyjaśnienia.

## Kroki

Kolejność: F0, F1, F2, F3, F4, F5. F1 (serwer) i F2 (renderer) współdzielą kontrakt i fixtures, więc idą szeregowo (F2 po commicie F1). F3 (biblioteka i dokumenty reguły) jest rozłączna plikami z F1 i F2, ale jej testy treści i `library_smoke` czytają całą bibliotekę, więc szczebel testów F3 uruchamia orkiestrator po zakończeniu pisania F2; pisanie F3 może iść równolegle z F2 (dwóch piszących, rozłączne pliki).
F4 (scribe) startuje po commitach F1–F3. Kategorie pętli `kod`: F1 jedna runda (pole addytywne, odczyt bez gita; bez strażnika wycieków, migracji i danych wrażliwych), F2 jedna runda, F3 pętla pełna (reguła zgód i commitów w rdzeniu instrukcji: granica uprawnień), F5 końcowa jedna runda plus verifier.
Faza F2 nie ma znacznika `[UI]`: to jeden chip w istniejącym nagłówku, bez nowego ekranu; prototypu nie zamawiano, a dowodem wzrokowym są zrzuty obu motywów z podglądu (zob. „Otwarte pytania”).

| Id | Krok | Rola | Exec | Ryzyko kroku | Pliki |
|---|---|---|---|---|---|
| F0 | Baseline: moduły punktowe przed zmianą (dashboard contract/renderer/a11y/theme_tokens, feature_demo, delivery_mode, library_smoke, leakguard), `wc -c library/instructions/_base.md`; zapis liczb w „Wyniku” | test-runner | dispatch:cheap | low | (brak zapisu) |
| F1 | Serwer: wspólny pomocnik `gitdir`/`commondir` w `skillsync_workflow/dashboard.py` (z `read_git`, bez zmiany jego wyniku), `project.worktree` w `build_status`, testy rozpoznania (nowy plik), fixtures (`null`, `cp-ready` = `main`, przypadek `worktree` budowany w teście), sekcja i normalizacja w `docs/dashboard-contract.md` | implementer | dispatch:standard | medium | `skillsync_workflow/dashboard.py`, `tests/test_skillsync_dashboard_workdir.py`, `tests/test_skillsync_dashboard_contract.py`, `tests/fixtures/dashboard/**`, `docs/dashboard-contract.md` |
| F2 | Renderer: chip (`span.m` z `i-folder` i wewnętrznym `span` nazwy), `desc()`/`data-desc-extra`, CSS (wielokropek na wewnętrznym `span`), `descriptions.json` (`workdir_chip`), opis w `library/skills/dashboard/SKILL.md`, testy renderera i dostępności, zrzuty obu motywów (podgląd port 5162, izolowany XDG, PID) | implementer | dispatch:standard | medium | `library/skills/dashboard/assets/dashboard-render.js`, `app.css`, `library/skills/dashboard/descriptions.json`, `library/skills/dashboard/SKILL.md`, `tests/test_skillsync_dashboard_renderer.py`, `tests/test_skillsync_dashboard_a11y.py` |
| F3 | Procedura B: reguła katalogu i warunki K9 w `instructions/_base.md` (budżet: przeniesienie zdania „Push na bazę” do `feature/SKILL.md`), `feature/SKILL.md`, `plan-large.md`, `plan-details.md` (`Katalog:`), `spec.md`, `pr-run/SKILL.md`, `pr-after-approval.md`, `retro-quality.md`, `worker-mode.md`, `library/skills/skillsync/SKILL.md`, `docs/workflow.md`, `docs/workflow-details.md`; testy treści (k10-*, `delivery_mode`, nowy test `Katalog:`) | implementer | group:procedura:capable | high | `library/instructions/_base.md`, `library/skills/**`, `docs/workflow*.md`, `tests/test_skillsync_feature_demo.py`, `tests/test_skillsync_delivery_mode.py` |
| F4 | Dokumentacja i wiedza: `CHANGELOG.md`, `library/CHANGELOG.md`, `library/layer.yaml`, `docs/upgrading.md`, `docs/domain/glossary.md`, lekcje (jeśli powstaną), `docs/dashboard-contract.md` (kontrola) | scribe | dispatch:cheap | low | `CHANGELOG.md`, `library/CHANGELOG.md`, `library/layer.yaml`, `docs/**` |
| F5 | Pętla końcowa `kod` (jedna runda + verifier), pełny bieg i macierz po LEASE, `leakcheck`, gitleaks, „Wynik”, retro-quality, `--final`, zamknięcie | orkiestrator | inline | medium | plan, spec, `.skillsync/runs/` |

## TESTY

- cwd: worktree `CORE-WORKDIR`; środowisko: `python3`, bez sieci; bez usług współdzielonych. Moduły punktowe: `python3 -m unittest tests.test_skillsync_dashboard_contract tests.test_skillsync_dashboard_workdir tests.test_skillsync_dashboard_renderer tests.test_skillsync_dashboard_a11y tests.test_skillsync_dashboard_theme_tokens tests.test_skillsync_feature_demo tests.test_skillsync_delivery_mode tests.test_skillsync_library_smoke` (rc w zmiennej, log do pliku `<krok>-<próba>.log` w `.skillsync/runs/2026-11-18-core-workdir/`).
- Do dostosowania: k10-* w `test_skillsync_feature_demo.py` (l.86–96), `test_skillsync_delivery_mode.py` (l.273–281, fraza „Słowa zgody: `CP`” musi zostać), fixtures `expected*.json`.
- Nowe: `test_skillsync_dashboard_workdir.py` (K1–K3, repozytoria w katalogu tymczasowym), przypadek fixtures `worktree` budowany w teście, testy treści `Katalog:` i warunków K9, testy renderera chipu.
- Baseline (F0): stare porażki oznaczone przed zmianą; po zmianie porównanie liczb. `py_compile` po każdej zmianie Pythona.
- Pełny bieg i macierz (`prepush-matrix`) tylko po LEASE GRANT od Dyspozytora (`flock` `heavy-run.lock`, pamięć ≥ 10 GiB, `MemoryMax`); podgląd dashboardu: port 5162, `XDG_*` w katalogu worktree, procesy zabijane po PID.

## Zasoby

Port podglądu 5162 (przydział z zlecenia), izolowany `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_STATE_HOME` w `CORE-WORKDIR/.preview-state`, zakaz dotykania `~/.local/share/skillsync`, `~/.config/skillsync`, głównych checkoutów, Skippstera, pliku stanu dyspozycji i repo ops. Baza danych: brak. Równoległość: tylko F2 ‖ F3 (rozłączne pliki).

## Dokumentacja i wiedza

Dezaktualizowane przez zmianę: `docs/dashboard-contract.md` (nowa sekcja i normalizacja), `docs/workflow.md`, `docs/workflow-details.md`, `library/skills/dashboard/SKILL.md`, `library/skills/skillsync/SKILL.md`, `instructions/_base.md` i skille `feature`/`pr-run`. Wiedza domenowa: terminy do `docs/domain/glossary.md`, rozpoznawanie worktree/main i wybór katalogu w `docs/domain/` (nowy rekord w `index.yaml`, właściciel: scribe w F4). Lekcja tylko, gdy zdarzy się potwierdzona awaria (np. budżet `_base.md`, zachowanie `pr` na bazie).

## Wersja i changelog

Propozycja: **1.10.0** (MINOR: nowe addytywne pole kontraktu i zmiana procedury `feature`; Breaking: None). Zlecenie Dyspozytora wskazuje 1.10.0; potwierdza Barciś najpóźniej przy „GO implement”. Pliki: `CHANGELOG.md` (EN, Added/Changed), `library/CHANGELOG.md`, `library/layer.yaml`, `docs/upgrading.md` (sekcja 1.9.1 → 1.10.0). Numer wersji generatora i warstwy wchodzi do diffu przed rundą 1 pętli końcowej. Tag i publikacja: osobne polecenie Barcisia.

## Ryzyka

Rejestr w [specu](../specs/2026-11-18-core-workdir.md) (R1–R8). Dodatkowo plan: (a) `_base.md` ma 5 B zapasu: F3 mierzy `wc -c` przed i po każdej edycji i przerywa przy konflikcie ograniczeń zamiast zagęszczać cudze zdania; (b) testy treści przypinają dosłowne frazy: zmiana frazy i testu w jednym commicie; (c) F2 dotyka wspólnego renderera: Skippster dostaje chip po swojej synchronizacji, bez naszej zmiany wtyczki.

## Otwarte pytania

- [ZAŁOŻENIE] F2 bez znacznika `[UI]` i bez prototypu: dowodem są zrzuty obu motywów; gdyby Barciś chciał bramki prototypu, trzeba dodać linię `Prototyp:` i fazę `[UI]` (zmiana planu). Przyjęte: bez.
- [ZAŁOŻENIE] Wersja 1.10.0 (zob. wyżej), do potwierdzenia przy „GO implement”.

## Progress

Pole puste (`- [ ]`) = do zrobienia, zaznaczone (`- [x]`) = zrobione; po wylądowaniu kroku dopisz jego sha po ` — `, a id ani tytułów nie zmieniaj.
Szacunek całości: ok. 5–6 h pracy agenta (cztery zdolności małe do średnich: serwer, renderer, procedura, dokumentacja).

### Faza 0: Baseline
- [ ] F0.1 Baseline modułów punktowych i `wc -c _base.md` (test-runner)

### Faza 1: Serwer
- [ ] F1.1 Kod: pomocnik `gitdir`/`commondir`, `project.worktree`, testy rozpoznania i fixtures (implementer)
- [ ] F1.2 Szczebel testów i commit kodu
- [ ] F1.3 Review `kod` fazy (jedna runda) i poprawki
- [ ] F1.4 `--fast` końca fazy i checkpoint fazy

### Faza 2: Renderer
- [ ] F2.1 Kod: chip, opis, CSS, `descriptions.json`, `SKILL.md` dashboardu, testy (implementer)
- [ ] F2.2 Zrzuty obu motywów z podglądu (port 5162) i szczebel testów
- [ ] F2.3 Commit kodu
- [ ] F2.4 Review `kod` fazy (jedna runda) i poprawki
- [ ] F2.5 `--fast` końca fazy i checkpoint fazy

### Faza 3: Procedura katalogu roboczego
- [ ] F3.1 Teksty reguły (`_base.md` z budżetem, `feature`, `plan-*`, `spec`, `pr-run`, `pr-after-approval`, `retro-quality`, `worker-mode`, `skillsync`, docs) i testy treści (implementer)
- [ ] F3.2 Szczebel testów i commit
- [ ] F3.3 Pętla pełna `kod` (reguła zgód i commitów) i poprawki
- [ ] F3.4 `--fast` końca fazy i checkpoint fazy

### Faza 4: Dokumentacja i wiedza
- [ ] F4.1 CHANGELOG-i, `layer.yaml`, `upgrading.md`, glosariusz, rekord `docs/domain` (scribe) i commit

### Faza 5: Zamknięcie
- [ ] F5.1 Pętla końcowa `kod` (jedna runda + verifier) i poprawki
- [ ] F5.2 Pełny bieg i macierz (po LEASE), `leakcheck --target core`, gitleaks
- [ ] F5.3 Wynik, retro-quality, `--final`, `Status: done` planu i `implemented` speca, commit

## Wynik

(po wykonaniu)
