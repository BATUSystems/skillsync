---
title: 'Izolacja HOME w środowisku gita ucina safe.directory: rozpoznanie checkoutu po błędzie gita daje fail-open, a nie BLOCKED'
tags: [skillsync, git, worktree, security, spec-snapshot, tests]
paths: ['skillsync_workflow/spec_snapshot.py', 'skillsync_workflow/specs.py', 'tests/test_skillsync_spec_snapshot.py', 'tests/test_skillsync_specs.py']
source: "skillsync_workflow/spec_snapshot.py:51-55 (`_git_env`, docstring), :86-92 (`_checkout_kind`); tests/test_skillsync_spec_snapshot.py:490 (`test_git_failure_in_linked_checkout_is_fail_closed`), :498 (`test_dangling_git_file_is_blocked_and_missing_git_is_not`); raport przebiegu CORE-SPECSTORE F3.3 (poprawka MAJOR-1, 2026-11-19)"
verified: 2026-11-19
---
**Objaw:** w połączonym worktree `skillsync spec snapshot --check` mógł zwrócić `PASS`, mimo że git nie potrafił ustalić, czy katalog jest worktree. Przyczyną była konfiguracja, a nie stan plików: git odmawiał pracy z powodu `dubious ownership`.

**Przyczyna:** środowisko gita z `HOME=/nonexistent` i `XDG_CONFIG_HOME=/nonexistent` ukrywa konfigurację globalną, a `safe.directory` żyje tylko w niej. Polecenie `rev-parse` kończyło się błędem, a kod traktował błąd jak brak `.git` (typ `none`), więc sprawdzenie przepuszczało połączony worktree.

**Reguła:**
- Do ustalenia typu checkoutu (`rev-parse`) używaj środowiska z prawdziwym `HOME` i konfiguracją użytkownika, bez zmiennych `GIT_*`. To robi `_git_env` w `spec_snapshot.py`.
- Gdy `.git` istnieje, a git zawodzi, wynik to `BLOCKED` (fail-closed), nigdy `none` ani `PASS`.
- Izolację `HOME` stosuj tylko tam, gdzie wynik ma nie zależeć od maszyny: sonda `check-ignore` w `specs.py` (`_git_env` z `GIT_CONFIG_GLOBAL=/dev/null`). Błąd tej sondy oznacza `source: default`, czyli brak reguły, a nie cichą zmianę trybu.
- Nie przenoś izolacji `HOME` do kodu, który odpowiada na pytanie „czy to worktree”.

**Jak wykryć:**
- `python3 -m unittest tests.test_skillsync_spec_snapshot` (grupa testów połączonego worktree, w tym `test_git_failure_in_linked_checkout_is_fail_closed`).
- Ręcznie: w worktree z zepsutym `.git` (plik `gitdir:` wskazujący nieistniejący katalog) `spec snapshot --check --to <kopia>` daje `BLOCKED`, a nie `PASS`.
