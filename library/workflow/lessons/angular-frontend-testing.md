---
title: Angular w testach frontendu (Vitest i Karma)
tags:
- angular
- vitest
- karma
- frontend
paths:
- '**/*.spec.ts'
- '**/karma.conf.js'
---

## Objaw

Komponent testowany w Vitest nie reaguje na zmianę wejścia. Test kończy się błędem kompilacji szablonu. Asercja
tekstu pada na podwójnej spacji. Lokalny bieg Karma nie startuje, a ktoś raportuje „testy zielone”.
Po dodaniu metody do serwisu testy konsumentów padają dopiero w CI.

## Przyczyna

Środowisko testowe (jsdom, headless Chrome jako root) różni się od przeglądarki. Ręczne mocki nie śledzą
zmian interfejsu serwisu. Szablony Angulara wstawiają dodatkowe spacje między interpolacjami.

## Reguła

- Wejścia ustawiaj przez `fixture.componentRef.setInput(...)` (wyzwala `ngOnChanges`), potem
  `detectChanges()`, `await whenStable()` i drugi `detectChanges()`. Bezpośrednie przypisanie do pola
  instancji nie wyzwala `ngOnChanges`.
- Obserwacja z jednego źródła, niezweryfikowana szerzej: w środowisku Vitest + jsdom wejścia typu `input()`
  (Signal API) razem z `setInput()` nie dały zmiany stanu komponentu, a dekorator `@Input()` działał. Zanim
  oprzesz o to decyzję projektową, sprawdź na własnej wersji Angulara i Vitest.
- Komponent standalone z `[(ngModel)]` wymaga jawnego importu `FormsModule` w konfiguracji testu.
- Przed asercją tekstu z `textContent` normalizuj białe znaki (`replace(/\s+/g, ' ').trim()`).
- Dla wielu testów tego samego komponentu użyj fabryki (konfiguracja `TestBed`, mock serwisu, `setInput`,
  stabilizacja), zamiast powtarzać szkielet.
- Dodając publiczną metodę do serwisu, przejrzyj `*.spec.ts` z ręcznymi mockami (`useValue: { ... }`).
  Brak metody w mocku nie psuje kompilacji, tylko daje `TypeError: ... is not a function` w czasie działania.
- Karma/ChromeHeadless jako root: bez `--no-sandbox` nie wystartuje. Dodaj tymczasowy `customLauncher`
  i uruchom z nim; zmianę `karma.conf.js` cofnij przed commitem zadania (to zmiana środowiskowa).
  Jeśli `ng` nie jest w PATH, wywołaj `./node_modules/.bin/ng`.
- Przeglądarka, która nie wystartowała, oznacza, że testy się nie wykonały. To nie jest PASS ani
  „pre-existing fail”. O wyniku decyduje podsumowanie (`TOTAL: x SUCCESS`) i exit code. Diagnozując porażkę,
  znajdź konkretny spec po nazwie; lawina `NG0303 ... is not a known element` to zwykle szum.
- Przy baseline częściowym nie używaj `git stash` bez ścieżek, bo schowa też tymczasowy launcher:
  `git stash push <plik...>`.

## Jak wykryć

- Brak linii z podsumowaniem testów w logu, a raport mówi PASS.
- `TypeError` na metodzie serwisu w specu, którego nie dotykałeś.
- Diff zadania zawiera zmiany `karma.conf.js` dotyczące sandboxa.
