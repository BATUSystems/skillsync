# Testy kroku planu przez skillsync (`test-run`)

Źródło prawdy dla ról i skilli: testy kroku planu uruchamia się **przez skillsync**, żeby strona statusu pokazała ich wynik (zakładka „Testy”, pasek przy kroku), a bramka mogła sprawdzić, że krok miał testy.

## Komenda

`skillsync test-run <projekt> --run <id przebiegu> --step <id kroku> -- <polecenie testów>`

- polecenie, jego wyjście i jego kod wyjścia zostają bez zmian (124 przy przekroczeniu `--timeout`);
- zdarzenia zapisują się w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl`; lista testów po zakończeniu pochodzi z raportu JUnit XML lub TRX (`--junit <plik|katalog>`, albo domyślne miejsca dotnet, Maven, Gradle), liczniki z wyjścia (np. Karma „Executed N of M”);
- kod wyjścia 0 przy wyjściu, które zgłasza porażki w znanym podsumowaniu (`FAILED (failures=N)` unittest, `N failed ... in Xs` pytest), to `BLOCKED` z powodem „rura, `|| true` albo opakowanie mogą ukrywać kod testów”; zwykła fraza „3 failed attempts” albo „0 failed” nie czerwieni zielonego biegu;
- SIGTERM (POSIX; na Windows CTRL_BREAK) przerywa bieg jak Ctrl+C: drzewo procesów polecenia kończy się, `end` ma status `BLOCKED` (kod `test-run` 2), blokada kroku jest zwolniona; zamknięcie okna konsoli albo `taskkill /F` na Windows nie daje szansy na sprzątanie (granica);
- `PASS` tylko przy znanej dodatniej liczbie wykonanych testów (jak w `validate`): zero testów, nieznana liczba i pominięcia dają `BLOCKED`; wyjątki są jawne i zapisane: `--allow-no-count <powód>`, `--allow-skips`;
- `validate --run` i `smart-test --run` NIE zapisują zdarzeń testów: testy kroku planu uruchamiaj przez `test-run`.

## Który krok i który przebieg

- Zlecenie dla podumysła **zawiera `--run` i `--step`**; bez `--step` komenda wybiera jedyny krok „w toku” z checkpointów, a przy kilku krokach „w toku” kończy się `BLOCKED` (kod 2): nie zgaduj.
- Biegi bez kroku idą do sekcji „Bez kroku”.

## Bramka kroków `[T]`

Krok oznaczony w planie `[T]` (na początku lub końcu tytułu w `## Progress` albo w komórce `Krok` tabeli) nie zamyka się bez dowodu: `checkpoint --status PASS --step <id> --evidence .skillsync/runs/<run>/tests/<krok>/<n>.jsonl` przyjmuje tylko zapis przechodzącego `test-run` TEGO kroku i przebiegu, wykonanego na bieżącej treści (drzewo robocze bez `.skillsync/` i `docs/plans/`): rejestr zapisuje odcisk drzewa na **początku** (`start.treeStart`) i na **końcu** (`end.tree`) biegu i oba muszą być równe, więc plik zmieniony w trakcie biegu (także wygenerowany przez testy i niewpisany do `.gitignore`) nie jest dowodem (komunikat nazywa zmienione ścieżki); przy kilku próbach tego kroku na tej samej treści decyduje NAJNOWSZA (starszy zielony bieg nie przebija nowszego czerwonego); bieg zaimportowany z innej maszyny jest ważny tylko dla commita z `--source-sha` i przy czystym drzewie (bez zmian niezacommitowanych). Zwolnienie: `--no-tests <powód>` (zapis w rekordzie, ostrzeżenie `doctor` `tests-waived`, `needsHuman: tests`: decyzja człowieka przy zamknięciu). Bramka chroni przed pominięciem testów, nie przed fałszerstwem pliku dowodu. `doctor` ostrzega też o kroku z testami bez `[T]` (`tests-step-not-marked`) i o kroku `[T]` odhaczonym bez biegu (`tests-not-run`).

## Adaptery wyjścia narzędzi (etap 2)

