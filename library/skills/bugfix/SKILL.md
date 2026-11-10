---
name: bugfix
description: Small bug fix with root cause proven from evidence, a narrow patch, a regression test and independent review; larger changes go to feature.
---

# Bugfix — mała, zweryfikowana poprawka błędu

Procedura obejmuje sprawdzenie zgłoszenia, brief z przyczyną na dowodach, test regresji czerwony przed poprawką, minimalną zmianę, kontrole, wiedzę, changelog, pętlę review i zamknięcie przebiegu. Gdy zmiana rośnie, przechodzisz do `feature`.

## Przebieg

1. **Krok 1 — stan problemu.** Sprawdź, czy błąd nadal występuje, czy nie ma już poprawki lub PR, oraz stan roboczy repo i bramkę otwartych planów ([feature](../feature/SKILL.md#rozpoznanie-i-plan), krok 1). Odróżnij odtworzenie błędu od wniosku wyciągniętego z kodu. Brak odtworzenia to jawne ograniczenie, nie zamknięcie zgłoszenia.
2. **Krok 2 — brief.** Zapisz łańcuch wyzwalacz → ścieżka w kodzie (`plik:linia`) → błędny wynik, z zakresem, oczekiwanym zachowaniem, testem regresji i ryzykami. Pewność każdej tezy o przyczynie podajesz jawnie: odtworzona albo wyczytana z kodu. Słaba pewność oznacza `LOW_CONFIDENCE`, brak poszerzania zakresu i pytanie do użytkownika. Zmiana kontraktu kieruje do `pre-implement`. Podumysły dostają w zleceniu ścieżkę raportu i oddają skrót na 15 linii ([raporty](../feature/references/reports.md)); pliku relacji nie czytają.
3. **Krok 3 — test regresji (oracle) przed poprawką.** Przed poprawką test ma być czerwony z właściwego powodu i widoczny na kodzie wyjściowym; porażkę pokaż w raporcie ([oracle](references/oracles.md)). Jeśli automatyczny test nie wchodzi w grę (środowisko, konfiguracja, UI), odnotuj ograniczenie, nadaj `LOW_CONFIDENCE` i dołącz ręczną weryfikację (kroki oraz wynik). Czerwonego testu nie fabrykuj. Potem implementer likwiduje źródło usterki, nie jej skutek, i koryguje istniejący test; asercje zostają mocne, a `sleep` i wyciszanie błędów odpadają.
4. **Krok 4 — kontrole.** Runner odpala `smart-test` oraz profil final, jeśli jest wymagany.
5. **Krok 5 — wiedza.** Rola `scribe` zapisuje, skąd wziął się błąd i jakiej reguły dotyczył: w dokumentacji obszaru oraz w `docs/domain` (reguła biznesowa) albo `docs/lessons` (pułapka techniczna), zgodnie z [knowledge.md](../workflow/references/knowledge.md). Zapis sprawdza reviewer.
6. **Krok 6 — changelog i wersja.** Wpis w changelogu wg [release.md](../workflow/references/release.md); proponujesz numer (najczęściej patch) i pytasz użytkownika o wersję. Bez odpowiedzi numer się nie zmienia. Gdy zgłoszenie weszło kluczem, zgodę dało już „GO implement” (wersja zapisana w briefie) i nie pytasz drugi raz. Warunek wejścia do pętli: przed jej pierwszą rundą diff zawiera dokumentację, changelog i uzgodnioną wersję, a profil final (krok 4) przechodzi na tym stanie.
7. **Krok 7 — pętla `kod`.** Pętla końcowa wg [review-loop](../feature/references/review-loop.md). Pierwsza runda ocenia bieżący diff; właściciel uwagi ją poprawia, po czym ponownie uruchamiasz testy, których dotyczy poprawka, i profil final. Zamknięcie uwag ocenia kolejna runda w świeżym podumyśle `reviewer`; `verifier` tylko do pojedynczych twierdzeń poza pętlą. Bez PASS z pętli nie zamkniesz zadania; STOP wyklucza zamknięcie.
8. **Krok 8 — codereview.** Zanim otworzysz PR, zaproponuj `codereview` (tryb „przed PR”) dla skończonych zmian.

**Wejście z klucza zgłoszenia** (także `/bugfix <KLUCZ>`): najpierw [ticket-intake](../feature/references/ticket-intake.md). Brief z kroku 2 staje się planem `docs/plans/RRRR-MM-DD-<klucz>.md` z nazwami gałęzi (bazowej i roboczej) oraz proponowaną wersją. Potem STOP do czasu „GO implement”, zanim powstanie test i poprawka (brief jest skróconą wersją aktu II). Gdy poprawka i review są gotowe, kod pozostaje niezacommitowany: zestawienie, STOP do przeglądu przez człowieka, a PR dopiero po „rób PR” ([pr-after-approval](../feature/references/pr-after-approval.md)). W tym wariancie krok 8 zastępuje reguła: `codereview` odpalasz wyłącznie przy System Impact High albo ryzyku high, w pozostałych przypadkach jedynie proponujesz; dopiero po utworzeniu PR-ów wpisz `done` i `--final`. Bez klucza przebieg się nie zmienia.

**Błąd opisany w FRD** (`docs/frd/`): jednorazowa ocena według [frd-intake](../feature/references/frd-intake.md) wykonywana przed briefem (gdy wejściem jest klucz, robi ją `ticket-intake`). W planie briefu mapa wymagań, sha pliku FRD i założenia; `reviewer` sprawdza je względem pliku.

**Poza wariantami:** zgłoszenie nowego zachowania zamiast błędu oznacza pytanie, czy przechodzimy do `feature`. Poprawka, która odsłania zmianę kontraktu lub danych nieopisaną w briefie, wymaga pytania o [spec](../feature/references/spec.md); zakresu nie poszerzasz samodzielnie, a zgoda oznacza przejście do `feature`.

**Kandydat `skillsync retro`** w stanie `ready` lub `recurred`, zatwierdzony przez użytkownika, może być zgłoszeniem: jego `failing_case` staje się punktem startu dla oracle, a poprawkę prowadzi jeden właściciel. Po niej zapisz `ref` poleceniem `skillsync retro-decide --decision approved --replace --ref <commit>` ([pomiar](../feature/references/measurement.md)).

## Zasady końcowe

- Przy drobnej edycji nie uruchamiaj całego zespołu ról; rosnący zakres przenieś do `feature`.
- Pilnuj zatwierdzenia zadania, baseline oraz granic publikacji.
- Zapisuj checkpoint po każdym rezultacie kroku, również po porażce, oraz gdy przebieg zostaje przerwany. Zamknięcie przebiega w tej kolejności: najpierw checkpoint `retro-quality` z retrospektywą jakości ([retro-quality](../feature/references/retro-quality.md)), potem krok `final` z `--status PASS --final`, na końcu `skillsync retro` ([pomiar](../feature/references/measurement.md)).
