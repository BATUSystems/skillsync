---
title: "Testy na macOS: ścieżki platformy zamiast XDG i system plików bez rozróżniania wielkości liter"
tags: [skillsync, tests, macos, ci, xdg, paths]
paths: ['tests/layers_helpers.py', 'tests/test_skillsync_workflow.py', 'skillsync_workflow/layers.py', '.github/workflows/ci.yml']
---
Objaw: na macOS 40 błędów w 10 modułach (FileNotFoundError, różnice ścieżek, `git add -A` poza repozytorium), na Linuksie zielono.
Przyczyna: (1) na macOS `layers.data_home()` i `cache_home()` dają `~/Library/Application Support|Caches/skillsync` pod `HOME`, a `XDG_*` czyta tylko Linux, tymczasem testy zakładały `XDG_DATA_HOME/skillsync` na sztywno; (2) tymczasowy katalog to `/var/...`, a `resolve()` daje `/private/var/...`; (3) APFS nie rozróżnia wielkości liter (`Demo` i `demo` to jeden katalog); (4) `env` ma backend własności procesów tylko na Linuksie, a hook pre-push opiera się na GNU `mktemp` (BSD ma fallback do katalogu użytkownika).
Dowód: PR #3 BATUSystems/skillsync, runy 42356489156 (40 błędów) i 42364003065 (4 joby macOS zielone). Drugi błąd własny: izolacja `HOME` w `WorkflowFixture` tworzyła `base/home`, kolidując z klasami pochodnymi (27 × FileExistsError); nazwa `machine-home` to naprawia.
Reguła: oczekiwane ścieżki stanu per-user w testach bierz z `machine_data_dir()` i `machine_cache_dir()` (`tests/layers_helpers.py`), nie z `XDG_*`; ścieżki porównuj po `resolve()`; nie zakładaj, że dwie nazwy różniące się wielkością liter są różnymi katalogami; test zależny od Linuksa lub GNU narzędzia pomijaj na macOS z uzasadnieniem w `skipTest`. Kod produkcyjny i kontrakt ścieżek (docs/configuration.md) zostają bez zmian.
Jak wykryć: CI `test (macos-latest, …)`; lokalnie na Linuksie nie widać, więc nowe testy ścieżek pisz przez helpery.
