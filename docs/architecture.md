# Architektura

Opisuje elementy generatora, składanie warstw, generowanie artefaktów per host agenta, przekazywanie warstw do procesów
potomnych, stan lokalny i granice. Czytaj przed zmianą generatora albo gdy trzeba ustalić, skąd w projekcie wziął się plik
i kto jest jego właścicielem.

## 1. Elementy

| Element | Rola |
|---|---|
| `skillsync_workflow/` | pakiet generatora: polecenia (`cli.py`: synchronizacja `link`, `instructions`, `push`, `pull`, `watch`, warstwy `layers`, strażnik `leakcheck`, `leakguard`) i komendy workflow (`doctor`, `route`, `validate`, `smart-test`, `checkpoint`, `prototype` i inne); punkt wejścia pakietu `skillsync` i `python -m skillsync_workflow` |
| `skillsync` | wrapper w korzeniu repo: uruchamia pakiet z checkoutu |
| `skillsync_workflow/selfcmd.py` | `self_argv()`: polecenie, którym Skillsync wywołuje sam siebie w zapisywanych artefaktach (`[python, <checkout>/skillsync]` albo `[python, -P, -m, skillsync_workflow]`) |
| `skillsync_workflow/dashboard.py` | stan projektu jako kontrakt JSON `schema: 1` (`dashboard show`, `dashboard metrics`) i polecenia `dashboard`; tylko odczyt, bez zapisu zgód; kontrakt: [dashboard-contract](dashboard-contract.md) |
| `skillsync_workflow/status.py` | raport `status`, zmiana linii `Status:` (`plan`/`spec approve`) i zgody: `approvals wait` oraz `approvals record`, którego jedyny zapis `status.record_approval` woła też serwer dashboardu |
| `skillsync_workflow/dashboard_http.py` | serwer HTTP strony statusu (`dashboard serve`, tylko biblioteka standardowa, ładowany leniwie); strona i zasoby pochodzą ze skilla `dashboard` aktywnych warstw; model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md) |
| warstwy | repo z układem `skills/`, `instructions/`, `agents/`, `workflow/` i plikiem `layer.yaml`; patrz [layers-authoring](layers-authoring.md) |
| usługa watch | ciągła synchronizacja po zmianach w warstwach; szczegóły usługi per system: [installation](installation.md) |
| `skillsync-ctl` | sterowanie usługą watch na Linuksie (`start`, `stop`, `restart`, `enable`, `disable`, `status`, `logs`); dostępne tylko w checkoucie repo |
| `skillsync-windows.ps1` | pomocniczy skrypt dla Windows: uruchamia `skillsync link` przez wrapper z checkoutu, więc warstwy obsługuje sam generator; dostępny tylko w checkoucie repo |

Zainstalowany pakiet zawiera wyłącznie `skillsync_workflow`; `skillsync-ctl` i `skillsync-windows.ps1` zostają skryptami checkoutu.

Generator nie zawiera treści organizacji. Wiedzę dostarczają warstwy, a generator je składa.

## 2. Składanie warstw

Maszyna ma listę warstw w configu ([configuration](configuration.md)). Dobór warstw dla projektu idzie po ścieżce projektu
(`layer_rules`). Składanie tworzy jeden widok rozwiązanych plików (`LayerSet`), z którego korzystają wszystkie komendy.

### Kolejność i własność pliku

- Plik należy do warstwy, z której został rozwiązany. Późniejsza warstwa w kolejności `use` wygrywa, `core` jest pierwsza,
  `personal` ostatnia.
- Nowy plik w skillu trafia do warstwy ostatniego dostawcy `SKILL.md`; nowy skill do `personal` (albo do wskazanej flagą warstwy).
- Komenda `push` zapisuje zmianę do właściciela pliku.

### Semantyka scalania

| Plik lub klucz | Semantyka |
|---|---|
| `skills/<n>/**` | per plik, późniejsza warstwa wygrywa; skill z dwoma dostawcami składany z symlinków |
| `instructions/<n>.md` | cały plik, późniejsza warstwa wygrywa; kolizja nazwy w dwóch warstwach = ostrzeżenie `doctor` |
| `instructions/_base.md`, `workflow/schema.json`, `workflow/benchmark.yaml`, `hooks/*` | tylko `core` |
| `instructions/_base.d/*.md` | suma; kolejność: warstwy w `use`, potem nazwa pliku; każdy fragment w znacznikach `SKILLSYNC:FRAGMENT` z nazwą warstwy i pliku |
| `agents/<n>.md` | cały plik, późniejsza warstwa wygrywa |
| `codex.yaml` | mapy scalane głęboko; listy wykluczeń skilli to suma |
| `workflow/defaults.yaml` | mapy scalane, listy zastępowane; wyjątki: `validation.checks[]` i `guardrails.rules[]` łączone po `id`, `routing.rules[]` i `guardrails.checks[]` dopisywane z deduplikacją po treści |
| `workflow/projects/*.yaml`, `workflow/trackers/*.yaml` | cały plik, późniejsza warstwa wygrywa |
| `workflow/skill-catalog.yaml` z `.d/` | suma wpisów `skills` i `orgs`; kolizja klucza: późniejsza |
| `workflow/skill-profiles.yaml` z `.d/` | per profil suma list skilli z deduplikacją |
| `workflow/cause-keys.yaml` z `.d/` | suma kluczy i aliasów; kolizja aliasu między warstwami = błąd `layers check` |
| `workflow/lessons/index.yaml` z `.d/` | suma rekordów po `path` |
| `NOTICE`, `CHANGELOG`, `README`, `layer.yaml`, `leakguard/` | nie są składane; należą do własnej warstwy |

