# Instalacja

Skillsync to narzędzie wiersza poleceń w Pythonie, instalowane przez [uv](https://docs.astral.sh/uv/) z tagu wydania.
Po instalacji dodajesz warstwy (`skillsync layers add`) i reguły ich użycia (`skillsync layers use`).

## Wymagania

| | Linux | macOS | Windows |
|---|---|---|---|
| git | z menedżera pakietów | Xcode Command Line Tools lub Homebrew | Git for Windows |
| Python 3.11+ | systemowy albo z `uv` | systemowy albo z `uv` | systemowy albo z `uv` |
| usługa watch | systemd (user), `skillsync-ctl` z checkoutu | brak (`skillsync watch` ręcznie) | brak (`skillsync watch` ręcznie) |

Wersje 1.0.x wspierają Linux, WSL2 i macOS. Instalator (`install.sh`) i `skillsync setup` działają na tych systemach; natywny Windows
jest poza drogą instalatora, zalecany jest WSL2 (Ubuntu), gdzie działa ta sama ścieżka co na Linuksie.

Pakiet jest czystym Pythonem, więc na macOS działa tak samo na Apple Silicon i Intel. Zakres wsparcia systemów: tabela
w [README](../README.pl.md#wymagania).

Rozwijane i sprawdzane z uv 0.13; minimalna wersja nie została zmierzona. CI używa bieżącego uv.

## Szybka instalacja (Linux, WSL2, macOS)

Instalator `install.sh` instaluje narzędzie i uruchamia kreator `skillsync setup`, który dodaje warstwę core,
opcjonalnie warstwę organizacji, regułę doboru warstw i kończy `skillsync layers check`:

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://raw.githubusercontent.com/BATUSystems/skillsync/<tag>/install.sh | sh -s -- [opcje setup]
```

Przykład z organizacją (`<org-id>` i `<org-repo-url>` zastąp wartościami swojej organizacji):

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://raw.githubusercontent.com/BATUSystems/skillsync/<tag>/install.sh | sh -s -- --org <org-id> --org-source <org-repo-url>
```

Co robi instalator, w tej kolejności:

| Krok | Działanie | Błąd |
|---|---|---|
| 1 | sprawdza system: Linux, WSL2, macOS | natywny Windows: komunikat o WSL2, kod 2 |
| 2 | sprawdza `git` i `curl` (ich nie instaluje) | brak narzędzia: komunikat z poleceniem instalacji, kod 2 |
| 3 | instaluje [uv](https://docs.astral.sh/uv/) oficjalnym instalatorem, jeśli `uv` nie ma w `PATH` | kod 3 |
| 4 | `uv tool install --force git+https://github.com/BATUSystems/skillsync@<tag>` | kod 3 |
| 5 | sprawdza, że `skillsync --version` zwraca tag | kod 3 |
| 6 | uruchamia `skillsync setup` z opcjami podanymi po `--` | wynik `setup`, patrz [cli](cli.md) |

Kody wyjścia instalatora: `0` (ok), `2` (błąd środowiska: system, brak `git` lub `curl`, obcy `skillsync` na `PATH`),
`3` (błąd instalacji `uv` lub `skillsync`). Po udanej instalacji instalator kończy się kodem `skillsync setup`
(`0` PASS, `1` FAIL, `2` BLOCKED). Kod `2` ma więc dwa źródła; komunikat (`install.sh: error: …` albo `setup: BLOCKED` z listą
kroków) mówi, które. Przy `BLOCKED` kreatora popraw przyczynę z komunikatu i uruchom `skillsync setup` ponownie (bez
ponownej instalacji); `skillsync setup --dry-run` pokazuje plan bez zapisu.

Bezpieczeństwo:

- Przed uruchomieniem przeczytaj skrypt (pobierz `install.sh` do pliku i otwórz go; nie uruchamiaj od razu `| sh`).
  Skrypt jest dostępny z niezmiennego tagu wydania; tag `v*` jest chroniony przez ruleset repo.
- Tag jest wbity w skrypt: instalator instaluje dokładnie tę wersję, którą opisuje dokumentacja tego tagu.
- Instalator nie używa `sudo`.
- Gdy na `PATH` jest `skillsync` niezainstalowany przez `uv` (checkout, symlink, pip), instalator odmawia nadpisania
  i kończy się kodem 2. Usuń ten `skillsync` albo uruchom instalator z `SKILLSYNC_INSTALL_FORCE=1`.
- Instalator nie pobiera żadnych skryptów poza oficjalnym instalatorem `uv` (`https://astral.sh/uv/install.sh`, tylko gdy `uv` brak).

Zmienne środowiskowe (głównie testy i CI):

| Zmienna | Skutek |
|---|---|
| `SKILLSYNC_TAG` | instaluje inny tag niż wbity w skrypt |
| `SKILLSYNC_INSTALL_SPEC` | pełna specyfikacja dla `uv tool install` (np. ścieżka do koła); pomija sprawdzenie wersji |
| `SKILLSYNC_NO_SETUP=1` | instaluje tylko narzędzie, bez `skillsync setup` |
| `SKILLSYNC_INSTALL_FORCE=1` | zgadza się na nadpisanie obcego `skillsync` na `PATH` |

Gdy odpowiedzi kreatora mają być czytane z terminala, a skrypt jest w potoku (`curl … | sh`), instalator czyta je z `/dev/tty`,
jeśli jest dostępny (ta gałąź nie ma testu automatycznego: sprawdzana ręcznie na koncie próbnym). Bez terminala `setup` działa nieinteraktywnie.

Opcje `skillsync setup` (pełna lista: `skillsync setup --help`):

| Opcja | Znaczenie |
|---|---|
| `--yes` | bez pytań; używa flag i wartości domyślnych |
| `--dry-run` | pokazuje plan i sprawdza dostęp (`git ls-remote`), nic nie zapisuje |
| `--json` | wynik w JSON (dane logowania w adresach są zredagowane) |
| `--core-source URL` | repo warstwy core (domyślnie `https://github.com/BATUSystems/skillsync`) |
| `--core-ref REF` | tag, gałąź lub commit core (domyślnie `v<zainstalowana wersja>`) |
| `--org ID` | id warstwy organizacji do dodania (bez opcji organizacji nie dodajesz) |
| `--org-source URL` | adres repo warstwy organizacji (wymagany przy nowej warstwie) |
| `--org-ref REF` | tag, gałąź lub commit warstwy organizacji |
| `--match GLOB` | glob projektów dla warstwy organizacji (powtarzalna; domyślnie `**`) |

Opcje `setup` przekazane po `sh -s --` trafiają do `skillsync setup` bez zmian.

Przykład z organizacją i jednym katalogiem projektów:

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://raw.githubusercontent.com/BATUSystems/skillsync/<tag>/install.sh | sh -s -- \
  --org <org-id> --org-source <org-repo-url> --match '~/Repos/<org-id>/**'
```

Wszystkie kroki `setup`, statusy i kody: [cli](cli.md#konfiguracja-maszyny-setup). Dostęp do repo organizacji musi działać
bez pytań: `git ls-remote <org-repo-url>` kończy się sukcesem bez hasła i bez potwierdzenia klucza.

Aktualizacja i odinstalowanie: [poniżej](#aktualizacja).

## Instalacja

```bash
uv tool install git+https://github.com/BATUSystems/skillsync@vX.Y.Z
```

Gdy powłoka nie widzi polecenia `skillsync`, uruchom `uv tool update-shell` i otwórz nową powłokę.

## Linux

Usługa watch (`skillsync-watch.service`, systemd user) działa z checkoutu repo przez `skillsync-ctl`
(`start`, `stop`, `restart`, `enable`, `disable`, `status`, `logs`).

## macOS

Ścieżki na macOS: config `~/.config/skillsync/config.yaml` (albo plik ze zmiennej `SKILLSYNC_CONFIG`), checkouty warstw
`~/Library/Application Support/skillsync/layers/<id>`, cache `~/Library/Caches/skillsync`.

## Windows

Symlinki na Windows wymagają trybu dewelopera (Ustawienia → Dla deweloperów) albo uprawnień administratora. Bez nich
`skillsync link` wypisuje `[warn] Symlinks unavailable (…); falling back to copy (Windows: enable Developer Mode)` i tworzy
kopie. Tryb kopii można też wybrać wprost: `sync.method: copy` w manifeście albo `SKILLSYNC_NO_SYMLINK` z wartością
`1`, `true`, `yes` lub `on` (`0`, `false`, `no`, `off` albo pusta wartość go wyłączają; inna wartość daje ostrzeżenie i włącza
tryb kopii). Repo warstw nie zawierają symlinków, więc `git config core.symlinks` nie wpływa na checkouty warstw. Ścieżki: config
`%USERPROFILE%\.config\skillsync\config.yaml`, warstwy `%LOCALAPPDATA%\skillsync\layers\<id>`, cache
`%LOCALAPPDATA%\skillsync\cache`.

## Warstwy

```bash
skillsync layers add core --kind core --source https://github.com/BATUSystems/skillsync --subdir library --ref vX.Y.Z
skillsync layers add <id> --kind personal|org --source <URL albo ścieżka> [--ref <tag>]
skillsync layers use <id> --match "<katalog projektów>/**"
skillsync layers check
```

Warstwy i reguły trafiają do configu maszyny (ścieżki: [configuration](configuration.md)).

## Prywatne warstwy

Warstwy prywatne klonuje `git clone` bez pytań w terminalu (`GIT_TERMINAL_PROMPT=0`). Przed `skillsync layers add`
skonfiguruj dostęp tak, żeby `git clone <URL warstwy>` działał bez pytania o hasło: SSH (klucz w agencie) albo HTTPS
z menedżerem poświadczeń (np. Git Credential Manager, `gh auth setup-git`).

## Sprawdzenie instalacji

```bash
skillsync --version
skillsync layers check
skillsync doctor <projekt>
```

## Aktualizacja

`skillsync pull` aktualizuje checkouty warstw (`layers update`) i linkuje projekty (`link`). Nie aktualizuje narzędzia
`skillsync` ani warstwy core przypiętej do tagu.

Aktualizację pakietu wykonaj ponownym zainstalowaniem z nowego tagu: `uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z`. `uv tool upgrade skillsync` zostaje przy przypiętym tagu.

Po instalacji przez `install.sh` uruchom ponownie instalator z nowego tagu (`<tag>` w adresie) albo wykonaj poniższe:

```bash
uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z
skillsync layers update core --ref vX.Y.Z
skillsync pull
```

Przed przejściem na nową wersję MAJOR przeczytaj [upgrading.md](upgrading.md).

## Odinstalowanie

```bash
uv tool uninstall skillsync
```

Na Linuksie najpierw zatrzymaj usługę watch: `skillsync-ctl stop` w checkoucie repo.

Po odinstalowaniu zostają: config maszyny, checkouty warstw i kopie zapasowe w katalogu danych oraz cache (ścieżki:
[configuration](configuration.md)). Usuń je ręcznie, jeśli nie są potrzebne.

## Migracja z instalacji ręcznej

Dotyczy maszyn, na których Skillsync działał z klonu repo i jednej biblioteki.

`skillsync layers migrate --layer-source core=<klon>/library` pokazuje plan (domyślnie dry-run, nic nie zapisuje);
z `--apply` zapisuje config z warstwami i kopię zapasową. Stara ścieżka biblioteki zostaje w configu jako `legacy_library`
do `layers migrate --finish --apply`. Powrót: kopia configu i poprzednia wersja generatora. Pełna procedura:
[upgrading](upgrading.md).

## Rozwiązywanie problemów

| Objaw | Przyczyna | Rozwiązanie |
|---|---|---|
| `skillsync: command not found` | katalog narzędzi `uv` poza `PATH` | `uv tool update-shell`, nowa powłoka |
| `layers check` → BLOCKED | brak `core`, niespełnione `core_min` albo `requires` | kod w wyniku: `core-missing` → `skillsync layers add core …`; `core-min-unmet` → nowszy generator albo starsza warstwa; `requires-unmet` → dodaj wymaganą warstwę; `layer-yaml-missing` → zatwierdź `layer.yaml` w repo warstwy i `skillsync layers update <id>` |
| pliki w projekcie są kopiami zamiast symlinków (Windows) | brak uprawnień do symlinków | tryb dewelopera Windows i ponowny `skillsync link`; albo świadomie tryb kopii (`sync.method: copy`) |
| prywatna warstwa się nie klonuje | brak uwierzytelnienia | dostęp SSH albo HTTPS z menedżerem poświadczeń; `git clone <URL>` musi działać bez pytań |
| `layers check` → `symlink-in-layer`, `leakcheck` → BLOCKED (Windows, macOS) | checkout warstwy leży w folderze synchronizowanym „na żądanie” (OneDrive „pliki na żądanie”, iCloud Drive z optymalizacją miejsca); placeholdery chmury i pliki deduplikacji są reparse pointami | przenieś checkout warstwy poza folder synchronizowany, np. do domyślnego katalogu warstw; zobacz sekcję „Bez symlinków” w [layers-authoring.md](layers-authoring.md) |
| usługa nie linkuje po zmianie (Linux) | usługa działa na kodzie z chwili startu | `skillsync-ctl status`, potem `skillsync-ctl restart` |
| `link` → `[blocked] … No layer_rules entry matches …` | brak reguły dla katalogu projektu | `skillsync layers use <personal|org> --match <glob>` (albo reguła `**`) |
| `layers check` → FAIL `hook-missing` | warstwa zapisywalna bez hooków strażnika | `skillsync leakguard install-hook <katalog warstwy>` |
| `layers check` → FAIL `hook-command-missing` | hook strażnika warstwy wywołuje plik, którego już nie ma (np. przeniesiony checkout generatora) | `skillsync leakguard install-hook <katalog warstwy>` (albo `skillsync layers add` dla tej warstwy) |
| `doctor` → FAIL `Missing installed skill` | projekt jeszcze nie podlinkowany | `skillsync link <projekt>` |
| `install.sh: error: native Windows is not supported` (kod 2) | uruchomienie w natywnym Windows (PowerShell, cmd, Git Bash) | zainstaluj WSL2 (`wsl --install -d Ubuntu`), otwórz powłokę Ubuntu i uruchom instalator tam |
| `install.sh: error: missing: git curl` (kod 2) | brak `git` albo `curl`; instalator ich nie instaluje | zainstaluj pakiety (np. `sudo apt-get install -y git curl`, `brew install git curl`) i uruchom instalator ponownie |
| `'skillsync' on PATH … was not installed by uv` (kod 2) | obcy `skillsync` (checkout, symlink, pip) | usuń go albo uruchom z `SKILLSYNC_INSTALL_FORCE=1` |
| `could not download the uv installer` (kod 3) | brak sieci albo proxy do `astral.sh` | zainstaluj `uv` ręcznie ([docs.astral.sh/uv](https://docs.astral.sh/uv/)) i uruchom instalator ponownie |
| `'uv tool install' failed` (kod 3) | tag nie istnieje albo brak dostępu do github.com | sprawdź tagi w repo na GitHubie i połączenie; `SKILLSYNC_TAG` pokazuje, jaki tag był użyty |
| `skillsync was installed but is not on PATH` (kod 3) | katalog narzędzi `uv` poza `PATH` | `uv tool update-shell`, nowa powłoka, potem `skillsync setup` |
| `setup: BLOCKED` → `no access to …: … Permission denied (publickey)` | klucz SSH nie jest w agencie albo nie jest dodany u dostawcy | `ssh-add -l`; dodaj klucz do konta u dostawcy; `ssh -T <git-host>`; potem `skillsync setup` |
| `setup: BLOCKED` → `Host key verification failed` | nieznany klucz hosta | połącz się raz interaktywnie (`ssh -T <git-host>`, potwierdź), potem `skillsync setup` |
| `setup: BLOCKED` → `could not resolve` / `timed out` | brak sieci albo VPN | sprawdź sieć lub VPN; `git ls-remote <url>` musi działać bez pytań |
| `setup: BLOCKED` → `Unknown ref <ref>` i „The ref … does not exist” w kroku `core` | wersja bez zdalnego tagu (build deweloperski) | podaj istniejący tag, gałąź albo commit: `skillsync setup --core-ref <ref>`; `main` nie jest używany automatycznie |
| `setup:` ostrzeżenie `git user.email is not set` | adres autora nie jest ustawiony przed dodaniem warstw | `git config --global user.email "<adres>"`, potem `skillsync layers check` |
| `setup: BLOCKED` → `… is not empty` (organizacja) | przerwane klonowanie zostawiło katalog warstwy | usuń wskazany katalog (`rm -r <ścieżka>`; nie ma go w configu) i uruchom `setup` ponownie |
| `setup: BLOCKED` → `already configured with a different kind or source` | id warstwy istnieje z innym adresem albo rodzajem | zmień ją poleceniami `skillsync layers …`; `setup` nie nadpisuje warstw |
| `setup: FAIL` → `layers check` (np. `symlink-in-layer`) | `layers check` zgłasza problem w warstwie | kod w wypisie; napraw warstwę według [layers-authoring](layers-authoring.md) i uruchom `skillsync layers check` |
| `setup` z `--yes` → `the config already has layer_rules: pass --match` | reguły już są, a `--yes` nie wie, który projekt ma używać warstwy | podaj `--match <glob>` |
