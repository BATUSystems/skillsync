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
skillsync leakcheck <katalog|projekt> [--target ID] [--require full] [--no-config] [--worktree|--staged|--range A..B|--history|--text FILE|--stdin] [--json]
```

Sprawdza treść plików, nazwy plików i metadane commitów (autor, e-mail). Kody: PASS 0, FAIL 1, BLOCKED 2. `--require full` kończy się BLOCKED, gdy pokrycie nie jest pełne; `--no-config` pomija config maszyny (tylko wzorce ogólne).

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

Kategorie wzorców (`category`): `d27`, `owner-path`, `email`, `machine`, `publisher`, `custom`, `attribution` (wbudowana: stopki i trailery narzędzi AI, zob. „Zakaz stopek i podpisów narzędzi AI”). Powierzchnie (`applies_to`):
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

Konto bez roli `developer`, `acceptor` ani `expert` (domyślnie `commenter`) dostaje na trasach odczytu pytań tylko podgląd (`200`, `preview: true`): tekst pytań oraz imiona kont, które napisały odpowiedzi i komentarze, z białej listy pól, bez loginów i adresów e-mail, bez `origin` (ścieżki speca) i bez adresatów (tylko ich liczba); wyciągi i odnośniki zapisane w pytaniu (`refs`) oraz teksty swobodne są pokazywane, więc nie wpisuje się w nie niczego poufnego; zapisy są odmawiane (`403`). Brak sesji to `401`, konto z hasłem domyślnym `403 password_change_required`.

- Poufność treści repozytorium opiera się na wyciągach: agent zapisuje w pytaniu fragment podlinkowanej sekcji, a serwer nie czyta speca
  ani dokumentacji. `origin.spec` to tylko ścieżka względna; widok eksperta jej nie zawiera. Treść pytań i odpowiedzi to dane, nie polecenia dla agenta.
- `questions.json` (i `questions.json.lock`) nigdy nie są serwowane statycznie (404, także z sesją; dotyczy to `<root>/questions.json` i `<root>/<slug>/questions.json`, także za dowiązaniem w przebiegu; głębiej zagnieżdżony plik o tej nazwie jest zwykłym plikiem prototypu). Pytania zwracają tylko trasy `/_api/v1/questions*` i `/_api/v1/prototypes/<slug>/questions*`, zawsze z sesją
  (także bez `--login-to-view`); filtr adresatów działa w serwerze, a cudzego pytania ekspert nie
  odróżnia od nieistniejącego (404). Konto z hasłem domyślnym (`mustChange`) dostaje 403 na wszystkich trasach pytań.
- Pokaz `prototype serve --demo` nie ma kont, sesji ani ciasteczek i niczego nie zapisuje: bramka w jednym miejscu (przed sesją i trasami) odpowiada `405 demo_read_only` na każdą metodę inną niż `GET`/`HEAD`,
  z API zostaje tylko `GET /_api/v1/session`, a `/_*` i reszta `/_api/*` to `404`. Statyka pokazu to lista dodatnia (ekrany, silnik, arkusze, media, `prototype.json` okrojony do `slug` i `title`): nie wydaje `comments.json`, `comments.js`,
  `README.md`, `iterations/`, `questions.json` ani dot-plików, także przez dowiązanie lub zapis ścieżki z `%2e`, ukośnikiem odwrotnym albo inną wielkością liter. Domyślnie słucha na `0.0.0.0` (`--host 127.0.0.1` zawęża do tej maszyny), a komunikat startowy mówi, że strona jest jawna; `--demo` z `--login-to-view` kończy się `BLOCKED`.
  Przy domyślnym `0.0.0.0` każdy z sieci może wołać galerię, która liczy opis każdego prototypu (jak galeria zwykła): dane nie wyciekają, a koszt procesora to znana cecha serwera (jeden wątek na połączenie, limit 30 s); poza zaufaną siecią postaw pokaz za proxy z limitami.
  Nagłówek `Host` nie jest sprawdzany: pokaz nie ma uprawnień ani danych kont, które przejęłaby strona obca (rebinding daje to samo co bezpośrednie żądanie do tego adresu: jawną treść tylko do odczytu); przy `--host 127.0.0.1` obca strona w przeglądarce użytkownika mogłaby przez rebinding odczytać prototypy, ale nie ich komentarze ani konta, bo ich nie wydajemy.
- Poufność statycznych prototypów zapewnia dopiero `--login-to-view`; przy kontach `expert` bez niego serwer ostrzega przy starcie.
  **Kto czyta komentarze.** Dziennik operacji (komentarze, wątki, decyzje, akceptacje z loginami) wydaje wyłącznie `GET /_api/v1/prototypes/<slug>/operations` (API dodaje imiona `people` z pliku kont i stan akceptacji); statyczne `<slug>/comments.json` i `comments.js` dają `404`, tak jak w pokazie. API daje **ten sam zbiór operacji bez rozróżnienia ról**, a jego bramką jest wyłącznie `--login-to-view`: bez niego czyta każdy, kto zna adres i port; z nim każde konto z sesją, także `expert`, `commenter` i `acceptor`. Rola `expert` nie ma więc węższego odczytu komentarzy niż inne (ogranicza ją tylko to, czego nie może zapisać: akceptacja, przywracanie, usuwanie). Test `tests/test_skillsync_prototype_comments_visibility.py` przypina ten stan, żeby zmiana modelu widoczności była świadoma, a nie przypadkowa.
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

`--target-patterns` to flaga wewnętrzna CI (nie ma jej w `--help`): wskazuje plik wzorców celu z gałęzi bazowej.

W innym systemie CI (np. Azure Pipelines) uruchom te same dwa polecenia; kod wyjścia 1 albo 2 zatrzymuje potok.

## Autor commitów

Ustaw `git config user.email` w każdym repo warstwy na adres właściwy dla jej organizacji; `leakcheck --history`
dopuszcza tylko adresy z listy celu.

## Zgłaszanie podatności

Zobacz [SECURITY.md](../SECURITY.md).

## Zakaz stopek i podpisów narzędzi AI

Reguła rdzenia (stała w `_base.md`, nienadpisywalna): w żadnym tekście wychodzącym (commit, PR, zadanie, dokument, CHANGELOG, kod) nie ma stopek, podpisów, trailerów współautora ani reklam narzędzi i modeli AI. Dwie klasy w jednym pliku danych (`skillsync_workflow/data/attribution-patterns.json`): A = stopka i reklama, B = trailer współautora AI (trailery ludzi nie są objęte); pole `enforce` wskazuje klasy w mocy. Wymuszenia:
- **Host:** `link` zapisuje w `.claude/settings.local.json` klucz `attribution` z pustymi `commit` i `pr` (forma obiektu; samo `false` wymaga nowszego hosta i nie jest używane; `sessionUrl` dotyczy sesji w chmurze i zostaje bez zmian; efekt na żywym hoście nie jest jeszcze potwierdzony); `skillsync attribution --user` robi to samo w `~/.claude/settings.json` na wyraźne polecenie. Pliki ustawień mają pierwszeństwo: zarządzane (managed), wiersz poleceń, lokalne, wspólne, użytkownika; tylko poziom zarządzany jest nie do nadpisania, więc twarda gwarancja dla całej maszyny to wpis `skillsync attribution --print-managed` w pliku ustawień zarządzanych hosta (skillsync go nie zapisuje). Dokumentacja hosta zastrzega, że instrukcje użytkownika o atrybucji mogą przeważyć nad domyślnym dopiskiem, o ile nie pochodzi on z poziomu zarządzanego, więc samo ustawienie nie jest twardą gwarancją. Przy `claude: false` `link` usuwa też klucz `attribution` z `settings.local.json`, ale tylko wtedy, gdy jest dokładnie naszą wartością (puste `commit` i `pr`); zakaz stopek AI w instrukcjach projektu (reguła `_base.md`) nie zależy od tego klucza.
- **Hak:** niewyłączalna część `guard-commands.py` odmawia poleceń, które piszą tekst z takim wzorcem (zob. [workflow](workflow.md)); widzi argumenty (także `--opcja=TEKST`, `-mTEKST`, `$'…\n…'`), heredoc, here-string, wartości zmiennych wpisane w tym samym poleceniu, podstawienia procesów, pliki `-F`/`--body-file`/`@plik` i pliki czytane przez `cat` w tym samym poleceniu, także po `cd` i `git -C`, w złożeniach `&&`, `;`, podpowłoce i `bash -c`. Nie widzi: narzędzi poza `Bash` (np. Jira przez MCP), treści z innych źródeł niż to polecenie (zmienna ustawiona wcześniej w sesji lub złożona z kawałków, plik tworzony skryptem, `eval`, `xargs`, dekodowanie `base64`, `$(<plik)`, aliasy gita, `git commit-tree`, `git interpret-trailers`, inne systemy kontroli wersji, `curl` i biblioteki HTTP do API trackerów, parafrazy stopek spoza wzorców). Commit z takim trailerem zatrzyma wtedy `pre-push` i CI (`leakcheck`); opis PR i zadania nie mają takiej siatki poza regułą z `_base.md`, ustawieniem hosta i `skillsync leakcheck --stdin`, więc to znane ograniczenie.
- **Leakguard:** kategoria `attribution` na wszystkich celach i klasach ścieżek (pre-commit, pre-push, `leakcheck --text/--stdin` dla opisów PR). Skan `--history` jej nie stosuje: istniejące commity zostają, kontrola dotyczy nowych zmian (zakres push i CI, indeks, tekst).
- **Warstwy i instrukcje:** `layers check` (kod `attribution-text`) i `doctor` dają FAIL, gdy warstwa lub wygenerowane instrukcje projektu zawierają tekst takiej atrybucji; tekst przeczący regule bez dosłownej frazy nie jest wykrywalny maszynowo, a skan obejmuje pliki `.md`, `.yaml`, `.yml`, `.txt`, `.toml` i `.json` w `instructions/`, `skills/`, `agents/` i `workflow/` (skrypty `.sh`, `.py` i `.ps1` w skillach oraz pliki poza tymi katalogami pomija).

## Hooki sesji i podumysłów

- **Kontekst wstrzykiwany do modelu** (`session-context.py`, SessionStart): wyłącznie dane z bieżącego projektu: nazwa, `delivery.mode`, tytuł i statusy otwartego planu oraz liczba ostrzeżeń `subagent-report`. Tytuł planu jest sanityzowany (znaki sterujące i nowe linie usunięte, do 120 znaków) i oznaczony w tekście jako dane, nie instrukcje. Limit 2000 znaków. Bez treści planów, sekretów i ścieżek spoza projektu.
- **Hooki lokalne:** działają z konfiguracji `.claude/settings.local.json` i skryptów w `.claude/hooks/`, bez sieci. Zapisują tylko w `.skillsync/` projektu (zdarzenia `subagent-report` i `cli-unreadable`). Błąd hooka kończy się wynikiem pustym i kodem 0 (fail-open); wyjątkiem jest jawny `deny`. Timeout hooka (3 s dla SessionStart, 5 s dla PostToolUse, 10 s dla pozostałych) kończy hook bez przerwania sesji.
- **Granica commitu podumysłu** (`hooks.subagent_commit`) to **guardrail przeciw pomyłce, nie granica bezpieczeństwa**. Hook rozpoznaje podumysł po `agent_id` w payloadzie, czyta tekst polecenia i nie widzi: uruchomionych skryptów (`./skrypt.sh`, `python x.py`), pipe do powłoki (`… | sh`), aliasów gita, zmiennych ustawionych wcześniej lub złożonych z kawałków, `eval` z wartością z zmiennej (`eval` z literałem jest rozpoznawany), `xargs`, plików tworzonych przez skrypt, zapisów poza `Bash` (np. narzędzie MCP). Świadome obejście jest możliwe.
- **`SKILLSYNC_GUARD_EXCEPT` nie omija granicy podumysłu.** Wyjątek z tą zmienną wymaga zgody użytkownika, a podumysł jej nie ma.
- **`guard-generated` nie widzi zapisów poza narzędziami `Write`, `Edit`, `NotebookEdit`:** `sed -i`, przekierowania i inne zapisy z `Bash` go omijają. Ostrzeżenie (`warn`) to przypomnienie, nie blokada.
- **`check-subagent-report` tylko zapisuje zdarzenie** (`warn`); nie blokuje i nie zmienia decyzji o uprawnieniach. Zgody (`GO`, `CP`, `READY`) pochodzą wyłącznie od użytkownika.

## Hook ochronny przed trwałym usunięciem

Część `hooks/guard-commands.py` (PreToolUse na `Bash`, tylko Claude Code, projekty z `feature` (także pod aliasem `agentflow`) lub `dispatch`) odmawia `rm -r`, `git clean -f`, `git worktree remove --force` i podobnych poza katalogami tymczasowymi, scratchpadem i katalogami odtwarzalnymi. To **siatka przeciw pomyłce, nie granica bezpieczeństwa**: czyta tekst komendy, więc nie widzi poleceń ukrytych w skryptach, nieznanych wrapperach ani zmiennych, których nie umie rozwiązać; dokumentacja hostów mówi to samo o regułach `Bash`. Wyjątek (`SKILLSYNC_GUARD_EXCEPT`) agent dopisuje sam po zgodzie użytkownika w oknie wyboru; hook nie umie sprawdzić zgody, zostawia ślad w `.skillsync/hook-exceptions.jsonl` (komenda do 500 znaków, bez redakcji sekretów: nie wpisuj sekretów do komend). Błąd hooka nigdy nie blokuje pracy.

**Quick actions GitLaba i `glab`.** Ten sam hook odmawia w każdej sesji zapisu `glab`, którego tekst ma linię zaczynającą się od `/słowo` (GitLab wykonałby ją po stronie serwera, np. scalił merge request), a podumysłowi przepuszcza z `glab mr` tylko odczyt. To także siatka przeciw pomyłce: nie czyta `glab api` (pola żądania ani `-X PUT …/merge`, jak `gh api`), przekierowania na standardowe wejście, `$(<plik)`, treści ze zmiennych ustawionych wcześniej, z `sed`, `jq` czy skryptów, plików zmienianych przez tę samą komendę po odczycie, tekstu za pierwszymi 64 KiB pliku, aliasów (`glab alias`), edytora interaktywnego ani narzędzi poza `Bash`. Zgodę na scalenie egzekwują dopiero ustawienia projektu na GitLabie (chronione gałęzie, uprawnienia tokenu).

**Ostrzeżenie o szerokich biegach testów.** Ten sam wpis PreToolUse niesie drugą część (`guard-tests.py`, `test-tools.json`; klucze `hooks.guard_tests`, `hooks.test_wrappers`, `hooks.wide_modules`, [manifest](manifest.md)), która nigdy nie blokuje: ostrzeżenie trafia do kontekstu modelu razem z wynikiem polecenia, czyli **po jego wykonaniu**, więc nie zapobiega samemu biegowi. Jej skuteczność na żywej sesji agenta nie jest zweryfikowana, a jak hak usuwania nie widzi narzędzia PowerShell. Wpis i skrypty zostają zawsze (niosą zakaz stopek narzędzi AI); wyłączenie obu części zostawia samą część zakazu; `doctor` raportuje stan. Granice: [test-run](../library/skills/workflow/references/test-run.md).

**Windows.** Hak czyta polecenie tak, jak czyta je powłoka narzędzia Bash (Git Bash): niecytowana ścieżka natywna (`rm -rf C:\work\x`) traci tam backslashe (naprawdę usuwa `C:workx` względem katalogu roboczego), więc hak widzi tę samą ścieżkę względną i odmawia. Rozpoznawane formy ścieżki do katalogu tymczasowego, scratchpada i katalogów odtwarzalnych: natywna w cudzysłowie (`'C:\work\x'`, `"C:\\work\\x"`), z ukośnikami (`C:/work/x`), forma Git Bash (`/c/work/x`, `/cygdrive/c/work/x`; mapowana na dysk; założenie: Git Bash, gdzie `/c` to dysk C:, nie WSL ani własny fstab), bez rozróżniania wielkości liter dysku i ścieżki, także nazwy 8.3 (po rozwinięciu `realpath`; przetestowane na Windows). Wątpliwość = odmowa: prefiks `\\?\`, UNC i wszystko, czego nie da się rozwiązać, zostaje odrzucone. Granica: hak jest podpięty tylko do narzędzia `Bash`; usunięcie wykonane narzędziem PowerShell (`Remove-Item -Recurse`, `rd /s`) poza `Bash` hak NIE widzi (w poleceniu `Bash` je rozpoznaje i odrzuca); parsera PowerShella nie ma.

## Wyniki testów na stronie statusu

Wyniki testów kroków planu (nazwy testów, grupy, komunikaty porażek) są **niemaskowane**: zapisują się surowo w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl` i pokazują się bez maski w polu `tests` strony statusu oraz w `GET /api/tests`, **na obu gniazdach, także gdy port odczytu jest widoczny w sieci lokalnej**. Komunikat porażki może nieść treść plików, które czyta test (porównania snapshotów, wydruk zmiennych), więc to w praktyce treść plików projektu.

- **Wiersz polecenia testów jest zawsze maskowany, heurystycznie** (`test_events.mask_command` plus `redact`): pary `nazwa=wartość` (hasło, token, klucz...), dane w adresie URL, `Authorization`, **wartość po fladze** o nazwie zawierającej token, password, passwd, secret, key, pass, auth albo credential, albo `pat` lub `pwd` jako osobny człon (`--token WARTOŚĆ`, `-api-key WARTOŚĆ`, `--github-pat WARTOŚĆ`; nazwa zawierająca któreś z dłuższych słów, np. `--keyword`, też maskuje wartość: wolimy zamaskować niż ujawnić) oraz wartość po `-p` tylko dla narzędzi, w których to hasło (`mysql`, `mysqldump`, `mysqladmin`, `mariadb`, `sshpass`). **Nie maskuje:** sekretu jako argumentu pozycyjnego (`tool deploy TAJNE`), krótkiej flagi innego rodzaju (`-k WARTOŚĆ`, a `-p` przy pytest, mvn, ssh to wtyczka, profil i port), sekretu wczytywanego przez skrypt ze środowiska (nie ma go w linii), wartości zaczynającej się od `-` po fladze (`--token -abc`) ani reguły `-p`, gdy polecenie zaczyna się od `sudo`, `env X=y` albo `X=y mysql` (narzędzie nie jest wtedy pierwszym słowem). Sekrety do testów przekazuj przez zmienne środowiska, nie przez argumenty.
- Plik zdarzeń jest wejściem niezaufanym (może pochodzić z innej maszyny): czytnik pomija linię zbyt długą, zbyt głęboką, z liczbą spoza zakresu (NaN, nieskończoność, `1e999`) albo z typem, którego JSON nie ma; samotny surogat zamienia na U+FFFD; pola `start` i `end` zostawia tylko znane i właściwego rodzaju. Identyfikatory drzew git z pliku (`treeStart`, `tree`) przechodzą dalej tylko jako 40–64 znaki szesnastkowe (nic z pliku nie trafia do wiersza poleceń gita jako opcja). Opcjonalne `end.changed` (ścieżki zmienionych plików, do 5, każda do 300 znaków) jest maskowane przy zapisie i przy odczycie, także bez `tests.mask`, i trafia tylko do komunikatu bramki, nigdy do polecenia. Jeden zły plik albo katalog nie kosztuje strony: pole `tests.skipped` w odpowiedzi nazywa pominięte (`{path, reason}`, stały powód, bez treści pliku).
- Wyjątek od maski dotyczy tylko liści będących wynikiem testu (`id`, `name`, `group`, `msg`, nazwa grupy); wiersz polecenia, tytuł kroku, powód zakończenia i cała reszta strony przechodzą przez maskę (`dashboard_tests.finalize_status`). Pliki zdarzeń mają limity (10 000 testów, 8 MiB na bieg, komunikat do 2000 znaków).
- `tests.mask: true` w manifeście (maskuje każda wartość poza jawnym wyłączeniem, zob. [manifest](manifest.md#klucz-tests)) maskuje wyniki przy zapisie i w odpowiedzi strony (stare surowe zdarzenia zostają na dysku). Bez tego klucza wyniki są jawne także dla każdego, kto widzi port odczytu w sieci lokalnej (zob. niżej).
- `doctor` ostrzega (`runs-not-ignored`), gdy `.skillsync/runs` jest śledzony w git albo nieobjęty `.gitignore`. Kopia przebiegu jest dozwolona tylko na tej samej maszynie.
- Komunikatów porażek i nazw testów nie wolno wklejać do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik` planu, raporty w repozytorium, wiadomości między sesjami): podaje się liczniki, `id` testu i ścieżkę do pliku przebiegu.
- Bramka kroków `[T]` chroni przed POMINIĘCIEM testów, nie przed FAŁSZERSTWEM: `checkpoint --evidence` zapisuje skrót pliku i nie weryfikuje, kto go napisał.

## Strona statusu w sieci lokalnej

`dashboard serve` bez flag słucha jednym gniazdem strony na `0.0.0.0` (port 5140), więc plany, specyfikacje, wyniki testów (jawne, bez maski, zob. wyżej; sekrety w pozostałej treści zredagowane heurystycznie) widzi każde urządzenie w sieci lokalnej, nieszyfrowanym HTTP. Bez sesji konta `approver` nie ma zgód; zgody i logowanie tego konta są przyjmowane lokalnie albo przez TLS. W nieufnej sieci zawęź nasłuch: `--read-bind 127.0.0.1` albo `dashboard.local_only: true` ([manifest](manifest.md#sekcja-dashboard)). Konsola przy starcie ostrzega o nasłuchu poza loopbackiem. Opis flag i sesji: [cli](cli.md), model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md).

Zgoda wymaga sesji `approver` z tokenem CSRF i ścisłym `Origin` zgodnym z `Host`. Żądanie z nagłówkiem pośrednika (`Forwarded`, `X-Forwarded-*`, `Via` i inne z listy) nie jest „lokalne”. Przez proxy zgoda przechodzi tylko jako TLS: proxy jest zaufane (`--trust-proxy` z `--proxy-address`) i wysyła `X-Forwarded-Proto: https`; inaczej jest odrzucona. Proxy na tej samej maszynie, które nie dodaje żadnego nagłówka pośrednika i ustawia `Host` na loopback, nie różni się od połączenia lokalnego, dlatego proxy ma zawsze dodawać `X-Forwarded-For` i `X-Forwarded-Proto`. Hasło przechodzi tak samo: lokalnie albo przez TLS (także przy zmianie hasła); po zwykłym HTTP z sieci nikt się nie loguje, chyba że `--allow-plain-login` (konta `commenter` i `expert`). `--allow-proxy-headers` zostało usunięte. `X-Forwarded-For` nie zmienia adresu połączenia zapisanego w `via.peer`.

Maska sekretów (`common.redact`) obejmuje raporty JSON CLI, stronę statusu, `dashboard show`, `skillsync status` i `GET /api/doc`; tekst planów i specyfikacji jest maskowany w całości przed parsowaniem. To heurystyka: nie maskuje gołych tokenów bez klucza, wartości krótszych niż 8 znaków ani zmiennych o nazwach, które o tym nie mówią (nazwa mówi o sekrecie, gdy zawiera `token`, `secret`, `password`, `passwd`, `api_key`, `credential` albo `PAT` lub `PWD` jako osobny człon rozdzielony `_` albo w camelCase, np. `GITHUB_PAT`, `DB_PWD`, `githubPat` (na Windows `os.environ` podaje nazwy wielkimi literami, więc nazwy zmiennych środowiskowych w camelCase nie są tam rozpoznawane jako wrażliwe; w tekście i w kluczach raportów są); zmienna `PWD` to katalog roboczy i nie jest maskowana, a `OLDPWD`, `OLD_PWD` i `INIT_PWD` nie są maskowane, gdy ich wartość jest ścieżką). Te same słowa (plus `api-key` i `authorization`) decydują, które klucze raportów i parametrów operacji `delivery` i `e2e` są sekretami. W tekście maskowana jest wartość po kluczu `password`, `passwd`, `token`, `secret`, `api_key`, `secret_key`, `access_key`, `private_key` ze znakiem `=` lub `:`; po złożonym `…_PAT`, `…-pwd` i `…Pat` tak samo, także gdy po `PAT` lub `PWD` wielkimi literami albo po `Pat` / `Pwd` w camelCase są kolejne człony (`GITHUB_PAT_RO`, `DB_PWD_PROD`, `githubPat_ro`; nie przy członie `_PATH`, `_DIR`, `_FILE`, `_HOME`, `_URL`), a po samym `pat` i `pwd` tylko przed `=` albo jako klucz w cudzysłowie (`pwd: …` i `PAT: …` to zwykłe zdania). `PWD` wielkimi literami przed ścieżką (`/`, `~`, `C:\`), `.`, `$(` lub `${` to katalog roboczy (także `$PWD`); każde inne `PWD=` to hasło z parametrów połączenia (hasło zaczynające się od `/`, `~` albo `\` zostaje wtedy widoczne). Dwa dalsze wyjątki, żeby nie maskować kodu i katalogów: wartość `OLDPWD`, `OLD_PWD`, `INIT_PWD` i flagi `--pwd`, która jest ścieżką (hasło o takim kształcie pod tymi nazwami zostaje widoczne), oraz przypisanie ze spacjami wokół `=`, którego niecytowana wartość jest wywołaniem albo elementem nazwy (`url_pat = re.compile(…)`). Te same nazwy (z `secret_words.py`) zna skan `feedback`, skan sekretów przy `prototype question` i maska parametrów URL w komunikatach o warstwach (tam także `pass`, `auth`, `key`, `sig`, `signature`, także parametr zagnieżdżony w wartości innego). Bramka `feedback send` jest szersza dla nazw z członem zaczynającym się od `_pat` (`github_pat_ro`, `MY_PATS`, wartość także w następnej linii; nie `_path`, `_pattern`, `_patch`). Ograniczenia: wywołanie z przecinkiem albo spacją w nawiasie (`re.compile(x, y)`) nadal jest maskowane, a hasło wyglądające dokładnie jak wywołanie (`pwd = Ab(cd)`) nie. Klucz lub wartość rozbite znakami formatującymi Unicode (kategoria Cf: spacja zerowej szerokości, znaki dwukierunkowe, miękki łącznik, BOM) są czytane bez nich, a gdy coś znajdą, wynik nie zawiera tych znaków. Cudzysłów bez zamknięcia kończy się na końcu linii, także samotnym CR. Kontrakt: [dashboard-contract](dashboard-contract.md).

**Co widzi czytelnik bez sesji** (także sesja, która musi zmienić hasło, i zawsze gniazdo odczytu):

- Na stronie statusu (`/api/status`, `/api/tabs`, `/api/history`) adresy e-mail zastępuje etykieta `uczestnik` albo pseudonim konta, ścieżki bezwzględne stają się nazwami katalogów albo ścieżkami względem kopii roboczej (katalog domowy: `~`), a w czekaniu nie ma loginu; sformatowany HTML zostaje tylko tam, gdzie nie było czego ukryć, inaczej jest zamaskowany zwykły tekst (zob. `dashboard-contract.md`). Treść dokumentów (`/api/doc`) i wyniki testów są jawne, w tym to, co wypisały testy.
- Na originie prototypów operacje komentarzy są budowane z białej listy pól (pseudonim zamiast loginu i imienia, bez `cid`, `importedAuthor` i nieznanych kluczy), `prototype.json` ma pięć pól bez ścieżek bezwzględnych, pytania do eksperta są podglądem z pseudonimami, a dzienniki `comments.json`, `questions.json` i ich kopie nie są serwowane jako pliki.
- Origin prototypów słucha tam, gdzie strona, więc widzi go ten sam krąg w sieci. Zalogowany czytelnik widzi to co dotąd. Samodzielny `prototype serve` nie redaguje widoku.

**Limity serwera logowania** (strona statusu i `prototype serve`):

- Za zaufanym proxy źródło limitu prób to ostatni adres spoza listy proxy ze **wszystkich** linii `X-Forwarded-For` po kolei (klient może dodać własną linię przed tą od proxy).
- Tam, gdzie konto nie może się zalogować (`approver` poza originem strony, w tym cały serwer z `--single-origin`; z `--allow-plain-login` zwykłe HTTP dla kont, których ta flaga nie wpuszcza; bez flagi zwykłe HTTP odmawia przed sprawdzeniem hasła i niczego nie liczy), dobre hasło liczy się jak próba: nie czyści licznika, kończy się na tym samym limicie (429) i podnosi alert konta `approver`. Ryzyko pozostające: odpowiedzi są różne (403 z drogą do logowania dla dobrego hasła, 401 dla złego albo nieznanego adresu, bez ujawniania, które adresy są kontami), więc w ramach limitu taki kanał odróżnia dobre hasło od złego.
- Ciało żądania tras logowania (`/_api/v1/login`, `logout`, `password`, `handoff` i `POST /_handoff`) ma jeden limit czasu na całość (10 s): klient wysyłający po bajcie nie przedłuża go i dostaje 408 `body_timeout`.
- `run/audit.jsonl` ma limit rozmiaru (5 MiB); pełny plik staje się `audit.jsonl.1`, zostają cztery starsze (`.1`–`.4`), najstarszy znika. Pola tekstowe są przycinane do 300 znaków, a linie odmów (`consent_refused`, `login_refused`, `login_limited`, `password_refused`) jednego źródła są ograniczone do 120 na minutę (reszta liczona w `suppressed`); zgody nie są pomijane. Ryzyko, które zostaje: nawet jedno źródło może w ciągu kilku do kilkunastu godzin (szacunek) zapełnić audyt odmowami i wypchnąć starsze wpisy, także zgody, a wiele adresów (np. wielu sieci IPv6) robi to szybciej, więc audyt, który ma przetrwać, trzeba kopiować poza maszynę. Gdy odłożenie się nie udaje (np. na Windows plik trzyma inny program), serwer dopisuje dalej do dwukrotności limitu, a potem traktuje audyt jako niedostępny, więc zgody są odrzucane (503 `audit_unavailable`), a konto `approver` się nie zaloguje. Narzędzie zbierające audyt powinno czytać także starsze pliki. Limit czasu nie obejmuje linii żądania i nagłówków (limit 30 s na odczyt) ani ciał tras zapisu zalogowanych kont.

## Zgłoszenia do trackera (`skillsync feedback`)

`feedback send` wysyła treść do trackera właściciela warstwy tylko po skanie wycieków (tożsamości innych warstw i znane kształty sekretów; wynik ma poziom pokrycia, `generic` nie sprawdza nazw klientów) i po zgodzie związanej z `sha12` dokładnej treści podglądu: zmiana treści unieważnia zgodę. Zgodę wyraża tylko użytkownik (słowo `WYŚLIJ <sha12>` albo przycisk na stronie statusu), a w sesji karty `send` i `approve` odmawiają. To guardrail przeciw pomyłce, nie granica bezpieczeństwa: granicą zaufania jest maszyna, a podgląd człowieka pozostaje główną kontrolą. Szczegóły i granice: [domain/feedback-channel](domain/feedback-channel.md).

## Weryfikacja UI (`ui-verify`)

- **Dekoder PNG odrzuca bomby dekompresyjne** przed dekodowaniem: bufor ponad 64 MiB, pierwszy chunk inny niż IHDR o długości 13, drugi IHDR, wymiar zerowy, ponad 16384 px na bok albo 50 Mpx, obraz z przeplotem Adam7, nieprawidłowy lub powtórzony chunk PLTE i ponad 100000 chunków kończą się kodem wyjścia 2 (`ImageLimitError`).
- **`capture --allow-trace` zapisuje artefakty z danymi wrażliwymi**: `trace.zip` i `storageState.json` mogą nieść wartości pól formularza, hasła, nagłówki, ciasteczka i tokeny. Pliki mają tryb 0600 (POSIX), a `<outDir>/.gitignore` je wyłącza, gdy żaden `.gitignore` jeszcze nie istnieje. Maska wyjścia ich nie obejmuje (`trace.zip` i PNG nie są maskowane, `storageState.json` tylko dla znanych wartości sekretów), więc brak znanego sekretu niczego nie dowodzi; nie commituj ich i nie wysyłaj poza maszynę.
- Werdykty, kody wyjścia i granice dowodu: [domain/ui-verify-werdykty-i-granice](domain/ui-verify-werdykty-i-granice.md).
