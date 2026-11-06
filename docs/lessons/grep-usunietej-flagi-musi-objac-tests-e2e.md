---
title: "Grep po usuniętej fladze musi objąć tests/e2e: kontrola ograniczona do tests/test_*.py przepuszcza stare wywołania"
tags: [skillsync, tests, e2e, cli, grep, regression, breaking-change, prototype]
paths: ['tests/e2e/**', 'tests/test_skillsync*.py', 'skillsync_workflow/prototype.py', 'skillsync_workflow/prototype_server.py']
source: "tests/e2e/prototype/accept_cli.py:19, tests/e2e/prototype/lib.mjs:179 i :183, tests/e2e/prototype/env.py:154, tests/e2e/prototype/f3.mjs:153 (wywołania --library w worktree P1 2026-11-05); skillsync_workflow/prototype.py (flaga --library była zdefiniowana, gdy zdarzenie wystąpiło); docs/plans/2026-11-05-warstwy-generator.md, Wynik, krok 2.4 (odkrycia dla 3.2) i K1 w specu A (kontrolny grep)"
verified: 2026-11-05
---
**Objaw:** po usunięciu flagi `--library` kontrolny grep w testach (`tests/test_*.py`) jest czysty, a w worktree P1 nadal
zostają wywołania tej flagi w testach E2E prototypu: `tests/e2e/prototype/accept_cli.py:19`, `lib.mjs:179` i `:183`,
`env.py:154`, `f3.mjs:153`. Po usunięciu flagi z prototypów wywołania te przestaną działać, a błąd ujawni się dopiero
przy uruchomieniu E2E (osobny bieg), a nie w zestawie `unittest discover`.

**Przyczyna:** wyszukiwanie zawężono do `tests/test_*.py`. Katalog `tests/e2e/**` zawiera pliki `.py` i `.mjs`, które nie pasują
do tego wzorca, więc nie były przeszukiwane. Flaga była wtedy jeszcze zdefiniowana w `skillsync_workflow/prototype.py`, więc stare wywołania w E2E działały
i dopiero usunięcie flagi z prototypów ujawniło je jako błąd.

**Reguła:**
- Grep po usuniętej fladze (lub nazwie usuniętej funkcji/klucza) obejmuje **cały** zakres: `tests/**` (w tym `tests/e2e/**`,
  pliki `.py`, `.mjs`, `.js`, `.ps1`, fixtury), `skillsync`, `skillsync_workflow/**`, `skills/**`, `instructions/**`, `docs/**`
  i `tests/windows/**`. Wyjątkiem są tylko wpisy historyczne w `CHANGELOG*` i w dokumentach planów/specyfikacji.
- Zawężenie do `tests/test_*.py` jest błędem kontroli. Zawężenie dozwolone jest tylko z uzasadnieniem w planie.
- Gdy flaga zostaje w kodzie tymczasowo (do kroku, który ją usunie), wywołania w testach E2E są zapisane w planie jako
  znane odstępstwo z numerem kroku, a nie znikają z kontroli.
- Zmiana CLI (flaga usunięta lub przeniesiona) wymaga kontroli wyszukiwania przed CP, nie tylko zielonego zestawu testów.

**Jak wykryć:**
- `rg -n -- '--library' tests skillsync skillsync_workflow skills instructions docs --glob '!CHANGELOG*'` — wynik poza
  dozwolonymi odstępstwami zapisanymi w planie ma być pusty.
- Przed CP: sprawdź, że lista trafień obejmuje pliki `.mjs` i `.py` w `tests/e2e/**`, nie tylko `tests/test_*.py`.
- Zestaw `unittest discover -s tests -p 'test_skillsync*.py'` nie wykrywa tych wywołań: do E2E potrzebny osobny bieg z
  przydzieloną przeglądarką, zapisany w logu (zob. plan, krok 5.1).
