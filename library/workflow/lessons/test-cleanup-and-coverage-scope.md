---
title: Sprzątanie artefaktów testów i pomiar coverage wycinka
tags:
- testing
- coverage
- cleanup
paths:
- '**/*.runsettings'
- '**/tests/**'
---

## Objaw

Kolejne biegi mają zanieczyszczony stan i fałszywe trafienia. Sprzątanie po testach kasuje śledzone pliki
referencyjne. Gate coverage dla wycinka systemu pokazuje wynik liczony dla całego kodu i nie nadaje się do
decyzji. Status „unknown” albo „partial” zostaje po tym, jak wynik już jest znany.

## Przyczyna

Testy integracyjne zostawiają artefakty w lokalnym rejestrze lub katalogu danych, który zawiera też pliki
śledzone w repo. Domyślny collector coverage liczy cały kod produktu. Raport gate przepisuje stary status.

## Reguła

- Po zestawie testów integracyjnych uruchom sprzątanie artefaktów wygenerowanych przez testy.
- Nie używaj ślepego usuwania po masce (`rm katalog/*.ext`), jeśli ten sam katalog trzyma śledzone pliki
  referencyjne. Usuwaj tylko artefakty testowe (np. po przedrostku nazwy) albo jawnie przywróć śledzone
  pliki po sprzątaniu.
- Gate obejmujący tylko wycinek systemu mierz osobną konfiguracją coverage (np. `.runsettings`) z filtrami
  include dla wycinka. Domyślny pomiar całego kodu jest bezużyteczny dla takiej decyzji.
- W raporcie gate zapisuj naraz: zakres pomiaru, surowy wynik (`lines-covered / lines-valid`,
  `branches-covered / branches-valid`) i czy dotyczy całego produktu, czy wycinka.
- Po zmierzeniu coverage nie zostawiaj stanu „unknown” ani „partial” z wcześniejszej iteracji. Wynik poniżej
  progu oznacza czerwony gate, nie „bursztynowy”.
- Oddzielaj dowód techniczny od formalnej akceptacji. „Dowody przygotowane” nie znaczy „bezpieczeństwo
  lub zgodność zatwierdzone”, nawet gdy build, regresja i macierz testów są zielone.

## Jak wykryć

- `git status` po testach pokazuje usunięte lub zmienione pliki referencyjne.
- Coverage wycinka bliski wartości dla całego kodu przy braku filtrów include.
- Raport gate bez zakresu pomiaru.