Moduł `skillsync_workflow/test_adapters/<narzędzie>.py` czyta linie wyjścia polecenia testów (`feed`) i pliki raportu po jego zakończeniu (`end`) i zgłasza to, co zrozumiał, tylko przez `ctx.emit` (`collected`, `test`, `counts`); plik zdarzeń, maskę, limity i kod wyjścia prowadzi rejestrator (`test_run.TestRecorder`). Adapter, który nie rozumie wyjścia, nie emituje nic, a przebieg zostaje przy licznikach z podsumowania i raportu. Pole `live` adaptera mówi, ile widać w trakcie biegu: `running` (stan pojedynczych testów na żywo), `completion` (lista po zakończeniu) albo `counts` (same liczniki); `partial` przekazuje nieskończony koniec linii (narzędzie drukuje `test_x ... ` przy starcie testu i `ok` przy końcu). Wyjątek w adapterze wyłącza go na ten przebieg; `end()` zwracające `True` pomija czytanie raportu. Nazwy i identyfikatory testów mają `/` zamiast `\`; test bez wyniku po końcu przebiegu nie jest „uruchomiony”, tylko „oczekuje”. Nowy adapter to nowy plik (rejestr odkrywa moduły sam), z fikstura wyjścia narzędzia w `tests/fixtures/test-runs/` (bez ścieżek domowych, nazw maszyn i identyfikatorów sesji) i testem przez `tests/adapter_harness.py`.

## Hook ostrzegający o gołych komendach testowych

Projekt z przepływem agentowym ma w hooku PreToolUse (`.claude/hooks/guard-commands.py`) drugą część (`hooks.guard_tests`, domyślnie `warn`): gdy komenda Bash uruchamia **szeroki bieg testów** poza `skillsync test-run` (bez filtra i bez pliku: `python3 -m unittest discover`, `pytest`, `dotnet test`, `cargo test`, `ng test`, `npm test`, …), model dostaje w kontekście ostrzeżenie. Ostrzeżenie **nie blokuje** i dociera razem z wynikiem polecenia, czyli po jego wykonaniu: ten bieg nie jest dowodem kroku `[T]`, powtórz go przez `skillsync test-run`. Punktowy bieg (plik, filtr, nazwa testu, mniej niż `hooks.wide_modules` modułów) i polecenia tylko wypisujące (`--collect-only`, `--list-tests`) są bez ostrzeżenia, tak samo opakowania (`skillsync test-run|validate|smart-test`, `flock … systemd-run`, `hooks.test_wrappers`). Jednorazowe pominięcie, tylko po zgodzie użytkownika: `SKILLSYNC_GUARD_EXCEPT='<powód>'` przed komendą (ślad w `.skillsync/hook-exceptions.jsonl`, `kind: tests`).

**Granice (uczciwie).** Hook czyta tekst komendy i rozpoznaje narzędzie po **programie** (pierwsze słowo po opakowaniach z tabeli `test_tools.json`), nie po słowie w linii. Nie widzi: `./scripts/test.sh`, `devbox run`, `docker exec`, `tox`/`nox`, `make` z celem innym niż `test|check`, polecenia przez `sudo` ani innego opakowania spoza tabeli, polecenia zdalnego (`ssh host '…'`), skryptu zapisanego narzędziem Write i uruchomionego potem; `npm|pnpm|yarn test` to ostrzeżenie „prawdopodobnie” (hook nie czyta `package.json`), `dotnet test` na jednym małym projekcie też ostrzega (hook nie zna rozmiaru). Na Windows polecenie jest czytane tak, jak czyta je Git Bash (jak w części usuwającej): nierozpoznana forma to brak ostrzeżenia, nigdy blokada. Skuteczność ostrzeżenia na żywej sesji (czy model je widzi) jest NOT_VERIFIED do czasu pomiaru; uzupełnia je bramka `checkpoint` kroków `[T]`, która jest dowodem, a hook nie jest. Błąd tej części hooka (brak albo uszkodzenie `test-tools.json`, błąd klasyfikatora) wycisza tylko ją: odmowa trwałego usunięcia działa bez zmian.

## Czego nie robić

- Nie wklejaj komunikatów porażek ani nazw testów z plików `.skillsync/runs/**/tests/` do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik`, raporty w repozytorium, wiadomości między sesjami, tracker): podaj liczniki, `id` testu i ścieżkę do pliku przebiegu. Wyniki testów nie są maskowane (`tests.mask`, `docs/security.md`).
- Ręcznie puszczone komendy testowe nie są dowodem kroku `[T]` ani nie są widoczne na stronie (hook ostrzega o szerokim biegu, zob. wyżej).
