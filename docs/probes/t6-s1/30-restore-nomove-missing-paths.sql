-- S1.3: fakt 1. Baza $(Db) istnieje (pliki w innym katalogu). Kopia $(Db2).bak pochodzi z bazy B, której pliki leżały w katalogu już USUNIĘTYM
-- (robi to run-s1.ps1: DROP B, usunięcie jej katalogu, kopia .bak w $(Dir2)). Do zapisu: błąd (numer, treść) i moment, albo użycie obecnych plików bazy.
RESTORE DATABASE [$(Db)] FROM DISK = N'$(Dir2)\$(Db2).bak' WITH REPLACE, STATS = 100;
