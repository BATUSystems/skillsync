# Konfiguracja maszyny

Config maszyny mówi, **jakie warstwy masz** i **która warstwa obsługuje który projekt**.
Repo projektu nigdy nie wskazuje organizacji: o doborze decyduje ścieżka projektu na Twojej maszynie.
Konfigurację projektu (skille, profile, kontrole) opisuje [manifest](manifest.md). Model warstw: [layers-authoring](layers-authoring.md).

## Gdzie leży plik

| System | Ścieżka domyślna |
|---|---|
| Linux, macOS, Windows | `~/.config/skillsync/config.yaml` |

Inną ścieżkę wskażesz zmienną `SKILLSYNC_CONFIG`.

Brak pliku configu jest błędem (exit 2).

## Zmienne środowiskowe

| Zmienna | Skutek |
|---|---|
| `SKILLSYNC_CONFIG` | ścieżka pliku configu maszyny zamiast domyślnej |
| `SKILLSYNC_NO_SYMLINK` | `1`, `true`, `yes`, `on` (bez względu na wielkość liter) wymuszają tryb kopii zamiast symlinków; `0`, `false`, `no`, `off` albo pusta wartość nie; inna wartość daje ostrzeżenie i włącza tryb kopii (`sync.method: copy` w manifeście działa tak samo) |
| `SKILLSYNC_LOCK_TIMEOUT` | ile sekund czekać na blokadę cache kompozytów skilli zajętą przez inny proces `skillsync` (domyślnie 60); po przekroczeniu BLOCKED (2) |

Warstw nie przekazuje się zmiennymi środowiskowymi. Procesy potomne `skillsync` dostają je jawnie w argumentach
(`--layer ID=PATH`), a komendy projektu uruchamiane przez `validate`, `guard` i `smart-test --run` nie dostają żadnych informacji o warstwach.

## Warstwy (`layers`)

Trzy rodzaje: `core` (dokładnie jedna, publiczny rdzeń), `org` (wiedza organizacji, może być kilka), `personal` (Twoja,
najwyżej jedna na projekt).

```yaml
layers:
  - id: core
    kind: core
    source: https://github.com/BATUSystems/skillsync
    subdir: library
    ref: vX.Y.Z
  - id: acme
    kind: org
    source: git@example.com:acme/skillsync-acme.git
  - id: moja
    kind: personal
    source: ~/Repos/moje-skillsync
```

| Pole | Znaczenie | Domyślnie |
|---|---|---|
| `id` | identyfikator warstwy, używany w `layer_rules` | — |
| `kind` | `core`, `org` albo `personal` | — |
| `source` | URL repo albo ścieżka lokalna | — |
| `subdir` | katalog z układem warstwy w repo | korzeń repo |
| `ref` | tag, gałąź, commit albo brak | brak |
| `path` | miejsce checkoutu | patrz tabela niżej |
| `writable` | czy `push` może zapisywać do warstwy | `core`: nie; `org` i `personal`: tak |

Domyślny katalog checkoutu warstwy (`layers add` bez `--path`):

| System | Katalog |
|---|---|
| Linux | `$XDG_DATA_HOME/skillsync/layers/<id>`, a gdy zmienna nie jest ustawiona: `~/.local/share/skillsync/layers/<id>` |
| macOS | `~/Library/Application Support/skillsync/layers/<id>` |
| Windows | `%LOCALAPPDATA%\skillsync\layers\<id>` |

## Katalogi danych i cache

| Rodzaj | Linux | macOS | Windows |
|---|---|---|---|
| dane (checkouty warstw, kopie) | `$XDG_DATA_HOME/skillsync` albo `~/.local/share/skillsync` | `~/Library/Application Support/skillsync` | `%LOCALAPPDATA%\skillsync` |
| cache kompozytów skilli | `$XDG_CACHE_HOME/skillsync/composed` albo `~/.cache/skillsync/composed` | `~/Library/Caches/skillsync/composed` | `%LOCALAPPDATA%\skillsync\cache\composed` |
| cache zależności skilla `ui-verify` (`<hash>` z plików skilla) | `$XDG_CACHE_HOME/skillsync/ui-verify/<hash>` albo `~/.cache/skillsync/ui-verify/<hash>` | `~/Library/Caches/skillsync/ui-verify/<hash>` | `%LOCALAPPDATA%\skillsync\cache\ui-verify\<hash>` |

Kopie zapasowe:

- manifesty projektów: `<dane>/backups/manifests/<RRRRMMDD-GGMMSS>[-N]/<zakodowana ścieżka>/.skillsync.yaml`;
- config: `config.yaml.bak-<znacznik czasu>` obok pliku configu.

