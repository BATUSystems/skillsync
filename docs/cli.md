# Referencja CLI

Opisuje semantykę komend `skillsync`, kody wyjścia i najważniejsze flagi. Pełną listę argumentów zawsze sprawdzaj przez
`skillsync <komenda> --help`; ten dokument jej nie powiela. Dobór komendy do zadania: [workflow](workflow.md).
Konfiguracja maszyny (warstwy, izolacja, strażnik): [configuration](configuration.md). Manifest projektu: [manifest](manifest.md).

`skillsync --version` wypisuje wersję generatora. Ten sam program uruchamia `python -m skillsync_workflow`. Polecenia, które
Skillsync zapisuje w artefaktach (zadanie VS Code w projekcie, launchery, hooki strażnika w warstwach), wywołują go tym samym interpreterem:
z checkoutu `python <checkout>/skillsync`, a z zainstalowanego pakietu `python -P -m skillsync_workflow`. Instalacja:
[installation](installation.md).

## Konwencje

- `[path]` to katalog projektu albo plik `.github/.skillsync.yaml`. Komendy workflow domyślnie używają bieżącego katalogu.
  Komendy synchronizacji (`link`, `instructions`, `push`, `pull`, `watch`) bez ścieżki działają na wszystkich projektach
  z `discover_paths` z [configuration](configuration.md).
- `--layer ID=PATH` (powtarzalna) zastępuje warstwy dla jednej komendy; kolejność daje rosnące pierwszeństwo. Dostępna
  m.in. w `link`, `push`, `doctor`, `layers list`, `layers check`. Pierwszeństwo: `--layer` > warstwy przekazane przez
  nadrzędny proces `skillsync` > config maszyny. Warstw nie przekazuje się zmienną środowiskową.
- Komendy kontrolne (`validate`, `smart-test`, `benchmark`, `env`, `e2e`, `ci`, `claim`, `tracker`, `upgrade`, `guard`)
  bez `--run` tylko planują albo raportują. `--run` wykonuje zaufane polecenia z manifestu albo warstwy, nigdy polecenia
  z treści ticketów, planów czy logów.
- Wynik komend workflow to JSON na stdout (wcięty). Wyjątek: `checkpoint`, `resume`, `metrics`, `benchmark` drukują jedną
  linię, a `--json` włącza wcięcia. Sekrety są maskowane.
