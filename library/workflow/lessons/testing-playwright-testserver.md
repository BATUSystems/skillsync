---
title: Playwright E2E i TestServer w testach backendu (okna dialogowe, proxy, WebSocket, EF)
tags:
- testing
- e2e
- playwright
- dotnet
paths:
- '**/e2e/**'
- '**/tests/**'
---

## Objaw

Scenariusz E2E zawiesza się na `confirm()`/`alert()`. Testy przechodzą na hoście backendu, a w przeglądarce
trasa frontendu się nie ładuje. Smoke test WebSocket w `TestServer` wypisuje wyjątek mimo zielonego wyniku.
Filtr po polu JSON w zapytaniu EF działa inaczej niż w pamięci.

## Przyczyna

- Okna dialogowe przeglądarki blokują krok, jeśli runner ich nie obsłuży.
- Backend i frontend stoją za proxy: adres backendu odpowiada, a routing frontendu bywa uszkodzony.
- `TestServer` po zamknięciu połączenia WebSocket potrafi zalogować błąd, że odpowiedź już się rozpoczęła.
- Provider EF może mapować pole tekstowe jak `jsonb`, wtedy tłumaczenie `Contains` jest niedostępne albo
  zwraca inny wynik niż oczekiwany.

## Reguła

- W definicji kroku `click` runnera Playwright jawnie ustaw akceptację dialogu (np. flagą `accept_dialog`),
  jeśli akcja wywołuje `confirm()` lub `alert()`.
- Bazowy URL testów E2E to adres serwera deweloperskiego frontendu (z proxy), a nie sam host backendu.
- Dla testów na lokalnym serwerze frontendu dodaj scenariusz sprawdzający nawigację (`routerLink`, zmiana
  trasy), bo API może działać, a trasa UI nie.
- Smoke testy funkcji z katalogiem i szczegółami powinny obejmować katalog, szczegóły i pełny przepływ
  instalacji lub zakupu, żeby rozbieżność backend/frontend wyszła wcześnie.
- Dla WebSocket w `TestServer` szum logu „status code cannot be set, the response has already started”
  po zamknięciu połączenia traktuj jako szum frameworka, jeśli test kontraktowy jest zielony.
- Przy filtrowaniu po polu mapowanym jak `jsonb` nie polegaj na tłumaczeniu `Contains`: pobierz ograniczoną
  próbkę i filtruj w pamięci.

## Jak wykryć

- Test E2E bez wyniku po kliknięciu akcji destrukcyjnej.
- Zielone API i czerwony ekran trasy przy tym samym teście.
- Wyjątek tłumaczenia zapytania EF w logu testu.
