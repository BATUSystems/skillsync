---
name: agent-runtime-pitfalls
description: Potwierdzone pułapki środowiska agentów (zsh, scratchpad, procesy w tle, npm, testy Django/Angular, dowody, worktree) i jak ich unikać. Czytaj przed skryptami i testami.
---

# Pułapki środowiska pracy agentów

Każda reguła wynika z rzeczywistej awarii. Gdy reguła przestaje
pasować do środowiska, popraw ją tutaj zamiast obchodzić.

## Powłoka (zsh użytkownika z aliasami)

- **`cp`, `mv`, `rm` mogą być aliasami z `-i`.** Polecenie pyta „nadpisać?” i w skrypcie
  bez terminala czeka w nieskończoność. W skryptach podumysłów używaj `command cp -f`,
  `command mv -f`, `command rm -f` (dwie powłoki wisiały na
  `cp -i` przy przywracaniu pliku z kopii po teście mutacyjnym).
- **`ls` może być aliasem do `lsd`** z kolorami i ikonami — wynik nie nadaje się do
  parsowania. Do list plików używaj `command ls -1`, `find` albo `git ls-files`.
- **zsh nie dzieli niecytowanych zmiennych na słowa.** `env $ENV cmd` (gdy
  `ENV="A=1 B=2"`) przekazuje jeden argument, a `${3:+--opcja "$3"}` daje jedno słowo
  `--opcja tekst` — polecenie dostaje zły argument i pada albo cicho ignoruje opcję
  (`az boards work-item update` z komentarzem się nie wykonał,
  a `env $ENV` nie ustawił zmiennych). Zmienne środowiskowe podawaj wprost przed poleceniem,
  opcje opcjonalne buduj w tablicy (`args=(--opcja "$x"); cmd "${args[@]}"`) albo użyj
  `${=VAR}` świadomie. To samo w pętlach: `for x in "a b"; do set -- $x` nie rozbija `x`
  (brak `SH_WORD_SPLIT`); pisz `set -- ${=x}` albo osobne wywołania (`skillsync claim` zwracał BLOCKED przy poprawnych danych).
- **`command` to builtin powłoki — `xargs` go nie uruchomi** (`xargs: nie udało się
  uruchomić polecenia 'command'`); aliasy i tak nie działają w `xargs`, więc podawaj ścieżkę
  bezwzględną: `xargs -0 -I{} /usr/bin/cp --parents {} <cel>/` (skutek: snapshot
  worktree bez nowych plików).
- **Niecytowany glob w argumencie kończy polecenie w zsh** (`grep --include=*.cs …` →
  `no matches found`, polecenie się nie wykonuje). Cytuj: `--include='*.cs'`, `'*.md'`.
- Testy mutacyjne (celowe psucie pliku) zawsze kończ przywróceniem przez `git checkout --
  <plik>` albo `command cp -f`, a przed commitem sprawdź `git diff`. Kopie robocze mutacji
  (skrypty, podumysły) zapisuj w scratchpadzie lub katalogu worktree, nigdy wprost w `/tmp`:
  kopia `prototype.py` w `/tmp/x` wywróciła test Skillsync, który zakłada brak `/tmp/x`
  (`tests/test_skillsync_knowledge_init.py:97`).
  Gdy przywrócenie zawiesi się (`cp -i`) albo skrypt zostanie przerwany, **mutant zostaje na dysku**:
  przed dalszą pracą porównaj plik z kopią (`diff -q`) lub `git diff`, nie zakładaj stanu wyjściowego.

## Scratchpad i narzędzia sesji

