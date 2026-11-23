-- S1.0: tylko odczyt. Wersja silnika, domyślne ścieżki (fakt 4, 10), tożsamość serwera (fakt 6).
SET NOCOUNT ON;
SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(40)) AS ProductVersion,
       CAST(SERVERPROPERTY('Edition') AS nvarchar(80)) AS Edition,
       CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(260)) AS DataPath,
       CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(260)) AS LogPath,
       CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS ServerName,
       CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS MachineName,
       SUSER_SNAME() AS LoginName;
SELECT name, state_desc FROM sys.databases WHERE name LIKE N'skillsync[_]t6[_]%' ESCAPE N'\' OR name LIKE N'skillsync[_]t6[_]%';
