---
title: "except Exception na granicy obcego źródła wymaga testu z wyjątkiem spoza (OSError, ValueError)"
tags: [skillsync, python, exceptions, retro, tests]
paths: ['skillsync_workflow/retro_scan.py', 'skillsync_workflow/retro.py', 'tests/test_skillsync_retro_scan.py', 'tests/test_skillsync_retro.py']
source: 'retro_scan.py:460 (_main_source), :516 (_cross_project); retro.py:917 (_project_entry), :966 (_retro_all); raporty code-review-1.2-review-1.md:20, code-review-1.2-review-2.md:24-27 (MINOR-5), :52 (wzorzec); 1.2t-test-writer.md:70 (testy M50-M52)'
verified: 2026-11-06
---
**Objaw:** jeden obcy przebieg albo projekt z nietypowym wyjątkiem (np. `OverflowError`, `RuntimeError`) przerywa cały
`retro --all` albo `retro-scan`. Wyjątek wychodzi, bo łapano tylko `(OSError, ValueError)`.

**Przyczyna:** granica obcego źródła łapała węziej, niż wymaga izolacja projektów. Wcześniejszy `except Exception`
przy `_project_entry` chronił przed tym, ale pętla pary (K8) i czytniki worktree tego nie miały. Zawężenie
`except Exception` do `(OSError, ValueError)` przy późniejszej zmianie cofa tę ochronę bez sygnału.

**Reguła:**
- Na granicy obcego źródła (główny checkout, inny projekt, pętla par) `except Exception` jest dopuszczalny, jeśli
  zwraca stały stan: źródło pominięte z ostrzeżeniem albo `*_truncated`. Nie zwracaj treści wyjątku.
- Każdy taki `except Exception` ma test z wyjątkiem spoza `(OSError, ValueError)`, np. `RuntimeError`. Test sprawdza,
  że wynik jest `PASS`, że pozostałe źródła się liczą i że flaga obcięcia jest ustawiona.
- Lokalne źródło (własny magazyn) nie dostaje szerokiego łapania: błąd tam ma być widoczny jako `BLOCKED`.

**Jak wykryć:**
- `grep -n "except Exception" skillsync_workflow/retro*.py`: każde miejsce ma komentarz o granicy i test.
- Mutacja: zawężenie wyjątku do `(OSError, ValueError)` w każdym z czterech miejsc musi zabić test
  (`UnexpectedForeignErrors`, `LibraryPairs.test_unexpected_error_of_the_reader_marks_the_pairs_truncated_and_passes`).
