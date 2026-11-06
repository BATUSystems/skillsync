---
title: "Test prywatności z literałem imienia w rdzeniu sam staje się wyciekiem: składaj literał albo czytaj go z pliku tożsamości"
tags: [skillsync, privacy, leakguard, tests, identity]
paths: ['tests/test_skillsync_retro.py', 'tests/test_skillsync_retro_scan.py', 'skillsync_workflow/leakguard.py']
source: 'raport code-review-1.5-review-1.md:22 (minor, literał w czterech asercjach negatywnych), :33 (odkrycie do wiedzy); test_skillsync_retro.py:1621, 3022, 3027, 3030 (wg recenzji)'
verified: 2026-11-06
---
**Objaw:** test sprawdzający, że rdzeń nie zawiera nazwy osoby, ma tę nazwę jako literał w asercji. Strażnik wycieków
(`leakcheck`) skanuje rdzeń i oznacza plik testu jako wyciek. Test przeciwko wyciekom sam jest wyciekiem.

**Przyczyna:** asercja negatywna (`assertNotIn(literal, text)`) wymaga literału w kodzie testu. Plik testu leży w rdzeniu,
więc literał trafia do rdzenia. Test źródła czytający tylko `retro.py` pomija nowe moduły, np. `retro_scan.py`.

**Reguła:**
- Literału osoby nie zapisuj w kodzie ani w dokumentach rdzenia. W testach składaj go w czasie wykonania
  (np. `"Bar" + "cis"`) albo czytaj z pliku tożsamości strażnika (katalog `identity_dir` z configu maszyny).
  Test bez tego pliku ma się pominąć z jawnym powodem, a nie przejść po cichu.
- Test źródła obejmuje wszystkie moduły, które wchodzą do wyniku: także `retro_scan.py`, a nie tylko `retro.py`.
- Nazwa klienta, osoby albo ścieżka tej maszyny w tekście dla użytkownika (help, komunikat, docstring) to ten sam wyciek.

**Jak wykryć:**
- `grep -rn "<imię>" tests/ skillsync_workflow/ docs/` z listą z pliku tożsamości (nie z literału w poleceniu).
- Uruchomienie `skillsync leakcheck` na rdzeniu po zmianie testów: plik testu nie może być oznaczony.
- Przegląd asercji `assertNotIn` z literałem: każda ma składanie albo odczyt z pliku.
