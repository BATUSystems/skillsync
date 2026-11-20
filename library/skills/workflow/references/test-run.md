# Testy kroku planu przez skillsync (`test-run`)

Źródło prawdy dla ról i skilli: testy kroku planu uruchamia się **przez skillsync**, żeby strona statusu pokazała ich wynik (zakładka „Testy”, pasek przy kroku), a bramka mogła sprawdzić, że krok miał testy.

## Komenda

`skillsync test-run <projekt> --run <id przebiegu> --step <id kroku> -- <polecenie testów>`

- polecenie i jego wyjście zostają bez zmian; kod wyjścia `test-run`: PASS 0, `FAIL` (kod polecenia ≠ 0) = kod polecenia, 124 przy przekroczeniu `--timeout`, 127 brak programu; bieg z kodem polecenia 0 bez zapisanego `PASS` (`BLOCKED`: brak dodatniej liczby testów, pominięte bez zgody, „kod 0, ale wyjście raportuje niezaliczone”, niewykonane specyfikacje Karmy; albo brak zapisu statusu po błędzie zapisu lub przerwaniu) kończy się kodem 2 z linią stderr `test-run: exit code 2: …`; 2 jest też BLOCKED przed startem, więc kod polecenia 2 jest po samym kodzie nierozróżnialny od BLOCKED: rozróżnia linia stderr (`test-run: step <id> (<źródło>), attempt <n>: <STATUS>`, `BLOCKED: …`); `--import` zwraca 0 przy poprawnym imporcie niezależnie od statusu biegu; kod 0 zdejmują tylko `--allow-no-count <powód>` (brak liczników) i `--allow-skips`; zero wykonanych testów, „kod 0, ale wyjście raportuje niezaliczone” i niewykonane specyfikacje Karmy nie mają zdjęcia żadną flagą (naprawa polecenia testów);
- `--run <id>` (także z `--import`) wymaga śladu przebiegu w `.skillsync/runs/`: katalogu `<id>/` (nie dowiązania) albo zwykłego pliku `<id>.json`, nazwa dokładna; bez śladu `BLOCKED` (kod 2) z `--run <id>: no such run in .skillsync/runs/ (runs: …)`, nic się nie zapisuje, polecenie nie startuje; przebieg zakłada `skillsync checkpoint` (przy `--import` można też skopiować przebieg); bez `--run` wybierany jest jedyny katalog przebiegu (rekord i dowiązanie się nie liczą);
- zdarzenia zapisują się w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl`; lista testów po zakończeniu pochodzi z raportu JUnit XML lub TRX (`--junit <plik|katalog>`, albo domyślne miejsca dotnet, Maven, Gradle), liczniki z wyjścia (np. Karma „Executed N of M”);
- kod wyjścia 0 przy wyjściu, które zgłasza porażki w znanym podsumowaniu (`FAILED (failures=N)` unittest, `N failed ... in Xs` pytest), to `BLOCKED` z powodem „rura, `|| true` albo opakowanie mogą ukrywać kod testów”; zwykła fraza „3 failed attempts” albo „0 failed” nie czerwieni zielonego biegu;
- SIGTERM (POSIX; na Windows CTRL_BREAK) przerywa bieg jak Ctrl+C: drzewo procesów polecenia kończy się, `end` ma status `BLOCKED` (kod `test-run` 2), blokada kroku jest zwolniona; zamknięcie okna konsoli albo `taskkill /F` na Windows nie daje szansy na sprzątanie (granica);
- `PASS` tylko przy znanej dodatniej liczbie wykonanych testów (jak w `validate`): zero testów, nieznana liczba i pominięcia dają `BLOCKED`; jawne i zapisane wyjątki: `--allow-no-count <powód>` (tylko nieznana liczba), `--allow-skips` (pominięcia); zero wykonanych testów nie ma wyjątku;
- nakładka (np. skrypt powłoki owijający polecenie testów) musi przekazać kod wyjścia polecenia (np. `exit $LASTEXITCODE`); kod 0 nakładki mimo błędu testów daje PASS bez dowodu. `--allow-no-count` niczego nie weryfikuje: kodu wyjścia pilnuje tylko kod polecenia. Liczniki z polecenia nierozpoznanego (`tool: unknown`) pochodzą z luźnych wyrażeń po całym wyjściu. Przy nakładkach wskaż adapter przez `--format` (rozpoznanie narzędzia nie widzi poleceń za `powershell -File`). Przed użyciem nakładki sprawdź ją poleceniem, które pada: `exit 3` ma dać FAIL z kodem 3;
- `validate --run` i `smart-test --run` NIE zapisują zdarzeń testów: testy kroku planu uruchamiaj przez `test-run`.

## Który krok i który przebieg

- Zlecenie dla podumysła **zawiera `--run` i `--step`**; bez `--step` komenda wybiera jedyny krok „w toku” z checkpointów, a przy kilku krokach „w toku” kończy się `BLOCKED` (kod 2): nie zgaduj.
- Biegi bez kroku idą do sekcji „Bez kroku”.

## Bramka kroków `[T]`

Krok oznaczony w planie `[T]` (na początku lub końcu tytułu w `## Progress` albo w komórce `Krok` tabeli) nie zamyka się bez dowodu: `checkpoint --status PASS --step <id> --evidence .skillsync/runs/<run>/tests/<krok>/<n>.jsonl` przyjmuje tylko zapis przechodzącego `test-run` TEGO kroku i przebiegu, wykonanego na bieżącej treści (drzewo robocze bez `.skillsync/` i `docs/plans/`): rejestr zapisuje odcisk drzewa na **początku** (`start.treeStart`) i na **końcu** (`end.tree`) biegu i oba muszą być równe, więc plik zmieniony w trakcie biegu (także wygenerowany przez testy i niewpisany do `.gitignore`) nie jest dowodem (komunikat nazywa zmienione ścieżki); przy kilku próbach tego kroku na tej samej treści decyduje NAJNOWSZA (starszy zielony bieg nie przebija nowszego czerwonego); bieg zaimportowany z innej maszyny jest ważny tylko dla commita z `--source-sha` i przy czystym drzewie (bez zmian niezacommitowanych). Zwolnienie: `--no-tests <powód>` (zapis w rekordzie, ostrzeżenie `doctor` `tests-waived`, `needsHuman: tests`: decyzja człowieka przy zamknięciu). Bramka chroni przed pominięciem testów, nie przed fałszerstwem pliku dowodu. `doctor` ostrzega też o kroku z testami bez `[T]` (`tests-step-not-marked`) i o kroku `[T]` odhaczonym bez biegu (`tests-not-run`).

