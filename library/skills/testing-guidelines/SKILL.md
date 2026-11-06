---
name: testing-guidelines
description: Cross-project testing guidelines for unit/integration/e2e strategy, test structure, and quality gates.
user-invocable: true
---

# Testing Guidelines (Cross-Project)

Ten skill zawiera neutralne zasady testowania niezależne od konkretnego frameworka.

## Kiedy używać

Użyj tego skilla, gdy definiujesz lub egzekwujesz:

- strategię testów (unit/integration/e2e),
- strukturę testów,
- kryteria jakości przed mergem/releasem,
- podejście do mocków i fixture.

## Pyramid baseline

- **Unit tests**: szybkie, deterministyczne, izolowane.
- **Integration tests**: krytyczne przepływy na realnych granicach modułów.
- **E2E tests**: tylko najważniejsze ścieżki użytkownika.

## Test design rules

- AAA (`Arrange`, `Act`, `Assert`) i jedna odpowiedzialność testu.
- Nazwy testów opisują zachowanie, nie implementację.
- Unikaj over-mockingu; mockuj tylko granice systemu.
- Dane testowe powinny być minimalne i czytelne.

## Quality gates (minimum)

1. Wszystkie testy przechodzą lokalnie i w CI.
2. Brak flaky tests w krytycznych ścieżkach.
3. Nowa logika ma pokrycie adekwatne do ryzyka.
4. Błędy regresyjne mają test reprodukujący.

## Practical workflow

- Najpierw test czerwony (RED), potem implementacja (GREEN), potem porządki (REFACTOR).
- Dla bugfixa dodaj test, który padał przed poprawką.
- Uruchamiaj najpierw testy punktowe, potem szerszy zestaw.

## .NET / OpenTelemetry: Package Reality Check

- Przed dodaniem instrumentacji OTel sprawdź, czy dana paczka ma stabilne wydanie zgodne z baseline projektu.

## Static checks after refactor (required)

- Po większym refaktorze uruchom diagnostykę IDE (`get_errors`) dla zmienionych plików.
- Przed zamknięciem zadania uruchom `get_errors` dla całego workspace.
- Traktuj błędy typu "missing attribute" i "unresolved import" jako blokery, nawet jeśli runtime czasem "działa".
- Dla skryptów narzędziowych (np. parsery SQL) dodaj szybki smoke run po poprawkach.

## Performance integration tests (lesson from W-B)

- Dla endpointow analytics dodawaj jawne testy wydajnosciowe na datasetach 10k i 100k eventow.
- W testach integracyjnych mierz czas przez `Stopwatch`, a wynik loguj przez `ITestOutputHelper` (np. `286 ms`, `471 ms`) jako artefakt do raportu milestone.
- Asercje wydajnosci trzymaj jako progi kontraktowe (np. `<3s` dla 10k i `<5s` dla 100k) oraz uruchamiaj je z filtrem testowym dedykowanym paczce.
- Benchmark endpointu wykonuj po seedzie deterministycznym; unikaj mieszania z calym suite, bo szum innych testow zaciemnia wynik.

## Project-specific variants

Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.

## Indeks doświadczeń

Lekcje zostały wydzielone do bibliotecznego `workflow/lessons/index.yaml`.
Dobierz rekord przez `skillsync route <projekt> --topic angular` (albo dotnet/testing).
Nie wczytuj całej historii testów na początku zadania.
