# Granice narzędzi i znane luki

## Znane granice (zatwierdzone)

- **Kształt pominięty przy różnicy wymiarów.** Gdy wymiary elementu różnią się więcej niż `sizeTolerancePx`,
  kształt dostaje `SKIPPED` (`box-mismatch`) i nie jest oceniany. To nie jest już luka: para jest wtedy
  UNVERIFIED (kod 3), także gdy pomiar pudełka dał PASS (zatwierdzone odstępstwo na `width`/`height` albo
  `tolerancePx` większe niż `sizeTolerancePx`). Para jest PASS tylko z jawnym `acceptShape` (kształt
  `ACCEPTED` z powodem w raporcie).
- **Odporność na przesunięcie.** Przesunięcie do `shiftRadius` pikseli nie daje FAIL (zamierzone). Wyłączenie:
  `pixel-diff --no-tolerate-1px`. W `measure` promień to `shiftPx` (domyślnie `ceil(deviceScaleFactor)`),
  w `diff` zawsze 1.
- **Duże maski rozmywają próg.** Mianownik procentu obejmuje cały obszar, także zamalowany. Duża maska
  zmniejsza udział różnic w pozostałym obszarze, więc próg `maxPct` staje się łagodniejszy. Maskuj małe
  obszary albo obniż `maxPct`.
- **Polityka origin nie obejmuje subzasobów.** Kontrola dotyczy adresu strony i nawigacji (główne, ramki).
  Obrazy, skrypty, style i `fetch` ładowane przez stronę nie są sprawdzane.

## Zakres pomiaru

- `measure` mierzy jeden element (pierwsze dopasowanie selektora) po każdej stronie: pudełko, wybrane style
  i piksele samego elementu. Położenie potomków i ich wygląd nie są mierzone.
- `measure` przyjmuje `--allow-trace`, ale nie zapisuje trace.
- Renderowanie zależy od systemu (fonty, GPU, skalowanie). Zakresy w `fixtures/cases.json` zamiast wartości
  dokładnych. Porównania między maszynami wymagają tej samej bazy renderowania.
- Obrazy są w całości w pamięci. Brak limitu rozmiaru po stronie narzędzia.
- `capture` ma jeden kontekst przeglądarki na bieg: logowanie raz, ciasteczka wspólne. Maski selektorowe to
  prostokąty bounding box mierzone przed zrzutem; przy `fullPage` uwzględniają przewinięcie, a przy zmianie
  układu strony między zrzutami mogą zamalować inne miejsce.
- Asercja `assert` sprawdza obecność elementu lub tekst; nie dowodzi wyglądu.
- `diff` nie stosuje masek (zamalowane są już w PNG przez `capture`), nie porównuje wpisów `ADDED`, a zrzut
  bez asercji po którejkolwiek stronie to UNVERIFIED.

## Sekrety i artefakty

- Maska wyjścia działa na tekście (zapisy stringów, stdout, stderr, komunikaty). PNG i `trace.zip` są zapisywane
  bajtowo, bez maski.
- Maska zna tylko wartości ze zmiennych wskazanych w `secretEnv`, `UI_VERIFY_SECRET_ENV` i `valueEnv`.
  Tokeny sesji w `storageState.json` nie są maskowane, dopóki nie są wartością takiej zmiennej.
- `trace.zip` i `storageState.json` powstają tylko z `--allow-trace` i mogą zawierać dane logowania.

## Przeglądarka i instalacja

- Przeglądarki nie instaluje ani nie pobiera skill. Potrzebny jest Chrome lub Chromium na maszynie.
  Bez niego każdy przebieg z przeglądarką kończy się kodem 3 (NOT_RUN), nie PASS.
- Instalacja wymaga npm (z Node) i dostępu do rejestru npm. Proxy i `.npmrc` użytkownika działają przez npm.
  Brak sieci daje kod 3 z instrukcją.
- `pixel-diff` czyta dowolne ścieżki wejściowe wskazane w argumentach (bez ograniczenia do katalogu), a zapis
  jest tylko do `--diff-out` pod bieżącym katalogiem. `diff` wymusza katalogi pod bieżącym katalogiem
  (wyjście poza niego to błąd 2).
- Testowane minimum: Node 22. Deklarowane `>= 20.11` nie jest testowane. Node 18 nie jest obsługiwany.

## Znane granice wewnętrzne (część do 1.14.x)

- Blokada instalacji (plik `.lock`) nie ma przejęcia przeterminowanej blokady po awarii procesu.
- Katalog `<hash>.tmp-<pid>` po `SIGKILL` zostaje w cache; narzędzie go nie sprząta.
- Instalacja używa środowiska npm użytkownika (`.npmrc`, zmienne `npm_config_*`); nie jest ono czyszczone.
- Wymaganie Node ≥ 20.11 nie jest egzekwowane przy starcie (brak wczesnego sprawdzenia wersji).
- `PLAYWRIGHT_MODULE_PATH` wskazuje kod ładowany bez sprawdzenia lockfile i bez kontroli bezpieczeństwa cache.

## Do 1.14.x

- Maski selektorem rozwiązywane per strona w adapterze `measure`.
- Akceptacja odstępstw per tryb i wiersz z pliku danych.
- Macierz DPR z referencjami.
- Polityka subzasobów.
- Maskowanie binariów (`trace.zip`, PNG).
- Limit rozmiaru dekodera PNG.
- Blokada instalacji: przejęcie przeterminowanej blokady.
- Sprzątanie `<hash>.tmp-<pid>` po `SIGKILL`.

## Co nie jest sprawdzone tym dokumentem

- Przebieg z przeglądarką (render fikstur, `measure` i `capture` na stronach syntetycznych) wymaga Chrome.
  Bez niego wynik to NOT_RUN.
- Wynik selftestów na konkretnej maszynie nie jest częścią dokumentacji.
