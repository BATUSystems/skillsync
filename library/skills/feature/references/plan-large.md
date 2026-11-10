# Plan zmiany dużej albo niejasnej

Czytaj ten plik tylko wtedy, gdy bramka planu w kroku 3 `feature` wskaże `plan-large`. Uruchamia go zmiana, która dodaje zdolność, rusza architekturę albo schemat danych, zmienia kontrakt API, sięga kilku modułów lub faz, albo startuje z briefu pełnego krytycznych niewiadomych.

Zanim cokolwiek utworzysz, otwórz `docs/plans/` i `docs/specs/`. Plan, który już pokrywa zadanie, zaktualizuj (tryb `reuse`). Niedokończony szkielet speca z pytaniami (status `draft`, bez planu) wznów, zamiast zakładać nowy ([spec](spec.md)).

Bugfix, dokumentację, zależności i izolowany refaktor planuj zwykłym planem ([plan](plan-details.md)).

Spec żyje pod `docs/specs/RRRR-MM-DD-<slug>.md`; jego format, statusy i moment zamrożenia podaje [spec](spec.md). Plan wskazuje spec przez `Spec:` i opisuje wyłącznie wykonanie.

Ten dokument prowadzi akt I (spec, pętla `spec`, zgoda GO plan). Akt II (plan, pętla `plan`, zgoda GO implement) oraz tabele stanów opisują [pętla przeglądu](review-loop.md) i [spec](spec.md).

## 1. Szkielet i twarda bramka pytań — STOP

- Zakaz pisania speca w jednym kroku. Pierwszy zapis to szkielet: `draft` z częścią o problemie, o zakresie i sekcją `Pytania otwarte`. Zanim padnie GO plan, plan nie powstaje.
- Krytyczna niewiadoma to założenie, którego pomyłka oznaczałaby przepisanie dużej części speca; typowe przypadki: format danych, zasięg, miejsce połączenia z resztą systemu, dobór narzędzia.
- Sprawdź podział: gdy zlecenie obejmuje kilka zdolności, z których każda działa samodzielnie, zapytaj użytkownika, czy rozbić je na osobne plany i specy.
- Każde pytanie dostaje numer `Q1`, `Q2`… i własną, krótką linię; wybieraj formę tak/nie lub zestaw opcji.
- Po pokazaniu szkieletu zatrzymaj się i czekaj na odpowiedzi użytkownika.
- Do czasu odpowiedzi działa tylko `explore` oraz zbieranie danych niezależnych od pytań: kto korzysta z modułu, jak zbudowany jest kod, jakie kontrakty już istnieją. Projekt na domysłach jest zakazany.
- Niewiadomą odkrytą później zgłaszaj bramką ponownie, tylko dla nowych pytań.
- Gdy odpowiedzi padną, `Pytania otwarte` znikają z dokumentu; rozstrzygnięcia lądują w `Decyzje i alternatywy`, a spec uzupełnia się o kryteria akceptacji i mapę FRD ([spec](spec.md)).

## 2. Sekcje rozszerzone

Umiejscowienie: w specu, poza fazami i krokami — te są w planie.

- Decyzje i alternatywy: dwie tabele — decyzja z uzasadnieniem oraz alternatywa z powodem odrzucenia.
- `Badanie wzorców` tylko wtedy, gdy zmiana wnosi nowy mechanizm. Zestawienie: co sprawdzono, co wybrano, co odrzucono i z jakiego powodu. Nazwa zgodna z szablonem speca.
- Fazy oraz kroki żyją w planie: faza jest przyrostem nadającym się do wydania, krok zadaniem dającym się sprawdzić testem; aplikacja po każdym kroku pozostaje sprawna. Odroczone pomysły nazywaj wprost, bez mieszania ich z MVP.
- Rejestr ryzyk w sekcji `Ryzyka`. Każda pozycja: scenariusz, waga (`critical`, `high`, `medium`, `low`), obszar, którego dotyczy, sposób łagodzenia, pozostałe ryzyko i uzasadnienie, czemu jest akceptowalne.
- Pięć kategorii do przejścia: dane (przerwanie w trakcie, równoległe zapisy, zapis niepełny); efekty łańcuchowe; rozdział najemców i uprawnień; przejście między wersjami (równoległa praca starej i nowej, ponawianie, uzupełnianie wsteczne); utrzymanie (wykrywanie awarii, zasięg szkód, wzrost wolumenu).
- Każda mutacja ma sposób odwrócenia albo jawną deklarację nieodwracalności z uzasadnieniem.

