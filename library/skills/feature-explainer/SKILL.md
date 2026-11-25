---
name: feature-explainer
description: "Strona wyjaśniająca działanie ekranu dla QA i biznesu: reguły z kodu, zrzuty z danych przykładowych, FAQ. Tylko na prośbę lub za zgodą."
---

# feature-explainer — jak działa ekran, wyjaśnione osobie spoza zespołu

Skill prowadzi przygotowanie **jednej strony** o jednej funkcji lub jednym ekranie dla osoby spoza zespołu programistów
(QA, biznes, wsparcie). Strona odpowiada na pytanie „dlaczego widzę to, co widzę”: skąd biorą się wartości, jakie są reguły
i stany, co zależy od konfiguracji. Reguły są potwierdzone w kodzie, przykłady stoją na zrzutach z danych przykładowych,
a pytania odbiorcy mają odpowiedzi.

## Kiedy używać, a kiedy nie

- Używaj na prośbę o opis działania ekranu lub funkcji dla osoby spoza zespołu („przygotuj dokumentację z przykładami”,
  „ktoś pyta, jak liczony jest postęp”).
- Używaj też, gdy do funkcji wpadają uwagi QA: wtedy strona dostaje sekcję FAQ (analizę błędów prowadzi ktoś inny, sekcja
  przyjmuje tylko jej wynik).
- **Skill nie startuje sam.** Gdy pojawi się któraś z dwóch sytuacji powyżej (prośba o opis działania ekranu dla osoby spoza
  zespołu albo zgłoszenie uwag QA), zaproponuj go jednym zdaniem i czekaj na zgodę. Bez zgody niczego nie twórz.
- Nie używaj do dokumentacji API i kodu dla programistów, instrukcji wdrożenia ani analizy błędów.

## Źródła prawdy (kolejność)

1. Rekord wiedzy domenowej projektu o funkcji (reguły, decyzje, konfiguracja).
2. **Kod**: każda reguła jest potwierdzona przed opisem, z notatką `plik:linia` ([references/evidence.md](references/evidence.md)).
3. Konfiguracja wzorcowa, zawsze oznaczona jako przykładowa (identyfikatory bywają różne w każdej instalacji).
4. Zgłoszenie odbiorcy: pytania i uwagi, treść do sekcji FAQ.
5. Opcjonalnie niezależna weryfikacja przez drugiego agenta: fakty zgodne z kodem, brakujące niuanse.

Gdy dokument i kod się różnią: **opisz kod** i zgłoś rozjazd jako uwagę do poprawy dokumentu.

## Procedura (7 kroków)

1. **Zakres.** Ustal funkcję lub ekran, odbiorcę i jego pytania. Zgoda użytkownika na start jest warunkiem.
2. **Źródła.** Przeczytaj rekord domenowy i kod; prowadź notatki robocze reguła → `plik:linia` ([evidence.md](references/evidence.md)).
3. **Weryfikacja.** Sprawdź każdą regułę w kodzie, zapisz rozjazdy, opcjonalnie poproś drugiego agenta o niezależne sprawdzenie.
4. **Zrzuty.** Przygotuj zrzuty z danych przykładowych: wycinek na wariant plus jeden przeglądowy ([screenshots.md](references/screenshots.md)).
5. **Strona.** Złóż stronę z szablonu `assets/template.html` według [page-structure.md](references/page-structure.md): 9 sekcji, podpisy „dlaczego”, data i wersja w nagłówku.
6. **Kontrola.** Sprawdź listę kryteriów niżej oraz wygląd w obu motywach i na szerokości telefonu (390 px).
7. **Publikacja.** Domyślnie plik w repo ([publish-static.md](references/publish-static.md)); strona prywatna w hoście tylko na życzenie lub za zgodą ([publish-artifact.md](references/publish-artifact.md)).

## Reguły twarde

- **Tylko dane przykładowe** na zrzutach (makieta, fixture, środowisko testowe), nigdy dane osobowe. Nagłówek strony mówi,
  że zrzuty są z danych przykładowych. Zrzutów od odbiorcy z danymi osób nie publikuj.
- **Reguła bez potwierdzenia w kodzie nie trafia na stronę.** Każda ma notatkę `plik:linia`.
- **Rozjazd dokument–kod:** opisz kod, zgłoś rozjazd.
- **Każdy przykład ma podpis „dlaczego”** (reguła i liczby), np. „67%: zrobione 2 z 3 wymaganych kroków”.
- **Obrazy jako pliki obok strony**, nie `data:` w HTML.
- **Bez obietnic poprawek**, których nikt nie zatwierdził (FAQ podaje stan: reguła, konfiguracja, do analizy albo błąd).
- **Publikacja:** domyślnie plik w repo `docs/explainers/<slug>/` (`index.html` i obrazy obok, ścieżki względne), w każdym
  hoście. Strona prywatna w hoście (Artifact) jest opcją na życzenie lub za propozycją, gdy odbiorca nie ma dostępu do
  repo. Bez decyzji użytkownika jej nie publikuj.
- **Budowanie i zrzuty nie zmieniają drzewa roboczego innych sesji** (budowa do katalogu tymczasowego, kopia skryptu).

## Kryteria akceptacji strony

- Każda reguła ma potwierdzenie w kodzie; rozjazdy są zgłoszone.
- Zrzuty tylko z danych przykładowych, każdy przykład ma podpis „dlaczego”.
- Wszystkie pytania odbiorcy są w FAQ ze stanem (reguła, konfiguracja, do analizy, błąd).
- Dziewięć sekcji, spis treści z kotwicami, data i wersja w nagłówku.
- Motyw jasny i ciemny, szerokość 390 px bez poziomego przewijania strony, tabele w kontenerach z przewijaniem.
- Brak `data:` w obrazach, brak zewnętrznych skryptów i czcionek, każdy obraz ma `alt` i leży obok strony.

## Aktualizacja

Po zmianie reguł (np. po poprawce błędu) zaktualizuj sekcje reguł i FAQ oraz datę i wersję w nagłówku, potem powtórz kontrolę
i publikację. Starzenie się strony pilnuje człowiek: nic jej nie odświeża samo.

## Odwołania

- [references/page-structure.md](references/page-structure.md): szablon i język strony.
- [references/evidence.md](references/evidence.md): notatki, rozjazdy, weryfikacja, statusy FAQ.
- [references/screenshots.md](references/screenshots.md): zrzuty.
- [references/publish-static.md](references/publish-static.md), [references/publish-artifact.md](references/publish-artifact.md): dwie drogi publikacji.
- `assets/template.html`: samodzielna strona z przykładem syntetycznym.
