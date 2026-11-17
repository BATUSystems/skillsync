---
title: 'Izolacja stanu w testach: XDG_* izoluje tylko Linux, a test serwera bez pełnej izolacji pisze do prawdziwego katalogu użytkownika'
tags: [skillsync, tests, isolation, macos, windows, xdg, home]
paths: ['tests/layers_helpers.py', 'skillsync_workflow/layers.py', 'tests/test_skillsync_tmp_path_guard.py']
source: 'prace nad wydaniem 1.8.0: macierz punktowa na Windows i macOS; uzupełnia lekcję o ścieżkach platformy zamiast XDG'
verified: 2026-11-17
---
**Objaw:** test, który startuje serwer strony statusu albo zapisuje stan użytkownika, na macOS i Windows kończy się zapisem w prawdziwym katalogu danych deweloperskiego konta albo czyta jego stan; na Linuksie z ustawionymi `XDG_*` wszystko izolowane i zielone.

**Przyczyna:** `XDG_DATA_HOME` i `XDG_CACHE_HOME` kierują stan tylko na Linuksie. macOS czyta `~/Library/Application Support|Caches` pod `HOME`, Windows `LOCALAPPDATA` i `APPDATA`. Izolacja jednej rodziny zmiennych zostawia pozostałe wskazujące na prawdziwego użytkownika (patrz też lekcja o ścieżkach platformy zamiast XDG).

**Reguła:** test, który startuje proces mający stan, dostaje pełną izolację przez wspólny pomocnik (`HOME`, `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `XDG_*` wskazują katalog testu), a pomocnik odmawia startu, gdy ich nie umie ustawić. Strażnik statyczny pilnuje, że moduł testów startujący serwer albo zapisujący stan używa tego pomocnika; porty z puli, nie losowe.

**Jak wykryć:** po biegu na macOS lub Windows sprawdź, czy prawdziwy katalog danych użytkownika zmienił czas modyfikacji; strażnik statyczny nad modułami testów; test pomocnika bez `HOME`/`LOCALAPPDATA` musi odmówić startu.
