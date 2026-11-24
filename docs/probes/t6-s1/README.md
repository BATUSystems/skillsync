# Sondy S1 dla T6 MOVE (Dostawa 2): WYKONANE (dowody: evidence-2026-11-23.md)

Status: sondy S1 wykonane 2026-11-23 (wyniki: `evidence-2026-11-23.md`); skrypty do powtórzenia wyłącznie za jawną zgodą właściciela maszyny i z przydziałem zasobu.

## Zakres (fakty ze specu T6; reszta S7/później)
Fakty 1, 2, 3, 4, 6 (tylko część: nagłówek własnej kopii; LocalDB innego użytkownika NOT_RUN bez drugiego konta), 7, 10 (bez ograniczonego loginu: NOT_RUN). Fakty 5, 8, 9 w S1 częściowo: 8 (SINGLE_USER + DROP) w sprzątaniu każdej sondy; 5 i 9 poza S1 (S7).

## Środowisko i granice
- Maszyna testowa (Windows), LocalDB 2019, sqlcmd 15.0, auth zintegrowane (-E). Instancja: najpierw `sqllocaldb i` (odczyt) i wybór istniejącej testowej; nie tworzymy ani nie zatrzymujemy cudzych instancji.
- Tylko bazy `skillsync_t6_<4 hex>`; katalog testowy `%TEMP%\skillsync-t6-s1-<Id>\{a,b,bak,c,d}` (ASCII) oraz `...\<run>-pl\zażółć` dla faktu 7. Żadnych innych baz ani plików.
- Przed startem: przydział zasobu (maszyna testowa wolna od innych biegów). Jeden bieg naraz, `timeout 300` na każde wywołanie sqlcmd.
- Opcje sqlcmd jak w `db_snapshot.py:388-393`: `-S (localdb)\<inst> -d master -E -b -l 60` (bez `-x`) oraz dla tabel `-W -s "|" -w 65535`. Zmienne `-v Dir=... Db=...`.
- Brak sekretów w zapisie wyników; ścieżki profilu zamaskowane w raporcie.

## Kroki (sprzątanie jednorazowo w `finally` skryptu `run-s1.ps1`; `90-cleanup.sql` tylko do ręcznego użycia)
| Krok | Plik | Fakt | Kryterium PASS (obserwacja, nie werdykt silnika) |
|---|---|---|---|
| S1.0 | 00-env.sql | 4, 6, 10 | wartości `InstanceDefaultDataPath/LogPath` (w tym NULL i końcowy separator), `ServerName`, `MachineName` zapisane dosłownie |
| S1.1 | 10-create-source.sql | 2 | baza z .mdf+.ndf+.ldf i kopia `.bak` istnieją |
| S1.2 | (dwa wywołania -Q w run-s1.ps1; 20-filelistonly.sql tylko do ręcznego użycia) | 3 | liczba kolumn i nagłówki FILELISTONLY w trybie `-W -s "|"`, wartości `Type`, kolejność wierszy; `parse_sqlcmd_table` (db_snapshot.py:473) parsuje wynik (sprawdzenie offline na zapisanym wyjściu) |
| S1.3 | 30-restore-nomove-missing-paths.sql | 1 | zapisany błąd (numer, treść) i moment albo informacja, że silnik użył obecnych plików; stan bazy po próbie |
| S1.4 | 40-restore-move-existing.sql | 2 | `physical_name` = obecne ścieżki, brak sierot w katalogu, tabele t=1, t2=1 wiersz |
| S1.5 | (skrypt wywołujący) | 7 | `BACKUP` do katalogu z polskimi znakami: czy sqlcmd (code page) przyjmuje ścieżkę; zapis dosłowny, co potwierdza lub zmienia Q5 |
| S1.6 | (skrypt wywołujący) | 10 | nazwa logiczna ze spacją brzegową: zachowanie `-W` i `MOVE` (na świeżej bazie skillsync_t6_*) |

S1.3 wymaga przygotowania: baza B (`skillsync_t6_<b>`) w katalogu `b` (kopia .bak przeniesiona do `bak`), następnie DROP B i usunięcie katalogu `b` (tylko nasz podkatalog w %TEMP%), potem RESTORE na istniejącą bazę A z kopią B (nazwa i pliki różne). Robi to skrypt wywołujący (PowerShell na maszynie testowej, kolejność: create B, backup B, DROP B, usuń `b`, restore, odczyt stanu).

## Lista do utworzenia i do sprzątnięcia
Bazy: `skillsync_t6_<id>` (źródłowa A), `skillsync_t6_<id>b` (B), `skillsync_t6_<id>c` (S1.6), `skillsync_t6_<id>d` (S1.5). Katalogi: `skillsync-t6-s1-<Id>\{a,b,bak,c,d}`, `<run>-pl\zażółć`. Po bieg: `sys.databases LIKE 'skillsync_t6_%'` musi być puste, katalogi usunięte (kosz niedostępny na Windows: tylko nasze ścieżki, po wypisaniu listy), zapisany raport z wynikiem sprzątania.

## Wynik i dalszy krok
Raport sond (obserwacje dosłownie, bez interpretacji w kodzie) trafia do T6 jako dowód K8 i do specu (sekcja „Fakty”). Dostawa 2 (kod `MOVE`) nie zmienia trybu `none`.
Otwarte: instancja LocalDB do użycia (S1.0 odczyt), czy jest drugie konto Windows dla faktu 6 (inaczej NOT_RUN).

## Skrypt wywołujący (dopisany 23.11)
`run-s1.ps1 -Instance <LocalDB> -Id <4 hex>` (Id wybrany i zapisany PRZED startem) wykonuje S1.0–S1.6 po kolei; wszystko w `try/finally` z sprzątaniem; `-CleanupOnly -Id <4 hex>` sprząta po przerwaniu (tylko bazy `skillsync_t6_<Id>*` i katalogi `%TEMP%\skillsync-t6-s1-<Id>[-pl]`). Logi zostają w `%TEMP%\skillsync-t6-s1-<Id>-log`; po zebraniu `-CleanupOnly -Id <Id> -RemoveLog` usuwa też ten katalog. Limit 300 s na wywołanie sqlcmd (potem zabicie PID, rc=124). Auth zintegrowane, brak sekretów.

Uwagi: bez `-x` w sqlcmd (wyłączałoby zmienne `$(var)`); S1.4 faktycznie pomijane, gdy A nie jest ONLINE; S1.5/S1.6 na niezależnych bazach (…d, …c); skrypt kończy kodem 1 przy niepełnym sprzątaniu lub kroku z rc≠0 (część rc≠0 to oczekiwane obserwacje); logi zawierają nazwę konta i ścieżki profilu: maskować przed przekazaniem. Przed GRANT: ParseFile w PS 5.1 oraz próba `sqlcmd -v X=1 -Q "SELECT '$(X)'"` na instancji.
Id: 4 małe znaki hex (wielkość liter wymuszona).
