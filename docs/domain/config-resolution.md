---
title: "Rozwiązywanie configu maszyny, warstw, katalogów danych i listy projektów"
tags: [skillsync, configuration, layers, library]
paths: ['skillsync', 'skillsync_workflow/common.py', 'skillsync_workflow/layers.py', 'skillsync_workflow/prototype.py', 'skillsync-windows.ps1']
source: "skillsync_workflow/layers.py: `config_path` (SKILLSYNC_CONFIG), `parse_machine_config` (klucze leakguard, nieznany klucz); skillsync_workflow/common.py (`workflow_config`); skillsync_workflow/prototype.py (zasoby z `LayerSet.skill_files`); docs/specs/2026-11-05-warstwy-generator.md (K3, K11, Kontrakty „Config maszyny”, „Katalogi”); fakty F-G03 (katalogi per system); plan docs/plans/2026-11-05-warstwy-generator.md (Wynik 2.3, 2.4)"
verified: 2026-11-06
---
Reguła:
- Config maszyny: `SKILLSYNC_CONFIG` (pusty = nieustawiony, jak w `config_path`; także w `prototype user`), inaczej `~/.config/skillsync/config.yaml`
  na każdym systemie. Brak pliku = błąd (exit 2, `Missing config: <ścieżka>`). Wyjątek: przy nadpisaniach `--layer`
  (`load_config(optional=True)` w `skillsync`) brak pliku daje pusty config bez błędu. Resolver warstw zgłasza brak
  configu jako BLOCKED `missing-config`. Klucze: `layers`, `layer_rules`, `isolation`, `leakguard`, `allow_override`,
  `legacy_library`, `discover_paths`, `tags`, `variables`, `python_command`, `entrypoint`, `memory_sync`, `prototype`
  (opis: [layers](layers.md), [leakguard](leakguard.md)).
- Dobór warstw wykonuje się po ścieżce projektu z `layer_rules` (`realpath`), nie po `project.name`. Szczegóły: [layers](layers.md).
- **Klucz `library:` nie jest czytany przez żadną komendę.** Config z `library:`
  daje BLOCKED (exit 2) z instrukcją `skillsync layers migrate`.
  Migracja zapisuje stary klucz jako `legacy_library` (tylko do rozpoznawania zarządzanych symlinków).
- **`sync.library` w manifeście jest ignorowane.** `doctor` ostrzega o nim; `layers migrate --manifests` usuwa go z kopią
  zapasową. Manifest nie wybiera warstw: klucze `layers` i `layer_rules` w manifeście = błąd `doctor`.
- `prototype` czyta zasoby skilla `prototype` z warstw (`LayerSet.skill_files`, późniejsza warstwa wygrywa, linki w warstwie
  pomijane wg D73), tylko w akcjach `init`, `sync` i `migrate` bez `--assets`; pozostałe akcje czytają sam manifest.
  `--assets DIR` wyłącza warstwy.
- Komendy bez ścieżki (`link`, `instructions`, `push`, `pull`, `watch`) działają na każdym `.github/.skillsync.yaml`
  znalezionym pod `discover_paths` (także w worktree).
- Warstwy `workflow`: `workflow/defaults.yaml` → `workflow/projects/<project.name z / → ->.yaml` → `workflow:` manifestu.
  Mapy scalane, listy zastępowane. Scalanie plików wielowarstwowych (np. `skill-catalog.yaml`, `cause-keys.yaml`):
  [layers](layers.md), tabela K2.
