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
  **Kto czyta komentarze.** Dziennik operacji (komentarze, wątki, decyzje, akceptacje z loginami) wydaje wyłącznie `GET /_api/v1/prototypes/<slug>/operations` (API dodaje imiona `people` z pliku kont i stan akceptacji); statyczne `<slug>/comments.json` i `comments.js` dają `404`, tak jak w pokazie (od 1.9.0). API daje **ten sam zbiór operacji bez rozróżnienia ról**, a jego bramką jest wyłącznie `--login-to-view`: bez niego czyta każdy, kto zna adres i port; z nim każde konto z sesją, także `expert`, `commenter` i `acceptor`. Rola `expert` nie ma więc węższego odczytu komentarzy niż inne (ogranicza ją tylko to, czego nie może zapisać: akceptacja, przywracanie, usuwanie). Test `tests/test_skillsync_prototype_comments_visibility.py` przypina ten stan, żeby zmiana modelu widoczności była świadoma, a nie przypadkowa.
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

## Zakaz stopek i podpisów narzędzi AI

Reguła rdzenia (stała w `_base.md`, nienadpisywalna): w żadnym tekście wychodzącym (commit, PR, zadanie, dokument, CHANGELOG, kod) nie ma stopek, podpisów, trailerów współautora ani reklam narzędzi i modeli AI. Dwie klasy w jednym pliku danych (`skillsync_workflow/data/attribution-patterns.json`): A = stopka i reklama, B = trailer współautora AI (trailery ludzi nie są objęte); pole `enforce` wskazuje klasy w mocy. Wymuszenia:
- **Host:** `link` zapisuje w `.claude/settings.local.json` klucz `attribution` z pustymi `commit` i `pr` (forma obiektu; samo `false` wymaga nowszego hosta i nie jest używane; `sessionUrl` dotyczy sesji w chmurze i zostaje bez zmian; efekt na żywym hoście nie jest jeszcze potwierdzony); `skillsync attribution --user` robi to samo w `~/.claude/settings.json` na wyraźne polecenie. Pliki ustawień mają pierwszeństwo: zarządzane (managed), wiersz poleceń, lokalne, wspólne, użytkownika; tylko poziom zarządzany jest nie do nadpisania, więc twarda gwarancja dla całej maszyny to wpis `skillsync attribution --print-managed` w pliku ustawień zarządzanych hosta (skillsync go nie zapisuje). Dokumentacja hosta zastrzega, że instrukcje użytkownika o atrybucji mogą przeważyć nad domyślnym dopiskiem, o ile nie pochodzi on z poziomu zarządzanego, więc samo ustawienie nie jest twardą gwarancją. Przy `claude: false` `link` usuwa też klucz `attribution` z `settings.local.json`, ale tylko wtedy, gdy jest dokładnie naszą wartością (puste `commit` i `pr`); zakaz stopek AI w instrukcjach projektu (reguła `_base.md`) nie zależy od tego klucza.
- **Hak:** niewyłączalna część `guard-commands.py` odmawia poleceń, które piszą tekst z takim wzorcem (zob. [workflow](workflow.md)); widzi argumenty (także `--opcja=TEKST`, `-mTEKST`, `$'…\n…'`), heredoc, here-string, wartości zmiennych wpisane w tym samym poleceniu, podstawienia procesów, pliki `-F`/`--body-file`/`@plik` i pliki czytane przez `cat` w tym samym poleceniu, także po `cd` i `git -C`, w złożeniach `&&`, `;`, podpowłoce i `bash -c`. Nie widzi: narzędzi poza `Bash` (np. Jira przez MCP), treści z innych źródeł niż to polecenie (zmienna ustawiona wcześniej w sesji lub złożona z kawałków, plik tworzony skryptem, `eval`, `xargs`, dekodowanie `base64`, `$(<plik)`, aliasy gita, `git commit-tree`, `git interpret-trailers`, inne systemy kontroli wersji, `curl` i biblioteki HTTP do API trackerów, parafrazy stopek spoza wzorców). Commit z takim trailerem zatrzyma wtedy `pre-push` i CI (`leakcheck`); opis PR i zadania nie mają takiej siatki poza regułą z `_base.md`, ustawieniem hosta i `skillsync leakcheck --stdin`, więc to znane ograniczenie.
- **Leakguard:** kategoria `attribution` na wszystkich celach i klasach ścieżek (pre-commit, pre-push, `leakcheck --text/--stdin` dla opisów PR). Skan `--history` jej nie stosuje: istniejące commity zostają, kontrola dotyczy nowych zmian (zakres push i CI, indeks, tekst).
- **Warstwy i instrukcje:** `layers check` (kod `attribution-text`) i `doctor` dają FAIL, gdy warstwa lub wygenerowane instrukcje projektu zawierają tekst takiej atrybucji; tekst przeczący regule bez dosłownej frazy nie jest wykrywalny maszynowo, a skan obejmuje pliki `.md`, `.yaml`, `.yml`, `.txt`, `.toml` i `.json` w `instructions/`, `skills/`, `agents/` i `workflow/` (skrypty `.sh`, `.py` i `.ps1` w skillach oraz pliki poza tymi katalogami pomija).

