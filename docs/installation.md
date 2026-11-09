# Instalacja

Skillsync to narzędzie wiersza poleceń w Pythonie, instalowane przez [uv](https://docs.astral.sh/uv/) z tagu wydania.
Po instalacji dodajesz warstwy (`skillsync layers add`) i reguły ich użycia (`skillsync layers use`).

## Wymagania

| | Linux | macOS | Windows |
|---|---|---|---|
| git | z menedżera pakietów | Xcode Command Line Tools lub Homebrew | Git for Windows |
| Python 3.11+ | systemowy albo z `uv` | systemowy albo z `uv` | systemowy albo z `uv` |
| usługa watch | systemd (user), `skillsync-ctl` z checkoutu | brak (`skillsync watch` ręcznie) | brak (`skillsync watch` ręcznie) |

Wersje 1.0.x wspierają Linux, WSL i macOS; natywny Windows jest planowany na 1.0.2.

Pakiet jest czystym Pythonem, więc na macOS działa tak samo na Apple Silicon i Intel. Zakres wsparcia systemów: tabela
w [README](../README.pl.md#wymagania).

Rozwijane i sprawdzane z uv 0.13; minimalna wersja nie została zmierzona. CI używa bieżącego uv.

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

`skillsync pull` aktualizuje checkouty warstw (`layers update`) i linkuje projekty (`link`).

Aktualizację pakietu wykonaj ponownym zainstalowaniem z nowego tagu: `uv tool install --force git+https://github.com/BATUSystems/skillsync@vX.Y.Z`. `uv tool upgrade skillsync` zostaje przy przypiętym tagu.

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
