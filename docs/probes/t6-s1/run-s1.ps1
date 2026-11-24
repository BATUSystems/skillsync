# Sondy S1 T6 MOVE. RUN ONLY on a test machine with an explicit grant. Windows PowerShell 5.1+ (file is ASCII only on purpose).
# Full run:      powershell -File run-s1.ps1 -Instance <LocalDB name> -Id <4 hex, chosen and written down BEFORE start>
# Wrapper self-test (read-only SELECTs, no databases; FAIL only on the Polish cases = code page of the output, not quoting):  -Instance <name> -Id <fresh 4 hex> -SelfTest
# Cleanup only:  powershell -File run-s1.ps1 -Instance <name> -Id <4 hex> -CleanupOnly [-RemoveLog]
# Limits: only databases skillsync_t6_<Id>[bcd] and dirs %TEMP%\skillsync-t6-s1-<Id>[-pl|-log]; integrated auth (-E), no secrets;
# every sqlcmd call has a 300 s limit (client is killed; the server may still finish the statement); cleanup in finally and via -CleanupOnly.
# Exit code: 0 = all steps rc=0 and cleanup complete; 1 = some step failed (observations still valid) or cleanup incomplete.
param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$Instance,
  [Parameter(Mandatory=$true)][ValidatePattern('^(?-i)[0-9a-f]{4}\z')][string]$Id,
  [switch]$CleanupOnly,
  [switch]$SelfTest,
  [switch]$RemoveLog
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Server = "(localdb)\$Instance"
$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$TempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
if ($TempRoot.Contains("'")) { throw 'TEMP contains an apostrophe: refusing' }
$Root   = "$TempRoot\skillsync-t6-s1-$Id"
$RootPl = "$TempRoot\skillsync-t6-s1-$Id-pl"
$LogDir = "$TempRoot\skillsync-t6-s1-$Id-log"
$DbA = "skillsync_t6_$Id"; $DbB = "skillsync_t6_${Id}b"; $DbC = "skillsync_t6_${Id}c"; $DbD = "skillsync_t6_${Id}d"
$Dbs = @($DbA, $DbB, $DbC, $DbD)
$script:Failures = 0
$script:CleanupFailures = 0
$SqlcmdExe = (Get-Command sqlcmd.exe -CommandType Application | Select-Object -First 1).Source
foreach ($v in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'SQLCMD*' })) { Remove-Item -LiteralPath ("Env:\" + $v.Name) }

function Assert-OwnDb([string]$n) { if ($n -cnotmatch "^skillsync_t6_$Id[bcd]?$") { throw 'foreign database: refusing' } }
function Assert-OwnDir([string]$d) {
  $full = [System.IO.Path]::GetFullPath($d).TrimEnd('\')
  if ($full -cnotmatch '^.+\\skillsync-t6-s1-[0-9a-f]{4}(-pl|-log)?$') { throw 'foreign directory: refusing' }
  if (-not $full.StartsWith("$TempRoot\skillsync-t6-s1-$Id", [System.StringComparison]::OrdinalIgnoreCase)) { throw 'foreign directory: refusing' }
  if (-not [string]::Equals([System.IO.Path]::GetDirectoryName($full), $TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'foreign directory: refusing' }
}
function Remove-Tree([string]$d) {
  if (-not (Test-Path -LiteralPath $d)) { return }
  if ((Get-Item -LiteralPath $d -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw 'reparse point at the root: refusing' }
  $links = @(Get-ChildItem -LiteralPath $d -Recurse -Force | Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint })
  if ($links.Count -gt 0) { throw 'reparse point inside: refusing' }
  Remove-Item -LiteralPath $d -Recurse -Force
}
function Remove-OwnDir([string]$d) { Assert-OwnDir $d; Remove-Tree $d }
# a fixed subdirectory directly under our own $Root (only the names used by this script)
function Remove-OwnSub([string]$d) {
  $full = [System.IO.Path]::GetFullPath($d).TrimEnd('\')
  Assert-OwnDir $Root
  if ($full -cnotmatch '\\skillsync-t6-s1-[0-9a-f]{4}\\(a|b|bak|c|d)$') { throw 'foreign subdirectory: refusing' }
  if (-not [string]::Equals([System.IO.Path]::GetDirectoryName($full), $Root, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'foreign subdirectory: refusing' }
  Remove-Tree $full
}
function ConvertTo-ArgText([string]$v) {
  $e = [regex]::Replace($v, '(\\*)"', '$1$1\"')
  $e = [regex]::Replace($e, '(\\+)$', '$1$1')
  return $e
}
function Invoke-Sqlcmd2([string[]]$ArgList, [string]$LogName) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $SqlcmdExe
  # no -x: it would disable the $(var) substitution used by the -i scripts
  $all = @('-S', $Server, '-d', 'master', '-E', '-b', '-l', '60', '-W', '-s', '|', '-w', '65535') + $ArgList
  # option flags (-S, -E, ...) must stay unquoted: sqlcmd rejects a quoted flag; values are always quoted
  $parts = New-Object System.Collections.Generic.List[string]; $afterV = $false
  foreach ($x in $all) {
    if ($x -cmatch '^-[A-Za-z]$') { $parts.Add($x); $afterV = ($x -ceq '-v'); continue }
    if ($afterV -and $x -cmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $parts.Add($Matches[1] + '="' + $Matches[2] + '"') }  # sqlcmd wants -v Name="value" Name2="value2" and takes the value literally (SelfTest 23.11: a doubled trailing backslash arrives doubled), so no CommandLineToArgvW escaping here
    else { $parts.Add('"' + (ConvertTo-ArgText $x) + '"') }
  }
  $psi.Arguments = $parts -join ' '
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
  $p = [System.Diagnostics.Process]::Start($psi)
  $outTask = $p.StandardOutput.ReadToEndAsync(); $errTask = $p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit(300000)) { try { $p.Kill() } catch {}; $rc = 124 } else { $rc = $p.ExitCode }
  $out = $outTask.Result; $err = $errTask.Result
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  Set-Content -LiteralPath "$LogDir\$LogName.txt" -Value @("rc=$rc", '--- stdout', $out, '--- stderr', $err) -Encoding UTF8
  Write-Host ("[{0}] rc={1}" -f $LogName, $rc)
  Write-Host $out; if ($err) { Write-Host $err }
  if ($rc -ne 0) { if ($LogName.StartsWith('cleanup')) { $script:CleanupFailures++ } else { $script:Failures++ } }
  return $rc
}
function Remove-OwnDb([string]$n, [string]$LogPrefix = 'cleanup') {
  Assert-OwnDb $n
  $sql = "IF DB_ID(N'$n') IS NOT NULL BEGIN BEGIN TRY ALTER DATABASE [$n] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; END TRY BEGIN CATCH PRINT 'alter failed, trying drop'; END CATCH; DROP DATABASE [$n]; END"
  [void](Invoke-Sqlcmd2 @('-Q', $sql) "$LogPrefix-$n")
}
function Get-LeftoverDbs {
  $list = ($Dbs | ForEach-Object { "N'$_'" }) -join ','
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $SqlcmdExe
  $psi.Arguments = "-S `"$Server`" -d master -E -b -l 60 -h -1 -W -Q `"SET NOCOUNT ON; SELECT name FROM sys.databases WHERE name IN ($list)`""
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
  $p = [System.Diagnostics.Process]::Start($psi)
  $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit(120000)) { try { $p.Kill() } catch {}; return @('<check timed out>') }
  if ($p.ExitCode -ne 0) { return @('<check failed>') }
  return @($o.Result -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
}
function Invoke-Cleanup {
  foreach ($n in $Dbs) { try { Remove-OwnDb $n } catch { Write-Host "cleanup db ${n}: $($_.Exception.Message)" } }
  $left = @(Get-LeftoverDbs)
  if ($left.Count -eq 0) {
    foreach ($d in @($Root, $RootPl)) { try { Remove-OwnDir $d } catch { Write-Host "cleanup dir: $($_.Exception.Message)" } }
  } else { Write-Host "databases remain, directories KEPT: $($left -join ', ')" }
  if ($RemoveLog) { try { Remove-OwnDir $LogDir } catch { Write-Host "cleanup log dir: $($_.Exception.Message)" } }
  $dirsLeft = @(@($Root, $RootPl) | Where-Object { Test-Path -LiteralPath $_ })
  $ok = ($left.Count -eq 0) -and ($dirsLeft.Count -eq 0) -and ($script:CleanupFailures -eq 0)
  Write-Host ("CLEANUP {0}: databases left={1}; directories left={2}; sqlcmd failures in cleanup={3}" -f $(if ($ok) { 'COMPLETE' } else { 'INCOMPLETE' }), $left.Count, $dirsLeft.Count, $script:CleanupFailures)
  return $ok
}

Write-Host "Id=$Id Server=$Server Root=$Root (write the Id down: -CleanupOnly needs it)"
function Get-LogText([string]$name) { return (Get-Content -LiteralPath "$LogDir\$name.txt" -Raw -Encoding UTF8) }
if ($SelfTest) {
  $pl = [string]::Concat('za', [char]0x017C, [char]0x00F3, [char]0x0142, [char]0x0107)
  $fail = 0
  if ((Test-Path -LiteralPath $Root) -or (Test-Path -LiteralPath $LogDir)) { throw 'SelfTest: Id already used (directories exist): refusing' }
  New-Item -ItemType Directory -Force -Path $Root | Out-Null
  try {
    $cases = @(
      @{ n = 'st-space';    v = "C:\x y\z";            exp = "C:\x y\z" },
      @{ n = 'st-polish';   v = "C:\$pl\k";            exp = "C:\$pl\k" },
      @{ n = 'st-trailbs';  v = "C:\x y\";             exp = "C:\x y\" },
      @{ n = 'st-trailbs2'; v = "C:\$pl y\\";         exp = "C:\$pl y\\" },
      @{ n = 'st-plain';    v = "abc";                  exp = "abc" }
    )
    foreach ($c in $cases) {
      $rc = Invoke-Sqlcmd2 @('-Q', 'SET NOCOUNT ON; SELECT N''[$(Val)]'' AS r', '-v', "Val=$($c.v)") $c.n
      $ok = ($rc -eq 0) -and ((Get-LogText $c.n).Contains('[' + $c.exp + ']'))
      Write-Host ("SELFTEST -v/-Q {0}: {1}" -f $c.n, $(if ($ok) { 'PASS' } else { 'FAIL' })); if (-not $ok) { $fail++ }
    }
    # -i with a file, two -v values, one with a space and a trailing backslash
    $f = "$Root\selftest.sql"
    Set-Content -LiteralPath $f -Value "SET NOCOUNT ON; SELECT N'[`$(A)|`$(B)]' AS r;" -Encoding ASCII
    $rc = Invoke-Sqlcmd2 @('-i', $f, '-v', "A=C:\x y\", "B=$pl") 'st-file'
    $ok = ($rc -eq 0) -and ((Get-LogText 'st-file').Contains("[C:\x y\|$pl]"))
    Write-Host ("SELFTEST -i/-v two values: {0}" -f $(if ($ok) { 'PASS' } else { 'FAIL' })); if (-not $ok) { $fail++ }
    # server name shape, -E, -b, -W, -s: the wrapper always passes them; a missing column separator would show in the output
    $rc = Invoke-Sqlcmd2 @('-Q', "SET NOCOUNT ON; SELECT 1 AS a, 2 AS b") 'st-shape'
    $ok = ($rc -eq 0) -and ((Get-LogText 'st-shape').Contains('a|b'))
    Write-Host ("SELFTEST -S/-E/-b/-W/-s/-w shape: {0}" -f $(if ($ok) { 'PASS' } else { 'FAIL' })); if (-not $ok) { $fail++ }
  }
  finally {
    Remove-OwnDir $Root; Remove-OwnDir $LogDir
    Write-Host ("SELFTEST cleanup: dirs left={0}" -f @(@($Root, $LogDir) | Where-Object { Test-Path -LiteralPath $_ }).Count)
  }
  Write-Host ("SELFTEST failures: {0}" -f $fail)
  if ($fail -eq 0) { exit 0 } else { exit 1 }
}
if ($CleanupOnly) { if (Invoke-Cleanup) { exit 0 } else { exit 1 } }

$cleanOk = $false
try {
  New-Item -ItemType Directory -Force -Path $Root, "$Root\a", "$Root\b", "$Root\bak", "$Root\c", "$Root\d" | Out-Null
  $Dir = "$Root\a"; $DirB = "$Root\b"; $BakDir = "$Root\bak"; $DirC = "$Root\c"; $DirD = "$Root\d"

  Write-Host '== S1.0 environment (facts 4, 6, 10)'
  [void](Invoke-Sqlcmd2 @('-i', "$Here\00-env.sql") 's1-0-env')

  Write-Host '== S1.1 source database A with backup; S1.2 FILELISTONLY and HEADERONLY separately (fact 3)'
  $rc1 = Invoke-Sqlcmd2 @('-i', "$Here\10-create-source.sql", '-v', "Dir=$Dir", "Db=$DbA") 's1-1-create-a'
  if ($rc1 -eq 0) {
    [void](Invoke-Sqlcmd2 @('-Q', "RESTORE FILELISTONLY FROM DISK = N'$Dir\$DbA.bak' WITH FILE = 1") 's1-2-filelistonly')
    [void](Invoke-Sqlcmd2 @('-Q', "RESTORE HEADERONLY FROM DISK = N'$Dir\$DbA.bak'") 's1-2-headeronly')
  } else { Write-Host 'S1.1 failed: S1.2-S1.4 skipped (A missing)' }

  Write-Host '== S1.3 RESTORE without MOVE when the paths from the backup do not exist (fact 1)'
  $rc3 = Invoke-Sqlcmd2 @('-i', "$Here\10-create-source.sql", '-v', "Dir=$DirB", "Db=$DbB") 's1-3-create-b'
  if ($rc1 -eq 0 -and $rc3 -eq 0) {
    Copy-Item -LiteralPath "$DirB\$DbB.bak" -Destination "$BakDir\$DbB.bak"
    Remove-OwnDb $DbB 's1-3-drop'
    $left = @(Get-LeftoverDbs | Where-Object { $_ -eq $DbB -or $_ -like '<check*' })
    if (@($left).Count -eq 0) {
      Remove-OwnSub $DirB
      [void](Invoke-Sqlcmd2 @('-i', "$Here\30-restore-nomove-missing-paths.sql", '-v', "Db=$DbA", "Db2=$DbB", "Dir2=$BakDir") 's1-3-restore-nomove')
      [void](Invoke-Sqlcmd2 @('-Q', "SET NOCOUNT ON; SELECT name, state_desc FROM sys.databases WHERE name = N'$DbA'; SELECT name, physical_name FROM sys.master_files WHERE database_id = DB_ID(N'$DbA')") 's1-3-state-after')
    } else { Write-Host 'S1.3: database B was not dropped; restore skipped' }
  } else { Write-Host 'S1.3 skipped (A or B not created)' }

  Write-Host '== S1.4 RESTORE with MOVE onto the current paths (fact 2); only when A is ONLINE'
  $online = Invoke-Sqlcmd2 @('-Q', "IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'$DbA' AND state = 0) RAISERROR('A is not ONLINE', 16, 1)") 's1-4-precheck'
  if ($online -eq 0) {
    [void](Invoke-Sqlcmd2 @('-i', "$Here\40-restore-move-existing.sql", '-v', "Dir=$Dir", "Db=$DbA") 's1-4-restore-move')
    Get-ChildItem -LiteralPath $Dir | ForEach-Object { Write-Host ("file: {0} {1}" -f $_.Name, $_.Length) }
  } else { Write-Host 'S1.4 skipped: A is not ONLINE after S1.3 (this itself is a fact-1 observation)' }

  Write-Host '== S1.5 path with Polish letters (fact 7); independent database D'
  $PlDir = "$RootPl\" + [string]::Concat('za', [char]0x017C, [char]0x00F3, [char]0x0142, [char]0x0107)
  New-Item -ItemType Directory -Force -Path $PlDir | Out-Null
  $rc5 = Invoke-Sqlcmd2 @('-Q', "CREATE DATABASE [$DbD] ON PRIMARY (NAME = N'd_data', FILENAME = N'$DirD\d.mdf', SIZE = 8MB) LOG ON (NAME = N'd_log', FILENAME = N'$DirD\d.ldf', SIZE = 8MB)") 's1-5-create-d'
  if ($rc5 -eq 0) {
    [void](Invoke-Sqlcmd2 @('-Q', "BACKUP DATABASE [$DbD] TO DISK = N'$PlDir\pl.bak' WITH INIT, CHECKSUM; RESTORE VERIFYONLY FROM DISK = N'$PlDir\pl.bak'") 's1-5-nonascii')
    Get-ChildItem -LiteralPath $PlDir | ForEach-Object { Write-Host ("file: {0} {1}" -f $_.Name, $_.Length) }
  }

  Write-Host '== S1.6 logical name with a trailing space (fact 10)'
  $sqlC = "CREATE DATABASE [$DbC] ON PRIMARY (NAME = N'sp_data ', FILENAME = N'$DirC\c.mdf', SIZE = 8MB) LOG ON (NAME = N'sp_log', FILENAME = N'$DirC\c.ldf', SIZE = 8MB); BACKUP DATABASE [$DbC] TO DISK = N'$DirC\c.bak' WITH INIT; RESTORE FILELISTONLY FROM DISK = N'$DirC\c.bak'; SELECT name, '[' + name + ']' AS bracketed, DATALENGTH(name) AS dl, LEN(name + N'x') AS lenx, CONVERT(varbinary(40), name) AS hex FROM sys.master_files WHERE database_id = DB_ID(N'$DbC')"
  [void](Invoke-Sqlcmd2 @('-Q', $sqlC) 's1-6-trailing-space')
  $sqlC2 = "CREATE TABLE #f (LogicalName nvarchar(128), PhysicalName nvarchar(260), Type char(1), FileGroupName nvarchar(128), Size numeric(20,0), MaxSize numeric(20,0), FileId bigint, CreateLSN numeric(25,0), DropLSN numeric(25,0) NULL, UniqueId uniqueidentifier, ReadOnlyLSN numeric(25,0) NULL, ReadWriteLSN numeric(25,0) NULL, BackupSizeInBytes bigint, SourceBlockSize int, FileGroupId int, LogGroupGUID uniqueidentifier NULL, DifferentialBaseLSN numeric(25,0) NULL, DifferentialBaseGUID uniqueidentifier, IsReadOnly bit, IsPresent bit, TDEThumbprint varbinary(32), SnapshotUrl nvarchar(360)); INSERT #f EXEC(N'RESTORE FILELISTONLY FROM DISK = N''$DirC\c.bak'''); SELECT LogicalName, DATALENGTH(LogicalName) AS dl, LEN(LogicalName + N'x') AS lenx, CONVERT(varbinary(40), LogicalName) AS hex FROM #f"
  [void](Invoke-Sqlcmd2 @('-Q', $sqlC2) 's1-6b-filelist-raw')
  Write-Host "Logs: $LogDir (collect, then run -CleanupOnly -RemoveLog)"
}
finally {
  Write-Host '== CLEANUP'
  $cleanOk = Invoke-Cleanup
}
Write-Host ("step failures (rc != 0, some are expected observations): {0}" -f $script:Failures)
if ($cleanOk -and $script:Failures -eq 0) { exit 0 } else { exit 1 }