## Hooki sesji i podagentów

- **Kontekst wstrzykiwany do modelu** (`session-context.py`, SessionStart): wyłącznie dane z bieżącego projektu: nazwa, `delivery.mode`, tytuł i statusy otwartego planu oraz liczba ostrzeżeń `subagent-report`. Tytuł planu jest sanityzowany (znaki sterujące i nowe linie usunięte, do 120 znaków) i oznaczony w tekście jako dane, nie instrukcje. Limit 2000 znaków. Bez treści planów, sekretów i ścieżek spoza projektu.
- **Hooki lokalne:** działają z konfiguracji `.claude/settings.local.json` i skryptów w `.claude/hooks/`, bez sieci. Zapisują tylko w `.skillsync/` projektu (zdarzenia `subagent-report` i `cli-unreadable`). Błąd hooka kończy się wynikiem pustym i kodem 0 (fail-open); wyjątkiem jest jawny `deny`. Timeout hooka (3 s dla SessionStart, 10 s dla pozostałych) kończy hook bez przerwania sesji.
- **Granica commitu podagenta** (`hooks.subagent_commit`) to **guardrail przeciw pomyłce, nie granica bezpieczeństwa**. Hook rozpoznaje podagenta po `agent_id` w payloadzie, czyta tekst polecenia i nie widzi: uruchomionych skryptów (`./skrypt.sh`, `python x.py`), pipe do powłoki (`… | sh`), aliasów gita, zmiennych ustawionych wcześniej lub złożonych z kawałków, `eval` z wartością z zmiennej (`eval` z literałem jest rozpoznawany), `xargs`, plików tworzonych przez skrypt, zapisów poza `Bash` (np. narzędzie MCP). Świadome obejście jest możliwe.
- **`SKILLSYNC_GUARD_EXCEPT` nie omija granicy podagenta.** Wyjątek z tą zmienną wymaga zgody użytkownika, a podagent jej nie ma.
- **`guard-generated` nie widzi zapisów poza narzędziami `Write`, `Edit`, `NotebookEdit`:** `sed -i`, przekierowania i inne zapisy z `Bash` go omijają. Ostrzeżenie (`warn`) to przypomnienie, nie blokada.
- **`check-subagent-report` tylko zapisuje zdarzenie** (`warn`); nie blokuje i nie zmienia decyzji o uprawnieniach. Zgody (`GO`, `CP`, `READY`) pochodzą wyłącznie od użytkownika.

## Hook ochronny przed trwałym usunięciem

