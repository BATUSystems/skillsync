---
title: 'session_name w JSON paska bywa tytułem wygenerowanym przez AI, nie nazwą nadaną przez użytkownika: z pola nie wolno wnioskować o tożsamości sesji'
tags: [skillsync, claude-code, statusline, session-name, identity, cards]
paths: ['library/hooks/statusline.py', 'tests/test_skillsync_statusline.py', 'library/instructions/_base.md']
source: 'Dokumentacja Claude Code statusline.md (odczyt 2026-11-21, opis pola session_name); kod library/hooks/statusline.py (build_segments: sekcja „sesja: ”); spec CLAUDE-7, sekcja Ryzyka („Nazwa sesji wzięta za tożsamość karty”)'
verified: 2026-11-21
---
**Objaw:** pasek pokazuje `sesja: <tekst>`, a tekst nie odpowiada aliasowi karty (np. pokazuje tytuł pracy zamiast aliasu `<alias>`), albo odwrotnie: sesja bez nazwy ma pusty segment, więc agent uznaje brak segmentu za brak tożsamości.

**Przyczyna:** pole `session_name` w JSON na stdin paska to nazwa ustawiona przez `--name` albo `/rename`. Gdy jej nie ma, Claude Code wstawia tytuł wygenerowany przez AI, który opisuje pracę. Domyślna nazwa wyświetlana (np. `my-app-3f`) do pola nie trafia. Pole jest opcjonalne. Skrypt paska tylko je oczyszcza i skraca; nie wie, skąd pochodzi wartość.

**Reguła:**
- Nie wnioskuj tożsamości karty (aliasu, roli, rodzaju) z `session_name` ani z segmentu `sesja:` w pasku. Tożsamość sprawdza się według `AGENTS.md`: alias z `ASSIGN`, nazwa sesji w `ListAgents`, nazwa nadana przez użytkownika.
- Segment `sesja:` zostaje z etykietą i bez twierdzenia, kim jest sesja. Nie zmieniaj go na samą wartość ani na alias.
- Gdy alias i segment się różnią, zgłoś niejasność zamiast wybierać jedną wersję.
- Każda zmiana, która ma pokazywać tożsamość, potrzebuje innego źródła (np. pliku karty), a nie pola paska.

**Jak wykryć:**
- `grep -n "session_name" library/hooks/statusline.py`: powinna być jedna linia czytająca pole, z etykietą `sesja: `.
- `python3 -m unittest discover -s tests -p 'test_skillsync_statusline.py' -v`: testy pola `session_name` (oczyszczanie, skracanie, brak pola).
- W sesji: `/rename <nazwa>` ustawia pole; bez nazwy pasek pokazuje tytuł AI albo nic.
