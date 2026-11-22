---
title: 'Test z celowo wrogimi nazwami łamie strażnika ścieżek tmp dopiero w pełnym biegu; obiekty git poza bazą wymagają alternatywy'
tags: [skillsync, tests, tmp-path-guard, git, alternates, test-gate]
paths: ['tests/test_skillsync_tmp_path_guard.py', 'tests/test_skillsync_test_gate.py', 'skillsync_workflow/test_gate.py', 'docs/development.md']
source: "pełny bieg Linux na b5c1024 (7340 testów, 1 FAIL tmp_path_guard.test_strict na test_gate.py), poprawka 8461e43; test_gate.work_tree_id z GIT_OBJECT_DIRECTORY"
verified: 2026-11-21
---
**Objaw:** testy punktowe zielone, pełny bieg czerwony: `tmp_path_guard.test_strict` wskazał `Path(tmp) / name` w nowym teście ze ścieżkami
ze spacją, dwukropkiem i cudzysłowem.

**Przyczyna:** strażnik skanuje wszystkie testy i nie odróżnia nazwy z literału od danych; `tmp_paths.child` odrzuca dwukropek i cudzysłów
(`UnsafeName`), więc nie służy testowi, który ma sprawdzić właśnie takie ścieżki.

**Reguła:**
- Nowy test składający ścieżkę tmp z celowo wrogimi nazwami dostaje wpis `ALLOWED` w `test_skillsync_tmp_path_guard.py` z uzasadnieniem (jak `test_run_hardening`).
- Po dodaniu lub zmianie testu ze ścieżką tmp uruchom punktowo także moduł strażnika, nie tylko moduł zmienianego testu.

**Obiekty git poza bazą (`work_tree_id`):**
- Tymczasowy indeks z osobnym `GIT_OBJECT_DIRECTORY` musi dołączać obiekty repozytorium przez `GIT_ALTERNATE_OBJECT_DIRECTORIES`: separator `os.pathsep`,
  ścieżka ze znakiem specjalnym w cudzysłowie `"` (unescape w stylu C). Bez tego `read-tree HEAD` nie znajduje obiektów.
- Katalog obiektów bierz z `git rev-parse --git-path objects` (linked worktree i klon `--shared` mają inny układ).
- Test „liczba obiektów bez zmian” liczy PRZED `legacy_id`, bo stara implementacja na kopii linked worktree pisze do wspólnego repozytorium.
- Pole `end.changed` pochodzi z pliku zdarzeń (wejście niezaufane): tylko do tekstu komunikatu i maskowane; do gita trafiają wyłącznie id drzew po regexie.
