---
title: 'Weryfikacja UI (ui-verify): werdykty, kody wyjścia i granice dowodu'
tags: [skillsync, ui-verify, verification, exit-codes, testing]
paths: ['library/skills/ui-verify/**', 'tests/ui_verify/**', 'tests/test_skillsync_ui_verify.py']
source: plan zadania (poza rdzeniem)
verified: 2026-11-23
---
Reguła: wynik weryfikacji wyglądu ma trzy stany dowodu. PASS (kod 0) tylko wtedy, gdy każda wymagana pozycja ma dowód. FAIL (kod 1) tylko wtedy, gdy weryfikacja wykryła różnicę. Brak dowodu (brak asercji pozytywnej, brak elementu, brak manifestu, brak zależności lub przeglądarki) to UNVERIFIED albo NOT_RUN, kod 3. Błąd narzędzia (konfiguracja, padnięcie Chrome, timeout, nieobsłużony wyjątek) to kod 2, nigdy 1.

Dlaczego: kod 1 w bramce znaczy „wykryto różnicę”. Gdyby błąd narzędzia dawał 1, wyglądałby jak regresja. Gdyby brak dowodu dawał 0, wyglądałby jak zgodność. Zrzut bez asercji pozytywnej (strona mogła się nie wyrenderować) nie dowodzi zgodności.

Wyjątki i przykłady:
- `diff` daje SAME tylko wtedy, gdy oba zrzuty mają asercję PASS i równe wymiary. Brak `manifest.json` albo pusty zestaw to UNVERIFIED (kod 3).
- `measure` z elementem nieobecnym po jednej stronie daje UNVERIFIED (kod 3), nie PASS.
- Łączenie: FAIL wygrywa; PASS tylko przy niepustej liście samych PASS.
- Kształt SKIPPED (`box-mismatch`) to brak dowodu: para jest UNVERIFIED (kod 3), także przy pomiarze PASS dzięki zatwierdzonemu odstępstwu (to już nie luka). PASS wymaga jawnego `acceptShape: { reason }` w parze (kształt ACCEPTED, liczony jak PASS, powód w raporcie).
- Zły stan strony (przekierowanie na inny adres niż zadany) to UNVERIFIED z powodem `page-state-mismatch`, nie PASS i nie FAIL; adres jest sprawdzany po załadowaniu i ponownie tuż przed zrzutem (późniejsze przekierowanie nie jest wykrywane, asercja pozytywna jest drugą linią obrony). Niedozwolona nawigacja (polityka origin: nawigacje główne i ramki, nie subzasoby) i niedozwolona zmienna `valueEnv` (spoza `UI_VERIFY_SECRET_ENV` i bez prefiksu `UI_VERIFY_`) to błąd 2. Literówka w `styleProps` (brak wartości we wzorcu) to też błąd 2, bo dawałaby cichy PASS.
- Niebezpieczny cache (względna ścieżka, w katalogu skilla, obcy właściciel, zapis g/o) to kod 2 przy każdym ładowaniu kodu z cache, nie 3. Wywołanie skryptu przez symlink wykonuje `main` (`isMain` porównuje realpath); skrypt główny nigdy nie kończy się cichym 0.
- Brak zależności, przeglądarki lub sieci to kod 3 z instrukcją, nie FAIL.
- **Limit dekodera PNG:** `decodePng` (`lib/runtime.mjs`) przed dekodowaniem sprawdza długość bufora (64 MiB) i przechodzi po nagłówkach chunków: pierwszy musi być IHDR o długości 13, drugiego IHDR nie może być (pngjs bierze ostatni), wymiary (16384 na bok, 50 M px) brak przeplotu Adam7 (pngjs rozpakowuje go bez limitu) oraz co najwyżej jeden PLTE o długości ≤ 768 B podzielnej przez 3 (pngjs dokleja tablicę na każdy wpis palety i kumuluje chunki, więc plik < 64 MiB wymusiłby gigabajty sterty). `pngjs` liczy rozmiar danych z nagłówka, więc nagłówek z ogromnymi wymiarami jest bombą dekompresyjną; dlatego kontrola nie może przepuszczać „dziwnego” nagłówka do dekodera. Naruszenie to `ImageLimitError` i kod 2 (ERROR), nie UNVERIFIED i nie FAIL; `diff` zgłasza to jako `DiffError` (kod 2), a `pixel-diff` i `diff` sprawdzają przed odczytem, że to zwykły plik w limicie rozmiaru (`assertPngFileStat`), a sama kontrola odrzuca też ponad 100000 chunków. Bez kontroli do `pngjs` idą tylko bufory bez sygnatury PNG, pliki urwane i bajty po IEND; `pngjs` 7.0.0 (przypięty) nie dostaje z nich danych i rzuca błąd przed przydziałem (pilnuje tego test na prawdziwym `pngjs`).
- **Binaria bez maski:** `trace.zip` i PNG nie są maskowane, a `storageState.json` (tylko z `--allow-trace`, zapisywany jako tekst) przechodzi przez maskę wyjścia tylko dla znanych wartości (reszta zostaje); narzędzie niczego tu nie obiecuje (trace niesie wartości pól formularza, nagłówki i treści sieci, ciasteczka, klatki ekranu; maska zna tylko wartości wskazanych zmiennych). Trace i `storageState.json` powstają z trybem 0600 (POSIX), a `capture` zakłada `.gitignore` w `outDir` tylko gdy go nie ma (nie w katalogu konfiguracji i nie nadpisuje istniejącego). Brak znanego sekretu w pliku niczego nie dowodzi.

Gdzie w kodzie:
- `library/skills/ui-verify/scripts/lib/verdict.mjs` (EXIT, combine, exitFor, isMain, installGuards z maskowaniem)
- `library/skills/ui-verify/scripts/lib/measure-core.mjs` (łączenie pomiaru i kształtu, SKIPPED, ACCEPTED, element nieobecny, page-state-mismatch)
- `library/skills/ui-verify/scripts/lib/playwright-adapter.mjs` (polityka nawigacji, symlinki, stan strony), `lib/policy.mjs` (`assertRealInside`), `lib/runtime.mjs` (kontrola cache)
- `library/skills/ui-verify/scripts/lib/diff-core.mjs:16-58` (SAME tylko przy PASS po obu stronach, no-manifest, no-shots)
- `library/skills/ui-verify/scripts/lib/capture-core.mjs` (NONE → UNVERIFIED, FAIL asercji, `valueEnv`, `assertTimeoutMs`)
- `library/skills/ui-verify/scripts/pixel-diff.mjs` (kod wyjścia z werdyktu; `ImageLimitError` → kod 2), `lib/runtime.mjs` (`decodePng`, `MAX_PNG_*`), `lib/artifacts.mjs` (`writeSensitiveArtifacts`: 0600, `.gitignore`, ostrzeżenie), `lib/out.mjs` (`writeFile` z `mode`)
- testy: `tests/ui_verify/verdict.test.mjs`, `tests/ui_verify/adapter.test.mjs`, `tests/ui_verify/entrypoint.test.mjs`, `tests/ui_verify/artifacts.test.mjs`, `tests/ui_verify/runtime.test.mjs` (limity PNG), `tests/test_skillsync_ui_verify.py`
