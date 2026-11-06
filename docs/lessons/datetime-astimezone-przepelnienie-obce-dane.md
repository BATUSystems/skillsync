---
title: "datetime.astimezone rzuca OverflowError na krańcach zakresu: parser obcych danych mapuje go na ValueError"
tags: [skillsync, python, datetime, retro, parsing, tests]
paths: ['skillsync_workflow/retro_scan.py', 'skillsync_workflow/runs.py', 'skillsync_workflow/retro.py', 'tests/test_skillsync_retro_scan.py']
source: 'retro_scan.py:140-148 (_moment); runs.py:37-43 (_recorded_at, łapie OverflowError); retro.py:440-448 (_record_day); raporty code-review-1.2-review-1.md:18-24 i :57, code-review-1.2-review-2.md:14 i :49, 1.2t-mutation.log:43 (M42)'
verified: 2026-11-06
---
**Objaw:** skan `retro-scan` (główny checkout albo `--cross-project`) kończy się tracebackiem zamiast ostrzeżenia
`source-skipped:`. Przyczyną jest `created_at` z obcego pliku `retro-signals.json` z datą przy krańcu zakresu, np.
`0001-01-01T00:00:00+01:00` albo `9999-12-31T23:59:59-01:00`.

**Przyczyna:** `datetime.astimezone(timezone.utc)` przelicza strefę i na krańcu zakresu wychodzi poza `datetime.min` albo
`datetime.max`. Rzuca wtedy `OverflowError`, a nie `ValueError`. Parser (`_parse_run`) łapie tylko `ValueError`, więc
wyjątek przechodzi przez czytnik, a obce źródło przerywa skan.

**Reguła:** w parserze danych z obcych źródeł przeliczaj strefę w `try` i mapuj `OverflowError` na `ValueError`, żeby
przebieg trafił do `unreadable`. Każdy nowy czytnik obcych danych dostaje test „wartość poza zakresem nie przerywa
reszty”, z oboma krańcami daty i z sprawdzeniem, że pozostałe przebiegi i pary dalej się liczą.

**Jak wykryć:**
- `grep -n "astimezone" skillsync_workflow/` i sprawdzenie, czy wywołanie jest w `try`, które łapie `OverflowError`
  albo jest pod parserem łapiącym `ValueError`. Przykład poprawny: `retro_scan.py:_moment`.
- Test na skrajnym `created_at` w obcym projekcie (`--cross-project`) oraz w głównym checkoucie: wynik to `PASS`
  z ostrzeżeniem, nie traceback.
- Stan bieżący: wszystkie trzy czytniki łapią `OverflowError`: `retro_scan.py:_moment`, `runs.py:_recorded_at`
  (używany też przez `retrospective`) i `retro.py:_record_day`. Sprawdzenie: `grep -n "OverflowError" skillsync_workflow/retro*.py skillsync_workflow/runs.py`.
- Testy graniczne dat 0001 i 9999 (oba krańce): `tests/test_skillsync_retro.py`, `tests/test_skillsync_retro_scan.py`
  (`BAD_TIMES`) i `tests/test_skillsync_workflow_runs.py`. Nowy czytnik obcych danych dodaje taki sam test.
