# Rozwój generatora

Instrukcja zmieniania `skillsync` i pakietu `skillsync_workflow/`: środowisko, testy, kontrole składni, bezpieczne próby na
projekcie testowym, usługa watch, Windows i pułapki. Czytaj przed pierwszą edycją kodu.
Zasady udziału zewnętrznego (zgłoszenia, `leakcheck` przed PR): [CONTRIBUTING](../CONTRIBUTING.md).

## Układ repo

| Element | Rola |
|---|---|
| `skillsync` | wrapper uruchamiający pakiet z checkoutu (skrypt bez rozszerzenia) |
| `skillsync_workflow/` | pakiet generatora: polecenia w `cli.py`, komendy workflow (rejestracja w `__init__.py:register_subcommands`), `selfcmd.py` |
| `skillsync-ctl` | sterowanie usługą watch (bash, tylko Linux) |
| `skillsync-windows.ps1` | bootstrapper dla Windows |
| `tests/` | testy `unittest` i zasoby testowe (`tests/fixtures/`, `tests/e2e/prototype/`) |
| `library/` | warstwa `core`: skille, role, instrukcje i workflow rdzenia |
| `docs/` | dokumentacja użytkownika (ta strona i pozostałe) |

## Środowisko

- Python 3.11 lub nowszy (`tomllib`) i PyYAML. Testy używają wyłącznie `unittest`, bez pytest, żeby działały na Windows bez dodatkowych instalacji.
- Kod poleceń leży w `skillsync_workflow/cli.py`; testy importują go przez `tests/cli_module.py`. Plik `skillsync` to tylko
  wrapper (bez PyYAML kończy się komunikatem `PyYAML is required`).
- Uruchamianie z drzewa źródeł: `./skillsync ...` w katalogu repo albo `python3 -m skillsync_workflow ...`.
- Artefakty, które wywołują Skillsync (zadanie VS Code, launchery, hooki strażnika, `{skillsync}` w poleceniach), dostają
  postać z `selfcmd.self_argv()`: z checkoutu `python <checkout>/skillsync`, z pakietu `python -P -m skillsync_workflow`.
  Po przeniesieniu checkoutu uruchom `skillsync link` w projektach.

Instalacja deweloperska: `uv tool install -e .`. Stare dowiązanie `~/.local/bin/skillsync` usuń przed instalacją (inaczej uv odmówi nadpisania), potem sprawdź `which skillsync`.

## Testy

```bash
python3 -m unittest discover -s tests -p 'test_skillsync*.py'      # pełny zestaw
python3 -m unittest tests/test_skillsync_dispatch.py               # jeden plik
python3 -m unittest tests.test_skillsync_dispatch.ExpandSkillsTests # jedna klasa
python3 -m py_compile skillsync skillsync_workflow/*.py             # składnia Pythona
bash -n skillsync-ctl                                               # składnia skryptu
```

Po każdej zmianie Pythona uruchom `py_compile`, po zmianie `skillsync-ctl` uruchom `bash -n`. Każda zmiana zachowania wymaga testu;
nie osłabiaj asercji, żeby dopasować je do błędu.

### Skipy

Skip bez powodu albo poza listą dozwolonych to błąd, nie zielony wynik. Same skipy nie dowodzą zaliczenia: sprawdź liczbę
uruchomionych testów.

- Dekoratory z powodem są w `tests/layers_helpers.py`. Sprawdzają **system** (np. `needs_os_symlinks`, `needs_posix`, `needs_flock`),
  nigdy tryb linkowania: `SKILLSYNC_NO_SYMLINK=1` nie może pomijać testu, który tylko podkłada dowiązanie.
- Test, który sprawdza wynik trybu symlink, przypina go dekoratorem `@link_mode('symlink')`; odpowiednik trybu kopii to `@link_mode('copy')`.
- `tests/skip-baseline.json` opisuje dozwolone pominięcia per system i tryb; `scripts/check_skips.py` sprawdza log biegu:

