---
title: "Ostrzeżenie o braku `link` nie może zależeć od niczego, co instaluje `link` (hak, wygenerowane instrukcje)"
tags: [skillsync, link, hook, worktree, doctor, route, instrukcje]
paths: ['skillsync_workflow/linkage.py', 'library/hooks/session-context.py', 'tests/test_skillsync_linkage.py']
source: 'przypadek z terenu 20.11.2026 (uczestnik zewnętrznego hackathonu, Windows, bez usługi watch: skill z manifestu niedostępny, sesja nie wiedziała dlaczego); skillsync_workflow/cli.py `ensure_root_gitignore` i `ensure_gitignore` (CLAUDE.md, AGENTS.md, copilot-instructions.md, skills/ i .skillsync.yaml są domyślnie ignorowane); library/hooks/session-context.py (hak instalowany przez link do ignorowanego .claude/settings.local.json)'
verified: 2026-11-23
---
**Objaw:** świeży klon albo `git worktree add` nie ma zainstalowanych skilli z manifestu (`/feature` nie istnieje), a sesja nie dostaje żadnej wskazówki, że trzeba uruchomić `skillsync link`.

**Przyczyna:** wszystko, co mogłoby ostrzec, instaluje albo generuje sam `link`: hak `session-context` (do ignorowanego `.claude/settings.local.json`), wygenerowane `CLAUDE.md` i `AGENTS.md` (domyślnie w `.gitignore`), katalogi skilli. Przed pierwszym `link` ich po prostu nie ma.

**Reguła:**
- Ostrzeżenie o braku `link` umieszczaj tam, gdzie działa bez `link`: w poleceniach CLI (`doctor`, `route`, jeden wiersz na stderr w pozostałych), nie w haku ani w wygenerowanych instrukcjach. Hak pokrywa tylko stan „link był, potem przybył skill”.
- Sprawdzenie ma być tanie (`exists`/dowiązanie i jedno `git ls-files -s` na host, bez hashowania), bo biegnie przy każdym poleceniu; oczekiwany zbiór bierz z tej samej funkcji co `doctor` (skill bez źródła w warstwach, wyłączony hostem lub wykluczony nie wchodzi do oczekiwanych), inaczej alarm „uruchom link” bywa stały i fałszywy.
- Wiersz na stderr nigdy nie zmienia stdout, `--json` ani kodu wyjścia i ma wyłącznik `SKILLSYNC_NO_LINK_NOTICE=1`.

**Jak wykryć:** `skillsync doctor` (zbiorczy błąd `Host <host>: N of M skills from the manifest are not linked`), `skillsync route <projekt> --json` (`linkage`, `linkageNote`), testy `tests/test_skillsync_linkage.py`.

**Granice aktualności:** stan z 1.15.0; zakłada, że `link` nadal instaluje skille poza gitem.
