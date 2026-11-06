---
title: 'Stop hook sprawdzaj w prawdziwym Claude Code: testy i przeglądy nie widzą jego środowiska'
tags: [workflow, hook, claude-code, testy]
paths: ['hooks/*', 'skillsync', 'tests/test_skillsync_closure.py']
source: "Próba `claude -p` w projekcie tymczasowym, konto prywatne, 2026-10-31; .skillsync/runs/2026-10-31-r2b/live/; docs/plans/2026-10-31-r2b-petla-po-kazdym-zadaniu.md"
verified: 2026-10-31
---
**Objaw:** hook `check-closure.py` miał 40 zielonych testów i przegląd PASS, a na żywo w sesji startującej z podkatalogu nic nie robił, zaś w sesji z katalogu głównego blokował turę więcej niż raz.

**Dlaczego:** `CLAUDE_PROJECT_DIR` to katalog startu sesji, a nie korzeń projektu, choć ustawienia są czytane także z katalogów wyżej. `stop_hook_active` przestaje chronić, gdy model użyje narzędzia między zatrzymaniami, więc nie gwarantuje jednej blokady. Testy wywoływały hook z założonymi wartościami, a przeglądy czytały kod bez uruchamiania.

**Co działa:** uruchom hook przez prawdziwe `claude -p` (`--max-turns`, `--output-format json`, tani model), z dodatkowym hookiem dopisującym `pwd` i zmienne do pliku. Sprawdzaj `num_turns` i liczbę wpisów. Korzeń bierz z `git rev-parse --show-toplevel`, a jednorazowość zapewnij własnym znacznikiem per sesja i plan.

**Pułapka pokrewna:** kod wyjścia 2 z samego `python3` przy braku skryptu też jest blokadą. Wpis musi sprawdzać istnienie pliku, inaczej brakujący skrypt zablokuje turę bez końca.
