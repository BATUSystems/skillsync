-- S1.4: fakt 2. MOVE na obecne ścieżki istniejącej bazy z REPLACE (2 pliki danych + log); potem odczyt physical_name, zawartość tabel, brak sierot w $(Dir).
RESTORE DATABASE [$(Db)] FROM DISK = N'$(Dir)\$(Db).bak'
  WITH REPLACE, MOVE N'src_data' TO N'$(Dir)\$(Db)_data.mdf', MOVE N'src_data2' TO N'$(Dir)\$(Db)_data2.ndf', MOVE N'src_log' TO N'$(Dir)\$(Db)_log.ldf', STATS = 100;
SELECT name, type_desc, physical_name FROM sys.master_files WHERE database_id = DB_ID(N'$(Db)') ORDER BY file_id;
EXEC(N'USE [$(Db)]; SELECT (SELECT COUNT(*) FROM dbo.t) AS t_rows, (SELECT COUNT(*) FROM dbo.t2) AS t2_rows;');
