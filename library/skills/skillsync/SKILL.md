---
name: skillsync
description: Skillsync workflow guide for managing skill layers (core, org, personal), adding new skills, syncing instructions between projects and their owning layers, and understanding symlink-based skill distribution
user-invokable: true
---

# Skillsync — zarządzanie warstwami skilli Copilota

Skillsync to lokalne narzędzie CLI (`~/.local/bin/skillsync`) synchronizujące skille i instrukcje między warstwami (core, org, personal) a projektami. Obsługuje wiele projektów jednocześnie poprzez dowiązania symboliczne.

> **Dokumentacja i kod generatora:** osobny projekt `<katalog repo skillsync>`
> (`README.md` z mapą; `docs/layers.md`, `docs/cli.md`, `docs/manifest.md`, `docs/library.md`, `docs/architecture.md`,
> `docs/workflow.md`, `docs/development.md`). Ten skill to skrót operacyjny; przy rozbieżności
> rozstrzyga kod i `docs/` tamtego repo. Zmiany generatora prowadź tam, nie w repo scripts.

## Architektura systemu

Treść żyje w **warstwach** (`layer.yaml` w korzeniu układu): `core` (rdzeń: `_base.md`, hooki, skille ogólne), `org` (organizacje użytkownika: ludzie, struktura, instancje) i `personal` (własne). Lista warstw (`layers`) i reguły doboru (`layer_rules`, po rozwiązanej ścieżce projektu) są tylko w `~/.config/skillsync/config.yaml`; manifest projektu warstwy nie wybiera. Później wymieniona warstwa wygrywa per plik; `org` nie nadpisuje rdzenia bez `allow_override`. Brak trybu `library:`. Każda warstwa ma układ poniżej:

```
<warstwa>/
├── skills/
│   ├── <nazwa-skilla>/
│   │   └── SKILL.md        ← ŹRÓDŁO PRAWDY dla skilli (zasila .github/skills/ ORAZ .claude/skills/)
│   └── ...
├── agents/
│   └── <nazwa>.md          ← podagenci Claude Code (frontmatter name/description/tools/model)
├── hooks/
│   ├── check-tests.py      ← hook Stop (bramka testów), jeden skrypt Pythona dla Linux i Windows
│   └── check-closure.py    ← hook Stop (zamknięcie planu), tylko biblioteka standardowa
└── instructions/
    ├── _base.md             ← wspólna preambuła (persona, workflow)
    └── <template>.md        ← instrukcje specyficzne dla projektu

layer.yaml                  ← id, kind, version, core_min, requires

<projekt>/.github/
├── skills/
│   └── <nazwa-skilla>/     ← symlink -> plik warstwy albo kompozyt z wielu warstw (cache skillsync)
├── copilot-instructions.md ← złączenie _base + template (budowane przez skillsync)
└── .skillsync.yaml         ← manifest projektu

<projekt>/CLAUDE.md          ← nagłówek Claude Code + _base + TEN SAM template (dla Claude Code)

<projekt>/.claude/
├── skills/<skill>          ← symlink (lub kopia) - lustro skills: z manifestu
├── agents/<agent>.md       ← symlink (lub kopia) - przy `feature` wszystkie role (jak Codex); `agents:` zawęża
├── hooks/check-tests.py    ← KOPIA - tylko gdy hooks.stop_tests: true i projekt NIE ma `feature`
├── hooks/check-closure.py  ← KOPIA - projekty z `feature`, chyba że hooks.stop_closure: false
├── settings.json           ← COMMITOWANY, ręcznie kuratorowany; skillsync scala tylko hooks.Stop
└── .gitignore              ← wpisy per plik zarządzany (nie całe katalogi)
```

**Kluczowa zasada**: `.github/skills/*/` to **symlniki**, NIE oryginały. `copilot-instructions.md` i `CLAUDE.md` to oba pochodne (`_base` + `<template>.md`) — źródłem prawdy są zawsze warstwy, nigdy któryś z tych dwóch wygenerowanych plików bezpośrednio.

## Komendy

