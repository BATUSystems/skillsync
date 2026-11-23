-- S1.1: baza źródłowa z 2 plikami danych (.mdf + .ndf, osobna FILEGROUP) i 1 logiem. Podstaw :Dir (katalog testowy, istniejący, ASCII) i :Db.
-- Uruchamiać z sqlcmd -v Dir="C:\probe" Db="skillsync_t6_ab12"
SET NOCOUNT ON;
CREATE DATABASE [$(Db)] ON PRIMARY (NAME = N'src_data', FILENAME = N'$(Dir)\$(Db)_data.mdf', SIZE = 8MB),
  FILEGROUP FG2 (NAME = N'src_data2', FILENAME = N'$(Dir)\$(Db)_data2.ndf', SIZE = 8MB)
  LOG ON (NAME = N'src_log', FILENAME = N'$(Dir)\$(Db)_log.ldf', SIZE = 8MB);
EXEC(N'USE [$(Db)]; CREATE TABLE dbo.t (id int PRIMARY KEY, v nvarchar(50)) ON [PRIMARY]; CREATE TABLE dbo.t2 (id int PRIMARY KEY, v nvarchar(50)) ON FG2; INSERT dbo.t VALUES (1, N''a''); INSERT dbo.t2 VALUES (1, N''b'');');
BACKUP DATABASE [$(Db)] TO DISK = N'$(Dir)\$(Db).bak' WITH INIT, CHECKSUM;
SELECT name, type_desc, physical_name FROM sys.master_files WHERE database_id = DB_ID(N'$(Db)') ORDER BY file_id;
