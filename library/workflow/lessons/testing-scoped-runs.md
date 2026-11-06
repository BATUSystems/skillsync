---
title: Testy zawężone, preflight środowiska i filtry w solution
tags:
- testing
- test-selection
- dotnet
paths:
- '**/tests/**'
- '**/*.csproj'
---

## Objaw

Raport dostawy mówi „testy zielone”, ale nie wiadomo, co faktycznie uruchomiono. Albo integracyjne testy
padają z błędem środowiska (brak bazy, brak usługi) i ktoś bierze to za regresję w kodzie. Albo filtrowany
bieg w solution wypisuje „żaden test nie jest zgodny z filtrem” i wygląda jak porażka.

## Przyczyna

Zawężony bieg (`--filter`, jeden projekt, jeden wycinek domeny) jest legalnym dowodem, ale tylko dla swojego
zakresu. Środowisko testowe (baza, seed, usługi sieciowe) bywa niekompletne, a komunikat środowiskowy
wygląda jak błąd testu. W solution z wieloma projektami filtr pasuje tylko do części z nich.

## Reguła

- Zawężony bieg jest dopuszczalny po zmianie lokalnej, o ile raport to jasno oznacza. Zapisz osobno:
  co uruchomiono zawężone i czego nie uruchomiono jako pełnej regresji.
- Preflight kończący się STOP z powodu niekompletnego środowiska jest poprawną decyzją. Błąd środowiskowy
  (np. brak bazy testowej) nie jest dowodem regresji, dopóki nie sprawdzisz dostępności środowiska.
- Opisz zależności preflightu: oczekiwana baza, seed, dostępne usługi. Dodaj do checklisty repo osobny punkt
  o stanie środowiska testowego, żeby raport rozróżniał „kod” od „infrastruktury”.
- Baseline wycinka potwierdzaj wąskim filtrem domenowym: to szybki i obronny dowód zielonego wycinka bez
  uruchamiania całej platformy.
- W filtrowanym biegu po solution komunikat „żaden test nie pasuje do filtra” w projektach pobocznych nie jest
  porażką. Liczy się wynik projektów, które mają testy danego obszaru, oraz końcowy exit code.
- Zmianę z migracją walidujesz dwuetapowo: wąskie testy jednostkowe nowego modelu lub silnika, potem
  migracja, na końcu szerszy filtr całego obszaru. Przy silnikach reguł filtr obejmuje nowy kod i poprzedni
  szew (najpierw unity obu, potem szerszy obszar w solution), żeby wykryć naruszenie starego zachowania.
- Gdy terminal zgubi wynik długiego biegu, powtórz go z przekierowaniem do pliku logu i pokaż koniec logu.
  Nie zgaduj wyniku z ciszy narzędzia.
- Seedując ustawienia tenanta lub inne rekordy z kluczem unikalnym w testach integracyjnych, usuń istniejące
  wpisy przed wstawieniem nowych, inaczej dostaniesz konflikt klucza.

## Jak wykryć

- Raport bez sekcji „uruchomione zawężone / nieuruchomione jako pełne”.
- Wyjątek bazy danych o nieistniejącej bazie lub odmowie połączenia zamiast asercji testu.
- Wynik oparty na komunikacie narzędzia, a nie na exit code i podsumowaniu liczby testów.
