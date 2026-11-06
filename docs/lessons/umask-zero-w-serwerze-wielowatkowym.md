---
title: "os.umask(0) i przywrócenie w serwerze wielowątkowym to wyścig: inny wątek tworzy plik z umask 0"
tags: [skillsync, python, umask, threading, permissions, prototype]
paths: ['skillsync_workflow/prototype_server.py', 'skillsync_workflow/prototype.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.1-1.2-implementer.md (Odkrycia 5-6); prototype.py atomic_write
verified: 2026-11-04
---
**Objaw:** (potencjalny) plik utworzony w serwerze z trybem szerszym niż zamierzony (0666 zamiast `& ~umask`). Reguła trybu nowego pliku różni się między modułami: `prototype.py:atomic_write` używa `0666 & ~umask`, serwer `comments.json` `0644 & ~umask`.

**Przyczyna:** wzorzec „`os.umask(0)`, odczyt, przywrócenie” (tak odczytuje umask `prototype.py`) zmienia stan procesu; wątek serwera, który w tym oknie utworzy plik, dostaje umask 0. Dodatkowo `tempfile` tworzy 0600 (zob. `tempfile-tworzy-0600-prototyp-nieczytelny-dla-grupy.md`).

**Reguła:** w kodzie wielowątkowym nie odczytuj umask przez `os.umask(0)`. `prototype_server.current_umask()` czyta wartość z `/proc/self/status`, a gdy jej brak, używa wartości zapamiętanej przy imporcie. Tryb nowego pliku ustawiaj jawnie `chmod`, istniejący zachowuj.

**Jak wykryć:** `grep -n "umask(0)" skillsync_workflow/*.py` (`prototype_server.py` robi to tylko jako zapas bez `/proc`, jednorazowo przy imporcie; `prototype.py` w każdym zapisie, ale bez wątków); testy `AtomicWriteTests` i `FirstWrite…world_readable` w `tests/test_skillsync_prototype_server.py`.
