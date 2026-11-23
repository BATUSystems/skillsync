-- S1.9: sprzątanie. Tylko bazy skillsync_t6_*; stan RESTORING/dowolny: SINGLE_USER + DROP (fakt 8). Podstaw :Db.
IF DB_ID(N'$(Db)') IS NOT NULL
BEGIN
  BEGIN TRY ALTER DATABASE [$(Db)] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; END TRY BEGIN CATCH PRINT 'alter failed, trying drop'; END CATCH;
  DROP DATABASE [$(Db)];
END
