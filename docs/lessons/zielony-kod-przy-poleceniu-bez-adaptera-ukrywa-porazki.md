---
title: 'Kod wyjścia 0 przy poleceniu bez adaptera nie dowodzi testów: podsumowanie porażek Playwrighta, Vitesta i Jesta było niewidoczne'
tags: [skillsync, test-run, evidence, exit-codes, regex, playwright, vitest, jest]
paths: ['skillsync_workflow/test_run.py', 'skillsync_workflow/test_adapters/playwright.py', 'skillsync_workflow/test_adapters/vitest.py', 'tests/test_skillsync_test_run_hardening.py']
source: 'odtworzenie 20.11 na atrapie (projekt tymczasowy, `./skillsync test-run`, wyjście w stylu Playwrighta `  1 failed` / `  5 passed (3s)` przy kodzie 0: PASS, counts 5, failed 0, source regex); kod `_hidden_failures` (przed poprawką: tylko unittest i pytest); testy HiddenFailures w tests/test_skillsync_test_run_hardening.py'
verified: 2026-11-20
---
**Objaw:** `test-run` zaliczył bieg `PASS` (kod 0, `source: regex`, `counts` 5, `failed` 0) dla polecenia, którego wyjście drukowało `  1 failed` i `  5 passed (3s)` w stylu Playwrighta. Ta sama luka dotyczy wyjścia Vitesta (wiersz `Tests  1 failed | 4 passed`) i Jesta (`Tests: 1 failed, 4 passed, 5 total`). Pytest z `3 failed, 5 passed in 2.00s` przy kodzie 0 był już `BLOCKED`, więc luka dotyczyła tylko narzędzi bez tej reguły.

**Przyczyna:** `_hidden_failures` rozpoznawało przy kodzie 0 tylko podsumowania unittest (`FAILED (…)`) i pytest. Polecenie bez adaptera i bez raportu JUnit dostaje liczniki z luźnego odczytu całego wyjścia (`regex`), więc przy kodzie 0 jedynym dowodem był kod wyjścia. Nakładka, która kończy się kodem 0 mimo błędu testów (np. skrypt `run.ps1` owijający `npx`), przechodziła bez innego śladu.

**Reguła:**
- Dla poleceń bez adaptera i bez raportu `PASS` nie jest dowodem, dopóki nie sprawdzisz `end.source`: `regex` znaczy, że liczby pochodzą z tekstu, nie z narzędzia.
- Przy kodzie 0 i źródle `regex` czytaj tylko zakotwiczone wiersze podsumowania: Playwright (`  N failed`, `  N interrupted`, N > 0), Vitest (wiersz `Tests` z `N failed` i sumą w nawiasie, nigdy `Test Files`), Jest (`^Tests:\s+N failed,`). Nowe narzędzie dodawaj wzorcem z adaptera, nie gołym `(\d+) failed`: `3 failed attempts`, `0 failed`, `failed=0` i `Test Files  1 failed` muszą zostać zielone.
- Źródła `junit` i `adapter` nie przechodzą przez tę regułę: liczbę daje raport albo adapter (zob. [liczniki z regex kontra zdarzenia adaptera](liczniki-z-regex-kontra-zdarzenia-adaptera-jedno-zrodlo-wyniku.md)).
- Jest nie ma adaptera, więc jego obejściem jest raport `--junit`. Flagi zdejmującej luki nie ma: `--allow-no-count` nie weryfikuje kodu testów. Naprawa polecenia albo nakładki (kod wyjścia ma być kodem testów) jest jedynym pewnym wyjściem.
- Granica: Karma `(N FAILED)` przy kodzie 0 nadal nie jest czytana przez `_hidden_failures` (zob. rekord domeny `test-run-rozpoznanie-narzedzia-i-liczniki-karmy.md`).

**Jak wykryć:**
- Test `tests/test_skillsync_test_run_hardening.py`, klasa `HiddenFailures`: `test_a_clean_exit_with_a_playwright_vitest_or_jest_summary_is_blocked` (czerwony przed poprawką) i przypadki negatywne w tej samej klasie (`test_green_runs_with_the_word_failed_in_a_log_stay_green`, `test_a_log_that_only_looks_like_a_summary_of_another_tool_stays_green`).
- Mutacja: wzorzec bez zakotwiczenia (goły `(\d+) failed`) musi zaczerwienić przypadki negatywne; bez tego test nie chroni przed fałszywym `BLOCKED`.
- Ręcznie na atrapie: w projekcie tymczasowym z `skillsync checkpoint` polecenie drukujące `  1 failed` i kończące się kodem 0 daje `BLOCKED` i kod wyjścia 2. Rekord `end` z `source: regex` i statusem `PASS` przy wyjściu z `N failed` to sygnał, że reguła nie objęła narzędzia.

**Dowód:** kod `skillsync_workflow/test_run.py` (`_hidden_failures`, ok. linii 59–86); wzorce `test_adapters/playwright.py` (`SUMMARY`) i `test_adapters/vitest.py` (`TESTS`, `SUMMARY_PART`); testy `HiddenFailures`.

**Granice aktualności:** od wydania z wpisem Breaking „ukryte niepowodzenia” w `CHANGELOG.md` (sekcja `[Unreleased]`). Przy zmianie wzorców Playwrighta, Vitesta lub Jesta sprawdź test z pkt „Jak wykryć”.