| Komenda | Kierunek | Opis |
|---------|----------|------|
| `skillsync link <path>` | warstwy → projekt | Tworzy symlinki skilli (skill z wielu warstw = kompozyt z symlinków), buduje `copilot-instructions.md` **i** `CLAUDE.md` z `_base` + template (tworzy `CLAUDE.md`, jeśli jeszcze go w projekcie nie ma) |
| `skillsync push <path> [--to-layer ID]` | projekt → warstwa | Zapisuje zmiany do warstwy-właściciela pliku (nowy plik: `--to-layer`, domyślnie personal); do `core` tylko z `--to-layer <core>`, inaczej `[info]` i pominięcie; przed zapisem `leakcheck` (wyciek = exit 1, warstwa niezapisywalna = 2). Lokalną część `copilot-instructions.md` **i/lub** `CLAUDE.md` kopiuje do tego samego template (odcina `_base` po markerze `<!-- SKILLSYNC:BASE-END -->`; dla `CLAUDE.md` najpierw odcina też wymagany nagłówek Claude Code) |
| `skillsync layers init\|add\|update\|list\|use\|check\|migrate` | config i warstwy | `init` tworzy warstwę osobistą, `add` podpina źródło, `update [id]` aktualizuje checkouty (`pull` = `update` + `link`), `list`/`check [--project P] [--isolation] [--json]` pokazują i walidują efektywny zestaw, `use` ustawia `layer_rules`, `migrate` (domyślnie dry-run) przenosi stary config |
| `skillsync leakcheck <katalog\|projekt> [--target ID] [--staged\|--range A..B\|--history]` | tylko odczyt | skan wycieku tożsamości (FAIL = exit 1); `leakguard install-hook <repo>` instaluje lokalne hooki pre-commit/pre-push |
| `skillsync instructions <path>` | warstwy → projekt | Tylko przebudowuje `copilot-instructions.md` i `CLAUDE.md` bez dotykania symlinków |
| `skillsync skills index [--check]` | warstwy | Generuje indeks skilli z `workflow/skill-catalog.yaml` (cache lub stdout); `--check` → exit 1 przy nieaktualnym |
| `skillsync suggest <path>` | tylko odczyt | JSON: skille do dodania/usunięcia wg katalogu (organizacja, technologie) |
| `skillsync skills list <path> [--status S] --json` | tylko odczyt | JSON `schema: 1`: skille projektu (active/companion/suggested/available) z metadanymi |
| `skillsync skills remove <projekt> <skill> [--no-link]` | zapis manifestu | usuwa jawny wpis z `skills:` (odwrotność `add`), potem `link` |
| `skillsync status <path> --json` | tylko odczyt | JSON `schema: 1`: plany z postępem (`## Progress`; statusy `draft|approved|in_progress|done|archived|unknown`, `archived` bez `needsHuman`, `needsHuman: closure` przy `in_progress` ze wszystkimi krokami `[x]`, `pull_request`, gdy taki plan ma `Ticket:` bez `PR:`; `doctor` ostrzega tylko o `closure`), specyfikacje, pliki FRD (bez treści), przebiegi, dzierżawy |
| `skillsync plan approve\|archive\|unarchive <projekt> <plik> [--json]` | zapis linii `Status:` | tylko `draft` → `approved` / `done` → `archived` / `archived` → `done` w `docs/plans/*.md`; reszta pliku bez zmian; błąd = exit 2, `BLOCKED` |
| `skillsync knowledge init <path> [--dry-run]` | projekt | Tworzy brakujące `docs/domain/index.yaml`, `docs/lessons/index.yaml`, `docs/README.md` (nigdy nie nadpisuje) |
| `skillsync map <path> [--check]` | projekt | Generuje `.skillsync/map.md` (mapa repo); `--check` → exit 1 przy nieaktualnej/brakującej |

`link`, `push` i `instructions` mają `--dry-run` (pokazuje `[dry-run] Would ...`, nic nie zapisuje) — używaj przed pierwszym realnym `link` na nowym projekcie. Operacje są idempotentne: drugi `link` bez zmian w warstwach nic nie wypisuje.

Wspólna flaga `--layer id=ścieżka` (powtarzalna; ścieżka = katalog warstwy z `layer.yaml`) zastępuje warstwy dla jednej komendy; forma `,synthetic` (most 0.9.x) jest odrzucana (`synthetic-refused`). Brak dopasowania `layer_rules` albo config bez `layers` = `BLOCKED` (exit 2).

### Instalacja i `skillsync setup`

- Instalator `install.sh` (Linux, WSL2, macOS; natywny Windows → komunikat o WSL2, exit 2) z tagu wydania: instaluje `uv`, jeśli go brak,
  `uv tool install --force git+https://github.com/BATUSystems/skillsync@<tag>`, sprawdza `skillsync --version` = tag i uruchamia `skillsync setup "$@"`.
  Bez sudo; wymaga `git` i `curl` (nie instaluje ich). Odmawia nadpisania obcego `skillsync` na PATH (`SKILLSYNC_INSTALL_FORCE=1` pozwala).
  Zmienne: `SKILLSYNC_TAG`, `SKILLSYNC_INSTALL_SPEC`, `SKILLSYNC_NO_SETUP=1`. Kody: 0, 2 (środowisko), 3 (instalacja).
- `skillsync setup [--yes] [--dry-run] [--json] [--core-source URL] [--core-ref REF] [--org ID --org-source URL [--org-ref REF]] [--match GLOB]...`:
  kroki `author-email` (przed warstwami), `core`, `org` (najpierw `git ls-remote`, limit 30 s; warstwa tylko do odczytu),
  `rule` (`**: [core]` przy pustym `layer_rules`; z organizacją `layers use --match`, domyślnie `**`), `check` (`layers check` na przykładowym katalogu).
  Idempotentny: drugie uruchomienie daje `unchanged`. Statusy: PASS 0, FAIL 1 (`layers check`), BLOCKED 2.
- Nie aktualizuje narzędzia ani core przypiętego do tagu. Aktualizacja: ponowne `install.sh` z nowego tagu albo
  `uv tool install --force …@<tag>` + `skillsync layers update core --ref <tag>`. `skillsync pull` odświeża tylko checkouty i linki.

### Migracja maszyny

`skillsync layers migrate --layer-source core=<katalog z layer.yaml>` (dry-run) pokazuje, jak dotychczasowy config zostanie przeniesiony na warstwy; `--apply` zapisuje config z kopią zapasową (stary `library` → `legacy_library`). Config z mostem 0.9.x (`synthetic`) dostaje wpis zastąpiony prawdziwą warstwą `core`. Migracja i aktualizacja generatora idą razem; po niej `skillsync-ctl restart`, potem `skillsync link`.

### Claude Code: agenci, hook Stop, settings.json

