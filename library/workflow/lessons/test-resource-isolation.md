---
title: Izolacja zasobów testowych
tags:
- testing
- concurrency
paths:
- '**/conftest.py'
- '**/migrations/**'
---

## Objaw

Równoległe biegi testów uszkadzają bazę, zajmują ten sam port albo nadpisują sobie katalog build lub coverage.
Wyniki są losowe albo baza testowa jest zepsuta po biegu.

## Przyczyna

Osobny worktree izoluje pliki kodu, ale nie izoluje bazy danych, portów ani katalogów build/coverage.
Dwa runnery pytest na tej samej bazie testowej potrafią ją uszkodzić.

## Reguła

- Jeden runner na zasób (baza, port, katalog build/coverage) albo potwierdzona pełna izolacja
  (osobna baza, port i katalog wyjściowy dla każdego biegu).
- Reguła dotyczy także hooków i testów uruchamianych przez implementera, nie tylko dedykowanego runnera.
- Zasoby przydziela sesja główna (orkiestrator) przed uruchomieniem. Brak przydziału oznacza `NOT_RUN`
  z komendą do wykonania przez runnera.

## Jak wykryć

- Dwa procesy testów używają tej samej nazwy bazy lub tego samego portu.
- Błędy „baza już istnieje”, „port zajęty” albo migracje wykonywane w trakcie cudzego biegu.
