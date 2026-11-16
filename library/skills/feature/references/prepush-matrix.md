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
| każda zmiana `tests/skip-baseline.json` (wpis, kategoria, klucz systemu, `null` → lista) | CAŁY moduł `test_skillsync_check_skips` (strażnik kształtu `RealBaselineTests` oczekuje listy albo `null` według krotki `measured`) i `test_skillsync_ci_workflows` na najstarszej i najnowszej wersji Pythona, plus `check_skips.py` na logach wszystkich systemów; sam skrypt na logach nie wystarcza (d6cabea, baseline ubuntu-latest: czerwony strażnik kształtu na origin) |
| nowa kategoria lub fragment skipa | samospójność: każdy fragment klasyfikuje się do własnej kategorii |
| pełna pętla Linux przed pushem (krok obowiązkowy) | pętla z `-v` do pliku logu, potem `scripts/check_skips.py` na tym logu (rc=0); komendy w bloku „Komendy kroków obowiązkowych” pod tabelą. Pętla bez `-v` nie jest dowodem: gdy w logu są skipy (a pełna pętla je ma), `check_skips.py` kończy się kodem 2, bo podsumowanie mówi o skipach, a log nie ma wierszy wyników; log bez skipów bez `-v` da 0, ale nic nie dowodzi |
| lokalne E2E prototypu | ten sam krok co w CI (`.github/workflows/ci.yml`, job „Prototype E2E (Chrome)”): każdy skip w logu modułu `prototype_e2e` to porażka (zielone `OK` przy skipach nie dowodzi E2E); komendy w bloku pod tabelą |

### Komendy kroków obowiązkowych

```sh
# pełna pętla Linux (z -v!), potem baseline skipów
python3 -m unittest discover -s tests -p 'test_skillsync*.py' -v 2>&1 | tee unittest.log
python3 scripts/check_skips.py unittest.log --system linux --mode symlink

# lokalne E2E prototypu (jak w CI): każdy skip to porażka
rc=0
SKILLSYNC_E2E_CHROME=<chrome> python3 -m unittest discover -s tests -p 'test_skillsync_prototype_e2e.py' -v > e2e.log 2>&1 || rc=$?
cat e2e.log
[ "$rc" -eq 0 ] || echo "E2E: bieg zakończony kodem $rc: porażka"      # jak w CI: rc == 0 ORAZ brak skipów
if grep -Eq '\.\.\. skipped|^OK \(skipped' e2e.log; then echo "E2E pominięte: porażka"; fi
```

Zakres: maszyny Windows i macOS są wspólne dla projektów, więc **pełna macierz przed pushem idzie raz, na gałąź wydania** (integracyjną); gałąź robocza dostaje dowód punktowy (zmienione moduły na Windows 3.11, 3.12 i 3.13, macOS dla HTTP, plików i sygnałów), nie pełny bieg.

Zasady biegów: procesy testów zatrzymujesz **wyłącznie po własnym PID** (albo własnej grupie procesów lub scope `systemd-run`), nigdy po wzorcu nazwy (`pkill -f`): na wspólnej stacji zabijesz cudzy bieg. Równoległe biegi na jednej maszynie (np. Windows 3.11, 3.12 i 3.13 naraz) dostają rozłączne plasterki portów testowych serwerów (`SKILLSYNC_TEST_DASHBOARD_PORTS=<od>-<do>`), inaczej moduły z serwerem dają `WinError 10048` (zajęty port), co nie jest porażką produktu.

## Biegi na maszynie zdalnej

Lekcje z biegów macierzy na maszynach zdalnych (Windows, macOS):

- **Logi przyrostowo.** Logi modułów kopiuj na stację w trakcie biegu (co kilka minut), nie dopiero po jego końcu: wiszący albo ubity bieg zostawia wtedy dowód,
  a `check_skips` ma co czytać.
- **Śmierć po PID-ach całego drzewa.** Po `kill` sprawdź, że wszystkie procesy drzewa (runner, interpreter, serwery testów) nie żyją (po PID-ach zapisanych przy
  starcie, nie po nazwie), dopiero potem usuń katalogi robocze; inaczej żywy proces trzyma pliki i porty, a usunięcie zawodzi albo psuje następny bieg.
- **Jedna pula portów = jeden proces.** Plasterek portów testowych należy do jednego biegu naraz; dwa procesy na tym samym plasterku dają zajęty port, który
  wygląda jak porażka produktu.
- **Lista modułów bez aliasów powłoki.** Listę modułów do biegu zbieraj poleceniem, którego nie podmienia alias (`command ls`, ścieżka bezwzględna albo `glob` w
  skrypcie): alias z kolorami i ikonami wstawia do listy kody sterujące, a bieg uruchamia puste albo błędne nazwy modułów.
- **Start w tle na macOS bez `setsid`.** Standardowy macOS nie ma polecenia `setsid`; start w tle rób przez `nohup` z przekierowaniem i zapisem PID-u w pliku, a
  w ciągu minuty sprawdź, że proces żyje i log rośnie.

Monitor CI: wyniku `gh pr checks` nie iteruj pętlą `for` po zmiennej (zsh nie dzieli zmiennej na linie, cały wynik to jeden element); używaj `gh pr checks <nr> --json name,state` (z `jq`) albo `gh pr checks <nr> | while read -r line; do …; done`.

Po ciężkim biegu na zasobie współdzielonym (stacja, maszyna zdalna) sprawdź, że blokada (`flock`, plik lease) i procesy testowe zniknęły (`pgrep` po zapisanych PID-ach, `fuser`/`lsof` na pliku blokady, brak nasłuchujących portów z puli), zamiast ufać deklaracji biegu: dopiero to zwalnia zasób.

Skrypty pomocnicze leżą w repo operacyjnym `skillsync-ops` (`tools/win-shard/`): `prepush.sh` (macierz Windows i `check_skips` z jednej komendy),
`runlike.sh`/`runlike.ps1` (Windows, środowisko jak runner), `skips_union.py` i `apply_windows_baseline.py` (baseline z logów CI); w rdzeniu jest tylko
`scripts/check_skips.py`. Raport kroku wymienia
kombinacje z wynikiem (PASS/FAIL/NOT_RUN), wersje interpreterów i komendy.
