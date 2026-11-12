---
title: "Warstwy generatora: core, org, personal, layer.yaml, layer_rules, LayerSet, kompozyty, _base.d, kody layers check"
tags: [skillsync, layers, configuration, composition, isolation, push, versioning]
paths: ['skillsync_workflow/layers.py', 'skillsync', 'skillsync_workflow/common.py', 'skillsync_workflow/quality.py', 'skillsync-windows.ps1', 'tests/test_skillsync_layers*.py']
source: "spec zadania (poza rdzeniem) (Terminologia, K1–K15, Kontrakty, Migracja); fakty z kodu skillsync_workflow/layers.py (match_rule, LayerSet, check_layers, migrate_config, _check_bridge, parse_layer_flag, isolation_findings), potwierdzone 2026-11-06; plan zadania (poza rdzeniem), sekcja Wynik (krok 2.1–2.4)"
verified: 2026-11-06
---
Reguła: generator składa skille, instrukcje, agenty i workflow z **warstw**. Warstwa to katalog o układzie
biblioteki (`skills/`, `instructions/`, `agents/`, `workflow/`, `codex.yaml`) z plikiem `layer.yaml` w korzeniu
układu. Lista warstw i reguły doboru są **tylko w configu maszyny** (`layers`, `layer_rules`); manifest projektu
nie wybiera warstw.

## Rodzaje warstw (`kind`)

- `core`: dokładnie jedna; wspólny agent flow i skille obok generatora (publiczny rdzeń). Domyślnie `writable: false`; zapis wymaga `writable: true` w configu i `--to-layer core`.
- `org`: wiedza jednej organizacji; może być wiele. Domyślnie zapisywalna.
- `personal`: warstwa użytkownika; najwyżej jedna na projekt. Domyślnie zapisywalna.

Nie mylić: `kind` warstwy (`core|org|personal`) to nie `term.kind` w `identity.yaml` (rodzaj terminu tożsamości).
„Warstwy workflow” (scalanie `defaults.yaml` → `projects/*.yaml` → `workflow:` manifestu) to inne pojęcie; patrz
[config-resolution](config-resolution.md).

## Config maszyny: `layers` i `layer_rules`

```yaml
layers:
  - id: core
    kind: core
    source: https://github.com/BATUSystems/skillsync
    subdir: library          # katalog układu w repo; domyślnie korzeń
    ref: v1.0.0              # tag, gałąź, commit lub brak
    path: ~/.local/share/skillsync/layers/core   # checkout; domyślnie wg systemu (config-resolution)
    writable: false          # domyślnie: core false, org/personal true
  - id: acme
    kind: org
    source: git@example.com:acme/skillsync-acme.git
layer_rules:                 # pierwsza pasująca wygrywa
  - match: ["~/work/acme/**"]
    use: [core, acme, moja]  # kolejność = rosnące pierwszeństwo
  - match: ["**"]
    use: [core, moja]
isolation:
  allowed_layers: [core, acme, moja]
```

Zasady configu:
- `use` zawiera wyłącznie `id` z `layers`; inaczej błąd.
- `core` pierwsza, `personal` ostatnia w `use` (inaczej ostrzeżenie); najwyżej jedna `personal` na projekt.
- Warstwa z `use`, której nie ma na maszynie, jest pomijana z informacją (`layers check`: NOT_RUN dla tej warstwy).
- Config zawiera tylko źródła i ścieżki użytkownika; rdzeń nie ma domyślnych adresów prywatnych repo.
- `layers add|use|migrate` edytują config tekstowo z kopią zapasową.

## Dobór warstw (`layer_rules`)

Źródło: `layers.py` (`match_rule`), `layers.py` (`LayerSet`). Reguły i zasady:

- Dopasowanie **wyłącznie po rozwiązanej ścieżce projektu** (`realpath`). `project.name` z manifestu nie bierze udziału;
  rozbieżność nazwy i ścieżki to ostrzeżenie.
- Składnia globu: `~` rozwijane; `*` = jeden segment; `?` = jeden znak; `**` = dowolna liczba segmentów;
  wiodące `**/` = dowolny prefiks; `[...]` **nie** jest klasą znaków (jest dosłowne); glob względny pasuje gdziekolwiek;
  `a/**` nie obejmuje `a`.