- Statusy: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`. Plan albo brak konfiguracji daje `NOT_RUN` lub `BLOCKED`; to nie jest
  dowód, że aplikacja działa.

## Kody wyjścia

| Status | Kod | Uwagi |
|---|---|---|
| `PASS`, `NOT_RUN` | 0 | |
| `FAIL` | 1 | np. wyciek w `leakcheck`, `doctor` z błędem (`error`), izolacja w `layers check --isolation` |
| `BLOCKED` | 2 | brak warstwy dla projektu, zły argument, odmowa zapisu |
| błąd składni argumentów | 2 | usage na stderr, bez JSON |
| wyjątek (`skillsync: <komunikat>` na stderr) | 2 | także brak pliku configu |

- Synchronizacje (`link`, `instructions`, `push`, `watch` i pozostałe) kończą się kodem 0 także przy ostrzeżeniach
  (`[warn]`, `[conflict]`, `[info]`); czytaj stdout.
- `pull` działa jak `layers update` i `link`: przy brudnym drzewie warstwy albo braku fast-forward ta warstwa dostaje BLOCKED
  (np. `my: BLOCKED (dirty)`), pozostałe są aktualizowane, a `pull` kończy się kodem 2.
- Szczegółowe reguły kodów komend workflow: [workflow](workflow.md).

## Konfiguracja maszyny (`setup`)

`skillsync setup [--yes] [--dry-run] [--json] [--core-source URL] [--core-ref REF] [--org ID --org-source URL [--org-ref REF]] [--match GLOB]...`

Kreator konfiguracji maszyny. Nie ma własnej logiki warstw: wywołuje operacje `layers add`, `layers use` i `layers check`
(oraz dodanie reguły `**: [core]`, gdy config nie ma reguł). Tryb interaktywny,
gdy stdin jest terminalem i nie podano `--yes`; inaczej nieinteraktywny (bez pytań). Bez `--org` organizacja jest pominięta.

| Krok | Skutek | Wynik |
|---|---|---|
| `author-email` | sprawdza `git config --global user.email` przed dodaniem warstw (strażnik zapisuje adres przy `layers add`) | `unchanged`, `done` (interaktywnie, po podaniu adresu), `skipped` z ostrzeżeniem |
| `core` | `layers add core --kind core --subdir library --ref <core-ref>`; domyślny ref `v<zainstalowana wersja>` | `done`, `unchanged` (warstwa jest; inny ref = ostrzeżenie, bez zmiany), `blocked` |
| `org` | tylko przy `--org`: najpierw `git ls-remote` z `BatchMode` i limitem 30 s, potem `layers add <id> --kind org` (tylko do odczytu) | `done`, `unchanged` (ten sam id i source; bez `ls-remote`), `blocked` (brak dostępu, inny source lub rodzaj, osierocony katalog) |
| `rule` | bez organizacji: gdy `layer_rules` jest puste, dodaje `**: [core]`; z organizacją: `layers use <id> --match …` (domyślnie `**`) | `done`, `unchanged`, `blocked`; w `--dry-run` zamiast `done` jest `planned` (także dla `core` i `org`) |
| `check` | `layers check` na katalogu pasującym do reguły (najpierw reguła organizacji; `**` → katalog tymczasowy, glob z `*` → tymczasowy podkatalog jego prefiksu, glob bez `*` → ten katalog). Gdy żaden istniejący katalog nie pasuje: `layers check` wszystkich warstw z neutralnego cwd i ostrzeżenie | `done` (PASS), `failed` (FAIL), `blocked` (BLOCKED) |

Wynik `check` pochodzi z `layers check`: FAIL (1) to problem zgłoszony przez warstwy, BLOCKED (2) to brak warunku
(np. brak `core`). Warunki i treść komunikatów `blocked` opisuje [installation](installation.md#rozwiązywanie-problemów).

Idempotencja: drugie uruchomienie z tymi samymi argumentami daje `unchanged` we wszystkich krokach, kończy się kodem 0 i nie
zmienia configu (bajt w bajt). Przerwane uruchomienie (np. po `core`) dokańcza kolejne.

- `--dry-run`: pokazuje plan i wynik `git ls-remote` dla organizacji; nic nie klonuje i nie zapisuje configu.
- `--json`: schemat `{schema: 1, status, dry_run, steps: [{step, result, detail}], warnings, next}`; adresy są zredagowane (bez poświadczeń).
- Po zakończeniu w trybie tekstowym wypisuje `Next:` (dla `PASS`): link i sprawdzenie projektu, `skillsync pull`,
  aktualizacja narzędzia przez ponowne `install.sh` z nowego tagu, `uv tool update-shell`. `setup` nie wykonuje tych poleceń.

Statusy i kody (zob. [statuses-and-exit-codes](domain/statuses-and-exit-codes.md)):

| Status | Kod | Kiedy |
|---|---|---|
| `PASS` | 0 | wszystkie kroki bez blokady, `layers check` = PASS |
| `FAIL` | 1 | kroki bez blokady, `layers check` zgłasza problem (np. `symlink-in-layer`) |
| `BLOCKED` | 2 | zła flaga, brak dostępu do `--org-source`, nieznana wersja core bez zdalnego tagu (`--core-ref`), błąd git |

Wyjątek poza kreatorem (np. błąd odczytu configu) to `skillsync: <komunikat>` na stderr z kodem 2.

## Warstwy (`layers`)

Pomoc: `skillsync layers --help`. Model warstw: [layers-authoring](layers-authoring.md), składanie: [architecture](architecture.md).

| Komenda | Skutek |
|---|---|
| `layers init <katalog> --kind personal\|org [--id ID] [--json]` | tworzy szkielet warstwy (repo git z hookami strażnika) |
| `layers add <id> --kind core\|org\|personal --source <URL\|ścieżka> [--subdir D] [--ref R] [--path P] [--writable \| --read-only] [--json]` | klonuje warstwę, rejestruje ją w configu i instaluje hooki |
| `layers use <id> --match <glob> [--position N] [--json]` | dodaje wpis `layer_rules` dla projektów pasujących do globu |
| `layers update [id] [--ref R] [--json]` | aktualizuje checkouty warstw (tag, gałąź, commit albo `pull`) |
| `layers list [--project P] [--isolation] [--layer ID=PATH] [--json]` | efektywna lista warstw dla projektu albo bieżącego katalogu |
| `layers check [--project P] [--isolation] [--layer ID=PATH] [--json]` | wersje, `requires`, `core_min`, hooki, kolizje, `.orphans`; z `--isolation` tylko kontrolę izolacji |
| `layers migrate [--apply \| --dry-run] [...] [--json]` | przenosi config maszyny na `layers` i `layer_rules`; domyślnie dry-run |

Flagi `layers migrate` z pomocy: `--apply`, `--dry-run` (domyślnie), `--layer-source ID=PATH`, `--kind ID=KIND`,
`--match ID=GLOB`, `--manifests`, `--project PATH`, `--finish`. Migrację istniejącej maszyny opisuje [upgrading](upgrading.md).

Kody wyjścia warstw: `layers check` zwraca PASS/NOT_RUN (0), FAIL (1) przy izolacji, braku hooka albo kolizji,
BLOCKED (2) przy cyklu, braku `core`, niespełnionym `requires` albo `core_min`, braku `version` lub `core_min`.
`layers list` nie sprawdza hooków.
`layers init|add|use|update|migrate` zwracają BLOCKED (2) przy złym argumencie, konflikcie `id` albo brudnym drzewie
przy `update`.

## Synchronizacja

Pomoc: `skillsync <komenda> --help`.

| Komenda | Skutek |
|---|---|
| `init [katalog] [--id ID] [--json]` | tworzy warstwę personal (odpowiednik `layers init --kind personal`) |
| `import <projekt> [--dry-run] [--layer ID=PATH] [--to-layer ID]` | kopiuje skille i instrukcje projektu do warstwy (domyślnie personal), tworzy manifest, jeśli go brak |
| `link [path] [--dry-run] [--layer ID=PATH]` | warstwy → projekt: skille, instrukcje, role i pliki hostów (pełna lista: [architecture](architecture.md)) |
| `instructions [path] [--dry-run] [--layer ID=PATH]` | przebudowuje instrukcje projektu (`.github/copilot-instructions.md`, `CLAUDE.md`, `AGENTS.md`, artefakty Codexa) |
| `push [path] [--dry-run] [--to-layer ID] [--layer ID=PATH]` | projekt → warstwy właściciela plików (szczegóły: [layers-authoring](layers-authoring.md)) |
| `pull [path]` | aktualizuje wszystkie warstwy (`layers update`), potem `link` |
| `watch [path] [--interval S]` | pętla: po zmianie w warstwach wykonuje `link`; zwykle uruchamiana przez usługę |
| `memory-sync [--dry-run]` | kopiuje pliki z sekcji `memory_sync` configu na hosty ([configuration](configuration.md)) |
| `list [--layer ID=PATH]` | wypisuje skille z kolumną warstwy, która dostarcza skill |
| `add <projekt> <skill> [--layer ID=PATH]` | dopisuje skill do manifestu i wykonuje `link` |
| `skills add\|list\|index\|remove` | szkielet skilla, lista z członkostwem, indeks katalogu, usunięcie jawnego wpisu z manifestu |

Flagi `skills`: `add [--add-to-project] [--layer ID=PATH] [--to-layer ID]`, `list [--json] [--layer ID=PATH] [--status]`,
`index [--check] [--layer ID=PATH] [--stdout]`, `remove [--json] [--layer ID=PATH] [--no-link]`.

Dwie różne flagi warstw:

- `--to-layer ID` (`push`, `import`, `skills add`) wskazuje warstwę, do której trafiają nowe pliki (domyślnie `personal`).
  Zapis do warstwy `core` wymaga `--to-layer core`, bo ta opcja jest też zgodą na zmianę plików rdzenia.
- `--layer ID=PATH` (powtarzalna) zastępuje na czas jednego wywołania cały zestaw warstw podanymi katalogami: działają
  tylko warstwy z flag (kolejność flag = rosnące pierwszeństwo), a `layer_rules` z configu są pomijane. `core` też trzeba
  wtedy podać.

## Kontrola konfiguracji i kontekstu

| Komenda | Skutek |
|---|---|
| `doctor [path] [--layer ID=PATH] [--json]` | statyczna walidacja konfiguracji; `error` → FAIL (1), same ostrzeżenia → PASS (0), nieczytelny config albo config z kluczem `library:` → BLOCKED (2). Nie testuje aplikacji |
| `route`, `readiness`, `review`, `validate`, `smart-test`, `guard` | dobór kontekstu, gotowość planu, checklista review, plan i wykonanie kontroli, strażnik konwencji; `review` zwraca też `profile` (`docs`, gdy zmiana dotyka wyłącznie `docs/**`, plików `.md` skilli, `CHANGELOG*` albo notatek wydania, inaczej `kod`) i `docsFiles`; ścieżki i pozycje docs nadpisują `workflow.review.docs_paths` i `workflow.review.docs_checklist` |
| `map`, `suggest`, `knowledge init`, `status`, `plan`, `dispatch` | mapa repo, sugestie skilli, szkielet `docs/domain` i `docs/lessons`, odczyt stanu, zmiana statusu planu, stan dyspozycji |

Pola workflow w tej grupie:

- `status` (JSON, schemat bez zmian): rekord planu z poprawną linią `Spec:` ma `preImplement`: `required` (spec ma niepustą
  sekcję `Kontrakty`, `Migracja` lub `Dane`), `sections`, `run`, `report` (ścieżka raportu), `verdict` (najgorszy werdykt
  z `pre-implement*.md`), `warning` (tekst albo `null`). `null` w polu oznacza brak poprawnej linii `Spec:` albo nieczytelny spec. `warning` jest tylko
  ostrzeżeniem dla planów `draft`, `approved` i `in_progress`; nie blokuje zatwierdzeń.
- `plan approve` i `spec approve` (JSON): pole `warnings` (lista, może być pusta) z tym samym tekstem ostrzeżenia. Status
  i kod wyjścia nie zależą od ostrzeżeń.
- `readiness --run-id <id>`: oprócz wyniku na stdout zapisuje `.skillsync/runs/<id>/readiness.md` (pierwsza linia
  `Readiness: PASS|BLOCKED`) i dodaje `reportFile` do JSON. Bez flagi zachowanie się nie zmienia. Niepoprawny id albo
  dowiązanie w ścieżce przebiegu daje `BLOCKED` (exit 2) z `reason`, bez zapisu.

Komendy opisuje [workflow](workflow.md). `doctor` sprawdza m.in. skille z manifestu, sekcje `dispatch` i `prototype`,
spójność instrukcji z plikami oraz budżety bajtów.

- `route` zwraca pole `ceremony {value, source: manifest|default|invalid[, error]}` (klucz manifestu `workflow.ceremony`: `full` | `demo`;
  brak klucza = `full`, `source: default`; `error` tylko przy `invalid`, wtedy `value: null`). `doctor` zgłasza błąd dla innej wartości.
- `readiness --plan <plik>` przy planie z fazami `[UI]` (znacznik w tabeli kroków, [prototypes](prototypes.md#akceptacja)): FAIL bramki prototypu
  nie dodaje wpisu do `issues` (`prototypeGate` zostaje w raporcie); BLOCKED bramki i faza `[UI]` bez linii `Prototyp:` i bez obejścia właściciela
  dają BLOCKED. Opcjonalne pola raportu:

  | Pole | Znaczenie | Kiedy |
  |---|---|---|
  | `uiPhases` | id faz `[UI]` | zawsze, gdy plan ma fazy `[UI]` |
  | `nonUiPhases` | id pozostałych faz | razem z `uiPhases` |
  | `uiPhasesWaiting` | fazy `[UI]` czekające na akceptację (równe `uiPhases`) | tylko przy FAIL bramki |
  | `warnings` | lista napisów, nie blokuje | `[UI]` w wierszu tabelowym poza rozpoznaną tabelą kroków; „bramka pominięta przez właściciela” w planie z fazami `[UI]` |

## Przebieg i pomiar

| Komenda | Skutek |
|---|---|
| `checkpoint`, `resume` | zapis i sprawdzenie kroku przebiegu |
| `metrics`, `retro`, `retro-decide`, `benchmark` | pomiary, raport retrospektywy, rejestracja decyzji, benchmark narzędzi |

Szczegóły: [workflow](workflow.md).

## Dashboard i zgody

| Komenda | Skutek |
|---|---|
| `dashboard show <projekt> [--json] [--plan <rel>]` | stan projektu jako JSON `schema: 1` ([dashboard-contract](dashboard-contract.md)); tylko odczyt |
| `dashboard serve <projekt> [--port 5140] [--host 127.0.0.1] [--approve-host H] [--read-host H] [--no-approvals] [--plan <rel>] [--session-url URL]` | strona statusu z żywym odświeżaniem (SSE) i przyciskami zgód |
| `dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | czasy podkroków `Fn.k`, liczby rund i porażek przebiegu; podkrok bez danych jest oznaczony jawnie |
| `approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> --source cli\|ide [--sha SHA] [--file PLIK] [--target X] [--branch Y]` | zapis zgody użytkownika w `.skillsync/runs/<run-id>/approvals.jsonl` |
| `approvals wait …` | czekanie na zgodę (odczyt tego samego pliku), opis w [workflow](workflow.md) |

- `serve` wiąże się z `127.0.0.1`; port wybiera się per projekt (domyślnie 5140, dozwolone 5100–5199), żeby strony różnych
  projektów nie kolidowały. Nagłówek `Host` jest sprawdzany na wszystkich endpointach: odczyt spoza loopbacku (tunel, LAN)
  wymaga `--approve-host <nazwa>` (odczyt i zgody) albo `--read-host <nazwa>` (tylko odczyt z przeglądarki: widzowie przez tunel, także surowy podgląd `/api/doc` bez maskowania sekretów; nie chroni przed klientem podrabiającym `Host`, nieufni widzowie = `--no-approvals`; `POST
  /api/approve` = 403; obie flagi powtarzalne, nazwa bez portu); widok bez zapisu zgód dla wszystkich to `--no-approvals`.
  Zgody zapisuje serwer w tym samym
  procesie (bez podprocesu `skillsync`). Model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md).
- `approvals record` jest jedynym zapisem zgód: blokada plikowa, odmowa duplikatu (to samo słowo i `sha` w przebiegu), maskowanie
  sekretów. Słowo wpisuje użytkownik; agent tej komendy nie wywołuje.
- Kody: `0` = `PASS`, `2` = `BLOCKED` (zły argument, odmowa zapisu, brak projektu lub przebiegu, port zajęty); `approvals wait`
  kończy się `3` przy przekroczeniu czasu. Strona i dokument skilla: skill `dashboard` w bibliotece.

## Rozszerzenia workflow

`env`, `e2e`, `tracker`, `ci`, `claim`, `profiles`, `upgrade`. Opis i kody: [workflow](workflow.md).

## Prototypy

`skillsync prototype init|sync|index|list|ids|migrate|serve|user|login-link|iterate|restore|accept|gate|comments|triage|decide|resolve|merge-comments|question <projekt> …`

Komenda działa na klikalnych prototypach HTML w repo projektu. Opis akcji, kodów i formatu: [prototypes](prototypes.md).
`list --json`: pole `acceptance` = `{screens, accepted, stale, perScreen}` (`null` przy v1 i bez `comments.json`); `perScreen` to `[{id, state}]` dla każdego ekranu w kolejności dokumentu, `state` = `accepted` | `stale` | `pending` (accepted + stale + pending = `screens`).

`prototype gate <projekt> --plan <plan> [--phase <Fn>]`: bramka akceptacji prototypu. `--phase` jest dozwolone tylko z `--plan` (ze slugami: BLOCKED z JSON-em, kod wyjścia 2)
i dopasowuje id fazy dosłownie (wielkość liter). Wynik wg tabeli stanów ([prototypes](prototypes.md#akceptacja)): faza `[UI]` dostaje wynik całego planu
(PASS/FAIL), faza bez `[UI]` w planie z fazami `[UI]` PASS z notą i pustymi `prototypes`, `missing`, `acceptances`, nieznana faza BLOCKED `unknown_phase`,
plan z fazą `[UI]` bez linii `Prototyp:` i bez obejścia właściciela BLOCKED `ui_phase_without_prototype`. Raport dostaje opcjonalne pola `phase`
(przy `--phase`) oraz `phases` i `uiPhases` (listy id, tylko gdy plan ma fazy `[UI]`). Kody wyjścia bez zmian: PASS 0, FAIL 1, BLOCKED 2.

### Pytania do eksperta (`prototype question`, `prototype user roles`)

Agent zadaje pytania ekspertom domenowym przez wspólny serwer prototypów; odpowiedzi czyta z pliku `<root>/questions.json`
(bez serwera). Odpowiadać może tylko człowiek z rolą `expert` na stronie `/_experts`: agent nie ma polecenia `answer`.
Model, limity i strona: [prototypes](prototypes.md#pytania-do-eksperta), poufność: [security](security.md).

```bash
skillsync prototype user add . ewa.ekspert@example.com --role expert --password-prompt   # konto eksperta
skillsync prototype user roles . ewa.ekspert@example.com --role expert --role commenter  # zastępuje zbiór ról
skillsync prototype question url .                                                       # adres strony i liczba kont expert
skillsync prototype question add . --text "Czy zwrot po terminie jest dozwolony?" \
  --ref "Sekcja 3|docs/specs/zwroty.md#3|Zwrot do 14 dni od zakupu." \
  --option "Tak, bez limitu" --option "Tak, do 30 dni:recommended" --option "Nie" \
  --assume "do 30 dni" --origin docs/specs/zwroty.md:Q3                                  # domyślnie agent nie czeka
skillsync prototype question add . --text "Jaki jest klucz idempotencji płatności?" \
  --blocking --to ewa.ekspert@example.com --origin docs/specs/platnosci.md:Q5            # agent czeka na odpowiedź
skillsync prototype question list . --status open,answered
skillsync prototype question show . qn-3f9a1c
skillsync prototype question wait . qn-3f9a1c --timeout 600
skillsync prototype question wait . --all --since 2026-11-12T08:00:00Z
skillsync prototype question record . qn-3f9a1c --spec docs/specs/zwroty.md --line 42   # po wpisaniu wiersza do speca
skillsync prototype question withdraw . qn-3f9a1c                                        # tylko pytanie otwarte
skillsync prototype question export . --json > pytania.json
```

- `add`: dokładnie jedno z `--assume <wartość>` (pytanie nie zatrzymuje agenta; ekspert potwierdza albo koryguje) i `--blocking`
  (agent czeka). `--ref` ma postać `TYTUŁ|ADRES[|WYCIĄG]` (do 5; adres względny albo `https`), `--option` `ETYKIETA[:recommended]`
  (do 8, najwyżej jedna rekomendowana), `--to LOGIN` powtarzane (domyślnie wszyscy eksperci; login spoza pliku kont daje
  ostrzeżenie), `--refers-to <id>` (spór z wcześniejszą odpowiedzią), `--origin <spec>:Q<n>` (ścieżka względna). Wyciąg ze speca lub
  dokumentacji agent wpisuje sam: serwer nie czyta repozytorium. Skan sekretów w treści jest heurystyką: trafienie blokuje
  (`secret_detected`, exit 2, bez wartości w raporcie), `--force` przepuszcza z ostrzeżeniem. Raport: `questionId`, `specLine`
  (wiersz do speca), `files` (pliki do commita, w tym `<root>/.gitignore` z `questions.json.lock` i `*.tmp` oraz `<root>/.gitattributes`
  z `merge=skillsync-questions`, dopisywane przy pierwszym użyciu), `warnings`, `url`, gdy serwer projektu działa. Powtórka z tym
  samym `--question-id` jest idempotentna.
- `list`, `show`, `export` zwracają dokument `expert-questions/1`: `{schema, project, url?, questions: [{id, text, state, blocking, assumed,
  addressees, refs, options, answer?, comments, refersTo?, origin, recorded?}]}` oraz `status: PASS` (potrzebne do kodu wyjścia; klient ignoruje
  nieznane pola). `show` i `wait` dodają `answerLine`: gotowy wiersz `[EKSPERT]` do speca (`null` bez odpowiedzi); `specLine` z `add` i `withdraw`
  to wiersz czekającego pytania. Domyślny filtr `list` to `open,answered`; `--status` przyjmuje też `withdrawn` i `all`.
- `wait <id>` zwraca od razu PASS (`event: answer`), gdy pytanie jest już odpowiedziane, a poza tym czeka na odpowiedź albo komentarz
  nowszy niż `--since` (domyślnie „teraz”) lub `--after <id operacji>`. `wait --all` kończy się, gdy istnieje pytanie odpowiedziane
  bez operacji `recorded` albo zdarzenie nowsze niż `--since`/`--after`. TIMEOUT to exit 3.
- `record <id> --spec … --line <numer>` zapisuje tylko znacznik „wpisano do speca” (idempotentnie); `withdraw <id>` wycofuje pytanie otwarte,
  także blokujące, i zwraca zwykły wiersz `Q` programisty (odpowiedzianego nie da się wycofać: `already_answered`).
- `url` zwraca adres strony i liczbę kont `expert`; BLOCKED (`users_file_missing`, `users_file_invalid`, `no_expert_accounts`) znaczy, że
  nie ma komu zadać pytania, więc agent pyta zwykle. Nie sprawdza, czy serwer działa (pole `running` mówi o stanie z pliku serwera).
- `add`, `list`, `show`, `export` i `url` przyjmują `--users` (plik kont względem projektu, jak w `user`).
- `merge --driver %O %A %B` to sterownik gita `skillsync-questions` (rejestracja: [prototypes](prototypes.md#pytania-do-eksperta-w-gicie));
  `merge <projekt> <inny-questions.json>` scala ręcznie.
- Kody wyjścia: 0 PASS, 1 FAIL, 2 BLOCKED, 3 TIMEOUT. `1` znaczy, że magazyn odrzucił treść albo stan (zła ścieżka w `--origin`,
  nieznany `--refers-to`, `already_answered`, `withdrawn`, brak obu `--assume`/`--blocking` jako `invalid_mode`, konflikt scalania).
  `2` znaczy błędne wywołanie albo środowisko (zły format `--origin`, `secret_detected`, plik za duży 507, uszkodzony plik, brak kont w `url`).

## Bezpieczeństwo

| Komenda | Skutek |
|---|---|
| `leakcheck [path] [--worktree \| --staged \| --range A..B \| --history \| --text FILE \| --stdin] [--target ID] [--require full] [--no-config] [--json]` | skan treści, nazw plików i metadanych commitów albo tekstu poza gitem (`--text`, `--stdin`) |
| `leakguard patterns [--target ID] [--json]` | scalona lista wzorców (wbudowana i celu), JSON |
| `leakguard install-hook <repo> [--remove] [--target ID] [--json]` | zarządzany blok w lokalnych hookach `pre-commit` i `pre-push` |

`leakcheck` zwraca PASS (0), FAIL (1) z listą `{plik, kategoria, miejsce}` albo BLOCKED (2), gdy `--require full` nie ma
pełnego pokrycia, plik wzorców jest błędny albo tekst do skanu nie da się wczytać. Nieznany cel daje `coverage: generic` z ostrzeżeniem. Raport nigdy nie
pokazuje wartości wzorców. Model: [security](security.md).

`--text FILE` i `--stdin` to kontrola publikacji tekstu spoza gita (opis PR-a, komentarz, notatka wydania; do 1 MiB, bez tekstu w argumencie komendy): ten sam cel, te same tożsamości i wzorce co `--range`, tekst skanowany w najostrzejszej klasie ścieżki (jak plik instrukcji), bez wymogu repozytorium git. Raport podaje etykietę (nazwa pliku albo `<stdin>`), kategorię i miejsce `linia:kolumna`, nigdy samego tekstu. Instrukcje skilli `pr-run`, `feature` i `dispatch`: opis zapisany do pliku przechodzi `leakcheck --text <plik>` przed `gh pr create`, `gh pr edit --body`, `gh release create`.

## Schematy JSON

Raporty mają co najmniej te pola; konsument ignoruje pola, których nie zna.

| Komenda | Kształt (co najmniej) |
|---|---|
| `layers check --json` | `{schema: 1, status, project, rule, layers, findings: [{code, severity, layer, message}]}` |
| `leakcheck --json` | `{schema: 1, status, coverage, target, findings: [{path, category, location}], notices, warnings}`; po udanym skanie także `authors_scope`, przy BLOCKED `reason` |
| `prototype login-link --json` | `{schema: 1, status: "PASS", url, expires}` (`url` = `http://127.0.0.1:<port>/_login/link?t=<token>`, `expires` = ISO-8601 UTC, token tylko na stdout) albo `{schema: 1, status: "BLOCKED", reason, code}` z `code` ∈ (exit 2) `server_not_running` (brak serwera projektu: brak pliku stanu, martwy PID, inny `instance` albo inny plik użytkowników), `ambiguous_server` (kilka serwerów projektu bez `--port`), `link_needs_loopback` (host serwera inny niż `127.0.0.1`/`localhost`/wildcard), `link_unavailable` (serwer z `--trust-proxy`: brak endpointu linku), `run_dir_unsafe` (katalog `run/` z prawami ≠ 0700, innego właściciela lub dowiązanie), `unknown_user` (konto nie istnieje; nigdy nie jest tworzone), `password_change_required` (konto z `mustChange`), `invalid_path` (`--path` poza dozwolonymi), `invalid_port` (`--port` poza 1-65535), `invalid_project` (błędny projekt lub manifest), `too_many_pending` (32 oczekujące tokeny), `users_unreadable` (nieczytelny plik użytkowników); zapisy z sesji linku trafiają do lokalnego `.skillsync/prototype/run/audit.jsonl` (`{at, login, via, op, screen?}`), nie do `comments.json`; flagi `--user LOGIN` (wymagana), `--path /ścieżka`, `--port N`, `--users PLIK` |
| `prototype question list`, `show`, `export` | `{schema: "expert-questions/1", status: "PASS", project, url?, questions: [{id, text, state: open|answered|withdrawn, blocking, assumed, addressees, refs, options, answer?: {by, name, at, optionId?, optionLabel?, text?, confirm?, comment?}, comments, refersTo?, origin: {spec, q}, recorded?: {spec, line, at}}]}`; `show` i `wait` dodają `answerLine`, `add` i `withdraw` `specLine`; klient ignoruje nieznane pola, zmiana niezgodna = nowa wartość `schema` |
| `layers list --json` | `{schema: 1, status, generator_version, project, rule, layers: [{id, kind, path, root, subdir, version, writable, present}], composed_root, notes}` |

## Najczęstsze przepływy

```bash
skillsync link <projekt> --dry-run && skillsync link <projekt>   # nowy albo zmieniony projekt
skillsync doctor <projekt> --json                                # po link: oczekiwane PASS
skillsync layers check                                           # po zmianie warstw albo configu
skillsync smart-test <projekt> --run                             # testy po zmianie (z przydziałem zasobów)
```

Przed pierwszym `link` na nowym projekcie zawsze uruchom `--dry-run`.