```bash
python3 -m unittest discover -s tests -p 'test_skillsync*.py' -v 2>&1 | tee run.log
python3 scripts/check_skips.py run.log --system ubuntu-latest --mode copy
```

### Zmienne testów

| Zmienna | Skutek |
|---|---|
| `SKILLSYNC_TEST_LIBRARY` | katalog warstwy `core` z szablonami i skillami (`library/` w repo); część testów pomija się bez niej |
| `SKILLSYNC_TEST_NODE` | ścieżka do Node ≥ 18 (testy silnika prototypów) |
| `SKILLSYNC_E2E_CHROME` | binarka Chrome/Chromium dla testów E2E; bez niej przypadki z przeglądarką się pomijają |
| `SKILLSYNC_E2E_PORTS` | porty E2E `<chrome>,<serwer1>,<serwer2>` (domyślnie `9333,4445,4446`) |
| `SKILLSYNC_E2E_RESULTS_DIR`, `SKILLSYNC_E2E_ONLY` | katalog wyników JSON; lista ID scenariuszy (tylko do debugowania) |
| `SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH` | plik silnika prototypów ładowany przez testy modułu silnika (domyślnie kopia z `tests/fixtures/prototype/engine-v2/`) |
| `SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` | źródło silnika w warstwie `core`; test sprawdza świeżość kopii |
| `SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1` | jawne pominięcie kontroli świeżości (gdy `V2_PATH` wskazuje inny silnik niż kopia) |
| `SKILLSYNC_PROTOTYPE_NOW` | wstrzyknięty zegar `prototype init` (pole `created`); tylko testy |
| `SKILLSYNC_TEST_DOC_TOPICS` | plik `tematy-dokumentow.md` dla testu tematów (opcjonalny; bez niego test czyta plik z `docs/prototype-contract/`) |
| `SKILLSYNC_CONFIG` | config maszyny; w testach ustawiaj konfigurację słownikiem `config`, a nie zmienną po imporcie |

Kompletny bieg prototypów kończy się bez skipów: wymaga Node, `SKILLSYNC_TEST_LIBRARY`, `SKILLSYNC_E2E_CHROME` i node ≥ 22 dla E2E.
Ciężkie biegi uruchamiaj pod limitem pamięci, np. `systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0`.

### Katalog tymczasowy

Testy tworzą repozytoria git w katalogu tymczasowym. Gdy `TMPDIR` leży w repozytorium, test sprawdzający `map` poza repo git nie przechodzi.
Ustaw prywatny katalog tymczasowy spoza repo, a nie współdzielone `/tmp`:

```bash
export TMPDIR="$(mktemp -d -p <katalog spoza repo>)"
```

`TMPDIR` ustawiają same testy: `tests/test_skillsync_leakguard.py`, `tests/test_skillsync_closure.py`,
`tests/test_skillsync_prototype_server.py` i `tests/e2e/prototype/env.py`.

### CI

Workflow `ci` (`.github/workflows/ci.yml`) ma zadania:

- `test (<system>, <python>, <tryb>)`: systemy `ubuntu-latest`, `windows-latest`, `macos-latest` × Python 3.11 i 3.12 × tryby `symlink`, `copy`
  (`SKILLSYNC_NO_SYMLINK=1`; Windows tylko w trybie kopii); biblioteka testowa to `library/` z checkoutu, silnik prototypu
  z `library/skills/prototype/assets/repo/prototype.js`; składnia (`py_compile`, `bash -n`), `tests/k1_grep.py`, testy jednostkowe, skipy wobec
  `tests/skip-baseline.json` (`scripts/check_skips.py`) i smoke (`scripts/smoke.sh`, na Windows `scripts/smoke.ps1`);
- `install-smoke (<system>)`: budowa koła (`uv build`), `scripts/check_wheel.py`, instalacja jako narzędzie i `skillsync --version`, `--help`, `leakcheck`, `self_argv` poza checkoutem;
- `e2e (ubuntu-latest)`: E2E prototypu w Chrome (pominięty test jest błędem zadania);
- `release-check`: `scripts/check_release.py` (sekcja CHANGELOG dla `__version__`; w PR tylko gdy zmienia `__version__`);
- `leakcheck`: strażnik na drzewie roboczym i commitach PR-a; działa tylko w pull requestach repo `BATUSystems/skillsync`.