- `.claude/skills/` i `.claude/agents/` **nie są w całości własnością skillsync** (projekty trzymają tam własne pliki). Skillsync sprząta i zastępuje wyłącznie symlinki do warstw (albo nietknięte kopie z pliku stanu), nigdy zwykłych plików, które sam nie utworzył — przy kolizji nazw wypisuje `[warn] Skipping ...`.
- Projekty z `feature`: kopiuje `check-closure.py` i scala wpis Stop w `.claude/settings.local.json`, zachowując resztę pliku. `hooks.stop_closure: false` usuwa wpis i skrypt. Plik śledzony przez git albo niepoprawny JSON: `[warn]`, bez zapisu. Blokuje raz na sesję i plan plan `done` zmieniony względem `HEAD`, gdy przebieg z `Przebieg:` nie ma rekordu `final`.
- `hooks.stop_tests: true` + `tests.command` w manifeście → kopiuje `check-tests.py` i dopisuje `hooks.Stop` do `.claude/settings.json` (forma exec: `command` = `python_command` z `~/.config/skillsync/config.yaml`, `args` = skrypt + komenda testów). Przed zapisem robi `settings.json.skillsync-backup`. `permissions`, inne hooki i `settings.local.json` są nietykane. Niepoprawny JSON w istniejącym pliku → błąd i pominięcie (nigdy nadpisanie).
- Hook: exit 2 + ostatnie linie błędów na stderr przy porażce testów; exit 0 przy `stop_hook_active`, braku zmian w repo (`git status --porcelain`) albo braku komendy testów.
- Uwaga: `settings.json` jest commitowany, a `.claude/hooks/check-tests.py` gitignorowany (generowany) — po świeżym klonie hook nie zadziała, dopóki nie wykonasz `skillsync link`.

### Tryb kopiowania (Windows bez Developer Mode)

Gdy `os.symlink` się nie uda (albo `sync.method: copy`), skillsync kopiuje i zapisuje w `.github/.skillsync-state.json` hash biblioteki z chwili kopiowania (bazę trójstronnego porównania; plik jest gitignorowany). `link`: zmiana tylko w bibliotece → odświeża kopię; zmiana tylko lokalna → zostawia i mówi `[info] Local edits ... not pushed yet`; zmiana po obu stronach → `[conflict]`, nic nie nadpisuje. `push`: lokalna zmiana skilla/agenta trafia do biblioteki (`Pushed local edits`), konflikt → ostrzeżenie. Hash ignoruje różnicę CRLF/LF. Zmienne: `SKILLSYNC_NO_SYMLINK=1` (wymuś kopiowanie), `SKILLSYNC_CONFIG=<plik>` (inny config).

### CLAUDE.md — jedno źródło prawdy z copilot-instructions.md

`CLAUDE.md` (root projektu) nie ma własnego template'u — renderuje się z dokładnie tego samego `instructions.merge` + `instructions.template`, co `.github/copilot-instructions.md`, tylko z doklejonym na sztywno nagłówkiem:

```
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```

Skutki:
- Edycja `_base.md` lub `<template>.md` w bibliotece + `skillsync link` aktualizuje **oba** pliki naraz.
- `skillsync push` z projektu, w którym edytowano `CLAUDE.md`, nadpisze ten sam template co `copilot-instructions.md` — jeśli oba pliki mają rozjechaną lokalną część, wygrywa ten, który zapushowano jako ostatni.
- Tryb `agents` (`instructions.entrypoint: agents` w manifeście albo `entrypoint: agents` w `~/.config/skillsync/config.yaml`; domyślnie `inline`): treść raz w bloku `AGENTS.md`, `CLAUDE.md` = nagłówek + `@AGENTS.md`, bez `.codex/skillsync-instructions.md`; `push` czyta wtedy blok `AGENTS.md`. Generator wraca do `inline` z ostrzeżeniem, gdy `AGENTS.md` jest śledzony/nieignorowany w git (także błąd gita), leży poza gitem z własną treścią, jest dowiązaniem albo ma zepsute znaczniki. Domyślną wartość zmieniaj dopiero po aktualizacji Skippstera (rozwija importy w sesjach).
- Plik jest automatycznie dopisywany do `.gitignore` projektu (tak jak `copilot-instructions.md`) — nie commituj go, edytuj i pushuj do warstwy.
- Wyłączenie dla konkretnego projektu: `claude: false` (albo `claude: {enabled: false}`) w `.github/.skillsync.yaml`.

## Jak dodać nowy skill (MUST-FOLLOW)

### Krok 1 — utwórz skill we właściwej warstwie

Treść ogólna → `core` (zapis tylko z `--to-layer <core>`); organizacja → jej warstwa `org`; własne → `personal`.

```bash
mkdir -p <korzeń-warstwy>/skills/<nazwa-skilla>
# następnie utwórz plik SKILL.md (patrz format poniżej)
```

Zasady pisania skilla (opis, referencje, swoboda, zimny odczyt): [authoring.md](references/authoring.md).

**NIGDY** nie twórz skilla bezpośrednio w `.github/skills/` projektu — zostanie nadpisany lub usunięty przy kolejnym `skillsync link`.

**Nazwa i zakres (nie mieszaj firm ani projektów; skill organizacji i projektu należy do warstwy org, nie do core):**
- skill organizacji (`<organizacja>`) — tylko firma: ludzie, struktura, eskalacje, wspólne instancje narzędzi;
- wiedza jednego projektu → skill z prefiksem projektu (`<projekt>-*`) albo `instructions/<Projekt>.md`;
- nazwa ogólna (`acli`, `azure-devops`, `csharp-*`, `sql-*`, `nhibernate`) = treść ogólna, bez projektów, serwerów i loginów;
  konkrety projektu przenieś do skilla projektu (`references/<źródło>.md`); ogólny skill może mieć najwyżej jednolinijkowy odsyłacz do skilla projektu, bez treści projektowej.
