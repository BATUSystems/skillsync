---
title: "Plan done scalony z innej maszyny: brak lokalnego .skillsync/runs to nie przebieg otwarty, dowodem zamknięcia jest śledzony plan z Wynikiem"
tags: [skillsync, hooks, closure, doctor, runs, git, multi-machine]
paths: ['library/hooks/check-closure.py', 'skillsync_workflow/quality.py', 'tests/test_skillsync_closure.py', 'tests/test_skillsync_run_alias.py']
source: "zgłoszenie Agady (plan S8a w VE, 25.11.2026): hak Stop i doctor alarmowały „done bez final” dla planu z innej maszyny; CORE-1170-STOPHOOK"
verified: 2026-11-25
---
**Objaw:** plan `done` scalony z innej maszyny (albo wciągnięty z gałęzi) powoduje w hooku Stop blokadę „Plan done bez zamknięcia przebiegu” z podpowiedzią `checkpoint --final`, a `doctor` ostrzega „run has no `final` record”, choć przebieg zamknięto tam, gdzie go prowadzono.

**Przyczyna:** `.skillsync/runs` jest poza gitem (ignorowany), więc przebieg z innej maszyny nie ma tu żadnego śladu. Kod traktował brak katalogu albo pliku przebiegu jak przebieg otwarty (`run_state` zwracał `open`, `_has_final_record` fałsz). Rekord `final` może zapisać tylko maszyna, która prowadziła przebieg, więc podpowiedź `--final` nic nie naprawia.

**Reguła:** (0) poprawna linia `Zamknięcie:` w nagłówku planu zamyka go niezależnie od `.skillsync/runs` (dowód w gicie, lekcja `dowod-zamkniecia-w-gicie-nie-w-magazynie-lokalnym.md`); bez niej: (1) „otwarty” znaczy: istnieje lokalny ślad przebiegu (`<id>.json`, `<id>.*.json` albo katalog `<id>`) i nie ma w nim `final`; (2) przebieg bez śladu lokalnie nie blokuje i `doctor` go nie zgłasza: sesja, która go nie prowadziła, niczego nie naprawi; hak, gdy plan nie ma niepustego `## Wynik` (szablon `(po wykonaniu)` i same nagłówki `###` nie liczą się), tylko informuje jedną linią „przebieg prowadzony poza tym checkoutem; brak sekcji Wynik w planie”, nigdy podpowiedzią `--final` (decyzja po zgłoszeniu Agady, 25.11); (3) każdy stan wyprowadzany z plików poza gitem musi odróżniać „brak śladu” od „ślad bez dowodu”; (4) `skillsync status` nie ma tego alarmu (`needsHuman: closure` dotyczy tylko planu `in_progress` z odhaczonymi krokami): sprawdza to test w świeżym klonie bez `.skillsync/runs`.

**Jak wykryć:** test odtwarza plan `done` bez katalogu `runs` (hak nie blokuje, `doctor` milczy, `status` bez `needsHuman`) i ten sam plan z lokalnym katalogiem przebiegu bez `final` (hak blokuje i podpowiada `--final`).
