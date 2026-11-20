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
| `layers use <id> --match <glob> [--position N] [--json]` | dodaje wpis `layer_rules` dla projektów pasujących do globu; gdy reguła o dokładnie tym samym `match` już istnieje, dopisuje warstwę do jej `use` (bez drugiej reguły; warstwa już obecna w `use` = bez zmian; `--position` odmowa BLOCKED); lista `use: [a, b]` jest zmieniana w tekście (komentarze poza listą zostają; komentarze wewnątrz listy rozbitej na linie giną; plik z CRLF dostaje LF), a lista blokowa, kotwica, tag i inne nietypowe układy powodują przepisanie pliku z kopią zapasową |
| `layers update [id] [--ref R] [--json]` | aktualizuje checkouty warstw (tag, gałąź, commit albo `pull`) |
| `layers list [--project P] [--isolation] [--layer ID=PATH] [--json]` | efektywna lista warstw dla projektu albo bieżącego katalogu |
| `layers check [--project P] [--isolation] [--layer ID=PATH] [--json]` | wersje, `requires`, `core_min`, hooki, kolizje, `.orphans`, tekst zakazanej atrybucji narzędzi AI w instrukcjach, skillach, rolach i `workflow/` warstwy (`attribution-text`, FAIL); z `--isolation` tylko kontrolę izolacji |
| `layers migrate [--apply \| --dry-run] [...] [--json]` | przenosi config maszyny na `layers` i `layer_rules`; domyślnie dry-run |

Flagi `layers migrate` z pomocy: `--apply`, `--dry-run` (domyślnie), `--layer-source ID=PATH`, `--kind ID=KIND`,
`--match ID=GLOB`, `--manifests`, `--project PATH`, `--finish`. Migrację istniejącej maszyny opisuje [upgrading](upgrading.md).

Kody wyjścia warstw: `layers check` zwraca PASS/NOT_RUN (0), FAIL (1) przy izolacji, braku hooka, kolizji albo tekście zakazanej atrybucji,
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
| `link [path] [--dry-run] [--layer ID=PATH]` | warstwy → projekt: skille, instrukcje, role i pliki hostów (pełna lista: [architecture](architecture.md)); w projekcie z Claude Code zapisuje też hak PreToolUse na `Bash` (`.claude/hooks/guard-commands.py` zawsze odmawia poleceń z stopką lub trailerem narzędzia AI (z kopią wzorców `attribution-patterns.json`) i ustawia klucz `attribution` w `.claude/settings.local.json`; odmawia też trwałego usunięcia, `guard-tests.py` z `test-tools.json` ostrzega o szerokim biegu testów poza `skillsync test-run`) i jego wpis w `.claude/settings.local.json`, według kluczy `hooks.guard_delete`, `hooks.guard_tests`, `hooks.test_wrappers` i `hooks.wide_modules` ([manifest](manifest.md)); wpis i skrypty zostają zawsze (zakaz atrybucji nie ma przełącznika), a `hooks.guard_delete` i `hooks.guard_tests` wyłączają tylko swoje części; granice ostrzeżenia: [test-run](../library/skills/workflow/references/test-run.md) |
| `attribution [--user] [--dry-run] [--print-managed]` | ustawienie hosta wyłączające atrybucję narzędzia AI (puste `commit` i `pr`): bez flag raportuje stan pliku użytkownika (0 wyłączona, 1 włączona lub brak), `--user` zapisuje je w `~/.claude/settings.json` (jawnie, pozostałe klucze zostają, kopia zapasowa), `--print-managed` wypisuje obiekt do pliku ustawień zarządzanych (nigdy zapisywany). W projektach robi to `link` |
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

## Ścieżki w raportach

Każde pole ścieżki w raportach JSON ma na Windows ukośnik `/`: `root` i `project` (`status`, `dashboard show`, `catalog list`, `layers *`, `dispatch`, `runs`), `composed_root`, `config`, `manifest`, `backup`, `layers[].path` i `layers[].root` (`layers *`), `budgets[].path` (`doctor`), `log` (`workflow validate`), `checkpoint` (`checkpoint`), `targets` (plan wycofania `upgrade`), `knowledge init` (`created`, `existing`, `skipped[].path`), `route` (`lessons[].path`, `domain[].path`, `index`), `doctor` (`findings[].path`), `leakcheck` (`directory`, `repo`, `hooks[].path`), `env` (`launchers[]`), `e2e` (`recorded`, `scenario`, `report`), `repomap` (`path`) oraz komunikat `prototype serve` o pliku użytkowników; test `tests/test_skillsync_report_paths.py` pilnuje, żeby nowe pole nie wróciło do postaci natywnej. Pomocnik: `skillsync_workflow/pathfmt.py` (`posix`). Wyjątek: komendy do wykonania (zadanie `skillsync: link`, komendy hooków,
`selfcmd`, `cwd` w `checks[]` raportów `smart-test` i `validate`) i ścieżki, które produkt sam otwiera, zostają w postaci natywnej. Na Linuksie i macOS pola ścieżek się nie zmieniają.

## Kontrola konfiguracji i kontekstu

| Komenda | Skutek |
|---|---|
| `doctor [path] [--layer ID=PATH] [--json]` | statyczna walidacja konfiguracji; `error` → FAIL (1), same ostrzeżenia → PASS (0), nieczytelny config albo config z kluczem `library:` → BLOCKED (2). Nie testuje aplikacji; raportuje też stan haka (zakaz stopek i podpisów narzędzi AI, usuwanie, ostrzeżenie o testach): brak haka, brak lub uszkodzone dane zakazu atrybucji, brak klucza `attribution` w `settings.local.json` (w projekcie nigdy niepodlinkowanym: tylko ostrzeżenie) oraz tekst zakazanej atrybucji w warstwie lub w wygenerowanych instrukcjach to `error` (FAIL); nieaktualny wobec manifestu lub generatora, ustawienia wspólne i użytkownika z włączoną atrybucją to ostrzeżenie; aktywny z trybem to `info` (zob. `link`) |
| `route`, `readiness`, `review`, `validate`, `smart-test`, `guard` | dobór kontekstu, gotowość planu, checklista review, plan i wykonanie kontroli, strażnik konwencji; `review` zwraca też `profile` (`docs`, gdy zmiana dotyka wyłącznie dokumentacji (`docs/**` bez plików kodu), plików `.md` skilli, `CHANGELOG*` albo notatek wydania, `prototyp`, gdy zmiana mieści się w całości w katalogu prototypu (`prototype.root` z manifestu, domyślnie `docs/prototypes`), inaczej `kod`; `--profile kod|docs|prototyp` wymusza profil), `docsFiles` i `prototypeFiles`; pozycje listy prototypu dostaje także zmiana mieszana, która dotyka plików prototypu; ścieżki i pozycje docs nadpisują `workflow.review.docs_paths` i `workflow.review.docs_checklist`, ścieżki prototypu `workflow.review.prototype_paths`, a listę `workflow.review.prototype` (jedno źródło: `library/workflow/defaults.yaml`) rozszerzają warstwy, `workflow/projects/<slug>.yaml` i manifest: pozycje są dopisywane w kolejności rdzeń, warstwy, projekt, manifest, równe dosłownie pomijane (zostaje pierwsza), a pozycji rdzenia nie da się usunąć |
| `map`, `suggest`, `knowledge init`, `status`, `plan`, `dispatch` | mapa repo, sugestie skilli, szkielet `docs/domain` i `docs/lessons`, odczyt stanu, zmiana statusu planu, stan dyspozycji |

