---
title: "Komenda bez help i bez opisu w docs/cli.md przechodzi cicho; klasę kodu wyjścia potwierdź w handlerze, nie z pamięci"
tags: [skillsync, cli, help, exit-codes, docs, tests, guard]
paths: ['skillsync_workflow/cli_help.py', 'skillsync_workflow/cli.py', 'skillsync_workflow/__init__.py', 'tests/test_skillsync_cli_help_guard.py', 'tests/parser_tree.py', 'tests/data/cli_help_exceptions/**', 'docs/cli.md']
---
Objaw: nowa komenda albo flaga wchodzi bez `help` albo bez opisu w `docs/cli.md`, a `skillsync <komenda> --help` podaje linię `Exit codes:` z kodami, których komenda nie zwraca. W CORE-1160 z pierwszej mapy klas 29 z 122 była błędna; `argparse` nie zgłasza żadnego z tych braków.
Przyczyna: klasę kodu przepisano z nazwy albo z sąsiedniej komendy (np. `prototype sync` wzięte za klasę wszystkich `prototype`), a opisu nie wymusza ani `argparse`, ani żaden test przed strażnikiem.
Dowód: `skillsync_workflow/cli_help.py` (`EXIT_CLASS`, `EXIT_CLASSES`); strażnik `tests/test_skillsync_cli_help_guard.py`, reguły G1–G3b; zlecenie CORE-1160-HELP, krok H-8 (dane z pierwszej mapy klas).
Reguła:
- Każda komenda liściowa, grupa i argument ma `help` (grupa i komenda liściowa także `description`), po angielsku, w czystym ASCII.
- Każda komenda liściowa ma wpis w `EXIT_CLASS`. Nie ma klasy domyślnej: brak wpisu to błąd, a nie cichy wybór klasy.
- Klasę wyprowadź z handlera (wartości zwracane przez `emit`/`exit_code`) i potwierdź uruchomieniem, nie z nazwy ani z innej komendy. Epilog `no-fail` deklaruje brak kodu 1, a strażnik sprawdza tę deklarację tylko na reprezentantach klasy, więc nową komendę w tej klasie przetestuj ręcznie.
- Każda zmiana flagi, komendy albo kodu wyjścia zmienia też opis w `docs/cli.md` i wpis w `EXIT_CLASS`.
- Wyjątek od strażnika to wpis w `tests/data/cli_help_exceptions/<paczka>.yaml` z niepustym `reason`. Nie dopisuj go, żeby przejść test: poprawiony tekst jest pierwszym wyborem, a górna granica `MAX_EXCEPTIONS` tylko spada.
Jak wykryć: `python3 -m unittest discover -s tests -p test_skillsync_cli_help_guard.py` (G3 wskazuje brak wpisu klasy albo złe kody w epilogu, G3b uruchamia reprezentantów klas); punktowo `skillsync <komenda> --help` i porównanie linii `Exit codes:` z handlerem. Znana granica: `serve` nie jest uruchamiany do kodu 0.
