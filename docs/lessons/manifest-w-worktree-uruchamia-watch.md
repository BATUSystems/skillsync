---
title: 'Kopia manifestu w worktree sprawia, że usługa watch linkuje worktree'
tags: [skillsync, worktree, watch, manifest, doctor, dispatch]
paths: ['skillsync', 'skillsync-ctl', 'skillsync_workflow/quality.py']
source: '.skillsync/runs/2026-11-03-prototype-cli (P2-prototype-cli, 2026-11-04): logs/baseline-doctor.json (73 findings) vs logs/final-doctor-1.json (5); AGENTS.md i CLAUDE.md w worktree z 00:19; decyzja Dyspozytora D9'
verified: 2026-11-04
---
**Objaw:** w worktree paczki, do którego skopiowano `.github/.skillsync.yaml` (reguła z `checkpoint-w-worktree-wymaga-manifestu.md`), po pewnym czasie pojawiają się `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.github/skills/` i inne artefakty `link`, a `skillsync doctor` w tym worktree nagle zgłasza dużo mniej uwag niż na początku (u nas 73 → 5), choć kod paczki tego nie zmienił.

**Przyczyna:** worktree leży pod katalogiem przeszukiwanym przez `discover_paths`, więc z manifestem staje się zwykłym projektem; uruchomiona usługa `skillsync-watch` linkuje go przy najbliższej zmianie biblioteki. Pliki są ignorowane przez `.gitignore`, więc nie trafiają do commita, ale zmieniają wynik `doctor` i stan worktree w trakcie paczki.

**Reguła:** baseline `doctor` w worktree porównuj z wynikiem końcowym po kluczu (poziom, ścieżka, powód) i osobno wymieniaj uwagi zniknięte przez podlinkowanie; za dowód braku regresji uznawaj tylko nowe uwagi. Wygenerowanych plików nie usuwaj ręcznie (to pliki generowane); worktree zostaje podlinkowany do końca paczki. Przy ocenie efektów ubocznych pamiętaj, że kopia manifestu to też zgoda na linkowanie przez watch.

**Jak wykryć:** w worktree pliki `AGENTS.md`/`CLAUDE.md` nowsze niż kopia manifestu; w porównaniu doctora uwagi „Missing installed skill”/„Missing routed instruction” znikają bez zmian w kodzie.