Lista plików (`--files` albo zmienione pliki z git) w `route`, `readiness`, `review`, `smart-test`, `validate` i `guard` jest sprawdzana na samej ścieżce: rodzic jest rozwiązywany fizycznie, ostatni człon nie. Przechodzi ścieżka względna albo bezwzględna leżąca w projekcie, także dowiązanie jako OSTATNI człon (tak listuje je git; dowiązanie do katalogu poza projektem też, z ukośnikiem końcowym lub bez), plik usunięty i plik w nieistniejącym katalogu. Odrzucane (BLOCKED): ścieżka bezwzględna poza projektem, `..` wychodzące z projektu i dowiązanie w środku ścieżki, które wskazuje poza projekt. Na POSIX `..` po dowiązaniu (`a/link/..`) oznacza prawdziwy rodzic celu, więc wychodzi poza projekt i jest odrzucane; na Windows `..` jest składane leksykalnie, zanim system spojrzy na dowiązania (ścieżka `a\link\..\x` to `a\x`), więc zostaje w projekcie, zgodnie z tym, co system otworzy. Ścieżki, które polecenie otwiera (plan, środowisko, role, prototypy), są sprawdzane z podążaniem za dowiązaniami. `guard` nie otwiera zmienionego dowiązania, tylko odnotowuje je w `skipped`.

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
- `route` zwraca pole `specs {tracked, archive, source}` (klucz `workflow.specs`, tryb nieśledzony; `source`: `manifest`, `gitignore`, `default` albo `invalid`, przy `invalid` `tracked: null` i `error`). Wykrycie: `git check-ignore --no-index -v` dla `docs/specs/x.md` i `docs/plans/x.md`, tylko reguły z plików `.gitignore` drzewa repozytorium, także zagnieżdżonych (negacja, `.git/info/exclude` i globalny `.gitignore` się nie liczą). Projekt, który nie jest korzeniem repo, dostaje `default`. To pole nie jest listą `specs` z `status`. `doctor` zgłasza błąd dla pary `tracked: true` i reguły obu katalogów, ostrzeżenia dla `tracked: false` bez reguły, reguły tylko jednego katalogu i śledzonych plików w `docs/specs` lub `docs/plans` (z komendą `git rm --cached`; pliki zostają na dysku).
- `spec snapshot`, ograniczenia: brak blokady współbieżnej (równoległe wywołania mogą dać fałszywy `BLOCKED`, rozwiązywany przez `--replace`); nieczytelny `docs/.saved.json` bez `--replace` nie jest ruszany, a cel z innym sha daje `BLOCKED`; `redact` nie maskuje tokenów typu `ghp_` w nazwie podanej w `--spec`.
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
| `checkpoint`, `resume` | zapis i sprawdzenie kroku przebiegu (krok `[T]` z `--status PASS` wymaga dowodu z `test-run` albo `--no-tests POWÓD`); `--manifest PLIK` bierze manifest spoza repozytorium (np. publiczny rdzeń bez `.github/.skillsync.yaml`), a korzeń projektu i zapis `.skillsync/runs/` zostają w `<path>`; bez flagi zachowanie bez zmian; zły plik to błąd z nazwą flagi |
| `spec snapshot <projekt> --run-id <id> [--spec PLIK] [--manifest PLIK] [--replace] [--json]` | kopiuje spec (z linii `Spec:` planu o `Przebieg: <id>`) i plan do `.skillsync/runs/<id>/docs/specs\|plans/` oraz, przy `workflow.specs.archive`, do `<archiwum>/<projekt>/<id>/specs\|plans/` (`<projekt>` = `project.name`, a bez niego nazwa katalogu repozytorium; znaki spoza `[A-Za-z0-9._-]` zamienione na `-`). Zapis przez plik tymczasowy i rename, z compare-and-swap osobno dla każdego celu: sha ostatniego zapisu na parę (cel, plik) jest w `.skillsync/runs/<id>/docs/.saved.json`. Cel zmieniony przez kogoś innego albo bez wpisu (z innym sha niż źródło) = `BLOCKED` dla tego celu; `--replace` nadpisuje (decyzja człowieka). Bez planu i bez `--spec` = `BLOCKED`, nic nie zapisano. Kody: 0 PASS, 1 FAIL (archiwum niedostępne; cel przebiegu zapisany), 2 BLOCKED; przy mieszanym wyniku wygrywa najwyższa waga (2 > 1 > 0). Treść plików nie trafia do komunikatów |
| `spec snapshot <projekt> --run-id <id> --check [--to KATALOG] [--json]` | tylko odczyt: porównuje sha speca i planu przebiegu z kopiami w `.skillsync/runs/<id>/docs/`, w archiwum (gdy jest) i w `KATALOG` (korzeń kopii projektu; sprawdzane jest `<KATALOG>/.skillsync/runs/<id>/docs/specs\|plans/`). `PASS` (0) gdy wszystkie cele zgadzają się; `BLOCKED` (2) z listą rozbieżnych celów, gdy archiwum jest niedostępne, albo w połączonym worktree bez celu poza nim (ani archiwum, ani `--to`). Procedura usuwa worktree tylko po `PASS` celu poza worktree |
| `metrics`, `retro`, `retro-decide`, `benchmark` | pomiary, raport retrospektywy, rejestracja decyzji, benchmark narzędzi |

Szczegóły: [workflow](workflow.md).

## Dashboard i zgody

