---
title: "Tabela narzędzi testowych ma dwa interpretery (pakiet i samodzielny hak): zmiana kolejności lub pola wymaga testu parytetu i zgodności wstecznej"
tags: [skillsync, test-run, hooks, guard-tests, detect-tool, parity, compatibility]
paths: ['skillsync_workflow/test_run.py', 'skillsync_workflow/data/test_tools.json', 'library/hooks/guard-tests.py', 'tests/test_skillsync_test_run*.py', 'tests/fixtures/**']
source: 'odczyt skillsync_workflow/test_run.py (`detect_tool`, `unwrap_command`) i library/hooks/guard-tests.py (`tool_of`, `program_target`, `classify`); prace nad rozpoznawaniem narzędzia w `test-run` i haku'
verified: 2026-11-19
---
**Objaw:** to samo polecenie dostawało inne narzędzie w `test-run` niż w haku `guard-tests` (np. `python -W error -m unittest tests` albo `python3.12 -m pytest` poza ostrzeżeniem), a słowo modułu `unittest` było szukane w całym poleceniu, więc `go test ./unittest` dawało zły adapter.

**Przyczyna:** tabelę (`data/test_tools.json`) czytają dwa niezależne interpretery: `detect_tool` w pakiecie i `tool_of` w haku, który jest samodzielnym skryptem bez importu pakietu i czyta własną kopię tabeli (odświeża ją `skillsync link`). Zmiana kolejności kroków albo nowe pole w jednym miejscu rozjeżdża drugie, a stara kopia tabeli przy haku musi dalej działać z nowym kodem.

**Reguła:** każda zmiana kolejności rozpoznawania lub nowego pola tabeli zmienia oba interpretery razem i ma test parytetu `tool_of` ⇔ `detect_tool` na wspólnej liście poleceń. Pola nowe są opcjonalne (schemat bez podbicia), a test zgodności wstecznej ładuje kopię tabeli z poprzedniego wydania z `tests/fixtures` do nowego kodu. Słowo modułu będące też narzędziem (`pytest`) nie może zniknąć z żadnej ścieżki (krok „ostatniej deski”); moduł niebędący narzędziem (`unittest`) nie pasuje poza krokami 1–2. Klasyfikacja programu w haku używa tylko kroków 1–2, więc dopisanie kroku do `detect_tool` nie rozszerza haka bez osobnej decyzji.

**Jak wykryć:** test parytetu na zestawie poleceń (opakowania, `-m` z gospodarzem, `go test ./unittest`, `echo pytest`); test starej kopii tabeli; po zmianie `skillsync link` na projekcie testowym i sprawdzenie, że kopia przy haku jest nowa.
