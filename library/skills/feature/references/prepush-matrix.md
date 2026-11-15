# Macierz przed pushem

Dotyczy projektów Pythonowych z baseline skipów i macierzą CI (rdzeń skillsync); w innych projektach zastosuj odpowiednik: najstarsza wspierana
wersja runtime'u, każdy system z CI, sprawdzenie baseline na logach wszystkich systemów. `PUSH SLOT` występuje w trybie `direct` z dyspozytorem;
w trybie `pr` ten sam dowód jest warunkiem pushu gałęzi PR.

Lekcja z wydania 1.4.1: trzy czerwone obroty CI wykryły to, czego nie widziała stacja (najnowszy interpreter, jeden system, bez
sprawdzenia baseline skipów). Paczka nie idzie do `PUSH SLOT`, dopóki nie ma dowodu z poniższej macierzy; brak kombinacji = `NOT_RUN`
z powodem w raporcie.

| Zmiana dotyczy | Wymagane przed pushem |
|---|---|
| zmiana funkcji produktu | po KAŻDEJ zmianie funkcji uruchom ponownie wszystkie moduły, które ją dotykają (`grep` po nazwie w `tests/`), także strażniki (np. test AST), nie tylko moduł napisany do tej zmiany |
| każdy kod | Linux na **najstarszej wspieranej wersji Pythona** (3.11) i na interpreterze stacji: `py_compile` zmienionych plików i moduły zmienione/nowe (uv: `uv python install 3.11` poza repo, venv z PyYAML) |
| testy podmieniające `os.name`, `sys.platform`, `os.path.isabs` | pełny moduł na 3.11 i na najnowszym (Windows 3.13 zmienia `isabs`; `Path` pod podmienionym `os.name` na 3.11 nie działa) |
| ścieżki, pliki, końce linii, symlinki, blokady | Windows 3.11, 3.12 i 3.13 w środowisku jak runner (pusty profil, PATH bez `skillsync`, TEMP w formie 8.3), punktowo moduły zmienione; pełny bieg po LEASE |
| HTTP, pliki, sygnały | macOS (CI albo maszyna) dla zmienionych modułów |
| każdy nowy lub zmieniony skip | `scripts/check_skips.py` z baseline po zmianie na logach `-v` **wszystkich systemów** (Linux, macOS, Windows; klucz `<system>/<tryb>`): rc=0; kategorie sprawdzaj pod kątem przechwytywania (pierwsza pasująca wygrywa) |
| nowa kategoria lub fragment skipa | samospójność: każdy fragment klasyfikuje się do własnej kategorii |

Zakres: maszyny Windows i macOS są wspólne dla projektów, więc **pełna macierz przed pushem idzie raz, na gałąź wydania** (integracyjną); gałąź robocza dostaje dowód punktowy (zmienione moduły na Windows 3.11, 3.12 i 3.13, macOS dla HTTP, plików i sygnałów), nie pełny bieg.

Zasady biegów: procesy testów zatrzymujesz **wyłącznie po własnym PID** (albo własnej grupie procesów lub scope `systemd-run`), nigdy po wzorcu nazwy (`pkill -f`): na wspólnej stacji zabijesz cudzy bieg. Równoległe biegi na jednej maszynie (np. Windows 3.11, 3.12 i 3.13 naraz) dostają rozłączne plasterki portów testowych serwerów (`SKILLSYNC_TEST_DASHBOARD_PORTS=<od>-<do>`), inaczej moduły z serwerem dają `WinError 10048` (zajęty port), co nie jest porażką produktu.

Skrypty pomocnicze leżą w repo operacyjnym `skillsync-ops` (`tools/win-shard/`): `prepush.sh` (macierz Windows i `check_skips` z jednej komendy),
`runlike.sh`/`runlike.ps1` (Windows, środowisko jak runner), `skips_union.py` i `apply_windows_baseline.py` (baseline z logów CI); w rdzeniu jest tylko
`scripts/check_skips.py`. Raport kroku wymienia
kombinacje z wynikiem (PASS/FAIL/NOT_RUN), wersje interpreterów i komendy.