| Komenda | Skutek |
|---|---|
| `dashboard show <projekt> [--json] [--plan <rel>]` | stan projektu jako JSON `schema: 1` ([dashboard-contract](dashboard-contract.md)); tylko odczyt |
| `test-run <projekt> [--run ID] [--step ID] [--junit PLIK\|KATALOG ...] [--allow-no-count POWÓD] [--allow-skips] [--format auto\|none\|NAZWA] [--timeout S] -- <polecenie...>`; `test-run <projekt> --import PLIK --step ID --source-sha SHA [--run ID]` | uruchamia komendę testów projektu przez wspólny runner i zapisuje jej zdarzenia dla strony statusu (`.skillsync/runs/<run>/tests/<krok>/<n>.jsonl`); wyjście i kod wyjścia komendy bez zmian (124 przy limicie czasu, 127 brak komendy); `PASS` tylko przy znanej dodatniej liczbie wykonanych testów (zasada `validate`), lista testów z raportu JUnit XML lub TRX; krok bez `--step` = jedyny „w toku” z checkpointów (kilka = BLOCKED, 2); `--run` bez flagi = jedyny przebieg projektu; `--format` wybiera adapter stanu pojedynczych testów: `auto` (domyślnie, po narzędziu rozpoznanym w poleceniu także za `npx`, `timeout`, `env`, `python -m` i podobnymi) pisze na stderr przed startem polecenia jedną linię: `test-run: adapter <nazwa> (live <running|completion|counts>)` (po średniku bywa podpowiedź, jak uzyskać listę pojedynczych testów, np. `--reporter=verbose` dla Vitest) albo `test-run: no adapter for <narzędzie> (known: ...); pass --format <nazwa>, or --format none to silence`; `none` wyłącza adapter, nazwa wymusza go (`angular`, `cargo-test`, `cargo-test-serial`, `cypress`, `dotnet`, `karma`, `node-test`, `playwright`, `pytest`, `tap`, `unittest`, `vitest`; nieznana nazwa to błąd z listą, np. `--format ?`; opis w [test-adapters](test-adapters.md)); `--timeout S` to limit czasu w sekundach (domyślnie 3600); bez adaptera bieg jest zapisany z liczników podsumowania i raportu; `--import PLIK --source-sha SHA` przenosi plik zdarzeń zapisany na innej maszynie (walidacja, bajty bez zmian, kopia bez `.git` podaje commit) |
| `dashboard serve <projekt> [--port N] [--read-port N --approve-port M] [--read-bind ADR] [--approve-bind ADR] [--allow-proxy-headers] [--prototype-port N] [--host H] [--approve-host H] [--read-host H] [--no-approvals] [--plan <rel>] [--session-url URL] [--poll S] [--verbose]` | strona statusu z żywym odświeżaniem (SSE) i przyciskami zgód. Bez flag portów: dwa gniazda, odczyt na `5140` (wszystkie interfejsy) i zgody na `5141` (tylko ta maszyna); `--port N`: jedno gniazdo (tryb jednego portu); `--read-port`/`--approve-port`: jawne numery (niżej); `--poll S`: odstęp sprawdzania zmian plików (domyślnie 1, najmniej 0,2); `--verbose`: log każdego żądania na stderr |
| `dashboard url <projekt> [--port N] [--host NAZWA] [--read] [--lan] [--json]` | adres z tokenem zgód (`http://127.0.0.1:<port>/#zgoda=<token>`) działającego `dashboard serve` projektu (z pliku stanu w katalogu danych maszyny); `--read`: adres gniazda odczytu, bez fragmentu i tokenu; `--lan`: adresy gniazda odczytu dla innych urządzeń, jeden na linię (`http://<IP>:<port>/`; w `--json` pole `lanUrls`); `BLOCKED` (exit 2): `server_not_running`, `approvals_disabled` (serwer z `--no-approvals`), `ambiguous_server`, `host_not_allowed`, `invalid_project`, `read_not_exposed` (gniazdo odczytu słucha tylko na loopbacku, `--lan`); zob. [dashboard-contract](dashboard-contract.md) |
| `dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | czasy podkroków `Fn.k`, liczby rund i porażek przebiegu; podkrok bez danych jest oznaczony jawnie |
| `approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> --source cli\|ide [--sha SHA] [--file PLIK] [--target X] [--branch Y]` | zapis zgody użytkownika w `.skillsync/runs/<run-id>/approvals.jsonl`; dla `CP` i `READY` dopisuje pole `planState` (status planu przebiegu w chwili zgody), a duplikat to ta sama para (sha, `planState`) jako ostatni wpis słowa w przebiegu |
| `approvals wait …` | czekanie na zgodę (odczyt tego samego pliku), opis w [workflow](workflow.md); przy `tracked: false` zamknięcie planu nie zmienia HEAD, a `wait` nie zna `planState`, więc zawsze podawaj `--since <czas STOP>` |

- Bez flag portów `serve` uruchamia tryb dwóch gniazd: odczyt na `5140` na wszystkich interfejsach (`0.0.0.0`), zgody na
  `5141` tylko na `127.0.0.1` (zob. niżej). Port wybiera się per projekt (dozwolone 5100–5199), żeby strony różnych
  projektów nie kolidowały. Nagłówek `Host` jest sprawdzany na wszystkich endpointach. Gniazdo odczytu przyjmuje loopback,
  nazwy z `--approve-host` i `--read-host`, literały IP interfejsów tej maszyny oraz własną nazwę maszyny
  (`socket.gethostname()`, jej krótką formę i `<krótka>.local`; dopasowanie tekstowe, bez DNS; tylko odczyt).
  Gniazdo zgód przyjmuje loopback, nazwy z `--approve-host` i literały IP interfejsów tej maszyny; nazwy maszyny nie.
  Odczyt spoza loopbacku w trybie jednego portu (`--port`) wymaga `--approve-host <nazwa>` (odczyt i zgody) albo
  `--read-host <nazwa>` (tylko odczyt z przeglądarki: widzowie przez tunel, także podgląd `/api/doc` (maskowany, ale maskowanie jest heurystyką); nie chroni przed klientem podrabiającym `Host`, nieufni widzowie = `--no-approvals`; `POST
  /api/approve` = 403; obie flagi powtarzalne, nazwa bez portu); widok bez zapisu zgód dla wszystkich to `--no-approvals`.
  Zgody zapisuje serwer w tym samym
  procesie (bez podprocesu `skillsync`). Model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md).
- Tryb dwóch gniazd: `serve` bez flag portów albo z `--read-port N --approve-port M` uruchamia dwa gniazda HTTP nad jednym
  stanem projektu (jeden watcher, jedno Ctrl+C). Rola wynika z **portu**, nie z nagłówka `Host`. Gniazdo odczytu ma strony
  i dane, bez trasy zgód (`POST /api/approve` = 404) i bez tokenu. Gniazdo zgód wymaga tokenu, ścisłego `Origin`, odrzuca
  nagłówki pośrednika (`Forwarded`, `X-Forwarded-*`, `Via` i inne) i zapisuje w `approvals.jsonl`, skąd przyszła zgoda
  (`via`). Numery bez flag portów: odczyt 5140, zgody 5141; przy jawnych `--read-port`/`--approve-port` podajesz numery sam
  (flagi nie mają wartości domyślnej). Samo `--read-port` albo samo `--approve-port` uruchamia jedno gniazdo. `--port` nie
  łączy się z tymi flagami (BLOCKED, exit 2). Kolejność odmów, `Origin` i reguła `Host`:
  [dashboard-contract](dashboard-contract.md), sekcja „Serwer”.
- Nasłuch w trybie dwóch gniazd: odczyt domyślnie na `0.0.0.0` (każde urządzenie w sieci lokalnej), zgody domyślnie na
  `127.0.0.1`. Odczyt: `--read-bind ADR` (flaga), potem `--host H` (tylko z `--read-port`, tylko odczyt; `--host ''` = wszystkie interfejsy), potem
  `dashboard:` w manifeście projektu, potem w configu maszyny, na końcu `0.0.0.0`. Zgody: `--approve-bind ADR`, potem
  `dashboard:`, na końcu `127.0.0.1`; `--host` nie zmienia gniazda zgód. Adres `ADR`/`H`: literał IP, `localhost`, `0.0.0.0`
  albo `::`. Tryb `--port` nasłuchuje na `127.0.0.1`, chyba że podano `--host`. Przy odczycie poza loopbackiem konsola wypisuje
  jedno ostrzeżenie o dostępności dla urządzeń w sieci; zgody z innego urządzenia wymagają `--approve-bind 0.0.0.0` (lub
  ustawienia), bo token idzie nieszyfrowanym HTTP. W nieufnej sieci zawęź odczyt do tej maszyny (`--read-bind 127.0.0.1`
  albo `dashboard.local_only: true`). Port odczytu (5140) nie ma trasy zgód: tunel na zgody to jawne
  `--approve-port M` i tunel na port M (`ssh -L M:127.0.0.1:M <host>`; `--approve-host` tylko dla nazwy z tunelu odwrotnego albo bramki); tunel do strony
  zawsze kieruj na port odczytu. Ryzyko: [workflow](workflow.md), sekcja „Strona statusu”.
- Stary tryb za tunelem: `serve --port 5140 --approve-host <nazwa tunelu>` (jeden port z bramką `Host`, jak do 1.7.0).
  `--host` i `--approve-host` bez portów są BLOCKED z podpowiedzią, bo zmieniłyby znaczenie.
- Ustawienie `dashboard:` (klucze `local_only: true|false`, `read_bind`, `approve_bind`) jest w configu maszyny
  i w manifeście projektu, zob. [configuration](configuration.md) i [manifest](manifest.md#sekcja-dashboard).
  Nieznany klucz: `doctor` ostrzega, `serve` BLOCKED z nazwą klucza; zła wartość: BLOCKED z nazwą klucza (nigdy z wartością).
- Komunikaty startowe (konsola): przy odczycie poza loopbackiem jedno ostrzeżenie o dostępności dla każdego urządzenia w
  sieci lokalnej (plany, specyfikacje, wyniki, sekrety zredagowane heurystycznie, ale wyniki testów jawne, bez maski; ruch nieszyfrowany) z podpowiedzią
  `--read-bind 127.0.0.1` albo `dashboard.local_only: true`; jedno zdanie o zmianie domyślnego nasłuchu (od 1.8.0) i powrocie
  flagą `--read-bind 127.0.0.1`, wypisywane tylko przez wydania przed 1.10; adresy IPv4 maszyny po jednym w linii (sieć
  tylko-IPv6 ich nie dostanie) plus adres po nazwie maszyny (`http://<nazwa>.local:5140/`); nazwa maszyny w komunikacie;
  linia o zgodach tylko lokalnych z podpowiedzią `--approve-bind 0.0.0.0`. Gdy oba gniazda słuchają na loopbacku, konsola
  pisze, że strona jest tylko na tej maszynie. Na Windows, przy pierwszym nasłuchu na `0.0.0.0`, konsola dodaje linię o Zaporze
  Windows; macOS z zaporą aplikacji może też zapytać o dostęp przy pierwszym nasłuchu.
