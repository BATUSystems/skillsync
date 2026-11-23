---
title: "Format `lines` w `test-run`: linie `PASS <nazwa>` / `FAIL <nazwa>` w kolumnie 0, jawny wybór, gramatyka i gwarancje recordera"
tags: [skillsync, test-run, adapters, lines, scripts, output, evidence]
paths: ['skillsync_workflow/test_adapters/lines.py', 'skillsync_workflow/test_run.py', 'tests/test_skillsync_adapter_lines.py', 'tests/test_skillsync_adapter_contract.py']
source: "odczyt skillsync_workflow/test_adapters/lines.py (RESULT, BARE, SEPARATOR_CHARS, MAX_*, tools/explicit_only/live, feed, _detail, end); test_run.py `_finish` (gałąź kodu 0 z porażkami); spec paczki CORE-1140-T3 (ustalenia 1–3, gramatyka); testy tests/test_skillsync_adapter_lines.py i tests/test_skillsync_test_run_lines.py"
verified: 2026-11-22
---
Reguła: adapter `lines` zlicza linie wyniku, które skrypt kontrolny drukuje w kolumnie 0: `PASS <nazwa>` albo `FAIL <nazwa>`. Nie ma narzędzia przypisanego do `auto` (`tools = ()`, `explicit_only = True`), więc działa tylko przy `--format lines`. `live = 'completion'`: wynik jest emitowany od razu po linii, a stan „oczekuje” nie jest sztuczny. Adapter nie czyta raportu pliku ani stderr.

Gramatyka wyniku (po zdjęciu ANSI i końcowych białych znaków, `\r` włącznie):
- kotwica na kolumnę 0 i wielkość liter znacząca: tylko `PASS` i `FAIL`;
- po słowie dokładnie jeden separator: spacje/tabulatory, albo dwukropek, myślnik, półpauza, pauza (z opcjonalnymi spacjami); reszta linii to nazwa;
- goły `PASS` / `FAIL` jest wynikiem o nazwie `(bez nazwy)`;
- nazwa złożona wyłącznie z separatora (`PASS -`) nie jest wynikiem.

Nie są wynikami: `ALL PASS (…)`, `SOME FAIL (…)`, `SUMMARY …`, `PASSED`, `FAILED`, `Pass`, `pass:`, linie wcięte. `SKIP` / `PENDING` nie są rozpoznawane (poza zakresem paczki CORE-1140-T3).

Limity: `MAX_LINE` 2000 (dłuższe linie pomijane), `MAX_NAME` 300 (nazwa w id), `MAX_SEEN` 50 000 (duplikaty ponad limit nie są rozróżniane), komunikat porażki: maks. 10 niepustych linii i 500 znaków.

Komunikat porażki: niepuste linie po `FAIL` do następnej linii wyniku. Porażka jest emitowana od razu, a po końcu bloku drugi raz z komunikatem; maskę nakłada recorder.

Kod wyjścia (rozstrzyga recorder, nie adapter):
- kod 0 bez żadnej linii wyniku: `BLOCKED` (2), bo nie ma dowodu;
- kod ≠ 0: `FAIL` z kodem polecenia;
- kod 0 z linią `FAIL`: `BLOCKED` z powodem o niepowodzeniach (jak `tap` z `not ok`); recorder nie zgaduje, który z dwóch sygnałów jest prawdziwy.

Słownik:
- **lines**: adapter linii wyniku wybierany tylko jawnie; **nie oznacza** raportu tekstowego ani formatu TAP (`ok N - …`, zob. `node-test-formaty.md`).
- **kolumna 0**: wynik nie może być wcięty; linia wcięta z `PASS` to szczegół kontroli, nie wynik.
- **zagnieżdżone narzędzie**: gramatyka liczy linie `PASS`/`FAIL` w kolumnie 0 także z wyjścia narzędzi uruchamianych przez skrypt (np. `FAIL: test_x (m.C)` z unittest, gołe `PASS`/`FAIL` z `go test`); skrypt kontrolny przepuszczający takie wyjście powinien je filtrować albo wcinać.

Jak sprawdzić: `skillsync test-run <projekt> --format lines -- <skrypt>` na skrypcie drukującym `PASS`, `FAIL` i podsumowanie `ALL PASS`: liczniki mają zawierać tylko linie wyników, a źródło w `end` ma być `adapter`, nie `regex`.