Część `hooks/guard-commands.py` (PreToolUse na `Bash`, tylko Claude Code, projekty z `feature` (także pod aliasem `agentflow`) lub `dispatch`) odmawia `rm -r`, `git clean -f`, `git worktree remove --force` i podobnych poza katalogami tymczasowymi, scratchpadem i katalogami odtwarzalnymi. To **siatka przeciw pomyłce, nie granica bezpieczeństwa**: czyta tekst komendy, więc nie widzi poleceń ukrytych w skryptach, nieznanych wrapperach ani zmiennych, których nie umie rozwiązać; dokumentacja hostów mówi to samo o regułach `Bash`. Wyjątek (`SKILLSYNC_GUARD_EXCEPT`) agent dopisuje sam po zgodzie użytkownika w oknie wyboru; hook nie umie sprawdzić zgody, zostawia ślad w `.skillsync/hook-exceptions.jsonl` (komenda do 500 znaków, bez redakcji sekretów: nie wpisuj sekretów do komend). Błąd hooka nigdy nie blokuje pracy.

**Ostrzeżenie o szerokich biegach testów.** Ten sam wpis PreToolUse niesie drugą część (`guard-tests.py`, `test-tools.json`; klucze `hooks.guard_tests`, `hooks.test_wrappers`, `hooks.wide_modules`, [manifest](manifest.md)), która nigdy nie blokuje: ostrzeżenie trafia do kontekstu modelu razem z wynikiem polecenia, czyli **po jego wykonaniu**, więc nie zapobiega samemu biegowi. Jej skuteczność na żywej sesji agenta nie jest zweryfikowana, a jak hak usuwania nie widzi narzędzia PowerShell. Wpis i skrypty zostają zawsze (niosą zakaz stopek narzędzi AI); wyłączenie obu części zostawia samą część zakazu; `doctor` raportuje stan. Granice: [test-run](../library/skills/workflow/references/test-run.md).