Generator nie zapisuje logów maszyny. Logi walidacji projektu leżą w `.skillsync/logs/<id>/` w projekcie.

Klucz `writable` nie trzeba podawać, gdy domyślne wartości są właściwe. Hooki `pre-commit` i `pre-push` instalują
`layers add` i `layers init` w checkoutach warstw zapisywalnych.

## Zmienne ui-verify

Skill `ui-verify` (`library/skills/ui-verify/`, skrypty Node) czyta te zmienne środowiska. Brak zmiennych nie blokuje
skilla: domyślne ścieżki działają, a brak przeglądarki daje kod 3.

| Zmienna | Skutek |
|---|---|
| `UI_VERIFY_CACHE_DIR` | katalog główny cache zależności; pod nim leży katalog `<hash>` |
| `PLAYWRIGHT_MODULE_PATH` | ścieżka paczki `playwright-core` albo `node_modules`, który ją zawiera (ma pierwszeństwo) |
| `UI_VERIFY_CHROME` | ścieżka pliku Chrome lub Chromium (pierwsza w kolejności wykrywania) |
| `CHROME_EXECUTABLE_PATH` | ścieżka Chrome, gdy `UI_VERIFY_CHROME` nie jest ustawione |
| `UI_VERIFY_NO_SANDBOX` | `1` wyłącza sandbox Chrome (tylko kontenery CI) |
| `UI_VERIFY_SECRET_ENV` | nazwy zmiennych po przecinku, których wartości są maskowane w wyjściach |

Zależności są przypięte w `package.json` i `package-lock.json` skilla i instalowane poza repo projektu.
Przeglądarki skill nie pobiera. Szczegóły: `library/skills/ui-verify/SKILL.md`.

## Dobór warstw do projektu (`layer_rules`)

Pierwsza pasująca reguła wygrywa. Dopasowanie idzie **wyłącznie po rozwiązanej ścieżce projektu** (`realpath`);
`project.name` z manifestu nie bierze udziału w doborze. Rozbieżność nazwy i ścieżki to ostrzeżenie.

```yaml
layer_rules:
  - match: ["~/Repos/Acme/**"]
    use: [core, acme, moja]
  - match: ["**"]
    use: [core, moja]
```

- `use` to kolejność rosnącego pierwszeństwa: `core` pierwsza, `personal` ostatnia (inaczej ostrzeżenie).
- Zasady globów: `~` rozwijane; `*` obejmuje jeden segment; `?` jeden znak; `**` dowolną liczbę segmentów; wiodące `**/`
  dopasowuje dowolny prefiks; `[...]` nie jest klasą znaków (jest dosłowne); glob względny pasuje gdziekolwiek. Ścieżki są
  porównywane po `realpath`.
