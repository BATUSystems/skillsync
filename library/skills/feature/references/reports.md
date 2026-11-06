# Raporty podumysłów: plik + skrót

Czytaj przy pisaniu zlecenia podumysłowi i przy odbiorze raportu.

## Ścieżka

Pełny raport: `.skillsync/runs/<run-id>/<krok>-<rola>.md` (`.skillsync/` jest ignorowany przez git).
`<run-id>` to slug planu/zadania, `<krok>` numer kroku planu. Ścieżkę podaje zlecenie.

## Skrót do orkiestratora (≤ 15 linii)

1. status: `PASS` / `FAIL` / `BLOCKED` / `NOT_RUN` / `CHANGES_REQUIRED` (zawsze, także przy porażce);
2. zmienione pliki (lista lub liczba plus katalog);
3. blokery (jawnie, także „brak”);
4. liczba „Odkryć”;
5. ścieżka pełnego raportu.

Skrót nie może ukrywać porażek: status i blokery są zawsze w skrócie.

## Role z zapisem i role tylko do odczytu

- Role z Write (`implementer`, `test-writer`, `test-runner`, `scribe`, `mechanic`) zapisują pełny raport
  w pliku i zwracają skrót.
- Role tylko do odczytu (`Explore`, `reviewer`, `verifier`) nie mają Write. Dwie dopuszczalne ścieżki:
  (a) orkiestrator zapisuje ich pełny raport do pliku, jeśli jest potrzebny później (np. mapa zmian,
  lista uwag); (b) rola zwraca skrót plus sekcję szczegółów ograniczoną do ≤ 60 linii.
  Zlecenie wskazuje, która ścieżka obowiązuje.

Wyjątek od zakresu zapisu: `mechanic` (Edit + Write), `test-runner` (zapis przez Bash do logów) i `scribe`
(`docs/**`) mogą zapisać raport wyłącznie do `.skillsync/runs/<run-id>/` — nic poza tym katalogiem
(`mechanic` ma Write tylko do tego celu).

## Kiedy orkiestrator czyta pełny raport

Tylko przy `FAIL`/`BLOCKED`/`CHANGES_REQUIRED`, przy liczbie odkryć > 0 oraz przed review
(materiał dla reviewera). Przed odhaczeniem kroku i tak sprawdza rzeczywiste pliki i wyniki poleceń.

## Odkrycia i relacja

„Odkrycia” (wiedza domenowa, nieaktualna dokumentacja, pułapki; każde ze źródłem: plik:linia,
ticket, dane, odpowiedź użytkownika) są w pełnym raporcie, a w skrócie jest ich liczba.
Terminy specyficzne dla projektu zgłaszaj jako potwierdzone definicje ze źródłem albo jako
kandydatów z hipotezą znaczenia i pytaniem do wyjaśnienia. Nie przedstawiaj domysłu z nazwy
klasy jako ustalonego znaczenia biznesowego; przed pracą przeczytaj przekazane rekordy bootstrap.
Podumysły nie czytają pliku relacji ani rozszerzenia osobowości; to dotyczy wyłącznie sesji głównej.
