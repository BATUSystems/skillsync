# K8 T6 MOVE (test nowego kodu db_snapshot restore --move). Based on the S1 wrapper. RUN ONLY on a test machine with an explicit grant. Windows PowerShell 5.1+ (file is ASCII only on purpose).
# Full run:      powershell -File run-k8.ps1 -Instance <LocalDB name> -Id <4 hex, chosen and written down BEFORE start> -ToolPath <db_snapshot.py copy in %TEMP%>
# Preflight (read only, creates no database): add -PreflightOnly to the full-run command (checks the helpers, Python, default directories)
# Cleanup only:  powershell -File run-k8.ps1 -Instance <name> -Id <4 hex> -CleanupOnly [-RemoveLog]   (no -ToolPath needed)
# Limits: only databases skillsync_t6_<Id>[bcd] and dirs %TEMP%\skillsync-t6-s1-<Id>[-pl|-log]; integrated auth (-E), no secrets;
# every sqlcmd call has a 300 s limit (client is killed; the server may still finish the statement); cleanup in finally and via -CleanupOnly.
# Exit code: 0 = all steps rc=0 and cleanup complete; 1 = some step failed (observations still valid) or cleanup incomplete.
param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$Instance,
  [Parameter(Mandatory=$true)][ValidatePattern('^(?-i)[0-9a-f]{4}\z')][string]$Id,
  [string]$ToolPath = '',
  [switch]$CleanupOnly,
  [switch]$PreflightOnly,
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
$script:ObserveOnly = $false
$script:LogSeq = 0
$script:LastLogPath = ''
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
  $script:LogSeq++
  $script:LastLogPath = ("{0}\{1:D3}-{2}.txt" -f $LogDir, $script:LogSeq, $LogName)
  Set-Content -LiteralPath $script:LastLogPath -Value @("rc=$rc", '--- stdout', $out, '--- stderr', $err) -Encoding UTF8
  Write-Host ("[{0}] rc={1}" -f $LogName, $rc)
  Write-Host $out; if ($err) { Write-Host $err }
  if ($rc -ne 0) { if ($LogName.StartsWith('cleanup')) { $script:CleanupFailures++ } elseif (-not $script:ObserveOnly) { $script:Failures++ } }
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
function Get-LogText([string]$name) { return (Get-Content -LiteralPath $script:LastLogPath -Raw -Encoding UTF8) }
if ($CleanupOnly) { if (Invoke-Cleanup) { exit 0 } else { exit 1 } }

