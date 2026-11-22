---
title: "Alias w regexie fail-closed (bramka prototypu) wymaga przestawienia wykrywania literówek: sam alias zostawia fail-open"
tags: [skillsync, prototype, gate, regex, aliases, fail-closed, tests]
paths: ['skillsync_workflow/prototype.py', 'tests/test_skillsync_prototype*.py']
source: "spec paczki CORE-1130-I18N-ALIASES (22.11, zatwierdzony), ustalenie 12 (prototype.py:3178-3183: GATE_PROTOTYPE_LINE, GATE_BYPASS_LINE, _NEAR_PROTOTYPE, _NEAR_BYPASS; stan @ ccc01eb)"
verified: 2026-11-22
---
Objaw: w planie linia obejścia bramki zapisana po angielsku z błędem (np. data w złym formacie) przechodziła bramkę jako `PASS` bez komunikatu. Polskiej linii z tym samym błędem nie przepuszczano.
Przyczyna: wykrywanie bliskich literówek (`_NEAR_PROTOTYPE`, `_NEAR_BYPASS`) znało tylko polskie formy. Linia, która nie pasowała do wzorca i nie była „bliska”, była po cichu ignorowana. Dopisanie samego aliasu do wzorca obejścia nie zmieniało tej gałęzi.
Reguła: dodając alias do regexu fail-closed, przestaw jednocześnie wykrywanie bliskich form, aby poprawny alias był przyjmowany, a literówki w obu językach dalej odrzucane (`BLOCKED`: `invalid_prototype_line`, `invalid_bypass_line`). Test musi objąć trzy przypadki: poprawny alias przyjęty; literówka w aliasie odrzucona; literówka w polskiej formie odrzucona.
Jak wykryć: w `tests/test_skillsync_prototype*.py` szukaj par: poprawna linia angielska i błędna linia angielska. Ręcznie: `skillsync prototype gate --plan <plan>` na planie z linią `Prototype gate: skipped — …` i złą datą; oczekiwane `BLOCKED` `invalid_bypass_line`, nie `PASS`.
Stan po review (runda 1, 22.11): każda linia nagłówka planu (przed pierwszym `## `, bez `#` na początku) zaczynająca się od `Prototype gate` albo `Bramka prototypu`, która nie jest pełną linią obejścia (także angielska proza), daje `BLOCKED` `invalid_bypass_line` (`prototype.py:3186` `_NEAR_BYPASS`, `:3296-3298`). Test punktowy: `tests/test_skillsync_prototype_gate_alias.py`.
