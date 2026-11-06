---
title: 'Bez obowiązkowych checkpointów pętla samodoskonalenia jest ślepa, a błędny zapis rekordu psuje klasę przebiegu'
tags: [skillsync, checkpoint, retro, workflow, evidence, dispatch]
paths: ['skillsync_workflow/retro.py', 'skillsync_workflow/runs.py']
source: docs/plans/2026-10-30-r2-self-improve.md (F1, F2, 4.1 próbny raport); library/skills/feature/references/measurement.md w bibliotece; dyspozycja 2026-10-30-skippster-r4-r3 (odbiór R3b, S1, S2, R4)
verified: 2026-10-31
---
**Objaw 1:** procedury nie wymagały checkpointów, więc `retro` dla `poc-a` dało `no_data`. Błąd B1, luka dowodu P13a i incydent z plikami planów pozostały niewidoczne.
**Przyczyna 1:** `feature` i `bugfix` kazały zapisywać checkpoint tylko przy przerwaniu, a w worktree bez manifestu polecenie i tak nie działało ([lekcja](checkpoint-w-worktree-wymaga-manifestu.md)).
**Reguła 1:** rekord po każdym wyniku kroku, także `FAIL`, `BLOCKED`, `NOT_RUN`; odbiór paczki przez `retro`. Niezapisana porażka nadal jest niewidoczna, tego narzędzie nie wykryje.

**Objaw 2 (R3b, S1, R4):** `review`, `test-red` lub `spike-*` zostawał na `FAIL`, bo domknięcia brakowało albo zapisano je pod inną nazwą (`verify-review`, `spike-claude-2`), a raport R3b mówił tylko „retro wykonane”. Klasa: `incomplete`.
**Przyczyna 2:** klasa grupuje rekordy po nazwie kroku, więc ostatni rekord starej nazwy zostaje `FAIL` (`runs.py:_classify_run`).
**Reguła 2:** ponowienie idzie pod tą samą nazwą `--step` z kolejnym `--attempt`. Raport podaje klasę, nie sam fakt uruchomienia. `retro` sprawdzaj w trakcie przebiegu.

**Objaw 3 (R3b, R4):** plan albo nadpisany log jako dowód daje trwałe `partial`, bo plik się zmienił.
**Przyczyna 3:** rekord przechowuje skrót SHA-256 pliku, a plan i ponownie używany log zmieniają się po zapisie rekordu.
**Reguła 3:** dowód to własny plik w katalogu przebiegu, nigdy plik śledzony ani nadpisywany. Luki nie usuwamy; przyjmuje ją użytkownik albo robimy nową próbę.

**Jak wykryć:** `skillsync retro <główne repo> --run-id <run>.<paczka>`, pola `class` i `rubric.evidence`. Rekordy dopisane po fakcie widać w `recorded_at`; taki przebieg nie liczy się do kryterium R2.