- **Scratchpad sesji (`/tmp/claude-…/scratchpad`) znika przy restarcie lub wznowieniu
  sesji** — razem z narzędziami tam zainstalowanymi (`func`, `azurite`), logami i plikami
  roboczymi. Przed biegiem sprawdzaj narzędzia (`<narzędzie> --version`), a przed użyciem
  pliku jego istnienie: `az repos pr create --description "$(cat <plik>)"` z nieistniejącym
  plikiem utworzył PR z pustym opisem. Narzędzia potrzebne wielokrotnie
  instaluj w trwałym katalogu (np. `~/.local/share/agent-tools`) albo odtwarzaj na starcie.
  `/tmp` to tmpfs: awaria zasilania czyści naraz scratchpady wszystkich sesji. Dowody i kopie
  przebiegów trzymaj w repo (`.skillsync/runs/`, ignorowane) albo w worktree (po awarii przepadły scratchpady kart i dyspozytora, dowody przetrwały w
  `.skillsync/runs`).
- **chrome-devtools MCP łączy się z `127.0.0.1:9222`** i bez działającego Chrome zwraca
  `Could not connect to Chrome`. Uruchom go w tle na czas sprawdzenia:
  `google-chrome-stable --headless=new --remote-debugging-port=9222
  --remote-debugging-address=127.0.0.1 --user-data-dir=<katalog poza /tmp>`, a po sprawdzeniu
  zatrzymaj proces i sprawdź port (`ss -ltn`); dzierżawa przeglądarki obejmuje też port 9222.
  **Najpierw sprawdź, kto trzyma port** (`ss -ltnp 'sport = :9222'`). Jeśli 9222 na 127.0.0.1
  trzyma przeglądarka użytkownika, headless dostaje tylko `[::1]:9222`, a MCP steruje
  **prawdziwym Chrome użytkownika** (otwiera mu karty). Wtedy nie używaj MCP. Uruchom własny
  headless na innym porcie (np. 9333) i steruj nim przez CDP. Kart użytkownika nie ruszaj,
  a przypadkiem otwartą zamknij.
- **Zrzuty i odczyty przeglądarki rób w osobnym profilu bez rozszerzeń** (`--user-data-dir=<pusty katalog>`),
  a kolory weryfikuj z tokenów i computed styles (`getComputedStyle`), nie ze zrzutu. Rozszerzenie
  zmieniające motyw (np. dark mode) przekolorowuje stronę i fałszuje zrzuty, więc „zły kolor” bywa
  skutkiem rozszerzenia w profilu, a nie błędem strony. Profil użytkownika ma takie rozszerzenia.
- **`python3 -m http.server` nie wyłącza cache przeglądarki.** Chrome serwuje wtedy stare
  `prototype.js`/CSS po zmianie pliku. Skutek to fałszywa czerwień albo fałszywa zieleń
  przy powtórnym teście. Przed pomiarem włącz `Network.setCacheDisabled({cacheDisabled: true})` przez CDP
  albo otwórz plik z parametrem `?v=<mtime>`.
- **Domyślny `node`/`npm` w powłoce bywa stary** (npm 16 z domyślnego aliasu nvm), a projekt
  wymaga Node 22 (`.nvmrc`). Ustawiaj PATH bezwzględnie w tym samym poleceniu:
  `PATH=$HOME/.nvm/versions/node/v22.<x>/bin:$PATH npm …` (`nvm use` nie przetrwa między
  wywołaniami narzędzia).
- **`python3` z PATH bywa pyenv bez potrzebnych modułów** — `skillsync` pada na
  `No module named 'yaml'`, skrypty z PyJWT na brak `jwt`. Uruchamiaj
  `/usr/bin/python3 ~/.local/bin/skillsync …` i przekazuj `PYTHON=/usr/bin/python3`
  skryptom testowym. Maszyna główna użytkownika ma PyYAML także w pyenv 3.11.8, ale
  ten Python nie ma modułu `ssl`, więc `pip` nie pobierze z sieci; na innych maszynach nadal sprawdzaj.
