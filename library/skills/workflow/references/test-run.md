# Testy kroku planu przez skillsync (`test-run`)

Źródło prawdy dla ról i skilli: testy kroku planu uruchamia się **przez skillsync**, żeby strona statusu pokazała ich wynik (zakładka „Testy”, pasek przy kroku), a bramka mogła sprawdzić, że krok miał testy.

## Komenda

`skillsync test-run <projekt> --run <id przebiegu> --step <id kroku> -- <polecenie testów>`

- polecenie, jego wyjście i jego kod wyjścia zostają bez zmian (124 przy przekroczeniu `--timeout`);
- zdarzenia zapisują się w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl`; lista testów po zakończeniu pochodzi z raportu JUnit XML lub TRX (`--junit <plik|katalog>`, albo domyślne miejsca dotnet, Maven, Gradle), liczniki z wyjścia (np. Karma „Executed N of M”);
- `PASS` tylko przy znanej dodatniej liczbie wykonanych testów (jak w `validate`): zero testów, nieznana liczba i pominięcia dają `BLOCKED`; wyjątki są jawne i zapisane: `--allow-no-count <powód>`, `--allow-skips`;
- `validate --run` i `smart-test --run` NIE zapisują zdarzeń testów: testy kroku planu uruchamiaj przez `test-run`.

## Który krok i który przebieg

- Zlecenie dla podumysła **zawiera `--run` i `--step`**; bez `--step` komenda wybiera jedyny krok „w toku” z checkpointów, a przy kilku krokach „w toku” kończy się `BLOCKED` (kod 2): nie zgaduj.
- Biegi bez kroku idą do sekcji „Bez kroku”.

## Bramka kroków `[T]`

Krok oznaczony w planie `[T]` (na początku lub końcu tytułu w `## Progress` albo w komórce `Krok` tabeli) nie zamyka się bez dowodu: `checkpoint --status PASS --step <id> --evidence .skillsync/runs/<run>/tests/<krok>/<n>.jsonl` przyjmuje tylko zapis przechodzącego `test-run` TEGO kroku i przebiegu, wykonanego na bieżącej treści (drzewo robocze bez `.skillsync/` i `docs/plans/`). Zwolnienie: `--no-tests <powód>` (zapis w rekordzie, ostrzeżenie `doctor` `tests-waived`, `needsHuman: tests`: decyzja człowieka przy zamknięciu). Bramka chroni przed pominięciem testów, nie przed fałszerstwem pliku dowodu. `doctor` ostrzega też o kroku z testami bez `[T]` (`tests-step-not-marked`) i o kroku `[T]` odhaczonym bez biegu (`tests-not-run`).

## Czego nie robić

- Nie wklejaj komunikatów porażek ani nazw testów z plików `.skillsync/runs/**/tests/` do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik`, raporty w repozytorium, wiadomości między sesjami, tracker): podaj liczniki, `id` testu i ścieżkę do pliku przebiegu. Wyniki testów nie są maskowane (`tests.mask`, `docs/security.md`).
- Ręcznie puszczone komendy testowe nie są dowodem kroku `[T]` ani nie są widoczne na stronie.
