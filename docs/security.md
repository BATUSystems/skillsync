# Bezpieczeństwo i izolacja

Skillsync zakłada, że jedna osoba pracuje dla kilku organizacji. Treść jednej organizacji nie może trafić do repo
innej, a prywatne nawyki użytkownika nie mogą trafić do repo publicznego.

## Model

| Mechanizm | Chroni przed |
|---|---|
| dobór warstw po ścieżce projektu (config maszyny) | podpięciem obcej warstwy przez manifest w repo |
| `isolation.allowed_layers` + `layers check --isolation` | obecnością cudzej warstwy na maszynie organizacji |
| model zaufania warstw | nadpisaniem kontroli, ról i hooków rdzenia przez warstwę org bez zgody |
| `leakcheck` + hooki git + CI | wyciekiem nazw, ścieżek, tożsamości i metadanych commitów do repo docelowego |
| repo warstwy bez symlinków i reparse points | treścią spoza repo warstwy podpiętą linkiem i ominięciem strażnika |

## `leakcheck`

```bash
skillsync leakcheck <katalog|projekt> [--target ID] [--worktree|--staged|--range A..B|--history] [--json]
```

Sprawdza treść plików, nazwy plików i metadane commitów (autor, e-mail). Kody: PASS 0, FAIL 1, BLOCKED 2.

Źródła wzorców (w tej kolejności): pliki tożsamości innych warstw na maszynie, `.leakguard/patterns.yaml` w repo docelowym,
wzorce wbudowane (np. ścieżki domowe). Wynik ma poziom pokrycia: `full`, `partial`, `generic`.

Raport zawsze podaje pełną ścieżkę pliku z trafieniem, także w CI.

### Wzorce

Wzorce wbudowane są zasobem generatora (`skillsync_workflow/data/leakguard-patterns.yaml`) i zawierają wyłącznie część
ogólną: działają w CI publicznego repo bez żadnej listy lokalnej. Scaloną listę (wbudowane i dodatki repo docelowego) wypisuje:

```bash
skillsync leakguard patterns --json
```

Z tej samej listy korzysta generator strony dokumentacji (`docs/site/build.py`), więc strona i `leakcheck` blokują to samo.

Format pliku (`schema: 1`, ten sam dla zasobu generatora i `.leakguard/patterns.yaml`):

| Pole | Znaczenie |
|---|---|
| `id` | unikalny identyfikator wpisu |
| `category` | rodzaj wzorca |
| `regex` | wyrażenie w składni Pythona `re`, dopasowywane do każdej linii osobno |
| `flags` | `ignorecase`, `multiline` |
| `applies_to` | powierzchnie: treść, nazwy plików, commity |
| `path_classes` | klasy ścieżek, w których wzorzec obowiązuje |
| `paths` | zawężenie do ścieżek (globy) |

Sekcja `allow` wyłącza trafienie tylko wtedy, gdy dopasowanie wzorca mieści się w całości w dopasowaniu wyjątku.

Kategorie wzorców (`category`): `d27`, `owner-path`, `email`, `machine`, `publisher`, `custom`. Powierzchnie (`applies_to`):
`content`, `filename`, `commit`. Klasy ścieżek (`path_classes`): `instrukcje`, `atrybucje`, `inne`. Format `identity.yaml`
opisuje [layers-authoring](layers-authoring.md).

Poziomy pokrycia (`coverage`): `full` (każdy inny cel niepubliczny ma warstwę z plikiem tożsamości), `partial` (część celów),
`generic` (brak tożsamości albo nieznany cel).

Przykład dodatku w `.leakguard/patterns.yaml` repo docelowego (nazwa hosta wewnętrznego i wyjątek dla przykładu
w dokumentacji):

```yaml
schema: 1
patterns:
  - id: host-wewnetrzny
    category: custom
    regex: '\bbuild-[0-9]+\.intranet\.example\b'
    flags: [ignorecase]
    applies_to: [content, filename, commit]
allow:
  - id: host-w-przykladzie-dokumentacji
    category: custom
    regex: 'build-0\.intranet\.example'
    paths: ['docs/**']
```

## Warstwy bez symlinków

Repo warstwy nie może zawierać symlinków, junctions ani reparse points, także na ścieżce do `subdir` warstwy oraz
w miejscu `layer.yaml` i `.leakguard/patterns.yaml`. `skillsync layers check` zgłasza wtedy błąd `symlink-in-layer`,
a `leakcheck` kończy się BLOCKED. Symlinki, które generator sam tworzy w projektach i w cache, są dozwolone.
Ograniczenia (foldery synchronizowane „na żądanie”): [layers-authoring.md](layers-authoring.md), sekcja „Bez symlinków”.

## Hooki git

```bash
skillsync leakguard install-hook <repo>
```

`pre-commit` skanuje zmiany staged, `pre-push` zakres wypychanych commitów. Istniejące hooki zostają; Skillsync dopisuje zarządzany blok.

## CI

Przykład GitHub Actions: zadanie `leakcheck` w `.github/workflows/ci.yml` tego repo. Skanuje drzewo robocze i commity
PR-a bez configu maszyny, z wzorcami celu z gałęzi bazowej:

```bash
python3 skillsync leakcheck --no-config --worktree --target-patterns "$RUNNER_TEMP/base-patterns.yaml" --json .
python3 skillsync leakcheck --no-config --range "${BASE_SHA}..HEAD" --target-patterns "$RUNNER_TEMP/base-patterns.yaml" --json .
```

W innym systemie CI (np. Azure Pipelines) uruchom te same dwa polecenia; kod wyjścia 1 albo 2 zatrzymuje potok.

## Autor commitów

Ustaw `git config user.email` w każdym repo warstwy na adres właściwy dla jej organizacji; `leakcheck --history`
dopuszcza tylko adresy z listy celu.

## Zgłaszanie podatności

Zobacz [SECURITY.md](../SECURITY.md).