- **GUI/IDE uruchomione przez `systemd --user` (np. przywrócenie sesji KDE) nie ma PATH z rc
  powłoki** — brakuje m.in. `~/.local/bin`, więc rozszerzenie lub agent dostaje
  `spawn skillsync ENOENT`, a w terminalu wszystko działa. Rozpoznanie: rodzic procesu IDE
  (`ps -o ppid= -p <pid>`) to `systemd --user`, PATH jak w `systemctl --user show-environment`.
  Narzędzia z `~/.local/bin` wołaj przez ścieżkę bezwzględną albo PATH z dopisanymi na końcu
  katalogami zapasowymi (`~/.local/bin`, `~/bin`, `/usr/local/bin`); nie czytaj PATH z powłoki
  logowania — wykonuje rc i offline potrafi zawisnąć (lekcja
  `docs/lessons/path-hosta-z-systemd-user.md`).
- **`pgrep -f` i `grep` łapią własną powłokę**, gdy wzorzec jest w treści polecenia. Licz przez `pgrep -f '[w]zorzec' | wc -l` (nawias na pierwszej literze wzorca),
  a przynależność procesu do katalogu sprawdzaj w `/proc/<pid>/cwd`.

## Procesy

- **Szukaj procesów po katalogu roboczym, nie po wzorcu nazwy.** `pgrep -f` łapie własną
  powłokę, której wiersz poleceń zawiera szukany tekst (fałszywe alarmy), i pomija procesy
  o innej nazwie niż zgadnięta (karta szukała `sed -i`/`ng test`, a wisiał `cp -i`).
  Wzorzec: `for p in $(pgrep -f '<wzorzec>'); do echo "$p $(readlink /proc/$p/cwd) $(ps -o etime=,stat= -p $p)"; done`.
- **`cat > plik` bez przekierowanego wejścia czeka w nieskończoność na stdin**, polecenie
  przechodzi w tło po limicie czasu i zostaje wiszący `zsh -c …` z `cat`. Pliki zapisuj
  narzędziem do zapisu albo heredociem (`cat > plik <<'END'`). Sprzątając taki proces, zabij
  najpierw powłokę-rodzica, potem `cat` — samo zamknięcie `cat` pozwala powłoce wykonać
  resztę zaplanowanego polecenia (np. drugi raz tę samą edycję pliku).
- Kończ wyłącznie procesy uruchomione przez siebie, po PID, sprawdziwszy cwd i rodzica
  (`ps -o ppid=`). Nigdy `pkill`/`killall` wzorcem — zabija testy innych sesji.
- Długi bieg testów uruchamiaj w tle z logiem do pliku i `timeout`; kończ go dopiero po
  sprawdzeniu, że naprawdę wisi (stan `S` bez postępu w logu), a nie że trwa.
- **Raportuj PID procesu testów, nie wrappera.** `nohup`/`zsh -c` kończy się od razu i zostawia
  łańcuch `zsh -c` → `timeout` → `python -m pytest`; zgłoszony PID wrappera „nie istnieje”, a
  liczenie procesów wzorcem daje trzy „biegi” zamiast jednego (dwa razy PID
  wrappera, raz fałszywy alarm o trzech równoległych biegach). Podawaj PID z
  `pgrep -f 'venv/bin/python -m pytest'` i jego cwd.

## Podumysły w tle

- **Review lub inny podumysł w tle może nie wrócić** (zerwana sesja, restart). Ustal termin w zleceniu;
  po nim uznaj wynik za utracony, wyślij `STATUS` i puść zadanie ponownie — nie czekaj bez śladu
  (karta czekała na review, które nie wróciło; dyspozytor wykrył to po
  braku zapisów w transkrypcie).

## npm i lockfile

- **`npm ci --dry-run` potrafi wyczyścić `node_modules`** w bieżącym katalogu (obserwacja). Do sprawdzenia spójności lockfile użyj prawdziwego `npm ci` w worktree.
- **Nowszy npm przepisuje niezwiązane pola lockfile** (np. usunął `libc` w całym pliku).
  Przy dodawaniu zależności: zmiana tylko w jednym commicie, a dowód spójności to
  `sha256sum package-lock.json package.json` przed i po prawdziwym `npm ci` (muszą być
  identyczne) oraz `npm ls <pakiet>` bez błędów peer/invalid.

## Testy

