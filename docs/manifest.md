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
| `hooks.guard_delete` | `deny` \| `off` (też `true` = `deny`, `false` = `off`) | hook PreToolUse na `Bash` odmawiający trwałego usunięcia (`rm -r`, `git clean -f`, `git worktree remove --force`, …) z gotową komendą kosza; brak klucza: `deny` w projektach ze skillem `feature`, `agentflow` albo `dispatch`, `off` w pozostałych; `off` wyłącza tylko część usuwania: wpis i skrypt zostają zawsze, bo niosą niewyłączalny zakaz stopek i podpisów narzędzi AI (`--attribution on`, bez klucza manifestu); błędna wartość = `off` i ostrzeżenie; hak istnieje tylko dla Claude Code (przy wyłączonym `claude` brak haka); `doctor` raportuje stan (nieobecny lub bez danych zakazu atrybucji = FAIL, nieaktualny = ostrzeżenie, aktywny = info) |
| `hooks.guard_tests` | `warn` \| `off` (też `true` = `warn`, `false` = `off`) | ta sama część PreToolUse **ostrzega** (nie blokuje) o szerokim biegu testów poza `skillsync test-run` (`unittest discover`, `pytest` bez pliku, `dotnet test` bez `--filter`, `cargo test` bez filtra, `ng test`, `npm test` i inne z tabeli narzędzi); ostrzeżenie trafia do kontekstu modelu razem z wynikiem polecenia, czyli po jego wykonaniu; brak klucza: `warn` w projektach ze skillem `feature`/`agentflow`/`dispatch`, `off` w pozostałych; błędna wartość = `off` i ostrzeżenie w `link`/`doctor`; `link` kopiuje `guard-tests.py` i `test-tools.json` obok hooka |
| `hooks.test_wrappers` | lista napisów | prefiksy polecenia, które liczą się jak opakowanie (ostrzeżenie ich nie obejmuje), np. `make test`; wbudowane: `skillsync test-run`, `skillsync validate`/`smart-test`, `flock … systemd-run …` |
| `hooks.wide_modules` | liczba całkowita ≥ 2 | od ilu modułów `unittest` bieg liczy się jako szeroki (domyślnie 25) |
| `hooks.delete_allow` | lista napisów | dodatkowe cele wolne od blokady: nazwa katalogu (np. `coverage`), ścieżka względna od korzenia projektu (`out/cache`) albo bezwzględna (jak katalog tymczasowy); domyślnie `node_modules`, `.venv`, `__pycache__`, `dist`, `build` i katalogi tymczasowe |
| `hooks.stop_tests` | bool | kopiuje `check-tests.py` i dopisuje hook Stop; tylko projekty **bez** `feature`, wymaga `tests.command` |
| `tests.command` | string | komenda dla hooka Stop i fallback `legacy-tests` dla `validate` i `smart-test` |
| `claude` | `false` \| `{enabled: false}` | wyłącza `CLAUDE.md` i wszystko w `.claude/`, w tym `.mcp.json` |
| `mcp` | `false` \| `{enabled: false}` | wyłącza tylko `.mcp.json` |
| `codex` | `true` \| `false` \| mapa | domyślnie włączony przy `feature`; mapa: `enabled`, `max_concurrent_threads_per_session` (int ≥ 1), `exclude_skills`, `agent_overrides` |
| `cursor` | mapa | `rules.base`, `rules.project` (nazwy szablonów), `skills` (bool, domyślnie `true`); pusta sekcja = bez Cursora |
| `vscode.link_on_open` | bool | `false` wyłącza dopisywanie taska `skillsync: link` |
| `workflow` | mapa | nadpisania ustawień workflow, patrz sekcja „Scalanie ustawień workflow” niżej |
| `workflow.ceremony` | `full` \| `demo` | ceremonia skilla `feature`; brak klucza = `full`; wartości tylko małymi literami, inna wartość (także pusta, `null`, nie napis) = błąd `doctor`. Czytany wyłącznie z manifestu projektu: w `workflow/defaults.yaml` i `workflow/projects/<slug>.yaml` jest ignorowany. `route` zwraca `ceremony {value, source: manifest\|default\|invalid[, error]}` |
| `workflow.specs` | mapa `{tracked: bool, archive: string}` (oba klucze opcjonalne) | przechowywanie speców i planów. `tracked: false` = spec i plan nie są śledzone przez git (tryb nieśledzony); bez klucza tryb wykrywa reguła `/docs/specs/` i `/docs/plans/` w którymkolwiek `.gitignore` drzewa repozytorium (także zagnieżdżonym). `archive` = opcjonalne archiwum speców poza repozytorium, `~/` albo ścieżka bezwzględna (bezwzględna daje ostrzeżenie), bez `..`. `tracked: true` przy regule obu katalogów = błąd (`source: invalid`). Czytany wyłącznie z manifestu projektu. `route` zwraca `specs {tracked, archive, source: manifest\|gitignore\|default\|invalid[, error]}`; szczegóły: [spec-store](../library/skills/feature/references/spec-store.md) |
| `dispatch` | mapa | konfiguracja skilla `dispatch`; walidowana, bez wpływu na artefakty; szczegóły: [workflow](workflow.md) |
| `prototype` | mapa | konfiguracja komendy `prototype`; szczegóły: [prototypes](prototypes.md) |
| `dashboard` | mapa `local_only`, `read_bind`, `approve_bind` | nasłuch `dashboard serve` tego projektu; szczegóły niżej (sekcja „Sekcja `dashboard`”) |
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
w sensie provider i CI, `skill_profiles`, `guardrails`, `knowledge`): [workflow](workflow.md). `knowledge.domain` i `knowledge.lessons`
to ciąg albo lista ciągów (produkt z kilku repozytoriów): [workflow](workflow.md), „Katalogi wiedzy: ciąg albo lista”.
Wyjątek od scalania: `workflow.ceremony` (`full` | `demo`) i `workflow.specs` czyta się tylko z manifestu projektu, wcześniejsze poziomy go ignorują.

