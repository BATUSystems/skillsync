---
title: 'Checkpoint nie działa w worktree bez manifestu, a sam przebieg znika razem z worktree'
tags: [skillsync, checkpoint, worktree, dispatch, manifest]
paths: ['skillsync_workflow/runs.py', 'skillsync_workflow/common.py', 'tests/test_skillsync_retro.py']
source: plan zadania (poza rdzeniem) (F9, N4, K11); skippster plan zadania (poza rdzeniem) (plan 44/44); library/skills/feature/references/worker-mode.md w bibliotece; tests/test_skillsync_retro.py (WorktreeFlow)
verified: 2026-10-31
---
**Objaw:** `skillsync checkpoint` w worktree paczki kończy się błędem (exit 2), więc karta nie zapisuje rekordów, a raport `retro` daje `no_data`. Przebieg `poc-a` skończył 44 kroki bez ani jednego checkpointu.

**Przyczyna:** `.github/.skillsync.yaml` jest w części projektów ignorowany przez git, więc `git worktree add` go nie kopiuje. `load_project` szuka manifestu tylko w katalogu podanym w poleceniu.

**Reguła:** manifest kopiuje karta po `git worktree add`, wyłącznie gdy `ls-files --error-unmatch` kończy się błędem i pliku brak, bez nadpisywania, z kontrolą `check-ignore`. Przed `REPORT DONE` i usunięciem worktree kopia przebiegu trafia do głównego repo; odbiór sprawdza `retro` tam. Procedura jest właścicielem tej reguły: zmiana `load_project` ma zbyt duży zasięg (N4).

**Jak wykryć:** `retro` z `no_data` albo brakiem przebiegu w głównym repo; test końca do końca `WorktreeFlow`.