- Pierwsza pasująca reguła w kolejności pliku wygrywa. Reguła za `**` nigdy nie zadziała: `layers use` ostrzega o tym
  (pole `warnings`, „never matches”), gdy nowa reguła trafia za regułę `**`. `layers check` tego nie zgłasza.
- Wielkość liter: Windows i macOS porównują bez rozróżniania (na Windows `\` → `/`, litery dysków małe po normalizacji;
  na macOS składanie robi jawnie generator, bo samo `normcase` tego nie robi). Linux rozróżnia wielkość liter.
- Worktree git dopasowuje się po ścieżce głównego repo; gdy reguły brak, po własnej ścieżce.
- Brak pasującej reguły = **BLOCKED** (exit 2, kod `no-rule`) z instrukcją `skillsync layers use`.
- Komendy bez projektu (`layers list` bez `--project`, `import`, `leakcheck <katalog>`): najpierw reguła dla bieżącego
  katalogu; poza projektem wszystkie `layers` w kolejności core, org (kolejność z configu), personal.

## Reguły z `skillsync setup`

Źródło: `skillsync_workflow/setup_wizard.py` (`_rule_step`, `_core_step`), INST-1, 2026-11-09.

- `layers add core` nie tworzy reguły. `setup` gwarantuje regułę pokrywającą każdy projekt: gdy `layer_rules` jest puste,
  dopisuje `{match: ["**"], use: [core]}` (`layers_ensure_core_rule`).
- Z organizacją `setup` wywołuje `layers use <org> --match …` (domyślnie `**`); reguła zawiera `core`, jak przy ręcznym `layers use`.
- Przy istniejących regułach i `--yes` bez `--match` `setup` nie dodaje `**` (zasłoniłaby starą regułę) i kończy się BLOCKED.
  Wyjątek: jedyna reguła `**: [core]` (ślad po `setup` bez organizacji) nie liczy się jako reguła użytkownika; wtedy organizacja
  dostaje `**` (reguła staje się `**: [core, <org>]`). Bez `--org` istniejące warstwy organizacji i ich reguły zostają nietknięte.
- Identyczna reguła już istnieje: `unchanged`. Szczegóły kroków: [cli](../cli.md#konfiguracja-maszyny-setup).

## Dodawanie reguł (`layers use`, `layers add`, `layers init`)

- `layers use <id> --match GLOB` (powtarzalne, wymagane) tworzy regułę; bez `--position` wstawia ją **przed** regułą `**`.
  `--position` liczone od 0 (indeks w `layer_rules`).
- `layers use` bez reguły `**` buduje regułę na `[core]` + jedyną skonfigurowaną warstwę personal; przy kilku warstwach
  personal wypisuje ostrzeżenie z instrukcją `layers use <personal> --match '**'`.
- `layers add` i `layers init` **nie tworzą reguły `**`**; `layers add` warstwy org albo personal wypisuje ostrzeżenie
  z instrukcją `layers use <id> --match <glob>`. Regułę `**` tworzy `layers migrate` albo `layers use <personal> --match '**'`.
- `layers add` warstwy core dopisuje ją na początek każdej istniejącej reguły bez core (`rules_updated` w wyniku i ostrzeżenie;
  config jest przepisywany bez komentarzy, kopia zapasowa zostaje). `layers init` w `next` podaje commit szkieletu przed
  `layers add`; `layers add` ze źródła bez commitów ostrzega o pustym repozytorium.
- Przy istniejących regułach nowa warstwa personal nie jest dopisywana do `layer_rules`, a `isolation.allowed_layers`
  nie jest poszerzane (ostrzeżenia).

## Scalanie plików (semantyka K2)

Źródło: spec A, tabela „Tabela semantyki scalania”. Późniejsza warstwa wygrywa, o ile wiersz nie mówi inaczej.

| Plik / klucz | Semantyka |
|---|---|
| `skills/<n>/**` | per plik, późniejsza wygrywa; skill z ≥2 dostawcami składany jako kompozyt |
| `instructions/<n>.md` | cały plik, późniejsza wygrywa; kolizja nazwy w dwóch warstwach = ostrzeżenie |
| `instructions/_base.md`, `workflow/schema.json`, `workflow/benchmark.yaml`, `hooks/*` | tylko `core` |
| `instructions/_base.d/*.md` | suma; patrz „_base.d” niżej |
| `agents/<n>.md` | cały plik, późniejsza wygrywa |
| `codex.yaml` | mapy scalane głęboko; listy wykluczeń skilli = suma |
| `workflow/defaults.yaml` | mapy scalane, listy zastępowane; `validation.checks[]` i `guardrails.rules[]` łączone po `id` (kolizja: późniejsza); `routing.rules[]` i `guardrails.checks[]` (bez `id`) dopisywane w kolejności `use` z deduplikacją po treści |
| `workflow/projects/*.yaml`, `workflow/trackers/*.yaml` | cały plik, późniejsza wygrywa |
| `workflow/skill-catalog.yaml` + `skill-catalog.d/<id>.yaml` | suma wpisów `skills` i `orgs`; kolizja klucza: późniejsza |
| `workflow/skill-profiles.yaml` + `.d/` | per profil (w tym `default`) suma list skilli z deduplikacją |
| `workflow/cause-keys.yaml` + `.d/` | suma kluczy i aliasów; kolizja aliasu (w jednej warstwie albo między warstwami) = błąd (`alias-collision`) |
| `workflow/lessons/index.yaml` + `.d/` | suma rekordów po `path`; pliki lekcji per ścieżka |
| `NOTICE`, `CHANGELOG`, `README`, `layer.yaml`, `leakguard/` | nie składane (własność warstwy) |

Wyjątek implementacyjny: `imiona.md` (skill `dispatch`) nie jest czytany przez kod; czyta go agent, a nakładka działa
per plik w kompozycie skilla.

## `layer.yaml`

Źródło: `layers.py` (walidacja schematu), spec A „Kontrakty”, fakty F-G09.

- Wymagane: `schema` (= `1`), `id` (`[a-z0-9][a-z0-9-]*`), `kind` (`core|org|personal`).
- Opcjonalne: `version` (SemVer), `core_min` (SemVer), `requires` (lista `id`), `identity` (ścieżka względna;
  domyślnie `leakguard/identity.yaml`, czytany także bez deklaracji — fail-closed).
- Nieznane klucze są ignorowane. Brak pliku = BLOCKED (`layer-yaml-missing`).
- `layers init` tworzy `version: 0.1.0` i `core_min` = wersja generatora (`__version__`).

## Wersje i `core_min` (K12)

- `core_min` porównywane z `__version__` generatora. `core_min` > wersja generatora = BLOCKED (`core-min-unmet`).
  `version` i `core_min` są wymagane: ich brak = BLOCKED (`version-missing`, `core-min-missing`; `check_layers`).
- Wersja `core` różna od `__version__` = ostrzeżenie `core-version-mismatch` (każda różnica).
- Podbicie MAJOR warstwy ze skillami wymaga wpisu „Breaking” w jej CHANGELOG. Tabela, kiedy podbić MAJOR/MINOR/PATCH:
  spec A, „SemVer (K12)”; użytkownik: [docs/layers-authoring.md](../layers-authoring.md).

## Kompozyt skilla

Źródło: spec A „Kompozyt skilla”, `layers.py`, plan Wynik (2.2a/2.2b).

- Skill dostarczany przez ≥2 warstwy (lub z dostawcą plus link w środku) jest składany w cache:
  `<cache>/composed/<skill>/<hash>` (katalog cache wg systemu; patrz [config-resolution](config-resolution.md)).
- `<hash>` = SHA-256 posortowanej listy `(ścieżka względna, id warstwy, realpath pliku źródłowego)`.
- Wpisy kompozytu to symlinki do plików źródłowych. W trybie kopii (`SKILLSYNC_NO_SYMLINK=1` albo `sync.method: copy`)
  są kopiami plików, a stan `.github/.skillsync-state.json` zapisuje `files: {plik: {owner, base}}`.
- **Reguła właściciela (K4):** plik należy do warstwy, z której został rozwiązany. Edycja przez symlink trafia do
  checkoutu właściciela. `push` zapisuje do właściciela.
- Nowy plik w kompozycie: przy odbudowie i przy `push` adoptowany do właściciela po `leakcheck`; gdy to niemożliwe,
  trafia do `<hash>.orphans/`, którego nic nie usuwa automatycznie, a `layers check` go wypisuje (kod `orphans`).
- Zwykły plik w miejscu symlinka (zapis atomowy edytora: plik tymczasowy + `rename`) = modyfikacja właściciela:
  adopcja po `leakcheck` albo `.orphans`, nigdy nadpisanie przez odbudowę.
- Nieużywane `<hash>` usuwa `link`; `.orphans` nigdy.
- Tryb kopii: stary stan `{klucz: hash drzewa}` jest jednorazowo konwertowany; pliki zgodne z bazą dostają bazę z bieżącego
  źródła, rozbieżne są oznaczane jako lokalne edycje i nigdy nie są nadpisywane.
- Gdy zapis `push` w kopii natrafia na plik o nieznanej bazie różny od pliku właściciela, daje `[conflict]` i wstrzymuje
  push projektu w całości (exit 2).

## Persona: `instructions/_base.d/` (K5)

Źródło: spec A K5 i „push i instrukcje”; `layers.py` (składanie); fakty F-G09 (kontrakt właściciela kodu).

- Źródło: `instructions/_base.d/*.md` w każdej aktywnej warstwie. Pliki sumowane w kolejności: warstwy z `use`
  (kolejność `use`), potem nazwa pliku.
- Fragmenty o prefiksie `00-` … `09-` idą **przed** tekstem `_base` rdzenia, pozostałe **po** nim. Całość leży przed
  znacznikiem `SKILLSYNC:BASE-END`.
- Każdy fragment jest w znacznikach `<!-- SKILLSYNC:FRAGMENT begin <warstwa>/<plik> -->` … `end`.
- `push` do `copilot-instructions.md`/`CLAUDE.md` dzieli tekst tymi znacznikami; zmiana bloku jest zapisywana do warstwy
  fragmentu (po `leakcheck`). Tekst bazowy rdzenia poza blokami zapisuje się tylko z `--to-layer <core>`.
- Bez fragmentu personal procedury dispatch działają z aliasami `karta-N`.
- Pliki ze zmiennymi `{{ … }}` (`_base`, fragment `_base.d`, szablon) przy `push` są pomijane z ostrzeżeniem.

## Zapis: `push`, `import`, `skills add`

- `--to-layer ID` (cel zapisu). Nie jest to wspólna flaga `--layer id=ścieżka` (ta zastępuje warstwy dla jednej komendy).
- Zapis do `core` wymaga `writable: true` w configu i `--to-layer <core>`; bez `--to-layer` plik jest pominięty z `[info]`.
- `import` i `skills add` mają domyślnie `personal`.
- Warstwa `writable: false` odmawia zapisu (exit 2), także z `--to-layer <core>`. Config z wpisem `synthetic` (0.9.x)
  blokuje `push`, `import` i `skills add` już przy wyborze warstw (`legacy-config`, instrukcja `layers migrate`).
- `push` w warstwie zapisywalnej na odłączonym HEAD (`ref` = tag lub commit) = `BLOCKED` z instrukcją `layers update --ref <gałąź>`.
- Przed zapisem `leakcheck --target <warstwa>`; wyciek = exit 1 bez zapisu.
- Każdy `[conflict]` w `push` = exit 2, projekt bez zapisu.

## Hooki i strażnik w warstwach zapisywalnych

- `layers add` i `layers init` instalują hooki `pre-commit` i `pre-push` w lokalnym katalogu hooków
  (`git rev-parse --git-path hooks`), jako zarządzany blok; istniejących hooków nie nadpisują.
- `core.hooksPath` wskazujący katalog śledzony w repo: odmowa z `[info]` i instrukcją ręcznego wpięcia.
- `layers check` = FAIL (`hook-missing`) przy zapisywalnej warstwie bez hooka. Warstwa zapisywalna poza gitem = `hook-missing`.
- Szczegóły strażnika: [leakguard](leakguard.md).

## Zakaz symlinków w repo warstwy (D73)

Źródło: spec A K-„Zakaz symlinków”; plan Wynik (D73 i doprecyzowanie).

- Repo warstwy (treść śledzona przez git) nie zawiera symlinków, junctions ani reparse points, także na ścieżce `subdir`
  i w `layer.yaml`. Każdy taki wpis daje `symlink-in-layer` (FAIL w `layers check`).
- Wszystkie odczyty generatora pomijają takie wpisy.
- Symlinki tworzone przez generator w projektach i w cache kompozytu są dozwolone.
- Dla `leakcheck` taki wpis daje BLOCKED (exit 2).

## Model zaufania warstw (`allow_override`)

Źródło: spec A „Kontrakty” (zaufanie), plan Wynik (2.1 review 1–4, 2.3).

- Warstwa `org` **nie nadpisuje** elementów `core` bez wpisu w `allow_override` (config maszyny). Chronione są: `checks`
  (`id`), adaptery trackerów, role, hooki, wszystkie klucze ról w `codex.yaml` (np. `sandbox_mode`) i istniejące pliki
  skilli rdzenia (np. `skills/<n>/SKILL.md`). Wpis `allow_override` obejmuje klucz i jego potomków, także przy zmianie typu;
  `id` elementu listy to nazwa atomowa.
- Kontrola porównuje wynik scalenia `[core, org]` z elementami core; identyczna kopia nie jest nadpisaniem.
- Nadpisanie bez zgody = `override-without-consent` (FAIL w `layers check`). `personal` może nadpisywać.
- Ryzyko rezydualne: `workflow/projects/<slug>.yaml` z warstwy `org` jest konfiguracją projektów tej organizacji i może
  zastępować listy, w tym `validation.checks`; model zaufania tego nie obejmuje.
- Warstwa jest kodem wykonywalnym (`validation.checks`, hooki): podpina się tylko źródła, którym się ufa.

## `isolation` i `layers check --isolation` (K10)

- PASS tylko gdy: (1) każdy `layers[].path` ma `layer.yaml` o `id` z `isolation.allowed_layers`; (2) w katalogu warstw
  systemu, w `legacy_library` i w znanych lokalizacjach legacy nie ma biblioteki o `id` spoza listy ani katalogu bez
  `layer.yaml`; (3) w drzewie nie ma pliku `identity` obcej warstwy.
- Bez `isolation.allowed_layers` wynik to **NOT_RUN** (exit 0).
- Wpis `synthetic` (config 0.9.x) daje FAIL `isolation-legacy-bridge`, także gdy katalog nie istnieje.
- `--project` i `--layer` z `--isolation` są ignorowane z ostrzeżeniem.
- Pełny tryb: `skillsync layers check --isolation`; `layers check` bez tej flagi sprawdza pozostałe kody.

## Migracja configu (`layers migrate`, K11)

Źródło: spec A „Migracja”, `layers.py` (`migrate_config`), plan Wynik (2.4 i odkrycia 2.4).

- Klucz `library:` **nie jest czytany**. Config z `library:` daje BLOCKED (exit 2) z instrukcją
  `skillsync layers migrate` (zob. [config-resolution](config-resolution.md)).
- `layers migrate` domyślnie **dry-run**; zapis tylko z `--apply`. Tworzy kopię zapasową configu obok pliku.
- Zachowuje `discover_paths`, `tags`, `variables`, `python_command`, `entrypoint` i inne klucze. Stary `library` zapisuje
  jako `legacy_library`, tylko do rozpoznawania zarządzanych symlinków.
- `--layer-source ID=PATH` (tylko ścieżki lokalne; URL = BLOCKED z instrukcją `layers add` po migracji), `--kind ID=KIND`,
  `--match ID=GLOB`.
- `--manifests` usuwa `sync.library` z lokalnych manifestów z kopią (`<dane>/backups/manifests/...`).
- `--finish` usuwa `legacy_library` po skasowaniu starego klona i PASS `layers check --isolation`; wymaga `allowed_layers`.
- Przy braku `isolation` `--apply` dopisuje `allowed_layers` ze wszystkimi warstwami (core, org, personal);
  `isolation` bez `allowed_layers` daje ostrzeżenie bez zmiany.
- Po migracji: `skillsync leakguard install-hook` dla każdej warstwy zapisywalnej (hooki nie są dopisywane automatycznie
  przy migracji configu).
- Przepisany YAML traci komentarze; zostają w kopii obok configu.
- Znane ograniczenie: `layers migrate` na configu z dwiema warstwami personal daje BLOCKED (`conflict`, „More than one personal layer”),
  także gdy config jest już zmigrowany. Spec zakłada jedną warstwę personal na użytkownika.

## Config 0.9.x z wpisem `synthetic`

Źródło: `layers.py` (`_check_bridge`, `parse_layer_flag`, `migrate_config`, `isolation_findings`).

- Config zapisany przez generator 0.9.x ma wpis `core` z `synthetic: {version, core_min}` i `writable: false`, bez `layer.yaml`.
  Generator go rozpoznaje, ale nie czyta jako warstwy:
  - `link`, `instructions`, `push`, `import`, `skills add|list`, `doctor`, `list` i `layers list|check|update` = BLOCKED
    (exit 2, kod `legacy-config`) z instrukcją
    `skillsync layers migrate --layer-source core=PATH --apply`;
  - `layers check --isolation` = FAIL, `isolation-legacy-bridge`.
- `layers migrate --layer-source core=<prawdziwy core>` zastępuje wpis: `layer_rules` i `isolation.allowed_layers` dostają
  id nowej warstwy core (D162). Warstwy org i personal dodane w tym samym wywołaniu wchodzą do reguł i do izolacji (D166).
  Config już na warstwach (bez wpisu 0.9.x) nie jest poszerzany (decyzja 2.4).
- `layers migrate` na configu z wpisem 0.9.x bez `--layer-source` rodzaju core (także z samymi warstwami org i personal)
  = BLOCKED `no-core`, nic nie jest zapisywane; dwa źródła core = BLOCKED `conflict`.
- Przy `--layer id=ścieżka,synthetic` odpowiedź to BLOCKED z kodem `synthetic-refused`.
- Flaga `--adopt-legacy` nie jest obsługiwana: BLOCKED (exit 2, kod `bad-argument`) z instrukcją `--layer-source core=PATH`.

## Flagi wspólne i ograniczenia `--library`

- `--library` nie jest przyjmowane przez żadną komendę, także `prototype *` (sekcja **Breaking** w CHANGELOG).
- `--layer id=ścieżka` (powtarzalna) zastępuje warstwy dla jednej komendy, do testów i diagnostyki. Własne procesy
  potomne `skillsync` (np. `quality` → `guard`) dostają warstwy jawnie w argv. Komendy projektu uruchamiane przez
  `validate`, `guard`, `smart-test --run` nie dostają żadnych informacji o warstwach.
- Komendy workflow bez czytania biblioteki (`status`, `plan`, `claim`, `dispatch`, `metrics`, `benchmark`, `map --check`)
  nie rozwiązują warstw; `--layer` jest dla nich przyjmowane i ignorowane.
- `sync.library` w manifeście jest ignorowane przez warstwy; `doctor` ostrzega, `layers migrate --manifests` usuwa.
- `layers` lub `layer_rules` w manifeście = błąd `doctor`.

## Komendy i kody wyjścia

Źródło: spec A „Komendy i kody wyjścia”; fakty F-G01, F-G14.

| Komenda | Zachowanie | Kody |
|---|---|---|
| `layers list\|check [--project P] [--isolation] [--json]` | efektywna lista, `requires`, `core_min`, hooki, README/CHANGELOG/wersja, kolizje, `.orphans` | PASS 0, NOT_RUN 0, FAIL 1, BLOCKED 2 |
| `layers init\|add\|use\|update\|migrate` | K9/K11 i „Aktualizacja warstw” | PASS 0, BLOCKED 2 |
| `link`, `instructions`, `push`, `pull`, `watch` | warstwy przez `LayerSet`; bramka `link` drukuje tylko `blocked`, `error` i noty doboru | 0 z ostrzeżeniami; BLOCKED 2 |

`layers list --json`: `{schema: 1, status, generator_version, layers: [{id, kind, path, subdir, version, writable, root, synthetic, present}], composed_root, origin, project, rule, notes}`
`layers check --json`: `{schema, status, layers: [{…pola warstwy, status}], findings: [{code, layer, message}]}`;
`status` per warstwa to najgorszy wynik jej ustaleń (`BLOCKED`, `FAIL`, `PASS`) albo `NOT_RUN` dla warstwy spoza maszyny.

`layers update [id] [--ref REF] [--json]` (bez `--isolation`): tag = `fetch --tags` + checkout; gałąź = `fetch` + `merge --ff-only`;
commit = checkout (odłączony HEAD); brak `ref` = `pull --ff-only`; brudne drzewo albo brak fast-forward = BLOCKED dla tej warstwy.
`--ref` zmienia `ref` w configu.

### Istotność kodów `layers check`

Źródło: fakty F-G14 (kody i poziomy w kodzie); spec A (tabela kodów).

| Poziom | Kody | Efekt |
|---|---|---|
| blocked | `core-missing`, `layer-yaml-invalid`, `layer-yaml-missing`, `id-mismatch`, `kind-mismatch`, `version-missing`, `core-min-missing`, `core-min-unmet`, `requires-unmet`, `requires-cycle` | `layers check` BLOCKED (2); `doctor` FAIL (1); `link` BLOCKED (2) |
| error | `symlink-in-layer`, `identity-invalid`, `case-collision`, `override-without-consent`, `merge-invalid`, `alias-collision` | `layers check` FAIL (1); `doctor` ostrzeżenie; `link` pokazuje `[warn]` |
| warning | `readme-missing`, `changelog-missing`, `identity-layer-mismatch`, `core-version-mismatch`, `hook-legacy`, `instruction-collision`, `core-not-first`, `personal-not-last`, `project-name-mismatch`, `core-only-ignored` | ostrzeżenie |
| info | `layer-absent`, `isolation-layer-absent` | informacja; `doctor` pomija |
| FAIL izolacji | `isolation-layer-not-allowed`, `isolation-legacy-bridge`, `isolation-layer-yaml-invalid`, `isolation-no-layer-yaml`, `isolation-foreign-library`, `isolation-unknown-checkout`, `isolation-legacy-library`, `isolation-foreign-identity` | tylko `layers check --isolation`: FAIL (1) |

Kody `merge-invalid` i `alias-collision` to błędy (`error`) w raporcie `layers check` (`merge_findings`, `merged_cause_keys`).
Przy odczycie uszkodzonego pliku scalania (`_load_mapping`, `merged_cause_keys`) ten sam kod zgłasza wyjątek i kończy komendę BLOCKED.
Kody `isolation-*` pochodzą z `isolation_findings`. Lista kodów jest wykazem z kodu na dzień opisu; nie jest kontraktem zamkniętym.

Poziom `error` w `doctor` = ostrzeżenie (nie FAIL). `doctor` mapuje `blocked` → error.

Kody `hook-missing` (error) i `orphans` (warning) są zgłaszane tylko przez `layers check`; `doctor` i `link` ich nie pokazują.
`hook-missing` daje FAIL (1) w `layers check`, a `orphans` jest ostrzeżeniem.

## Komendy `init`, `list`, `skills index`

- `skillsync init` = `layers init --kind personal`.
- `skillsync list` pokazuje skille z kolumną warstwy.
- `skillsync skills index` zapisuje indeks do `<cache>/skills-index/INDEX-<ids>.md` albo na stdout (`--stdout`); `--layer`
  zamiast `--library`. Indeks nie jest zapisywany do warstw. Komenda `--check` sprawdza plik w cache (`catalog.py`).

## Znane ograniczenia (stan 2026-11-05)

- Ścieżki warstw w cache kompozytu: `realpath` i `abspath` przy aliasach ścieżek warstwy mogą przepisywać linki między projektami
  (treść poprawna, brak idempotencji).
- `layers init` → `layers add --source <katalog>` wymaga commita w katalogu źródłowym; `add` ostrzega przy źródle bez commitów.
- `layers init` i `layers add` dopisują stub celu strażnika; pusty `remote_match` uzupełnia `layers add`.
- Wykrywanie wywołania `leakcheck` w hooku jest heurystyczne (linia niekomentarzowa z poleceniem); kontrola `hook-missing`
  jest bramką tylko przez sam hook i CI.
- Migawka `watch` może wchłonąć zmianę zrobioną w trakcie cyklu `link`; wyzwoli ją dopiero następna zmiana.
- Błąd IO w pętli zapisu `push` może zostawić zapis częściowy jednostki. Stan jest zgodny z rzeczywistością, następny
  `push` go dokończy; `OSError` daje exit 2.
- `push`, `import` i `skills add` także w trybie kopii tworzą `composed/.lock` i czekają na obcą blokadę `link`
  do `SKILLSYNC_LOCK_TIMEOUT`; potem BLOCKED.
- Katalog `.github/skills` zarządzany przez generator usuwa ręczne skille użytkownika (zachowanie dry-run: `Would remove obsolete:`).
- Placeholdery chmury (pliki „na żądanie”) i pliki deduplikacji mają atrybut reparse point i są odrzucane (zakaz symlinków);
  checkoutu warstwy nie trzyma się w folderze synchronizowanym „na żądanie”.
- Na Windows i macOS wynik `layers` i strażnika nie zależy od wielkości liter w ścieżce; na Linuksie zależy. Testy dla macOS i
  Windows są `NOT_RUN` do czasu ręcznego smoke (plan, K13).

Gdzie w kodzie: `skillsync_workflow/layers.py`: `match_rule`, `LayerSet`, `check_layers`, `_check_bridge`,
`migrate_config`, `parse_layer_flag`, `isolation_findings`; `skillsync` (wspólna flaga `--layer`, `LayerWriter.refusal`).
