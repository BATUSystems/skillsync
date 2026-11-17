---
title: 'Windows w asercjach testów: separator w kluczach i str(Path), krótka nazwa 8.3, CRLF z print potomka i otwarty uchwyt blokujący sprzątanie'
tags: [skillsync, tests, windows, paths, crlf, tempdir]
paths: ['tests/adapter_replay.py', 'tests/tmp_paths.py', 'tests/test_skillsync_adapter_web_counts.py', 'skillsync_workflow/test_events.py']
source: 'prace nad wydaniem 1.8.0: macierz Windows (adaptery, zdarzenia testów, sprzątanie katalogów tymczasowych); uzupełnia lekcję o as_posix w JSON'
verified: 2026-11-17
---
**Objaw:** na Windows czerwone są asercje, które na Linuksie przechodzą: klucz słownika z `\` zamiast `/`, ścieżka tymczasowa w krótkiej formie `RUNNER~1` różna od `realpath`, liczba bajtów wyjścia potomka większa o jeden na linię, `PermissionError`/`WinError 32` przy sprzątaniu katalogu testu.

**Przyczyna:** (1) `str(Path)` i `relative_to` dają natywny separator, a identyfikator testu i grupa muszą mieć `/` (strumień i raport JUnit mają się zgadzać). (2) Katalog tymczasowy bywa podany jako nazwa 8.3, a porównanie z `resolve()` jej nie rozwija. (3) `print` potomka kończy linię `\r\n`, więc liczenie bajtów i `endswith('\n')` zawodzą. (4) Windows nie usuwa pliku ani katalogu z otwartym uchwytem: writer zdarzeń otwarty do końca testu blokuje sprzątanie.

**Reguła:** ścieżki w kluczach i identyfikatorach normalizuj `as_posix()` po obu stronach porównania (także grupę, nie tylko id); katalog tymczasowy porównuj po `resolve()`; wyjście potomka porównuj po normalizacji końców linii; każdy otwarty uchwyt zamykaj `addCleanup` zarejestrowanym PO sprzątaniu katalogu (kolejność odwrotna: najpierw zamknięcie). Dla adapterów dodaj test syntetyczny: strumień z `\r\n` cięty rurą w dowolnym miejscu daje wynik jak `\n`.

**Jak wykryć:** `WinError 32` w sprzątaniu; różnice tylko w separatorze lub o jeden bajt na linię; test z ścieżką `e2e\a.spec.ts` w strumieniu, która ma dać id i grupę z `/`.
