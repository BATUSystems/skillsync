---
title: "Tokeny protokołu mają jedno źródło (protocol_tokens.py); samodzielny hook trzyma kopię pilnowaną testem"
tags: [skillsync, protocol, tokens, parser, hooks, i18n, tests]
paths: ['skillsync_workflow/protocol_tokens.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/test_gate.py', 'skillsync_workflow/status.py', 'library/hooks/check-closure.py', 'tests/test_skillsync_run_alias.py']
source: "spec paczki CORE-1130-I18N-ALIASES (22.11, zatwierdzony), ustalenia 1 i 13; stan @ ccc01eb: quality.py _PLAN_RUN, test_gate.py _RUN_LINE, library/hooks/check-closure.py _RUN"
verified: 2026-11-22
---
Objaw: ta sama reguła linii `Przebieg:` była w trzech miejscach (`quality._PLAN_RUN`, `test_gate._RUN_LINE`, `check-closure.py` `_RUN`). Zmiana w jednym nie trafiała do pozostałych, więc ten sam plan mógł mieć inny przebieg w zamknięciu, w bramce testów i w hooku Stop. Komentarz „skopiowany z status.py” nie pilnował zgodności.
Przyczyna: hook Stop jest samodzielny (tylko biblioteka standardowa, nie może importować `skillsync_workflow`), a moduły generatora kopiowały regułę zamiast importować. Nie było testu, który porównywałby kopie.
Reguła: tokeny i ich gramatykę zmieniaj tylko w `skillsync_workflow/protocol_tokens.py` (`TOKENS`, `RUN_LINE`, `alternation`, `token_alternation`). Moduły importują stamtąd; nie dopisuj własnego wyrażenia z `Przebieg` ani `Run`. Hook `library/hooks/check-closure.py` zostaje samodzielny z lokalną kopią `Run:`/`Przebieg:`; każda zmiana w nim musi przejść `tests/test_skillsync_run_alias.py`, który czyta źródło hooka i porównuje jego tokeny z modułem.
Jak wykryć: `grep -rn "Przebieg" skillsync_workflow library/hooks` — poza `protocol_tokens.py` i komunikatami nie powinno być reguły wyszukującej linię przebiegu. Po zmianie tokenów uruchom punktowo `tests/test_skillsync_run_alias.py`.
Stan po review (runda 1, 22.11): moduły importujące `protocol_tokens` to `quality`, `test_gate`, `status`, `prototype`, `retro` (`bare_alternation`) i `plan_phases` (`bare_names`); `common.py` go nie importuje. Nowy wzorzec w tych modułach buduj z `bare_alternation` (z `capture=True` dla jednej grupy przechwytującej), nie z ręcznej listy słów. Sprawdź: `grep -rn "protocol_tokens" skillsync_workflow`.
