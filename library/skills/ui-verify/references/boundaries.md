# Granice narzędzi i znane luki

## Znane luki (zatwierdzone)

- **Kształt pominięty przy różnicy wymiarów.** Gdy wymiary elementu różnią się więcej niż `sizeTolerancePx`,
  kształt dostaje `SKIPPED` (`box-mismatch`) i nie jest oceniany. Przy zatwierdzonym odstępstwie (`deviations`)
  na `width` albo `height`, albo gdy `tolerancePx` jest większe niż `sizeTolerancePx`, pomiar pudełka może
  dać PASS. Wtedy łączny wynik to PASS bez dowodu pikselowego, bo `SKIPPED` nie wchodzi do łączenia.
  Test w selftestcie `measure` (`box mismatch skips the shape check`) pokrywa tylko przypadek, w którym
  pomiar też daje FAIL.
- **Odporność na 1 px.** Przesunięcie o 1 px nie daje FAIL (zamierzone). Wyłączenie: `pixel-diff --no-tolerate-1px`.
  W `measure` i `diff` odporność jest zawsze włączona.

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
- `pixel-diff` i `diff` czytają dowolne ścieżki wskazane w argumentach (bez ograniczenia do katalogu). Zapis
  jest tylko do `--diff-out` pod bieżącym katalogiem.
- Testowane minimum: Node 22. Deklarowane `>= 20.11` nie jest testowane. Node 18 nie jest obsługiwany.

## Co nie jest sprawdzone tym dokumentem

- Przebieg z przeglądarką (render fikstur, `measure` i `capture` na stronach syntetycznych) wymaga Chrome.
  Bez niego wynik to NOT_RUN.
- Wynik selftestów na konkretnej maszynie nie jest częścią dokumentacji.