- Uruchamianie dwóch gniazd: porty 5140 i 5141 muszą być wolne. Przed bindem każdy port jest sprawdzany połączeniem na loopback
  (na każdym systemie); gdy coś już odpowiada, start kończy się BLOCKED z numerem portu, flagami `--read-port`/`--approve-port`
  i właścicielem (projekt z plików stanu albo „inny proces”); serwer nie przechodzi na inny port. Zatrzymaj
  wcześniejsze `dashboard serve` tego samego hosta (także te z `--no-approvals`), zanim uruchomisz tryb dwóch gniazd.
  Jedno wywołanie z dwoma portami zastępuje dwie instancje (jedna z `--no-approvals`); wywołania z `--port` działają bez
  zmian. `--port` z `--read-bind`, `--approve-bind` albo `--allow-proxy-headers` = BLOCKED. `--allow-proxy-headers` działa
  tylko w trybie z gniazdem zgód (domyślnym albo z `--approve-port`); `--read-host '*'` tylko na gnieździe odczytu.
- `approvals record` jest jedynym zapisem zgód: blokada plikowa, odmowa duplikatu (to samo słowo i `sha` w przebiegu), maskowanie
  sekretów. Słowo wpisuje użytkownik; agent tej komendy nie wywołuje.
- Kody: `0` = `PASS`, `2` = `BLOCKED` (zły argument, odmowa zapisu, brak projektu lub przebiegu, port zajęty); `approvals wait`
  kończy się `3` przy przekroczeniu czasu. Strona i dokument skilla: skill `dashboard` w bibliotece.

## Rozszerzenia workflow

`env`, `e2e`, `tracker`, `ci`, `claim`, `profiles`, `upgrade`. Opis i kody: [workflow](workflow.md).

## Prototypy

`skillsync prototype init|sync|index|list|ids|migrate|serve|user|login-link|iterate|restore|writing|accept|gate|comments|triage|decide|resolve|merge-comments|question <projekt> …`

Komenda działa na klikalnych prototypach HTML w repo projektu. Opis akcji, kodów i formatu: [prototypes](prototypes.md).

