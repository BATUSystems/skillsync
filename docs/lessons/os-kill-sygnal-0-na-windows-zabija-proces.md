---
title: "os.kill(pid, 0) na Windows zabija proces zamiast go sprawdzać: żywotność PID tam rozstrzyga inny dowód"
tags: [skillsync, python, windows, process, prototype, login-link, portability]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_login_link.py']
source: 'prototype_server.py: link_pid_alive (pre-implement-r2 DASH-2, odkrycie 4 implementera F-R2); dokumentacja Pythona: os.kill na Windows z sygnałem innym niż CTRL_C_EVENT/CTRL_BREAK_EVENT wywołuje TerminateProcess'
verified: 2026-11-11
---
**Objaw:** kod sprawdzający, czy proces o danym PID żyje, przez `os.kill(pid, 0)` działa na Linuksie i macOS, a na
Windows kończy sprawdzany proces (kod wyjścia 0).

**Przyczyna:** na Windows `os.kill` z sygnałem innym niż `CTRL_C_EVENT` i `CTRL_BREAK_EVENT` wywołuje
`TerminateProcess`; sygnał 0 nie jest „pustym” sprawdzeniem jak na POSIX.

**Reguła:** w kodzie przenośnym nie używaj `os.kill(pid, 0)` do sprawdzania żywotności na Windows. Pomijaj tam
sprawdzenie PID i polegaj na innym dowodzie (w login-link: `instance` zwrócone przez żywy serwer pod adresem).

**Jak wykryć:**
- `grep -n "os.kill" skillsync_workflow/*.py`: każde użycie z sygnałem 0 musi być za bramką `os.name != 'nt'`.
  Wzorzec poprawny: `prototype_server.py:link_pid_alive` (na nt zwraca True).
- Test z podmienionym `_LINK_POSIX = False`: funkcja nie woła `os.kill`
  (`tests/test_skillsync_prototype_login_link.py`).