$PyExe = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
if (-not $ToolPath -or -not (Test-Path -LiteralPath $ToolPath)) { throw 'tool not found: pass -ToolPath' }
if ($TempRoot -notmatch '^[A-Za-z0-9 _.\\:-]+$') { throw 'TEMP has characters outside the tool whitelist (8.3 name or non-ASCII): refusing' }
$pyv = (& $PyExe --version 2>&1 | Out-String).Trim()
if ($pyv -notmatch 'Python 3\.(1[1-9]|[2-9][0-9])') { throw "Python 3.11+ required, found: $pyv" }
if ((Test-Path -LiteralPath $Root) -or (Test-Path -LiteralPath $LogDir)) { throw 'Id already used (directories exist): refusing' }
$script:Checks = 0
function Check([string]$name, [bool]$ok) {
  $script:Checks++
  Write-Host ("CHECK {0}: {1}" -f $name, $(if ($ok) { 'PASS' } else { 'FAIL' }))
  if (-not $ok) { $script:Failures++ }
}
function Invoke-Tool([string[]]$ToolArgs, [string]$LogName) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $PyExe
  $parts = New-Object System.Collections.Generic.List[string]
  $parts.Add('"' + (ConvertTo-ArgText $ToolPath) + '"')
  foreach ($x in $ToolArgs) { if ($x -cmatch '^--?[A-Za-z][A-Za-z0-9-]*$') { $parts.Add($x) } else { $parts.Add('"' + (ConvertTo-ArgText $x) + '"') } }
  $psi.Arguments = $parts -join ' '
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
  $p = [System.Diagnostics.Process]::Start($psi)
  $outTask = $p.StandardOutput.ReadToEndAsync(); $errTask = $p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit(900000)) { try { & taskkill.exe /T /F /PID $p.Id | Out-Null } catch {}; $rc = 124 } else { $rc = $p.ExitCode }
  $out = $outTask.Result; $err = $errTask.Result
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  $script:LogSeq++
  $script:LastLogPath = ("{0}\{1:D3}-{2}.txt" -f $LogDir, $script:LogSeq, $LogName)
  Set-Content -LiteralPath $script:LastLogPath -Value @("rc=$rc", '--- stdout', $out, '--- stderr', $err) -Encoding UTF8
  Write-Host ("[{0}] tool rc={1}" -f $LogName, $rc)
  Write-Host $out; if ($err) { Write-Host $err }
  return $rc
}
function Get-Rows([string]$sql, [string]$name) {
  [void](Invoke-Sqlcmd2 @('-Q', "SET NOCOUNT ON; $sql") $name)
  $lines = @((Get-LogText $name) -split "`r?`n")
  $i = 0; while ($i -lt $lines.Count -and $lines[$i] -notmatch '^-+(\|-+)*$') { $i++ }
  $rows = @(); $i++
  while ($i -lt $lines.Count -and $lines[$i].Trim() -ne '' -and $lines[$i] -notmatch '^\(') { $rows += $lines[$i].Trim(); $i++ }
  return $rows
}
function New-TestDb([string]$db, [string]$dir) {
  Assert-OwnDb $db
  $sql = "CREATE DATABASE [$db] ON PRIMARY (NAME = N'k_data', FILENAME = N'$dir\${db}_data.mdf', SIZE = 8MB), FILEGROUP FG2 (NAME = N'k_data2', FILENAME = N'$dir\${db}_data2.ndf', SIZE = 8MB) LOG ON (NAME = N'k_log', FILENAME = N'$dir\${db}_log.ldf', SIZE = 8MB); EXEC(N'USE [$db]; CREATE TABLE dbo.t (id int PRIMARY KEY, v nvarchar(50)) ON [PRIMARY]; CREATE TABLE dbo.t2 (id int PRIMARY KEY, v nvarchar(50)) ON FG2; INSERT dbo.t VALUES (1, N''a''); INSERT dbo.t2 VALUES (1, N''b'');')"
  return (Invoke-Sqlcmd2 @('-Q', $sql) "create-$db")
}
function Get-Counts([string]$db, [string]$name) { return (@(Get-Rows "SELECT CAST((SELECT COUNT(*) FROM [$db].dbo.t) AS varchar(10)) + '/' + CAST((SELECT COUNT(*) FROM [$db].dbo.t2) AS varchar(10)) AS c" $name)) -join ';' }
function Get-Files([string]$db, [string]$name) { return (@(Get-Rows "SELECT name + '=' + physical_name FROM sys.master_files WHERE database_id = DB_ID(N'$db') ORDER BY name" $name)) -join ';' }
function Get-Access([string]$db, [string]$name) { return (@(Get-Rows "SELECT state_desc + '/' + user_access_desc FROM sys.databases WHERE name = N'$db'" $name)) -join ';' }
function Add-Rows([string]$db, [string]$name) { [void](Invoke-Sqlcmd2 @('-Q', "INSERT [$db].dbo.t VALUES (2, N'x'); INSERT [$db].dbo.t2 VALUES (2, N'y')") $name) }
function Get-Backup([string]$db) { $f = Get-ChildItem -LiteralPath $BakDir -Filter "$db-k8-*.bak" | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($f) { return $f.FullName } else { return $null } }
function Get-AllDbs([string]$name) { return (@(Get-Rows 'SELECT name FROM sys.databases ORDER BY name' $name)) -join ';' }
function Get-DefaultDirs([string]$name) { return @(Get-Rows "SELECT ISNULL(CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(260)), N'NULL') + '|' + ISNULL(CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(260)), N'NULL')" $name) }
function Get-ProfileFiles([string[]]$dirs) {
  $found = @()
  foreach ($d in $dirs) { if ($d -and (Test-Path -LiteralPath $d)) { $found += @(Get-ChildItem -LiteralPath $d -Filter "skillsync_t6_${Id}*" -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) } }
  return $found
}
function Db-Exists([string]$db) { return (@(Get-Rows "SELECT name FROM sys.databases WHERE name = N'$db'" "exists-$db").Count -gt 0) }