`prototype serve <projekt> [--layer ID=PATH] [--assets DIR] [--json] [--host ADR] [--port N] [--users PLIK] [--secure-cookies] [--trust-proxy] [--no-source-writes] [--login-to-view] [--demo]`: serwer prototypów ze wspólnym API komentarzy (na pierwszym planie, Ctrl+C zatrzymuje). `--host`: adres nasłuchu (domyślnie `127.0.0.1`); `--port`: port (domyślnie `prototype.port` z manifestu, bez klucza 4445; `0` = dowolny wolny); `--users`: plik użytkowników względem projektu; `--secure-cookies`: ciasteczko sesji z `Secure` (za TLS); `--trust-proxy`: limit logowania liczony z `X-Forwarded-For`; `--no-source-writes`: serwer nigdy nie zapisuje źródeł prototypów (przywracanie ekranów); `--login-to-view`: logowanie także do oglądania. `--demo`: pokaz tylko do odczytu bez kont (bez logowania, komentarzy, wątków, akceptacji i odpowiedzi eksperta; serwer odmawia każdego zapisu, istniejące komentarze są ukryte, strony są jawne dla każdego, kto dotrze do adresu); domyślny nasłuch pokazu to `0.0.0.0` (każdy interfejs), a `--host 127.0.0.1` zawęża go do tej maszyny. `--demo` z `--login-to-view` kończy się `BLOCKED` (exit 2: pokaz nie ma kont ani logowania); `--users`, `--secure-cookies` i `--trust-proxy` przy `--demo` są ignorowane z notą. Model zagrożeń pokazu: [security](security.md#serwer-prototypów-pytania-do-eksperta).
`list --json`: pole `acceptance` = `{screens, accepted, stale, perScreen}` (`null` przy v1 i bez `comments.json`); `perScreen` to `[{id, state}]` dla każdego ekranu w kolejności dokumentu, `state` = `accepted` | `stale` | `pending` (accepted + stale + pending = `screens`).

`prototype gate <projekt> --plan <plan> [--phase <Fn>]`: bramka akceptacji prototypu. `--phase` jest dozwolone tylko z `--plan` (ze slugami: BLOCKED z JSON-em, kod wyjścia 2)
i dopasowuje id fazy dosłownie (wielkość liter). Wynik wg tabeli stanów ([prototypes](prototypes.md#akceptacja)): faza `[UI]` dostaje wynik całego planu
(PASS/FAIL), faza bez `[UI]` w planie z fazami `[UI]` PASS z notą i pustymi `prototypes`, `missing`, `acceptances`, nieznana faza BLOCKED `unknown_phase`,
plan z fazą `[UI]` bez linii `Prototyp:` i bez obejścia właściciela BLOCKED `ui_phase_without_prototype`. Raport dostaje opcjonalne pola `phase`
(przy `--phase`) oraz `phases` i `uiPhases` (listy id, tylko gdy plan ma fazy `[UI]`). Kody wyjścia bez zmian: PASS 0, FAIL 1, BLOCKED 2.

### Marker zapisu (`prototype writing`)

`prototype writing <projekt> <slug> start|done|status [--ttl <s>] [--by <login>] [--screens <N>]`: agent oznacza, że jeszcze pisze ekrany prototypu.
`start` zapisuje atomowo, pod blokadą prototypu, plik `<root>/<slug>/.writing.json` (`since`, `renewedAt`, `expiresAt`, `by`) i dopisuje
`.writing.json` do `<proto>/.gitignore` (gdy się nie da, ostrzeżenie w raporcie); powtórzone `start` na ważnym markerze zachowuje `since` (początek pisania)
i `by` (chyba że podano `--by`), ustawia `renewedAt` i nowy `expiresAt`, więc odnawia marker (także skracając TTL); na wygasłym albo uszkodzonym zaczyna od nowa. `--ttl` to sekundy, 60–3600, domyślnie 900; `--by` to login (pierwszy znak litera lub cyfra, dalej litery, cyfry i `. _ @ + -`, najwyżej 64 znaki;
domyślnie `agent`); `--screens` (tylko `start`, liczba całkowita 1–200) zapisuje w markerze `screensPlanned`, czyli ile ekranów agent zamierza napisać
(strona statusu pokazuje „N z M” i pasek); powtórzone `start` bez `--screens` zachowuje wartość z ważnego markera. `done` usuwa marker (idempotentnie, bez blokady), `status` zwraca `{status, writing: null | {since, expiresAt, renewedAt, by[, screensPlanned]}}`
(bez blokady). Kody: PASS 0; BLOCKED 2 z `error`: `needs_migration` (prototyp bez `comments.json`), `lock_timeout` (zajęta blokada prototypu,
np. trwa `iterate` albo `restore`: ponów), `invalid_args` (zły `--ttl`, `--by` albo `--screens`); FAIL (1) nie występuje. Nieznana akcja to błąd argparse.

Wpływ ważnego markera na inne komendy (definicja w [prototypes](prototypes.md#marker-zapisu-agent-pisze-ekrany)):
- `accept` (także `--project`) kończy się BLOCKED `writing_in_progress` przed pytaniem o hasło i ponownie pod blokadą transakcji;
  przy `--project` wstępna kontrola obejmuje wszystkie prototypy, a odmowa w trakcie niesie listę `writing` oraz `accepted`/`pending`.
  `accepted: []` bywa przy markerze wykrytym dopiero w `build` pierwszego prototypu projektu (nic jeszcze nie zapisano).
- `gate` (po slugach i `--plan`) zwraca FAIL z wpisem `missing` `{slug, path, screen: null, reason: 'writing'}` dla każdego prototypu z ważnym
  markerem, także gdy wszystkie jego ekrany mają ważną akceptację; linia „Bramka prototypu: pominięta — …” nadal daje PASS.
- `wait` nie zwraca `accepted`, dopóki marker jest ważny (komentarze budzą jak dotąd).
- `list` ma pole `writing`: `null` albo `{since, expiresAt, renewedAt[, screensPlanned]}` (bez `by`).
- `iterate` przy ważnym markerze działa i dodaje ostrzeżenie do raportu.
- `status --ttl` i `done --by` są walidowane tak samo jak `start` (zły argument = BLOCKED `invalid_args`).

### Pytania do eksperta (`prototype question`, `prototype user roles`)

Agent zadaje pytania ekspertom domenowym przez wspólny serwer prototypów; odpowiedzi czyta z pliku `<root>/<slug>/questions.json`
przebiegu (bez serwera). Odpowiadać może tylko człowiek z rolą `expert` na stronie `/_experts`: agent nie ma polecenia `answer`.
Model, limity i strona: [prototypes](prototypes.md#pytania-do-eksperta), poufność: [security](security.md).

```bash
skillsync prototype serve . --demo                                                       # pokaz tylko do odczytu bez kont, każdy interfejs (--host 127.0.0.1 zawęża)
skillsync prototype sync . --check                                                       # FAIL (kod 1): nieaktualne pliki albo brak wymaganych tokenów motywu (pole `missing`)
skillsync prototype user add . ewa.ekspert@example.com --role expert --password-prompt   # konto eksperta
skillsync prototype user roles . ewa.ekspert@example.com --role expert --role commenter  # zastępuje zbiór ról
skillsync prototype question url .                                                       # adres strony i liczba kont expert
skillsync prototype question add . --slug zwroty --text "Czy zwrot po terminie jest dozwolony?" \
  --ref "Sekcja 3|docs/specs/zwroty.md#3|Zwrot do 14 dni od zakupu." \
  --option "Tak, bez limitu" --option "Tak, do 30 dni:recommended" --option "Nie" \
  --assume "do 30 dni" --origin docs/specs/zwroty.md:Q3                                  # domyślnie agent nie czeka
skillsync prototype question add . --slug zwroty --text "Czy przycisk „Zwróć” ma być widoczny po terminie?" \
  --screen zwroty#s4 --assume "nie" --origin docs/specs/zwroty.md:Q6                      # link „Zobacz ekran s4” na stronie eksperta (powtarzalne, z --ref najwyżej 5)
skillsync prototype question add . --text "W jakich formatach eksport?" --multi \
  --option "CSV:recommended" --option "XLSX:recommended" --option "PDF" \
  --assume "CSV i XLSX" --origin docs/specs/eksport.md:Q4                                 # ekspert zaznacza jeden lub więcej wariantów
skillsync prototype question add . --text "Jaki jest klucz idempotencji płatności?" \
  --blocking --to ewa.ekspert@example.com --origin docs/specs/platnosci.md:Q5            # bez --slug: przebieg ze speca
skillsync prototype question list . --slug zwroty --status open,answered                 # jeden przebieg
skillsync prototype question list . --status open,answered                               # wszystkie przebiegi
skillsync prototype question show . qn-3f9a1c                                            # slug nie jest potrzebny: id jest unikalny
skillsync prototype question wait . qn-3f9a1c --timeout 600
skillsync prototype question wait . --all --slug zwroty --since 2026-11-12T08:00:00Z
skillsync prototype question record . qn-3f9a1c --spec docs/specs/zwroty.md --line 42   # po wpisaniu wiersza do speca
skillsync prototype question withdraw . qn-3f9a1c                                        # tylko pytanie otwarte
skillsync prototype question export . --slug zwroty --json > pytania.json
skillsync prototype question migrate . --dry-run                                         # plan podziału starego pliku
skillsync prototype question migrate . --slug-for docs/specs/zwroty.md=zwroty            # rozstrzygnięcie jawne
skillsync prototype question purge . --slug zwroty                                       # podgląd: tylko wycofane (bez --yes niczego nie zmienia)
skillsync prototype question purge . --slug zwroty --yes                                 # usuwa wycofane
skillsync prototype question purge . --slug zwroty --include-open --yes                  # także otwarte bez odpowiedzi i komentarza
```

Plik użytkowników jest prywatny: na POSIX tryb 0600, na Windows ACL (tylko użytkownik, SYSTEM i administratorzy lokalni). Komendy `prototype user ...` i `prototype serve` na Windows nie wypisują ostrzeżenia o pominiętej kontroli; stary plik z szerokim ACL jest naprawiany przy pierwszym zapisie albo odczycie (jedna linia `ACL Windows: ograniczono dostęp do pliku ...` w `warnings`), a ostrzeżenie `nie udało się ograniczyć dostępu do pliku ...` pojawia się tylko, gdy ACL nie da się ustawić (FAT/exFAT, udział sieciowy bez obsługi, brak uprawnień). Zapis pliku i tak się udaje. Szczegóły: `docs/prototypes.md`.


- `add`: dokładnie jedno z `--assume <wartość>` (pytanie nie zatrzymuje agenta; ekspert potwierdza albo koryguje) i `--blocking`
  (agent czeka). `--slug <slug>` wybiera przebieg, którego `<root>/<slug>/questions.json` dostaje pytanie. Bez `--slug` przebieg
  to jedyny prototyp, którego `requirements` równa się ścieżce z `--origin`; zero dopasowań = BLOCKED `slug_required` (z listą
  `prototypes`), kilka = BLOCKED `slug_ambiguous` (z `candidates`). `--ref` ma postać `TYTUŁ|ADRES[|WYCIĄG]` (do 5; adres względny albo `https`), `--option` `ETYKIETA[:recommended]`
  (do 8, najwyżej jedna rekomendowana, kilka tylko z `--multi`), `--multi` (pytanie wielokrotnego wyboru: ekspert zaznacza co najmniej jeden
  wariant, wymaga co najmniej 2 opcji; „żaden” to własna odpowiedź tekstowa; do pytań typu „które formaty/kolumny/role” zamiast
  rozbijania na pytania tak/nie), `--to LOGIN` powtarzane (domyślnie wszyscy eksperci; login spoza pliku kont daje
  ostrzeżenie), `--refers-to <id>` (spór z wcześniejszą odpowiedzią w tym samym przebiegu), `--origin <spec>:Q<n>` (ścieżka względna). Wyciąg ze speca lub
  dokumentacji agent wpisuje sam: serwer nie czyta repozytorium. Skan sekretów w treści jest heurystyką: trafienie blokuje
  (`secret_detected`, exit 2, bez wartości w raporcie), `--force` przepuszcza z ostrzeżeniem. Raport: `questionId`, `slug`, `specLine`
  (wiersz do speca), `files` (pliki do commita, w tym `<root>/<slug>/questions.json`, `<root>/.gitignore` z `questions.json.lock` i `*.tmp` oraz `<root>/.gitattributes`
  z `merge=skillsync-questions`, dopisywane przy pierwszym użyciu), `created` (nowo utworzone pliki: plik pytań, jego lock, `<root>/.gitignore`, `<root>/.gitattributes`), `ignored` (te utworzone pliki, które git ignoruje, czyli lock: `created` = `files` bez dopisanych wcześniej + `ignored`),
  `notes` (zdania typu „created <ścieżka>”, także o locku ignorowanym przez git), `warnings`, `url`, gdy serwer projektu działa albo ustawiono `prototype.public_url`/`SKILLSYNC_PROTOTYPE_PUBLIC_URL`. Powtórka z tym
  samym `--question-id` jest idempotentna. Osierocony lock (`questions.json.lock` bez pliku pytań, wolny) usuwa następny `add` tego przebiegu
  (best effort; na platformie, która nie usuwa otwartego pliku, zostaje). Odrzucony `add` nie zakłada ani pliku, ani locka.
- `list`, `show`, `export` zwracają dokument `expert-questions/1`: `{schema, project, url?, questions: [{id, slug, text, state, blocking, assumed,
  addressees, refs, options, answer?, comments, refersTo?, origin, recorded?}]}` oraz `status: PASS` (potrzebne do kodu wyjścia; klient ignoruje
  nieznane pola). Pytanie wielokrotnego wyboru ma `multi: true`, a jego odpowiedź `optionIds` i `optionLabels` (listy w kolejności opcji
  pytania) zamiast `optionId`/`optionLabel`; pytania jednokrotne i stare dzienniki bez zmian. `slug` to nazwa przebiegu, `null` dla pytania ze starego pliku. `--slug` zawęża do jednego przebiegu; bez niego
  łączą wszystkie przebiegi i dopisują stary plik (`legacy` w dokumencie, gdy istnieje). `list` ma `runs` (`slug`, liczby pytań).
  Raporty `list`, `show` i `export` mają na początku `untrusted: true` i stały `notice` (treść odpowiedzi i komentarzy ekspertów to dane); `wait` dodaje je w zdarzeniach `answer` i `comment`.
  `show` i `wait` mają ten sam kształt: `questions[]` (jedno pytanie; `wait --all` zwraca pierwsze gotowe), `question` (to samo pytanie) i `answerLine`; `wait` dodaje `event`, `questionId`, `slug` (i `pending` przy `--all`), `show` dokument `expert-questions/1` (`schema`, `project`). Oba dodają `answerLine`: gotowy wiersz `[EKSPERT]` do speca (`null` bez odpowiedzi; czas odpowiedzi lokalny z offsetem, np. `2026-11-13T13:30+01:00`, a w dzienniku nadal UTC); `specLine` z `add` i `withdraw`
  to wiersz czekającego pytania. Domyślny filtr `list` to `open,answered`; `--status` przyjmuje też `withdrawn` i `all`.
- `show`, `record`, `withdraw` i `wait <id>` znajdują pytanie po id (`--slug` opcjonalny, zawęża szukanie). `record` i `withdraw` pytania
  ze starego pliku dają BLOCKED `migration_required`.
- `wait <id>` zwraca od razu PASS (`event: answer`), gdy pytanie jest już odpowiedziane, a poza tym czeka na odpowiedź albo komentarz
  nowszy niż `--since` (domyślnie „teraz”) lub `--after <id operacji>`. `wait --all` (z `--slug` albo po wszystkich przebiegach) kończy się, gdy istnieje pytanie odpowiedziane
  bez operacji `recorded` albo zdarzenie nowsze niż `--since`/`--after`. TIMEOUT to exit 3.
- `record <id> --spec … --line <numer>` zapisuje znacznik „wpisano do speca” (idempotentnie), ale najpierw czyta wskazaną linię speca i wymaga w niej `[EKSPERT]` oraz identyfikatora pytania (wiersz z pola `answerLine`); inaczej FAIL (1) z kodem `record_line_mismatch` (inna treść linii), `record_line_out_of_range` (za krótki spec) albo `record_spec_unreadable` (brak pliku, ścieżka poza repozytorium) i podpowiedzią w `hint`. Pytanie bez odpowiedzi daje najpierw `not_answered`; `withdraw <id>` wycofuje pytanie otwarte,
  także blokujące, zwraca stan po operacji (`state` i `questions[0].state` = `withdrawn`) i zwykły wiersz `Q` programisty (odpowiedzianego nie da się wycofać: `already_answered`).
  `--reason <tekst>` (1–500 znaków, bez sekretu; `secret_detected` bez `--force`) zapisuje powód w operacji `withdraw` (pole `reason`) i zwraca go w raporcie; ekspert, do którego pytanie było skierowane, widzi je jako „wycofane” z powodem. Złe `--reason` to BLOCKED `invalid_reason`; powtórne `withdraw` niczego nie zmienia (zostaje pierwszy powód).
- `url` zwraca adres strony i liczbę kont `expert` (adres z `prototype.public_url` albo zmiennej `SKILLSYNC_PROTOTYPE_PUBLIC_URL`, gdy ustawiono: za tunelem ssh inaczej byłoby 127.0.0.1; błędna zmienna to BLOCKED `invalid_public_url`); BLOCKED (`users_file_missing`, `users_file_invalid`, `no_expert_accounts`) znaczy, że
  nie ma komu zadać pytania, więc agent pyta zwykle. Nie sprawdza, czy serwer działa (pole `running` mówi o stanie z pliku serwera).
- `add`, `list`, `show`, `export` i `url` przyjmują `--users` (plik kont względem projektu, jak w `user`).
- `migrate [--dry-run] [--slug-for SPEC=SLUG]` dzieli stary `<root>/questions.json` na pliki przebiegów według `origin.spec`. Idempotentna.
  Wynik: `migrated` (per slug: `operations`, `added`), `legacy` (`trashed` | `renamed:<nazwa>` | `absent`), `files`; `--dry-run` zwraca `plan` i `legacy: kept`.
  Pytanie bez jednoznacznego przebiegu albo operacja bez swojego `ask` = BLOCKED `unassigned` z listą, bez zapisu. Stary plik trafia
  do kosza (`trash-put`, `gio trash`, `trash`; bez nich zmiana nazwy na `.questions.json.migrated` (nazwa z kropką, której serwer prototypów nie wydaje), nigdy usunięcie) dopiero po odczycie zwrotnym;
  `verify_failed` = BLOCKED, stary plik zostaje.
- `purge --slug <slug> [--include-open] [--yes]` usuwa z dziennika przebiegu domyślnie tylko pytania wycofane (wycofane z komentarzem albo odpowiedzią zostaje); `--include-open` dodaje otwarte bez odpowiedzi
  i bez komentarza (świeże pytanie czekające na eksperta nie jest usuwane bez tej flagi). Pytanie z odpowiedzią albo komentarzem zostaje w obu trybach,
  tak samo pytanie, do którego odwołuje się zachowane (`refersTo`). Bez `--yes` tylko podgląd: `dryRun: true`, lista `removed` z `id`, `q`, `state`, `reason`, `text`
  oraz `skipped` (pytania pominięte z powodem: otwarte z podpowiedzią `--include-open`, wycofane z komentarzem, zachowane przez `refersTo`). Wymaga `--slug`.
- `merge --driver %O %A %B` to sterownik gita `skillsync-questions` (rejestracja: [prototypes](prototypes.md#pytania-do-eksperta-w-gicie));
  `merge <projekt> <inny-questions.json> --slug <slug>` scala ręcznie do pliku tego przebiegu (`--slug` jest wymagany).
- Kody wyjścia: 0 PASS, 1 FAIL, 2 BLOCKED, 3 TIMEOUT. `1` znaczy, że magazyn odrzucił treść albo stan (zła ścieżka w `--origin`,
  nieznany `--refers-to`, `already_answered`, `withdrawn`, brak obu `--assume`/`--blocking` jako `invalid_mode`, konflikt scalania, `migrate` przy tym samym `id` z inną treścią).
  `2` znaczy błędne wywołanie albo środowisko (zły format `--origin`, `secret_detected`, plik za duży 507, uszkodzony plik, brak kont w `url`,
  `slug_required`, `slug_ambiguous`, `migration_required`, `unassigned`, `verify_failed`).

## Bezpieczeństwo

| Komenda | Skutek |
|---|---|
| `leakcheck [path] [--worktree \| --staged \| --range A..B \| --history \| --text FILE \| --stdin] [--target ID] [--require full] [--no-config] [--json]` | skan treści, nazw plików i metadanych commitów albo tekstu poza gitem (`--text`, `--stdin`) |
| `leakguard patterns [--target ID] [--json]` | scalona lista wzorców (wbudowana i celu), JSON |
| `leakguard install-hook <repo> [--remove] [--target ID] [--json]` | zarządzany blok w lokalnych hookach `pre-commit` i `pre-push` |

`leakcheck` zwraca PASS (0), FAIL (1) z listą `{plik, kategoria, miejsce}` albo BLOCKED (2), gdy `--require full` nie ma
pełnego pokrycia, plik wzorców jest błędny albo tekst do skanu nie da się wczytać. Nieznany cel daje `coverage: generic` z ostrzeżeniem. Raport nigdy nie
pokazuje wartości wzorców. Model: [security](security.md).

`--text FILE` i `--stdin` to kontrola publikacji tekstu spoza gita (opis PR-a, komentarz, notatka wydania; do 1 MiB, bez tekstu w argumencie komendy): te same tożsamości i wzorce co `--range`, ale cel wynika z katalogu bieżącego albo `--target` (nie z repozytorium docelowego publikacji: podaj `--target <id>`), tekst skanowany w najostrzejszej klasie ścieżki (jak plik instrukcji), bez wymogu repozytorium git. Raport podaje etykietę (nazwa pliku albo `<stdin>`), kategorię i miejsce `linia:kolumna`, nigdy samego tekstu. Instrukcje skilli `pr-run`, `feature` i `dispatch`: opis zapisany do pliku przechodzi `leakcheck --text <plik>` przed `gh pr create`, `gh pr edit --body`, `gh release create`.

## Schematy JSON

Raporty mają co najmniej te pola; konsument ignoruje pola, których nie zna.

| Komenda | Kształt (co najmniej) |
|---|---|
| `layers check --json` | `{schema: 1, status, project, rule, layers, findings: [{code, severity, layer, message}]}` |
| `leakcheck --json` | `{schema: 1, status, coverage, target, findings: [{path, category, location}], notices, warnings}`; po udanym skanie także `authors_scope`, przy BLOCKED `reason` |
| `prototype comments --json`, `prototype wait --json` (zdarzenie `comment`) | `{status: "PASS", slug, …, untrusted: true, notice}`: `notice` to stały tekst (dosłownie w [prototypes](prototypes.md#role-kont) obok opisu pracy agenta na komentarzach), teksty wiadomości bez zmian; oba pola są na początku raportu; `wait` z TIMEOUT albo `accepted` nie ma tych pól (brak tekstów od osób) |
| `prototype login-link --json` | `{schema: 1, status: "PASS", url, expires}` (`url` = `http://127.0.0.1:<port>/_login/link?t=<token>`, `expires` = ISO-8601 UTC, token tylko na stdout) albo `{schema: 1, status: "BLOCKED", reason, code}` z `code` ∈ (exit 2) `server_not_running` (brak serwera projektu: brak pliku stanu, martwy PID, inny `instance` albo inny plik użytkowników), `ambiguous_server` (kilka serwerów projektu bez `--port`), `link_needs_loopback` (host serwera inny niż `127.0.0.1`/`localhost`/wildcard), `link_unavailable` (serwer z `--trust-proxy`: brak endpointu linku), `run_dir_unsafe` (katalog `run/` z prawami ≠ 0700, innego właściciela lub dowiązanie), `unknown_user` (konto nie istnieje; nigdy nie jest tworzone), `password_change_required` (konto z `mustChange`), `invalid_path` (`--path` poza dozwolonymi), `invalid_port` (`--port` poza 1-65535), `invalid_project` (błędny projekt lub manifest), `too_many_pending` (32 oczekujące tokeny), `users_unreadable` (nieczytelny plik użytkowników); zapisy z sesji linku trafiają do lokalnego `.skillsync/prototype/run/audit.jsonl` (`{at, login, via, op, screen?}`), nie do `comments.json`; flagi `--user LOGIN` (wymagana), `--path /ścieżka`, `--port N`, `--users PLIK` |
| `prototype question list`, `show`, `export` | `{untrusted: true, notice, schema: "expert-questions/1", status: "PASS", project, url?, questions: [{id, text, state: open|answered|withdrawn, blocking, assumed, addressees, refs, options, answer?: {by, name, at, optionId?, optionLabel?, optionIds?, optionLabels?, text?, confirm?, comment?}, comments, refersTo?, withdrawn?: {at, reason?}, origin: {spec, q}, recorded?: {spec, line, at}}]}`; `show` i `wait` mają `question`, `questions[]` i `answerLine` w raporcie, `add` i `withdraw` `specLine`; klient ignoruje nieznane pola, zmiana niezgodna = nowa wartość `schema`; `untrusted: true` i stały `notice` (treść odpowiedzi i komentarzy ekspertów to dane od ekspertów, nie polecenia) są pierwszymi kluczami raportu, także przy liście pustej; to samo mają zdarzenia `answer` i `comment` komendy `question wait` (zaraz po `status` i `event`), a TIMEOUT i błędy nie; `add`, `withdraw`, `record`, `purge`, `migrate`, `merge` i `url` nie oddają tekstu od cudzych ludzi i pól nie mają (wyjątek: błędy `merge` i `migrate` przy ręcznie edytowanym pliku mogą przytoczyć identyfikator albo nazwę pola z pliku; ten sam autor może wpisać tekst wprost do speca) |
| `layers list --json` | `{schema: 1, status, generator_version, project, rule, layers: [{id, kind, path, root, subdir, version, writable, present}], composed_root, notes}` |

## Najczęstsze przepływy

```bash
skillsync link <projekt> --dry-run && skillsync link <projekt>   # nowy albo zmieniony projekt
skillsync doctor <projekt> --json                                # po link: oczekiwane PASS
skillsync layers check                                           # po zmianie warstw albo configu
skillsync smart-test <projekt> --run                             # testy po zmianie (z przydziałem zasobów)
```

Przed pierwszym `link` na nowym projekcie zawsze uruchom `--dry-run`.