Szczegóły i tabela: `<katalog repo skillsync>/docs/library.md` („Organizacja, projekt, wiedza ogólna”).

### Krok 2 — dopisz skill do manifestu projektu

W `.github/.skillsync.yaml` dodaj wpis w sekcji `skills:`:

```yaml
skills:
- istniejacy-skill
- nowy-skill     # ← dodaj tutaj
```

### Krok 3 — katalog skilli (bez listy w instrukcjach)

Nie dopisuj skilla do sekcji "Skills Reference" w instrukcjach projektu: host ładuje opisy skilli z `description`, a lista duplikowałaby je w każdej turze.
Instrukcje projektu mają tylko punkty wejścia (3–5 linii). Zamiast tego:

1. dopisz skill do `workflow/skill-catalog.yaml` (albo `skill-catalog.d/<id>.yaml`) swojej warstwy (`scope`: org|client|project|tech|workflow|tool|personal, `org`, `tech`);
2. `description` ≤ 200 bajtów (`doctor` ostrzega powyżej);
3. `skillsync skills index` odświeża indeks (`--check` sprawdza aktualność);
4. `skillsync suggest <projekt>` pokazuje braki/nadmiary skilli projektu (tylko odczyt), `skillsync doctor` ostrzega o skillu cudzej organizacji, braku skilla organizacji i niewykrytej technologii;
5. `skillsync map <projekt>` buduje `.skillsync/map.md` (mapa repo dla `explore`/orkiestratora).

### Krok 4 — wykonaj linkowanie

```bash
skillsync link <project-path>
```

Komenda tworzy symlink `<projekt>/.github/skills/nowy-skill/` → warstwa (lub kompozyt) i przebudowuje `copilot-instructions.md`.

### Krok 5 — synchronizuj instrukcje z warstwami

```bash
skillsync push <project-path> [--to-layer <id>]
```

Zapisuje zaktualizowaną lokalną część instrukcji do szablonu jego warstwy-właściciela (nowy szablon: `--to-layer`, domyślnie personal). Przed zapisem działa `leakcheck`; wyciek blokuje push. Warstwa `core` jest niezapisywalna bez `--to-layer <core>`.

## Format pliku SKILL.md

```markdown
---
name: nazwa-skilla
description: Jednoliniowy opis bez cudzysłowów używany przez Copilot do autodiscovery
user-invokable: true
---

# Tytuł Skilla

Treść skilla...
```

### Zasady YAML front matter

- `name`: musi odpowiadać nazwie katalogu w bibliotece i wpisowi w `.skillsync.yaml`
- `description`: **jedna linia**, bez cudzysłowów, bez keywords: prefix — Copilot używa tego do autodiscovery
- `user-invokable: true` — pozwala użytkownikowi ręcznie wskazać skill w rozmowie
- Bez dodatkowych pól (`tags:`, `keywords:`) — powodują błędy walidacji

Szczegółowy przewodnik YAML: patrz skill `copilot-skills-yaml`.

## Zasady pracy z instrukcjami

### Marker końca preambuły

Plik `_base.md` kończy się markerem:
```
<!-- SKILLSYNC:BASE-END -->
```

Marker ten jest używany przez `skillsync push` do odcięcia wspólnej preambuły od lokalnej części instrukcji projektu. Marker pojawia się w złożonym `copilot-instructions.md` projektu i separuje blok `_base` od bloku lokalnego template'u.

**Nigdy nie usuwaj ani nie przenoś tego markera** — zepsuje `skillsync push`.

### Kiedy uruchamiać które komendy

```
Edytujesz copilot-instructions.md LUB CLAUDE.md w projekcie
  → skillsync push <path>          (projekt → warstwa-właściciel, ten sam template dla obu)

Edytujesz _base.md, fragment _base.d lub template w warstwie
  → skillsync link <path>          (warstwy → projekt, aktualizuje OBA pliki)

Dodajesz nowy skill do warstwy
  → skillsync link <path>          (tworzy symlink + przebudowuje instrukcje)

Projekt nie ma jeszcze CLAUDE.md
  → skillsync link <path>          (template albo feature/merge rdzenia; plik ręczny zostaje)

Podejrzewasz nadpisanie przy starcie VS Code
  → sprawdź WSZYSTKIE trzy pliki:
     - <projekt>/.github/copilot-instructions.md
     - <projekt>/CLAUDE.md
     - instructions/<template>.md w warstwie-właścicielu (`skillsync layers list`)
```

## Struktura .skillsync.yaml

```yaml
version: 1
project:
  name: Acme/Shop/shop-api   # identyfikator projektu
  type: local
skills:                                 # lista skilli do zlinkowania
- nazwa-skilla-1
- nazwa-skilla-2
instructions:
  merge:
  - _base                              # szablony do złączenia jako preambuła
  template: Acme-Shop-shop-api  # nazwa pliku template w bibliotece
sync:
  method: symlink                      # symlink | copy (symlink z automatycznym fallbackiem do kopii)
# --- workflow agentowy (Claude Code + Codex) ---
# `feature` w skills (alias `agentflow` nadal działa): włącza workflow; role dla obu hostów domyślnie z codex.yaml
agents: [implementer, test-writer, test-runner, reviewer, explore]  # opcjonalnie: zawężenie (pusta lista = brak ról)
hooks:
  stop_tests: true                     # tylko projekty BEZ feature; z feature testy uruchamia test-runner
tests:
  command: "cd tests && ./run-tests.sh"  # na Windows podaj komendę działającą w cmd.exe (np. dotnet test)
claude: false                          # (opcjonalnie) wyłącza CLAUDE.md i wszystko w .claude/
# vscode: {link_on_open: false}        # (opcjonalnie) bez taska `skillsync: link` (folderOpen) w .vscode/tasks.json
# dispatch:                            # (opcjonalnie) konfiguracja skilla `dispatch`, walidowana przez doctor
#   max_parallel: 3                    #   klucze: <katalog repo skillsync>/docs/manifest.md (sekcja `dispatch`)
```

