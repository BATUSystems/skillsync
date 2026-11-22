---
title: "Adapter linii wyniku musi kotwiczyć na kolumnie 0 i odrzucać podsumowania; skrypt z kodem 0 i linią FAIL to BLOCKED, nie PASS"
tags: [skillsync, test-run, adapters, lines, evidence, exit-codes, scripts]
paths: ['skillsync_workflow/test_adapters/lines.py', 'skillsync_workflow/test_run.py', 'tests/test_skillsync_adapter_lines.py']
source: "'projekt syntetyczny: skrypt drukujący `PASS a`, `FAIL b`, `ALL PASS (x)`; odczyt lines.py l. 26 (RESULT z kotwicą `^`), l. 50–52 (wynik odrzucany, gdy nie pasuje do kolumny 0), test_run.py `_finish` l. 636–643 (kod 0 z porażkami → BLOCKED); spec paczki CORE-1140-T3 (ustalenia 3 i ryzyka); ryzyko z adaptera tap (cli.md, wiersz test-run: kod 0 z `not ok` = BLOCKED); bez uruchamiania testów w kroku S3"
verified: 2026-11-22
---
**Objaw:** skrypt kontrolny (np. powłokowy `*_checks.sh`) drukuje po jednej linii na kontrolę `PASS <nazwa>` albo `FAIL <nazwa>`. Dla takiego skryptu `test-run` nie ma adaptera, więc projekt dopisywał do skryptu udawane podsumowanie w stylu pytest (`N passed in X s`), żeby `test-run` miał liczbę. Wyniku nie widać po samym kodzie: skrypt z kontrolą `FAIL` mógł zakończyć się kodem 0.

**Przyczyna:** bez adaptera liczniki pochodzą z luźnego odczytu całego wyjścia albo z kodu wyjścia. Wzorzec bez kotwicy na początku linii liczy też podsumowanie skryptu (`ALL PASS (…)`, `SOME FAIL (…)`) jak test. Kod 0 mówi tylko, że skrypt się zakończył, a nie że każda kontrola przeszła.

**Reguła:**
1. Adapter linii wyniku kotwiczy na kolumnie 0 (wzorzec z `^`, także bez wcięć) i rozpoznaje tylko wyniki zgodne z gramatyką; podsumowania `ALL PASS`, `SOME FAIL`, `SUMMARY` oraz linie wcięte nie są wynikami. Bez kotwicy podsumowanie skryptu liczy się jak test.
2. Skrypt, który kończy się kodem 0 mimo linii `FAIL`, jest `BLOCKED` (kod 2), nie `PASS`. Kod 0 zdejmuje tylko dowód braku porażek, nie porażki z wyjścia.
3. Adapter nie rozstrzyga takiego konfliktu sam: liczniki i status rozstrzyga recorder, a adapter tylko zgłasza to, co przeczytał.

**Jak wykryć:**
- Test `tests/test_skillsync_adapter_lines.py`: przypadki negatywne (`ALL PASS (x)`, `SOME FAIL (x)`, `SUMMARY`, `Pass`, `pass:`, linia wcięta z `PASS`) muszą dać zero testów; przypadek z `FAIL` i kodem 0 musi dać `BLOCKED`.
- Mutacja: zdjęcie kotwicy `^` z wzorca w `lines.py` (l. 26) musi zaczerwienić przypadki negatywne. Jeśli testy zostają zielone, nie chronią przed podsumowaniem skryptu.
- Ręcznie na skrypcie syntetycznym: `skillsync test-run <projekt> --format lines -- ./skrypt.sh`, gdzie skrypt drukuje `PASS a`, `FAIL b`, `ALL PASS (x)` i kończy się kodem 0. Oczekiwane: liczniki tylko z dwóch linii wyniku, status `BLOCKED` (kod 2), a w zdarzeniu `end` źródło `adapter`, nie `regex`.
- Gdy w `end` widać źródło `regex` przy `--format lines`, adapter nie zgłosił wyników, więc bieg wrócił do luźnego odczytu. Sprawdź wtedy, czy skrypt ma linie w kolumnie 0.