- `project.name` musi być bezpiecznym slugiem (bez `..`, ukośników wiodących i `\`), inaczej `workflow_config` rzuca błąd.

## Katalogi danych, checkoutów, cache i kopii

Źródło: spec A (Kontrakty, „Config maszyny”), fakty F-G03 (`layers.py`, `common.py`).

| Rodzaj | Linux | macOS | Windows |
|---|---|---|---|
| config maszyny | `~/.config/skillsync/config.yaml` | to samo | to samo |
| dane (checkouty warstw, kopie) | `$XDG_DATA_HOME/skillsync` albo `~/.local/share/skillsync` | `~/Library/Application Support/skillsync` | `%LOCALAPPDATA%\skillsync` |
| checkout warstwy (`layers add` bez `--path`) | `<dane>/layers/<id>` | `<dane>/layers/<id>` | `<dane>\layers\<id>` |
| cache kompozytów skilli | `$XDG_CACHE_HOME/skillsync/composed` albo `~/.cache/skillsync/composed` | `~/Library/Caches/skillsync/composed` | `%LOCALAPPDATA%\skillsync\cache\composed` |

- `XDG_DATA_HOME` i `XDG_CACHE_HOME` są brane pod uwagę tylko jako ścieżki bezwzględne; inna wartość = katalog domyślny (`_env_dir` w `layers.py`).
- Kopie zapasowe manifestów: `<dane>/backups/manifests/<RRRRMMDD-GGMMSS>[-N]/<zakodowana ścieżka>/.skillsync.yaml`.
- Kopia configu: `config.yaml.bak-<znacznik czasu>` obok pliku configu (przy `SKILLSYNC_CONFIG` w repo kopia trafia tam też).
- Generator nie zapisuje logów maszyny. Logi walidacji projektu są w `.skillsync/logs/<id>/` w projekcie.
- Cache kompozytów jest odtwarzalny; rejestr `.projects.json` i blokada `composed/.lock` leżą w cache.

## Zmienne środowiskowe

| Zmienna | Skutek |
|---|---|
| `SKILLSYNC_CONFIG` | ścieżka pliku configu maszyny zamiast domyślnej |
| `SKILLSYNC_NO_SYMLINK=1` | wymusza tryb kopii zamiast symlinków (`sync.method: copy` w manifeście działa tak samo) |
| `SKILLSYNC_LOCK_TIMEOUT` | czas czekania na blokadę cache (domyślnie 60 s); przekroczenie = `[blocked] <projekt>`, exit 2 |

Warstw nie przekazuje się zmiennymi środowiskowymi. Procesy potomne `skillsync` dostają je jawnie w argumentach
(`--layer ID=PATH`). Komendy projektu uruchamiane przez `validate`, `guard` i `smart-test --run` nie dostają informacji o warstwach.

## Klucze pozostałe

| Klucz | Typ | Znaczenie |
|---|---|---|
| `discover_paths` | lista katalogów | korzenie przeszukiwane pod `.github/.skillsync.yaml` przez komendy bez ścieżki |
| `tags` | mapa `nazwa: [skille]` | grupy dla `@nazwa` w `skills:` manifestu |
| `variables` | mapa | wartości `{{ klucz }}` w szablonach instrukcji; nadpisywane przez `instructions.variables` w manifeście |
| `python_command` | string | interpreter w hookach Stop w `.claude/settings*.json` |
| `entrypoint` | `inline` \| `agents` | domyślny tryb wejścia instrukcji, gdy manifest nie podaje `instructions.entrypoint` |
| `legacy_library` | ścieżka | katalog starej biblioteki zapisany przez `layers migrate`; do rozpoznawania dowiązań i `--finish` |
| `memory_sync` | mapa | kopiowanie plików pamięci na hosty (`skillsync memory-sync`) |
| `prototype.default_password` | string | hasło domyślne kont `skillsync prototype user`; tylko w configu maszyny; plik nie może mieć uprawnień dla grupy ani innych (0600 lub ściślej; szerszy = BLOCKED z `chmod 600`; na Windows kontrola pominięta z ostrzeżeniem) |

`prototype.default_password`: brak pliku, brak klucza lub wartość niebędąca napisem = BLOCKED. Wartość nie trafia do żadnego
komunikatu ani wyniku JSON; błąd składni YAML configu zastępuje komunikat ogólny, bo PyYAML cytuje wiersz pliku
(`common.emit`/`redact` maskuje wzorce sekretów, nie to hasło). Źródło: `prototype.py:default_password`.

## Dlaczego

Config jest per maszyna (ścieżki, interpreter, warstwy), manifest per projekt. Jedyna ścieżka wyboru warstw to config
maszyny i `layer_rules`; manifest nie może wskazać organizacji, bo repo projektu nie decyduje o swojej warstwie.

Gdzie w kodzie: `skillsync_workflow/layers.py`: `config_path` (`SKILLSYNC_CONFIG`), `parse_machine_config` (klucze `leakguard`,
nieznany klucz = ostrzeżenie), `skillsync_workflow/common.py:workflow_config`, `skillsync_workflow/prototype.py:default_password`,
`skillsync-windows.ps1` (czyta `SKILLSYNC_CONFIG`; config bez `layers` = exit 2 z instrukcją migracji).
