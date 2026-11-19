---
title: "`test-run`: kolejność rozpoznawania narzędzia (`detect_tool`) i reguła linii `Executed` Karmy"
tags: [skillsync, test-run, detect-tool, karma, counts, hooks]
paths: ['skillsync_workflow/test_run.py', 'skillsync_workflow/data/test_tools.json', 'skillsync_workflow/test_adapters/karma_line.py', 'skillsync_workflow/quality.py', 'library/hooks/guard-tests.py', 'tests/test_skillsync_test_run*.py']
source: 'odczyt skillsync_workflow/test_run.py (`detect_tool`, `unwrap_command`, `cmd_metachar_notice`), test_adapters/karma_line.py (`parse_line`, `Tally`), library/hooks/guard-tests.py (`tool_of`, `program_target`)'
verified: 2026-11-19
load: on-demand
---
Reguła rozpoznania: `detect_tool` zwraca narzędzie w kolejności: (1) pierwsze słowo po zdjęciu opakowań (słowa modułów, potem narzędzia z tabeli); (2) słowo po `-m`, gdy wcześniej stoi „gospodarz” (`python*`, `py`, `pypy*`, `coverage`); (3) narzędzie z tabeli w pierwszych trzech słowach; (4) słowo modułu będące też narzędziem (`pytest`, `py.test`) w dowolnym miejscu. Moduł niebędący narzędziem (`unittest`) nie pasuje w krokach 3–4. Hak `guard-tests` odtwarza kroki 1–4 w `tool_of`, ale klasyfikuje program tylko kroki 1–2 (krok 2 wymaga, by sam pierwszy wyraz był gospodarzem). `tox` jest narzędziem, nie opakowaniem. Szczegóły i przykłady: `docs/test-adapters.md`.

Reguła linii Karmy: `Executed N of M` jest licznikiem, gdy po zdjęciu ANSI tekst przed nim jest pusty albo jest etykietą przeglądarki kończącą się `): `, a po `of M` stoi koniec linii albo spacja i `(`, `SUCCESS`, `ERROR`, `DISCONNECTED` lub `FAILED`. `Executed` nie liczy pominiętych: passed = N − X, failed = X, skipped tylko z `(skipped S)`, total = M. Kilka przeglądarek: suma ostatnich linii każdej etykiety. Jedno źródło semantyki: `test_adapters/karma_line.py` (czytnik na żywo, `quality._test_counts`, `_failure_count`). Granica: przy kodzie wyjścia 0 porażki Karmy z logu nie są czytane.

Powiązana lekcja: `docs/lessons/tabela-narzedzi-dwa-interpretery-test-parytetu.md`.
