---
title: 'link przepisywał cały .gitignore projektu: dopisuj do plików użytkownika bez zmiany istniejących bajtów'
tags: [skillsync, windows, gitignore, crlf, link]
paths: ['skillsync_workflow/cli.py', 'tests/test_skillsync_gitignore_eol.py']
source: 'WIN-2: projekt na natywnym Windows z core.autocrlf=true i plikiem .gitignore z mieszanymi końcami linii (i/mixed); dowód na Windows 5.11: stary kod zwiększył plik z 25 do 44 bajtów i zamienił CRLF na LF, nowy dopisał tylko dwie linie w stylu CRLF'
verified: 2026-11-09
---
**Objaw:** po `skillsync link` diff `.gitignore` obejmował cały plik zamiast dopisanych linii: `read_text().splitlines()` gubiło końce linii, a `write_text_lf` zapisało całość z LF.

**Przyczyna:** zapis „dopisz do pliku użytkownika” przechodził przez odczyt tekstowy i zapis całego pliku z `\n`.

**Reguła:** dopisuj przez `add_gitignore_entries` (bajty istniejącej treści bez zmian, nowe linie w stylu ostatniej linii pliku, brak końcowego nowego wiersza uzupełniony, plik nowy albo pusty = LF). Każde nowe miejsce, które dopisuje do pliku użytkownika, używa tej funkcji albo zachowuje bajty tak samo; testy `tests/test_skillsync_gitignore_eol.py` pilnują CRLF, mieszanych końców i bajtów spoza UTF-8.

**Znane luki tej samej klasy (osobne kroki):** `AGENTS.md` i `.codex/config.toml` użytkownika (odczyt tekstowy, zapis z LF, kopia zapasowa też z LF), pliki JSON (`tasks.json`, `settings.json`, `.mcp.json`, przepisywane w całości); `prototype.py:_ensure_lines` dopisuje zawsze `\n`.

**Jak wykryć:** `git diff --stat` na `.gitignore` po `link` w projekcie z CRLF; `git ls-files --eol .gitignore` (`i/mixed`).
