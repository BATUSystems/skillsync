---
title: Instrukcje generowane (CLAUDE.md, AGENTS.md, copilot-instructions.md) i push
tags: [skillsync, instructions, claude, codex, push]
paths: ['skillsync']
source: "odczyt skillsync:build_instructions, build_claude_md, update_codex_entrypoint, strip_merge_preamble, cmd_push 2026-10-02; tryb agents: write_agents_block, agents_block_for_push, spec docs/specs/2026-11-02-agents-md-jedno-zrodlo.md"
verified: 2026-11-02
---
Reguła: instrukcje w projekcie są pochodne. Źródłem są `instructions/_base.md` (+ inne z `instructions.merge`) i szablon projektu `instructions/<template>.md` w bibliotece.
- `.github/copilot-instructions.md` = `merge` + `template` (wymaga `instructions.template`).
- `CLAUDE.md` = stały nagłówek Claude Code + te same części; bez szablonu, ale z `feature` → `_base` + odsyłacz do `.github/copilot-instructions.md`.
- Codex: `.codex/skillsync-instructions.md` (to samo, domyślnie `_base`) + blok `<!-- SKILLSYNC:CODEX-BEGIN/END -->` w `AGENTS.md` odsyłający do niego.
- Tryb wejścia instrukcji: `inline` (domyślny, opis wyżej) albo `agents`: treść `CLAUDE.md` raz w bloku `AGENTS.md` (Codex czyta ją wprost, akapit Codexa w `CODEX-NOTE-*`), `CLAUDE.md` = nagłówek + `@AGENTS.md`, bez `.codex/skillsync-instructions.md`. Codex w `agents` dostaje treść Claude: bez `_base` przy `template` bez `merge` i ze wskazówką w brzmieniu Claude. Tryb awaryjny `inline`, gdy treść mogłaby trafić do współdzielonego `AGENTS.md` (repo git bez ignorowania, własna treść poza gitem), dowiązania albo zepsutych znaczników. Sesje Skippstera doklejają `CLAUDE.md` jako tekst, więc zmiana domyślnego trybu wymaga Skippstera rozwijającego importy.
- `skillsync push` odcina część wspólną po markerze `<!-- SKILLSYNC:BASE-END -->` (z `_base.md`) i zapisuje resztę jako szablon. `CLAUDE.md` i `copilot-instructions.md` zapisują **ten sam** szablon — wygrywa ostatni push.

Dlaczego: jedno źródło prawdy dla trzech hostów; edycja w jednym miejscu (biblioteka) propaguje się wszędzie.

Wyjątki i przykłady: marker `SKILLSYNC:BASE-END` musi zostać w `_base.md` (bez niego `push` używa kruchego, historycznego odcinania prefiksu). Ręczny `CLAUDE.md` bez nagłówka generatora jest pomijany tylko dla projektów bez szablonu.
Pliki generowane są ignorowane przez git: `CLAUDE.md` (i `AGENTS.md`, jeśli utworzył go generator; istniejący `AGENTS.md` zostaje śledzony, a ignorowany jest jego backup) w `.gitignore` projektu, `copilot-instructions.md` w `.github/.gitignore`. Zmienne `{{ klucz }}` podstawia `apply_variables` (`config.variables`, nadpisane `instructions.variables`).
Budżety rozmiaru generowanych plików sprawdza `doctor` (`workflow.context`).

Gdzie w kodzie: `skillsync:build_instructions`, `skillsync:build_claude_md`, `skillsync:build_codex`, `skillsync:strip_merge_preamble`, `skillsync:cmd_push`; tryb `agents`: `skillsync:instructions_entrypoint`, `skillsync:write_agents_block`, `skillsync:agents_md_shareable`, `skillsync:agents_block_for_push`, `skillsync_workflow/quality.py:claude_imports_agents`. Ograniczenie: powrót `agents`→`inline` zostawia w `AGENTS.md.skillsync-backup` pełną treść, jeśli kopii jeszcze nie było (jak przy każdej zmianie bloku w `inline`).
