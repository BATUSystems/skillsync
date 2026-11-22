---
title: "Alias w regexie nie dodaje grup przechwytujących, a flagi (?i) idą tylko na początku wzorca (Python 3.11)"
tags: [skillsync, python, regex, aliases, groups, python311]
paths: ['skillsync_workflow/retro.py', 'skillsync_workflow/quality.py', 'skillsync_workflow/protocol_tokens.py', 'tests/test_skillsync_crlf_headers.py']
source: "spec paczki CORE-1130-I18N-ALIASES (22.11, zatwierdzony), ustalenie 18; tests/test_skillsync_crlf_headers.py:52-66 (quality._PLAN_RUN, group(1)); retro.py:595 (retro._SIGNAL, group(6)); stan @ ccc01eb"
verified: 2026-11-22
---
Objaw: po dopisaniu aliasu kod czyta zły numer grupy (`retro._SIGNAL` używa `group(6)`, test CRLF `_PLAN_RUN.group(1)`), więc wynik jest cicho zły. Wzorzec składany z fragmentów z `(?i)` w środku rzuca `re.error: global flags not at the start of the expression` na Pythonie 3.11.
Przyczyna: alias wstawiony w nowy nawias przechwytujący przesuwa numerację grup. Flaga `(?i)` w środku wzorca jest od Pythona 3.11 błędem.
Reguła: alias wchodzi do istniejącej grupy albo do `(?:…)`; nie dodawaj nawiasu przechwytującego. Lokalnie bez wielkości liter używaj `(?i:…)` (helper `protocol_tokens.ci`), nigdy `(?i)` w środku. Przed zmianą wyrażenia sprawdź wszystkie użycia `group(n)` tego wzorca w całym module.
Jak wykryć: `grep -n "group(" skillsync_workflow/retro.py` i porównaj numery z liczbą grup we wzorcu; `grep -rn "(?i)" skillsync_workflow` — każde trafienie musi być na początku wzorca; `tests/test_skillsync_crlf_headers.py` punktowo na Pythonie 3.11.