## Pliki generowane

Pliki, które zapisuje `skillsync link`, są generowane i nie edytuje się ich ręcznie. Ich listę per host agenta
(Copilot, Claude Code, Codex, Cursor, VS Code) prowadzi [architecture](architecture.md), sekcja „Artefakty per host”.
Manifest sam jest wpisany do `.github/.gitignore` (nie jest wersjonowany, chyba że był już śledzony).

## Sekcja `dashboard`

Opcjonalna. Wpływa tylko na `dashboard serve` tego projektu. Ta sama sekcja może być w configu maszyny
([configuration](configuration.md#pozostałe-klucze)); pierwszeństwo: flaga CLI > manifest > config maszyny > domyślne. Manifest może tylko ZAWĘŻAĆ (adres loopback albo `local_only: true`): szerszy adres w manifeście sklonowanego repozytorium kończy `serve` BLOCKED i jest błędem `doctor`; poszerza flaga albo config maszyny. Uszkodzony config maszyny też blokuje `serve` (ustawienie zawężające nie może zniknąć po cichu).

```yaml
dashboard:
  local_only: true        # opcjonalnie: true = oba gniazda na 127.0.0.1; false = brak preferencji
  read_bind: 127.0.0.1      # opcjonalnie: adres nasłuchu gniazda odczytu
  approve_bind: 127.0.0.1 # opcjonalnie: adres nasłuchu gniazda zgód
```

- `read_bind`, `approve_bind`: literał IP, `localhost`, `0.0.0.0` albo `::`. Jawny bind wygrywa ze skrótem `local_only`
  w tym samym źródle.
- Brak sekcji = domyślne: odczyt na `0.0.0.0`, zgody na `127.0.0.1` (tryb dwóch gniazd, zob. [cli](cli.md)).
- Nieznany klucz: `doctor` ostrzega, a `dashboard serve` kończy się BLOCKED z nazwą klucza. Zła wartość: błąd z nazwą klucza,
  bez wartości w komunikacie.

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
  dark_theme: true                      # opcjonalnie; false = prototypy bez ciemnego motywu
  public_url: https://proto.example.org # opcjonalnie
  port: 5130                            # opcjonalnie
```

Ścieżki są względne do katalogu projektu. Ścieżka bezwzględna, `..` wychodzące z repo i dowiązanie na zewnątrz są błędem.

`dark_theme` (bool, domyślnie `true`): `false` wyłącza ciemny motyw prototypów i ukrywa przycisk „Motyw” w pasku silnika (strona zawsze jasna, także przy ciemnym systemie i zapamiętanym wyborze). `init` i `migrate` zapisują na znaczniku `<html>` atrybuty `data-theme="light" data-proto-dark="off"` (poza `<head>` i ekranami, więc hashe ekranów i akceptacje zostają ważne); istniejące prototypy dostają to przez `skillsync prototype migrate`, a `sync --check` ostrzega, dopóki go nie wykonasz. Wartość inna niż `true`/`false` to błąd walidacji (`doctor`).

`port` (liczba całkowita 1024-65535) to port serwera prototypów tego projektu: domyślny port `prototype serve` (flaga `--port` wygrywa; bez klucza 4445), port sprawdzany przez stronę statusu (`dashboard serve --prototype-port` wygrywa; bez klucza 5130) i domyślny adres `prototype question url`. Dzięki jednemu kluczowi serwer i strona statusu zgadzają się co do portu; `doctor` zgłasza błędną wartość. `public_url` to adres, pod którym eksperci otwierają serwer prototypów (np. koniec tunelu ssh albo odwrotne proxy): http(s), bez danych logowania, zapytania i fragmentu; końcowy `/` jest obcinany. Zmienna środowiskowa `SKILLSYNC_PROTOTYPE_PUBLIC_URL` ma pierwszeństwo przed manifestem. Adres trafia do `prototype question url`, pola `url` w `prototype question add` (także gdy serwer nie działa lokalnie) i dodatkowej linii „Adres publiczny” komunikatu `prototype serve`; `prototype link` zostaje lokalny. Błędna wartość w manifeście to błąd walidacji (`doctor`), a w zmiennej BLOCKED `invalid_public_url`.

## Klucz `tests`

`tests.command` (komenda testów projektu) i `tests.mask` (domyślnie wyłączone; maskuje każda wartość inna niż jawne wyłączenie: brak klucza, `false`, `null`, `0`, napis pusty albo `off`/`false`/`no`/`0` bez względu na wielkość liter i spacje; literówka maskuje, bo błąd ma nie ujawniać wyników): `true` maskuje wyniki testów (nazwy, grupy, komunikaty porażek) przy zapisie zdarzeń `skillsync test-run` i w odpowiedzi strony statusu; domyślnie wyniki są zapisywane i pokazywane bez maski (zob. `docs/security.md`, „Wyniki testów na stronie statusu”).