**Windows.** Hak czyta polecenie tak, jak czyta je powłoka narzędzia Bash (Git Bash): niecytowana ścieżka natywna (`rm -rf C:\work\x`) traci tam backslashe (naprawdę usuwa `C:workx` względem katalogu roboczego), więc hak widzi tę samą ścieżkę względną i odmawia. Rozpoznawane formy ścieżki do katalogu tymczasowego, scratchpada i katalogów odtwarzalnych: natywna w cudzysłowie (`'C:\work\x'`, `"C:\\work\\x"`), z ukośnikami (`C:/work/x`), forma Git Bash (`/c/work/x`, `/cygdrive/c/work/x`; mapowana na dysk; założenie: Git Bash, gdzie `/c` to dysk C:, nie WSL ani własny fstab), bez rozróżniania wielkości liter dysku i ścieżki, także nazwy 8.3 (po rozwinięciu `realpath`; przetestowane na Windows). Wątpliwość = odmowa: prefiks `\\?\`, UNC i wszystko, czego nie da się rozwiązać, zostaje odrzucone. Granica: hak jest podpięty tylko do narzędzia `Bash`; usunięcie wykonane narzędziem PowerShell (`Remove-Item -Recurse`, `rd /s`) poza `Bash` hak NIE widzi (w poleceniu `Bash` je rozpoznaje i odrzuca); parsera PowerShella nie ma.

## Wyniki testów na stronie statusu

Wyniki testów kroków planu (nazwy testów, grupy, komunikaty porażek) są **niemaskowane**: zapisują się surowo w `.skillsync/runs/<run>/tests/<krok>/<n>.jsonl` i pokazują się bez maski w polu `tests` strony statusu oraz w `GET /api/tests`, **na obu gniazdach, także gdy port odczytu jest widoczny w sieci lokalnej** (decyzja właściciela, 2026-11-16). Komunikat porażki może nieść treść plików, które czyta test (porównania snapshotów, wydruk zmiennych), więc to w praktyce treść plików projektu.

- **Wiersz polecenia testów jest zawsze maskowany, heurystycznie** (`test_events.mask_command` plus `redact`): pary `nazwa=wartość` (hasło, token, klucz...), dane w adresie URL, `Authorization`, **wartość po fladze** o nazwie zawierającej token, password, passwd, secret, key, pass, auth albo credential (`--token WARTOŚĆ`, `-api-key WARTOŚĆ`; nazwa zawierająca takie słowo, np. `--keyword`, też maskuje wartość: wolimy zamaskować niż ujawnić) oraz wartość po `-p` tylko dla narzędzi, w których to hasło (`mysql`, `mysqldump`, `mysqladmin`, `mariadb`, `sshpass`). **Nie maskuje:** sekretu jako argumentu pozycyjnego (`tool deploy TAJNE`), krótkiej flagi innego rodzaju (`-k WARTOŚĆ`, a `-p` przy pytest, mvn, ssh to wtyczka, profil i port), sekretu wczytywanego przez skrypt ze środowiska (nie ma go w linii), wartości zaczynającej się od `-` po fladze (`--token -abc`) ani reguły `-p`, gdy polecenie zaczyna się od `sudo`, `env X=y` albo `X=y mysql` (narzędzie nie jest wtedy pierwszym słowem). Sekrety do testów przekazuj przez zmienne środowiska, nie przez argumenty.
- Plik zdarzeń jest wejściem niezaufanym (może pochodzić z innej maszyny): czytnik pomija linię zbyt długą, zbyt głęboką, z liczbą spoza zakresu (NaN, nieskończoność, `1e999`) albo z typem, którego JSON nie ma; samotny surogat zamienia na U+FFFD; pola `start` i `end` zostawia tylko znane i właściwego rodzaju. Identyfikatory drzew git z pliku (`treeStart`, `tree`) przechodzą dalej tylko jako 40–64 znaki szesnastkowe (nic z pliku nie trafia do wiersza poleceń gita jako opcja). Opcjonalne `end.changed` (ścieżki zmienionych plików, do 5, każda do 300 znaków) jest maskowane przy zapisie i przy odczycie, także bez `tests.mask`, i trafia tylko do komunikatu bramki, nigdy do polecenia. Jeden zły plik albo katalog nie kosztuje strony: pole `tests.skipped` w odpowiedzi nazywa pominięte (`{path, reason}`, stały powód, bez treści pliku).
- Wyjątek od maski dotyczy tylko liści będących wynikiem testu (`id`, `name`, `group`, `msg`, nazwa grupy); wiersz polecenia, tytuł kroku, powód zakończenia i cała reszta strony przechodzą przez maskę (`dashboard_tests.finalize_status`). Pliki zdarzeń mają limity (10 000 testów, 8 MiB na bieg, komunikat do 2000 znaków).
- `tests.mask: true` w manifeście (maskuje każda wartość poza jawnym wyłączeniem, zob. [manifest](manifest.md#klucz-tests)) maskuje wyniki przy zapisie i w odpowiedzi strony (stare surowe zdarzenia zostają na dysku). Bez tego klucza wyniki są jawne także dla każdego, kto widzi port odczytu w sieci lokalnej (zob. niżej).
- `doctor` ostrzega (`runs-not-ignored`), gdy `.skillsync/runs` jest śledzony w git albo nieobjęty `.gitignore`. Kopia przebiegu jest dozwolona tylko na tej samej maszynie.
- Komunikatów porażek i nazw testów nie wolno wklejać do treści wychodzących poza maszynę (opis i komentarz PR, `## Wynik` planu, raporty w repozytorium, wiadomości między sesjami): podaje się liczniki, `id` testu i ścieżkę do pliku przebiegu.
- Bramka kroków `[T]` chroni przed POMINIĘCIEM testów, nie przed FAŁSZERSTWEM: `checkpoint --evidence` zapisuje skrót pliku i nie weryfikuje, kto go napisał.

## Strona statusu w sieci lokalnej

`dashboard serve` bez flag słucha gniazdem odczytu na `0.0.0.0` (port 5140), więc plany, specyfikacje, wyniki testów (jawne, bez maski, zob. wyżej; sekrety w pozostałej treści zredagowane heurystycznie) widzi każde urządzenie w sieci lokalnej, nieszyfrowanym HTTP. Gniazdo odczytu nie ma trasy zgód; zgody słuchają domyślnie na `127.0.0.1`. W nieufnej sieci zawęź odczyt: `--read-bind 127.0.0.1` albo `dashboard.local_only: true` ([manifest](manifest.md#sekcja-dashboard)). Konsola przy starcie ostrzega o nasłuchu poza loopbackiem. Opis flag i tokenu zgód: [cli](cli.md), model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md).
