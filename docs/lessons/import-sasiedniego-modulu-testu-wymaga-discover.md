---
title: 'Test importujący sąsiedni moduł testów pada przy `python3 -m unittest tests.<moduł>`: uruchamiaj punktowo przez discover albo z PYTHONPATH=tests'
tags: [skillsync, tests, unittest, imports, agents]
paths: ['tests/test_skillsync_retro.py', 'tests/test_skillsync_workflow.py']
source: "tests/test_skillsync_retro.py:19 (`from test_skillsync_workflow import CLI, WorkflowFixture`); raport przebiegu CORE-SPECSTORE F3.1 (19.11, odkrycie: ImportError przy `python3 -m unittest tests.test_skillsync_retro`)"
verified: 2026-11-19
---
**Objaw:** `python3 -m unittest tests.test_skillsync_retro` kończy się `ImportError` (brak modułu `test_skillsync_workflow`), zanim wykona się którykolwiek test. Bieg wygląda jak regresja, a nią nie jest.

**Przyczyna:** moduł testu importuje sąsiedni plik testowy jako górny poziom (`from test_skillsync_workflow import ...`). Przy `python3 -m unittest tests.<moduł>` katalog `tests/` nie jest na `sys.path`. `discover -s tests` dodaje go, więc ten sam test przechodzi.

**Reguła:**
- Punktowe uruchomienie modułu testu, który importuje sąsiada: `python3 -m unittest discover -s tests -p 'test_skillsync_retro.py'` albo `PYTHONPATH=tests python3 -m unittest tests.test_skillsync_retro`.
- Nie „naprawiaj” importu w teście samą zmianą ścieżki bez sprawdzenia, czy pozostałe moduły mają ten sam wzorzec.
- Wynik `ImportError` przy uruchomieniu punktowym nie jest dowodem zaliczenia ani porażki testu; zapisz go jako NOT_RUN z komendą.

**Jak wykryć:**
- `grep -rn "^from test_skillsync" tests/` pokazuje moduły zależne od `sys.path` z katalogu `tests/`.
- `python3 -m unittest tests.test_skillsync_retro` powtarza błąd; `PYTHONPATH=tests` go usuwa.
