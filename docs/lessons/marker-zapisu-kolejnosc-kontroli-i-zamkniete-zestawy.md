---
title: "Marker zapisu: kolejność kontroli w serwerze i pułapki testów (zamknięte zestawy pól, osobny .gitignore fixtury, zegar testu)"
tags: [skillsync, prototype, writing, server, testy, accept]
paths: ['skillsync_workflow/prototype_server.py', 'skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype_accept.py', 'tests/test_skillsync_prototype_cli.py', 'tests/test_skillsync_prototype_writing*.py', 'tests/fixtures/prototype/assets/repo/.gitignore']
source: 'odczyt funkcji _prepare_accept, prepare_browser_operation, accept_project, perform_restore, read_writing w prototype_server.py oraz testów tests/test_skillsync_prototype_writing_http.py i _writing_gate.py (wydanie z markerem zapisu)'
verified: 2026-11-12
---
**Kolejność kontroli.** Odmowa `writing_in_progress` (409) stoi po walidacji kształtu (400) i po dedupie `cid` (idempotentne ponowienie nie jest blokowane), a przed porównaniem `version`/hash (`stale_view`) w `_prepare_accept`, w `accept_project` (preflight przed pierwszym zapisem) i w `perform_restore`. Marker czytaj pod blokadą transakcji zegarem sklepu. Odwrócenie kolejności z `stale_view` pokazuje użytkownikowi „przeładuj” zamiast „agent pisze”.

**Zamknięte zestawy pól w testach.** Nowe pole odpowiedzi (`writing` w `list` i `/project`) łamie testy, które porównują pełny zestaw kluczy (`LISTING_FIELDS` w `test_skillsync_prototype_accept.py`, `ADDED_FIELDS` w `test_skillsync_prototype_cli.py`, `test_list_shape_and_sorting`). Po dodaniu pola przejdź je grepem po nazwach tych zestawów.

**Osobna kopia `.gitignore`.** `tests/fixtures/prototype/assets/repo/.gitignore` nie jest lustrem szablonu biblioteki. Zmiana `GITIGNORE_LINES` wymaga ręcznej aktualizacji fixtury, inaczej `migrate` w testach daje `migrated` zamiast `already_migrated`.

**Zegar testu a zegar komendy.** Marker ma okno czasowe liczone od `since`; gdy test zapisuje marker zegarem wstrzykniętym, a komenda czyta zegarem rzeczywistym (albo odwrotnie), marker bywa „z przyszłości” lub wygasły i asercja „nie blokuje” przechodzi trywialnie. Test markera musi użyć tego samego zegara po obu stronach albo obu rzeczywistych, a przypadek negatywny sprawdzać obok pozytywnego.

**Jak wykryć:** `grep -n "LISTING_FIELDS\|ADDED_FIELDS" tests/*.py`; `diff tests/fixtures/prototype/assets/repo/.gitignore library/skills/prototype/assets/repo/.gitignore`.