Workflow `release-smoke` (`.github/workflows/release-smoke.yml`) po tagu `v*` sprawdza CHANGELOG względem tagu i instalację z tagu na trzech systemach.

## Próba na projekcie testowym

Nigdy nie sprawdzaj nowego zachowania `link` najpierw na prawdziwym projekcie. Receptura z izolowanym configiem (nic nie jest zapisywane):

```bash
tmp=$(mktemp -d); mkdir -p "$tmp/proj/.github"
cat > "$tmp/config.yaml" <<EOF
layers:
  - {id: core, kind: core, path: <ścieżka do checkoutu repo>, subdir: library}
layer_rules:
  - match: ["**"]
    use: [core]
discover_paths: []
python_command: python3
EOF
cat > "$tmp/proj/.github/.skillsync.yaml" <<EOF
version: 1
project: {name: Org/Projekt, type: local}
skills: [feature]
instructions: {merge: [_base], template: Org-Projekt}
sync: {method: symlink}
tests: {command: "make test"}
EOF
SKILLSYNC_CONFIG="$tmp/config.yaml" ./skillsync link "$tmp/proj" --dry-run
SKILLSYNC_CONFIG="$tmp/config.yaml" ./skillsync instructions "$tmp/proj" --dry-run
```

Prawdziwy `link` na projekcie testowym (też w `$tmp`) pokazuje pełny efekt. Potem `SKILLSYNC_CONFIG=... ./skillsync doctor "$tmp/proj"`
powinien dać PASS. Przed pierwszym `link` doctor zwraca FAIL z powodu braku zainstalowanych skilli i `CLAUDE.md`; to oczekiwane.
`skillsync link` bez ścieżki działa na wszystkich projektach z `discover_paths`.

W recepturze warstwa ma `path` (istniejący checkout); sam `source` bez `layers add` daje warstwę nieobecną
(`[info] Layer core is not present`).

## Tryb symlink i kopia

- Domyślnie generator tworzy dowiązania. `SKILLSYNC_NO_SYMLINK=1` albo `sync.method: copy` wymuszają kopie ze stanem w `.github/.skillsync-state.json`.
- Testy na obu trybach: dowiązania przypinane do trybu symlink, odpowiedniki kopii do trybu copy (zob. sekcja „Skipy”).
- Przed zmianą logiki linkowania uruchom testy w obu trybach.

## Usługa watch

- Usługa `skillsync-watch.service` (Linux, systemd user) uruchamia `watch` i trzyma kod z chwili startu. Po zmianie generatora: `skillsync-ctl restart`.
- Plik unitu leży poza repo, w katalogu użytkownika systemd; `ExecStart` wskazuje dowiązanie `skillsync` do watch.
- Watch linkuje automatycznie przy każdej zmianie w warstwach. Przy wieloetapowej zmianie (rename skilla, nowa wersja protokołu) zatrzymaj usługę
  (`skillsync-ctl stop`), wykonaj zmiany, sprawdź `link --dry-run`, potem `skillsync-ctl start`.
- Stan i logi: `skillsync-ctl status`, `skillsync-ctl logs`.

Na macOS i Windows usługi watch nie ma; `skillsync watch` uruchamia się tam ręcznie.

## Windows

