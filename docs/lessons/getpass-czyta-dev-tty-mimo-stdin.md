---
title: "getpass czyta /dev/tty także przy stdin z rury: wymóg hasła z terminala trzeba sprawdzić przez stdin.isatty()"
tags: [skillsync, python, getpass, tty, security, prototype, cli]
paths: ['skillsync_workflow/prototype.py', 'skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_accept.py']
source: '.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/F3-kontrakt.md (D: sprawdzenie stdin.isatty() przed getpass); 3.1-srv-implementer.md (CLI na pty: bez TTY = tty_required); kod skillsync_workflow/prototype.py:2104-2108 (_stdin_is_tty), 2121-2122 (BLOCKED przed pytaniem), 2146 (getpass); stdlib /usr/lib/python3.14/getpass.py:50-52 (os.open(''/dev/tty'') przed czytaniem stdin)'
verified: 2026-11-05
---
**Objaw (bez jawnego sprawdzenia):** komenda wymagająca hasła od człowieka, uruchomiona ze stdin z rury albo z przekierowania (`… < plik`), nie bierze hasła z tego wejścia. `getpass.getpass` pyta w terminalu sterującym procesu (`/dev/tty`), a wejście z rury zostaje nieużyte. Samo to, że stdin nie jest terminalem, nie blokuje więc odczytu hasła.

**Przyczyna:** `getpass.unix_getpass` (CPython) najpierw otwiera `/dev/tty` (`os.open('/dev/tty', O_RDWR|O_NOCTTY)`) i dopiero przy błędzie czyta `sys.stdin`. Wybór źródła nie zależy od tego, czy stdin jest TTY.

**Reguła:**
- Komenda, która wymaga hasła od człowieka przy terminalu, sprawdza `sys.stdin.isatty()` przed jakimkolwiek `getpass.getpass` i przy `False` kończy `BLOCKED` `tty_required` bez pytania (w `accept`: `_stdin_is_tty`, `prototype.py:2104-2108`, użycie `prototype.py:2121-2122`).
- Nie dodawaj źródła hasła z rury ani z argumentu do takiej komendy bez osobnej decyzji (`accept` nie ma `--password-stdin`).
- Test: przypadek z pseudo-TTY (`pty`, `skipUnless` gdy brak `pty`) oraz przypadek ze stdin z rury, który musi zwrócić `tty_required` i nie wywołać `getpass` (atrapa `getpass` zlicza wywołania).
- Granica: `user --password-prompt` (`prototype.py:1737`) i `serve --password-prompt` (`prototype_server.py:5086`) nie mają tego sprawdzenia (stan kodu z 2026-11-05); zmiana tam to osobna decyzja.

**Jak wykryć:**
- `grep -n "getpass" skillsync_workflow/*.py`: każde wywołanie w komendzie wymagającej terminala ma wcześniej `isatty`.
- `grep -n "/dev/tty" /usr/lib/python3*/getpass.py` (stdlib tej maszyny): potwierdza otwieranie `/dev/tty` przed stdin.
- Ręcznie: `echo haslo | python3 ./skillsync prototype accept <projekt> <slug> --screen s1 --by <login>` musi dać `BLOCKED` `tty_required` (exit 2) bez pytania o hasło.

**Granice aktualności:** źródło z CPython 3.14 (`/usr/lib/python3.14/getpass.py`); w innych wersjach nie sprawdzono.
