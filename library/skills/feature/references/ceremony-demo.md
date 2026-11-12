# Ceremonia `demo`

Czytaj przy doborze zakresu, gdy `skillsync route <projekt>` zwraca `ceremony.value: demo` albo plan ma linię `Ceremonia: demo`.
Tryb jest dla krótkich przebiegów pokazowych: obniża koszt procesu, nie obniża kontroli granic dostępu.

## Odczyt

- Wartość to pole `ceremony.value` z `skillsync route <projekt>` (klucz manifestu `workflow.ceremony`: `full` albo `demo`).
- Brak pola `ceremony` w odpowiedzi (starszy `skillsync`) albo `source: default` = `full`.
- `source: invalid` (błędna wartość w manifeście) = pytanie do użytkownika, jak przy `delivery`; nie zakładaj `full` ani `demo`.
- Plan przy `demo` ma w nagłówku linię `Ceremonia: demo` (jak `Oddanie:`, osobny wiersz); przy `full` linia jest opcjonalna.
  Rozbieżność linii planu z `route` przy „GO implement” = pytanie do użytkownika, bez zgadywania.

## Po specu i review: następny krok to prototyp

Przy `demo` samo ukończenie speca i review `spec` nie jest powodem, by bezczynnie czekać na „GO plan”. Gdy spec ma ekrany albo
sekcje UI, następnym krokiem jest prototyp (skill `prototype`, [plan-details](plan-details.md#bramka-prototypu)): zbuduj go od razu,
przygotuj szkic planu `draft (szkic — czeka na GO plan)` i zatrzymaj się dopiero na akceptacji prototypu (STOP, `skillsync prototype wait`).
Bez ekranów w specu nie ma prototypu: poproś o „GO plan” od razu. Zgody nadal daje wyłącznie użytkownik; prototyp nie jest „GO plan”.

## Co zmienia `ceremony: demo`

- **Review `kod`**: `demo` zmienia liczbę pętli: jedno review `kod` na cały przebieg (jedna runda na końcu, zamknięcie majorów przez `verifier`) PLUS pętla pełna
  ([review-loop](review-loop.md#kategoria-pętli-i-przejścia)) dla każdego kroku ze strażnikiem wycieków, migracją albo bezpieczeństwem. W `demo` jednorundowe review `kod` ma **najwyżej 2 rundy**: druga tylko przy blockerze, przy co najmniej 3 majorach, gdy poprawki dotknęły strażnika wycieków, migracji lub bezpieczeństwa (w tym uprawnień) albo po drugim `REFUTED` tej samej uwagi; inaczej majory zamyka `verifier`, a trzeciej rundy nie ma (dalsze uwagi: poprawka i `verifier` albo zapis do „Długu”). Druga runda z tych powodów nie jest `ceremony-excess`. Pętle pełne (kroki ze strażnikiem wycieków, migracją, bezpieczeństwem) zostają w `demo` bez zmian, także limit 4 rund.
  Pętla końcowa pozostaje pełna, gdy cała zmiana należy do kategorii z sekcji „Kategoria pętli i przejścia” ([review-loop](review-loop.md#kategoria-pętli-i-przejścia)). Wszystkie trzy kategorie z tej sekcji zostają bez zmian; `demo` ich nie osłabia.
- **Test-writer**: tylko w fazach z uprawnieniami lub izolacją danych; w pozostałych testy jednostkowe pisze `implementer` fazy.
- **Dokumentacja**: ograniczona do changeloga i „Wyniku” planu.
- **Odroczone**: `retro-quality` i rekordy wiedzy (`docs/domain`, `docs/lessons`) przechodzą do podsekcji „Dług (ceremony: demo)”
  w „Wyniku” planu: lista odroczonych rekordów (temat, źródło, proponowany właściciel zapisu). Dług spłaca człowiek albo kolejny przebieg `full`.
- **Bez zmian**: testy, ustalona wersja, rekord `--final`, zamknięcie planu i pętle według kategorii. Plan kończy się `done` z wypełnioną
  podsekcją „Dług (ceremony: demo)”. Relaksacja w regule „Done” rdzenia dotyczy wyłącznie dokumentacji ponad changelog i „Wynik”,
  `retro-quality` i rekordów wiedzy. `skillsync checkpoint --final` zgłosi wtedy ostrzeżenie `retro-quality-missing`; jest oczekiwane i nie blokuje.
  Dług zastępuje `retro-quality` także w liście warunków przed stanem „czeka na READY/CP”.