- Na Windows i macOS porównanie ignoruje wielkość liter. Na Windows separator `\` jest zamieniany na `/`, a litery dysków
  są małe po normalizacji. Na Linuksie wielkość liter ma znaczenie.
- Reguła za `**` nigdy nie zadziała: `layers check` zgłasza ostrzeżenie „never matches”.
- `layers use` bez `--position` wstawia nową regułę przed regułą `**`. Gdy reguła o dokładnie tym samym `match` już istnieje, `layers use` dopisuje warstwę do jej `use` (kolejność core, org, personal; warstwa personal zastępuje personal; przy kilku takich regułach zmienia pierwszą, bo ona obowiązuje) zamiast tworzyć drugą, która nigdy by nie zadziałała; z `--position` odmawia (BLOCKED). Warstwa już obecna w `use` tej reguły zostaje tam, gdzie stoi (PASS bez zmiany): kolejność decyduje, która warstwa wygrywa. Lista `use: [a, b]` (zapis, jaki robi `layers use`) jest zmieniana w tekście: reszta pliku, także komentarze poza listą (np. na końcu linii), zostaje bez zmian; komentarze wewnątrz listy rozbitej na linie giną, a kotwica albo tag przed listą, lista blokowa i inne nietypowe układy powodują przepisanie całego pliku bez komentarzy. Plik z końcami linii CRLF ma po edycji LF (oryginał zostaje w kopii `.bak-*`). `layers check` i `doctor` ostrzegają (`rule-unreachable`), że późniejsza reguła o tym samym `match` nigdy nie zadziała (ostrzega tylko projekt, który trafia w pierwszą z tych reguł; projekt pasujący do innej reguły go nie zobaczy).
- Worktree git dopasowuje się po ścieżce głównego repo, a gdy reguły brak, po własnej ścieżce.
- Brak pasującej reguły = BLOCKED (2) z instrukcją `skillsync layers use`.
- Warstwa z `use`, której nie ma na maszynie, jest pomijana z informacją; `layers check` daje wtedy NOT_RUN.
- `use` może zawierać wyłącznie `id` zdefiniowane w `layers`; inaczej błąd.

Przykład z dwiema organizacjami na rozłącznych katalogach:

```yaml
layers:
  - {id: core, kind: core, source: https://github.com/BATUSystems/skillsync, ref: vX.Y.Z}
  - {id: acme, kind: org, source: git@example.com:acme/skillsync-acme.git}
  - {id: globex, kind: org, source: git@example.com:globex/skillsync-globex.git}
layer_rules:
  - match: ["~/Repos/Acme/**"]
    use: [core, acme]
  - match: ["~/Repos/Globex/**"]
    use: [core, globex]
  - match: ["**"]
    use: [core]
```

## Izolacja (`isolation`)

`isolation.allowed_layers` wymienia jedyne warstwy, które mogą istnieć na maszynie:

```yaml
isolation:
  allowed_layers: [core, acme, moja]
```

`skillsync layers check --isolation` daje PASS tylko wtedy, gdy:
1. każdy checkout ma `layer.yaml` z `id` z listy;
2. w katalogu warstw systemu ani w znanych lokalizacjach nie ma warstwy spoza listy ani katalogu skilli bez `layer.yaml`;
3. w drzewie nie leży plik tożsamości obcej warstwy.

Gdy klucz `isolation` nie istnieje, `layers migrate --apply` dopisuje `allowed_layers` ze wszystkimi warstwami (w kolejności
core, org, personal). `isolation` bez `allowed_layers` daje ostrzeżenie i nie zmienia configu. `layers add`, `layers init` i
`layers use` nie zmieniają `isolation`. `layers migrate --finish` wymaga `allowed_layers`.

## Strażnik wycieków (`leakguard`)

```yaml
leakguard:
  identity_dir: ~/Repos/ops/leakguard          # jawne pliki tożsamości (repo operacyjne, prywatne)
  targets:
    acme: {scope: org-private, remote_match: ["git@example.com:acme/*"], authors: ["jan@acme.example"]}
    core: {scope: public, remote_match: ["example.com/owner/skillsync*"], authors: ["owner@example.com"]}
```

| Pole | Znaczenie |
|---|---|
| `identity_dir` | katalog z plikami `identity` celów (repo prywatne, poza repo publicznymi) |
| `targets.<id>.scope` | `public`, `org-private` albo `private`; wpływa na CI i poziom pokrycia (`coverage`) |
| `targets.<id>.remote_match` | wzorce adresów remote, po których `leakcheck` rozpoznaje cel |
| `targets.<id>.authors` | adresy dozwolonych autorów i committerów celu |
| `targets.<id>.authors_scope` | `committer` (sprawdzany committer; w commicie lokalnego autora zatwierdzonym przez kogoś innego także autor) albo `all` (autor i committer); domyślnie `committer` dla `scope: public`, `all` dla `org-private` i `private` |
| `targets.<id>.committer_allow` | adresy przyjmowane TYLKO jako committer i tagger (np. przycisk scalania hosta, `noreply`), nigdy jako autor; domyślnie pusta lista; porównanie bez rozróżniania wielkości liter; adres autora zostaje w `authors`; działa dla zakresu/historii i tagów, nie dla `pre-commit`; przy `authors_scope: committer` zwykle bez efektu (sens ma przy `all`) |
| `trailer_allow` | adresy dozwolone w trailerach `Co-Authored-By` ludzi (lista); nie dotyczy trailerów narzędzi AI, które zakazuje kategoria `attribution` |

Klucze `trailer_allow`, `authors_scope` i `committer_allow` czyta config maszyny; flagi `--trailer-allow` i `--targets-json` są wejściami dla narzędzi
operacyjnych i nie są pokazywane w `--help`.

Model wykrywania, poziomy `coverage` i format wzorców: [security](security.md). Config nie zawiera nazw klientów
w repo publicznym; tożsamości żyją w prywatnych warstwach i w `identity_dir`.

## Pozostałe klucze

| Klucz | Typ | Znaczenie |
|---|---|---|
| `discover_paths` | lista katalogów | korzenie przeszukiwane pod `.github/.skillsync.yaml` przez komendy bez ścieżki (`link`, `instructions`, `push`, `pull`, `watch`); obejmuje worktree |
| `discover_exclude` | lista ścieżek (`~` rozwijane) | domyślnie pusta; ścieżki i ich podkatalogi są pomijane przy każdym szukaniu projektów w `discover_paths`: `link`, `instructions`, `push` bez ścieżki, `retro --all` i `retro-scan --cross-project` (porównanie po `resolve()`); przykład: `discover_exclude: [~/projects/old]`; zły typ wartości: w komendach synchronizacji `skillsync: <komunikat>` na stderr i kod 2 (cała komenda zablokowana); w `retro --all` i `retro-scan --cross-project` JSON `BLOCKED` z `reason` na stdout i kod 2 |
| `tags` | mapa `nazwa: [skille]` | grupy dla `@nazwa` w `skills:` manifestu |
| `variables` | mapa | wartości `{{ klucz }}` w szablonach instrukcji; nadpisywane przez `instructions.variables` w manifeście |
| `python_command` | string | interpreter w hookach Stop w `.claude/settings*.json` (Linux `python3`, Windows `py` albo `python`) |
| `entrypoint` | `inline` \| `agents` | domyślny tryb wejścia instrukcji, gdy manifest nie podaje `instructions.entrypoint` |
| `allow_override` | lista | identyfikatory elementów rdzenia i ścieżki plików, które warstwa `org` może nadpisać; domyślnie żadne |
| `feedback_targets` | lista | cele zgłoszeń zmian do warstw zatwierdzone przez człowieka: `{layer, kind: azure-boards, organization, project}`; zapisuje je `skillsync feedback trust <warstwa>` (tylko z terminala). Sekcja `feedback:` w `layer.yaml` jest tylko propozycją celu; bez wpisu tutaj nic nie jest wysyłane, a zmiana organizacji lub projektu w warstwie unieważnia zgodę. Wpis nieznanej warstwy albo z nieprawidłową wartością to błąd wczytania konfiguracji. Zob. [zgłoszenia zmian](domain/feedback-channel.md) |
| `legacy_library` | ścieżka | katalog starej biblioteki zapisany przez `layers migrate`; służy do rozpoznawania dowiązań starej konfiguracji i do `layers migrate --finish` |
| `memory_sync` | mapa | kopiowanie plików pamięci na hosty (niżej) |
| `dashboard` | mapa | nasłuch `dashboard serve` na tej maszynie: `local_only` (`true` = oba gniazda na `127.0.0.1`; `false` = brak preferencji), `read_bind`, `approve_bind` (literał IP, `localhost`, `0.0.0.0` albo `::`); pierwszeństwo: flaga CLI > manifest projektu > ten config > domyślne (odczyt `0.0.0.0`, zgody `127.0.0.1`); nieznany klucz = ostrzeżenie `doctor` i BLOCKED `serve`; szczegóły: [manifest](manifest.md#sekcja-dashboard) Manifest projektu może tylko zawężać (poszerza flaga albo ten config). |
| `prototype.default_password` | string | hasło domyślne kont tworzonych przez `prototype user add\|import`, gdy nie podano innego; plik configu musi być prywatny (POSIX: uprawnienia 0600; Windows: ACL tylko dla użytkownika, SYSTEM i administratorów, ustawiany i naprawiany przez narzędzie); wartość nigdy nie jest wypisywana |

### Kopiowanie plików pamięci (`memory_sync`)

```yaml
memory_sync:
  files:                        # źródła (expanduser); tylko odczyt
    - ~/.config/agent/memories/notes.md
  targets:
    - host: example-host        # alias z ~/.ssh/config; logowanie kluczem bez hasła
      dir: "~/.codex/memories"  # katalog docelowy; nazwa pliku jak w źródle
```

Kierunek jest jednostronny (źródło → cel). Porażka (host wyłączony, brak sieci) daje ostrzeżenie i ponowną próbę,
bez zatrzymywania `watch`. Plik na celu nie jest nadpisywany zmianami z celu. Komenda: `skillsync memory-sync`.
Zmiana sekcji wymaga restartu usługi watch.

## Migracja

Config przenosi `skillsync layers migrate` (domyślnie dry-run). Migrację istniejącej maszyny opisuje [upgrading](upgrading.md).

## Przykłady

**Jedna organizacja i warstwa osobista.** Zestaw jak w sekcji „Warstwy” i „Dobór warstw”: `core`, `acme`, `moja`;
reguła `match` na katalog projektów organizacji, reguła `**` dla reszty.

**Dwie organizacje na jednej maszynie.** Patrz przykład wyżej: dwie warstwy `org` i dwie reguły `match` na rozłączne katalogi.

**Bez organizacji.** Tylko `core` i opcjonalnie `personal`; reguła `match: ["**"]`.

```yaml
layers:
  - {id: core, kind: core, source: https://github.com/BATUSystems/skillsync, ref: vX.Y.Z}
  - {id: moja, kind: personal, source: ~/Repos/moje-skillsync}
layer_rules:
  - match: ["**"]
    use: [core, moja]
```
