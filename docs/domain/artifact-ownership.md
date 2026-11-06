---
title: Własność artefaktów generowanych w projektach
tags: [skillsync, artifacts, ownership, link]
paths: ['skillsync', 'tests/test_skillsync*.py']
source: odczyt skillsync (place, prune_managed, write_managed_text, build_*) 2026-10-02
verified: 2026-10-02
---
Reguła: generator zmienia w projekcie wyłącznie to, co sam wytworzył albo co jest jednoznacznie linkiem do biblioteki. Zakres zależy od miejsca:

| Miejsce | Zasada |
|---|---|
| `.github/skills/` | **cały katalog należy do skillsync** — każdy niepotrzebny symlink lub katalog jest usuwany (`prune_managed(owned=True)`) |
| `.claude/skills/`, `.claude/agents/`, `.agents/skills/` | usuwane/zastępowane są tylko symlinki do biblioteki i nietknięte kopie z pliku stanu; własne pliki zostają (`[warn] Skipping ...`) |
| Pliki pochodne kopiowane (`.codex/agents/*.toml`, `.codex/skillsync-instructions.md`) | zapis tylko, gdy plik nie istnieje lub jest nietknięty względem stanu; lokalna edycja → `[conflict] Preserving local file` (`write_managed_text`) |
| `.claude/settings.json`, `.codex/config.toml`, `AGENTS.md`, `.vscode/tasks.json`, `.mcp.json` | scalane punktowo (hook Stop / blok `SKILLSYNC:*` / task `skillsync: link` / serwery pochodne); reszta pliku nietykalna; backup `*.skillsync-backup`: `AGENTS.md` i `config.toml` — tylko gdy backup jeszcze nie istnieje (pierwsza zmiana); `settings.json` i `tasks.json` — przy każdym zapisie, nadpisuje poprzedni (`merge_claude_settings`, `ensure_link_on_open`) |
| `CLAUDE.md` | generowany; ręczny plik bez nagłówka generatora jest pomijany tylko, gdy projekt nie ma szablonu; w trybie `agents` tylko import `@AGENTS.md` |
| `.cursor/rules/*.mdc` | każdy `*.mdc` spoza `base`/`project`/skilli jest uznawany za przestarzały i **usuwany** (gdy skille Cursora włączone) |

Dlaczego: projekty trzymają własne role, skille i ustawienia obok generowanych; jedyne bezpieczne kryterium to „link do biblioteki albo hash zgodny ze stanem”.

Wyjątki i przykłady: zmiana nazwy skilla → stary link znika przy `link` (`[clean] Removed obsolete`), ale wpis `skills/<stara>` w `.claude/.gitignore` i `.agents/.gitignore` zostaje (martwy, nieszkodliwy).
Symlinkowany `AGENTS.md` albo `.codex/config.toml` są pomijane z ostrzeżeniem. Nieprawidłowy JSON w `.claude/settings.json` lub `.mcp.json` → `[error]` i pominięcie, nigdy nadpisanie.

Gdzie w kodzie: `skillsync:place`, `skillsync:prune_managed`, `skillsync:write_managed_text`, `skillsync:build_cursor_rules`, `skillsync:merge_claude_settings`.
