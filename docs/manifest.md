# Manifest projektu

Każdy projekt ma plik `.github/.skillsync.yaml`. Manifest wybiera skille, profile, kontrole i ustawienia workflow
**tego projektu**. Nie wskazuje warstw ani organizacji: o tym decyduje [config maszyny](configuration.md), a dobór
warstw idzie po ścieżce projektu, nie po nazwie.

## Minimalny manifest

```yaml
version: 1
project: {name: Org/Projekt, type: local}
skills: [feature]
instructions: {merge: [_base], template: Org-Projekt}   # plik instructions/Org-Projekt.md w warstwie
sync: {method: symlink}
tests: {command: "make test"}
```

Po zapisaniu manifestu: `skillsync link <projekt> --dry-run`, potem `skillsync link <projekt>`, potem
`skillsync doctor <projekt>`.

## Klucze

| Klucz | Typ / domyślnie | Skutek |
|---|---|---|
| `version` | `1` | jedyna obsługiwana wersja |
| `project.name` | string | wybiera plik `workflow/projects/<name z / → ->.yaml`; nie wpływa na dobór warstw |
| `skills` | lista | skille: nazwa, `@tag`, wildcard `*`/`?`; patrz sekcja „Specyfikacja `skills`” niżej |
| `instructions.merge` | lista nazw | części wspólne scalane przed szablonem (zwykle `[_base]`) |
| `instructions.template` | nazwa `instructions/<nazwa>.md` | szablon instrukcji projektu; bez niego nie powstaje `.github/copilot-instructions.md` i `push` nie działa |
| `instructions.variables` | mapa | zmienne szablonów; nadpisują `variables` z configu |
| `instructions.entrypoint` | `inline` (domyślnie) \| `agents` | tryb wejścia instrukcji; `inline` pełna treść w `CLAUDE.md`, `agents` treść w `AGENTS.md`, a `CLAUDE.md` importuje ją |
| `sync.method` | `symlink` (domyślnie) \| `copy` | `copy` wymusza kopie ze śledzeniem stanu |
| `sync.library` | — | **ignorowane** z ostrzeżeniem; dobór warstw jest tylko w configu maszyny |
| `agents` | lista nazw ról | zawęża role dla obu hostów; pusta lista = brak ról; brak klucza przy `feature` = wszystkie role z `codex.yaml` |
| `hooks.stop_closure` | bool | projekty z `feature`: kopiuje `check-closure.py` i dopisuje hook Stop; `false` go usuwa |
| `hooks.stop_tests` | bool | kopiuje `check-tests.py` i dopisuje hook Stop; tylko projekty **bez** `feature`, wymaga `tests.command` |
| `tests.command` | string | komenda dla hooka Stop i fallback `legacy-tests` dla `validate` i `smart-test` |
| `claude` | `false` \| `{enabled: false}` | wyłącza `CLAUDE.md` i wszystko w `.claude/`, w tym `.mcp.json` |
| `mcp` | `false` \| `{enabled: false}` | wyłącza tylko `.mcp.json` |
| `codex` | `true` \| `false` \| mapa | domyślnie włączony przy `feature`; mapa: `enabled`, `max_concurrent_threads_per_session` (int ≥ 1), `exclude_skills`, `agent_overrides` |
| `cursor` | mapa | `rules.base`, `rules.project` (nazwy szablonów), `skills` (bool, domyślnie `true`); pusta sekcja = bez Cursora |
| `vscode.link_on_open` | bool | `false` wyłącza dopisywanie taska `skillsync: link` |
| `workflow` | mapa | nadpisania ustawień workflow, patrz sekcja „Scalanie ustawień workflow” niżej |
| `workflow.ceremony` | `full` \| `demo` | ceremonia skilla `feature`; brak klucza = `full`; wartości tylko małymi literami, inna wartość (także pusta, `null`, nie napis) = błąd `doctor`. Czytany wyłącznie z manifestu projektu: w `workflow/defaults.yaml` i `workflow/projects/<slug>.yaml` jest ignorowany. `route` zwraca `ceremony {value, source: manifest\|default\|invalid[, error]}` |
| `dispatch` | mapa | konfiguracja skilla `dispatch`; walidowana, bez wpływu na artefakty; szczegóły: [workflow](workflow.md) |
| `prototype` | mapa | konfiguracja komendy `prototype`; szczegóły: [prototypes](prototypes.md) |
| `delivery` | `{mode: pr \| direct, base: <gałąź>}` | sposób dostarczania zmian przez agenta; brak `delivery.mode` = `pr` z ostrzeżeniem `doctor`; `mode: direct` bez `base` = ostrzeżenie; zła wartość lub typ = błąd; nieznany klucz = ostrzeżenie; nie zmienia artefaktów `link`. Inne niż `workflow.delivery` (provider i CI) |

