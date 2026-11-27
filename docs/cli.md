# Referencja CLI

Kontrakt komend `skillsync`: składnia, flagi istotne dla użytkownika, kody wyjścia i kształt JSON. Procedury i dobór komendy do zadania
opisuje [workflow](workflow.md). Pełną listę argumentów każdej komendy zawsze potwierdzaj przez `skillsync <komenda> --help`.
Konfiguracja maszyny (warstwy, izolacja, strażnik): [configuration](configuration.md). Manifest projektu: [manifest](manifest.md).

`skillsync --version` wypisuje wersję generatora. Ten sam program uruchamia `python -m skillsync_workflow`. Polecenia, które
Skillsync zapisuje w artefaktach (zadanie VS Code w projekcie, launchery, hooki strażnika w warstwach), wywołują go tym samym interpreterem:
z checkoutu `python <checkout>/skillsync`, a z zainstalowanego pakietu `python -P -m skillsync_workflow`. Instalacja:
[installation](installation.md).

## Spis komend

| Komendy | Sekcja |
|---|---|
| `setup` | [Konfiguracja maszyny](#konfiguracja-maszyny-setup) |
| `completion` | [Uzupełnianie powłoki](#uzupełnianie-powłoki-completion) |
| `layers` (`init`, `add`, `use`, `update`, `list`, `check`, `translations`, `migrate`) | [Warstwy](#warstwy-layers) |
| `init`, `import`, `link`, `instructions`, `pull`, `push`, `watch`, `memory-sync`, `attribution`, `list`, `add`, `skills` | [Synchronizacja](#synchronizacja) |
| `doctor`, `route`, `readiness`, `review`, `validate`, `smart-test`, `map`, `suggest`, `knowledge`, `status`, `dispatch`, `feedback` | [Kontrola konfiguracji i kontekstu](#kontrola-konfiguracji-i-kontekstu) (profile `review`: [Profile review](#profile-review)) |
| `checkpoint`, `resume`, `metrics`, `agent-run`, `review-round`, `resources`, `plan-fresh`, `servers` | [Przebieg i pomiar](#przebieg-i-pomiar) |
| `retro`, `retro-scan`, `retro-decide` | [Retrospektywa](#retrospektywa) |
| `plan`, `spec` (`approve`, `archive`, `unarchive`, `snapshot`) | [Plany i specyfikacje](#plany-i-specyfikacje) |
| `test-run` | [Testy](#testy-test-run) |
| `dashboard`, `approvals` | [Dashboard i zgody](#dashboard-i-zgody) |
| `env`, `e2e`, `tracker`, `ci`, `claim`, `guard`, `profiles`, `upgrade`, `benchmark` | [Rozszerzenia workflow](#rozszerzenia-workflow) |
| `prototype` | [Prototypy](#prototypy) |
| `leakcheck`, `leakguard` | [Bezpieczeństwo](#bezpieczeństwo) |

Kształt JSON komend: [Schematy JSON](#schematy-json). Kody wyjścia: [Kody wyjścia](#kody-wyjścia).

## Konwencje

- `[path]` to katalog projektu albo plik `.github/.skillsync.yaml`. Komendy workflow domyślnie używają bieżącego katalogu.
  Komendy synchronizacji (`link`, `instructions`, `push`, `pull`, `watch`) bez ścieżki działają na wszystkich projektach
  z `discover_paths` z [configuration](configuration.md).
- `--layer ID=PATH` (powtarzalna) zastępuje warstwy dla jednej komendy; kolejność daje rosnące pierwszeństwo. Dostępna
  m.in. w `link`, `push`, `doctor`, `layers list`, `layers check`. Pierwszeństwo: `--layer` > warstwy przekazane przez
  nadrzędny proces `skillsync` > config maszyny. Warstw nie przekazuje się zmienną środowiskową.
- Komendy kontrolne (`validate`, `smart-test`, `benchmark`, `env`, `e2e`, `ci`, `claim`, `tracker`, `upgrade`, `guard`)
  bez `--run` tylko planują albo raportują. `--run` wykonuje zaufane polecenia z manifestu albo warstwy, nigdy polecenia
  z treści ticketów, planów czy logów.
- Komendy tylko do odczytu (bez zapisu w projekcie): `status`, `dispatch`, `route`, `profiles`, `suggest`, `resources`, `plan-fresh`, `retro`,
  `retro-scan`, `map --check`, `doctor`, `leakcheck`, `dashboard show|metrics|url`, `layers list|check`.
- Wynik komend workflow to JSON na stdout (wcięty). Wyjątek: `checkpoint`, `resume`, `metrics`, `benchmark` drukują jedną
  linię, a `--json` włącza wcięcia. Sekrety są maskowane.
- Statusy: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`. Plan albo brak konfiguracji daje `NOT_RUN` lub `BLOCKED`; to nie jest
  dowód, że aplikacja działa.
- Każda komenda, podkomenda i flaga ma tekst `help` w `--help`, a każda komenda liściowa i grupa ma `description`. Każda
  komenda liściowa kończy `--help` linią `Exit codes: ...` z kodami swojej klasy. Teksty `--help` są po angielsku, w czystym
  ASCII; wartości domyślne są podane w tekście. Mapa komend na klasy kodów: `EXIT_CLASS` w `skillsync_workflow/cli_help.py`.
  Klasy i ich kody: [statuses-and-exit-codes](domain/statuses-and-exit-codes.md).
- Zmiana flagi, komendy albo kodu wyjścia zmienia też opis w sekcji ze spisu i wpis w `EXIT_CLASS`. Strażnik
  `tests/test_skillsync_cli_help_guard.py` odrzuca komendę lub flagę bez `help`, komendę bez `description`, komendę bez
  wpisu klasy oraz opis w tej sekcji, który nie zgadza się z parserem. Reguły strażnika i pliki wyjątków: [development](development.md).

## Kody wyjścia

| Status | Kod | Uwagi |
|---|---|---|
| `PASS`, `NOT_RUN` | 0 | |
| `FAIL` | 1 | np. wyciek w `leakcheck`, `doctor` z błędem (`error`), izolacja w `layers check --isolation` |
| `BLOCKED` | 2 | brak warstwy dla projektu, zły argument, odmowa zapisu |
| `TIMEOUT` | 3 | upływ czasu oczekiwania: `approvals wait`, `feedback wait`, `prototype wait`, `prototype question wait`; w `theme_check.py` (skill `prototype`) kod 3 to `UNVERIFIED` (brak pomiaru): to dwa rozłączne znaczenia kodu 3 |
| błąd składni argumentów | 2 | usage na stderr, bez JSON |
| wyjątek (`skillsync: <komunikat>` na stderr) | 2 | także brak pliku configu |
| `test-run` | kod komendy przy `FAIL`; 2 przy kodzie komendy 0 bez `PASS` | wyjątek od mapowania statusów: pełny opis w sekcji [Testy](#testy-test-run) |

- Synchronizacje (`link`, `instructions`, `push`, `watch` i pozostałe) kończą się kodem 0 także przy ostrzeżeniach
  (`[warn]`, `[conflict]`); `[info]` to komunikat, nie ostrzeżenie. Czytaj stdout.
- `pull` działa jak `layers update` i `link`: przy brudnym drzewie warstwy albo braku fast-forward ta warstwa dostaje BLOCKED
  (np. `my: BLOCKED (dirty)`), pozostałe są aktualizowane, a `pull` kończy się kodem 2.
- Szczegółowe reguły kodów komend workflow: [workflow](workflow.md).

## Konfiguracja maszyny (`setup`)

`skillsync setup [--yes] [--dry-run] [--json] [--core-source URL] [--core-ref REF] [--org ID --org-source URL [--org-ref REF]] [--match GLOB]...`

Kreator konfiguracji maszyny. Nie ma własnej logiki warstw: wywołuje operacje `layers add`, `layers use` i `layers check`
(oraz dodanie reguły `**: [core]`, gdy config nie ma reguł). Tryb interaktywny,
gdy stdin jest terminalem i nie podano `--yes`; inaczej nieinteraktywny (bez pytań). Bez `--org` organizacja jest pominięta.

| Krok | Skutek | Wynik |
|---|---|---|
| `author-email` | sprawdza `git config --global user.email` przed dodaniem warstw (strażnik zapisuje adres przy `layers add`) | `unchanged`, `done` (interaktywnie, po podaniu adresu), `skipped` z ostrzeżeniem |
| `core` | `layers add core --kind core --subdir library --ref <core-ref>`; domyślny ref `v<zainstalowana wersja>` | `done`, `unchanged` (warstwa jest; inny ref = ostrzeżenie, bez zmiany), `blocked` |
| `org` | tylko przy `--org`: najpierw `git ls-remote` z `BatchMode` i limitem 30 s, potem `layers add <id> --kind org` (tylko do odczytu) | `done`, `unchanged` (ten sam id i source; bez `ls-remote`), `blocked` (brak dostępu, inny source lub rodzaj, osierocony katalog) |
| `rule` | bez organizacji: gdy `layer_rules` jest puste, dodaje `**: [core]`; z organizacją: `layers use <id> --match …` (domyślnie `**`) | `done`, `unchanged`, `blocked`; w `--dry-run` zamiast `done` jest `planned` (także dla `core` i `org`) |
| `completion` | po kroku reguł i przed `check`, tylko gdy setup nie jest `blocked`: instaluje uzupełnianie powłoki w tym samym procesie (jak `completion install --auto`, zob. niżej) | `done`, `unchanged`, `planned` (`--dry-run`), `skipped` (`SKILLSYNC_NO_COMPLETION`, brak powłoki, brak drzewa parsera albo porażka instalacji z ostrzeżeniem); nigdy `blocked` |
| `check` | `layers check` na katalogu pasującym do reguły (najpierw reguła organizacji; `**` → katalog tymczasowy, glob z `*` → tymczasowy podkatalog jego prefiksu, glob bez `*` → ten katalog). Gdy żaden istniejący katalog nie pasuje: `layers check` wszystkich warstw z neutralnego cwd i ostrzeżenie | `done` (PASS), `failed` (FAIL), `blocked` (BLOCKED) |

Wynik `check` pochodzi z `layers check`: FAIL (1) to problem zgłoszony przez warstwy, BLOCKED (2) to brak warunku
(np. brak `core`). Warunki i treść komunikatów `blocked` opisuje [installation](installation.md#rozwiązywanie-problemów).

Idempotencja: drugie uruchomienie z tymi samymi argumentami daje `unchanged` w krokach, które coś zmieniają (kroki `skipped`, np. przy
`SKILLSYNC_NO_COMPLETION`, zostają `skipped`), kończy się kodem 0 i nie zmienia configu (bajt w bajt). Przerwane uruchomienie (np. po `core`) dokańcza kolejne.

- `--dry-run`: pokazuje plan i wynik `git ls-remote` dla organizacji; nic nie klonuje i nie zapisuje configu.
- `--json`: schemat `{schema: 1, status, dry_run, steps: [{step, result, detail}], warnings, next}`; adresy są zredagowane (bez poświadczeń).
- Po zakończeniu w trybie tekstowym wypisuje `Next:` (dla `PASS`): link i sprawdzenie projektu, `skillsync pull`,
  aktualizacja narzędzia przez ponowne `install.sh` z nowego tagu, `skillsync completion install|uninstall` (uzupełnianie dla bash, zsh i PowerShella),
  `uv tool update-shell`. `setup` nie wykonuje tych poleceń (uzupełnianie instaluje własnym krokiem `completion`).

Statusy i kody (zob. [statuses-and-exit-codes](domain/statuses-and-exit-codes.md)):

| Status | Kod | Kiedy |
|---|---|---|
| `PASS` | 0 | wszystkie kroki bez blokady, `layers check` = PASS |
| `FAIL` | 1 | kroki bez blokady, `layers check` zgłasza problem (np. `symlink-in-layer`) |
| `BLOCKED` | 2 | zła flaga, brak dostępu do `--org-source`, nieznana wersja core bez zdalnego tagu (`--core-ref`), błąd git |

Wyjątek poza kreatorem (np. błąd odczytu configu) to `skillsync: <komunikat>` na stderr z kodem 2.

## Uzupełnianie powłoki (`completion`)

`skillsync completion bash|zsh|powershell` wypisuje na stdout skrypt uzupełniania (UTF-8, końce linii LF). Skrypt jest
generowany z drzewa parsera: podkomendy, zagnieżdżone podkomendy, flagi i ich wartości z `choices`. Flagi i argumenty,
których nazwa zawiera człon `path`, `project`, `file`, `dir` albo `directory`, dostają uzupełnianie plików powłoki.
Skrypt jest samodzielny: przy Tab nie uruchamia `skillsync` i nie czyta danych projektów.
Windows PowerShell 5.1 nie woła completera dla samego `-` ani `--`: wpisz co najmniej jedną literę po myślnikach (`skillsync link --d`); PowerShell 7 podpowiada flagi już po `--`.

`skillsync completion install|uninstall [--shell bash|zsh|powershell]… [--dry-run] [--json]`

- `--shell` (powtarzalna) zawęża wybór. Bez niej `install` obsługuje powłoki znalezione na PATH (`bash`, `zsh`, `pwsh`,
  `powershell`), a `uninstall` wszystkie trzy.
- `--dry-run` wypisuje każdy plik i blok, które powstałyby lub zostałyby usunięte, i niczego nie zapisuje.
- `--json`: `{schema: 1, status, dry_run, actions: [{shell, path, result, detail}], warnings}`. `result` to `created`,
  `updated`, `unchanged`, `removed`, `skipped` (plan z `--dry-run` albo wyłączenie zmienną) albo `failed`.
- `install --auto` jest dla wywołań z `install.sh` i kroku `setup`: respektuje `SKILLSYNC_NO_COMPLETION` (sprawdzaną przed liczeniem ścieżek, więc błędny `HOME`
  nie blokuje wyłączonego uzupełniania), a gdy nic się nie zmieniło, milczy. Przy `uninstall` flaga nie ma skutku.

| Powłoka | Skrypt | Plik startowy z blokiem |
|---|---|---|
| bash | `${XDG_DATA_HOME:-~/.local/share}/bash-completion/completions/skillsync` | brak, pliku rc nie zmieniamy |
| zsh | `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions/_skillsync` | `${ZDOTDIR:-~}/.zshrc` |
| PowerShell | `skillsync-completion.ps1` w `%LOCALAPPDATA%\skillsync\completion\` (Windows) albo w `${XDG_DATA_HOME:-~/.local/share}/skillsync/completion/` | profil `CurrentUserAllHosts` (`profile.ps1`) każdego znalezionego hosta: `pwsh` (PowerShell 7) i `powershell` (Windows PowerShell 5.1) |

Blok w pliku startowym leży między znacznikami `# >>> skillsync completion >>>` i `# <<< skillsync completion <<<`.
Zasady dla plików użytkownika:

- Przed pierwszą edycją powstaje kopia `<plik>.skillsync-bak` (bajt w bajt). Kolejne instalacje jej nie zmieniają, a
  `uninstall` jej nie usuwa.
- Zapis jest atomowy, z zachowaniem końców linii (CRLF w profilu zostaje CRLF) i BOM. Nowy profil PowerShella i skrypt
  PowerShella powstają jako UTF-8 z BOM.
- Dwa bloki albo niedomknięty znacznik to `failed` z komunikatem; plik nie jest wtedy zapisywany.
- Plik zaczynający się BOM-em UTF-16 albo UTF-32 lub zawierający bajty NUL (np. profil zapisany przez `Out-File` w Windows PowerShell 5.1) to `failed`
  (przy `install` i `uninstall`), bez zapisu i bez kopii: dopisanie UTF-8 uszkodziłoby go. Zapisz plik jako UTF-8 i powtórz polecenie.
- Cel tylko do odczytu (np. magazyn menedżera dotfiles; na Windows atrybut tylko do odczytu) to `failed` bez kopii i bez zmian. Symlink do pliku rc
  zostaje: zapis i kopia trafiają do celu linku. Wiszący symlink (cel nie istnieje) to `failed` z komunikatem: nie powstaje ani plik, ani katalog;
  `uninstall` zostawia taki link i zgłasza `unchanged`.
- Tuż przed zapisem plik jest czytany ponownie i porównywany z odczytem z początku (bajty, rozmiar, czas modyfikacji). Zmiana dokonana w międzyczasie
  przez kogoś innego to `failed` z komunikatem „changed during the operation, run it again”, bez zapisu; powstała już kopia `.skillsync-bak` zostaje.
- Zapis zastępuje plik atomowo (plik tymczasowy i `os.replace`) i przenosi na nowy plik właściciela oraz atrybuty rozszerzone (xattr; w miarę
  możliwości, błędy są ignorowane). Plik z kilkoma twardymi dowiązaniami (`st_nlink` > 1) jest zapisywany w miejscu, żeby nie zerwać pozostałych dowiązań.
- Plik własnego skryptu bez nagłówka `# skillsync completion (generated by …)` (zmieniony albo cudzy) nie jest nadpisywany
  ani usuwany: wynik `failed`, reszta `uninstall` idzie dalej.
- `uninstall` usuwa tylko blok i nasze pliki. Po usunięciu skryptu PowerShella usuwa puste katalogi `completion` i
  nadrzędny `skillsync` w katalogu danych; katalogów `bash-completion` i `zsh/site-functions` nigdy. Plik rc, który
  instalacja utworzyła i który po usunięciu bloku jest pusty, także znika. Plik rc, który po usunięciu bloku wraca do
  stanu sprzed instalacji, jest bajt w bajt taki sam jak przed instalacją.
- Ścieżki liczone są z `HOME` (na Windows z `USERPROFILE`, a gdy go brak, z `HOME`) i zmiennych `XDG_DATA_HOME`, `XDG_CONFIG_HOME` (tylko
  domyślny profil PowerShella poza Windows: `${XDG_CONFIG_HOME:-~/.config}/powershell/profile.ps1`), `ZDOTDIR`, `LOCALAPPDATA`.
  Ścieżka musi być bezwzględna, bez `..` i bez znaków sterujących. Skrypt PowerShella w bloku jest cytowany literalnie (apostrofy, także typograficzne
  U+2018, U+2019, U+201A i U+201B, są podwajane). Host dostaje jedno zapytanie `-NoProfile -NonInteractive` (limit 10 s) i odpowiada dwiema liniami ASCII:
  `P64:<base64 z bajtów UTF-8 ścieżki $PROFILE.CurrentUserAllHosts>` (kodowanie base64 sprawia, że strona kodowa hosta nie ma znaczenia, także dla
  polskich liter w ścieżce) oraz `POLICY:<efektywna ExecutionPolicy>`. Host na PATH bez użytecznej odpowiedzi (przekroczony czas, śmieci, ścieżka
  niebezwzględna, błąd dekodowania) jest pomijany: akcja `skipped` z powodem i ostrzeżenie, bez zapisu i bez zgadywania miejsca. Domyślne profile
  (Windows: `Documents\PowerShell\profile.ps1` dla pwsh i `Documents\WindowsPowerShell\profile.ps1` dla 5.1; poza Windows
  `${XDG_CONFIG_HOME:-~/.config}/powershell/profile.ps1`) służą tylko wtedy, gdy na PATH nie ma żadnego hosta.
- **ExecutionPolicy:** gdy efektywna polityka hosta to `Restricted` albo `AllSigned`, profil nie wczytałby niepodpisanego skryptu, więc dla tego hosta
  nie powstaje nic (ani profil, ani skrypt): akcja `skipped` z powodem i poleceniem `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, które
  wykonujesz sam (polityki nie zmieniamy za użytkownika), plus wpis w `warnings`. Przy `install --auto` (`install.sh`, `setup`) jest z tego jedna linia
  informacji, bez ostrzeżenia na stderr i bez statusu `FAIL`. Polityka nieznana (host nie podał jej) nie blokuje. `uninstall` polityki nie sprawdza.
- Profil `CurrentUserAllHosts` jest czytany przez każdy host PowerShella, także przez konsolę rozszerzenia VS Code (ona ma własny host, a więc własny
  profil hosta, którego profil `CurrentUserCurrentHost` konsoli systemowej nie pokrywa). Skrypt rejestruje uzupełnianie dla `skillsync` i `skillsync.exe`.
- Blok w `.zshrc` ma dwa warianty. Wariant A (rejestracja bez `compinit`): `fpath=(<katalog> $fpath)`, `autoload -Uz _skillsync`,
  `(( $+functions[compdef] )) && compdef _skillsync skillsync`; wybierany, gdy reszta pliku poza naszym blokiem zawiera osobne słowo `compinit` albo
  `oh-my-zsh`, `zinit`, `antidote`, `zplug`, `prezto` (późniejszy `compinit` znajdzie funkcję przez `fpath`). Wariant B: jak A, ale gdy `compdef` nie
  istnieje, blok sam wywołuje `compinit -i`. Zmiana wariantu przy ponownej instalacji to `updated` w miejscu, nadal jeden blok.
- bash: uzupełnianie nie obejmuje zapisu `--flag=value` (bash rozbija słowo na `=` przez `COMP_WORDBREAKS`); używaj `--flag wartość`. Dla kandydatów,
  które nie są ścieżkami, skrypt wyłącza tryb nazw plików (`compopt +o filenames`, gdy bash ma `compopt`), więc bash nie dopisuje `/` do nazw zgodnych
  z katalogiem w bieżącym katalogu.
- Koszt: start PowerShella z blokiem w profilu rośnie o ok. 38 ms (pomiar pwsh 7 na Linuksie: wczytanie przez kropkę skryptu ok. 50 KB).

Wyłączenie: `SKILLSYNC_NO_COMPLETION=1` pomija wywołania automatyczne (`install.sh`, krok `completion` w `setup`) z wynikiem
`skipped`. Wartości `1`, `true`, `yes`, `on` włączają wyłączenie; `0`, `false`, `no`, `off` i pusta wartość go nie włączają;
inna wartość daje ostrzeżenie i działa jak włączona. Jawne `skillsync completion install` działa zawsze.

Kody wyjścia: 0 `PASS` albo `NOT_RUN` (brak żadnej powłoki to `NOT_RUN` z ostrzeżeniem), 1 `FAIL` (część akcji `failed`),
2 `BLOCKED` (ścieżka niepoprawna albo katalog domowy nie do ustalenia: na Windows dopiero gdy brak i `USERPROFILE`, i `HOME`). Nieznana powłoka w `--shell`
albo w `completion <powłoka>` to błąd składni argumentów (kod 2).

Krok `completion` w `skillsync setup` wynika z tej samej logiki: `done`, `unchanged`, `planned` (`--dry-run`) albo `skipped`
z ostrzeżeniem. Porażka instalacji uzupełniania nigdy nie blokuje `setup`.

## Warstwy (`layers`)

Pomoc: `skillsync layers --help`. Model warstw: [layers-authoring](layers-authoring.md), składanie: [architecture](architecture.md).

| Komenda | Skutek |
|---|---|
| `layers init <katalog> --kind personal\|org [--id ID] [--json]` | tworzy szkielet warstwy (repo git z hookami strażnika) |
| `layers add <id> --kind core\|org\|personal --source <URL\|ścieżka> [--subdir D] [--ref R] [--path P] [--writable \| --read-only] [--translation] [--json]` | klonuje warstwę, rejestruje ją w configu i instaluje hooki; `--translation` to zgoda, że warstwa `org` jest tłumaczeniem rdzenia (`translation_of: core`) |
| `layers use <id> --match <glob>… [--position N] [--json]` | dodaje wpis `layer_rules` dla projektów pasujących do globu; gdy reguła o dokładnie tym samym `match` już istnieje, dopisuje warstwę do jej `use` (bez drugiej reguły; warstwa już obecna w `use` = bez zmian; `--position` odmowa BLOCKED); lista `use: [a, b]` jest zmieniana w tekście (komentarze poza listą zostają; komentarze wewnątrz listy rozbitej na linie giną; plik z CRLF dostaje LF), a lista blokowa, kotwica, tag i inne nietypowe układy powodują przepisanie pliku z kopią zapasową |
| `layers update [id] [--ref R] [--json]` | aktualizuje checkouty warstw (tag, gałąź, commit albo `pull`) |
| `layers list [--project P] [--isolation] [--layer ID=PATH] [--json]` | efektywna lista warstw dla projektu albo bieżącego katalogu |
| `layers check [--project P] [--isolation] [--layer ID=PATH] [--json]` | wersje, `requires`, `core_min`, hooki, kolizje, `.orphans`, tekst zakazanej atrybucji narzędzi AI w instrukcjach, skillach, rolach i `workflow/` warstwy (`attribution-text`, FAIL); z `--isolation` tylko kontrolę izolacji |
| `layers translations [--project P] [--update] [--json]` | sprawdza warstwy tłumaczeń względem rdzenia (pliki, nagłówki, `translations.lock.yaml`); `--update` najpierw zapisuje `translations.lock.yaml` każdej zapisywalnej warstwy tłumaczenia z bieżących plików rdzenia (po przeglądzie tłumaczeń); JSON: `{schema, status, translations: [{layer, language, files, changed}], findings}`, kody jak `layers check` |
| `layers migrate [--apply \| --dry-run] [...] [--json]` | przenosi config maszyny na `layers` i `layer_rules`; domyślnie dry-run |

Flagi `layers migrate` z pomocy: `--apply`, `--dry-run` (domyślnie), `--layer-source ID=PATH`, `--kind ID=KIND`,
`--match ID=GLOB`, `--manifests`, `--project PATH`, `--finish`. Migrację istniejącej maszyny opisuje [upgrading](upgrading.md).

Kody wyjścia warstw: `layers check` zwraca PASS/NOT_RUN (0), FAIL (1) przy izolacji, braku hooka, kolizji albo tekście zakazanej atrybucji,
BLOCKED (2) przy cyklu, braku `core`, niespełnionym `requires` albo `core_min`, braku `version` lub `core_min`.
`layers list` nie sprawdza hooków.
`layers init|add|use|update|translations|migrate` zwracają BLOCKED (2) przy złym argumencie, konflikcie `id` albo brudnym drzewie
przy `update`.

## Synchronizacja

Pomoc: `skillsync <komenda> --help`.

| Komenda | Skutek |
|---|---|
| `init [katalog] [--id ID] [--json]` | tworzy warstwę personal (odpowiednik `layers init --kind personal`) |
| `import <projekt> [--dry-run] [--layer ID=PATH] [--to-layer ID]` | kopiuje skille i instrukcje projektu do warstwy (domyślnie personal), tworzy manifest, jeśli go brak |
| `link [path] [--dry-run] [--layer ID=PATH]` | warstwy → projekt: skille, instrukcje, role i pliki hostów (pełna lista: [architecture](architecture.md)); w projekcie z Claude Code zapisuje też hak PreToolUse na `Bash` (`.claude/hooks/guard-commands.py` zawsze odmawia poleceń z stopką lub trailerem narzędzia AI (z kopią wzorców `attribution-patterns.json`) i ustawia klucz `attribution` w `.claude/settings.local.json`; odmawia też trwałego usunięcia, `guard-tests.py` z `test-tools.json` ostrzega o szerokim biegu testów poza `skillsync test-run`) i jego wpis w `.claude/settings.local.json`, według kluczy `hooks.guard_delete`, `hooks.guard_tests`, `hooks.test_wrappers` i `hooks.wide_modules` ([manifest](manifest.md)); wpis i skrypty zostają zawsze (zakaz atrybucji nie ma przełącznika), a `hooks.guard_delete` i `hooks.guard_tests` wyłączają tylko swoje części; granice ostrzeżenia: [test-run](../library/skills/workflow/references/test-run.md) |
| `attribution [--user] [--dry-run] [--print-managed]` | ustawienie hosta wyłączające atrybucję narzędzia AI (puste `commit` i `pr`): bez flag raportuje stan pliku użytkownika (0 wyłączona, 1 włączona lub brak), `--user` zapisuje je w `~/.claude/settings.json` (jawnie, pozostałe klucze zostają, kopia zapasowa), `--print-managed` wypisuje obiekt do pliku ustawień zarządzanych (nigdy zapisywany). W projektach robi to `link` |
| `instructions [path] [--dry-run] [--layer ID=PATH]` | przebudowuje instrukcje projektu (`.github/copilot-instructions.md`, `CLAUDE.md`, `AGENTS.md`, artefakty Codexa) |
| `push [path] [--dry-run] [--to-layer ID] [--layer ID=PATH]` | projekt → warstwy właściciela plików (szczegóły: [layers-authoring](layers-authoring.md)) |
| `pull [path]` | aktualizuje wszystkie warstwy (`layers update`), potem `link` |
| `watch [path] [--interval S]` | pętla: po zmianie w warstwach wykonuje `link`; zwykle uruchamiana przez usługę |
| `memory-sync [--dry-run]` | kopiuje pliki z sekcji `memory_sync` configu na hosty ([configuration](configuration.md)) |
| `list [--layer ID=PATH]` | wypisuje skille z kolumną warstwy, która dostarcza skill |
| `add <projekt> <skill> [--layer ID=PATH]` | dopisuje skill do manifestu i wykonuje `link` |
| `skills add\|list\|index\|remove` | szkielet skilla, lista z członkostwem, indeks katalogu, usunięcie jawnego wpisu z manifestu |

Flagi `skills`: `add <nazwa> [--add-to-project <projekt>] [--layer ID=PATH] [--to-layer ID]` (`--add-to-project` bierze ścieżkę projektu, do którego manifestu trafi nowy skill), `list [--json] [--layer ID=PATH] [--status]`,
`index [--check] [--layer ID=PATH] [--stdout]`, `remove [--json] [--layer ID=PATH] [--no-link]`.

Usługa `watch` (uruchamiana przez `skillsync-watch.service`) nie widzi `CLAUDE_CONFIG_DIR` z sesji interaktywnej. Interaktywny
`link` z tą zmienną wskazującą katalog z cudzym `statusLine` może usunąć nasz wpis paska (z `[warn]`); `watch` przy najbliższej
zmianie w warstwach zainstaluje go ponownie, aż do kolejnego `link` w tej samej powłoce. Szczegóły: [domain](domain/statusline-i-output-styles.md).

Dwie różne flagi warstw:

- `--to-layer ID` (`push`, `import`, `skills add`) wskazuje warstwę, do której trafiają nowe pliki (domyślnie `personal`).
  Zapis do warstwy `core` wymaga `--to-layer core`, bo ta opcja jest też zgodą na zmianę plików rdzenia.
- `--layer ID=PATH` (powtarzalna) zastępuje na czas jednego wywołania cały zestaw warstw podanymi katalogami: działają
  tylko warstwy z flag (kolejność flag = rosnące pierwszeństwo), a `layer_rules` z configu są pomijane. `core` też trzeba
  wtedy podać.

## Ścieżki w raportach

Każde pole ścieżki w raportach JSON ma na Windows ukośnik `/`: `root` i `project` (`status`, `dashboard show`, `skills list`, `layers *`, `dispatch`), `composed_root`, `config`, `manifest`, `backup`, `layers[].path` i `layers[].root` (`layers *`), `budgets[].path` (`doctor`), `log` (`workflow validate`), `checkpoint` (`checkpoint`), `targets` (plan wycofania `upgrade`), `knowledge init` (`created`, `existing`, `skipped[].path`), `route` (`lessons[].path`, `domain[].path`, `index`), `doctor` (`findings[].path`), `leakcheck` (`directory`, `repo`, `hooks[].path`), `env` (`launchers[]`), `e2e` (`recorded`, `scenario`, `report`), `map` (`path`) oraz komunikat `prototype serve` o pliku użytkowników; test `tests/test_skillsync_report_paths.py` pilnuje, żeby nowe pole nie wróciło do postaci natywnej. Pomocnik: `skillsync_workflow/pathfmt.py` (`posix`). Wyjątek: komendy do wykonania (zadanie `skillsync: link`, komendy hooków,
`selfcmd`, `cwd` w `checks[]` raportów `smart-test` i `validate`) i ścieżki, które produkt sam otwiera, zostają w postaci natywnej. Na Linuksie i macOS pola ścieżek się nie zmieniają.

## Kontrola konfiguracji i kontekstu

| Komenda | Skutek |
|---|---|
| `doctor [path] [--layer ID=PATH] [--json]` | statyczna walidacja konfiguracji; `error` → FAIL (1), same ostrzeżenia → PASS (0), nieczytelny config albo config z kluczem `library:` → BLOCKED (2). Nie testuje aplikacji; raportuje też stan haka (zakaz stopek i podpisów narzędzi AI, usuwanie, ostrzeżenie o testach): brak haka, brak lub uszkodzone dane zakazu atrybucji, brak klucza `attribution` w `settings.local.json` (w projekcie nigdy niepodlinkowanym: tylko ostrzeżenie) oraz tekst zakazanej atrybucji w warstwie lub w wygenerowanych instrukcjach to `error` (FAIL); nieaktualny wobec manifestu lub generatora, ustawienia wspólne i użytkownika z włączoną atrybucją to ostrzeżenie; aktywny z trybem to `info` (zob. `link`) |
| `route`, `readiness`, `review`, `validate`, `smart-test` | dobór kontekstu, gotowość planu, checklista review, plan i wykonanie kontroli (`guard`: wiersz w sekcji [Rozszerzenia workflow](#rozszerzenia-workflow); wspólne flagi: `--files PLIKI…`, `--base REV`, `--risk low\|medium\|high`, `--profile`, `--run`; `route` także `--topics`, `--tools-all`, `--tools-history`; `smart-test --profile quick\|final`; `validate --profile P --check ID`). Profil i listy `review`: sekcja „Profile `review`” poniżej |
| `map [--check]`, `suggest`, `knowledge init`, `status`, `dispatch [--run-id ID] [--pulse]` | mapa repo (`.skillsync/map.md`; `--check` kończy się kodem 1, gdy mapy brak albo jest nieaktualna), sugestie skilli do dodania lub usunięcia, szkielet `docs/domain` i `docs/lessons` (nigdy nie nadpisuje), odczyt stanu planów, przebiegów i dzierżaw (JSON), stan dyspozycji (`--pulse`: ustalenia pulsu) |

### Profile `review`

`review` zwraca też `profile` (`docs`, gdy zmiana dotyka wyłącznie dokumentacji (`docs/**` bez plików kodu), plików `.md` skilli, `CHANGELOG*` albo notatek wydania, `prototyp`, gdy zmiana mieści się w całości w katalogu prototypu (`prototype.root` z manifestu, domyślnie `docs/prototypes`), inaczej `kod`; `--profile kod|docs|prototyp` wymusza profil (angielskie aliasy `code` = `kod`, `prototype` = `prototyp`; raport zostaje po polsku)), `docsFiles` i `prototypeFiles`; pozycje listy prototypu dostaje także zmiana mieszana, która dotyka plików prototypu; ścieżki i pozycje docs nadpisują `workflow.review.docs_paths` i `workflow.review.docs_checklist`, ścieżki prototypu `workflow.review.prototype_paths`, a listę `workflow.review.prototype` (jedno źródło: `library/workflow/defaults.yaml`) rozszerzają warstwy, `workflow/projects/<slug>.yaml` i manifest: pozycje są dopisywane w kolejności rdzeń, warstwy, projekt, manifest, równe dosłownie pomijane (zostaje pierwsza), a pozycji rdzenia nie da się usunąć

**Niezlinkowane skille.** Skille z manifestu nie są w gicie: `link` instaluje je w `.github/skills`, `.claude/skills` i `.agents/skills`, więc świeży klon albo `git worktree add` ich nie ma, dopóki nie uruchomisz `skillsync link <katalog>`. Sprawdza to jedna tania funkcja (`linkage_status`: tylko `exists`/dowiązanie i jedno `git ls-files -s` na host, bez hashowania; za niezlinkowany liczy się brak, dowiązanie wiszące i plik, który git śledzi jako dowiązanie, a nie odtworzył; skill bez źródła w warstwach, wyłączony hostem (`claude: false`) lub wykluczony z codexa (`codex.exclude_skills` z warstw i z manifestu) nie wchodzi do oczekiwanych; nieaktualną kopię lub dowiązanie nadal wykrywa tylko `doctor`). `doctor` zwraca jeden zbiorczy błąd na host (przed błędami per skill), `route` i `status --json` dodają pola `linkage` i `linkageNote` tylko przy problemie (zlinkowany projekt: wynik bez zmian), hak `session-context` dopisuje linię o skillach claude (`status` ładuje do tego warstwy; brak configu lub błąd warstw = brak pola, status nadal PASS). Pozostałe polecenia (poza `link`, `pull`, `watch`, `doctor`, `route`, `status` i wywołaniami z `--json`) wypisują w takim projekcie jeden wiersz `skillsync: Skills from the manifest are not linked …` na **stderr** (stdout i kod wyjścia bez zmian; projekt wskazuje argument `path` albo katalog roboczy; projekt zlinkowany płaci tylko odczyt manifestu i kilka `stat`; `SKILLSYNC_NO_LINK_NOTICE=1` wyłącza). Hak nie pokrywa świeżego klonu (instaluje go `link`); pokrywają go `doctor`, `route` i wiersz na stderr.

**Narzędzia i zastępowanie.** `route` zwraca dodatkowo sekcję `tools` (rekordy indeksu narzędzi dopasowane do `--topics`/`--files`; najwyżej 10, pole `tools_truncated`; `tools_note`, `tools_warnings`; klucza nie ma, gdy nigdzie nie ma indeksu), flaga `--tools-all` dodaje wszystkie aktywne rekordy, `--tools-history` listę `tools_hidden` (zastąpione, usunięte, ukryte przez wyższą warstwę). `doctor` dodaje ostrzeżenia `superseded` (wpis `supersedes` warstwy pasuje do plików projektu; wyjątek: manifest `superseded_keep`) oraz kontrolę `docs/tools/index.yaml` (schemat, `path`, nagłówki skryptów); `layers check` kontroluje `tools/index.yaml` warstw. `layers update` i `pull` drukują linię `superseded tools: N newly declared by updated layers; run skillsync doctor in each project`, a JSON raportu `layers update` ma addytywne pole `superseded` (`{<id warstwy>: N}`), gdy aktualizacja zadeklarowała nowe wpisy. Żadna z tych komend niczego nie uruchamia. Procedura agenta i schemat: [narzędzia i zastępowanie](domain/tools-and-superseding.md).

**Zgłoszenia zmian do warstwy.** `skillsync feedback new --file <szkic.json>`, `targets`, `trust <warstwa>` (tylko z terminala: człowiek wpisuje nazwę organizacji), `draft F<n>` (podgląd ze skanem wycieków i deduplikacją), `approve F<n> --word "WYŚLIJ <sha12>"` (zapis zgody po słowie wpisanym przez użytkownika), `send F<n> [--replace]` (nowy element albo komentarz do otwartego; odmawia bez wpisu zgody z pełnym sha treści, w worktree i w sesji karty, gdy proces ustawi `SKILLSYNC_CARD_ALIAS`; `approve` też), `list`, `wait F<n> [--timeout S] [--interval S]` (tylko odczyt: czeka na ważną zgodę szkicu z podglądem; 0 = zgoda `{approval, sha12, source}`, 3 = `NOT_RUN` po czasie, 2 = błąd użycia, np. szkic nieznany, bez podglądu, nieaktualny lub wysłany; domyślnie 1800 s i co 5 s). Szkice i wyniki w `.skillsync/feedback/` GŁÓWNEGO checkoutu. Wszystkie komendy drukują JSON; kody wyjścia jak w innych komendach (BLOCKED = 2). `skillsync tracker --provider azure-feedback` jest odrzucany (dostawca wewnętrzny). Procedura i granice: [zgłoszenia zmian](domain/feedback-channel.md).

Lista plików (`--files` albo zmienione pliki z git) w `route`, `readiness`, `review`, `smart-test`, `validate` i `guard` jest sprawdzana na samej ścieżce: rodzic jest rozwiązywany fizycznie, ostatni człon nie. Przechodzi ścieżka względna albo bezwzględna leżąca w projekcie, także dowiązanie jako OSTATNI człon (tak listuje je git; dowiązanie do katalogu poza projektem też, z ukośnikiem końcowym lub bez), plik usunięty i plik w nieistniejącym katalogu. Odrzucane (BLOCKED): ścieżka bezwzględna poza projektem, `..` wychodzące z projektu i dowiązanie w środku ścieżki, które wskazuje poza projekt. Na POSIX `..` po dowiązaniu (`a/link/..`) oznacza prawdziwy rodzic celu, więc wychodzi poza projekt i jest odrzucane; na Windows `..` jest składane leksykalnie, zanim system spojrzy na dowiązania (ścieżka `a\link\..\x` to `a\x`), więc zostaje w projekcie, zgodnie z tym, co system otworzy. Ścieżki, które polecenie otwiera (plan, środowisko, role, prototypy), są sprawdzane z podążaniem za dowiązaniami. `guard` nie otwiera zmienionego dowiązania, tylko odnotowuje je w `skipped`.

Pola workflow w tej grupie:

- `status` (JSON, schemat bez zmian): rekord planu z poprawną linią `Spec:` ma `preImplement`: `required` (spec ma niepustą
  sekcję `Kontrakty`, `Migracja` lub `Dane`), `sections`, `run`, `report` (ścieżka raportu), `verdict` (najgorszy werdykt
  z `pre-implement*.md`), `warning` (tekst albo `null`). `null` w polu oznacza brak poprawnej linii `Spec:` albo nieczytelny spec. `warning` jest tylko
  ostrzeżeniem dla planów `draft`, `approved` i `in_progress`; nie blokuje zatwierdzeń.
- `plan approve` i `spec approve` (JSON): pole `warnings` (lista, może być pusta) z tym samym tekstem ostrzeżenia. Status
  i kod wyjścia nie zależą od ostrzeżeń.
- `readiness --run-id <id>`: oprócz wyniku na stdout zapisuje `.skillsync/runs/<id>/readiness.md` (pierwsza linia
  `Readiness: PASS|BLOCKED`) i dodaje `reportFile` do JSON. Bez flagi zachowanie się nie zmienia. Niepoprawny id albo
  dowiązanie w ścieżce przebiegu daje `BLOCKED` (exit 2) z `reason`, bez zapisu.
- Nazwy sekcji gotowości z configu mają pary polska↔angielska: `Cel`/`Goal`, `Kryteria`/`Criteria`, `TESTY`/`TESTS`, `Wiedza`/`Knowledge`, `Ryzyka`/`Risks`, `Kontrakty`/`Contracts`, `Migracja`/`Migration`. Plan z nagłówkami angielskimi przechodzi tę kontrolę tak samo jak polski; klucze w `workflow/defaults.yaml` zostają polskie. Tabela tokenów: [workflow](workflow.md#aliasy-angielskie-tokenów-protokołu).

Komendy opisuje [workflow](workflow.md). `doctor` sprawdza m.in. skille z manifestu (także frontmatter: nieznany klucz, zły typ lub wartość to ostrzeżenie, nigdy błąd), sekcje `dispatch` i `prototype`,
spójność instrukcji z plikami, budżety bajtów oraz pola ról wymagające nowszego Claude Code (jedna pozycja `info`, bez wpływu na kod wyjścia; bez uruchamiania `claude`).

**Hooki Claude Code (`link` i `doctor`).** `link` instaluje, aktualizuje i usuwa wpisy hooków `session-context`, `guard-generated`, `check-subagent-report` oraz argument `--subagent-commit` strażnika poleceń wg kluczy `hooks.session_context`, `hooks.guard_generated`, `hooks.subagent_report`, `hooks.subagent_commit` ([manifest](manifest.md)); skrypty trafiają do `.claude/hooks/`, a `.skillsync/` jest w `.gitignore`, gdy hook jest włączony. Wartość spoza dozwolonych = wyłączony hook i ostrzeżenie. `claude: false` usuwa nasze wpisy hooków (closure, strażnik poleceń i trzy nowe hooki; wpis Stop testów ze `settings.json` i jego skrypt zostają) oraz nasze skrypty; cudze wpisy zostają. `doctor` raportuje stan każdego hooka (zainstalowany, tryb, zgodność części stałej wpisu z tym, co zapisze `link`; kopia skryptu różna od biblioteki = ostrzeżenie) oraz liczbę ostrzeżeń `subagent-report` z ostatnich 14 dni (pozycja `info`, bez wpływu na kod wyjścia). Zasady i tabela zdarzeń: [architecture](architecture.md), sekcja „Hooki Claude Code”.

**Pasek statusu i styl (`claude.statusline`, `claude.output_styles`, opt-in; domyślnie `false`).**
- `link` przy `claude.statusline: true` kopiuje `statusline.py` do `.claude/hooks/` i dopisuje `statusLine` do `.claude/settings.local.json`.
  Gdy znajdzie cudzy `statusLine` (w `.claude/settings.local.json`, `.claude/settings.json`, `~/.claude/settings.json` albo w
  `$CLAUDE_CONFIG_DIR/settings.json`, gdy zmienna jest ustawiona bezwzględnie) albo `disableAllHooks: true` w którymkolwiek z nich,
  nie zapisuje i wypisuje `[warn]` z nazwą pliku i priorytetem. Gdy nasz wpis był już zainstalowany, usuwa go wraz z kopią skryptu,
  z tym samym `[warn]`. Plik ustawień, którego nie da się odczytać, to `[warn]` bez zapisu. Plik śledzony przez git nie jest zapisywany.
- `claude: false` albo `claude.statusline: false` usuwa nasz wpis i kopię skryptu (kopię tylko naszą: z sygnaturą `skillsync-managed` albo zgodną z biblioteką;
  edytowana kopia bez sygnatury zostaje, a `link` wypisuje `[warn]`, gdy usunął nasz wpis; cudzy wpis i cudzy plik zostają bez komunikatu).
- `claude.output_styles: true`: `link` kopiuje `karta.md` do `.claude/output-styles/` jako kopię zarządzaną; cudzy plik albo
  dowiązanie pod tą nazwą nie jest zastępowane (`[warn]`). Lokalnie zmieniona kopia zostaje, a `link` wypisuje zwykłe `[info]`.
  Wyłączenie (`false` albo `claude: false`) usuwa wyłącznie ten plik, i to tylko gdy jest nietknięty albo bajtowo zgodny z biblioteką
  i nie jest śledzony przez git; cudze pliki i dowiązania w katalogu nigdy nie są ruszane, a bez klucza `link` niczego tam nie czyta.
  `doctor` przy `output_styles: true` podaje `info` z poleceniem `/output-style karta`. Generator nie ustawia `outputStyle`.
- `doctor` przy `claude.statusline: true`: `warning` dla cudzego paska, `disableAllHooks` lub nieczytelnego pliku (komunikat wymienia plik
  i priorytet); `warning` dla brakującego skryptu albo kopii różnej od biblioteki, gdy wpis jest nasz (brak samego wpisu nie jest zgłaszany);
  `info` z wymaganiem `refreshInterval` (Claude Code ≥ 2.1.97; starszy go pomija) i z informacją, że wyświetlanie paska w rozszerzeniu VS Code
  jest niepotwierdzone.
- `doctor`, klucze `claude:`: `error` dla wartości nie-bool w `enabled`, `statusline` i `output_styles`; `warning` dla nieznanego klucza
  i dla wartości `claude` niebędącej ani mapą, ani boolem (Claude zostaje włączony). Bez nowych kluczy `doctor` nie dodaje żadnego wpisu.
- Ręczna próba wyświetlania (CLI i VS Code): [development](development.md), sekcja „Ręczna próba”.

- `route` zwraca pole `ceremony {value, source: manifest|default|invalid[, error]}` (klucz manifestu `workflow.ceremony`: `full` | `demo`;
  brak klucza = `full`, `source: default`; `error` tylko przy `invalid`, wtedy `value: null`). `doctor` zgłasza błąd dla innej wartości.
- `route` zwraca pole `specs {tracked, archive, source}` (klucz `workflow.specs`, tryb nieśledzony; `source`: `manifest`, `gitignore`, `default` albo `invalid`, przy `invalid` `tracked: null` i `error`). Wykrycie: `git check-ignore --no-index -v` dla `docs/specs/x.md` i `docs/plans/x.md`, tylko reguły z plików `.gitignore` drzewa repozytorium, także zagnieżdżonych (negacja, `.git/info/exclude` i globalny `.gitignore` się nie liczą). Projekt, który nie jest korzeniem repo, dostaje `default`. To pole nie jest listą `specs` z `status`. `doctor` zgłasza błąd dla pary `tracked: true` i reguły obu katalogów, ostrzeżenia dla `tracked: false` bez reguły, reguły tylko jednego katalogu i śledzonych plików w `docs/specs` lub `docs/plans` (z komendą `git rm --cached`; pliki zostają na dysku).
- `spec snapshot`, ograniczenia: brak blokady współbieżnej (równoległe wywołania mogą dać fałszywy `BLOCKED`, rozwiązywany przez `--replace`); nieczytelny `docs/.saved.json` bez `--replace` nie jest ruszany, a cel z innym sha daje `BLOCKED`; `redact` nie maskuje tokenów typu `ghp_` w nazwie podanej w `--spec`.
- `readiness --plan <plik>` przy planie z fazami `[UI]` (znacznik w tabeli kroków, [prototypes](prototypes.md#akceptacja)): FAIL bramki prototypu
  nie dodaje wpisu do `issues` (`prototypeGate` zostaje w raporcie); BLOCKED bramki i faza `[UI]` bez linii `Prototyp:` i bez obejścia właściciela
  dają BLOCKED. Opcjonalne pola raportu:

  | Pole | Znaczenie | Kiedy |
  |---|---|---|
  | `uiPhases` | id faz `[UI]` | zawsze, gdy plan ma fazy `[UI]` |
  | `nonUiPhases` | id pozostałych faz | razem z `uiPhases` |
  | `uiPhasesWaiting` | fazy `[UI]` czekające na akceptację (równe `uiPhases`) | tylko przy FAIL bramki |
  | `warnings` | lista napisów, nie blokuje | `[UI]` w wierszu tabelowym poza rozpoznaną tabelą kroków; „bramka pominięta przez właściciela” w planie z fazami `[UI]` |

## Przebieg i pomiar

| Komenda | Skutek |
|---|---|
| `checkpoint <path> --run-id ID --step KROK --status PASS\|FAIL\|BLOCKED\|NOT_RUN --next TEKST [--evidence PLIK…] [--final] [--cause-key KLUCZ --owner instruction\|skill\|hook\|route\|test\|tool] [--cause TEKST] [--attempt N] [--denied N] [--inherited N] [--duration S] [--cost X [--cost-unit WAL]] [--no-tests POWÓD] [--manifest PLIK]`; `resume <path> …` | zapis i sprawdzenie kroku przebiegu; `--final` oznacza rekord końcowy przebiegu (bez zapisanej retrospektywy kod 0 z ostrzeżeniami `retro-quality-missing:` i `retro-quality-unverified:` w `warnings`) i dopisuje albo odświeża [linię `Zamknięcie:`](workflow.md#dowód-zamknięcia-w-planie-linia-zamknięcie) w jedynym planie o tym `Przebieg:` (wynik JSON: `planClosure: {path, action: added\|updated\|none}`, `path` null, gdy nie wskazano planu; nieudany zapis to ostrzeżenie `plan-closure:` w `warnings`, bez zmiany kodu wyjścia; komunikat o zmianie pliku idzie na stderr), `--cause-key` (`[a-z0-9][a-z0-9-]{0,47}`) i `--owner` (wymaga `--cause-key`) opisują przyczynę poprawki, `--denied` i `--inherited` to przyrosty w tym rekordzie (>= 0), `--cost-unit` to trzyliterowa waluta (domyślnie USD), `--evidence` to ścieżki plików w projekcie (nie opis). `resume` zwraca 2 przy nieaktualnych dowodach albo kroku innym niż PASS. Krok testowy (`[T]`) z `--status PASS` wymaga dowodu z `test-run` albo `--no-tests POWÓD`; gdy drzewo zmieniło się w trakcie biegu, komunikat nazywa do 5 ścieżek z `end.changed`, zob. `library/skills/workflow/references/test-run.md`; `--manifest PLIK` bierze manifest spoza repozytorium (np. publiczny rdzeń bez `.github/.skillsync.yaml`), a korzeń projektu i zapis `.skillsync/runs/` zostają w `<path>`; bez flagi zachowanie bez zmian; zły plik to błąd z nazwą flagi |
| `metrics [path] [--json] [--classify]` | agregaty zapisanych pomiarów (`runs`, `checkpoints`, koszty); `--classify` dodaje deterministyczną klasę każdego przebiegu (`clean`, `recovery`, `unexplained`, `incomplete`, `no_data`). Wynik z `--classify` zawiera wolny tekst `cause` i nie maskuje `id`: nie publikuj go (zakaz wolnego tekstu dotyczy tylko `retro`). Tylko odczyt, kod 0 |

Szczegóły: [workflow](workflow.md).

### Biegi podumysłów i rundy review

Dziennik biegów `agents.jsonl` czyta strona statusu ([dashboard-contract](dashboard-contract.md)); model rund: [rundy-review-i-dziennik-biegow](domain/rundy-review-i-dziennik-biegow.md).

#### `agent-run`

Składnia: `agent-run start|eta|end <projekt> --run-id ID ...`

Dziennik biegów podumysłów `.skillsync/runs/<run-id>/agents.jsonl` (jedna linia na zdarzenie, tylko pola z list zamkniętych, bez tekstu sesji); pisze go wyłącznie orkiestrator. `start --role ROLA --kind review|run [--profile spec|plan|code|final|docs|prototype|retro|pre-implement] [--step KROK] [--round N] [--eta HH:MM]` drukuje `agent=a-<k>` (katalog przebiegu musi istnieć, `agent-run` go nie zakłada; numer z całego pliku, pod blokującą blokadą `.agents.lock`; wcześniejszy otwarty bieg tej samej pracy kończy się jako `ABORTED`), `eta --agent ID --eta HH:MM` przesuwa termin, `end (--agent ID | --role ROLA [--step] [--round]) --result WYNIK [--report plik.md]` zamyka bieg (wynik z listy roli: reviewer `PASS`/`CHANGES_REQUIRED`/`BLOCKED`/`NOT_RUN`, verifier `CONFIRMED`/`REFUTED`/`UNDETERMINED`, test-runner `PASS`/`FAIL`, pozostałe `DONE`/`FAIL`; każda `BLOCKED`/`NOT_RUN`/`ABORTED`). Odmowa (kod 2): zła rola, rodzaj, profil, krok, runda, godzina, `run-id`, symlink, `end` bez otwartego biegu. Strona statusu czyta ten dziennik (trwające biegi, ETA, wyniki).

#### `review-round`

Składnia: `review-round start|freeze <projekt> --kind spec|plan|code [--step KROK] [--run-id ID] [--spec PLIK] [--plan PLIK]`

Runda review bez pamięci sesji (nakładka na `agent-run`; osobna od `review <projekt>` z checklistą). `start [--eta HH:MM]` wybiera przebieg (`--run-id`; dla `spec` jak strona: `Przebieg:` speca, katalog z linii `Plan:`, nazwa pliku speca, pierwszy istniejący, inaczej nowy; dla `plan` i `code` `Przebieg:` bieżącego planu albo `--plan`), zakłada brakujący katalog przebiegu, nadaje numer rundy N z plików raportów (`spec-review-N.md`, `plan-review-N.md`, `code-review-N.md`, `code-review-<krok>-N.md`; dziennik nie wpływa na N), zamyka wcześniejszą otwartą rundę tego profilu i kroku jako `ABORTED` (ten sam N) i drukuje `round=N agent=a-<k> report=<ścieżka>`. `freeze (--file PLIK | stdin) [--result PASS|CHANGES_REQUIRED|BLOCKED|NOT_RUN]` zapisuje raport jako `<prefiks>-N.md` (nigdy nie nadpisuje: przy istniejącym pliku bierze następny wolny numer), wynik z `--result` albo z linii `Wynik: …` (też `Result:`, w pierwszych 40 liniach raportu, z apostrofami lub bez; kilka różnych wyników = odmowa), zamyka bieg w dzienniku; `freeze` działa także bez `start`: bieg dostaje wtedy w dzienniku `"measured":false` (czas jego trwania jest nieznany, nie 0 s: pole `seconds` biegu w statusie i w `dashboard metrics` to null, strona pisze „czas nieznany”; kto liczy średnie czasów biegów, pomija wiersz z `seconds: null`). `--step` tylko dla `code` (`[A-Za-z0-9.]+(-[A-Za-z0-9.]+)*`). Etykieta `Review ×N` na stronie liczy te pliki. Kod 2: zły argument, raport pusty albo ponad 1 MiB, brak wyniku, nie do wybrania przebieg (`podaj --run-id`), symlink.

#### `resources`

Składnia: `resources [projekt] [--json] [--max N]`

Ile podumysłów można jeszcze uruchomić (tylko odczyt, kod 0; 2 przy złym użyciu). Pola JSON (`schema: 1`): `host{os, cpus, load1|null, memAvailableMiB|null, cpuBusy|null, sources{load, mem}}`, `running[{agent, role, step|null, startedAt}]` (otwarte biegi `agent-run` ze wszystkich `.skillsync/runs/*/agents.jsonl`, bez starszych niż 90 minut: porzucone), `max` (domyślnie 5, `--max` 1-5), `allowed`, `limits{byMax, byLoad, byMemory}`, `reason`. `byMax = max - running`; `byLoad = floor(0,75 * cpus - load1)` (Linux i macOS: `os.getloadavg`; na Windows `floor(0,75 * cpus - busy * cpus)` z `cpuBusy`, czyli zajętości procesora z dwóch próbek `GetSystemTimes` w odstępie 0,5 s, więc komenda trwa o ok. 0,5 s dłużej); `byMemory = floor((pamięć dostępna - 3072 MiB) / 1024 MiB)` (Linux `MemAvailable` z `/proc/meminfo`, macOS `vm_stat`: strony wolne, spekulatywne i nieaktywne, Windows `GlobalMemoryStatusEx`); `host.sources` nazywa użyte źródła (`load`: `loadavg`, `cpu-sample` albo `null`; `mem`), a `null` zostaje tylko tam, gdzie źródła nie dało się odczytać. `allowed = max(0, min(znane limity))`, ale co najmniej 1, gdy nic nie trwa (cudzy load nie zatrzymuje pracy całkiem); `null` nie ogranicza, `reason` mówi, czego brak. Ograniczenia: `running` widzi tylko biegi zapisane przez `agent-run` w tym projekcie (zaniża obciążenie); `cpu_count` i `MemAvailable` nie widzą cgroup kontenera; próg pamięci jest niezależny od protokołu stacji (`heavy-run.lock`, 10 GiB na ciężkie biegi) i od `dispatch.max_parallel`: to limit podumysłów jednej sesji, nie dzierżawa zasobu. Bez manifestu projektu raport powstaje tak samo (liczą się tylko dzienniki biegów).

#### `plan-fresh`

Składnia: `plan-fresh [projekt] [--json]`

Czy plany `docs/plans/*.md` ze statusem `in_progress` (z linią `Przebieg:` wskazującą katalog `.skillsync/runs/<id>` albo `<id>.<paczka>`) są aktualne wobec najnowszej linii `checkpoints.md` i otwartych biegów `agents.jsonl` (tylko odczyt, kod 0 zawsze; 2 przy złym użyciu). JSON (`schema: 1`): `plans`, `findings[{code, plan, step, detail, blocking}]`, `blocking`, `idle{allowed, steps, plan}`. Kody blokujące: `step-reported-unchecked` (element `zrobione:` zaczyna się od id kroku i zawiera sha commitu, 7-40 znaków hex z co najmniej jedną literą a-f, a krok ma `[ ]`) i `step-running-unmarked` (otwarty bieg `kind=run` z `step: X` z planu, a `w toku:` najnowszej linii nie ma X ani jego fazy: `F1` pokrywa `F1.2`, albo brak `checkpoints.md`). Informacyjnie `idle-capacity`: co najmniej dwa kroki `[ ]` pierwszej fazy z otwartymi krokami poza `w toku:` i `allowed >= 1` z `resources` (zależności kroków nie są parsowane). Okno: linia `checkpoints.md` liczy się, gdy plik jest nowszy niż plik planu i nie starszy niż 6 godzin; zdarzenia `agents.jsonl` (ISO ze strefą) podlegają tym samym warunkom. Id spoza kroków planu są ignorowane przy kodach blokujących, a plany z id czysto literowymi (`A`...`E`) są niewidoczne (gramatyka id strony statusu). Informacyjnie (`blocking: false`, kod wyjścia bez zmian, jedno znalezisko na kod i plan z polem `count` i trzema fragmentami) zgłasza też zapisy, których parser nie czyta: `step-id-unparsed` (linia kroku planu, np. `- [ ] FT.1`, bez czytelnego id), `checkpoint-id-skipped` (element najnowszej linii `w toku`/`zrobione` bez id na początku albo z drugim id poza nawiasem) i `checkpoint-id-unknown` (id na początku elementu, które nie jest krokiem żadnego planu `in_progress` tego przebiegu, np. skrót `K2`), każde z podpowiedzią poprawnego zapisu (gramatyka id i kody: `docs/dashboard-contract.md`). Plan i przebieg muszą leżeć w tym samym projekcie: sesja w worktree bez `docs/plans` niczego nie wykryje. Wywołuje je hook Stop (`--cli` wpisu) i `doctor` (ostrzeżenie dla planu ze znaleziskami blokującymi i po jednym ostrzeżeniu na kod i plan `in_progress` dla trzech kodów o id; `readiness <plan>` dodaje ten sam rodzaj ostrzeżenia do pola `warnings`, bez zmiany `status`).

#### `servers`

Składnia: `servers list [--root PATH]... [--kind dashboard|prototype] [--json]`, `servers stop [--root PATH]... [--port N] [--all] [--kind dashboard|prototype] [--timeout SEK] [--force] [--dry-run] [--json]`

Działające serwery tego użytkownika i ich łagodne zatrzymanie przed aktualizacją narzędzia (uruchomiony serwer blokuje `uv tool install --force` na Windows). `list` (tylko odczyt, kod 0) czyta pliki stanu serwerów strony statusu (`dashboard serve`, katalog stanu użytkownika) i serwerów prototypów (`prototype serve`, plik `server-<port>.json` w projekcie; szukany dla projektów z `--root`, bieżącego projektu i projektów serwerów strony statusu) i wypisuje rodzaj, port, pid, projekt i żywość; martwy wpis (proces nie działa) jest oznaczony. Nigdy nie wypisuje tokenu zgód. `stop` wymaga wyboru (`--root`, `--port` albo `--all`; bez wyboru nic nie robi, kod 2) i przed wysłaniem sygnału sprawdza proces wobec wpisu (linia poleceń i czas startu); obcy proces pod tym pid nie dostaje sygnału (`refused`; Windows: patrz niżej). Zatrzymanie łagodne to `SIGTERM` (Windows: `CTRL_BREAK`); polecenie czeka na zniknięcie pliku stanu (domyślnie 15 s, `--timeout`); serwer, który się nie zatrzymał, ma status `still-running` i nie jest zabijany; `--force` kończy go po ponownej kontroli tożsamości. Wpis martwego procesu jest usuwany (`dead-swept`). Wynik ostrzega, gdy serwer przyjmuje zgody (GO, CP, READY): oczekująca zgoda przepada. `--dry-run` niczego nie wysyła i kończy kodem 3. Kody `stop`: 0 zatrzymano (albo nie było czego), 1 serwer nadal działa, 2 brak wyboru, odmowa albo zły argument, 3 `--dry-run`. Tożsamość to tokeny wiersza poleceń (`skillsync`, rodzaj i `serve` jeden po drugim) oraz czas startu procesu przed czasem zapisu wpisu; brak któregokolwiek czasu to `refused` (przy dużym skoku zegara po starcie serwera prawdziwy serwer może zostać odrzucony: wtedy zatrzymaj go ręcznie). `--root` dopasowuje dokładnie katalog projektu serwera (serwer z podkatalogu lub innego worktree wymaga jego ścieżki), `--port` bez `--kind` obejmuje oba rodzaje na tym porcie; `--all` nie znajdzie serwera prototypów w projekcie, którego nie znano z `--root`, bieżącego katalogu ani serwerów strony statusu. `--dry-run` bez żadnego serwera też kończy się kodem 3. Windows: serwer, który nie odpowiada, ale jest procesem z wpisu, jest traktowany jako działający (nie jest uznawany za martwy); `CTRL_BREAK` trafia do wszystkich procesów konsoli serwera, więc serwery uruchamiaj we własnych oknach konsoli (inaczej zdarzenie dostaną też inne procesy tej konsoli, w tym powłoka); proces `stop` ignoruje to zdarzenie na czas wysłania. Na Windows nie ma taniego sprawdzenia pid: serwer, który nie odpowiada i którego pid należy do obcego procesu (albo nie należy do żadnego), jest uznawany za martwy, więc `stop` usuwa tylko jego plik stanu (`dead-swept`) i nic nie wysyła. Pokazowy serwer prototypów (tryb demo, tylko do odczytu) nie zapisuje pliku stanu, więc `servers` go nie widzi: przed aktualizacją zatrzymaj go ręcznie.

## Retrospektywa

Komendy na rekordach checkpointów i rejestrze `docs/retro/decisions.yaml`. Model klas i propozycji: [retro-proposals](domain/retro-proposals.md), [run-classes](domain/run-classes.md).

| Komenda | Skutek |
|---|---|
| `retro [path] [--run-id ID \| --all] [--all-states] [--json]` | raport tylko do odczytu z `.skillsync/runs`: klasa przebiegu (wynika wyłącznie z rekordów checkpointów), rubryka, kandydaci zmian i propozycje. `--run-id` zawęża do ID i `ID.*` oraz powiązanych katalogów raportów, `--all` obejmuje wszystkie projekty z `discover_paths` (bez `--run-id` i ścieżki), `--all-states` dodaje propozycje `rejected` i `applied` (domyślnie tylko nierozstrzygnięte). Pola identyfikacyjne są filtrowane po znanych wzorcach tokenów, więc nie wpisuj tajemnic w identyfikatory (np. `--cause-key`). Kody: `PASS` i `NOT_RUN` (brak danych, nie sukces) 0, `BLOCKED` 2; nigdy `FAIL` |
| `retro-scan [path] --run-id ID --signals PLIK [--manifest PLIK] [--cross-project] [--json]` | tylko odczyt: zestawia plik sygnałów bieżącego przebiegu (`--signals`: ścieżka względna do korzenia projektu, w `.skillsync/runs/<run-id>/`, do 256 KB; `run_id` w pliku musi równać się `--run-id`) z `retro-signals.json` i `retro-decisions.json` innych przebiegów oraz sekcją `proposals:` rejestru. Wynik: grupy sygnałów (propozycje do utworzenia), `repeat_of`, `attached_to`, ostrzeżenia. `--cross-project` liczy sygnał `repo: library` jako powtórzony także w innym projekcie z `discover_paths` (wynik zawiera tylko liczby). Kody: `PASS` 0 (także z ostrzeżeniami i `truncated: true`), `BLOCKED` 2 (brak `--run-id`, zły plik `--signals`, niepoprawny rejestr) `--manifest PLIK` czyta manifest z pliku poza repozytorium (publiczne repozytorium rdzenia go nie ma; jak w `agent-run`, `review-round` i `checkpoint`) i zastępuje nim też manifest głównego checkoutu powiązanego worktree. |
| `retro-decide [path] --cause-key KLUCZ --decision approved\|rejected [--owner ROLA --case TEKST] [--replace]` | zapis decyzji użytkownika w `docs/retro/decisions.yaml` (atomowo, data UTC dnia zapisu). `approved` wymaga `--owner` (`instruction`, `skill`, `hook`, `route`, `test`, `tool`) i `--case` (do 300 znaków), `rejected` ich nie przyjmuje; istniejący klucz wymaga `--replace` |
| `retro-decide [path] --proposal RUN/P<n> --decision rejected\|applied [--ref SHA]` | decyzja propozycji retrospektywy: `applied` wymaga `--ref` (pełny sha); zapis najpierw w `retro-decisions.json` przebiegu, potem w sekcji `proposals:` rejestru (`writes` w wyniku mówi, który zapis przeszedł); w worktree `BLOCKED`. Wyklucza `--cause-key`, `--owner` i `--case` |

Kody `retro-decide`: `PASS` 0, `BLOCKED` 2 (JSON z `reason`, bez zmian na dysku przy odmowie walidacji).

## Plany i specyfikacje

Operacje zmieniają linię `Status:` pliku (`abandon` i `unarchive` także linię `Porzucony:`; `archive` usuwa starą linię porzucenia); zgodę (słowo użytkownika) zapisuje się osobno przez `approvals record`. Akty i procedura: [workflow](workflow.md).

| Komenda | Skutek |
|---|---|
| `spec approve <projekt> <plik> [--expect-sha SHA] [--json]` | zgoda „GO plan”: status speca `draft` → `approved` i usunięcie dopisku „czeka na … GO plan” (inne dopiski zostają) |
| `plan approve <projekt> <plik> [--expect-sha SHA] [--json]` | zgoda „GO implement”: status planu `draft` → `approved` i usunięcie dopisku „czeka na GO implement” |
| `plan archive <projekt> <plik> [--expect-sha SHA] [--json]` | status planu `done` → `archived` (poza kolejkami, bez `needsHuman`); zapis pod blokadą `.skillsync/locks/plan-status.lock` wspólną z przyciskami strony (zajęta po 5 s: `BLOCKED`, kod 2) |
| `plan unarchive <projekt> <plik> [--expect-sha SHA] [--json]` | status planu `archived` → `done`; plan z linią `Porzucony:` wraca do `approved` i traci linię |
| `plan abandon <projekt> <plik> [--expect-sha SHA] [--json]` | status [porzuconego](workflow.md#plan-porzucony) planu `approved` → `archived` z linią `Porzucony: <data UTC>`; inny plan = `BLOCKED` (kod 2); zapis pod tą samą blokadą i z audytem `.skillsync/audit/plan-status.jsonl` (`via: cli`); wynik: `reason`, `plan`, `from`, `to`, `sha`, `warnings` (np. nie zapisany audyt `done`) |

`--expect-sha` to 40-znakowe sha bloba gita pliku w wersji pokazanej użytkownikowi (inny plik = odmowa). Wynik JSON ma pole `warnings` (ostrzeżenie `preImplement`; nie zmienia statusu ani kodu). Kody: `PASS` 0, `BLOCKED` 2. `spec snapshot` opisano niżej.

### `spec snapshot`

Składnia: `spec snapshot <projekt> --run-id <id> [--spec PLIK] [--manifest PLIK] [--replace] [--json]`

Kopiuje spec (z linii `Spec:` planu o `Przebieg: <id>`) i plan do `.skillsync/runs/<id>/docs/specs|plans/` oraz, przy `workflow.specs.archive`, do `<archiwum>/<projekt>/<id>/specs|plans/` (`<projekt>` = `project.name`, a bez niego nazwa katalogu repozytorium; znaki spoza `[A-Za-z0-9._-]` zamienione na `-`). Zapis przez plik tymczasowy i rename, z compare-and-swap osobno dla każdego celu: sha ostatniego zapisu na parę (cel, plik) jest w `.skillsync/runs/<id>/docs/.saved.json`. Cel zmieniony przez kogoś innego albo bez wpisu (z innym sha niż źródło) = `BLOCKED` dla tego celu; `--replace` nadpisuje (decyzja człowieka). Bez planu i bez `--spec` = `BLOCKED`, nic nie zapisano. Kody: 0 PASS, 1 FAIL (archiwum niedostępne; cel przebiegu zapisany), 2 BLOCKED; przy mieszanym wyniku wygrywa najwyższa waga (2 > 1 > 0). Treść plików nie trafia do komunikatów

Składnia: `spec snapshot <projekt> --run-id <id> --check [--to KATALOG] [--json]`

Tylko odczyt: porównuje sha speca i planu przebiegu z kopiami w `.skillsync/runs/<id>/docs/`, w archiwum (gdy jest) i w `KATALOG` (korzeń kopii projektu; sprawdzane jest `<KATALOG>/.skillsync/runs/<id>/docs/specs|plans/`). `PASS` (0) gdy wszystkie cele zgadzają się; `BLOCKED` (2) z listą rozbieżnych celów, gdy archiwum jest niedostępne, albo w połączonym worktree bez celu poza nim (ani archiwum, ani `--to`). Procedura usuwa worktree tylko po `PASS` celu poza worktree

## Testy (`test-run`)

```
skillsync test-run <projekt> [--run ID] [--step ID] [--junit PLIK|KATALOG ...] [--allow-no-count POWÓD] [--allow-skips]
                   [--format auto|none|NAZWA] [--timeout S] -- <polecenie...>
skillsync test-run <projekt> --import PLIK --step ID --source-sha SHA [--run ID]
```

Uruchamia komendę testów projektu przez wspólny runner i zapisuje jej zdarzenia dla strony statusu (`.skillsync/runs/<run>/tests/<krok>/<n>.jsonl`); wyjście komendy zostaje bez zmian. Opis adapterów: [test-adapters](test-adapters.md).

**Kody wyjścia**

| Sytuacja | Kod |
|---|---|
| `PASS` (znana dodatnia liczba wykonanych testów, zasada `validate`) | 0 |
| `FAIL` (kod komendy ≠ 0) | kod komendy |
| limit czasu / brak programu | 124 / 127 |
| bieg z kodem komendy 0 bez zapisanego statusu `PASS` | 2, z linią stderr `test-run: exit code 2: …` |
| `BLOCKED` przed startem (zły argument, kilka kroków w toku, brak przebiegu) | 2 |
| `--import` poprawnie zaimportowanego biegu (niezależnie od jego statusu) | 0 |

Kod 2 komendy nie różni się po samym kodzie od `BLOCKED`: rozróżnia linia stderr (`test-run: step <id> (<źródło>), attempt <n>: <STATUS>` albo `BLOCKED: …`).

**Bieg z kodem 0, ale bez `PASS`** (zapisany `BLOCKED`): brak dodatniej liczby wykonanych testów; testy pominięte bez zgody; „kod 0, ale wyjście raportuje niezaliczone” (przy źródle liczników `regex` także zakotwiczone podsumowania Playwrighta `  N failed` i `  N interrupted`, wiersz `Tests` z `N failed` w Vitest i `Tests: N failed,` w Jest; dla źródeł `junit` i `adapter` te wzorce nie są czytane, liczby daje raport albo adapter); niewykonane specyfikacje Karmy; albo brak zapisu statusu po błędzie zapisu lub przerwaniu.
Kod 0 zdejmują tylko `--allow-no-count <powód>` (brak liczników) i `--allow-skips` (pominięte). Zero wykonanych testów, „kod 0, ale wyjście raportuje niezaliczone” i niewykonane specyfikacje Karmy nie mają zdjęcia żadną flagą.

**Wybór przebiegu i kroku**

- `--run ID` (także z `--import`) wymaga śladu przebiegu w `.skillsync/runs/`: katalogu `<ID>/` (nie dowiązania) albo zwykłego pliku `<ID>.json` (nazwa dokładna). Bez śladu `BLOCKED` (2) z komunikatem `--run <ID>: no such run in .skillsync/runs/ (runs: …)` (bez żadnych przebiegów: `--run <ID>: no run in .skillsync/runs/`; przy `--import` z podpowiedzią skopiowania przebiegu albo założenia go przez `skillsync checkpoint`); nic się nie zapisuje i polecenie nie startuje.
- `--run` bez wartości = jedyny przebieg projektu (katalog; rekord `<id>.json` i dowiązanie się nie liczą).
- Krok bez `--step` = jedyny krok „w toku” z checkpointów (kilka = `BLOCKED`, 2).
- Lista testów pochodzi z raportu JUnit XML lub TRX (`--junit`; dla `vstest.console` także z miejsc podanych w poleceniu: `/ResultsDirectory`, `/Logger:trx;LogFileName=`, domyślnie `TestResults`; `--junit` ma pierwszeństwo).

**Adapter stanu pojedynczych testów (`--format`)**

- `auto` (domyślnie) rozpoznaje narzędzie w poleceniu, także za `npx`, `timeout`, `env`, `python -m` i podobnymi, i pisze na stderr przed startem jedną linię: `test-run: adapter <nazwa> (live <running|completion|counts>)` (po średniku bywa podpowiedź, jak uzyskać listę pojedynczych testów, np. `--reporter=verbose` dla Vitest) albo `test-run: no adapter for <narzędzie> (known: ...); pass --format <nazwa>, or --format none to silence`.
- `none` wyłącza adapter, nazwa wymusza go: `angular`, `cargo-test`, `cargo-test-serial`, `cypress`, `dotnet`, `karma`, `lines`, `node-test`, `playwright`, `pytest`, `tap`, `unittest`, `vitest`. Nieznana nazwa to błąd z listą (np. `--format ?`). `lines` i `tap` są tylko jawne (`auto` ich nie wybiera); `lines` czyta linie `PASS <nazwa>` / `FAIL <nazwa>` w kolumnie 0.
- Bez adaptera bieg jest zapisany z liczników podsumowania i raportu.
- `vstest.console` (narzędzie `vstest`, także `cmd /c "…"`) wybiera adapter `dotnet`; bez TRX liczby pochodzą z bloku podsumowania konsoli (EN i PL).

**Pozostałe flagi**: `--timeout S` to limit czasu w sekundach (domyślnie 3600). `--import PLIK --source-sha SHA` przenosi plik zdarzeń zapisany na innej maszynie (walidacja, bajty bez zmian, kopia bez `.git` podaje commit).

## Dashboard i zgody

| Komenda | Skutek |
|---|---|
| `dashboard show <projekt> [--json] [--plan <rel>]` | stan projektu jako JSON `schema: 1` ([dashboard-contract](dashboard-contract.md)); tylko odczyt; teksty w języku z manifestu `language` (pole `lang`; `texts` tylko dla języka innego niż `pl`) |
| `dashboard url <projekt> [--port N] [--host NAZWA] [--read] [--lan] [--all-tabs] [--json]` | adres strony działającego `dashboard serve` projektu (z pliku stanu w katalogu danych maszyny; bez tokenu, zgody daje sesja `approver`); w projekcie git bez `--port` wskazuje serwer rodzaju `project` i zwraca adres zakładki podanego katalogu, `/?tab=<id>` (tylko jego zakładka); gdy serwera projektu nie ma, odpowiada jedyny serwer tego katalogu (`standalone` albo starszej wersji, adres `/`); `--all-tabs`: adresy wszystkich zakładek serwera, adres podanego katalogu ostatni (w `--json` pole `tabs`; dla człowieka, karty tej flagi nie używają); `--port N`: dowolny serwer projektu na tym porcie, także rodzaju `standalone` (adres `/`) i serwer bez zakładek; `--read`: adres gniazda odczytu, bez fragmentu; `--lan`: adresy gniazda odczytu dla innych urządzeń, jeden na linię (`http://<IP>:<port>/`; w `--json` pole `lanUrls`); `--json`: obiekt `{schema, status, url, port, pid[, tab, tabs, role, readUrl, lanUrls]}` albo `BLOCKED {code, reason}`; adres idzie na stdout (ostatnia linia), uwagi na stderr; `BLOCKED` (exit 2): kody w [tabeli](#kody-blocked-serve-i-url); zob. [dashboard-contract](dashboard-contract.md) |
| `dashboard stop <projekt> [--port N] [--timeout S] [--force] [--dry-run] [--json]` | łagodne zatrzymanie serwerów strony statusu tego projektu (zob. [`dashboard serve --detach` i `dashboard stop`](#dashboard-serve---detach-i-dashboard-stop)) |
| `dashboard metrics <projekt> <run-id> [--json] [--plan <rel>]` | czasy podkroków `Fn.k`, liczby rund i porażek przebiegu; podkrok bez danych jest oznaczony jawnie |
| `approvals record <projekt> --word "GO plan\|GO implement\|CP\|READY" --run <run-id> --source cli\|ide [--sha SHA] [--file PLIK] [--target X] [--branch Y] [--expect-plan-state draft|approved|in_progress|done|archived]` | zapis zgody użytkownika w `.skillsync/runs/<run-id>/approvals.jsonl`; dla `CP` i `READY` dopisuje pole `planState` (status planu przebiegu w chwili zgody), a duplikat to ta sama para (sha, `planState`) jako ostatni wpis słowa w przebiegu; `--expect-plan-state <status>` (tylko `CP`/`READY`): zapis odmawia (kod 2, `status: BLOCKED`, `code: plan_state_changed`, nic nie zapisane), gdy status planu przebiegu odczytany pod tą samą blokadą pliku jest inny niż podany albo nieznany (brak planu z `Przebieg:`, brak linii `Status:`, status spoza listy, plan ponad limit rozmiaru); komunikat: `approvals record: plan state changed (expected <X>, current <Y>); consent not recorded` (`<Y>` = `unknown` przy nieznanym); wartość spoza listy i słowo GO z flagą to zwykłe `BLOCKED` z JSON-em, bez `code` |
| `approvals wait …` | czekanie na zgodę (odczyt tego samego pliku), opis w [workflow](workflow.md); przy `tracked: false` zamknięcie planu nie zmienia HEAD, a `wait` nie zna `planState`, więc zawsze podawaj `--since <czas STOP>` |

- `approvals record` jest jedynym zapisem zgód: blokada plikowa, odmowa duplikatu (to samo słowo i `sha` w przebiegu), odmowa przy zmienionym statusie planu (`--expect-plan-state`, sprawdzenie przed testem duplikatu), maskowanie
  sekretów. Słowo wpisuje użytkownik; agent tej komendy nie wywołuje.
- Kody: `0` = `PASS`, `2` = `BLOCKED` (zły argument, odmowa zapisu, brak projektu lub przebiegu, port zajęty); `approvals wait`
  kończy się `3` przy przekroczeniu czasu. Strona i dokument skilla: skill `dashboard` w bibliotece.

### `dashboard serve`

Składnia: `dashboard serve <projekt> [--port N] [--read-port N --approve-port M] [--read-bind ADR] [--approve-bind ADR] [--read-host H] [--approve-host H] [--host H] [--no-approvals] [--allow-remote-approvals] [--allow-plain-login] [--local-only] [--trust-proxy --proxy-address ADR] [--secure-cookies] [--public-url URL] [--prototype-url URL] [--prototype-port N] [--single-origin] [--page-port N] [--plan <rel>] [--session-url URL] [--poll S] [--verbose] [--standalone] [--all-tabs] [--json] [--detach]`

Strona statusu z żywym odświeżaniem (SSE), przyciskami zgód i logowaniem kontem. Bez flag portów: jedno gniazdo strony na `5140` (wszystkie interfejsy, `0.0.0.0`). Odczyt jest dla każdego, kto dociera do gniazda; zgody (`GO plan`, `GO implement`, `CP`, `READY`) daje tylko zalogowana sesja konta z rolą `approver`, lokalnie albo przez TLS. Zgody nie mają tokenu ani drugiego hasła. `--port N`: jedno gniazdo na `127.0.0.1` (tryb jednego portu); `--read-port`/`--approve-port`: jawne numery dwóch gniazd (niżej); `--poll S`: odstęp sprawdzania zmian plików (domyślnie 1, najmniej 0,2); `--verbose`: log każdego żądania na stderr.

- **Bez flag portów** `serve` otwiera jedno gniazdo strony na `5140`. Port jest per projekt (dozwolone 5100–5199), żeby strony różnych projektów nie kolidowały; port 5141 nie jest używany. Nagłówek `Host` jest sprawdzany na każdym endpoincie. Gniazdo przyjmuje loopback, literały IP interfejsów tej maszyny, własną nazwę maszyny (`socket.gethostname()`, jej krótką formę i `<krótka>.local`; dopasowanie tekstowe, bez DNS), nazwy z `--read-host` i `--approve-host` oraz nazwę z `--public-url`. Dodatkowe nazwy: `--read-host NAZWA` (powtarzalne, bez portu; `*` tylko z `--read-port`). Zgody nie zależą od nazwy `Host`: decydują sesja i to, skąd przyszło żądanie.
- **Zgody w tym samym gnieździe.** Logowanie i zgody idą przez gniazdo strony. Zgoda przyjmowana jest tylko z loopbacku albo przez TLS (zaufany proxy z `--trust-proxy` i `--proxy-address`, który wysyła `X-Forwarded-Proto: https`); zgoda ze zdalnego peera bez TLS wymaga `--allow-remote-approvals` i jest odradzana, a sesja `approver` po zwykłym HTTP z sieci i tak nie powstaje. Wymagania: nagłówek `X-Status-Board: 1`, `Content-Type: application/json`, dokładny `Origin` (ten sam `Host`, `https` przez TLS), `Sec-Fetch-Site: same-origin`, ciasteczko sesji i token CSRF sesji (nagłówek `X-Skillsync-CSRF`), ciało do 4096 bajtów. Kolejność i kody odmów: [dashboard-contract](dashboard-contract.md), sekcja „Kolejność odmów na gnieździe zgód”.
- **Sesja należy do nazwy hosta.** Ciasteczko sesji działa tylko pod nazwą, pod którą nastąpiło logowanie, a bez `--public-url` strona nie przekierowuje na jeden adres kanoniczny: `localhost`, `127.0.0.1`, nazwa maszyny i jej adres IP mają osobne sesje (każdy adres wymaga własnego logowania). To znane zachowanie. Z `--public-url` żądanie dokumentu pod inną nazwą niż loopback i bez nagłówków pośrednika jest przekierowywane na ten adres; nazwy loopback nie są przekierowywane.
- **Konto `approver`** ma hasło co najmniej 14 znaków, wpisywane w terminalu. Sesja tej roli wygasa po 2 h bez zapisu i najpóźniej po 12 h; logowanie działa tylko na origin strony statusu. Założenie konta: [upgrading](upgrading.md), sekcja „Przejście 1.20.1 → 2.0.0”.
- **Tryb dwóch gniazd** uruchamia `serve` z `--read-port N` i/lub `--approve-port M` (flagi nie mają wartości domyślnej). Rola wynika z **gniazda**, nie z nagłówka `Host`. Gniazdo odczytu (`--read-port`) nie ma żadnej trasy zapisu (każda metoda zapisu = 404) i nie zawiera zgód. Gniazdo zgód (`--approve-port`) ma stronę i trasy sesji (`/_login`, `/_api/v1/*`), a zgody przyjmuje od sesji `approver` z tym samym łańcuchem odmów co gniazdo strony. Samo `--read-port` albo samo `--approve-port` uruchamia jedno gniazdo. `--port` nie łączy się z tymi flagami (BLOCKED, kod 2). Model zaufania: [domain/dashboard-server.md](domain/dashboard-server.md).
- Nasłuch strony: gniazdo domyślne (albo gniazdo odczytu `--read-port`) słucha na `0.0.0.0` (każde urządzenie w sieci lokalnej). Origin prototypów (port strony plus 2, `--prototype-port`) słucha na tym samym adresie co strona, więc komentarze i API pytań są dostępne tam, gdzie strona.
- **Adres spoza pętli zwrotnej** (np. `--read-bind 192.168.1.10`, `dashboard.read_bind`, `--approve-bind` albo `--port N --host ADR`; każdy inny niż `127.0.0.1`, `::1`, `localhost`, `0.0.0.0` i `::`, także inny adres z `127.*`): serwer słucha też na `127.0.0.1` na tym samym porcie (drugie gniazdo strony i originu prototypów), więc logowanie, zgody, `login-link` i przekazanie sesji działają na tej maszynie. Start wypisuje oba adresy; `url`, `--detach` i dopisanie zakładki podają adres `127.0.0.1`, a `url --lan` i `url --read` adres nasłuchu. Gniazdo odczytu (`--read-port`) drugiego gniazda nie ma (nie ma logowania). Zajęty port na `127.0.0.1` = BLOCKED. Przy `--port N` adres sieciowy odpowiada tylko na nazwy z `--approve-host` i `--read-host`.
  Kolejność dla gniazda strony: `--read-bind ADR`, potem `--host H` (w trybie domyślnym BLOCKED; z `--read-port` tylko odczyt; `--host ''` = wszystkie interfejsy),
  potem `dashboard:` w manifeście projektu, potem w configu maszyny, na końcu `0.0.0.0`. Gniazdo zgód (tylko z `--approve-port`): `--approve-bind ADR`, potem
  `dashboard:`, na końcu `127.0.0.1`; `--host` go nie rusza. W trybie domyślnym `approve_bind` nie ma znaczenia, bo zgody idą przez gniazdo strony: gdy klucz jest ustawiony (nie przez `local_only`), `serve` wypisuje ostrzeżenie z podpowiedzią (`read_bind`/`--read-bind` albo `--approve-port`).
  Zgody z telefonu lub innego komputera wymagają TLS (zaufane proxy), niezależnie od adresu nasłuchu.
  `--page-port N`: port jedynego gniazda trybu domyślnego (zakres 5100–5199, domyślnie 5140); origin prototypów liczy się od niego (plus 2). Nie łączy się z `--port`, `--read-port` ani `--approve-port`.
  Adres `ADR`/`H`: literał IP, `localhost`, `0.0.0.0` albo `::`. Tryb `--port` nasłuchuje na `127.0.0.1`, chyba że podano `--host`.
  W nieufnej sieci zawęź nasłuch do tej maszyny (`--read-bind 127.0.0.1` albo `dashboard.local_only: true`): wtedy strona, także logowanie, jest tylko na tej maszynie.
  Tunel SSH na stronę: `ssh -L 5140:127.0.0.1:5140 <host>`, a adres `http://127.0.0.1:5140/` otwierasz lokalnie. Ryzyko: [workflow](workflow.md), sekcja „Strona statusu”.
- Tryb jednego portu za tunelem albo bramką nazwy: `serve --port 5140 --approve-host <nazwa>` (jeden port z bramką `Host`; zgody nadal tylko z sesji `approver`).
  `--host` i `--approve-host` bez portów są BLOCKED z podpowiedzią, bo zmieniłyby znaczenie.
- Ustawienie `dashboard:` (klucze `local_only: true|false`, `read_bind`, `approve_bind`) jest w configu maszyny
  i w manifeście projektu, zob. [configuration](configuration.md) i [manifest](manifest.md#sekcja-dashboard).
  Nieznany klucz: `doctor` ostrzega, `serve` BLOCKED z nazwą klucza; zła wartość: BLOCKED z nazwą klucza (nigdy z wartością).
- Komunikaty startowe (konsola): przy odczycie poza loopbackiem jedno ostrzeżenie o dostępności dla każdego urządzenia w
  sieci lokalnej (plany, specyfikacje, wyniki, sekrety zredagowane heurystycznie, ale wyniki testów jawne, bez maski; ruch nieszyfrowany) z podpowiedzią
  `--read-bind 127.0.0.1` albo `dashboard.local_only: true`; adresy IPv4 maszyny po jednym w linii (sieć
  tylko-IPv6 ich nie dostanie) plus adres po nazwie maszyny (`http://<nazwa>.local:5140/`); nazwa maszyny w komunikacie;
  konsola podaje też, że zgody wymagają konta `approver` (lokalnie albo przez TLS). Gdy gniazdo słucha tylko na loopbacku, konsola
  pisze, że strona jest tylko na tej maszynie. Na Windows, przy pierwszym nasłuchu na `0.0.0.0`, konsola dodaje linię o Zaporze
  Windows; macOS z zaporą aplikacji może też zapytać o dostęp przy pierwszym nasłuchu.
- Start: wymagane porty (domyślnie 5140; przy `--read-port`/`--approve-port` ich numery) muszą być wolne. Przed bindem port jest sprawdzany połączeniem na loopback
  (na każdym systemie); gdy coś już odpowiada, start kończy się BLOCKED z numerem portu i właścicielem (projekt z plików stanu albo „inny proces”);
  serwer nie przechodzi na inny port. Zatrzymaj wcześniejsze `dashboard serve` tego samego hosta, także to z `--no-approvals`, zanim uruchomisz nowy.
  `--allow-proxy-headers` jest BLOCKED w każdej kombinacji (zob. tabelę losu flag w [upgrading](upgrading.md)). `--read-host '*'` działa tylko z `--read-port`.

#### Origin prototypów

Ten sam proces `dashboard serve` serwuje prototypy z drugiego gniazda, czyli z osobnego originu przeglądarki. Prototyp nie ma przez to dostępu do strony statusu ani do jej sesji.

- **Port:** `--prototype-port N` (1024–65535); bez flagi port strony plus 2, a gdy jest zajęty, następny wolny z zakresu 5100–5199 (po końcu zakresu od początku). Odrzucane są porty strony, porty zakazane (4444–4448, 9222, 9333) i 5141 (port zgód wcześniejszych wersji), także podane jawnie; automat pomija też 5140. Zajęty albo odrzucony port jawny = BLOCKED (kod 2). Gdy port strony plus 2 jest zajęty, start wypisuje, kto go trzyma i na którym porcie słucha origin prototypów; nazwa właściciela (`projekt <katalog> (rola)`, także `prototypy` dla originu prototypów innego serwera strony) pochodzi z plików stanu serwerów tej maszyny, obcy proces to „inny proces”. `prototype.port` manifestu nie wybiera tego portu ani nie steruje zakładką startową: wskazuje tylko serwer prototypów sprawdzany przez zakładkę innego worktree.
- **Nasłuch i `Host`:** prototypy słuchają pod tym samym adresem co strona (w trybie dwóch gniazd: co gniazdo odczytu; z samym `--approve-port`: co gniazdo zgód). Przyjmowane nazwy to loopback, `--read-host`, `--approve-host` i nazwa z `--prototype-url` (bez niej z `--public-url`); poza trybem `--port` także literały IP tej maszyny i jej własne nazwy.
- **Kanoniczny adres:** przy `--public-url` / `--prototype-url` żądanie dokumentu pod inną nazwą (nie loopback, bez nagłówków proxy) dostaje 302 na origin z flag, z ciasteczkiem znacznikiem na 30 s. Żądanie z tym znacznikiem jest obsłużone bez przekierowania, także bez żadnego proxy: przeglądarka, która przez te 30 s otworzy znów tę samą inną nazwę (albo której przekierowanie nie zmieniło nazwy hosta), dostaje stronę pod nią zamiast 302. Dzięki temu proxy, które przepisuje `Host` i nie dodaje nagłówków `X-Forwarded-*`, nie zapętla przeglądarki (strona działa wtedy tylko do odczytu; dla logowania i zgód ustaw `--trust-proxy` z `--proxy-address`).
- **Adres za proxy:** `--prototype-url URL` (sam origin, `scheme://host[:port]`) to origin, na który strona przekazuje sesję i który wolno jej osadzić w ramce. Origin równy originowi strony = BLOCKED; para, która wygląda na różne „site”, daje ostrzeżenie. Przy `--public-url` z `https` bez `--prototype-url` prototypy nie otworzą się pod publicznym adresem (brak ramki i przekazania sesji; serwer ostrzega przy starcie).
- **Service worker:** każde żądanie z nagłówkiem `Service-Worker` dostaje 403 `service_worker_refused` (także w `prototype serve`), żeby worker jednego prototypu nie został w przeglądarce i nie odpowiadał za ten origin.
- **Sesja:** na origin prototypów przechodzi przez jednorazowe przekazanie (`/_handoff`, [dashboard-contract](dashboard-contract.md), sekcja „Zgody i sesje”). Przekazana sesja nie ma ról `approver`, `acceptor` ani `developer`.
- **`--single-origin`** (świadomy wybór, bez izolacji): prototypy są na originie strony, drugiego gniazda nie ma. Zgody ze strony i akcje na prototypach są wyłączone (403 `approvals_disabled`); silnik prototypów odmawia tym samym kodem akceptacji ekranów i projektu, cofnięcia akceptacji, przywrócenia ekranu i zdjęcia znacznika pisania ekranów także na trasach `/_api/v1/…` (skrypt prototypu działa na originie sesji), więc bramka planu nie dostaje takich akceptacji (komentarze działają; akceptację daje serwer bez `--single-origin` albo CLI). Konta z rolą `approver` się nie logują, na każdej stronie HTML statusu jest stały czerwony pasek, a nagłówek `Service-Worker` jest odrzucany na wszystkich trasach. Nie łączy się z `--allow-remote-approvals`, `--prototype-port`, `--prototype-url`, `--read-port` i `--approve-port`.

Co widzi czytelnik bez sesji: [security](security.md), sekcja „Strona statusu w sieci lokalnej”. Akcje na prototypach z originu strony: [dashboard-contract](dashboard-contract.md), sekcja „Akcje na prototypach”.

#### Worktree jednego projektu: dopisanie zamiast drugiego serwera

Jedna strona statusu obsługuje wszystkie worktree projektu git: każdy katalog to zakładka (własny stan i własna blokada zgód), a zakładki są u góry strony. Zgody nie mają tokenu: sesja `approver` działa dla wszystkich zakładek, a zgoda trafia do zakładki wskazanej w ciele żądania (`tab`). Zasady wspólne: [manifest](manifest.md#sekcja-dashboard) (wykluczenia, `inactive_days`), kontrakt: [dashboard-contract](dashboard-contract.md).

- **Dopisanie.** `dashboard serve` w projekcie git, w którym działa serwer rodzaju `project`, i bez żadnej flagi ustawień serwera (`--port`, `--read-port`, `--approve-port`, `--read-bind`, `--approve-bind`, `--read-host`, `--approve-host`, `--allow-proxy-headers`, `--allow-remote-approvals`, `--no-approvals`, `--layer`, `--allow-plain-login`, `--local-only`, `--trust-proxy`, `--proxy-address`, `--secure-cookies`, `--public-url`, `--prototype-url`, `--prototype-port`, `--page-port`, `--single-origin`, `--host`, `--session-url`, `--poll`, `--verbose`) i bez `--standalone` nie otwiera gniazd: dopisuje katalog jako zakładkę, wypisuje jej adres i kończy się kodem 0. Serwer jest szukany po wspólnym katalogu gita (`commonDir`) w plikach stanu, nie po nazwie pliku. `--plan` jest ustawieniem zakładki i nie blokuje dopisania. Obowiązują ustawienia działającego serwera (porty, nasłuch, zgody); komenda ostrzega o tym na stderr.
- **Start serwera.** Bez działającego serwera projektu `serve` startuje serwer rodzaju `project`: korzeniem jest główny katalog (jeśli istnieje), zakładką startową katalog uruchomienia; flagi ustawień są wtedy dozwolone. Zakładka powstaje tylko dla głównego katalogu albo worktree z poprawnym powiązaniem zwrotnym gita, niewykluczonego; start z innego katalogu to serwer `standalone` z ostrzeżeniem.
- **Osobny serwer.** `--standalone` albo jakakolwiek flaga ustawień przy działającym serwerze projektu uruchamia osobny serwer rodzaju `standalone` z jedną zakładką katalogu startu (z ostrzeżeniem, gdy powód to flagi). Start z `--no-approvals` albo `--layer` także jest `standalone` (podgląd bez zgód albo z cudzą warstwą nie jest serwerem projektu). Serwer `standalone` nie przyjmuje zakładek innych katalogów i nie jest celem dopisania ani wynikiem `dashboard url` bez `--port`.
- **Rodzaj ustalany raz.** Rodzaj serwera (`project` albo `standalone`) wynika ze startu i nie zmienia się w trakcie jego życia: po zakończeniu serwera projektu serwer `standalone` pozostaje `standalone`, a następne zwykłe `serve` startuje nowy serwer projektu. Serwer starszej wersji (bez zakładek) na portach domyślnych tego samego projektu nie przyjmuje zakładek: `serve` kończy się komunikatem z prośbą o jego zatrzymanie albo o `--standalone` na innych portach.
- **Wynik.** Adres zakładki (`/?tab=<id>`) jest ostatnią linią stdout; wszystkie komunikaty idą na stderr. `--json` zwraca przy dopisaniu `{schema, status, mode, url, tab, port, pid[, tabs]}` albo `BLOCKED {code, reason}` (exit 2); start serwera drukuje swój zwykły końcowy JSON. Domyślnie wypisywany jest tylko adres własnej zakładki; `--all-tabs` dodaje adresy wszystkich zakładek (własny na końcu).
- **Zamknięcie terminala.** Komunikat przy dopisaniu i przy starcie podaje PID i terminal procesu serwera: zamknięcie go zamyka stronę wszystkim zakładkom, a serwer działa, dopóki działa jego proces.
- **Widoczność zakładek.** Lista worktree odświeża się okresowo, więc worktree założony po starcie pojawia się sam, jeśli ma ślad pracy skillsync (wpis w `.skillsync/runs/` albo plik w `docs/plans`/`docs/specs` różny od głównego katalogu). Zakładki nieaktywne dłużej niż `dashboard.worktrees.inactive_days` są ukryte pod „pokaż wszystkie”; katalog uruchomienia komendy jest zawsze widoczny. Widocznych zakładek jest najwyżej 16. Porty różnych worktree tego samego projektu nie wymagają ręcznego przydziału: wszystkie obsługuje jeden serwer.
- **Serwer a zakładka Historia.** Plany zakończone (`done`, `archived`) z całego projektu są w zakładce „Historia”; zmiana ich statusu przez stronę używa tej samej blokady pliku co `plan archive|unarchive|abandon|approve` (niżej); porzucone plany `approved` też są na tej liście.

#### Kody BLOCKED serve i url

Kod wyjścia 2; w `--json` pole `code`, bez `--json` linia `kod: powód` na stderr.

| Kod | Komenda | Kiedy | Co zrobić |
|---|---|---|---|
| `ambiguous_server` | `serve` (dopisanie), `url` | działa więcej niż jeden serwer tego projektu (np. na niestandardowych portach) | zatrzymaj nadmiarowe albo wskaż serwer: `dashboard url --port N` |
| `worktree_rejected` | `serve` | katalog nie jest głównym katalogiem ani worktree z poprawnym powiązaniem zwrotnym gita (w tym `bare`) | uruchom komendę w prawdziwym worktree tego projektu |
| `worktree_excluded` | `serve` | worktree ma znacznik `.skillsync/no-dashboard` albo jest na liście `dashboard.worktrees.exclude` | usuń wykluczenie albo użyj `--standalone` |
| `tab_limit` | `serve` | serwer ma już 16 przypiętych zakładek | wyklucz zbędne worktree i ponów |
| `registration_timeout` | `serve` | serwer nie dopisał zakładki w 5 s; adres nie jest drukowany (nigdy martwy adres) | sprawdź konsolę serwera (PID w komunikacie) i ponów |
| `server_not_running` | `serve` (serwer zniknął w trakcie dopisywania), `url` | brak działającego serwera projektu ani innego serwera tego katalogu z plikiem stanu | uruchom `dashboard serve` |
| `legacy_server` | `serve` | zwykły start serwera projektu (bez flag ustawień), a na portach domyślnych tego projektu (odczytu i zgód) działa serwer starszej wersji (plik stanu schema 1, bez zakładek); drugi serwer nie zająłby tych portów | zatrzymaj starszy serwer (PID w komunikacie) albo uruchom `dashboard serve --standalone` na innych portach |
| `invalid_project` | `serve`, `url` | projekt nie wczytuje się, nieczytelny manifest głównego katalogu albo błędna sekcja `dashboard:`, nieistniejący `--plan`, błąd zapisu przypięcia | napraw manifest lub argumenty (komunikat nazywa klucz, nie wartość) |
| `approvals_disabled` | `url` | serwer działa z `--no-approvals`: zgód nie ma | użyj `url --read` albo uruchom serwer ze zgodami |
| `detach_failed` | `serve --detach` | serwer w tle zakończył się, zanim strona odpowiedziała (np. zajęty port albo własna odmowa startu, jak `legacy_server`: jej kod jest w logu), albo nie dało się go uruchomić | ostatnie linie jego logu są na stderr, cały log w pliku z komunikatu |
| `detach_timeout` | `serve --detach` | strona nie odpowiedziała w 30 s; uruchomiony proces został zatrzymany | sprawdź log z komunikatu, uruchom `serve` bez `--detach` |
| `invalid_project` | `stop` | katalogu projektu nie da się odczytać | popraw ścieżkę albo manifest |
| `unknown_tab` | `url` | katalog nie ma zakładki na wskazanym serwerze (`--port N` na serwerze `standalone` innego katalogu) albo zakładka nie jest jeszcze zarejestrowana | uruchom w katalogu `dashboard serve` |
| `host_not_allowed` | `url` | `--host` spoza loopbacku i nazw z `--approve-host` serwera | podaj dozwoloną nazwę |
| `read_not_exposed` | `url --lan` | gniazdo odczytu słucha tylko na loopbacku | uruchom serwer z szerszym `--read-bind` |

Blokada zapisu statusu: `plan archive`, `plan unarchive`, `plan abandon` i `plan approve` zapisują status planu pod blokadą `<katalog>/.skillsync/locks/plan-status.lock`, wspólną z przyciskami strony (archiwizacja, porzucenie i przywracanie w „Historii”, GO). Blokada chroni tylko zapisy skillsync, nie edytor; zajęta po 5 s kończy zapis `BLOCKED` (kod 2).

#### `dashboard serve --detach` i `dashboard stop`

- **Start w tle.** `dashboard serve <projekt> --detach` (z tymi samymi flagami co bez niego) uruchamia serwer jako osobny proces, który zostaje po zamknięciu polecenia i terminala (Windows: ukryta konsola, własna grupa procesów i wyjście z grupy zadań (job object) terminala, więc serwer przeżywa też zamknięcie sesji SSH; gdy grupa zadań na to nie pozwala, serwer startuje w niej, a polecenie ostrzega, że zakończy się razem z terminalem), czeka do 30 s, aż strona odpowie, wypisuje jej adres na stdout i kończy się kodem 0; PID, port, ścieżka logu i sposób zatrzymania idą na stderr. `--json`: `{schema, status, mode: "detached", url, port, pid, log}`. Konsola serwera trafia do pliku `<katalog stanu maszyny>/dashboard/<skrót katalogu>-detach-*.log` (tylko właściciel; zostaje 5 najnowszych logów katalogu; gdy serwer katalogu działa, starsze nie są usuwane). Przed startem usuwane są pliki stanu martwych serwerów strony statusu.
- Gdy serwer projektu już działa i nie podano flag ustawień, `--detach` niczego nie uruchamia: katalog zostaje dopisany jako zakładka, tak jak bez tej flagi.
- Przerwanie czekającego polecenia (Ctrl+C, `SIGTERM`, `SIGHUP`) zatrzymuje uruchamiany serwer.
- **Zatrzymanie.** `dashboard stop <projekt>` to `servers stop` ograniczone do serwerów strony statusu, dla podanego katalogu i głównego checkoutu projektu: działa też dla serwera uruchomionego bez `--detach`, sprawdza tożsamość procesu przed sygnałem i ma te same flagi, wynik i kody wyjścia (0 zatrzymano albo nie było czego, 1 serwer nadal działa, 2 odmowa albo zły argument, 3 `--dry-run`); zob. [`servers`](#servers). Serwer projektu jest wspólny dla wszystkich worktree, więc `stop` uruchomiony w worktree zamyka stronę wszystkim zakładkom (ostrzeżenie na stderr). Serwer `standalone` innego worktree zatrzymasz z jego katalogu.
- Windows: `CTRL_BREAK` z `servers stop` i `dashboard stop` dociera do całej konsoli serwera uruchomionego na pierwszym planie, więc razem z nim mogą skończyć się inne procesy tej konsoli; serwer uruchomiony z `--detach` ma własną konsolę.

## Rozszerzenia workflow

Komendy kontrolne z konwencji (bez `--run` tylko planują, kod 0 dla `NOT_RUN`; `BLOCKED` = 2). Procedury: [workflow](workflow.md).

| Komenda | Wymagane | Skutek |
|---|---|---|
| `env [path] [--action plan\|status\|up\|down] [--run] [--generate] [--json]` | | receptura środowiska z manifestu albo warstwy. Domyślnie `plan`; `up` i `down` wykonują polecenia receptury tylko z `--run`, `status` odczytuje zapisany stan. Poza Linuksem komenda zwraca BLOCKED (brak zweryfikowanego nadzoru nad procesami; wyjątek: `--generate`). `--generate --run` zapisuje launchery (`launchers[]`). Bez receptury `NOT_RUN` z powodem. |
| `e2e [path] [--action plan\|record\|run] [--observation PLIK] [--run] [--json]` | `record` wymaga `--observation` | `plan` (domyślnie) pokazuje skonfigurowane kontrole, katalogi obserwacji i scenariuszy; `record` zapisuje obserwację z dowodami lokalnymi (plik pod `.skillsync/e2e/`), `run` uruchamia natywne kontrole; oba wymagają `--run`, bez niego `NOT_RUN`. Procedura: skill `observed-e2e`. |
| `tracker --operation OP [--provider P] [--params JSON] [--run] [--allow-write] [path]` | `--operation` | jedna operacja zaufanego dostawcy trackera. Bez `--run` plan (efekt operacji i wymagane parametry), z `--run` wywołuje zaufany adapter. Operacja zapisująca wymaga `--allow-write` (inaczej BLOCKED) i jest sprawdzana odczytem zwrotnym; niezgodny odczyt to BLOCKED z `mutation_completed: true`. Nieznana operacja, brak wymaganych zdolności dostawcy i dostawca wewnętrzny (np. `azure-feedback`) to BLOCKED. |
| `ci --report PLIK [--run] [path]` | `--report` | diagnoza zaimportowanego raportu CI (JSON z `source` równym odciskowi bieżącego kodu i `checks[]` z `name`, `status`, `log`; do 1000 kontroli, log lokalny do 2 MiB). Nieaktualny odcisk to BLOCKED. Z `--run` i porażką uruchamia zaufane kontrole z `delivery.ci` (`fix_check_ids`, `rerun_check_ids`, `max_iterations` 0–3); polecenia z raportu nigdy nie są wykonywane. |
| `claim --task-id ID [--run-id ID] [--action status\|acquire\|renew\|release\|transfer] [--ttl S] [--to-run-id ID] [--takeover] [--run] [path]` | `--task-id` | dzierżawa zadania należąca do przebiegu. `status` (domyślnie) tylko odczytuje; `acquire`, `renew`, `release` i `transfer` (wymaga `--to-run-id`) zapisują wyłącznie z `--run`. `--ttl` w sekundach 1-86400 (domyślnie 900). Wygasłą dzierżawę przejmuje się jawnie flagą `--takeover` (inaczej BLOCKED). Procedura: skill `task-claim`. |
| `profiles [path] [--json]` | | wybrane profile skilli (`selected`), ich skille i katalog z budżetem bajtów (`skill_profiles.catalog_budget_bytes`, domyślnie 32768); tylko odczyt, `PASS`. |
| `upgrade [path] [--action plan\|apply\|rollback] [--backup ID] [--run]` | | migracja artefaktów workflow według `upgrade` z konfiguracji. `plan` (domyślnie) pokazuje zmiany; `apply` i `rollback` zapisują tylko z `--run`; `apply` robi kopię zapasową, `rollback` przywraca ją (`--backup`, domyślnie ostatnią) i odmawia (BLOCKED), gdy pliki zmieniono lokalnie po migracji. Przerwana migracja wymaga `rollback`. Bez artefaktów `NOT_RUN`. |
| `guard [path] [--files PLIKI…] [--run] [--json]` | | strażnik konwencji projektu (`guardrails.rules` i `checks` z konfiguracji) na zmienionych plikach (`--files` albo z gita); bez `--run` tylko plan (`NOT_RUN`: reguły, pliki i kontrole), z `--run` stosuje reguły do plików i uruchamia zaufane `checks`; dowiązania są odnotowywane w `skipped`. Bez skonfigurowanych strażników `NOT_RUN`. |
| `benchmark --suite PLIK [--run] [path]` | `--suite` | benchmark narzędzi według pliku zestawu przypadków (`id`, limit czasu); bez `--run` przypadki dostają `NOT_RUN`. Kody: `PASS` i `NOT_RUN` 0, `FAIL` 1, `BLOCKED` 2. |

## Prototypy

`skillsync prototype init|sync|index|list|ids|migrate|serve|user|login-link|iterate|restore|writing|accept|gate|comments|triage|decide|resolve|merge-comments|reply|wait|question <projekt> …`

Komenda działa na klikalnych prototypach HTML w repo projektu. Opis akcji, kodów i formatu: [prototypes](prototypes.md).

Akcje (każda: `skillsync prototype <akcja> <projekt> …`; wszystkie przyjmują `--json`, `--layer ID=PATH` i `--assets DIR`; kody: PASS 0, FAIL 1, BLOCKED 2, TIMEOUT 3):

| Akcja i składnia | Skutek |
|---|---|
| `init <projekt> <slug> --requirements PLIK [--title T]` | tworzy `<root>/<slug>` z zasobów prototypu (nigdy nie nadpisuje) |
| `sync <projekt> [slug] [--check]` | odświeża `tokens.css` i `theme.css` jednego lub wszystkich prototypów; `--check` zwraca FAIL (1) przy rozjeździe |
| `index <projekt>` | generuje galerię `<root>/index.html` |
| `list <projekt>` | prototypy jako JSON (ekrany, otwarte wątki komentarzy, `acceptance`, `writing`) |
| `ids <projekt> <slug> [--check \| --fix]` | sprawdza (domyślnie, FAIL 1) albo dopisuje uchwyty `data-proto-id` w `index.html` |
| `migrate <projekt> <slug> [--dry-run] [--allow-skipped]` | aktualizuje silnik, tokeny i ikony prototypu oraz migruje `comments.js` v1 do `comments.json` |
| `serve`, `user …`, `login-link --user LOGIN [--path P] [--port N] [--users PLIK]` | serwer, konta i jednorazowy link logowania (polecenie człowieka, agent go nie uruchamia) |
| `iterate <projekt> <slug>` | zamraża bieżącą wersję do `iterations/N/` (wymaga `ids --check` PASS) |
| `restore <projekt> <slug> [--screen sN] [--from ITERACJA] [--reason T] [--who LOGIN] [--users PLIK] [--dry-run \| --resume]` | przywraca ekran z zamrożonej iteracji (najpierw zamraża bieżącą wersję) |
| `accept <projekt> [slug] --by LOGIN [--screen sN \| --all \| --project] [--users PLIK]` | akceptacja ekranów bieżących plików; człowiek przy terminalu (hasło z TTY); BLOCKED `writing_in_progress`, gdy agent jeszcze pisze |
| `gate <projekt> [slugi…] [--plan PLIK [--phase Fn]]` | sprawdza akceptację prototypów planu (linie `Prototyp:`) albo wskazanych slugów (FAIL 1 = braki) |
| `comments <projekt> <slug> [--status S]` | fakty wątków komentarzy (tylko odczyt) |
| `triage <projekt> <slug> <wątek> --class clear\|disputed\|out_of_scope [--note T]` | klasyfikacja wątku przez agenta |
| `decide <projekt> <slug> <wątek> --kind agent\|developer --outcome apply\|keep\|variant\|defer\|out_of_scope --text T [--who LOGIN] [--iteration N] [--users PLIK]` | zapis decyzji o wątku |
| `resolve <projekt> <slug> <wątek> --reason done\|kept\|out_of_scope [--iteration N]` | zamknięcie wątku przez agenta |
| `merge-comments [<projekt> <slug> <inny-plik>] \| --driver %O %A %B` | scalenie dwóch dzienników komentarzy (także jako sterownik merge gita) |

Odpowiedzi agenta, oczekiwanie i marker zapisu opisują akapity poniżej (`reply`, `wait`, `writing`); pytania do eksperta: `prototype question`.

`prototype reply <projekt> <slug> <wątek> --text TEKST`: odpowiedź agenta w wątku komentarzy (autor `agent`, znaczek AI w interfejsie; to nie jest zgoda). `prototype wait <projekt> <slug> [--since ISO] [--after ID-OPERACJI] [--timeout S] [--interval S]`: czeka na nowy komentarz osoby albo na akceptację wszystkich ekranów; kod wyjścia 0 ze zdarzeniem `comment` albo `accepted`, 3 przy przekroczeniu czasu (domyślnie 1800 s, odpytywanie co 5 s); `--since` razem z `--after` to `BLOCKED` (kod 2).

`prototype serve <projekt> [--layer ID=PATH] [--assets DIR] [--json] [--host ADR] [--port N] [--users PLIK] [--secure-cookies] [--trust-proxy --proxy-address ADR] [--allow-plain-login] [--local-only] [--no-source-writes] [--login-to-view] [--demo]`: serwer prototypów ze wspólnym API komentarzy (na pierwszym planie, Ctrl+C zatrzymuje). Przy starcie (poza `--demo`) przypomina o `dashboard serve`, który serwuje te same prototypy razem ze zgodami i akceptacjami. Konta z rolą `approver` nie logują się tu hasłem ani nie zmieniają go, także w sesji z linku logowania (403 `approver_login_elsewhere`). `--host`: adres nasłuchu (domyślnie `127.0.0.1`); `--port`: port (domyślnie `prototype.port` z manifestu, bez klucza 4445; `0` = dowolny wolny); `--users`: plik użytkowników względem projektu; `--secure-cookies`: ciasteczko sesji z `Secure` (samo nie czyni żądania szyfrowanym); `--trust-proxy`: zaufany proxy, wymaga `--proxy-address` (bez listy serwer nie startuje); żądanie od takiego proxy jest TLS, gdy proxy wysyła `X-Forwarded-Proto: https`, a limit logowania liczy adres z `X-Forwarded-For`; link logowania jest wtedy wyłączony; `--proxy-address ADR`: adres lub sieć zaufanego proxy (powtarzalne lub lista po przecinku); `--allow-plain-login`: konta `commenter` i `expert` logują się hasłem przez HTTP z sieci (nigdy konta z rolami `approver`, `acceptor` czy `developer`); bez tej flagi po zwykłym HTTP z sieci logowanie kończy się 403 `secure_connection_required`; `--local-only`: odrzuca każdego peera spoza loopbacku; `--no-source-writes`: serwer nigdy nie zapisuje źródeł prototypów (przywracanie ekranów); `--login-to-view`: logowanie także do oglądania. `--demo`: pokaz tylko do odczytu bez kont (bez logowania, komentarzy, wątków, akceptacji i odpowiedzi eksperta; serwer odmawia każdego zapisu, istniejące komentarze są ukryte, strony są jawne dla każdego, kto dotrze do adresu); domyślny nasłuch pokazu to `0.0.0.0` (każdy interfejs), a `--host 127.0.0.1` zawęża go do tej maszyny. `--demo` z `--login-to-view` kończy się `BLOCKED` (exit 2: pokaz nie ma kont ani logowania); `--users`, `--secure-cookies` i `--trust-proxy` przy `--demo` są ignorowane z notą. Model zagrożeń pokazu: [security](security.md#serwer-prototypów-pytania-do-eksperta).
`list --json`: pole `acceptance` = `{screens, accepted, stale, perScreen}` (`null` przy v1 i bez `comments.json`); `perScreen` to `[{id, state}]` dla każdego ekranu w kolejności dokumentu, `state` = `accepted` | `stale` | `pending` (accepted + stale + pending = `screens`).

`prototype gate <projekt> --plan <plan> [--phase <Fn>]`: bramka akceptacji prototypu. `--phase` jest dozwolone tylko z `--plan` (ze slugami: BLOCKED z JSON-em, kod wyjścia 2)
i dopasowuje id fazy dosłownie (wielkość liter). Wynik wg tabeli stanów ([prototypes](prototypes.md#akceptacja)): faza `[UI]` dostaje wynik całego planu
(PASS/FAIL), faza bez `[UI]` w planie z fazami `[UI]` PASS z notą i pustymi `prototypes`, `missing`, `acceptances`, nieznana faza BLOCKED `unknown_phase`,
plan z fazą `[UI]` bez linii `Prototyp:` i bez obejścia właściciela BLOCKED `ui_phase_without_prototype`. Raport dostaje opcjonalne pola `phase`
(przy `--phase`) oraz `phases` i `uiPhases` (listy id, tylko gdy plan ma fazy `[UI]`). Kody wyjścia bez zmian: PASS 0, FAIL 1, BLOCKED 2.

### Marker zapisu (`prototype writing`)

`prototype writing <projekt> <slug> start|done|status [--ttl <s>] [--by <login>] [--screens <N>]`: agent oznacza, że jeszcze pisze ekrany prototypu.
`start` zapisuje atomowo, pod blokadą prototypu, plik `<root>/<slug>/.writing.json` (`since`, `renewedAt`, `expiresAt`, `by`) i dopisuje
`.writing.json` do `<proto>/.gitignore` (gdy się nie da, ostrzeżenie w raporcie); powtórzone `start` na ważnym markerze zachowuje `since` (początek pisania)
i `by` (chyba że podano `--by`), ustawia `renewedAt` i nowy `expiresAt`, więc odnawia marker (także skracając TTL); na wygasłym albo uszkodzonym zaczyna od nowa. `--ttl` to sekundy, 60–3600, domyślnie 900; `--by` to login (pierwszy znak litera lub cyfra, dalej litery, cyfry i `. _ @ + -`, najwyżej 64 znaki;
domyślnie `agent`); `--screens` (tylko `start`, liczba całkowita 1–200) zapisuje w markerze `screensPlanned`, czyli ile ekranów agent zamierza napisać
(strona statusu pokazuje „N z M” i pasek); powtórzone `start` bez `--screens` zachowuje wartość z ważnego markera. `done` usuwa marker (idempotentnie, bez blokady), `status` zwraca `{status, writing: null | {since, expiresAt, renewedAt, by[, screensPlanned]}}`
(bez blokady). Kody: PASS 0; BLOCKED 2 z `error`: `needs_migration` (prototyp bez `comments.json`), `lock_timeout` (zajęta blokada prototypu,
np. trwa `iterate` albo `restore`: ponów), `invalid_args` (zły `--ttl`, `--by` albo `--screens`); FAIL (1) nie występuje. Nieznana akcja to błąd argparse.

Wpływ ważnego markera na inne komendy (definicja w [prototypes](prototypes.md#marker-zapisu-agent-pisze-ekrany)):
- `accept` (także `--project`) kończy się BLOCKED `writing_in_progress` przed pytaniem o hasło i ponownie pod blokadą transakcji;
  przy `--project` wstępna kontrola obejmuje wszystkie prototypy, a odmowa w trakcie niesie listę `writing` oraz `accepted`/`pending`.
  `accepted: []` bywa przy markerze wykrytym dopiero w `build` pierwszego prototypu projektu (nic jeszcze nie zapisano).
- `gate` (po slugach i `--plan`) zwraca FAIL z wpisem `missing` `{slug, path, screen: null, reason: 'writing'}` dla każdego prototypu z ważnym
  markerem, także gdy wszystkie jego ekrany mają ważną akceptację; linia „Bramka prototypu: pominięta — …” nadal daje PASS.
- `wait` nie zwraca `accepted`, dopóki marker jest ważny (komentarze nadal budzą `wait`).
- `list` ma pole `writing`: `null` albo `{since, expiresAt, renewedAt[, screensPlanned]}` (bez `by`).
- `iterate` przy ważnym markerze działa i dodaje ostrzeżenie do raportu.
- `status --ttl` i `done --by` są walidowane tak samo jak `start` (zły argument = BLOCKED `invalid_args`).

### Pytania do eksperta (`prototype question`, `prototype user roles`)

Agent zadaje pytania ekspertom domenowym przez wspólny serwer prototypów; odpowiedzi czyta z pliku `<root>/<slug>/questions.json`
przebiegu (bez serwera). Odpowiadać może tylko człowiek z rolą `expert` na stronie `/_experts`: agent nie ma polecenia `answer`.
Model, limity i strona: [prototypes](prototypes.md#pytania-do-eksperta), poufność: [security](security.md).

```bash
skillsync prototype serve . --demo                                                       # pokaz tylko do odczytu bez kont, każdy interfejs (--host 127.0.0.1 zawęża)
skillsync prototype sync . --check                                                       # FAIL (kod 1): nieaktualne pliki albo brak wymaganych tokenów motywu (pole `missing`)
skillsync prototype user add . ewa.ekspert@example.com --role expert --password-prompt   # konto eksperta
skillsync prototype user roles . ewa.ekspert@example.com --role expert --role commenter  # zastępuje zbiór ról
skillsync prototype question url .                                                       # adres strony i liczba kont expert
skillsync prototype question add . --slug zwroty --text "Czy zwrot po terminie jest dozwolony?" \
  --ref "Sekcja 3|docs/specs/zwroty.md#3|Zwrot do 14 dni od zakupu." \
  --option "Tak, bez limitu" --option "Tak, do 30 dni:recommended" --option "Nie" \
  --assume "do 30 dni" --origin docs/specs/zwroty.md:Q3                                  # domyślnie agent nie czeka
skillsync prototype question add . --slug zwroty --text "Czy przycisk „Zwróć” ma być widoczny po terminie?" \
  --screen zwroty#s4 --assume "nie" --origin docs/specs/zwroty.md:Q6                      # link „Zobacz ekran s4” na stronie eksperta (powtarzalne, z --ref najwyżej 5)
skillsync prototype question add . --text "W jakich formatach eksport?" --multi \
  --option "CSV:recommended" --option "XLSX:recommended" --option "PDF" \
  --assume "CSV i XLSX" --origin docs/specs/eksport.md:Q4                                 # ekspert zaznacza jeden lub więcej wariantów
skillsync prototype question add . --text "Jaki jest klucz idempotencji płatności?" \
  --blocking --to ewa.ekspert@example.com --origin docs/specs/platnosci.md:Q5            # bez --slug: przebieg ze speca
skillsync prototype question list . --slug zwroty --status open,answered                 # jeden przebieg
skillsync prototype question list . --status open,answered                               # wszystkie przebiegi
skillsync prototype question show . qn-3f9a1c                                            # slug nie jest potrzebny: id jest unikalny
skillsync prototype question wait . qn-3f9a1c --timeout 600
skillsync prototype question wait . --all --slug zwroty --since 2026-11-12T08:00:00Z
skillsync prototype question record . qn-3f9a1c --spec docs/specs/zwroty.md --line 42   # po wpisaniu wiersza do speca
skillsync prototype question withdraw . qn-3f9a1c                                        # tylko pytanie otwarte
skillsync prototype question export . --slug zwroty --json > pytania.json
skillsync prototype question migrate . --dry-run                                         # plan podziału starego pliku
skillsync prototype question migrate . --slug-for docs/specs/zwroty.md=zwroty            # rozstrzygnięcie jawne
skillsync prototype question purge . --slug zwroty                                       # podgląd: tylko wycofane (bez --yes niczego nie zmienia)
skillsync prototype question purge . --slug zwroty --yes                                 # usuwa wycofane
skillsync prototype question purge . --slug zwroty --include-open --yes                  # także otwarte bez odpowiedzi i komentarza
```

Plik użytkowników jest prywatny: na POSIX tryb 0600, na Windows ACL (tylko użytkownik, SYSTEM i administratorzy lokalni). Komendy `prototype user ...` i `prototype serve` na Windows nie wypisują ostrzeżenia o pominiętej kontroli; stary plik z szerokim ACL jest naprawiany przy pierwszym zapisie albo odczycie (jedna linia `ACL Windows: ograniczono dostęp do pliku ...` w `warnings`), a ostrzeżenie `nie udało się ograniczyć dostępu do pliku ...` pojawia się tylko, gdy ACL nie da się ustawić (FAT/exFAT, udział sieciowy bez obsługi, brak uprawnień). Zapis pliku i tak się udaje. Szczegóły: `docs/prototypes.md`.


- `add`: dokładnie jedno z `--assume <wartość>` (pytanie nie zatrzymuje agenta; ekspert potwierdza albo koryguje) i `--blocking`
  (agent czeka). `--slug <slug>` wybiera przebieg, którego `<root>/<slug>/questions.json` dostaje pytanie. Bez `--slug` przebieg
  to jedyny prototyp, którego `requirements` równa się ścieżce z `--origin`; zero dopasowań = BLOCKED `slug_required` (z listą
  `prototypes`), kilka = BLOCKED `slug_ambiguous` (z `candidates`). `--ref` ma postać `TYTUŁ|ADRES[|WYCIĄG]` (do 5; adres względny albo `https`), `--option` `ETYKIETA[:recommended]`
  (do 8, najwyżej jedna rekomendowana, kilka tylko z `--multi`), `--multi` (pytanie wielokrotnego wyboru: ekspert zaznacza co najmniej jeden
  wariant, wymaga co najmniej 2 opcji; „żaden” to własna odpowiedź tekstowa; do pytań typu „które formaty/kolumny/role” zamiast
  rozbijania na pytania tak/nie), `--cid ID` (identyfikator deduplikacji; domyślnie wyprowadzony z id pytania), `--to LOGIN` powtarzane (domyślnie wszyscy eksperci; login spoza pliku kont daje
  ostrzeżenie), `--refers-to <id>` (spór z wcześniejszą odpowiedzią w tym samym przebiegu), `--origin <spec>:Q<n>` (ścieżka względna). Wyciąg ze speca lub
  dokumentacji agent wpisuje sam: serwer nie czyta repozytorium. Skan sekretów w treści jest heurystyką: trafienie blokuje
  (`secret_detected`, exit 2, bez wartości w raporcie), `--force` przepuszcza z ostrzeżeniem. Raport: `questionId`, `slug`, `specLine`
  (wiersz do speca), `files` (pliki do commita, w tym `<root>/<slug>/questions.json`, `<root>/.gitignore` z `questions.json.lock` i `*.tmp` oraz `<root>/.gitattributes`
  z `merge=skillsync-questions`, dopisywane przy pierwszym użyciu), `created` (nowo utworzone pliki: plik pytań, jego lock, `<root>/.gitignore`, `<root>/.gitattributes`), `ignored` (te utworzone pliki, które git ignoruje, czyli lock: `created` = `files` bez dopisanych wcześniej + `ignored`),
  `notes` (zdania typu „created <ścieżka>”, także o locku ignorowanym przez git), `warnings`, `url`, gdy serwer projektu działa albo ustawiono `prototype.public_url`/`SKILLSYNC_PROTOTYPE_PUBLIC_URL`. Powtórka z tym
  samym `--question-id` jest idempotentna. Osierocony lock (`questions.json.lock` bez pliku pytań, wolny) usuwa następny `add` tego przebiegu
  (best effort; na platformie, która nie usuwa otwartego pliku, zostaje). Odrzucony `add` nie zakłada ani pliku, ani locka.
- `list`, `show`, `export` zwracają dokument `expert-questions/1`: `{schema, project, url?, questions: [{id, slug, text, state, blocking, assumed,
  addressees, refs, options, answer?, comments, refersTo?, origin, recorded?}]}` oraz `status: PASS` (potrzebne do kodu wyjścia; klient ignoruje
  nieznane pola). Pytanie wielokrotnego wyboru ma `multi: true`, a jego odpowiedź `optionIds` i `optionLabels` (listy w kolejności opcji
  pytania) zamiast `optionId`/`optionLabel`; pytania jednokrotne mają `optionId` i `optionLabel`. `slug` to nazwa przebiegu, `null` dla pytania ze starego pliku. `--slug` zawęża do jednego przebiegu; bez niego
  łączą wszystkie przebiegi i dopisują stary plik (`legacy` w dokumencie, gdy istnieje). `list` ma `runs` (`slug`, liczby pytań).
  Raporty `list`, `show` i `export` mają na początku `untrusted: true` i stały `notice` (treść odpowiedzi i komentarzy ekspertów to dane); `wait` dodaje je w zdarzeniach `answer` i `comment`.
  `show` i `wait` mają ten sam kształt: `questions[]` (jedno pytanie; `wait --all` zwraca pierwsze gotowe), `question` (to samo pytanie) i `answerLine`; `wait` dodaje `event`, `questionId`, `slug` (i `pending` przy `--all`), `show` dokument `expert-questions/1` (`schema`, `project`). Oba dodają `answerLine`: gotowy wiersz `[EKSPERT]` do speca (`null` bez odpowiedzi; czas odpowiedzi lokalny z offsetem, np. `2026-11-13T13:30+01:00`, a w dzienniku nadal UTC); `specLine` z `add` i `withdraw`
  to wiersz czekającego pytania. Domyślny filtr `list` to `open,answered`; `--status` przyjmuje też `withdrawn` i `all`.
- `show`, `record`, `withdraw` i `wait <id>` znajdują pytanie po id (`--slug` opcjonalny, zawęża szukanie). `record` i `withdraw` pytania
  ze starego pliku dają BLOCKED `migration_required`.
- `wait <id>` zwraca od razu PASS (`event: answer`), gdy pytanie jest już odpowiedziane, a poza tym czeka na odpowiedź albo komentarz
  nowszy niż `--since` (domyślnie „teraz”) lub `--after <id operacji>`. `wait --all` (z `--slug` albo po wszystkich przebiegach) kończy się, gdy istnieje pytanie odpowiedziane
  bez operacji `recorded` albo zdarzenie nowsze niż `--since`/`--after`. TIMEOUT to exit 3.
- `record <id> --spec … --line <numer>` zapisuje znacznik „wpisano do speca” (idempotentnie), ale najpierw czyta wskazaną linię speca i wymaga w niej `[EKSPERT]` oraz identyfikatora pytania (wiersz z pola `answerLine`); inaczej FAIL (1) z kodem `record_line_mismatch` (inna treść linii), `record_line_out_of_range` (za krótki spec) albo `record_spec_unreadable` (brak pliku, ścieżka poza repozytorium) i podpowiedzią w `hint`. Pytanie bez odpowiedzi daje najpierw `not_answered`; `withdraw <id>` wycofuje pytanie otwarte,
  także blokujące, zwraca stan po operacji (`state` i `questions[0].state` = `withdrawn`) i zwykły wiersz `Q` programisty (odpowiedzianego nie da się wycofać: `already_answered`).
  `--reason <tekst>` (1–500 znaków, bez sekretu; `secret_detected` bez `--force`) zapisuje powód w operacji `withdraw` (pole `reason`) i zwraca go w raporcie; ekspert, do którego pytanie było skierowane, widzi je jako „wycofane” z powodem. Złe `--reason` to BLOCKED `invalid_reason`; powtórne `withdraw` niczego nie zmienia (zostaje pierwszy powód).
- `url` zwraca adres strony i liczbę kont `expert` (adres z `prototype.public_url` albo zmiennej `SKILLSYNC_PROTOTYPE_PUBLIC_URL`, gdy ustawiono: za tunelem ssh inaczej byłoby 127.0.0.1; błędna zmienna to BLOCKED `invalid_public_url`); BLOCKED (`users_file_missing`, `users_file_invalid`, `no_expert_accounts`) znaczy, że
  nie ma komu zadać pytania, więc agent pyta zwykle. Nie sprawdza, czy serwer działa (pole `running` mówi o stanie z pliku serwera).
- `add`, `list`, `show`, `export` i `url` przyjmują `--users` (plik kont względem projektu, jak w `user`).
- `migrate [--dry-run] [--slug-for SPEC=SLUG]` dzieli stary `<root>/questions.json` na pliki przebiegów według `origin.spec`. Idempotentna.
  Wynik: `migrated` (per slug: `operations`, `added`), `legacy` (`trashed` | `renamed:<nazwa>` | `absent`), `files`; `--dry-run` zwraca `plan` i `legacy: kept`.
  Pytanie bez jednoznacznego przebiegu albo operacja bez swojego `ask` = BLOCKED `unassigned` z listą, bez zapisu. Stary plik trafia
  do kosza (`trash-put`, `gio trash`, `trash`; bez nich zmiana nazwy na `.questions.json.migrated` (nazwa z kropką, której serwer prototypów nie wydaje), nigdy usunięcie) dopiero po odczycie zwrotnym;
  `verify_failed` = BLOCKED, stary plik zostaje.
- `purge --slug <slug> [--include-open] [--yes]` usuwa z dziennika przebiegu domyślnie tylko pytania wycofane (wycofane z komentarzem albo odpowiedzią zostaje); `--include-open` dodaje otwarte bez odpowiedzi
  i bez komentarza (świeże pytanie czekające na eksperta nie jest usuwane bez tej flagi). Pytanie z odpowiedzią albo komentarzem zostaje w obu trybach,
  tak samo pytanie, do którego odwołuje się zachowane (`refersTo`). Bez `--yes` tylko podgląd: `dryRun: true`, lista `removed` z `id`, `q`, `state`, `reason`, `text`
  oraz `skipped` (pytania pominięte z powodem: otwarte z podpowiedzią `--include-open`, wycofane z komentarzem, zachowane przez `refersTo`). Wymaga `--slug`.
- `merge --driver %O %A %B` to sterownik gita `skillsync-questions` (rejestracja: [prototypes](prototypes.md#pytania-do-eksperta-w-gicie));
  `merge <projekt> <inny-questions.json> --slug <slug>` scala ręcznie do pliku tego przebiegu (`--slug` jest wymagany).
- Kody wyjścia: 0 PASS, 1 FAIL, 2 BLOCKED, 3 TIMEOUT. `1` znaczy, że magazyn odrzucił treść albo stan (zła ścieżka w `--origin`,
  nieznany `--refers-to`, `already_answered`, `withdrawn`, brak obu `--assume`/`--blocking` jako `invalid_mode`, konflikt scalania, `migrate` przy tym samym `id` z inną treścią).
  `2` znaczy błędne wywołanie albo środowisko (zły format `--origin`, `secret_detected`, plik za duży 507, uszkodzony plik, brak kont w `url`,
  `slug_required`, `slug_ambiguous`, `migration_required`, `unassigned`, `verify_failed`).

## Bezpieczeństwo

| Komenda | Skutek |
|---|---|
| `leakcheck [path] [--worktree \| --staged \| --range A..B \| --history \| --text FILE \| --stdin] [--target ID] [--require full] [--no-config] [--json]` | skan treści, nazw plików i metadanych commitów albo tekstu poza gitem (`--text`, `--stdin`) |
| `leakguard patterns [--target ID] [--json]` | scalona lista wzorców (wbudowana i celu), JSON |
| `leakguard install-hook <repo> [--remove] [--target ID] [--json]` | zarządzany blok w lokalnych hookach `pre-commit` i `pre-push` |

`leakcheck` zwraca PASS (0), FAIL (1) z listą `{plik, kategoria, miejsce}` albo BLOCKED (2), gdy `--require full` nie ma
pełnego pokrycia, plik wzorców jest błędny albo tekst do skanu nie da się wczytać. Nieznany cel daje `coverage: generic` z ostrzeżeniem. Raport nigdy nie
pokazuje wartości wzorców. Model: [security](security.md).

`--text FILE` i `--stdin` to kontrola publikacji tekstu spoza gita (opis PR-a, komentarz, notatka wydania; do 1 MiB, bez tekstu w argumencie komendy): te same tożsamości i wzorce co `--range`, ale cel wynika z katalogu bieżącego albo `--target` (nie z repozytorium docelowego publikacji: podaj `--target <id>`), tekst skanowany w najostrzejszej klasie ścieżki (jak plik instrukcji), bez wymogu repozytorium git. Raport podaje etykietę (nazwa pliku albo `<stdin>`), kategorię i miejsce `linia:kolumna`, nigdy samego tekstu. Instrukcje skilli `pr-run`, `feature` i `dispatch`: opis zapisany do pliku przechodzi `leakcheck --text <plik>` przed `gh pr create`, `gh pr edit --body`, `gh release create`.

## Schematy JSON

Raporty mają co najmniej te pola; konsument ignoruje pola, których nie zna.

| Komenda | Kształt (co najmniej) |
|---|---|
| `layers check --json` | `{schema: 1, status, project, rule, layers, findings: [{code, severity, layer, message}]}` |
| `leakcheck --json` | `{schema: 1, status, coverage, target, findings: [{path, category, location}], notices, warnings}`; po udanym skanie także `authors_scope`, przy BLOCKED `reason` |
| `prototype comments --json`, `prototype wait --json` (zdarzenie `comment`) | `{status: "PASS", slug, …, untrusted: true, notice}`: `notice` to stały tekst (dosłownie w [prototypes](prototypes.md#role-kont) obok opisu pracy agenta na komentarzach), teksty wiadomości bez zmian; oba pola są na początku raportu; `wait` z TIMEOUT albo `accepted` nie ma tych pól (brak tekstów od osób) |
| `prototype login-link --json` | `{schema: 1, status: "PASS", url, expires}` (`url` = `http://127.0.0.1:<port>/_login/link?t=<token>`, `expires` = ISO-8601 UTC, token tylko na stdout) albo `{schema: 1, status: "BLOCKED", reason, code}` z `code` ∈ (exit 2) `server_not_running` (brak serwera projektu: brak pliku stanu, martwy PID, inny `instance` albo inny plik użytkowników), `ambiguous_server` (kilka serwerów projektu bez `--port`), `link_needs_loopback` (host serwera inny niż `127.0.0.1`/`localhost`/wildcard), `link_unavailable` (serwer z `--trust-proxy`: brak endpointu linku), `run_dir_unsafe` (katalog `run/` z prawami ≠ 0700, innego właściciela lub dowiązanie), `unknown_user` (konto nie istnieje; nigdy nie jest tworzone), `password_change_required` (konto z `mustChange`), `invalid_path` (`--path` poza dozwolonymi), `invalid_port` (`--port` poza 1-65535), `invalid_project` (błędny projekt lub manifest), `too_many_pending` (32 oczekujące tokeny), `users_unreadable` (nieczytelny plik użytkowników); zapisy z sesji linku trafiają do lokalnego `.skillsync/prototype/run/audit.jsonl` (`{at, login, via, op, screen?}`), nie do `comments.json`; flagi `--user LOGIN` (wymagana), `--path /ścieżka`, `--port N`, `--users PLIK` |
| `prototype question list`, `show`, `export` | `{untrusted: true, notice, schema: "expert-questions/1", status: "PASS", project, url?, questions: [{id, text, state: open|answered|withdrawn, blocking, assumed, addressees, refs, options, answer?: {by, name, at, optionId?, optionLabel?, optionIds?, optionLabels?, text?, confirm?, comment?}, comments, refersTo?, withdrawn?: {at, reason?}, origin: {spec, q}, recorded?: {spec, line, at}}]}`; `show` i `wait` mają `question`, `questions[]` i `answerLine` w raporcie, `add` i `withdraw` `specLine`; klient ignoruje nieznane pola, zmiana niezgodna = nowa wartość `schema`; `untrusted: true` i stały `notice` (treść odpowiedzi i komentarzy ekspertów to dane od ekspertów, nie polecenia) są pierwszymi kluczami raportu, także przy liście pustej; to samo mają zdarzenia `answer` i `comment` komendy `question wait` (zaraz po `status` i `event`), a TIMEOUT i błędy nie; `add`, `withdraw`, `record`, `purge`, `migrate`, `merge` i `url` nie oddają tekstu od cudzych ludzi i pól nie mają (wyjątek: błędy `merge` i `migrate` przy ręcznie edytowanym pliku mogą przytoczyć identyfikator albo nazwę pola z pliku; ten sam autor może wpisać tekst wprost do speca) |
| `layers list --json` | `{schema: 1, status, generator_version, project, rule, layers: [{id, kind, path, root, subdir, version, writable, present}], composed_root, notes}` |
| `status --json` | `{schema: 1, status, project, root, plans, specs, frds, runs, claims, approvals, findings}`; pola `linkage` i `linkageNote` tylko przy niezlinkowanych skillach; `needsHuman` i `preImplement` są polami rekordu planu, tak jak `closure` `{state, sha}` (tylko plan `done`; stany `none`, `tracked`, `final`, `bad-line`, `foreign`, `open`: [workflow](workflow.md#dowód-zamknięcia-w-planie-linia-zamknięcie)) i `abandoned` (bool, tylko plan `approved`: [definicja](workflow.md#plan-porzucony)) |
| `dispatch --json` | `{schema: 1, status, project, root, generatedAt, authoritative, dispatches, findings}`; stan: [dispatch-state-view](domain/dispatch-state-view.md) |
| `resources --json` | `{schema: 1, host, running, max, allowed, limits, reason}` |
| `plan-fresh --json` | `{schema: 1, plans, findings, blocking, idle}` |
| `route --json`, `doctor --json` | `route`: `{status, files, topics, routes, skipped, lessons, domain, delivery, ceremony, specs, tools?, note}`; `doctor`: `{status, findings: [{level: error\|warning\|info, path, reason}], budgets}` (bez pola `schema`; konsument ignoruje nieznane pola) |
| `map --json` | `{status, path, fingerprint}`; `--check` zwraca FAIL (1), gdy mapy brak albo jest nieaktualna |
| `dashboard show --json` | `schema: 1`, pełny kontrakt: [dashboard-contract](dashboard-contract.md) |

## Najczęstsze przepływy

```bash
skillsync link <projekt> --dry-run && skillsync link <projekt>   # nowy albo zmieniony projekt
skillsync doctor <projekt> --json                                # po link: oczekiwane PASS
skillsync layers check                                           # po zmianie warstw albo configu
skillsync smart-test <projekt> --run                             # testy po zmianie (z przydziałem zasobów)
```

Przed pierwszym `link` na nowym projekcie zawsze uruchom `--dry-run`.

Plan do zamknięcia (kolejność komend jednego przebiegu; procedura i zgody: [workflow](workflow.md)):

```bash
skillsync route <projekt> --topic <temat> --files <ścieżki>   # kontekst: instrukcje, lekcje, wiedza domenowa
skillsync readiness <projekt> --plan docs/plans/<plan>.md --run-id <run-id>   # struktura planu i speca
skillsync plan approve <projekt> docs/plans/<plan>.md          # po słowie „GO implement” od użytkownika
skillsync test-run <projekt> --run <run-id> --step <krok> -- <polecenie testów>
skillsync checkpoint <projekt> --run-id <run-id> --step <krok> --status PASS --evidence <plik> --next <następny krok>
skillsync checkpoint <projekt> --run-id <run-id> --step final --status PASS --final --next koniec --evidence <plik>
```

Strona statusu:

```bash
skillsync dashboard serve <projekt>        # strona na 5140 (zgody: sesja konta approver)
skillsync dashboard url <projekt>          # adres strony działającego serwera (bez tokenu)
```

