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
- Kontrola stanu strony (`page-state-mismatch`) porównuje adres po nawigacji z żądanym dwa razy: po załadowaniu
  strony i jeszcze raz tuż przed zrzutem (w `measure` i `capture`). Przekierowanie wykonane później niż tuż przed
  zrzutem nie jest wykrywane; asercja pozytywna (`assert`) to druga linia obrony.
- Obrazy są w całości w pamięci. Dekoder PNG odrzuca (kod 2, `ImageLimitError`) plik ponad 64 MiB oraz nagłówek IHDR
  deklarujący ponad 16384 px na bok, ponad 50 M px albo wymiar 0, zanim cokolwiek zdekoduje (bomba dekompresyjna). Bardzo duży
  `fullPage` (np. 16384 × 3100 px) zostanie odrzucony; podniesienie limitu wymaga zmiany stałych `MAX_PNG_*` w `scripts/lib/runtime.mjs`.
  Kontrola przechodzi po nagłówkach chunków (bez dekodowania) i odmawia (kod 2) także, gdy pierwszy chunk nie jest IHDR o długości 13,
  gdy jest drugi IHDR (pngjs bierze ostatni) albo gdy IHDR deklaruje przeplot Adam7 (pngjs rozpakowuje go bez limitu; Chrome takich
  zrzutów nie robi) albo gdy chunk PLTE ma ponad 768 B, długość niepodzielną przez 3 albo jest ich więcej niż jeden (`pngjs` dokleja
  tablicę na każdy wpis palety bez limitu, więc plik < 64 MiB wymusiłby gigabajty sterty). Plik większy niż 64 MiB albo niebędący zwykłym plikiem (np. dowiązanie do `/dev/zero`) nie jest nawet wczytywany
  (`pixel-diff`, `diff` sprawdzają `statSync` przed odczytem); więcej niż 100000 chunków też jest odrzucane (`pngjs` trzyma obiekt na
  każdy IDAT i płaci CPU za każdy chunk; Chrome pisze IDAT po ok. 8 KB, czyli ok. 8 tys. chunków na 64 MiB). Do `pngjs` bez kontroli idą bufory bez sygnatury PNG, pliki urwane (chunk lub
  IHDR poza buforem) i bajty po IEND: `pngjs` 7.0.0 (przypięty) nie dostaje z nich danych i rzuca błąd przed przydziałem (pilnuje tego test na
  prawdziwym `pngjs`). Limit 50 M px to ok. 200 MB bitmapy RGBA 8-bit; przy głębi 16 bit szczyt pamięci `pngjs` to 1,2–1,4 GB (rozpakowane
  dane, dane po filtrach, bitmapa 16-bit, wynik 8-bit i kopia), a głębia 16 bit i poniżej 8 bit idzie wolną ścieżką JS (dziesiątki sekund
  CPU przy 50 M px), czego limit nie ogranicza.
- `capture` ma jeden kontekst przeglądarki na bieg: logowanie raz, ciasteczka wspólne. Maski selektorowe to
  prostokąty bounding box mierzone przed zrzutem; przy `fullPage` uwzględniają przewinięcie, a przy zmianie
  układu strony między zrzutami mogą zamalować inne miejsce.
- Asercja `assert` sprawdza obecność elementu lub tekst; nie dowodzi wyglądu.
- `diff` nie stosuje masek (zamalowane są już w PNG przez `capture`), nie porównuje wpisów `ADDED`, a zrzut
  bez asercji po którejkolwiek stronie to UNVERIFIED.

## Sekrety i artefakty

- Maska wyjścia działa na tekście (zapisy stringów, stdout, stderr, komunikaty). PNG i `trace.zip` są zapisywane bajtowo, bez maski, a `storageState.json`
  (adapter zwraca go jako tekst) przechodzi przez maskę tylko jako tekst: znane wartości ze zmiennych są zastępowane `***` (co może
  uszkodzić plik do ponownego użycia), reszta zostaje. Wszystkie trzy bez gwarancji: **decyzja (1.15.0): binariów nie maskujemy**, bo maskowanie trace wymagałoby rozpakowania zip
  i zamalowania klatek ekranu, a i tak nie objęłoby sekretów, których narzędzie nie zna. Trace Playwrighta zawiera wartości pól
  formularza (także hasła z logowania), nagłówki i treści sieci, ciasteczka i tokeny, migawki DOM i klatki ekranu; zrzut PNG
  pokazuje to, co było na ekranie. Brak znanego sekretu w pliku niczego nie dowodzi.
- Maska zna tylko wartości ze zmiennych wskazanych w `secretEnv`, `UI_VERIFY_SECRET_ENV` i `valueEnv`.
  Tokeny sesji w `storageState.json` nie są maskowane, dopóki nie są wartością takiej zmiennej.
- `trace.zip` i `storageState.json` powstają tylko z `--allow-trace` i mogą zawierać dane logowania. `capture` zapisuje je z trybem 0600
  (POSIX; na Windows tryb nie jest egzekwowany) i zakłada `.gitignore` z ich nazwami w `outDir`, ale tylko gdy `outDir` leży pod
  katalogiem konfiguracji i `.gitignore` jeszcze nie istnieje (istniejącego, także jako dowiązania, nie zmienia). Ostrzeżenie na stderr
  wymienia zawartość i mówi, że maska jej nie obejmuje. Obszary PNG z danymi wrażliwymi zamalowuj maskami selektorowymi (`masks`).

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

- Blokada instalacji (plik `.lock`) przejmuje blokadę przeterminowaną: właściciel o martwym pid albo blokada
  starsza niż 10 min. Żywy właściciel młodszej blokady jest czekany do limitu (kod 3).
- Katalog `<hash>.tmp-<pid>` po `SIGKILL` zostaje w cache; narzędzie go nie sprząta.
- Instalacja używa środowiska npm użytkownika (`.npmrc`, zmienne `npm_config_*`); nie jest ono czyszczone.
- Wymaganie Node ≥ 20.11 nie jest egzekwowane przy starcie (brak wczesnego sprawdzenia wersji).
- `PLAYWRIGHT_MODULE_PATH` wskazuje kod ładowany bez sprawdzenia lockfile i bez kontroli bezpieczeństwa cache.

## Do 1.14.x

- Maski selektorem rozwiązywane per strona w adapterze `measure`.
- Akceptacja odstępstw per tryb i wiersz z pliku danych.
- Macierz DPR z referencjami.
- Polityka subzasobów.
- Sprzątanie `<hash>.tmp-<pid>` po `SIGKILL`.

## Ryzyko resztkowe

- Kontrola bezpieczeństwa cache dotyczy korzenia cache, nie podkatalogu `<hash>` ani `node_modules`. Npm przy
  umask 002 może nadać grupie zapis do tych katalogów. Zaufane są tylko cache na maszynie jednego użytkownika.

## Co nie jest sprawdzone tym dokumentem

- Przebieg z przeglądarką (render fikstur, `measure` i `capture` na stronach syntetycznych) wymaga Chrome.
  Bez niego wynik to NOT_RUN.
- Wynik selftestów na konkretnej maszynie nie jest częścią dokumentacji.
