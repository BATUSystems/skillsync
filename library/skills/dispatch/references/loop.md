# Pętla koordynacji

Czytaj przy każdej wiadomości od karty (tabela reakcji dyspozytora).

| Wiadomość karty | Reakcja dyspozytora |
|---|---|
| `FREE` / `BUSY` / `STATUS` | aktualizacja stanu; przy `czeka-na=użytkownik` nie ponaglaj |
| `LEASE REQUEST` | sprawdź faktyczny stan (porty, procesy, dysk, load, baza) → `LEASE GRANT` albo `LEASE QUEUED` |
| `LEASE RELEASE` | zweryfikuj exit code i log, procesy zatrzymane, porty wolne → dopiero potem następny GRANT |
| `PUSH SLOT` | kolejka merge; `PUSH GRANT` po jednym; zmiany kontraktów ostatnie |
| `PUSHED <hash>` | `git ls-remote` — hash na zdalnym repo; `MASTER MOVED` do pozostałych kart |
| `ID REQUEST` | nadaj numer z rejestru (stan + repo); do tego czasu karta używa etykiety roboczej |
| `REPORT DONE` | [odbiór paczki](#odbiór-paczki-report-done); oznacz paczkę; przydziel następną |
| `REPORT BLOCKED` | zbadaj dowody; eskalacja do użytkownika albo realokacja |
| gałąź główna czerwona | potwierdź dowód na czystym masterze; `MASTER RED` z jednym właścicielem poprawki |

Monitorowanie bez wiadomości: raz po zdarzeniu (np. użytkownik wraca) sprawdź karty, zdalną
gałąź i dysk. Karta bezczynna bez raportu → jedna sonda `STATUS?`.

## Odbiór paczki (`REPORT DONE`)

Sprawdź dowody w logach i `skillsync retro <główne repo> --run-id <run>.<paczka>`. Oceniasz ostatnią próbę (`.N` o największym N), przy ponowieniu wprost przez `--run-id <run>.<paczka>.N`.
Wcześniejsza próba `incomplete` jest dozwolona, a jej brak przy znanym ponowieniu oznacza odrzucenie; gdy poprzedni worktree i kopia nie istnieją, decyduje użytkownik. Odrzucenie: `REPORT REJECTED` z powodem.
- `incomplete` przez krok kończący się `NOT_RUN` lub `BLOCKED`: `REPORT BLOCKED` albo decyzja użytkownika; nigdy zamiana na `PASS`.
- Każde inne `incomplete`, także krok kończący się `FAIL` przy obecnym `--final`, oraz `no_data`, `unreadable`, brak przebiegu albo `rubric.evidence.status` inny niż `available`: odrzucenie. Dowód utracony lub zmieniony daje `partial` na stałe: nowa próba `.N` albo decyzja użytkownika.
- `unexplained`: karta uzupełnia przyczynę albo nowy dowód nowym rekordem, nie edycją pliku.
- Brak logu albo log starszy niż kod: odrzucenie z żądaniem rekordu `evidence-gap`; wpis o luce w `evidence` paczki zostaje po uzupełnieniu.
- Przy `retro=<n>` > 0 sprawdź, czy pliki `retro-*` są w kopii przebiegu w głównym checkoucie, i przekaż użytkownikowi listę poleceń „TAK <run-id>/P<n>” do wydania w zwykłej sesji `feature` w głównym checkoucie (nie dyspozytorowi) dla propozycji w stanie `deferred` z kopii `retro-signals.json` i `retro-decisions.json` (zgody nie udzielasz sam; karty dają propozycje także z powtórzeń sygnału, nie tylko z korekt użytkownika); brak plików przy `retro=<n>` > 0 to odrzucenie jak przy braku dowodu. `REPORT DONE` bez pola `retro=` to odrzucenie z żądaniem uzupełnienia raportu.
- Paczka zmienia `docs/domain`, `docs/lessons` lub manifest: `skillsync doctor` w worktree paczki, porównany z bazą. Nowy błąd = odrzucenie; zły indeks blokuje też `skillsync route`. Brak zainstalowanych skilli w worktree jest oczekiwany.