### Model zaufania

Warstwa `org` nie nadpisuje elementów `core` (sprawdzeń, adapterów trackerów, ról, hooków, kluczy ról w `codex.yaml`
i istniejących plików skilli rdzenia, np. `skills/<n>/SKILL.md`) bez wpisu w `allow_override`. Kolizja bez zgody to błąd
`layers check`. Warstwa `personal` może nadpisywać. Warstwa jest kodem wykonywalnym (sprawdzenia i hooki), więc
podpinaj tylko źródła, którym ufasz. Przypięcie `ref` do taga ogranicza zmiany bez Twojej wiedzy.

### Zakaz symlinków w warstwach

Repo warstwy nie zawiera symlinków, junctions ani innych reparse points. Taki wpis to błąd `symlink-in-layer` w `layers check`
i w `leakcheck`. Odczyty generatora go pomijają. Symlinki tworzone przez generator w projektach i w cache kompozytów są dozwolone.

### Kompozyt skilla

Skill dostarczany przez co najmniej dwie warstwy jest składany w cache: `<cache>/composed/<skill>/<hash>`, gdzie katalog `<cache>` zależy od systemu (tabela w [configuration](configuration.md), sekcja „Katalogi danych i cache”).
Wpisy to symlinki do plików źródłowych, więc edycja w projekcie trafia do pliku właściciela. W trybie kopii w projekcie
zapisywany jest stan `.github/.skillsync-state.json` z bazą scalania per plik.

Nowy plik w kompozycie jest przy odbudowie i przy `push` przenoszony do właściciela po `leakcheck`. Gdy to niemożliwe, trafia do
katalogu `<hash>.orphans/`, którego generator nie usuwa; `layers check` go wypisuje. Nieużywane `<hash>` usuwa `link`.

## 3. Generowanie artefaktów per host

`link` buduje artefakty w projekcie w tej kolejności:

1. skille z listy `skills` (po rozwinięciu) do `.github/skills/<skill>` (symlink albo kopia);
2. `.github/copilot-instructions.md` z `_base` warstw i szablonu projektu;
3. `.cursor/rules/*`, gdy manifest ma sekcję `cursor`;
4. blok z treścią w `AGENTS.md` (tryb `agents`);
5. `CLAUDE.md` (w trybie `agents` tylko import `@AGENTS.md`), role, skille, hooki i ustawienia Claude Code, `.mcp.json`;
6. `.github/.gitignore`;
7. artefakty Codexa: `.agents/skills`, `.codex/*`, blok w `AGENTS.md`;
8. task `skillsync: link` w `.vscode/tasks.json`, gdy projekt ma `feature`;
9. wpis `.skillsync/` w `.gitignore`, gdy projekt ma `feature`.

Każdy krok respektuje `--dry-run` i jest idempotentny.

### Artefakty per host

Kolumna „właściciel” określa, co generator może nadpisać lub usunąć.

| Host | Artefakt | Źródło | Właściciel |
|---|---|---|---|
| Copilot | `.github/skills/<skill>` | `skills/<skill>` | cały katalog: niepotrzebne symlinki i katalogi są usuwane |
| Copilot | `.github/copilot-instructions.md` | `_base` i szablon | wygenerowany, nie edytować |
| Claude Code | `CLAUDE.md` | nagłówek generatora, te same `_base` i szablon | ręczny plik bez nagłówka jest pomijany z ostrzeżeniem |
| Claude Code | `.claude/agents/<rola>.md`, `.claude/skills/<skill>` | role i skille | tylko zarządzane pliki; własne pliki zostają |
| Claude Code | `.claude/hooks/*.py`, `.claude/settings*.json` | kopie hooków; scalany wpis Stop | scalany tylko wpis Stop, reszta pliku zostaje; kopia zapasowa przed zapisem |
| Claude Code | `.mcp.json` | `.vscode/mcp.json` albo domyślny serwer | wyłączany `mcp: false` |
| Codex | `.agents/skills/<skill>`, `.codex/agents/<nazwa>.toml` | skille i role | generowane |
| Codex | `.codex/config.toml`, `AGENTS.md` | tylko zarządzane bloki | reszta pliku zostaje; kopia zapasowa |
| Cursor | `.cursor/rules/*.mdc` | szablony i symlinki do `SKILL.md` | przy włączonych skillach każdy inny `*.mdc` w tym katalogu jest usuwany |
| VS Code | `.vscode/tasks.json` | task `skillsync: link` | tylko własny task; cudze taski zostają |

