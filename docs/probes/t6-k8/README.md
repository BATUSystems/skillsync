# K8(b): test `db_snapshot restore --move` na LocalDB 2019 (przygotowane, do wykonania za zgodą i z przydziałem zasobu)

Skrypt: `run-k8.ps1` (oparty na wrapperze `../t6-s1/run-s1.ps1`; helpery były uruchomione na żywo w S1).
Uruchomienie z kopii roboczej narzędzia (`db_snapshot.py`) i skryptu we własnym katalogu w `%TEMP%`, posprzątanej po biegu:
1. Preflight (tylko odczyt, bez baz): `powershell -File run-k8.ps1 -Instance <nazwa> -Id <4 hex> -ToolPath <kopia db_snapshot.py> -PreflightOnly`.
2. Bieg: `... -Instance <nazwa> -Id <nowe 4 hex> -ToolPath <kopia>`; kontrola listy baz instancji przed i po (w wyniku `DBLIST-BEFORE/AFTER`, `DBLIST IDENTICAL`).
3. Po zebraniu logów: `... -CleanupOnly -Id <Id> -RemoveLog`.

Scenariusze (spec K8(b)): b1 `--move existing` (dane zmienione po kopii wracają, `physical_name` bez zmian, brak sierot, kopia bezpieczeństwa), b2 `--move new` z `--move-dir` i z domyślnymi ścieżkami silnika (pliki w katalogach domyślnych tylko wypisywane po DROP), b3 porażka `new` (kod 1, obserwacja wariantu komunikatu), b4 `--move none` na kopii z nieistniejącymi ścieżkami (kod 1, obserwacja `user_access_desc`), b5 `--move none` bez zmian.
Granice: tylko bazy `skillsync_t6_<Id>[bcd]`, tylko własne katalogi `%TEMP%\skillsync-t6-s1-<Id>[-pl|-log]`; auth zintegrowane; bez sekretów; limity 300 s na `sqlcmd`, 900 s na narzędzie. Wyniki w `evidence-*.md` po biegu (zanonimizowane).
