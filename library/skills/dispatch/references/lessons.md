# Lekcje z praktyki → reguły

Czytaj przy sporze o kolejność, dzierżawy, push lub status; tabela mapuje zdarzenia na reguły.

| # | Zdarzenie | Reguła (gdzie) |
|---|---|---|
| 1 | zlecenie do zajętej karty (nazwy się przetasowały, duplikaty) | aliasy z listy imion + sonda przed każdym przydziałem (Start 3–4, Przydział) |
| 2 | sesja-duch „waiting” blokowała paczkę 2 h | brak odpowiedzi na sondę → niedostępna, realokacja, prośba o restart (Start 4) |
| 3 | push dokumentów zepsuł fast-forward innej karcie | PUSH SLOT dla wszystkich pushy na bazę, także dokumentów (tryb `direct`; w `pr` dokumenty dyspozytora poza bazą) (pętla) |
| 4 | dwa razy „SEC-33” dla różnych luk | ID tylko od dyspozytora, etykiety robocze do nadania (pętla, `ID REQUEST`) |
| 5 | trzy gałęzie z migracjami EF | integrujący później regeneruje migrację po rebase (`MASTER MOVED`, multi-session.md) |
| 6 | zgłoszony push, którego nie było | `git ls-remote` — weryfikacja hasha (`PUSHED`) |
| 7 | „znana stara porażka” bez dowodu = zależność danych | wymagany dowód z czystego mastera; inaczej należy do paczki (Granice, multi-session.md) |
| 8 | load przy 3 kartach → niestabilne testy | guardy `max_load`/dysk, rekomendacja 2 kart przy E2E (Start 5, Przydział) |
| 9 | stała data w teście zaczerwieniła master wszystkim | `MASTER RED` + jeden właściciel poprawki, reszta nie naprawia (pętla) |
| 10 | GO użytkownika w kartach wbrew wygaszaniu | decyzja w karcie ma pierwszeństwo; aktualizuj stan (Granice) |
| 11 | projekt po iteracji bez `docs/lessons`, bo zlecenia wymagały tylko `docs/domain` | każde `ASSIGN` wymaga jawnie: reguły → `docs/domain`, potwierdzone awarie → `docs/lessons` (Przydział) |
| 12 | dyspozytor policzył łańcuch `zsh -c` → `timeout` → pytest jako trzy biegi | liczyć tylko procesy `python -m pytest` z cwd (skill `agent-runtime-pitfalls`) |
| 13 | restart maszyny przerwałby kilkugodzinny bieg | przed restartem: commit WIP we wszystkich kartach; poczekać na kończący się pełny bieg, gdy oszczędza powtórkę |
| 14 | paczka z niecytowanym YAML w nagłówku docs przeszła PUSH GRANT, `doctor` padł dopiero na developie | przed `PUSH GRANT` uruchom `skillsync doctor` w worktree karty; własny commit docs bramkuj wynikiem (`grep -q PASS && git commit`), nie samym wypisaniem statusu |
| 15 | pętle `while pgrep -f "<wzorzec>"` w kartach kręciły się 6–8 h (dopasowanie do własnej powłoki) | przy statusie przejrzyj procesy kart po cwd i rodzicu; właścicielowi wskaż PID do zabicia, czekanie tylko na PID (`kill -0`) albo znacznik w logu |
| 17 | podumysł karty zrobił `cd` do nieistniejącego worktree, łańcuch szedł dalej i `git checkout -f --detach` trafił w checkout dyspozytora; commit docs dyspozytora wylądował na obcej gałęzi | karty: polecenia git podumysłów tylko jako `git -C <ścieżka>` po sprawdzeniu istnienia katalogu; dyspozytor bramkuje swój commit `git symbolic-ref -q HEAD` = gałąź główna (obok `doctor`) |
| 16 | paczka „tylko BE” dodała wartość enuma; strażnik słowników i18n frontu padł dopiero w następnej paczce FE | strażniki FE czytające kod BE (słowniki, kody błędów) są obowiązkowe przed `PUSH GRANT` każdej paczki zmieniającej enumy, słowniki lub kody błędów — także bez zmian FE; wpisz to w `ASSIGN` |
| 18 | ASSIGN etapu speca zlecił pętlę planu przed „GO plan”; `plan-review` PASS (próba 5) przed `go-plan` był nieważny, potrzebna próba 6 (P2a-spec-d1, przebieg `okno-sesji-podumysly-zalaczniki`) | ASSIGN etapu speca w kolejności: pętla `spec` → „GO plan” → pętla `plan` od rundy 1 → „GO implement”; przeglądu planu przed „GO plan” nie zlecaj (reguła: `feature/references/spec.md`, tabela wznowienia, wiersze `approved`/`draft (czeka na spec)` i `approved z Spec-sha`; kolejność: `review-loop.md`) |
| 19 | karta zapisała rundy review jako osobne kroki `review-1`/`review-2` (`review-1` został na `FAIL` → `incomplete`, `REPORT REJECTED`); przy ponownym zamknięciu powtórzony `final` bez przyczyny dał `unexplained`, dopiero `final` z `--cause` dał `recovery` (P1-enoent) | kolejne rundy w tym samym kroku z rosnącym `--attempt`; ponowne zamknięcie jako `final-2` albo powtórzony `final` z `--cause` — przy odrzuceniu wpisz to w żądanie poprawki (reguła: `feature/references/measurement.md`, sekcja kroków i zamknięcia) |
| 20 | poprawki dopisywały dowód do pliku logu, na który wskazywał już wcześniejszy rekord (P1: `K1-mutation.log`, P2: raporty `1-implementer.md`, `3-test-writer.md`) — retro ma rubrykę evidence `partial` na stałe, odbiór tylko decyzją (D6) | każda próba i poprawka zapisuje dowód do nowego pliku `<krok>-<próba>.log`; wpisz to w `ASSIGN` i w zlecenie poprawki; zmienionego dowodu nie naprawi nowy rekord (`feature/references/measurement.md`, `--evidence`) |
| 21 | worktree Skillsync z kopią `.github/.skillsync.yaml` leżał w `discover_paths`: watch generował w nim `CLAUDE.md`/`AGENTS.md`/`.claude`, a `doctor` różnił się od baseline (P2); demo P3b trzeba było przenieść poza `~/Repos/BATUSystems` (D12) | demo i worktree trzymaj poza `discover_paths` albo bez manifestu; gdy manifest jest potrzebny (worker-mode, checkpointy), licz się z plikami generowanymi przez watch, porównuj `doctor` w tym samym worktree i po usunięciu worktree sprawdź journal watch |
