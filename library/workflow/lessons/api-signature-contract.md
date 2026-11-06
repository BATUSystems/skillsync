---
title: Kontrakty testów API (podpisy, nagłówki, filtry, kodowanie, redakcja)
tags:
- testing
- contracts
- api
- security
paths:
- '**/tests/**'
- '**/*Tests.cs'
---

## Objaw

Test przechodzi, a regresja bezpieczeństwa lub kontraktu API zostaje niezauważona: podpisany pakiet
akceptuje zmanipulowany podpis, endpoint publiczny zwraca `403` zamiast właściwego błędu, raport CSV
porównywany jako cały tekst psuje się przy zmianie formatu liczby.

## Przyczyna

Asercja sprawdza tylko status HTTP albo surowy tekst. Brakuje przypadków negatywnych, nagłówków bezpieczeństwa
i dowodu efektu ubocznego. Hash lub podpis liczony inaczej niż w backendzie przechodzi etap publikacji,
a odpada przy instalacji.

## Reguła

- Przepływ pakietu podpisanego ma minimalny komplet trzech testów integracyjnych: ścieżka poprawna,
  zmanipulowany podpis (odrzucenie), pakiet bez podpisu (odrzucenie).
- Rozdziel niezgodność skrótu od niezgodności podpisu. Niezgodność skrótu kończy walidację przed etapem
  kryptograficznym (fail-fast).
- Skrót (`sha256` definicji) w danych testowych licz po tej samej kanonikalizacji JSON, której używa backend.
  Skrót z surowego tekstu lub innego serializera może przejść publikację, a odpaść przy instalacji.
- Rotację kluczy testuj osobno dla trybu planowego (okres karencji aktywny) i awaryjnego (natychmiastowe
  odcięcie).
- Pobieranie podpisanym tokenem: cztery przypadki: token ważny (200), wygasły, zmanipulowany i dostęp bez
  tokenu (401/403). Asercje obejmują nagłówki `Cache-Control: no-store` i `Pragma: no-cache`.
- Testy integracyjne sprawdzają status HTTP, kod błędu domenowego (`errorCode`) i zdarzenie audytowe.
  Dla efektu biznesowego (np. automatyczne zawieszenie licencji) sprawdź też zmianę stanu.
- Dane poufne: test „obecne + zredagowane”. Dane istnieją w modelu odczytu, ale nie ma ich w eksporcie.
- Endpointy publiczne z polityką osadzania (formularze publiczne, linki jednorazowe): ustawiaj w testach
  jawnie `Origin` lub `Referer`. Bez nich weryfikacja osadzania ma pierwszeństwo przed logiką i maskuje
  regresję tokenu jako `403`. Zabezpieczenia takich endpointów mają dedykowaną asercję `errorCode`
  oraz testy zastępczego `Origin`/`Referer`.
- Raporty CSV i rozliczeniowe: normalizuj końce linii i asertuj semantycznie poszczególne wiersze i kolumny.
  Format `decimal` (`50`, `50.0000`) różni się bez zmiany znaczenia.
- Test E2E efektu ubocznego (np. rozstrzygnięcie powodujące zwrot) ma go sprawdzać przez ten sam endpoint
  i przepływ, a nie przez ręczne drugie żądanie, które maskuje brak orkiestracji.

## Jak wykryć

- Test kontraktu z jedną asercją statusu.
- Brak przypadków negatywnych dla podpisu lub tokenu.
- `INVALID_PACKAGE` lub podobny błąd instalacji po udanej publikacji danych testowych.
