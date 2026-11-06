---
title: django-tenants TenantTestCase.setUpClass nie woła super() - klasowe override_settings, setUpTestData i transakcja klasowa są martwe
tags:
- testing
- django
- django-tenants
- pytest
- performance
paths:
- '**/conftest.py'
- '**/tests/**'
---

## Objaw

Klasowy `@override_settings` nic nie zmienia, `setUpTestData` nigdy się nie wykonuje, dane z `setUpClass`
zostają zapisane między testami, a pełny bieg trwa bardzo długo. Nie ma żadnego błędu.

## Przyczyna

Obserwacja dla Django 5.2 i django-tenants 3.9. `django_tenants.test.cases.TenantTestCase` w
`setUpClass`/`tearDownClass` tworzy i usuwa schemat tenanta, ale nie woła `super()`. Nie wykonują się więc
`SimpleTestCase.setUpClass` ani `TestCase.setUpClass`. Skutki:

- klasowe `@override_settings` / `@modify_settings` są martwe (np. szybki hasher nigdy nie wchodzi,
  a hasła hashuje się powoli);
- `setUpTestData` nie jest wołane;
- nie ma transakcji klasowej, więc dane z `setUpClass` są zatwierdzane;
- każda klasa testów tworzy schemat od nowa i uruchamia na nim wszystkie migracje, co przy wielu klasach
  zajmuje większość czasu biegu.

## Reguła

- Ustawienia na całą klasę: dekorator metody, `with override_settings(...)` albo
  `override_settings(...).enable()` przed `super().setUpClass()` i `disable()` po `super().tearDownClass()`.
- Ustawienia globalne testów (szybki hasher MD5): `conftest.py`, `pytest_configure` z `trylast=True`,
  nie dekorator klasy.
- Dane klasowe twórz w `setUpClass` po `super().setUpClass()`, nie w `setUpTestData`.
- Sprzątanie po błędzie w `setUpClass` podklasy: `cls.addClassCleanup(...)`, inaczej zostaje schemat
  i kolejne klasy padają.
- Przyspieszenie: szybki hasher w testach i jeden współdzielony schemat tenanta na proces z transakcją
  klasową. Wymaga testów izolacji między klasami (brak przecieku danych, `Client`/`Domain`, sekwencji).
- Nie używaj `--nomigrations` jako przyspieszenia. Pomija `RunPython` i ograniczenia z migracji, więc testy
  constraintów i migracji dają fałszywe wyniki.

## Jak wykryć

- Test z klasowym `override_settings`, którego efekt jest niewidoczny w teście.
- Pomiar czasu: czas przygotowania schematu dominuje nad czasem samych testów.

## Granice

Dotyczy oryginalnego `TenantTestCase` (i `FastTenantTestCase` w zakresie `super()`). Własna baza testów,
która podmienia `setUpClass`, może przywrócić nadpisania i transakcję klasową. `setUpTestData` wymaga
wtedy osobnego wywołania.