Ustawienia per maszyna w `~/.config/skillsync/config.yaml` (nie synchronizowane): `layers`, `layer_rules`, `isolation`, `leakguard`, `discover_paths`, `variables`, `python_command` (Linux: `python3`; Windows zwykle `py`/`python`).

**Projekty archiwalne:** nieużywane projekty przenosimy do katalogu archiwum poza `discover_paths` (np. `~/projekty-archiwum/<organizacja>/<projekt>`), więc `link`/`watch`/`doctor` bez ścieżki ich nie obsługują. Archiwum leżące pod `discover_paths` wpisz do `discover_exclude` w configu maszyny (lista ścieżek, domyślnie pusta): pomijają je `retro --all` i `retro-scan --cross-project`, a komendy synchronizacji tego klucza nie czytają. W audytach manifestów, masowych operacjach, pushach i porządkach skilli pomijaj je; nie dopisuj ich wzorców do `workflow/skill-catalog.yaml`. Instrukcje projektu w bibliotece (`instructions/<Projekt>.md`) mogą zostać — bez manifestu nikt ich nie ładuje. Przywrócenie: przenieś katalog z powrotem i wykonaj `skillsync link <projekt>`.

## Prototypy (skillsync prototype)

Klikalne prototypy HTML w repo projektu (`<root>/<slug>/`, domyślnie `docs/prototypes`); nie mylić z Artifactem. Procedura: skill `prototype`.

- `skillsync prototype init <projekt> <slug> --requirements <md> [--title T]`: kopiuje silnik v2 i szablony (w tym `comments.json`, `.gitignore`, `.gitattributes`; bez `comments.js`), tworzy `tokens.css`, `theme.css`, `prototype.json`; istniejącego celu nie nadpisuje.
- Wspólne komentarze: `serve <projekt> [--host] [--port 4445] [--users PLIK] [--secure-cookies] [--trust-proxy] [--no-source-writes] [--login-to-view]` (serwer w pierwszym planie, odczyt publiczny, zapis po zalogowaniu; wymaga pliku użytkowników, bez niego exit 2; Ctrl+C = exit 0); `user add|passwd|remove|list|import <projekt> [<login>|<plik>] [--users] [--name] [--role commenter|acceptor|developer] [--password-prompt|--password-stdin]` (login = e-mail, hasło domyślne z `prototype.default_password` w configu maszyny 0600 i `mustChange`; nie wypisuj go); `ids <projekt> <slug> [--check|--fix]` (uchwyty `data-proto-id`; `--check` FAIL = exit 1); `migrate <projekt> <slug> [--dry-run] [--allow-skipped]` (v1 `comments.js` → v2 `comments.json`; na już zmigrowanym prototypie odświeża silnik i meta motywu).
- Iteracje: `iterate <projekt> <slug>` zamraża bieżącą wersję w `iterations/N/` (wymaga `ids --check` PASS; FAIL = exit 1). `restore <projekt> <slug> --screen sN --from N --reason TEKST --who LOGIN [--dry-run | --resume] [--users PLIK]` przywraca sekcję jednego ekranu po zamrożeniu bieżącej wersji; `--dry-run` tylko raportuje skutki, `--resume` dokańcza przerwane przywrócenie; odmowa = `BLOCKED`, nigdy FAIL.
- Decyzje agenta (lokalnie, `by: agent`): `comments <projekt> <slug> [--status open,awaiting|all]` (tylko odczyt); `triage <projekt> <slug> <wątek> --class clear|disputed|out_of_scope [--note T]`; `decide <projekt> <slug> <wątek> --kind agent|developer [--who LOGIN] --outcome O --text T [--iteration N]`; `resolve <projekt> <slug> <wątek> --reason done|kept|out_of_scope [--iteration N]`. `<wątek>` = `th-…` albo numer.
- Scalanie: `merge-comments <projekt> <slug> <inny-plik>` albo `merge-comments --driver %O %A %B` (sterownik scalania gita); konflikt = `BLOCKED` `merge_conflict`, bez zapisu.
- Klucz manifestu `prototype.commit_before_iteration` (bool, domyślnie `false`): tylko przy `true` `iterate` i `restore` z CLI commitują katalog prototypu przed zmianą (bez pusha).
- `sync <projekt> [<slug>] [--check]`: odświeża **tylko** `tokens.css` i `theme.css`; silnika (`prototype.js`, CSS) nie aktualizuje — poprawki silnika przenosi się ręcznie (skopiuj `prototype.js`, `prototype.css`, `components.css`, `screens.css` z `assets/repo` skilla `prototype`, usuń lokalne obejścia z `<style>`). `--check` nic nie zapisuje.
- `accept <projekt> <slug> (--screen sN … | --all) --by LOGIN` albo `accept <projekt> --project --by LOGIN`: akceptacja przez człowieka przy terminalu (hasło konta z TTY; bez TTY `BLOCKED` `tty_required`, bez pytania o hasło); agent jej nie uruchamia. `gate <projekt> (--plan PLIK | <slug> …)`: bramka akceptacji (`FAIL` = exit 1 przy brakach, `BLOCKED` = exit 2); `readiness --plan` z linią `Prototyp:` dodaje pole `prototypeGate`.
- `index <projekt>`: galeria `<root>/index.html`. `list <projekt> [--json]`: slug, tytuł, wymagania, ekrany, otwarte wątki.
- `<projekt>` jest wymagany we wszystkich akcjach (bez domyślnego `.`).
- Wspólne opcje `--layer id=ścieżka`, `--assets DIR` (tylko `init`, `sync` i `migrate` czytają zasoby skilla z warstw; `--assets` wyłącza warstwy, pozostałe akcje czytają sam manifest); wynik JSON; kody 0 PASS, 1 FAIL, 2 BLOCKED. FAIL (1): `sync --check` przy rozjeździe, `ids --check` przy brakujących, zduplikowanych lub nieprawidłowych uchwytach, `iterate` gdy `ids --check` nie przechodzi, `gate` przy brakujących akceptacjach. `BLOCKED` ma pierwszeństwo przed `FAIL`. Wyjątek: błąd argumentów (argparse) to exit 2 z usage na stderr, bez JSON — parser wyniku musi to obsłużyć.
- `list --json` (skrót kontraktu): `{status, root, prototypes[], warnings?}`; wpis `{slug, title, requirements, created, generated_by, path, screens, comments, commentsFormat, threads, iteration, acceptance}`. `screens` = liczba sekcji `.screen` w `index.html` bez atrybutu `data-prototype-example` (ekran przykładowy szablonu; starsze prototypy bez znacznika liczą go jak dotąd); `comments` = liczba otwartych wątków (open+awaiting+decided); `commentsFormat` 2 (`comments.json`), 1 (`comments.js`, ostrzeżenie o `migrate`) albo `null`; `threads` = `{open, awaiting, decided, closed, deferred, lost}` (`null` przy v1); `iteration` = bieżąca iteracja z `comments.json` (`null` przy v1), `acceptance` = `{screens, accepted, stale}` (`null` przy v1 i bez pliku). Uszkodzony `prototype.json` daje wpis `{slug, path, error}` i ostrzeżenie.
- Manifest, sekcja `prototype:` (opcjonalna): `root`, `theme.tokens_from` (CSS z tokenami), `theme.css`, `guidelines` (md), `commit_before_iteration` (bool); ścieżki względne, w repo, walidowane przez `doctor`.
- Szczegóły generatora: dokumentacja repo Skillsync, `docs/cli.md`, `docs/manifest.md` i rekord `docs/domain/prototype-manifest-and-cli.md`.

