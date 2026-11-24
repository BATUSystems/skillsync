-- S1.2: fakt 3 (kolumny i format FILELISTONLY; uruchomić DOKŁADNIE z opcjami db_snapshot: -W -s "|" -w 65535 -b; BEZ -x, bo wyłącza zmienne; plik nieużywany przez run-s1.ps1, S1.2 idzie przez -Q). Tylko odczyt.
RESTORE FILELISTONLY FROM DISK = N'$(Dir)\$(Db).bak' WITH FILE = 1;
RESTORE HEADERONLY FROM DISK = N'$(Dir)\$(Db).bak';