- **Windows: `Path.write_text` zamienia LF na CRLF**, więc skrót (sha256) liczony w teście z tekstu z LF
  nie zgadza się ze skrótem pliku. Helper fikstur, który zapisuje dowody, pisze z `newline=''` albo bajtami.
  Ścieżki w wyjściach JSON i w stanie przechodzą przez `.as_posix()`, nie `str(relative_to(...))`.
- **Dowód z testów jest ważny tylko, gdy bieg zaczął się po ostatniej zmianie kodu.**
  Porównuj godzinę startu w logu (`Start at` w Vitest, nagłówek logu) z `stat -c %y` plików
  z commita. Zmiana testu po biegu unieważnia dowód.
- **`element.click()` z evaluate nie dowodzi, że element da się kliknąć** — omija nakładki
  (panele, dialogi). W sprawdzeniu UI klikaj wskaźnikiem (chrome-devtools `click` po uid,
  CDP `Input.dispatchMouseEvent`), sprawdzaj `document.elementFromPoint` w miejscu kliku
  i powtarzaj w kilku szerokościach okna (np. 375, 1000, 1400 px). Dowód: panel komentarzy
  prototypu poniżej 64rem nakrywał treść — klik wskaźnikiem trafiał w panel, a
  `element.click()` „działał”.
- Logi zapisuj z nagłówkiem: komenda (bez sekretów), cwd, commit, start, load; liczby i
  `exit=<n>` na końcu. Scratchpad sesji znika po restarcie — trwałą kopię dowodu, potrzebną
  do wznowienia, trzymaj w worktree poza commitem albo w raporcie.
- **Django `manage.py test --parallel N` może paść na `cannot pickle 'traceback' object`**,
  co ukrywa prawdziwy wyjątek z testu w podprocesie. Taki wynik to nie „flake”: puść bieg
  seryjnie i dopiero on rozstrzyga.
- Wysokie obciążenie (load > ok. 12) psuje testy czasowe — zapisuj load przy starcie;
  przy kilku sesjach jeden pełny bieg frontu naraz, seryjne biegi Django mogą iść równolegle
  na osobnych bazach (np. PostgreSQL `postgresql:///<projekt>_<paczka>`).
- **Równoległe biegi Django na jednym serwerze PostgreSQL dzielą tablicę blokad**
  (`max_locks_per_transaction × max_connections`). Przy migracjach schematów w testach (django-tenants)
  dwa pełne biegi przy domyślnym 64 padają na `out of shared memory`; po
  `ALTER SYSTEM SET max_locks_per_transaction = 256` + restarcie szły cztery naraz. W raporcie podawaj
  `lock-errors` = liczba `OutOfMemory|max_locks` w logu.
- **Liczba testów w pełnym biegu ma się zgadzać z oczekiwaną.** Katalog bez `__init__.py`
  (namespace package) jest pomijany przez `unittest discover`, także w `manage.py test`
  i w CI — testy przechodzą przy uruchomieniu z etykietą, a w pełnym biegu nie istnieją
  (brak `core/__init__.py` ukrywał testy bezpieczeństwa). Gdy paczka
  dodaje testy, a liczba się nie zmienia, policz wykryte:
  `DiscoverRunner().build_suite([]).countTestCases()` albo porównaj z `build_suite(["<app>"])`.
- **xUnit może pominąć przypadek theory z tym samym ID** (dane różniące się tylko długim
  napisem są skracane w nazwie): w logu `Skipping test case with duplicate ID`, a suma wygląda
  wiarygodnie. Dodaj pierwszy parametr-etykietę i sprawdzaj logi `grep -i duplicate`.
- Testy „czerwone przed poprawką” mają padać na asercji, nie na błędzie kompilacji
  (np. brak metody) — inaczej dowodzą tylko, że kod się nie kompilował.
