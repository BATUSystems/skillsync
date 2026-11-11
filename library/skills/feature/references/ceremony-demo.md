# Ceremonia `demo`

Czytaj przy doborze zakresu, gdy `skillsync route <projekt>` zwraca `ceremony.value: demo` albo plan ma linię `Ceremonia: demo`.
Tryb jest dla krótkich przebiegów pokazowych: obniża koszt procesu, nie obniża kontroli granic dostępu.

## Odczyt

- Wartość to pole `ceremony.value` z `skillsync route <projekt>` (klucz manifestu `workflow.ceremony`: `full` albo `demo`).
- Brak pola `ceremony` w odpowiedzi (starszy `skillsync`) albo `source: default` = `full`.
- `source: invalid` (błędna wartość w manifeście) = pytanie do użytkownika, jak przy `delivery`; nie zakładaj `full` ani `demo`.
- Plan przy `demo` ma w nagłówku linię `Ceremonia: demo` (jak `Oddanie:`, osobny wiersz); przy `full` linia jest opcjonalna.
  Rozbieżność linii planu z `route` przy „GO implement” = pytanie do użytkownika, bez zgadywania.

## Co zmienia `ceremony: demo`

- **Review `kod`**: `demo` zmienia liczbę pętli: jedno review `kod` na cały przebieg (jedna runda na końcu, zamknięcie majorów przez `verifier`) PLUS pętla pełna
  ([review-loop](review-loop.md#kategoria-pętli-i-przejścia)) dla każdego kroku ze strażnikiem wycieków, migracją albo bezpieczeństwem, plus runda 2 po blockerze, jak w [review-loop](review-loop.md).
  Pętla końcowa pozostaje pełna, gdy cała zmiana należy do kategorii D113. Wszystkie trzy kategorie D113 zostają bez zmian; `demo` ich nie osłabia.
- **Test-writer**: tylko w fazach z uprawnieniami lub izolacją danych; w pozostałych testy jednostkowe pisze `implementer` fazy.
- **Dokumentacja**: ograniczona do changeloga i „Wyniku” planu.
- **Odroczone**: `retro-quality` i rekordy wiedzy (`docs/domain`, `docs/lessons`) przechodzą do podsekcji „Dług (ceremony: demo)”
  w „Wyniku” planu: lista odroczonych rekordów (temat, źródło, proponowany właściciel zapisu). Dług spłaca człowiek albo kolejny przebieg `full`.
- **Bez zmian**: testy, ustalona wersja, rekord `--final`, zamknięcie planu i pętle według kategorii. Plan kończy się `done` z wypełnioną
  podsekcją „Dług (ceremony: demo)”. Relaksacja w regule „Done” rdzenia dotyczy wyłącznie dokumentacji ponad changelog i „Wynik”,
  `retro-quality` i rekordów wiedzy. `skillsync checkpoint --final` zgłosi wtedy ostrzeżenie `retro-quality-missing`; jest oczekiwane i nie blokuje.
  Dług zastępuje `retro-quality` także w liście warunków przed stanem „czeka na READY/CP”.
