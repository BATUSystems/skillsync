# Plik stanu dyspozycji

Ścieżka: `.skillsync/dispatch/<run-id>.yaml` w katalogu repozytorium, którego dotyczy dyspozycja.
Katalog `.skillsync/` jest ignorowany przez git (lokalny, per maszyna, bez commit/push).
Plik jest pamięcią dyspozycji po restarcie — nazwy kart i kontekst rozmowy giną.

Zasady:
- Zapisuje go **wyłącznie dyspozytor** (karty tylko raportują), atomowo (plik tymczasowy +
  rename), po każdym zdarzeniu zmieniającym stan i przed każdą turą, która może zostać przerwana.
- Bez sekretów i surowych danych uwierzytelnienia (connection stringi tylko z szablonów,
  bez haseł). Logi i dowody — jako ścieżki, nie treść.
- `run-id`: `RRRR-MM-DD-<slug>[-<litera>]`; jeden aktywny dyspozytor na `run-id`.
  Przejęcie `run-id` przez nową sesję = `/dispatch resume` (niżej), nie równoległy zapis.
- `status` dyspozycji i paczki zapisuj wyłącznie wartościami ze schematu (paczka także `go`). Inne `skillsync dispatch` pokazuje jako `unknown`.
- Widok tylko do odczytu dla IDE i narzędzi: `skillsync dispatch <path> [--run-id <id>] [--json]`. Biała lista pól, bez wolnego
  tekstu, `authoritative: false`; żywa prawda to karty i zdalne repo. Kontrakt: `docs/domain/dispatch-state-view.md` w Skillsync.

## Schemat

```yaml
version: 1
run_id: 2026-10-02-fabrikam-a
goal: "Domknięcie paczek F05–F08"
repo: /home/user/Repos/Org/App          # jedno repo na dyspozycję
base_branch: main
base_commit: 1a2b3c4                    # master w chwili startu
mode: messages                          # messages | manual (tryb ręczny Codex)
status: running                         # planning | running | draining | done | aborted
model_gate: { required: "opus>=5.5", confirmed: "claude-opus-5-5", at: "2026-10-02T10:05:00Z" }
parallel_limit: 2                       # min(karty, dispatch.max_parallel), obniżony guardami
cards:
  - alias: Dyspozytor
    session_id: "<id>"
    session_name: "Zarządca"
    state: dispatcher
  - alias: karta-1                     # klucz; imię z listy imiona.md; nie wraca w obrębie run-id
    session_id: "<id>"                  # null w trybie ręcznym bez identyfikatora
    session_name: "Fabrikam F05"           # ostatnio potwierdzona nazwa
    state: working                      # probed | free | busy | working | waiting_user | unavailable | released
    package: F05
    last_probe: "2026-10-02T10:07:00Z"
    last_message: "2026-10-02T11:42:00Z"
    worktree: ../app-wt/F05             # utworzone przez kartę (zgłoszone)
    branch: f05-notifications
    test_db: app_integration_test_F05
packages:
  - id: F05
    title: "Powiadomienia"
    depends_on: []
    risk: medium                        # low | medium | high
    areas: ["src/Notifications/**"]
    forbidden_for_others: true
    resources: [e2e]                    # zasoby dzierżawione, których paczka potrzebuje
    status: in_progress                 # todo | assigned | in_progress | review | done | blocked
    card: karta-1
    input_commit: 1a2b3c4
    result_commit: null                 # hash/gałąź z REPORT DONE
    evidence: []                        # ścieżki logów zweryfikowanych przez dyspozytora i wpisy o lukach dowodu (nie usuwać)
    claim: { task_id: F05, run_id: "2026-10-02-fabrikam-a.karta-1", ttl: 14400 }   # slug bez "/", ≤ 80 znaków; claimy tylko z katalogu głównego repo
leases:
  - resource: e2e
    holder: F05                         # paczka
    card: karta-1
    granted_at: "2026-10-02T12:00:00Z"
    released_at: null
    release: null                       # { exit: 0, log: ".../e2e.log", verified: true }
    queue: [F06]                        # kolejka oczekujących paczek (FIFO)
merge_queue:                            # kolejność PUSH SLOT (tryb direct; w pr scala człowiek)
  - { package: F06, state: waiting }    # waiting | granted | pushed | verified
  - { package: F05, state: waiting }
master:
  head: 1a2b3c4                         # ostatnio zweryfikowany hash zdalnej gałęzi
  red: null                             # { hash, symptom, owner: F05 } gdy MASTER RED
id_registry:                            # nadane identyfikatory (dyspozytor jest jedynym źródłem)
  SEC: [{ id: SEC-33, package: F05, label: SEC-TBD-F05-1 }]
  ADR: []
decisions:                              # decyzje użytkownika i otwarte pytania
  - { id: D1, question: "Zakres RBAC w F06", state: open, asked: "2026-10-02T11:00:00Z" }
  - { id: D2, question: "Kolejność F05/F06", state: answered, answer: "F06 po F05", by: user, at: "2026-10-02T10:20:00Z" }
debt:
  - { package: F05, item: "Brak testu Vitest dla pustego stanu", source: "REPORT DONE" }
events:                                 # krótki dziennik (ostatnie ~50) do diagnostyki i wznowienia
  - { at: "2026-10-02T12:00:00Z", msg: "LEASE GRANT e2e F05" }
```

Pola są minimalne; dodawaj tylko to, co potrzebne do wznowienia. Pole nieznane zachowaj.

## Wznowienie

`/dispatch resume <run-id>` (bramka modelu ma zastosowanie, jak zawsze):

1. Wczytaj plik. Brak pliku lub `status: done|aborted` — zgłoś i zapytaj użytkownika.
2. Nie ufaj starym stanom kart: `ListAgents` (lub tryb ręczny: poproś o oznaczenia okien),
   dopasuj karty po identyfikatorze, potem po nazwie/aliasie. Zmienione nazwy → poproś o
   ponowną zmianę nazw wg tabeli z adaptera. Karty, których nie ma — `unavailable`.
3. Wyślij `PROBE`/`STATUS?` do kart w stanach `working|waiting_user|busy|assigned`; wyniki
   są prawdą bieżącą, plik — hipotezą.
4. Uzgodnij z faktami: `git ls-remote` (czy `PUSHED` rzeczywiście są na zdalnym repo, czy
   `master.head` się zmienił → `MASTER MOVED` do kart), stan dzierżaw (procesy, porty:
   dzierżawa bez żywej karty jest `released` dopiero po sprawdzeniu, nigdy automatycznie),
   `task-claim` (kto trzyma paczkę).
5. Pokaż użytkownikowi odtworzony `status` i rozbieżności; po jego akceptacji wznów pętlę.
   Nie wznawiaj przydziałów, dzierżaw ani PUSH SLOT bez tego potwierdzenia.
6. Zapisz zdarzenie `resumed` i nowy `session_id` dyspozytora w stanie.

Plik uszkodzony lub niespójny: zachowaj kopię (`<run-id>.yaml.bak`), odtwórz stan z faktów
(karty, zdalna gałąź, claimy) i oznacz, które pola są niepotwierdzone.