## 3. Przegląd w świeżym kontekście

- Kolejność po odpowiedziach nie podlega wyborowi. akt I: `pre-implement`, jeśli dotyczy (wynik jest tylko do odczytu; kontrakty, migrację i ryzyka przenosi orkiestrator), następnie raport spójności (§4), pętla `spec`, zgoda GO plan. akt II: plan, pętla `plan`, `skillsync readiness` z linią `Spec:`, decyzja o wersji, zgoda GO implement. Zasady pętli: [pętla przeglądu](review-loop.md).
- Każda runda to osobny podumysł z rolą `reviewer`, bo autor nie przeprowadzi surowej recenzji swojego tekstu. Liczbę rund wyznacza kategoria pętli z [pętli przeglądu](review-loop.md): pętla pełna tylko dla strażnika wycieków, migracji i bezpieczeństwa; w pozostałych przypadkach jedna runda, a kolejna tylko po blockerze. Samo ryzyko high ani `plan-large` nie dają pętli pełnej; powtórzenie rundy bez blockera to nadmiar ceremonii (`ceremony-excess`). Checklistę i materiał dla profili `spec` oraz `plan` masz w tym samym dokumencie.
- Werdykt `podziel` nie uruchamia przepisania; trafia do użytkownika w formie pytania.

### Challenger pomysłu (opcjonalny, dla zmiany dużej lub kosztownej)

- Kiedy: szkielet speca leży już na dysku, a użytkownik jeszcze nie dostał pytań.
- Wykonawca: `reviewer` w profilu `spec`, z zleceniem ataku na sam wniosek, nie na formę.
- Pięć pytań: czy problem istnieje i kogo dotyczy; czy rzetelnie zważono wariant bez zmian i wariant mniejszy; czy tempo i rampa wdrożenia odpowiadają ryzyku; które nieprzetestowane założenie jest najbardziej ryzykowne; czy opis zrozumie na zimno osoba bez kontekstu.
- Wynik przekładaj na statusy profili pętli roli `reviewer`; poważny problem oznacza `CHANGES_REQUIRED`.
- Uwagi challengera przychodzą do użytkownika jako `C1`, `C2`…, równolegle z `Q…`; zamyka je wyłącznie użytkownik. Odpowiedzi kasują obie listy pytań z dokumentu.

## 4. Raport spójności

Miejsce: w specu, przed zatwierdzeniem. Tabela o kolumnach `sprawdzenie | status | uwagi`. Obowiązkowo sprawdź: modele danych względem kontraktów API; kontrakty względem UI; czy ryzyka pokrywają każdy zapis; czy każda mutacja ma odwrócenie; czy cache i odczyty mają strategię unieważniania.

- Dołącz macierz zgodności z instrukcjami projektu o kolumnach: reguła, źródło, stan. Przy każdej rozbieżności podaj regułę, źródło, lukę oraz poprawkę.
- Werdykt binarny: `zgodny` albo `zablokowany` do czasu poprawek.

## 5. Po zgodzie

- Po zgodzie na GO plan: spec przechodzi w status `approved`, a obok od razu powstaje szkic planu z `Status: draft` i liniami `Spec:`, `Spec-sha:`, `Przebieg:`. Przy FRD dochodzi sha pliku FRD oraz założenia z `LUKI` ([frd-intake](frd-intake.md)).
- Wyjątek: gdy plan już istnieje (przebieg `direct`, który odsłonił zmianę kontraktu), szkicu nie tworzysz; do istniejącego planu dopisz `Spec:` i `Spec-sha:`.
- Zgoda GO implement otwiera akt III. Wznawianie, sha speca i statusy wyjaśniają [spec](spec.md) oraz [plan](plan-details.md).
