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
| `rule` | bez organizacji: gdy `layer_rules` jest puste, dodaje `**: [core]`; z organizacją: `layers use <id> --match …` (domyślnie `**`) | `done`, `unchanged`, `blocked` |
| `check` | `layers check` na przykładowym katalogu pasującym do reguły (nie na cwd) | `done` (PASS), `failed` (FAIL), `blocked` (BLOCKED) |

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
| `route`, `readiness`, `review`, `validate`, `smart-test`, `guard` | dobór kontekstu, gotowość planu, checklista review, plan i wykonanie kontroli, strażnik konwencji |
| `map`, `suggest`, `knowledge init`, `status`, `plan`, `dispatch` | mapa repo, sugestie skilli, szkielet `docs/domain` i `docs/lessons`, odczyt stanu, zmiana statusu planu, stan dyspozycji |

Komendy opisuje [workflow](workflow.md). `doctor` sprawdza m.in. skille z manifestu, sekcje `dispatch` i `prototype`,
spójność instrukcji z plikami oraz budżety bajtów.

## Przebieg i pomiar

| Komenda | Skutek |
|---|---|
| `checkpoint`, `resume` | zapis i sprawdzenie kroku przebiegu |
| `metrics`, `retro`, `retro-decide`, `benchmark` | pomiary, raport retrospektywy, rejestracja decyzji, benchmark narzędzi |

Szczegóły: [workflow](workflow.md).

## Rozszerzenia workflow

`env`, `e2e`, `tracker`, `ci`, `claim`, `profiles`, `upgrade`. Opis i kody: [workflow](workflow.md).

## Prototypy

`skillsync prototype init|sync|index|list|ids|migrate|serve|user|iterate|restore|accept|gate|comments|triage|decide|resolve|merge-comments <projekt> …`

Komenda działa na klikalnych prototypach HTML w repo projektu. Opis akcji, kodów i formatu: [prototypes](prototypes.md).

## Bezpieczeństwo

| Komenda | Skutek |
|---|---|
| `leakcheck [path] [--worktree \| --staged \| --range A..B \| --history] [--target ID] [--require full] [--no-config] [--json]` | skan treści, nazw plików i metadanych commitów |
| `leakguard patterns [--target ID] [--json]` | scalona lista wzorców (wbudowana i celu), JSON |
| `leakguard install-hook <repo> [--remove] [--target ID] [--json]` | zarządzany blok w lokalnych hookach `pre-commit` i `pre-push` |

`leakcheck` zwraca PASS (0), FAIL (1) z listą `{plik, kategoria, miejsce}` albo BLOCKED (2), gdy `--require full` nie ma
pełnego pokrycia albo plik wzorców jest błędny. Nieznany cel daje `coverage: generic` z ostrzeżeniem. Raport nigdy nie
pokazuje wartości wzorców. Model: [security](security.md).

## Schematy JSON

Raporty mają co najmniej te pola; konsument ignoruje pola, których nie zna.

| Komenda | Kształt (co najmniej) |
|---|---|
| `layers check --json` | `{schema: 1, status, project, rule, layers, findings: [{code, severity, layer, message}]}` |
| `leakcheck --json` | `{schema: 1, status, coverage, target, findings: [{path, category, location}], notices, warnings}`; po udanym skanie także `authors_scope`, przy BLOCKED `reason` |
| `layers list --json` | `{schema: 1, status, generator_version, project, rule, layers: [{id, kind, path, root, subdir, version, writable, present}], composed_root, notes}` |

## Najczęstsze przepływy

```bash
skillsync link <projekt> --dry-run && skillsync link <projekt>   # nowy albo zmieniony projekt
skillsync doctor <projekt> --json                                # po link: oczekiwane PASS
skillsync layers check                                           # po zmianie warstw albo configu
skillsync smart-test <projekt> --run                             # testy po zmianie (z przydziałem zasobów)
```

Przed pierwszym `link` na nowym projekcie zawsze uruchom `--dry-run`.