### Specyfikacja `skills`

- `nazwa` to katalog w `skills/` warstwy (`core`, `org` albo `personal`). Warstwy składają się w jedną listę dostępnych skilli.
- `@tag` to grupa z `tags` w configu maszyny. Tagi nie pochodzą z warstw.
- `wildcard` (np. `web-a11y-*`) dopasowuje wszystkie pasujące skille.
- Alias `agentflow` oznacza `feature` (gdy `feature` jest dostępny).
- `feature` (lub `agentflow`) automatycznie dołącza towarzyszy: `workflow`, `pre-implement`, `smart-test`, `bugfix`,
  `codereview` (tylko te, które istnieją w warstwach).
- Profile (`workflow.skill_profiles.selected`, domyślnie `core` i `testing`) **dodają** skille z `workflow/skill-profiles.yaml`.
  `selected: []` jest świadomym wyłączeniem profili. Wynik rozwijania jest wspólny dla wszystkich hostów.

Manifest nie wybiera warstw: klucze `layers` i `layer_rules` w manifeście daje błąd `doctor`. Klucz `sync.library` w manifeście
jest ignorowany, a `doctor` zgłasza to ostrzeżeniem. `skillsync layers migrate --manifests` usuwa ten klucz z manifestów (z kopią).

## Scalanie ustawień workflow

Ustawienia workflow składają się z trzech poziomów (to nie są warstwy w rozumieniu [configuration](configuration.md)): `workflow/defaults.yaml` z warstw, potem
`workflow/projects/<projekt>.yaml` z warstw, potem sekcja `workflow:` manifestu. Mapy scalają się rekurencyjnie,
**listy zastępuje się w całości**. Reguły scalania per plik i klucz, także dla `workflow/*`: [architecture](architecture.md).
Opis kluczy `workflow:` (`validation`, `testing`, `readiness`, `review`, `environment`, `e2e`, `doctor`, `delivery`
w sensie provider i CI, `skill_profiles`, `guardrails`, `knowledge`): [workflow](workflow.md).
Wyjątek od scalania: `workflow.ceremony` (`full` | `demo`) czyta się tylko z manifestu projektu, wcześniejsze poziomy go ignorują.

## Pliki generowane

Pliki, które zapisuje `skillsync link`, są generowane i nie edytuje się ich ręcznie. Ich listę per host agenta
(Copilot, Claude Code, Codex, Cursor, VS Code) prowadzi [architecture](architecture.md), sekcja „Artefakty per host”.
Manifest sam jest wpisany do `.github/.gitignore` (nie jest wersjonowany, chyba że był już śledzony).

## Sekcja `prototype`

Opcjonalna. Wpływa tylko na komendę `prototype`; szczegóły reguł i walidacji: [prototypes](prototypes.md).

```yaml
prototype:
  root: docs/prototypes                 # domyślnie
  theme:
    tokens_from: src/styles.css         # opcjonalnie
    css: docs/design/prototype.css      # opcjonalnie
  guidelines: docs/design/prototype.md  # opcjonalnie
  commit_before_iteration: false        # opcjonalnie
```

Ścieżki są względne do katalogu projektu. Ścieżka bezwzględna, `..` wychodzące z repo i dowiązanie na zewnątrz są błędem.