### Tryb dostarczania (`delivery`)

- Manifest, sekcja `delivery:` (opcjonalna): `mode` (`pr` | `direct`; brak = `pr`, `doctor` ostrzega) i `base` (gałąź bazowa; `direct` bez `base` daje ostrzeżenie). To inna sekcja niż `workflow.delivery` (provider, CI). Nie zmienia artefaktów `link`.
- `skillsync route <projekt> --json` zwraca `delivery {mode, base, source}`; `source`: `manifest`, `default` albo `invalid` (wtedy `error`, a agent pyta zamiast zakładać `pr`). Zachowanie agenta w obu trybach: instrukcje rdzenia (`CP`, `CPD`, `READY`).

## Troubleshooting

### Skill nie pojawia się po skillsync link
- Sprawdź czy nazwa w `skills:` w `.skillsync.yaml` dokładnie odpowiada nazwie katalogu w bibliotece (case-sensitive)
- Sprawdź czy `skills/<nazwa>/SKILL.md` istnieje w warstwie z `use` dla projektu (`skillsync layers list --project <projekt>`)
- Uruchom `skillsync link` ponownie i sprawdź output

### skillsync push nie odcina _base (duplicate preamble)
- Sprawdź czy `<!-- SKILLSYNC:BASE-END -->` jest w pliku projektu: `grep -c 'SKILLSYNC:BASE-END' .github/copilot-instructions.md` (albo `CLAUDE.md`)
- Jeśli marker jest nieobecny, uruchom najpierw `skillsync instructions <path>` żeby przebudować plik z markerem

### CLAUDE.md nie powstaje przy skillsync link
- Szablon jest opcjonalny dla `feature`: generator korzysta z `instructions.merge` lub `_base`,
  wskazując lokalne instrukcje projektu. Ręczny `CLAUDE.md` bez nagłówka generatora zostaje.
- Sprawdź czy w `.skillsync.yaml` nie ma `claude: false` / `claude: {enabled: false}`

### Zmiany w bibliotece nie trafiają do projektu
- Katalog `.github/skills/` zawiera symlniki — zmiany w warstwach są **natychmiastowe** (to ten sam plik na dysku)
- `copilot-instructions.md` wymaga jawnego przebudowania: `skillsync instructions <path>` lub `skillsync link <path>`