- **Testy migracji wstecz używają fabryk na starszym schemacie.** Gdy paczka zdejmuje z pola
  default albo `NOT NULL`, fabryka bez jawnej wartości daje `NULL`, a po cofnięciu migracji
  wiersz łamie stare ograniczenie (błędy w teście
  migracji innej paczki, niewidoczne w biegu celowanym `-k`). Przy zmianie defaultu przeszukaj
  testy migracji (`migrate_to`, `MigrationExecutor`) i podaj tam wartości jawnie.
- Nie uruchamiaj testów przy niedokończonym rebase (`git status`: „rebase in progress”):
  bieg mierzy stan pośredni i nie jest dowodem dla żadnego commita.
- **`psql` bez `-d` łączy się z bazą o nazwie użytkownika** (np. `user`), której zwykle nie ma: polecenie
  kończy się błędem, a skrypt sprzątający „usunął” bazy tylko w raporcie. Używaj `psql -d postgres` i sprawdzaj
  wynik (`select count(*) from pg_database where datname like ...`) po każdym `dropdb`.
- **Podumysł zapisuje pliki względem własnego cwd**, nie worktree orkiestratora: ścieżka `../<repo>-wt/<paczka>`
  liczona z worktree daje `<repo>-wt-wt/...`. W zleceniu podawaj ścieżki bezwzględne i sprawdzaj `git status` po kroku.
- **Nie uruchamiaj skryptu pomocniczego po samej nazwie.** Skrypt z katalogu tymczasowego (np. scratchpadu sesji) o nazwie
  sugerującej zapytanie SQL okazał się runnerem testów: zebrał 0 testów i na chwilę zmienił plik konfiguracji
  testów z danymi dostępowymi. Przed uruchomieniem przeczytaj jego początek i listę zapisywanych plików;
  po biegu sprawdź `git status` i `git diff` plików konfiguracji (przywróć je), a wynik „0 testów” traktuj
  jako brak dowodu, nie jako zielony bieg. Skrypty pomocnicze nazywaj według tego, co robią.
- **Mutację sprawdź `git diff` przed uruchomieniem testów.** `sed` bez dopasowania (kod
  wielolinijkowy, inne wcięcie, CRLF) kończy się sukcesem i nic nie zmienia, więc „mutant
  przeżył” jest fałszywy. Pusty diff = mutacja niezastosowana;
  edytuj narzędziem do edycji albo skryptem, który sprawdza liczbę zamian.
- **Każdy bieg mutanta ma mieć twardy limit pamięci i czasu.** Mutant może zamienić warunek
  na pętlę bez końca, która alokuje pamięć; `node --test` bez limitu zajął całą pamięć RAM
  i swap. Maszyna stanęła i trzeba było ją zrestartować (mutant `if (view)` → `if (true)`). W testowanym kodzie nie było pętli. Asercja nie przeszła
  na atrapie DOM, a `node:assert` zbudował komunikat porażki przez `inspect` z dużą głębokością
  i bez `customInspect`. Przeszedł przy tym cały cykliczny graf `parent↔children↔sink`.
  W atrapach DOM pola wskazujące na inne węzły (`parent`, `children`, `sink`, `listeners`)
  ustawiaj jako nieenumerowalne przez `Object.defineProperty(this, k, { enumerable: false })`.
  Hook `util.inspect.custom` tu nie pomaga, bo `assert` go pomija. Uruchamiaj
  `systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 timeout 300 node --test …`.
  Ten limit obejmuje całe drzewo procesów, także podprocesy testów. `NODE_OPTIONS=--max-old-space-size`
  nie wystarcza, bo nie ogranicza buforów poza stertą. Gdy w limit uderzy pojedynczy proces,
  dostajesz exit 137. `node --test` działa inaczej: OOM zabija tylko podproces pliku testowego,
  a rodzic kończy z exit 1, tak jak przy zwykłym nieudanym teście. Dlatego limit rozpoznawaj
  po braku podsumowania (`ℹ tests N`) albo po `oom-kill` w `journalctl --user`, nie po kodzie
  wyjścia. Zapisuj go jako `ZABITY-LIMIT`, nigdy jako PRZEŻYŁ. Ta sama zasada dotyczy
  pętli kalibracyjnych, fuzzingu i skryptów obciążeniowych.