- Skopiuj razem `skillsync`, katalog `skillsync_workflow/` i `skillsync-windows.ps1`. Sam `skillsync` bez pakietu nie działa (to wrapper).
- Bootstrapper `skillsync-windows.ps1` z Pythonem 3 i plikiem `skillsync` obok woła `skillsync link <projekt>` i tworzy config, jeśli go brak.
- Nieudany `git pull` warstwy nie przerywa synchronizacji, ale skrypt kończy się kodem 3 i ostrzeżeniem o starych artefaktach. Nieudany `git clone` daje kod 1.
- Bez Developer Mode dowiązania nie działają: generator przechodzi w tryb kopii; wymusisz go `SKILLSYNC_NO_SYMLINK=1` lub `sync.method: copy`.
- Ścieżki w wyjściach JSON i w zapisywanym stanie przechodzą przez `.as_posix()` (nie `str(relative_to(...))`); wywołanie `redact` na kluczu słownika przekazuje `True` (próg sekretu z env 8 znaków). Szczegóły: `docs/lessons/windows-sciezki-json-i-redact-kluczy.md`.
- Test środowiska: `tests/windows/skillsync-windows-check.ps1` (raport `[PASS]/[FAIL]/[SKIP]/[INFO]`, praca na kopii w katalogu tymczasowym).
  Uruchomienie z korzenia repo: `powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\windows\skillsync-windows-check.ps1`.
- Windows nie ma backendu własności procesów: `env up` i `env down` zwracają BLOCKED.

Ręczne testy na Windows i macOS są zaplanowane na późniejsze wydanie. Zakres obsługi Windows w v1.0.0 to tryb kopii, CLI, `link` i `doctor` (bez usługi, bez `flock` i TTY), zgodnie z tabelą wsparcia.

## Reguły zmian w kodzie

- **Rozwijanie skilli ma dwie implementacje** (`skillsync_workflow/cli.py:expand_skills` i `skillsync_workflow/common.py:expand_project_skills`) oraz zduplikowane stałe. Zmieniaj obie; `tests/test_skillsync_dispatch.py` pilnuje zgodności.
  `skillsync_workflow/cli.py` importuje inne moduły pakietu tolerancyjnie, wewnątrz funkcji (`except ModuleNotFoundError`,
  np. `_layers_module`, `expand_skills`); nowy import z pakietu w `cli.py` rób według tego wzorca, nie na poziomie modułu.
- **Nowa komenda workflow:** moduł w `skillsync_workflow/` z `register(subparsers)` i handlerem zwracającym kod wyjścia (`emit(report)`); wpis w `register_subcommands`; testy; opis w [cli](cli.md).
- **Komenda synchronizacji:** parser w `skillsync_workflow/cli.py:main` i funkcja `cmd_*` w tym samym pliku.
- **Nowy artefakt hosta:** builder wywoływany w `link_project`, obsługa `--dry-run`, reguła własności (nie nadpisuj ręcznych plików), wpisy gitignore, sprzątanie osieroconych plików, testy i opis w [architecture](architecture.md).
- Komendy wykonujące polecenia (`--run`) pobierają je tylko z zaufanego manifestu lub warstwy. Ścieżki stanu przechodzą przez kontrolę `storage.py:state_path`
  (bez dowiązań i wyjścia z projektu). Wynik przechodzi przez `common.py:redact`.
- Po zmianie kontraktu (nazwy skilli, ścieżki artefaktów, klucze manifestu, kody wyjścia): testy oraz aktualizacja [cli](cli.md), [manifest](manifest.md),
  [architecture](architecture.md) i `skills/skillsync/SKILL.md` w warstwie `core`.
- Istotna zmiana generatora lub CLI: wpis w `CHANGELOG.md` w sekcji `[Unreleased]`. Zmiana sekcji **Breaking** wymaga opisu zmiany niezgodnej.

## Kontrola przed commitem

```bash
python3 -m py_compile skillsync skillsync_workflow/*.py
bash -n skillsync-ctl
python3 -m unittest discover -s tests -p 'test_skillsync*.py'
```

Dla zmiany linkowania dodatkowo: `link --dry-run` na projekcie testowym (receptura wyżej).

## Strażnik wycieków na maszynie dewelopera

Pełne sprawdzenie (`skillsync leakcheck . --require full`) potrzebuje plików tożsamości innych celów niż publiczny rdzeń.
Nie trzymamy ich w tym repo: wskaż katalog w configu maszyny (`~/.config/skillsync/config.yaml`, albo ścieżka z `SKILLSYNC_CONFIG`):

