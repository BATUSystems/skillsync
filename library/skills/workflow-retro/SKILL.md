---
name: workflow-retro
description: Inspect measured workflow durations, costs, attempts and recorded causes; propose focused lessons without inventing savings or causality.
---

# Retrospektywa workflow

`skillsync retro <projekt> [--run-id <id>]` daje raport tylko do odczytu: klasy przebiegów (`no_data`, `incomplete`, `unexplained`, `recovery`, `clean`), rubrykę, sygnały z raportów i kandydatów zmiany.
`skillsync metrics <projekt> --classify` podaje te same klasy oraz czasy, koszty i `cause_ranking`, ale wypisuje wolny tekst przyczyn: wyniku nie publikuj.
Zacznij od `no_data` i `unexplained`, sprawdź `coverage`, `candidates_complete` i `not_detected`, a przy `baseline_seconds: null` nie wnioskuj o stratach czasu.
`no_data` to brak danych, nie sukces; niezapisana porażka jest niewidoczna. `clean` znaczy tylko, że zapisane rekordy nie mają problemów.
W `retro` plik uszkodzony daje pozycję `unreadable` i status `BLOCKED` (exit 2); raport nadal powstaje, a `candidates_complete: false` znaczy, że kandydaci są niepełni. `metrics` kończy się wtedy błędem.
Pokaż zakres danych, liczby znanych/nieznanych kosztów, próby i odnotowane przyczyny powtórek.
Nie wnioskuj o przyczynie z samego czasu ani nie licz restartu sesji jako błędu implementacji.

Przegląd okresowy (na prośbę albo co kilka przebiegów): `skillsync retro <projekt>` bez `--run-id`. Kolejność kandydatów:
`recurred`, `ready`, `needs_case`, `needs_owner`, `decided`. `recurred` ma `decision.records_after` > 0, czyli rekordy problemu po dniu decyzji (UTC):
sprawdź, czy poprawka trafiła do właściciela i czy przypadek nadal przechodzi, zanim zaproponujesz kolejną. Rejestr decyzji
`docs/retro/decisions.yaml` jest jedynym miejscem rozstrzygnięć; zmiana decyzji to `skillsync retro-decide --replace` (nowa data, ten sam wpis), nie drugi wpis tego klucza ani ręczna edycja.
Cykl i format: [pomiar](../feature/references/measurement.md), `docs/cli.md` w repo Skillsync.

Kandydat `ready` ma jednego właściciela i dowód z rekordu `FAIL`, ale to nie znaczy, że przypadek odtwarza błąd: sprawdź to przed propozycją. `needs_owner` i `needs_case` mówią, czego brakuje.
Najdroższy zapisany powtarzalny problem przełóż na małą zmianę procedury lub lekcję według
[lessons.md](../workflow/references/lessons.md); wybierz jednego najmniejszego właściciela poprawki (tabela tamże) i, gdy to zmiana reguły,
najpierw powtarzalny przypadek, który zawodzi. Zmiana reguły wymaga zgody użytkownika. Zachowaj dowody, aktualność i właściciela.
Wiedza wspólna trafia do biblioteki, produktowa do projektu. Pamięć Codexa aktualizuj tylko
na jawne polecenie użytkownika. Nie publikuj logów ani historii sesji automatycznie.
