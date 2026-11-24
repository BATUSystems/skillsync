---
title: "Hak z argumentem JSON ze ścieżką Windows: Git bash gubi backslashe, a hak fail-open milczy, więc test przez powłokę na Windows jest jedynym dowodem"
tags: [skillsync, windows, git-bash, hooks, fail-open, json, cli]
paths: ['skillsync_workflow/selfcmd.py', 'skillsync_workflow/cli.py', 'skillsync_workflow/hooksync.py', 'library/hooks/check-closure.py', 'tests/test_skillsync_closure.py']
source: "powtórka macierzy Windows (laptop z Windows) 24.11.2026 (INT de57f5d4): dwa testy „through the shell” w test_skillsync_closure czerwone na 3.12 i 3.11; diagnoza w kopii testowej: sys.argv haka miał pojedyncze backslashe w JSON-ie --cli"
verified: 2026-11-24
---
**Objaw:** hak (Stop, PostToolUse, wcześniej też session-context) uruchomiony z wygenerowanego wpisu na Windows nic nie robił: bez blokady, bez komunikatu, kod 0. Testy wywołujące skrypt bezpośrednio były zielone; czerwone były tylko testy uruchamiające wpis przez `sh -c`.

**Przyczyna:** wpis niesie `--cli '<JSON argv>'` z ścieżkami Windows. JSON ma `C:\\Tools\\...` (poprawny), ale Git bash (msys) przy przekazaniu argumentu do natywnego programu gubi jeden poziom backslashy, więc hak dostaje `C:\Tools\...` (niepoprawny JSON), `json.loads` rzuca wyjątek, a hak jest fail-open i milknie. Nic nie wskazuje na błąd.

**Reguła:** (1) argument powłoki niosący ścieżkę Windows zapisuj z `/` (`selfcmd.hook_argv_json`: `C:/...` i UNC `//serwer/...`; bez `\uXXXX`, bo to też backslash); (2) hak fail-open, który nie potrafi odczytać własnej konfiguracji, zostawia jeden ślad (`cli-unreadable` w `.skillsync/hook-events.jsonl`, raz na sesję, bez treści argumentu), a nie milczy; (3) zmiana formatu wpisu hooka wymaga, żeby `link` uznał stary format za nieaktualny (`hooksync.stale_cli`: wpis z backslashową ścieżką w `--cli` jest wymieniany), inaczej „uruchom link” niczego nie naprawia.

**Jak wykryć:** test, który uruchamia wygenerowany wpis przez `sh -c` (nie sam skrypt), musi przejść na macierzy Windows; test platformowo neutralny z atrapą `self_argv` pilnuje tylko formatu.