```yaml
leakguard:
  identity_dir: ~/Repos/<repo-operacyjne>/leakguard
```

Z ustawionym `identity_dir` `--require full` działa bez osobnego configu pomocniczego. Szczegóły pola: [configuration](configuration.md#strażnik-wycieków-leakguard).
`skillsync layers check --isolation` szuka plików tożsamości w katalogu biblioteki warstwy (korzeń `layer.yaml`), nie w całym klonie.

Po sklonowaniu repo do rozwoju zainstaluj hooki strażnika: `skillsync leakguard install-hook <klon>`. Worktree dzielą `.git/hooks` klonu, więc jedna instalacja obejmuje wszystkie; bez niej push nie przechodzi przez strażnika (przy `core.hooksPath` poza `.git` komenda odmówi i trzeba wołać `leakcheck` ręcznie).

## Flagi dla narzędzi integracyjnych

Strażnik ma flagi jawnych wejść dla hooków i narzędzi integracyjnych, nie do pracy ręcznej (działają, ale `--help`
ich nie pokazuje): `leakcheck` i `leakguard install-hook` mają `--identity`, `--targets-json` i `--trailer-allow`,
a `leakcheck` i `leakguard patterns` mają `--target-patterns`. W `leakcheck` każda z flag `--identity`, `--targets-json`, `--trailer-allow` zastępuje wejścia
z configu maszyny w całości: bez `--targets-json` cel jest nieznany, więc `--require full` kończy się BLOCKED.

## Pułapki

1. **`doctor` przed `link`**: FAIL (brak zainstalowanych skilli i instrukcji Claude). Po `link` ma być PASS. Same ostrzeżenia dają PASS (exit 0).
2. **Martwe wpisy gitignore po zmianie nazwy skilla**: `link` usuwa osierocone dowiązania (`[clean] Removed obsolete`), ale wpisy `skills/<stara>` w `.claude/.gitignore` i `.agents/.gitignore` zostają; są nieszkodliwe.
3. **Task folder-open** (`.vscode/tasks.json`): generator odświeża ścieżkę tylko w tasku o postaci, którą sam zapisał. Task niestandardowy o tej samej etykiecie zostaje bez zmian.
4. **Usługa watch** uruchamia kod z chwili startu; przy zmianach w wielu krokach zatrzymaj ją.
5. **Pliki generowane** (`CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `.codex/*`) nie są do edycji; zmieniaj warstwę i uruchom `link`, albo `push`.
6. **`.github/.gitignore` zawiera `.skillsync.yaml`**: manifest nie jest śledzony przez git (wymaga `git add -f`).
7. **`sync.library` w manifeście** jest ignorowane z ostrzeżeniem; dobór warstw jest tylko w configu maszyny.
8. **`instructions`** buduje też wszystkie artefakty Codexa (skille, role, `AGENTS.md`), nie tylko instrukcje.
9. **`add`** przepisuje YAML manifestu: traci komentarze i sortuje `skills`.
10. **Cursor**: przy włączonych skillach inne pliki `*.mdc` w `.cursor/rules/` są usuwane jako przestarzałe.
11. **`list`** w kolumnie opisu drukuje `---` (pierwsza linia `SKILL.md`).
12. **`checkpoint`** po awarii procesu może zostawić katalog `<run-id>.lock`; usuń go ręcznie po sprawdzeniu, że nic nie zapisuje.
13. **`smart-test`** wymaga, by projekt był korzeniem repo git; ścieżki wspólne i niezmapowane dają fallback do wszystkich testów (zamierzone).
14. **`SKILLSYNC_CONFIG`** wczytywane jest przy imporcie `skillsync_workflow.cli` (`CONFIG_PATH`); w testach używaj słownika `config`.
15. **Zapis `tasks.json` przez generator** przepisuje plik jako czysty JSON (usuwa komentarze i CRLF); kopia zapasowa jest nadpisywana przy kolejnym odświeżeniu.
