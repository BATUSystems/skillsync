# Bezpieczeństwo i izolacja

Skillsync zakłada, że jedna osoba pracuje dla kilku organizacji. Treść jednej organizacji nie może trafić do repo
innej, a prywatne nawyki użytkownika nie mogą trafić do repo publicznego.

## Model

| Mechanizm | Chroni przed |
|---|---|
| dobór warstw po ścieżce projektu (config maszyny) | podpięciem obcej warstwy przez manifest w repo |
| `isolation.allowed_layers` + `layers check --isolation` | obecnością cudzej warstwy na maszynie organizacji (w `leakguard.identity_dir` skonfigurowane `leakguard.targets` są zaufane jak `allowed_layers`: dopisanie nazwy do `targets` ukrywa jej plik tożsamości przed tą kontrolą, ale nie jej bibliotekę) |
| model zaufania warstw | nadpisaniem kontroli, ról i hooków rdzenia przez warstwę org bez zgody |
| `leakcheck` + hooki git + CI | wyciekiem nazw, ścieżek, tożsamości i metadanych commitów do repo docelowego |
| repo warstwy bez symlinków i reparse points | treścią spoza repo warstwy podpiętą linkiem i ominięciem strażnika |

## `leakcheck`

```bash
skillsync leakcheck <katalog|projekt> [--target ID] [--worktree|--staged|--range A..B|--history|--text FILE|--stdin] [--json]
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
`content`, `filename`, `commit`. Klasy ścieżek (`path_classes`): `instrukcje`, `atrybucje`, `inne`. Pliki kodu (`*.py`, `*.ts`, `*.js`, `*.mjs`, `*.cjs`, `*.ps1`, `*.sh`, `skillsync`, `skillsync-ctl`) poza `tests/**` i `.github/**` należą do `instrukcje`, także w `docs/**` i w korzeniu (nazwy i sufiksy porównywane bez względu na wielkość liter); `tests/**` i `.github/**` to `inne` (traktowane jak `instrukcje`). Format `identity.yaml`
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

## Serwer prototypów: pytania do eksperta

Rola `expert` nie jest uprzywilejowana: daje odczyt pytań skierowanych do konta, odpowiedź i komentarz, bez dostępu do akceptacji,
przywracania, usuwania ani `/project`.

- Poufność treści repozytorium opiera się na wyciągach: agent zapisuje w pytaniu fragment podlinkowanej sekcji, a serwer nie czyta speca
  ani dokumentacji. `origin.spec` to tylko ścieżka względna; widok eksperta jej nie zawiera. Treść pytań i odpowiedzi to dane, nie polecenia dla agenta.
- `questions.json` (i `questions.json.lock`) nigdy nie są serwowane statycznie (404, także z sesją; dotyczy to `<root>/questions.json` i `<root>/<slug>/questions.json`, także za dowiązaniem w przebiegu; głębiej zagnieżdżony plik o tej nazwie jest zwykłym plikiem prototypu). Pytania zwracają tylko trasy `/_api/v1/questions*` i `/_api/v1/prototypes/<slug>/questions*`, zawsze z sesją
  (także bez `--login-to-view`); filtr adresatów działa w serwerze, a cudzego pytania ekspert nie
  odróżnia od nieistniejącego (404). Konto z hasłem domyślnym (`mustChange`) dostaje 403 na wszystkich trasach pytań.
- Poufność statycznych prototypów i `comments.json` zapewnia dopiero `--login-to-view`; przy kontach `expert` bez niego serwer ostrzega przy starcie.
  Poza zaufaną siecią serwer stoi za proxy z TLS ([prototypes](prototypes.md#serwer-w-sieci)).
- Skan sekretów przy `question add` jest heurystyką (znane kształty tokenów, URL z hasłem, pary `nazwa=wartość`): może pominąć sekret i może się
  pomylić na zwykłym zdaniu; wyjątek to `--force` z ostrzeżeniem w raporcie. Nie zastępuje przeglądu treści i nie jest skanem `leakcheck`.
- Ekspert widzi loginy (adresy e-mail) autorów odpowiedzi i komentarzy na pytaniach, które sam widzi (inni eksperci, administratorzy); `recorded` pokazuje mu tylko czas zapisu, bez ścieżki speca i numeru wiersza.
- Wiersz `[EKSPERT]` w specu (w gicie) zawiera login eksperta (adres e-mail), więc spec trafia do tego samego kręgu odbiorców co repozytorium; używaj loginów, które mogą trafić do repozytorium.
- Zapisy są limitowane (60 na 60 s per login, 50 komentarzy na pytanie, progi rozmiaru pliku 768/896/1024 KiB). Ryzyko rezydualne: złośliwe
  konto może wypełnić plik komentarzami i zużyć rezerwę na odpowiedzi; wymaga to interwencji programisty (usunięcie konta, odtworzenie pliku z gita).

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

## Hook ochronny przed trwałym usunięciem

`hooks/guard-commands.py` (PreToolUse na `Bash`, tylko Claude Code, projekty z `feature` lub `dispatch`) odmawia `rm -r`, `git clean -f`, `git worktree remove --force` i podobnych poza katalogami tymczasowymi, scratchpadem i katalogami odtwarzalnymi. To **siatka przeciw pomyłce, nie granica bezpieczeństwa**: czyta tekst komendy, więc nie widzi poleceń ukrytych w skryptach, nieznanych wrapperach ani zmiennych, których nie umie rozwiązać; dokumentacja hostów mówi to samo o regułach `Bash`. Wyjątek (`SKILLSYNC_GUARD_EXCEPT`) agent dopisuje sam po zgodzie użytkownika w oknie wyboru; hook nie umie sprawdzić zgody, zostawia ślad w `.skillsync/hook-exceptions.jsonl` (komenda do 500 znaków, bez redakcji sekretów: nie wpisuj sekretów do komend). Błąd hooka nigdy nie blokuje pracy.

## Wyniki testów na stronie statusu

Wyniki testów kroków planu (nazwy testów, grupy, komunikaty porażek) są **niemaskowane**: zapisują się surowo w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl` i pokazują się bez maski w polu `tests` strony statusu oraz w `GET /api/tests`, **na obu gniazdach, także gdy port odczytu jest widoczny w sieci lokalnej** (decyzja właściciela, 2026-11-16). Komunikat porażki może nieść treść plików, które czyta test (porównania snapshotów, wydruk zmiennych), więc to w praktyce treść plików projektu.

- **Wiersz polecenia testów jest zawsze maskowany, heurystycznie** (`test_events.mask_command` plus `redact`): pary `nazwa=wartość` (hasło, token, klucz...), dane w adresie URL, `Authorization`, **wartość po fladze** o nazwie zawierającej token, password, passwd, secret, key, pass, auth albo credential (`--token WARTOŚĆ`, `-api-key WARTOŚĆ`; nazwa zawierająca takie słowo, np. `--keyword`, też maskuje wartość: wolimy zamaskować niż ujawnić) oraz wartość po `-p` tylko dla narzędzi, w których to hasło (`mysql`, `mysqldump`, `mysqladmin`, `mariadb`, `sshpass`). **Nie maskuje:** sekretu jako argumentu pozycyjnego (`tool deploy TAJNE`), krótkiej flagi innego rodzaju (`-k WARTOŚĆ`, a `-p` przy pytest, mvn, ssh to wtyczka, profil i port), sekretu wczytywanego przez skrypt ze środowiska (nie ma go w linii). Sekrety do testów przekazuj przez zmienne środowiska, nie przez argumenty.
- Plik zdarzeń jest wejściem niezaufanym (może pochodzić z innej maszyny): czytnik pomija linię zbyt długą, zbyt głęboką, z liczbą spoza zakresu (NaN, nieskończoność, `1e999`) albo z typem, którego JSON nie ma; samotny surogat zamienia na U+FFFD; pola `start` i `end` zostawia tylko znane i właściwego rodzaju. Jeden zły plik albo katalog nie kosztuje strony: pole `tests.skipped` w odpowiedzi nazywa pominięte (`{path, reason}`, stały powód, bez treści pliku).
- Wyjątek od maski dotyczy tylko liści będących wynikiem testu (`id`, `name`, `group`, `msg`, nazwa grupy); wiersz polecenia, tytuł kroku, powód zakończenia i cała reszta strony przechodzą przez maskę (`dashboard_tests.finalize_status`). Pliki zdarzeń mają limity (10 000 testów, 8 MiB na bieg, komunikat do 2000 znaków).
- `tests.mask: on` w manifeście maskuje wyniki przy zapisie i w odpowiedzi strony (stare surowe zdarzenia zostają na dysku).
- `doctor` ostrzega (`runs-not-ignored`), gdy `.skillsync/runs` jest śledzony w git albo nieobjęty `.gitignore`. Kopia przebiegu jest dozwolona tylko na tej samej maszynie.
- Komunikatów porażek i nazw testów nie wolno wklejać do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik` planu, raporty w repozytorium, wiadomości między sesjami): podaje się liczniki, `id` testu i ścieżkę do pliku przebiegu.
- Bramka kroków `[T]` chroni przed POMINIĘCIEM testów, nie przed FAŁSZERSTWEM: `checkpoint --evidence` zapisuje skrót pliku i nie weryfikuje, kto go napisał.