### Task `skillsync: link` na Windows przez Remote-SSH z Linuxa (Cursor/VS Code)
- Objaw: `Path to shell executable "\bin\bash" does not exist` przy `runOn: folderOpen`
- Przyczyna: task `type: shell` uruchamiany z lokalnego Linuxa próbuje otworzyć terminal z `/bin/bash` na zdalnym Windowsie
- Fix w `.vscode/tasks.json`: użyj `type: process` + `command: powershell.exe` (bez pośredniego shella)
- Fix w `.vscode/settings.json`: ustaw `terminal.integrated.automationProfile.windows` na `powershell.exe`
- Skrypt na Windows: `C:\Scripts\skillsync.ps1` (kopia `skillsync-windows.ps1` z repo Skillsync, `<katalog repo skillsync>`) — od teraz jest to bootstrapper: jeśli znajdzie Pythona 3 **i** plik `skillsync` obok siebie, woła Pythonowy `skillsync link` (pełne funkcje); inaczej tryb legacy bez CLAUDE.md/.claude/push. Kopia skryptu w `C:\Scripts\` **musi mieć obok siebie plik `skillsync`** (skopiuj oba albo uruchamiaj z klonu repo Skillsync).
- Nieudany `git pull` biblioteki (niezacommitowane zmiany w bibliotece, brak sieci) nie jest już połykany: skrypt wypisuje wyjście gita, synchronizuje ze starej biblioteki i kończy się ostrzeżeniem `[WARN] Biblioteka NIE zostala zaktualizowana` z kodem wyjścia **3** (zamiast `SUCCESS`/0). Nieudany `git clone` = exit 1. Gdy nowe skille „nie dojeżdżają”, najpierw sprawdź `git -C <biblioteka> status` na maszynie Windows.
- Aktualizacja generatora na Windows jest ręczna: po zmianie w repo Skillsync skopiuj ponownie `skillsync`, `skillsync_workflow/` i `skillsync-windows.ps1` (jako `skillsync.ps1`) do `C:\Scripts\`.
- Test środowiska Windows: `tests/windows/skillsync-windows-check.ps1` w repo Skillsync (raport PASS/FAIL z oczekiwanymi wynikami)
- Remote-SSH podaje `${workspaceFolder}` jako `/C:/Repos/...` — skrypt PS normalizuje to do `C:\Repos\...`

## Workflow v1 — kontrole, routing i pomiar

Źródło domyślnych ustawień: biblioteczny `workflow/defaults.yaml`; projekt nadpisuje
pole `workflow` manifestu. Mapy są scalane, listy zastępowane. Starsze manifesty pozostają
obsługiwane; `tests.command` jest fallbackiem. `feature` (lub alias `agentflow`) automatycznie dołącza istniejące
companiony workflow/pre-implement/smart-test/bugfix/codereview — bez względu na to, czy trafił do `skills:` wprost,
przez `@tag`, czy przez wildcard. Pozostałe skille (`workflow-retro`, `task-claim`, `workflow-upgrade`,
`skill-profiles`, `project-guard`, `dispatch`, `ci-stabilize`, `tracker-workflow`) należą do profilu
`workflow-extended`, włączanego w manifeście (`workflow.skill_profiles.selected`). Tagi `@nazwa` pochodzą z `tags:` w `~/.config/skillsync/config.yaml`
(per maszyna), nie z warstw.

| Komenda | Wynik |
|---|---|
| `route <path> --topic testing --files <paths>` | pasujące instrukcje i indeksowane lekcje |
| `doctor <path>` | statyczna walidacja źródeł, budżetów, referencji i adapterów |
| `readiness <path> --plan <file> --risk high --files <paths>` | gotowość struktury planu; przy linii `Spec:` także speca z `docs/specs` (pole `spec` w wyniku) |
| `review <path> --risk high --files <paths>` | checklista core + ryzyko + UI/project |
| `validate <path> --profile quick` | uporządkowany plan kontroli |
| `smart-test <path> [--base <ref>]` | selekcja według rzeczywistych zmian i zależności |
| `checkpoint <path> --run-id <id> --step <id> --status <status> --evidence <file> --next <opis> [--cause T] [--cause-key K] [--owner O] [--attempt N] [--denied N] [--inherited N] [--final]` | rekord kroku, także porażki; `--evidence` wymagany przy `PASS`; `--final` zamyka przebieg |
| `resume <path> --run-id <id>` | kontrola aktualności checkpointu |
| `metrics <path> [--classify]` | zapisane czasy, koszty i powtórki; klasy przebiegów (`--classify` wypisuje wolny tekst przyczyn) |
| `retro <path> [--run-id <id>] [--all-states]` | raport tylko do odczytu: klasy, rubryka, kandydaci zmiany; rejestr `docs/retro/decisions.yaml` daje `decided`/`recurred`, niepoprawny = `BLOCKED`; `--json` ma `proposals[]` (propozycje retrospektywy jakościowej bez uzasadnień i treści diffów; domyślnie nierozstrzygnięte, `--all-states` dodaje `rejected` i `applied`), `proposals_truncated` i `coverage.retro` |
| `retro --all` | kandydaci i `totals` ponad projektami z `discover_paths`, bez ścieżek z `discover_exclude` (config maszyny); `library_pairs[]` z parami (klucz, cel) sygnałów biblioteki z co najmniej dwóch projektów, bez nazw projektów, oraz `library_pairs_truncated` |
| `retro-scan <path> --run-id <id> --signals <plik> [--cross-project] --json` | tylko odczyt: próg i stan propozycji retrospektywy jakościowej dla sygnałów przebiegu (`--signals` to plik w `.skillsync/runs/<id>/`); wynik ma `signals[]`, `groups[]`, `sources`, `truncated`, `warnings`; w worktree czyta też główny checkout; `--cross-project` liczy powtórzenia sygnałów `repo: library` w innych projektach, bez ich nazw; złe wejście = `BLOCKED` |
| `retro-decide <path> --cause-key K --decision approved\|rejected` | zapis decyzji w `docs/retro/decisions.yaml`; `approved` wymaga `--owner` i `--case`, istniejący klucz `--replace`, po poprawce `--replace --ref` |
| `retro-decide <path> --proposal <run-id>/P<n> --decision rejected\|applied [--ref <sha>] [--replace]` | decyzja końcowa propozycji retrospektywy jakościowej: wpis w `retro-decisions.json` przebiegu i w sekcji `proposals:` rejestru `docs/retro/decisions.yaml` (opcjonalna; zawiera id, `repo`, `target`, decyzję, datę i `ref`); `applied` wymaga pełnego sha w `--ref`; wyklucza `--cause-key`, `--owner`, `--case`; w worktree `BLOCKED`; rejestr z `proposals:` wymaga generatora z tą obsługą |
| `dispatch <path> [--run-id <id>] [--json]` | stan dyspozycji tylko do odczytu: karty, paczki, dzierżawy, kolejka merge, koszt znany albo `unknown`; bez wolnego tekstu |
| `dashboard show <path> [--json] [--plan <rel>]` | tylko odczyt: stan planu, przebiegu i zgód jako JSON `schema: 1` (kontrakt: `docs/dashboard-contract.md` w repo Skillsync) |
| `dashboard serve <path> [--port 5140] [--host 127.0.0.1] [--approve-host H] [--no-approvals]` | strona statusu z przyciskami zgód; port per projekt, `Host` sprawdzany na każdym endpoincie, odczyt spoza loopbacku tylko przez `--approve-host`; zgody zapisuje in-process |
| `dashboard metrics <path> <run-id> [--json] [--plan <rel>]` | tylko odczyt: czasy podkroków `Fn.k`, liczby rund i porażek; podkrok bez danych oznaczony jawnie |
| `approvals record <path> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> --source cli\|dashboard\|ide [--sha SHA] [--file PLIK]` | zapis zgody w `.skillsync/runs/<run-id>/approvals.jsonl` (blokada plikowa, odmowa duplikatu = exit 2); tylko na słowo użytkownika, agent jej nie woła |
| `benchmark <path> --suite <yaml>` | powtarzalny plan przypadków offline |

Skill `dashboard` to strona statusu; wpis `status-board` w `skills:` rozwija się do `dashboard` tylko wtedy, gdy żadna warstwa nie ma skilla `status-board`.

`validate`, `smart-test`, `benchmark` wykonują komendy dopiero z `--run`.
Wyniki/cache/logi są lokalne w `.skillsync/`; generator dodaje ten katalog do gitignore.
Lesson records są trwałe w bibliotece `workflow/lessons` lub projektowym `docs/lessons`.
Wiedza domenowa projektu: jedno wspólne `docs/domain/index.yaml` (klucz `entries`) dla backendu i frontendu;
osobne projekty backend/frontend wskazują wspólny katalog nadrzędny przez `workflow.knowledge.domain: ../docs/domain`
(tylko projekt lub jego katalog nadrzędny, ścieżka kończy się na `docs/domain`). `route` zwraca ją w polu `domain`,
`doctor` waliduje rekordy i ostrzega o braku indeksu w projekcie z `feature` (format: `workflow/references/knowledge.md`).
Opcjonalne `load: bootstrap` w rekordzie indeksu domenowego powoduje zwracanie go przy każdym
`route`, także bez tematów/ścieżek; brak pola zachowuje stare dopasowanie. Jedyna jawna wartość
to `bootstrap`, inne wartości dają `doctor` FAIL. `docs/domain/glossary.md` indeksuj w ten sposób
i czytaj przed interpretacją ticketu; definicje zapisuj wyłącznie po potwierdzeniu ze źródłem.
Task `skillsync: link` przy `folderOpen` wykonuje `skillsync link ${workspaceFolder}` i propaguje
źródła warstw do projektu (symlinki skilli, kopie oraz generowane instrukcje i role).
Doctor/CLI review nie zastępują testów ani niezależnego review. Puste testy nie dają PASS.
Brak mapowania wymaga pełnego fallbacku albo BLOCKED, nie pominięcia sprawdzeń.

Szczegóły: [workflow](../workflow/SKILL.md) i
[konfiguracja](../workflow/references/configuration.md). Pythonowe źródło (repo Skillsync,
`<katalog repo skillsync>`) składa się z entry `skillsync` oraz pakietu `skillsync_workflow/`;
Windows kopiujący pojedynczy skrypt musi skopiować także ten pakiet, aby nowe komendy były dostępne.


## Workflow v2

Profile domyślne: `core` (agent-runtime-pitfalls) i `testing` (test-env, observed-e2e); `workflow-extended` (workflow-retro, task-claim, workflow-upgrade, skill-profiles, project-guard, dispatch, ci-stabilize, tracker-workflow) włączasz w `workflow.skill_profiles.selected`. Dodatkowe komendy:
`env`, `e2e`, `ci`, `claim`, `upgrade`, `profiles`, `tracker`, `guard`. Konfiguracje są
opcjonalne; środowisko, UI i tracker mają odpowiadać rzeczywistemu projektowi.
[Kontrakty i przykłady v2](../workflow/references/eight.md). Profile dodają do jawnego skills;
selected: [] jest świadomym opt-out. Nie skracaj ponownie _base.md w ramach tej migracji.
