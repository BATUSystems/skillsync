# Zrzuty ekranu

## Dane

- **Wyłącznie dane przykładowe**: makieta, fixture, środowisko testowe. Nigdy dane osobowe.
- Nagłówek strony mówi, że zrzuty pochodzą z danych przykładowych.
- Zrzutów od odbiorcy (czat, listy z nazwiskami, produkcja) nie publikuj. Odtwórz sytuację na danych przykładowych.
- Przed publikacją obejrzyj każdy obraz: żadnych imion, adresów, numerów, tokenów, nazw prywatnych środowisk.

## Źródło zrzutów

1. **Preferuj istniejący, hermetyczny skrypt zrzutów projektu**: lokalny serwer z fixture, Playwright, wszystkie warianty
   komponentu na jednej stronie. Nie wymyślaj nowego, gdy jest działający.
2. Gdy skrypt wymaga zbudowanej aplikacji, a budowa w bieżącym katalogu mogłaby przeszkodzić innej sesji: zbuduj do katalogu
   tymczasowego (opcja katalogu wyjściowego budowy) i uruchom **kopię** skryptu, która wskazuje ten katalog. Oryginału nie edytuj.
3. Gdy skryptu nie ma: napisz mały skrypt jednorazowy w katalogu tymczasowym, na fixture, bez zapisu w repo (chyba że użytkownik zdecyduje inaczej).

## Wycinki

- **Osobny wycinek na każdy wariant lub stan** (jedna karta = jeden obraz), plus **jeden zrzut przeglądowy** całego ekranu.
- Wycinek elementu to wywołanie Playwright `locator.screenshot` w skrypcie projektu. Komenda `capture` z `ui-verify` robi
  zrzuty całych stron i szerokości, nie wycinków pojedynczych elementów.
- Przed zrzutem poczekaj na asercję pozytywną (element, który dowodzi, że ekran się wyrenderował). Zrzut pustej strony nie jest dowodem.

## Format i skala

- JPEG, jakość ok. 80–85%, skala 0,7–0,8; PNG tylko dla obrazów z ostrymi, płaskimi krawędziami, które mają zostać małe.
- Nazwy plików opisowe i stabilne (`karta-wymagane-2z3.jpg`), bo ścieżki wchodzą do `index.html`.
- Obrazy leżą **obok strony** (ten sam katalog lub podkatalog `img/`), ścieżki względne. Nie wstawiaj `data:` do HTML.
- Każdy obraz ma `alt` (co przedstawia) i podpis „dlaczego” (reguła i liczby).

## Odwołania do innych skilli (gdy są dostępne)

- Gdy skill `ui-verify` jest dostępny: użyj go do zrzutów całych stron, do ustalenia systemowego Chrome i do reguł
  bezpieczeństwa konfiguracji zrzutów. `capture` w trybie ciemnym i na szerokości 390 px (`modes`, `widths`) oraz `a11y`
  pomagają w kontroli kroku 6.
- Gdy skill `prototype` jest dostępny: motyw jasny i ciemny oraz tokeny stylu bierz z jego `references/style.md` i `theme-check.md`.
- Gdy ich nie ma, stosuj regułę własną: zrzuty robisz przeglądarką sterowaną skryptem na fixture, a motyw
  to dwa komplety tokenów kolorów (jasny i ciemny) z kontrastem tekstu co najmniej 4,5:1.