- **`@vscode/test-electron`: `runTests` bez `version` pobiera najnowsze stabilne VS Code.**
  Worktree i main mogą wtedy testować na różnych wersjach: worktree miał 1.145.0,
  a main 1.144.x. Wersję przypinaj w jednym module, z którego importują runner i skrypty, i dodaj
  test-strażnika.
- **test-electron oddaje runnerowi tylko kod wyjścia**, więc w logu zostaje samo
  `Test run failed with code 1`. Niech suite zapisuje raport porażki do pliku wskazanego
  zmienną środowiskową (scenariusz, ostatni zaliczony blok, komunikat, stos), a runner niech go
  wypisze. Podmiana `console.log` w hoście rozszerzeń nie przechodzi do runnera, więc znacznik
  bloku zapisuj jawnie.

## Angular

- **`<iframe [src]="url">` ze zwykłym stringiem rzuca `NG0904`** (unsafe value in resource
  URL context) i podgląd się nie ładuje. Używaj `DomSanitizer.bypassSecurityTrustResourceUrl`
  wyłącznie dla adresów pasujących do ścisłej allowlisty (zakotwiczony regex ścieżki tego
  samego origin) i testuj odrzucenie innych.
- Store z `providedIn: 'root'` żyje przez całą sesję aplikacji: warunek „mam już dane tego
  projektu, nie pobieram” w konstruktorze strony daje nieaktualne dane po powrocie na
  stronę. Przy wejściu na stronę pobieraj zawsze (bez nadpisywania niezapisanych zmian).
- Kod po `await` w komponencie może wykonać się po jego zniszczeniu (`NG0911` przy
  `afterNextRender` na zniszczonym injectorze) — sprawdzaj flagę z `DestroyRef`.
- Klawisze Enter/Spacja na przycisku wewnątrz elementu z `(keydown.enter)` bąbelkują —
  przycisk usuwający wymaga `stopPropagation` na keydown albo rozdzielenia elementów.

## PDF (reportlab)

- `Frame.addFromList` nie obsługuje `KeepTogether` ani `keepWithNext` — „sierotę”
  (nagłówek sam na dole strony) trzeba obsłużyć własną pętlą wypełniania ramki; taka pętla
  używa prywatnych pól reportlaba, więc po aktualizacji biblioteki uruchom testy PDF.

## Git: składanie wydania z wielu worktree

- Paczki na osobnych gałęziach składaj na gałęzi wydania przez `git cherry-pick -x`; po
  każdym kroku sprawdź poprawność plików współdzielonych (np. `pl.json` jako JSON bez
  zdublowanych kluczy) i puść jeden pełny przebieg testów na złożonej gałęzi przed pushem.
- Przed usunięciem worktree po wydaniu sprawdź `git cherry origin/<gałąź> <gałąź-paczki>`
  — zero linii `+` oznacza, że każda zmiana jest już na zdalnej gałęzi mimo innych hashy.
- Push wydania tylko fast-forward; potwierdzaj hash przez `git ls-remote`.
- **Listę ścieżek do `git add` wypisuj jawnie**, a nie z filtra ani grepa po `git status`. Filtr łapie też
  nieśledzone pliki użytkownika, które leżą w drzewie obok pracy, i trafiają do commita. Przed commitem
  porównaj `git diff --cached --name-only` z listą kroku.

## Dostęp do serwerów i uprawnienia

- W trybie auto klasyfikator może odrzucić SSH do serwera jako odczyt z produkcji nawet
  przy regule `allow` w ustawieniach; jawne polecenie użytkownika w rozmowie może zmienić
  ocenę. Odmowy nie obchodź — zatrzymaj się i podaj użytkownikowi gotowe polecenie.
- `sudo` na zdalnym serwerze w nieinteraktywnym SSH kończy się `a password is required` —
  krok z `sudo` (np. restart usługi) zostaw użytkownikowi albo uzgodnij z nim wpis
  w sudoers tylko dla tego polecenia.
