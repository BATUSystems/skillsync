# Wersje i aktualizacje

Rdzeń, Skippster i każda warstwa mają wersję [SemVer](https://semver.org/lang/pl/) i tagi `vX.Y.Z`.
Warstwa deklaruje `core_min`: minimalną wersję rdzenia, z którą działa. `link` i `layers check` zwracają BLOCKED (2),
a `doctor` zgłasza błąd (FAIL, 1), gdy rdzeń jest za stary.

## Kiedy podbić wersję

| | Rdzeń | Warstwa org/personal |
|---|---|---|
| **MAJOR** | niezgodna zmiana CLI, kodów wyjścia, schematów JSON, kluczy manifestu/configu, formatu artefaktów; usunięcie lub zmiana nazwy skilla, roli, szablonu | usunięcie lub zmiana nazwy skilla, szablonu, roli; zmiana ścieżki pliku wskazywanego przez inne warstwy; podniesienie `core_min` o MAJOR |
| **MINOR** | nowe komendy, klucze, skille, role zgodne wstecz; rozszerzenie formatu pliku o pole, które starszy rdzeń ignoruje bez eskalacji uprawnień | nowy skill, szablon, fragment; podniesienie `core_min` o MINOR |
| **PATCH** | poprawki, dokumentacja | poprawki tekstu, aktualizacja danych |

## Co oznacza `core_min`

- Wersja rdzenia to wersja generatora.
- `layers check` i `link` zwracają BLOCKED (2), gdy `core_min` warstwy jest wyższe niż wersja generatora.
- `doctor` zgłasza to jako błąd (FAIL, 1).
- Rozjazd wersji generatora i warstwy `core`, który nie narusza `core_min`, to ostrzeżenie.
- Warstwa, która podnosi `core_min` o MAJOR, sama wymaga wpisu **Breaking** w `CHANGELOG.md`.

Wersję generatora pokazuje `skillsync --version`.

Pakiet aktualizujesz ponownym zainstalowaniem z tagu (`uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z`); zobacz [installation.md](installation.md#aktualizacja).

## Przejście na nową wersję MAJOR

1. Przeczytaj sekcję **Breaking** w `CHANGELOG.md` (zmiany niezgodne są tam wypisane).
2. Zaktualizuj rdzeń, potem warstwy.
3. `skillsync layers check` i `skillsync doctor` w projektach.
4. `skillsync link <projekt>` w każdym projekcie z `discover_paths` (albo `skillsync link` bez ścieżki).

## Przejście 1.3.0 → 1.4.0

Wydanie 1.4.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Zbiera pięć zmian: szacunek czasu (`eta`) na stronie statusu, drobne poprawki techniczne (publiczny adres prototypów, JSON w `show`/`wait`, port dashboardu), znacznik pisania z odnowieniem i zwolnieniem, polecenia w komentarzach i decyzje programisty, oznaczenie niezaufanej treści w komendach `question`. Warstwa `core` ma `core_min: 1.4.0`:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.4.0`, potem `skillsync layers update core --ref v1.4.0`.
2. Istniejące prototypy dostają nowy arkusz i silnik (stan przełączników, mniejsze pole komentarza, baner zapisu z godziną i „Zwolnij”) przez `skillsync prototype migrate`.
3. Pole `reason` w pytaniach do eksperta (`question withdraw --reason`): starsza wersja odrzuci plik `questions.json` z tym polem, więc zaktualizuj rdzeń u wszystkich pracujących na wspólnym projekcie, zanim ktokolwiek go użyje.
4. Raporty `prototype comments`, `prototype wait` i `question list|show|export|wait` mają dwa nowe klucze na początku (`untrusted`, `notice`); konsument pomijający nieznane klucze nie jest dotknięty.
5. Uruchom ponownie działający `prototype serve` i serwer strony statusu.

## Przejście 1.4.0 → 1.4.1

Wydanie 1.4.1 jest PATCH (zgodne wstecz w kontraktach: sekcja **Breaking** w `CHANGELOG.md` to „None.”), ale zmienia mechanizm blokady pliku zgód `approvals.jsonl` (zob. [dashboard-contract](dashboard-contract.md), „Blokada”).

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.4.1`, potem `skillsync layers update core --ref v1.4.1`.
2. **Uruchom ponownie działający `skillsync dashboard serve` PRZED pierwszą zgodą po aktualizacji i zaktualizuj każdą kopię `skillsync` (też z innego środowiska wirtualnego), z której wołasz `approvals record`.** Działający proces ma w pamięci stary kod i zapisuje zgody starą blokadą (plik `approvals.jsonl.lock` tworzony wyłącznie, `O_EXCL`), a nowy kod bierze blokadę systemową na tym samym pliku. Stary pisarz respektuje nowego (plik `.lock` istnieje przez cały czas jego trzymania, więc `O_EXCL` starego zawodzi i stary czeka), ale nowy nie widzi starego: otwiera plik `.lock` starego pisarza i blokuje go bez czekania, więc w tej samej chwili piszą obaj, a przy zwolnieniu nowy usuwa plik, który mógł być starego. Oba dopisują linię `O_APPEND` z `fsync`, więc nie uszkodzą pliku, ale mogą zapisać dwa razy tę samą parę słowo + sha. Czytelnicy zgód są na zdublowany wpis odporni (liczy się ostatni wpis na parę przebieg-słowo, test `test_a_duplicate_entry_in_the_file_is_harmless_for_the_readers`), więc skutek jest kosmetyczny. Po restarcie serwera i aktualizacji wszystkich kopii problem znika. Plik `approvals.jsonl.lock` pozostały po starym zapisie lub po awarii niczemu nie szkodzi: nowy kod go ignoruje.
3. Systemy plików bez blokad systemowych (niektóre udziały sieciowe, sterowniki FUSE): `approvals record` i zapis zgody ze strony statusu kończą się `BLOCKED` z powodem „file locks are not supported”, zamiast czekać (to jedyna zmiana zgodności tej wersji: stara blokada plikowa działała na każdym systemie plików z atomowym tworzeniem pliku). Trzymaj `.skillsync/` na dysku lokalnym. To samo rozróżnienie dotyczy blokad pytań do eksperta, komentarzy i użytkowników serwera prototypów (błąd 503 `lock_unsupported` zamiast `lock_timeout` po 5 s; polecenia `prototype user` zgłaszają `lock_unsupported`). Błąd `ENOLCK` (brak menedżera blokad) jest przez pierwszą połowę czasu oczekiwania ponawiany, bo bywa chwilowy.

## Przejście 1.5.0 → 1.6.0

Wydanie 1.6.0 jest MINOR z jednym wpisem **Breaking** w `CHANGELOG.md`: konto zalogowane na serwerze prototypów bez roli eksperta dostaje przy `GET /_api/v1/questions`, `/_api/v1/questions/<id>` i trasach przebiegu `GET /_api/v1/prototypes/<slug>/questions[/<id>]` odpowiedź `200` z podglądem tylko do odczytu (`preview: true`) zamiast `403`; klient, który po `403` poznawał brak roli, ma czytać `preview`. Zapis nadal kończy się `403`. Reszta jest zgodna wstecz: nowe klucze `dashboard show` i `/api/status` są opcjonalne (`schema: 1`), a stary renderer je pomija.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.6.0`, potem `skillsync layers update core --ref v1.6.0`.
2. Tryb prezentacji prototypu dostają tylko prototypy po `skillsync prototype migrate` (zmiana arkusza stylów może unieważnić akceptację ekranów; lista jest w polu `acceptance` wyniku).
3. Strona statusu na dwóch portach (opcjonalnie): jedno wywołanie `skillsync dashboard serve <projekt> --read-port 5140 --approve-port 5141` zastępuje dwie instancje (jedną z `--no-approvals`). Najpierw zatrzymaj stare procesy; oba porty muszą być wolne, inaczej start kończy się `BLOCKED` bez skutków ubocznych. Token zgód idzie zwykłym HTTP: w sieci nieznanej użyj `--approve-bind 127.0.0.1` albo tunelu SSH, a tunele kieruj na port odczytu. Port odczytu nie wymaga logowania i pokazuje treść pytań oraz loginy adresatów każdemu, kto ma do niego dostęp w sieci (świadoma decyzja właściciela): w sieci niezaufanej zawęź go przez `--read-bind 127.0.0.1`.
4. Warstwa, która dodaje pozycje do `workflow.review.prototype`, wymaga `core_min` 1.6.0 (starszy rdzeń nie ma profilu `prototyp`, `layers check` blokuje warstwę).
5. Konsument `skillsync status` ma przyjąć nieznaną wartość `acceptance` w `plans[].needsHuman`.
6. Uruchom ponownie działający `skillsync dashboard serve` i `prototype serve`.

## Przejście 1.6.0 → 1.6.1

Wydanie 1.6.1 jest PATCH (sekcja **Breaking** w `CHANGELOG.md` to „None.”) z poprawkami maskowania sekretów na stronie statusu (sekcja **Security**).

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.6.1`, potem `skillsync layers update core --ref v1.6.1`.
2. Uruchom ponownie działający `skillsync dashboard serve` (proces sprzed aktualizacji ma w pamięci stary kod i nadal oddaje `GET /api/doc` bez maskowania) oraz `prototype serve`.
3. Uwagi o widoczności komentarzy i odpowiedzi trafiają do istniejącego prototypu dopiero po `skillsync prototype migrate` (kopiowany jest tylko plik silnika `prototype.js`; arkusze stylów się nie zmieniają, więc akceptacja ekranów pozostaje ważna).
4. Projekt, który śledzi w git dowiązania skilli: po `git worktree add` uruchom `skillsync link <worktree>`; `doctor` ostrzega o śledzonych plikach generowanych i podaje gotowe `git rm --cached`. Na Windows z `core.symlinks=false` git zapisuje śledzone dowiązanie jako zwykły plik: `doctor` nazywa ten stan, a `skillsync link <projekt>` zastępuje takie pliki.
5. Konsument JSON-a `doctor`: `reason` znalezisk „Missing installed skill” i o dowiązaniu bez celu kończy się podpowiedzią `run skillsync link <projekt>`, więc dopasowuj początek tekstu. Raport `guard` może mieć pola `skipped` i `reason`.
6. `layers check`, `doctor` i `link` ostrzegają (`rule-unreachable`) o wpisie `layer_rules`, którego `match` powtarza wcześniejszy: scal takie wpisy w jeden.

## Przejście 1.7.0 → 1.8.0

Wydanie 1.8.0 ma dwie zmiany zachowania (sekcja **Breaking** w `CHANGELOG.md`): domyślny nasłuch strony statusu (punkty 2–5) i wynik `prototype sync --check` (punkt 6). Pozostałe punkty to nowe funkcje, które włączają się same albo wymagają jednego polecenia. Flagi i schematy JSON zostają w `schema: 1`.

1. **Rdzeń.** `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.8.0`, potem `skillsync layers update core --ref v1.8.0`. Python 3.11 jest wspierany: polecenia z opcją między argumentami pozycyjnymi (`prototype sync <ścieżka> --json <slug>`, `prototype gate`, `prototype question wait` i inne) działają tak samo jak na 3.12 i nowszych; nic nie trzeba zmieniać.
2. **Strona statusu: dwa gniazda i sieć lokalna (Breaking).** `skillsync dashboard serve <projekt>` bez flag portów działa teraz jako dwa gniazda: odczyt na 5140 na wszystkich interfejsach (`0.0.0.0`), zgody na 5141 tylko na tej maszynie (`127.0.0.1`). Wcześniej jedno gniazdo na pętli zwrotnej obsługiwało obie role. Co zobaczysz:
   - Adres zgód z tokenem podaje `skillsync dashboard url <projekt>`; adresy odczytu dla innych urządzeń: `skillsync dashboard url <projekt> --lan` (jeden na linię, w `--json` pole `lanUrls`). Strona odczytu odpowiada też pod nazwą maszyny (`<nazwa>`, `<nazwa>.local`); inna nazwa daje 403 z podpowiedzią flagi `--read-host`.
   - Start wypisuje ostrzeżenia: strona odczytu jest widoczna w sieci (plany, specyfikacje, wyniki; sekrety maskowane heurystycznie; ruch nieszyfrowany), jak wrócić do starego zachowania i że zgody są tylko lokalne.
   - Przy pierwszym nasłuchu na `0.0.0.0` zapora systemu może zapytać o dostęp: Windows Firewall (zezwól w sieci prywatnej, żeby zobaczyć stronę z telefonu; odmowa zostawia stronę lokalną), a na macOS zapora aplikacji.
   - Ustawienia trzymasz w sekcji `dashboard:` configu maszyny albo manifestu projektu (`local_only`, `read_bind`, `approve_bind`; zob. [manifest](manifest.md)); manifest projektu może tylko zawężać, szerszy adres to błąd `doctor` i BLOCKED przy `serve`.
3. **Jawne porty i zgody z sieci.** Skrypt lub usługa z jawnymi `--read-port/--approve-port`, z samym `--read-port` albo z `--no-approvals` ma od teraz gniazdo odczytu na `0.0.0.0` (wcześniej na pętli zwrotnej): plany, specyfikacje i wyniki są widoczne w sieci. Żeby zostało lokalnie, dodaj `--read-bind 127.0.0.1` albo ustaw `dashboard.local_only: true` (zob. punkt 4). Kto dawał zgody z telefonu lub innego komputera, dopisuje `--approve-bind 0.0.0.0` (albo `approve_bind: 0.0.0.0` w sekcji `dashboard:`); token zgód idzie wtedy nieszyfrowanym HTTP.
4. **Po staremu, tylko ta maszyna:** `dashboard.local_only: true` w configu maszyny ([configuration](configuration.md#pozostałe-klucze)) albo `--read-bind 127.0.0.1` przy starcie. Stary tryb jednego portu zostaje: `skillsync dashboard serve <projekt> --port 5140`. Uruchom ponownie działający `dashboard serve` i zatrzymaj stare procesy na portach 5140 i 5141: zajęty port daje BLOCKED z numerem portu i właścicielem (serwer nie przechodzi na inny port).
5. **`serve --host ADR` i `serve --approve-host NAZWA` bez flag portów** kończą się BLOCKED z podpowiedzią (dotąd znaczyły jeden port z bramką `Host`). Stary tryb, także za tunelem: `skillsync dashboard serve <projekt> --port 5140 --approve-host <nazwa tunelu>`. W trybie dwóch portów `--host` przesuwa tylko odczyt; zgody z sieci wymagają `--approve-bind 0.0.0.0`.
6. **`prototype sync --check` (Breaking).** Kończy się `FAIL` (kod 1, pole `missing`), gdy motyw nie definiuje wszystkich tokenów z `required-tokens.txt`; wcześniej tylko ostrzegał, a wynik to `PASS`. Dopisz brakujące tokeny (lista jest w komunikacie) w arkuszu z `prototype.theme.tokens_from` albo w `theme.css`. Nie ma przełącznika, który wyłącza kontrolę. `sync` bez `--check`, `init` i `migrate` nadal tylko ostrzegają.
7. **Prototypy: motyw, pokaz, migracja.**
   - `prototype.dark_theme: false` w manifeście wyłącza ciemny motyw i przycisk „Motyw”. Po dopisaniu klucza uruchom `skillsync prototype migrate <projekt> <slug>` (nowa kopia silnika i atrybuty `data-theme="light" data-proto-dark="off"` na `<html>`); akceptacje ekranów zostają, `migrate` pokazuje `acceptance.lost: 0`. Bez `migrate` stara kopia silnika zignoruje klucz.
   - `skillsync prototype serve <projekt> --demo` to pokaz tylko do odczytu bez kont (zob. [prototypes](prototypes.md#pokaz-tylko-do-odczytu-serve---demo)): domyślnie na `0.0.0.0`, strony są publiczne (`--host 127.0.0.1` zawęża), nie łączy się z `--login-to-view` (BLOCKED). Stara kopia silnika pokaże narzędzia komentowania (serwer i tak odmówi każdego zapisu), więc uruchom `prototype migrate`.
   - Uruchom ponownie działający `prototype serve`.
8. **Testy: `test-run`, kroki `[T]`, zakładka „Testy”.**
   - Testy kroku planu uruchamiaj przez `skillsync test-run <projekt> --run <id> --step <id> -- <polecenie>` (opcje: `--junit`, `--format`, `--allow-no-count`, `--allow-skips`, `--import PLIK --source-sha SHA`; zob. [cli](cli.md)). `validate` i `smart-test` nie zapisują zdarzeń testów; ręcznie puszczone komendy nie są widoczne na stronie. Zakładka „Testy” jest ukryta, dopóki przebieg nie ma pliku zdarzeń.
   - Kroki testowe oznacz w planie `[T]` (na początku lub końcu tytułu kroku w `## Progress` albo w komórce `Krok` tabeli). `checkpoint --status PASS` takiego kroku wymaga dowodu z `test-run` albo `--no-tests <powód>` (`doctor` ostrzega `tests-waived`).
   - Wynik `test-run` to `PASS` tylko przy znanej, dodatniej liczbie wykonanych testów; zero testów, nieznana liczba i pominięcia dają `BLOCKED`.
   - Lista testów na stronie zależy od narzędzia (szczegóły i granice: [test-adapters](test-adapters.md)):

     | Narzędzie | Lista testów i stan na żywo | Jak uzyskać listę |
     |---|---|---|
     | `unittest` | tak, stan „uruchomiony” | `python -m unittest -v` |
     | `pytest` | tak, stan „uruchomiony” | `pytest -v` (przy `-n`/xdist stan „uruchomiony” jest niewiarygodny); raport: `--junit-xml` |
     | `dotnet test` | tak, wynik po końcu testu | TRX: `--logger trx` (VSTest) albo `--report-xunit-trx` (Microsoft.Testing.Platform; `--output Detailed` drukuje też zaliczone) |
     | `cargo test` | tak (`--test-threads=1`: też stan „uruchomiony”) | domyślny format libtest |
     | Vitest | tak, po końcu testu | `--reporter=verbose`; bez niego same liczniki |
     | Karma | same liczniki | raport `karma-junit-reporter` i `--junit <plik>` dają nazwy |
     | `ng test` | liczniki; lista jak Vitest albo Karma | `--reporters=verbose` (Vitest) albo raport JUnit (Karma) |
     | Playwright | tak, po końcu testu | reporter `list` |
     | Cypress | tak, po końcu testu | reporter `spec` |

     Narzędzia webowe nie drukują linii startu pojedynczego testu, więc test jest „oczekujący” do wyniku. Linia `adapter <nazwa>` na stderr mówi, który adapter wybrano albo jak go wskazać (`--format <nazwa>`, `--format none`).
   - **Wyniki testów nie są maskowane** (strona statusu, także port odczytu widoczny w sieci lokalnej, i pliki `.skillsync/runs/<run>/tests/`): sprawdź, czy `.skillsync/` jest w `.gitignore` (`doctor`: `runs-not-ignored`), a kto chce maskę, ustawia `tests.mask: on` w `.github/.skillsync.yaml`. Maskowanie wiersza polecenia biegu jest heurystyką.
   - Konsument JSON `skillsync status` i strony statusu: przyjmij nieznaną wartość `tests` w `plans[].needsHuman` (krok testowy zamknięty bez testów) oraz nowe opcjonalne pola `tests`, `progress.phases[].steps[].tests`, `lanUrls` i `bind` w plikach stanu. Wtyczka edytora ignoruje nieznane pola, a zakładkę „Testy” dostanie z własnym wydaniem.
9. **Haki Claude Code.** Po następnym `skillsync link` projekt dostaje w ignorowanym `.claude/settings.local.json` wpis `PreToolUse` na `Bash` i skrypty w `.claude/hooks/` (część usuwania i ostrzeżenie o testach: projekty ze skillem `feature`, `agentflow` albo `dispatch`; zakaz stopek narzędzi AI: każdy projekt z Claude Code, zob. „Przejście 1.8.0 → 1.9.0”):
   - `guard-commands.py` odmawia trwałego usuwania (`rm -r`, `git clean -f`, `git worktree remove --force`, `find -delete`, ...) poza katalogami tymczasowymi i odtwarzalnymi; odmowa podaje zamiennik (`trash -- <ścieżki>`). Jednorazowy wyjątek zatwierdza użytkownik (`SKILLSYNC_GUARD_EXCEPT='<powód>'`, ślad w `.skillsync/hook-exceptions.jsonl`). Klucze: `hooks.guard_delete: deny|off`, `hooks.delete_allow`.
   - Ten sam wpis niesie ostrzeżenie o szerokim biegu testów poza `skillsync test-run`: `guard-tests.py` i `test-tools.json` obok. Ostrzega, nigdy nie blokuje, i odzywa się po wykonaniu polecenia. Klucze: `hooks.guard_tests: warn|off`, `hooks.test_wrappers` (prefiksy liczone jak opakowanie), `hooks.wide_modules` (od ilu modułów `unittest` bieg jest szeroki, domyślnie 25).
   - `hooks: {guard_delete: off, guard_tests: off}` wyłącza obie te części, a wpis i skrypty zostają, bo niosą zakaz stopek narzędzi AI. Śledzony `settings.local.json` nigdy nie jest zapisywany. Hak nie widzi narzędzia PowerShell. `skillsync link --dry-run` pokazuje zmianę, `doctor` stan.

## Przejście 1.6.1 → 1.7.0

Wydanie 1.7.0 jest MINOR (sekcja **Breaking** w `CHANGELOG.md` to „None.”): nowe elementy strony statusu i poprawki odczytu plików projektu.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.7.0`, potem `skillsync layers update core --ref v1.7.0`.
2. Uruchom ponownie działający `skillsync dashboard serve` (panel niewypchniętych commitów i przycisk powrotu na górę przychodzą z nowym kodem i zasobami strony). `skillsync prototype migrate` nie jest potrzebne: silnik prototypów się nie zmienił.
3. Wiersz `Prototyp:` (albo `Bramka prototypu:`) w nagłówku planu, który zawiera niewidoczny znak końca linii wklejony z dokumentu lub strony WWW (np. U+2028), jest teraz odrzucany z komunikatem podającym znak i jego pozycję; wcześniej był po cichu ucinany. Usuń znak i uruchom polecenie ponownie.
4. Windows: serwer uruchomiony przez program nadrzędny w osobnej grupie procesów zatrzymuj sygnałem CTRL_BREAK (kończy się kodem 0 i usuwa pliki stanu, jak Ctrl+C w konsoli). `Stop-Process` i `taskkill /F` nadal kończą proces natychmiast i zostawiają plik stanu; tabela jest w [dashboard-contract](dashboard-contract.md).
5. Konsument JSON-a strony statusu: `delivery` ma nowe pola `commits` i `commitsMore` (zawsze obecne; bez gita `null` i `0`).

## Przejście 1.4.1 → 1.5.0

Wydanie 1.5.0 jest MINOR z wpisem **Breaking** w `CHANGELOG.md` (decyzja właściciela): zgody ze strony statusu wymagają tokenu, więc własne skrypty i klienty, które wołają `POST /api/approve`, przestają działać bez dostosowania. Strona statusu i wtyczka edytora tego samego wydania nie wymagają zmian.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.5.0`, potem `skillsync layers update core --ref v1.5.0`.
2. **Token zgód (Breaking).** `POST /api/approve` bez nagłówka `X-Approve-Token` dostaje `403` (`code: "approve_token"`). Własny skrypt albo klient bierze adres poleceniem `skillsync dashboard url <projekt>` (token to fragment `#zgoda=<token>`) i wysyła jego wartość w nagłówku. Otwarta karta ze starą stroną dostaje to samo `403`, dopóki nie zostanie otwarta ponownie z adresu z fragmentem (przeglądarka zapamiętuje token w `localStorage`).
3. Uruchom ponownie działający `skillsync dashboard serve` i `prototype serve`: działający proces ma w pamięci stary kod (bez tokenu zgód i bez ACL plików).
4. Windows: pliki, które POSIX trzyma w trybie 0600 (plik użytkowników, stan i tokeny logowania jednorazowego, `audit.jsonl`, config maszyny z `prototype.default_password`, plik stanu tokenu zgód), są prywatne przez ACL; plik zapisany przez starszą wersję jest naprawiany przy pierwszej komendzie, która go czyta lub zapisuje (jedna linia `ACL Windows: ...`). Ostrzeżenie „kontrola uprawnień 0600 pominięta na Windows” znika: konsument filtrujący jego tekst musi to uwzględnić.
5. Windows: pola ścieżek we wszystkich raportach CLI mają `/` (zob. lista w `CHANGELOG.md`); konsument porównujący je z natywną ścieżką (`C:\...`) musi porównywać z `/` albo normalizować (`Path(wartość)`). Linux i macOS bez zmian.
6. Nowy klucz `targets.<id>.committer_allow` w configu maszyny (strażnik wycieków; domyślnie pusta lista, bez klucza zachowanie bez zmian).
7. Maskowanie sekretów ze zmiennych środowiska działa od 8 znaków: sekret krótszy niż 8 znaków nie jest już maskowany w wyjściu (maskowanie wzorcowe bez zmian).

## Przejście 1.2.x → 1.3.0

Wydanie 1.3.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Dodaje marker „agent pisze ekrany” (`skillsync prototype writing`, blokada akceptacji i przywracania), czas pracy i czekania na stronie statusu, `skillsync leakcheck --text` i profil przeglądu `docs`; repozytorium rdzenia przestaje śledzić `docs/specs/**` i `docs/plans/**`. Warstwa `core` ma `core_min: 1.3.0`:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.3.0`, potem `skillsync layers update core --ref v1.3.0`.
2. Istniejące prototypy dostają nowy silnik (baner zapisu) przez `skillsync prototype migrate`; do tego czasu starszy silnik pokazuje ogólny błąd przy zablokowanej akceptacji.
3. Uruchom ponownie działający `prototype serve`.

## Przejście 1.2.0 → 1.2.1

Wydanie 1.2.1 jest PATCH (sekcja **Breaking** w `CHANGELOG.md` opisuje zmianę lokalizacji pliku pytań). Pytania do eksperta są osobne dla każdego przebiegu:
`<root>/<slug>/questions.json` zamiast jednego `<root>/questions.json`.

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.2.1`, potem `skillsync layers update core --ref v1.2.1`.
2. Projekt z pytaniami i starym plikiem `<root>/questions.json`: `skillsync prototype question migrate . --dry-run` (plan), potem `skillsync prototype question migrate .`.
   BLOCKED `unassigned` oznacza pytania bez jednoznacznego przebiegu; rozstrzygnij je przez `--slug-for SPEC=SLUG`. Do czasu migracji serwer pokazuje stare pytania tylko do odczytu.
3. Uruchom ponownie działający `prototype serve`. Rejestracja sterownika `skillsync-questions` nie zmienia się, ale `merge` z ręczną formą projektową wymaga `--slug`.

## Przejście 1.1.x → 1.2.0

Wydanie 1.2.0 jest MINOR (zgodne wstecz): sekcja **Breaking** w `CHANGELOG.md` to „None.”. Dodaje pytania do eksperta domenowego: rolę `expert`,
konta z wieloma rolami, plik pytań przebiegu (`questions.json`), stronę `/_experts` i komendy `skillsync prototype question …` oraz `prototype user roles`.
Kolejność jest obowiązkowa, bo warstwa `core` ma `core_min: 1.2.0`. Warstwa `core` jest wydawana razem z rdzeniem, więc podniesienie jej `core_min` do wersji tego samego wydania nie jest zmianą MAJOR warstwy (reguły w tabeli i w sekcji „Co oznacza `core_min`” wyżej dotyczą rozjazdu między osobno wydawanymi warstwami):

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.2.0`, potem `skillsync layers update core --ref v1.2.0`.
2. Pozostałe warstwy: `skillsync layers update <id>` albo `skillsync pull`, potem `skillsync link`; uruchom ponownie działające `prototype serve`.

Plik kont i starszy rdzeń:

- konta ze starym polem `role` czytasz bez migracji, a odczyt niczego nie zapisuje;
- zapis jest zgodny wstecz: pole `role` zawiera najwyższą rolę ze zbioru rozumianego przez starszy rdzeń (`commenter`, `acceptor`, `developer`; kolejność `developer` > `acceptor` > `commenter`; konto tylko z `expert`
  ma `commenter`), a pole `roles` dochodzi obok tylko dla kont z rolą `expert` albo z kilkoma rolami;
- starszy rdzeń (1.1.x) czyta ten sam plik: ignoruje `roles`, więc widzi eksperta jako komentującego, bez eskalacji uprawnień;
  komendy `question` ma tylko rdzeń 1.2.0, dlatego `core_min` warstwy `core` blokuje jej linkowanie na starszym rdzeniu;
- `comments.json` i silnik prototypu nie zmieniają się; nowa funkcja `questions` w `GET /_api/v1/session` (`features`) to dodatek, a nie
  zmiana `server.version`.

Ręcznie, jeśli projekt używa pytań do eksperta: zarejestruj sterownik scalania `skillsync-questions`
([prototypes](prototypes.md#pytania-do-eksperta-w-gicie)) i uruchamiaj `serve` z `--login-to-view`.

## Przejście 1.0.x → 1.1.0

Wydanie 1.1.0 jest MINOR (zgodne wstecz): nowe komendy i klucze, bez zmian niezgodnych (sekcja **Breaking** w `CHANGELOG.md` to „None.”).
Gdy któraś warstwa ma `core_min: 1.1.0`, kolejność jest obowiązkowa:

1. Rdzeń: `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.1.0`, potem `skillsync layers update core --ref v1.1.0`.
   Warstwa z `core_min` wyższym niż wersja generatora jest odrzucana (`layers check` i `link` zwracają BLOCKED).
2. Pozostałe warstwy: `skillsync layers update <id>` albo `skillsync pull`, potem `skillsync link`.

Co się zmienia dla użytkownika:

- nowe komendy: `skillsync dashboard show|serve|metrics`, `skillsync approvals record`, `skillsync prototype login-link`;
- nowy klucz manifestu `workflow.ceremony` (`full` | `demo`), bramka prototypu per faza (`[UI]` w tabeli kroków planu), klasy pytań `Q` w specu;
- alias `status-board` → `dashboard`: wpis `status-board` w `skills:` rozwija się do `dashboard` tylko wtedy, gdy żadna warstwa nie dostarcza skilla
  `status-board`. Warstwa, która nadal go ma, zachowuje własny skill; po jego usunięciu z warstwy alias zaczyna działać, a skrypty projektów
  odwołujące się do ścieżki `status-board/scripts/status_board.py` trzeba zmienić na `skillsync dashboard serve <projekt>`.

## Przejście maszyny na warstwy (wersja 1.0.0)

Generator 1.0.0 nie czyta dwóch wcześniejszych postaci configu i dla obu zwraca BLOCKED z instrukcją
`skillsync layers migrate`:

- config z kluczem `library:`;
- config z mostem: tymczasowa warstwa `core` oznaczona `synthetic` (tylko do odczytu).

Procedura jest dla obu taka sama; przy moście `layers migrate` zastępuje tymczasowy wpis prawdziwą warstwą `core`.
Config jest per maszyna: wszystkie kroki wykonujesz osobno na każdej maszynie.

### Warstwa `core`

Repo rdzenia ma katalog `library/` z warstwą `core` i jej `layer.yaml` (`version`, `core_min`). Jeśli warstwa `core` leży
w innym katalogu (np. własna kopia biblioteki), podaj jego ścieżkę w `--layer-source core=…`.

Katalog bez `layer.yaml` wymaga w kroku 3 `--kind core=core`. Przed kontrolą w kroku 7 dodaj w nim `layer.yaml` z polami `schema: 1`,
`id: core`, `kind: core`, `version` i `core_min`. Bez tego pliku `layers check` i `link` zwracają BLOCKED (2,
`layer-yaml-missing`), a `doctor` zgłasza błąd (FAIL, 1); `layers migrate` tylko ostrzega.

### Kroki

1. **Kopia.** Zrób kopię configu (`~/.config/skillsync/config.yaml` albo plik wskazany przez `SKILLSYNC_CONFIG`) oraz kopie
   manifestów projektów i definicji usługi watch.
2. **Przepięcie generatora** na wersję 1.0.0 (dowiązanie albo pakiet). Nie restartuj jeszcze usługi watch i nie wykonuj `link`:
   nowy generator czyta config dopiero po migracji (kroki 3–4).

   Linux z checkoutu: usuń dowiązanie `~/.local/bin/skillsync`, potem `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.0.0` i sprawdź `skillsync --version`. `skillsync-ctl` zostaje w checkoucie (nie wchodzi do pakietu).
3. **Dry-run.** Sprawdź plan migracji:

   ```bash
   skillsync layers migrate --layer-source core=<lokalny klon repo skillsync>/library
   ```

   Dla warstw `org` i `personal`, które mają lokalne checkouty, dodaj `--layer-source <id>=<ścieżka>`. Dla każdej warstwy bez
   `layer.yaml` (także `core`) dodaj `--kind <id>=<rodzaj>` (`core`, `org` albo `personal`). Dla warstw `org` dodaj
   `--match <id>=<glob>`.
4. **Zapis.** Powtórz polecenie z `--apply`. Generator zapisuje kopię zapasową configu przed zmianą.
5. **Manifesty (opcjonalnie).** Usuń z manifestów projektów ignorowane ustawienie źródła biblioteki:

   ```bash
   skillsync layers migrate --manifests          # podgląd zmian
   skillsync layers migrate --manifests --apply  # zapis
   ```

   Kopie zmienionych manifestów trafiają do `~/.local/share/skillsync/backups/manifests/<czas>/`. Bez tego kroku manifesty
   zostają bez zmian (ustawienie jest ignorowane z ostrzeżeniem).
6. **Hooki warstw zapisywalnych.** Dla każdej warstwy `org` i `personal` z lokalnym checkoutem uruchom
   `skillsync leakguard install-hook <ścieżka warstwy>`, aby hooki `pre-commit` i `pre-push` działały bez zmian w repo.
7. **Kontrola.** `skillsync layers check` (PASS wymaga `layer.yaml` warstwy `core`, patrz „Warstwa `core`”, i hooków
   z kroku 6: bez nich FAIL `hook-missing`).
8. **Restart usługi watch** i `skillsync link` wszystkich projektów z `discover_paths`.
   Na Linuksie restart: `skillsync-ctl restart` w checkoucie repo.

   Na macOS i Windows usługi watch nie ma: zatrzymaj ręcznie uruchomione `skillsync watch` i uruchom je ponownie.
9. **Doctor.** `skillsync doctor <projekt>` w każdym projekcie: oczekiwany PASS.

### Zakończenie migracji

`legacy_library` to ścieżka starej biblioteki zapisana przez `layers migrate`. Służy do rozpoznawania dowiązań
utworzonych przez starą konfigurację.

```bash
skillsync layers migrate --finish          # podgląd
skillsync layers migrate --finish --apply  # usuwa legacy_library z configu tej maszyny
```

Wykonaj `--finish --apply`, gdy `skillsync layers check --isolation` daje PASS (`--finish` wymaga też
`isolation.allowed_layers` w configu).

Często `legacy_library` to ten sam katalog co warstwa `core`: wtedy go nie usuwaj, `--finish` przechodzi. Gdy warstwa `core`
wskazuje inny katalog, `--finish` zwraca BLOCKED (2), dopóki stary klon spod `legacy_library` istnieje: przenieś z niego
lokalne zmiany i usuń go.

### Cofnięcie

1. Przywróć poprzednią wersję generatora (odwrotność kroku 2): generator 1.0.0 zwraca BLOCKED dla przywróconego configu
   z `library:`.
2. Przywróć kopię configu i kopie manifestów oraz dowiązania i definicję usługi watch.

### Znane ograniczenie

Przed `layers migrate` config zawiera najwyżej jedną warstwę `personal`.

Przy kilku warstwach `personal` `layers migrate` kończy się BLOCKED (`More than one personal layer: <id>, <id>`) i niczego
nie zapisuje; w regułach (`layer_rules`) każda reguła może mieć najwyżej jedną warstwę `personal`.

### Reguła domyślna

Reguła `**` dla projektów bez innej reguły tworzy `layers migrate` albo ręcznie `skillsync layers use <personal> --match '**'`.
`layers add` i `layers init` tylko ostrzegają o jej braku.

### Warstwy zdalne

`--layer-source` przyjmuje wyłącznie ścieżki lokalne. Warstwę zdalną dodaj po migracji:
`skillsync layers add <id> --kind <rodzaj> --source <URL>`.

## Przejście 1.8.0 → 1.9.0

Wydanie 1.9.0 wprowadza globalny zakaz stopek, podpisów i trailerów narzędzi AI w tekstach wychodzących (sekcja **Breaking** w `CHANGELOG.md`).

1. **Rdzeń.** `uv tool install --force git+https://github.com/BATUSystems/skillsync@v1.9.0`, potem `skillsync layers update core --ref v1.9.0`, potem `skillsync link` w projektach (instrukcje, hak i ustawienie hosta).
2. **Przed aktualizacją sprawdź warstwy.** Warstwa (własna, organizacji, personal), której instrukcje, skille, role lub `workflow/` zawierają tekst takiej atrybucji, dostanie `FAIL` w `skillsync layers check`, a projekt `FAIL` w `doctor`. Przegląd: `git grep -n -i -E "generated (with|by)|co-authored-by: *(claude|codex|copilot|cursor|gemini)"` w warstwach i w plikach `AGENTS.md`/`CLAUDE.md`/`.github/copilot-instructions.md`; usuń takie linie u źródła.
3. **Trailer narzędzia AI w commitach przestaje być dozwolony** (dotąd leakguard go przepuszczał). `pre-push` i CI odrzucają nowe commity z takim trailerem lub stopką; commity już istniejące zostają bez zmian i `leakcheck --history` ich nie zgłasza. Opisy PR sprawdzisz `skillsync leakcheck --stdin`.
4. **Hak poleceń w każdym projekcie z Claude Code.** `link` instaluje `guard-commands.py` i `attribution-patterns.json` także tam, gdzie wcześniej hooka nie było (projekty bez `feature` i `dispatch`: tylko zakaz, część usuwania wyłączona). Zakaz odmawia poleceń zapisujących tekst (`git commit`, `gh pr create`, `az repos pr create` itd.) z taką stopką lub trailerem, nie ma przełącznika w manifeście ani wyjątku. `doctor`: brak haka lub danych to FAIL.
5. **Ustawienie hosta.** `link` zapisuje w `.claude/settings.local.json` klucz `attribution` z pustymi `commit` i `pr` (pozostałe klucze zostają; wartość niepusta jest zastępowana z ostrzeżeniem). Dla wszystkich projektów użytkownika: `skillsync attribution --user` (jawnie). Twarda gwarancja na maszynie: wpis z `skillsync attribution --print-managed` w pliku ustawień zarządzanych hosta; skillsync go nie zapisuje.
6. **Budżet instrukcji bazowej** `library/workflow/defaults.yaml: context.base_max_bytes` wzrósł z 8192 do 8704 B (nowa reguła w `_base.md`); projekt z własnym, niższym limitem w manifeście zobaczy ostrzeżenie `doctor`, jeśli baza go przekracza.

## Windows z instalacji uv

Aktualizacja maszyny z Windows, na której rdzeń stoi jako narzędzie uv (maszyna z instalacją sprzed kilku wydań). Polecenia z katalogu projektu, nie z katalogu domowego.

1. `uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z` (docelowy tag).
2. `skillsync layers update core --ref vX.Y.Z` (zapisuje kopię configu `.bak-<czas>`).
3. W katalogu projektu `skillsync layers check`. Dla każdej warstwy zapisywalnej: `skillsync leakguard install-hook <ścieżka warstwy>`; bez hooków `layers check` kończy się FAIL `hook-missing`.
4. `skillsync layers update <id warstwy org>`.
5. `skillsync link <projekt> --dry-run`, potem `skillsync link <projekt>`, na końcu `skillsync doctor <projekt>`.

Co zobaczysz po drodze:

- **`layers list` i `layers check` zależą od katalogu.** Pokazują warstwy według reguł dla bieżącego katalogu: poza projektem tylko `core`, w katalogu projektu także warstwę org. Sprawdzaj z katalogu projektu.
- **Projekt dowiązany jeszcze do starej biblioteki.** `doctor` przed `link` zwraca BLOCKED „Path escapes project: .github/skills/…”. `link` przepina dowiązania na warstwy (część skilli wskazuje wtedy katalog cache `composed`), usuwa przestarzały plik roli i odświeża zadanie startowe edytora.
- **Zadanie startowe edytora** (`.vscode/tasks.json`, `runOn: folderOpen`): stara definicja wołała `python <klon generatora>/skillsync link …` i po migracji configu na warstwy kończyła się `KeyError: 'library'`. `link` z nowej wersji przepisuje ją na plik wykonywalny z instalacji uv; edytor trzyma starą definicję do przeładowania okna.
- **Dwie komendy `skillsync` na PATH.** Obok pliku z instalacji uv może leżeć stary skrypt-opakowanie klonu; obowiązuje pierwsza. Sprawdź `Get-Command skillsync -All` (PowerShell) albo `where skillsync`.
- **Manifest.** `doctor` ostrzega o ignorowanym `sync: {library: …}` i o przemianowanej roli `Explore` → `explore`. `skillsync layers migrate --manifests` obejmuje tylko projekty z `discover_paths`; w projekcie spoza listy usuń klucz ręcznie.
- **Rekord domenowy z dwukropkiem w nagłówku.** `doctor` zgłasza błąd YAML przy niecytowanej wartości zawierającej „: ”; ujmij ją w apostrofy.
- **Warstwa prywatna** wymaga dostępu do jej repozytorium z tej maszyny; bez niego generowane instrukcje nie zawierają części pochodzących z tej warstwy.

## Ograniczenia na poszczególnych systemach

Zakres wsparcia systemów opisuje tabela w [README](../README.pl.md#wymagania).

Aktualizacja maszyny Windows z instalacji uv: sekcja „Windows z instalacji uv” wyżej (natywny Windows jest poza drogą instalatora (`install.sh`): WSL2). Procedura przejścia maszyny macOS na warstwy oraz ręczne testy na tym systemie powstaną w późniejszym wydaniu.

## Co dalej

Zmiany między wersjami opisuje wyłącznie `CHANGELOG.md`.
