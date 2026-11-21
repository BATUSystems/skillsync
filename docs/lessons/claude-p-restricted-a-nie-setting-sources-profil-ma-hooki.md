---
title: 'claude -p: do zadań tylko do odczytu użyj --restricted, nie --setting-sources user; flagi, których pomoc nie pokazuje albo opisuje inaczej'
tags: [skillsync, claude-code, claude-p, bezpieczenstwo, hooki, json-schema]
paths: ['tools/claude-headless/run.py', 'tools/claude-headless/claude_headless_core.py', 'tests/test_skillsync_claude_headless.py', 'docs/development.md']
source: 'CLAUDE-6, próby F0 z 2026-11-21 na Claude Code 2.1.318: 8 wywołań claude -p (haiku, konto prywatne, tymczasowy HOME i katalog roboczy)'
verified: 2026-11-21
---
**Objaw:** plan zakładał, że `--setting-sources user` wystarczy, by zadanie `claude -p` nie ładowało hooków projektu, a `--allowedTools Read,Grep,Glob` ograniczy narzędzia do odczytu; `--max-turns` miał być wymuszany flagą, której `claude --help` nie pokazuje; schemat miał być przekazany plikiem.

**Przyczyna:** (1) `--setting-sources user` ładuje właśnie ustawienia profilu użytkownika: jego hook `SessionStart`, wtyczki i reguły `permissions.allow` z `Bash(...)`. Hook projektu uruchamia się w trybie `-p` bez `--restricted` (kontrola pozytywna z markerem), a z `--restricted` nie uruchamia się (marker nie powstaje); `--restricted` ignoruje ustawienia user, project i local, usuwa narzędzia wykonujące kod i ogranicza narzędzia plikowe do katalogów roboczych (odczyt poza katalogiem roboczym: `DENIED`). (2) `--allowedTools` tylko auto-zezwala; zestaw narzędzi ogranicza `--tools`. (3) `--max-turns` istnieje i działa, choć pomoc go nie wymienia: po limicie wynik ma `subtype: error_max_turns` i kod 1. (4) `--json-schema` bierze schemat inline, nie ścieżkę; poprawny wynik jest w polu `structured_output`, a przy schemacie niemożliwym do spełnienia `claude` kończy sukcesem (kod 0) bez tego pola, tylko z tekstem. (5) Przekroczenie `--max-budget-usd` daje `subtype: error_max_budget_usd` i kod 1; budżet jest sprawdzany po turze, więc koszt może przekroczyć limit o jedną turę. (6) Flagi `--tools`, `--allowedTools` i `--mcp-config` są wariadyczne, więc prompt nie może iść pozycyjnie po nich: przekazuj go przez stdin.

**Reguła:** zadanie nieinteraktywne tylko do odczytu uruchamiaj z `--restricted --tools Read,Grep,Glob --strict-mcp-config`, z tymczasowym `HOME` i profilem tylko przez `CLAUDE_CONFIG_DIR`; wynik waliduj samodzielnie (brak `structured_output` przy sukcesie to wynik niezgodny); wywołania księguj w księdze konta, a koszt nieznany licz jako limit wywołania. `--bare` odpada na subskrypcji (wymaga klucza API).

**Jak wykryć:** próba w katalogu tymczasowym z hookiem-markerem w `.claude/settings.json`: z `--restricted` marker nie powstaje, bez niego (z `--setting-sources project`) powstaje; kontrolę pozytywną rób zawsze, inaczej brak markera niczego nie dowodzi.
