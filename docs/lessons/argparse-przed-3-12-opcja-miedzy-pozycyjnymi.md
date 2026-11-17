---
title: 'argparse Pythona przed 3.12 dopasowuje opcjonalny pozycyjny pusto przed opcjami: lokalny nowszy interpreter ukrywa wadę'
tags: [skillsync, python, argparse, cli, tests, compat]
paths: ['skillsync_workflow/argtools.py', 'skillsync_workflow/cli.py', 'tests/test_skillsync_argparse_positionals.py']
source: 'prace nad wydaniem 1.8.0: macierz punktowa na Pythonie 3.11.18 (polecenia prototype sync, gate, question wait, question merge, merge-comments) wobec 3.12.16 bez błędu'
verified: 2026-11-17
---
**Objaw:** `skillsync prototype sync <katalog> --json <slug>` (i jeszcze cztery polecenia z opcją między argumentami pozycyjnymi) kończy się `unrecognized arguments` na Pythonie 3.11, a na 3.12 i nowszych działa. Na stacji deweloperskiej z Pythonem 3.14 wszystko było zielone.

**Przyczyna:** argparse przed 3.12 dopasowuje pozycyjny `nargs='?'` albo `'*'` PUSTO zaraz po pierwszym pozycyjnym, więc drugi pozycyjny po opcji zostaje „nierozpoznany”. Wada dotyczy każdego polecenia o kształcie „pozycyjny + opcjonalny pozycyjny” albo dwóch pozycyjnych, razem 72 poleceń rdzenia, a lokalny interpreter nowszy niż najstarszy obsługiwany ukrywa ją w całości.

**Reguła:** jedno wspólne rozwiązanie zamiast łatek per polecenie: parsery liściowe czytają argumenty przemieszane (`argtools.IntermixedParser`), tylko przed 3.12. Parsery z `REMAINDER`, podpoleceniami albo grupą wykluczającą z pozycyjnym zostają standardowe, a test zgłasza takie polecenie. Test jest tabelą generowaną introspekcją drzewa argparse, więc nowe polecenie wchodzi do niej samo. Wariant „opcja między pozycyjnymi” musi dać to samo co „opcje na końcu”, a tryb przemieszany wymusza się także na nowszym Pythonie, żeby ścieżkę ćwiczyć wszędzie.

**Jak wykryć:** bieg na najstarszym obsługiwanym interpreterze (nie na lokalnym); sonda wywołująca każde polecenie w trzech układach (opcje między, po, przed pozycyjnymi) i szukająca `FAIL`. Wada nie jest wykrywalna przez czytanie kodu, dopóki test nie biegnie na 3.11.