Obsługiwane hosty w v1.0.0: Copilot, Claude Code, Codex i Cursor (plus VS Code dla taska `link`). Stan zapisuje `.github/.skillsync-state.json`.

### Tryb symlink i kopia

- Domyślnie generator tworzy symlinki. Gdy system ich nie pozwala, `sync.method: copy` albo `SKILLSYNC_NO_SYMLINK=1`
  wymuszają kopie.
- W trybie kopii przy kolejnym `link` porównywane są trzy strony: zmiana tylko w warstwie odświeża kopię; zmiana tylko
  lokalna zostaje; zmiana po obu stronach daje `[conflict]` i nic nie jest nadpisywane.
- `push` przenosi lokalne zmiany do właściciela.

Windows: symlink wymaga trybu dewelopera albo uprawnień administratora. Gdy `link` nie może go utworzyć, wypisuje
`[warn] Symlinks unavailable (…); falling back to copy (Windows: enable Developer Mode)` i przechodzi na kopie ze stanem
w `.github/.skillsync-state.json`. Repo warstw nie zawierają symlinków (`symlink-in-layer`), więc ustawienie gita
`core.symlinks` nie wpływa na ich checkouty.

## 4. Przekazywanie warstw do procesów potomnych

Procesy potomne, które `skillsync` uruchamia sam (np. `quality` wywołuje `guard`), dostają warstwy jawnie w argumentach
`--layer ID=PATH`. Warstwy nie są przenoszone przez zmienne środowiskowe. Komendy projektu uruchamiane przez `validate`,
`guard` i `smart-test --run` nie dostają żadnych informacji o warstwach.

## 5. Stan i katalogi robocze

| Ścieżka | Zawartość | Git |
|---|---|---|
| `.github/.skillsync-state.json` | stan trybu kopii i plików pochodnych | ignorowany (`.github/.gitignore`) |
| `.skillsync/logs/<uuid>/` | logi `validate --run` i `smart-test --run` | ignorowany |
| `.skillsync/cache/smart-test/` | zapisany plan selekcji testów (nie wynik) | ignorowany |
| `.skillsync/runs/<run-id>.json` | punkty kontrolne przebiegu | ignorowany |
| `.skillsync/runs/<run-id>/approvals.jsonl` | zgody użytkownika (jedno źródło: `approvals record`, serwer dashboardu, IDE); jedna linia JSON na zgodę, blokada `approvals.jsonl.lock` | ignorowany |
| `.skillsync/environments/`, `e2e/`, `claims/`, `upgrade/`, `providers/` | stan komend workflow | ignorowany |
| `.skillsync/dispatch/<run-id>.yaml` | stan dyspozycji; zapisuje go skill, generator tylko go ignoruje | ignorowany |
| `~/.cache/skillsync/composed/` | cache kompozytów; odtwarzalny | poza projektem |
| `<projekt>/.skillsync/prototype/users.json` | konta serwera prototypów (domyślnie) | ignorowany |

Stan w `.skillsync/` zapisuje się przez kontrolowane ścieżki: bez dowiązań i wyjścia poza projekt.

## 6. Granice

| Generator czyta | Generator zapisuje | Generator nie rusza |
|---|---|---|
| config maszyny, manifest projektu, checkouty warstw, pliki projektu | artefakty z sekcji 3, stan w `.skillsync/`, checkouty warstw przy `layers` i `pull`, zapis do właściciela przy `push` | pliki własne użytkownika w `.claude/` i `.agents/`, cudze hooki i taski, pliki projektów poza zarządzanymi artefaktami |

- Nic nie trafia do repo projektu poza artefaktami z sekcji 3.
- Zapis do warstwy `core` wymaga jawnej zgody (`--to-layer core`).
- Hooki git generator dopisuje tylko do lokalnego katalogu hooków, w zarządzanym bloku. Gdy `core.hooksPath` wskazuje katalog
  śledzony w repo, generator odmawia i wypisuje instrukcję ręcznego wywołania.
- Strażnik wycieków działa przed zapisem do warstwy i przed publikacją: [security](security.md).

## 7. Prototypy

Komenda `prototype` tworzy klikalne prototypy HTML w repo projektu. Serwer wspólnych komentarzy jest samodzielnym modułem
bez zależności od PyYAML i bez importów z generatora. Kierunek zależności: moduł prototypów korzysta z serwera, nigdy odwrotnie.
Opis: [prototypes](prototypes.md).