$cleanOk = $false
$dbsBefore = ''
$defDirs = @()
try {
  New-Item -ItemType Directory -Force -Path $Root, "$Root\a", "$Root\b", "$Root\bak", "$Root\c", "$Root\d" | Out-Null
  $Dir = "$Root\a"; $DirB = "$Root\b"; $BakDir = "$Root\bak"; $DirC = "$Root\c"; $DirD = "$Root\d"
  $ToolArgsBase = @('--server', $Server)

  Write-Host '== K8.0 environment (read only); instance database list before'
  $dbsBefore = Get-AllDbs 'k8-0-dbs-before'
  Write-Host ("DBLIST-BEFORE: {0}" -f $dbsBefore)
  $defRows = @(Get-DefaultDirs 'k8-0-defaultdirs')
  $defDirs = @(); if ($defRows.Count -gt 0) { $defDirs = @(@($defRows[0] -split '\|') | Where-Object { $_ -and $_ -ne 'NULL' }) }
  Write-Host ('DEFAULT DIRS examined: {0}' -f ($defDirs -join '; '))
  Check 'default data/log directories were read (otherwise the leftover-file checks prove nothing)' ($defDirs.Count -ge 1)
  if ($PreflightOnly) {
    Check 'preflight: database list read (non-empty)' ($dbsBefore -ne '')
    Write-Host ('PREFLIGHT done: python={0}; checks={1}; failures={2}' -f $pyv, $script:Checks, $script:Failures)
    return
  }
  [void](Invoke-Sqlcmd2 @('-Q', "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(40)) AS v, CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(260)) AS dp") 'k8-0-env')

  Write-Host '== b1: --move existing (data changed after the backup must come back)'
  $rc = New-TestDb $DbA $Dir
  $rcb = Invoke-Tool (@('backup', '--database', $DbA, '--label', 'k8', '--dir', $BakDir, '--json') + $ToolArgsBase) 'k8-b1-backup'
  $bak = Get-Backup $DbA
  Check 'b1 setup (create, backup, file found)' (($rc -eq 0) -and ($rcb -eq 0) -and ($bak -ne $null))
  if ($bak) {
    $filesBefore = Get-Files $DbA 'k8-b1-files-before'
    Add-Rows $DbA 'k8-b1-modify'
    Check 'b1 data changed before the restore (2/2)' ((Get-Counts $DbA 'k8-b1-counts-mod') -eq '2/2')
    $rc = Invoke-Tool (@('restore', '--file', $bak, '--database', $DbA, '--confirm', $DbA, '--dir', $BakDir, '--move', 'existing', '--json') + $ToolArgsBase) 'k8-b1-restore'
    Check 'b1 tool exit code 0' ($rc -eq 0)
    Check 'b1 data came back (1/1)' ((Get-Counts $DbA 'k8-b1-counts-after') -eq '1/1')
    Check 'b1 physical files unchanged' ((Get-Files $DbA 'k8-b1-files-after') -eq $filesBefore)
    $names = @(Get-ChildItem -LiteralPath $Dir | ForEach-Object { $_.Name } | Sort-Object)
    Check 'b1 no orphan files in the database directory (3 files)' ($names.Count -eq 3)
    Check 'b1 database ONLINE/MULTI_USER' ((Get-Access $DbA 'k8-b1-access') -eq 'ONLINE/MULTI_USER')
    Check 'b1 safety backup made (pre-restore)' (@(Get-ChildItem -LiteralPath $BakDir -Filter "$DbA-pre-restore-*.bak").Count -ge 1)
  }

  Write-Host '== b2: --move new with --move-dir, then with the default paths'
  if ($bak) {
    Remove-OwnDb $DbA 'k8-b2-drop'
    Check 'b2 database dropped before new' (-not (Db-Exists $DbA))
    $rc = Invoke-Tool (@('restore', '--file', $bak, '--database', $DbA, '--confirm', $DbA, '--dir', $BakDir, '--move', 'new', '--move-dir', $DirC, '--json') + $ToolArgsBase) 'k8-b2-new-movedir'
    Check 'b2 tool exit code 0' ($rc -eq 0)
    Check 'b2 data present (1/1)' ((Get-Counts $DbA 'k8-b2-counts') -eq '1/1')
    $f = Get-Files $DbA 'k8-b2-files'
    Check 'b2 all files under the --move-dir' (($f -split ';').Count -eq 3 -and -not (($f -split ';') | Where-Object { $_ -notlike "*=$DirC\*" }))
    Check 'b2 database ONLINE/MULTI_USER' ((Get-Access $DbA 'k8-b2-access') -eq 'ONLINE/MULTI_USER')
    Remove-OwnDb $DbA 'k8-b2b-drop'
    $rc = Invoke-Tool (@('restore', '--file', $bak, '--database', $DbA, '--confirm', $DbA, '--dir', $BakDir, '--move', 'new', '--json') + $ToolArgsBase) 'k8-b2b-new-default'
    Check 'b2b tool exit code 0 (default paths) or 2 (refused by the whitelist)' (($rc -eq 0) -or ($rc -eq 2))
    $filesB2b = Get-Files $DbA 'k8-b2b-files'
    Write-Host ("OBSERVATION b2b default paths: tool rc={0}, database exists={1}, files={2}" -f $rc, (Db-Exists $DbA), $filesB2b)
    Remove-OwnDb $DbA 'k8-b2b-drop2'
    Check 'b2b database dropped again' (-not (Db-Exists $DbA))
    $left = @(Get-ProfileFiles $defDirs)
    Check 'b2b no skillsync_t6 files left in the default data/log directories after the DROP' ($left.Count -eq 0)
    if ($left.Count -gt 0) { Write-Host ("LEFTOVER FILES (listed only, not deleted): {0}" -f ($left -join '; ')) }
  }

  Write-Host '== b3: --move new failing in the engine (move-dir does not exist): exit code 1, no database'
  if ($bak) {
    $rc = Invoke-Tool (@('restore', '--file', $bak, '--database', $DbA, '--confirm', $DbA, '--dir', $BakDir, '--move', 'new', '--move-dir', "$DirC\nodir", '--json') + $ToolArgsBase) 'k8-b3-new-fail'
    $b3Log = Get-Content -LiteralPath $script:LastLogPath -Raw -Encoding UTF8
    Check 'b3 tool exit code 1 (RESTORE started and failed)' ($rc -eq 1)
    Write-Host ("OBSERVATION b3: database exists after the failure = {0}" -f (Db-Exists $DbA))
    $b3 = $b3Log
    Write-Host ("OBSERVATION b3 message variant: state-read={0}; DROP advice={1}; no-database-left={2}" -f ($b3 -match 'Database state read afterwards|state:'), ($b3 -match 'DROP DATABASE'), ($b3 -match 'No database was left'))
    Remove-OwnDb $DbA 'k8-b3-drop'
  }

  Write-Host '== b4: --move none on a backup whose file paths no longer exist (observe user_access_desc)'
  $rc = New-TestDb $DbB $DirB
  $rcb = Invoke-Tool (@('backup', '--database', $DbB, '--label', 'k8', '--dir', $BakDir, '--json') + $ToolArgsBase) 'k8-b4-backup'
  $bak2 = Get-Backup $DbB
  Check 'b4 setup (create, backup, file found)' (($rc -eq 0) -and ($rcb -eq 0) -and ($bak2 -ne $null))
  if ($bak2) {
    Remove-OwnDb $DbB 'k8-b4-drop'
    Remove-OwnSub $DirB
    $rc = New-TestDb $DbB $DirC
    Check 'b4 database recreated in another directory' ($rc -eq 0)
    $rc = Invoke-Tool (@('restore', '--file', $bak2, '--database', $DbB, '--confirm', $DbB, '--dir', $BakDir, '--json') + $ToolArgsBase) 'k8-b4-none-missing'
    Check 'b4 tool exit code 1' ($rc -eq 1)
    $script:ObserveOnly = $true
    Write-Host ("OBSERVATION b4: state/access after the failure = {0}" -f (Get-Access $DbB 'k8-b4-access'))
    Write-Host ("OBSERVATION b4: counts after the failure = {0}" -f (Get-Counts $DbB 'k8-b4-counts'))
    $script:ObserveOnly = $false
    Remove-OwnDb $DbB 'k8-b4-drop2'
  }

  Write-Host '== b5: --move none unchanged (same paths, data comes back)'
  $rc = New-TestDb $DbD $DirD
  $rcb = Invoke-Tool (@('backup', '--database', $DbD, '--label', 'k8', '--dir', $BakDir, '--json') + $ToolArgsBase) 'k8-b5-backup'
  $bak3 = Get-Backup $DbD
  Check 'b5 setup' (($rc -eq 0) -and ($rcb -eq 0) -and ($bak3 -ne $null))
  if ($bak3) {
    Add-Rows $DbD 'k8-b5-modify'
    $rc = Invoke-Tool (@('restore', '--file', $bak3, '--database', $DbD, '--confirm', $DbD, '--dir', $BakDir, '--json') + $ToolArgsBase) 'k8-b5-restore-none'
    Check 'b5 tool exit code 0' ($rc -eq 0)
    Check 'b5 data came back (1/1)' ((Get-Counts $DbD 'k8-b5-counts') -eq '1/1')
    Check 'b5 database ONLINE/MULTI_USER' ((Get-Access $DbD 'k8-b5-access') -eq 'ONLINE/MULTI_USER')
  }
  Write-Host "Logs: $LogDir (collect, then run -CleanupOnly -RemoveLog); checks run: $($script:Checks)"
}
finally {
  Write-Host '== CLEANUP'
  $script:ObserveOnly = $false
  $cleanOk = Invoke-Cleanup
  try {
    $dbsAfter = Get-AllDbs 'k8-9-dbs-after'
    Write-Host ("DBLIST-AFTER:  {0}" -f $dbsAfter)
    if ($dbsBefore -eq '') { Write-Host 'DBLIST-BEFORE missing (run aborted early): comparison skipped' } elseif ($dbsBefore -ne $dbsAfter) { Write-Host 'DBLIST DIFFERENT (see above)'; $script:Failures++ } else { Write-Host 'DBLIST IDENTICAL' }
    $leftP = @(Get-ProfileFiles $defDirs)
    if ($leftP.Count -gt 0) { Write-Host ("LEFTOVER FILES in default directories (listed only): {0}" -f ($leftP -join '; ')); $script:Failures++ }
  } catch { Write-Host ("final checks failed: {0}" -f $_.Exception.Message); $script:Failures++ }
}
Write-Host ("failures (failed checks + sqlcmd rc != 0 outside the checks): {0}" -f $script:Failures)
if ($cleanOk -and $script:Failures -eq 0) { exit 0 } else { exit 1 }