## Adaptery wyjścia narzędzi (etap 2)

Moduł `skillsync_workflow/test_adapters/<narzędzie>.py` czyta linie wyjścia polecenia testów (`feed`) i pliki raportu po jego zakończeniu (`end`) i zgłasza to, co zrozumiał, tylko przez `ctx.emit` (`collected`, `test`, `counts`); plik zdarzeń, maskę, limity i kod wyjścia prowadzi rejestrator (`test_run.TestRecorder`). Adapter, który nie rozumie wyjścia, nie emituje nic, a przebieg zostaje przy licznikach z podsumowania i raportu. Pole `live` adaptera mówi, ile widać w trakcie biegu: `running` (stan pojedynczych testów na żywo), `completion` (lista po zakończeniu) albo `counts` (same liczniki); `partial` przekazuje nieskończony koniec linii (narzędzie drukuje `test_x ... ` przy starcie testu i `ok` przy końcu). Wyjątek w adapterze wyłącza go na ten przebieg; `end()` zwracające `True` pomija czytanie raportu. Nazwy i identyfikatory testów mają `/` zamiast `\`; test bez wyniku po końcu przebiegu nie jest „uruchomiony”, tylko „oczekuje”.

Tabela adapterów (pełne reguły i ograniczenia: `docs/test-adapters.md`):

| Adapter | Wybór przy `auto` | `live` | Źródło końcowe |
|---|---|---|---|
| `unittest` | `python -m unittest -v` | `running` | podsumowanie wyjścia |
| `pytest` | `pytest -v` (bez `--collect-only`) | `running` | `--junit-xml` (te same id) |
| `cargo-test` | `cargo test` | `completion` | stan z linii |
| `cargo-test-serial` | `cargo test` z `--test-threads=1` | `running` | stan z linii |
| `dotnet` | `dotnet test` | `completion` | TRX |
| `vitest` | `vitest …` (bez `ng`) | `completion` | JUnit (tylko z `--junit`) albo strumień |
| `karma` | `karma …` (bez `ng`) | `counts` | JUnit (nazwy) i liczniki ze strumienia |
| `playwright` | `playwright test` | `completion` | strumień |
| `cypress` | `cypress run` | `completion` | strumień |
| `angular` | `ng test` | `counts` | jak `vitest` albo `karma` |
| `tap` | nigdy: tylko `--format tap` (goły skrypt, `npm test`, `bats --tap`, `prove`) | `completion` | strumień |
| `node-test` | `node --test` z reporterem `tap` albo `spec` (lub bez reportera) | `completion` | strumień |

`node --test` drukuje TAP (Node 20 i 22 w potoku) albo `spec` (Node 24; TTY: spec). `node-test` rozpoznaje format po pierwszej istotnej linii. Gdy `auto` nie wybiera adaptera, użyj `--format <nazwa>` (np. `--format tap` dla skryptu, który drukuje TAP).

Nowy adapter to nowy plik (rejestr odkrywa moduły sam), z fikstura wyjścia narzędzia w `tests/fixtures/test-runs/` (bez ścieżek domowych, nazw maszyn i identyfikatorów sesji) i testem przez `tests/adapter_harness.py`.

## Hook ostrzegający o gołych komendach testowych

Projekt z przepływem agentowym ma w hooku PreToolUse (`.claude/hooks/guard-commands.py`) drugą część (`hooks.guard_tests`, domyślnie `warn`): gdy komenda Bash uruchamia **szeroki bieg testów** poza `skillsync test-run` (bez filtra i bez pliku: `python3 -m unittest discover`, `pytest`, `dotnet test`, `cargo test`, `ng test`, `npm test`, …), model dostaje w kontekście ostrzeżenie. Ostrzeżenie **nie blokuje** i dociera razem z wynikiem polecenia, czyli po jego wykonaniu: ten bieg nie jest dowodem kroku `[T]`, powtórz go przez `skillsync test-run`. Punktowy bieg (plik, filtr, nazwa testu, mniej niż `hooks.wide_modules` modułów) i polecenia tylko wypisujące (`--collect-only`, `--list-tests`) są bez ostrzeżenia, tak samo opakowania (`skillsync test-run|validate|smart-test`, `systemd-run …`, `flock <blokada> systemd-run …`, `skillsync validate|smart-test … --run`, `hooks.test_wrappers`; same `flock` bez `systemd-run` nie jest opakowaniem). Jednorazowe pominięcie, tylko po zgodzie użytkownika: `SKILLSYNC_GUARD_EXCEPT='<powód>'` przed komendą (ślad w `.skillsync/hook-exceptions.jsonl`, pole `kind: ["tests"]`).

**Granice (uczciwie).** Hook czyta tekst komendy i rozpoznaje narzędzie po **programie** (pierwsze słowo po opakowaniach z tabeli `test_tools.json`, w tym `poetry|pdm|pipenv run` i `env -S`; gdy ten pierwszy wyraz jest „gospodarzem” `python*`, `py`, `pypy*`, `coverage`, także słowo po `-m`: `python3.12 -m pytest`, `python3 -X dev -m pytest`, `py -3 -m pytest`), nie po słowie w linii. Kopie skryptu haka i tabeli `test-tools.json` leżą przy haku; po aktualizacji rdzenia uruchom `skillsync link`, żeby je odświeżyć (nowe opakowania i `env -S` zależą od tabeli, krok `-m` i nowe werdykty od skryptu). Nie widzi: `./scripts/test.sh`, `devbox run`, `docker exec`, `tox`/`nox` (`tox` jest w tabeli jako narzędzie, ale hak nie ma dla niego reguły szerokiego biegu), `make` z celem innym niż `test|check`, polecenia przez `sudo`, `nohup` ani innego opakowania spoza tabeli (także `sudo pytest`: hak klasyfikuje program, a nie słowo w linii), `-m` za skryptem (`python tools/run.py -m pytest` daje ostrzeżenie `wide`, bo hak nie wie, czyj to argument), polecenia po `nohup` w `env -S 'nohup python -m unittest'` (przyczyną jest `nohup`, nie `-S`: `env -S 'python3.12 -m pytest'` daje `wide`; słów rozdzielonych z `-S` nie czytają tylko kroki 2–4 funkcji `detect_tool`/`tool_of`, które działają na surowym argv), wąskiego wzorca w `unittest discover -p`, polecenia zdalnego (`ssh host '…'`), skryptu zapisanego narzędziem Write i uruchomionego potem; `npm|pnpm|yarn test` to ostrzeżenie „prawdopodobnie” (hook nie czyta `package.json`), `dotnet test` na jednym małym projekcie też ostrzega (hook nie zna rozmiaru). Na Windows polecenie jest czytane tak, jak czyta je Git Bash (jak w części usuwającej): nierozpoznana forma to brak ostrzeżenia, nigdy blokada. Skuteczność ostrzeżenia na żywej sesji (czy model je widzi) jest NOT_VERIFIED do czasu pomiaru; uzupełnia je bramka `checkpoint` kroków `[T]`, która jest dowodem, a hook nie jest. Błąd tej części hooka (brak albo uszkodzenie `test-tools.json`, błąd klasyfikatora) wycisza tylko ją: odmowa trwałego usunięcia działa bez zmian.

## Czego nie robić

- Nie wklejaj komunikatów porażek ani nazw testów z plików `.skillsync/runs/**/tests/` do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik`, raporty w repozytorium, wiadomości między sesjami, tracker): podaj liczniki, `id` testu i ścieżkę do pliku przebiegu. Wyniki testów nie są maskowane (`tests.mask`, `docs/security.md`).
- Ręcznie puszczone komendy testowe nie są dowodem kroku `[T]` ani nie są widoczne na stronie (hook ostrzega o szerokim biegu, zob. wyżej).
