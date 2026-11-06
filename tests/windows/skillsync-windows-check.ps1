#requires -Version 5.0
<#
.SYNOPSIS
    Skillsync - test srodowiska Windows (win-laptop / projekt). Tylko odczyt + katalog tymczasowy.
.DESCRIPTION
    Uruchom z korzenia sklonowanego repo Skillsync:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\windows\skillsync-windows-check.ps1
    Opcjonalnie sciezka do projektu projekt (tylko odczyt: sprawdza manifest, .gitignore, CRLF):
        ... -File .\tests\windows\skillsync-windows-check.ps1 -ProjectPath C:\Repos\projekt
    Skrypt NIE modyfikuje prawdziwej biblioteki ani zadnego projektu. Cala praca dzieje sie w %TEMP%
    na kopii biblioteki. Jedyny zapis poza %TEMP% to plik raportu (domyslnie %TEMP%\skillsync-windows-report.txt).
    Kazdy test drukuje: [PASS]/[FAIL]/[SKIP]/[INFO], oczekiwany wynik i wynik faktyczny.
    Odeslij caly raport (plik lub wklejony output).
#>
param(
    [string]$ProjectPath = "",
    [string]$ReportPath = (Join-Path ([System.IO.Path]::GetTempPath()) "skillsync-windows-report.txt")
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$Skillsync = Join-Path $RepoRoot "skillsync"
$UnitTests = Join-Path $RepoRoot "tests/test_skillsync.py"
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Fail = 0

function Out-Report([string]$Text) { Write-Host $Text; $script:Lines.Add($Text) }
function Check([string]$Name, [bool]$Ok, [string]$Expected, [string]$Got) {
    if ($Ok) { $tag = "[PASS]" } else { $tag = "[FAIL]"; $script:Fail++ }
    Out-Report ("{0} {1}`n        oczekiwano: {2}`n        jest:       {3}" -f $tag, $Name, $Expected, $Got)
}
function Info([string]$Name, [string]$Value) { Out-Report ("[INFO] {0}: {1}" -f $Name, $Value) }
function Skip([string]$Name, [string]$Why) { Out-Report ("[SKIP] {0} ({1})" -f $Name, $Why) }
function Has-CR([string]$Path) { return ([System.IO.File]::ReadAllBytes($Path) -contains 13) }
function Write-Lf([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    [System.IO.File]::WriteAllText($Path, ($Text -replace "`r`n", "`n"), $Utf8NoBom)
}

Out-Report "=== skillsync windows check  $(Get-Date -Format s) ==="

# ---------------------------------------------------------------- 0. srodowisko
Out-Report "`n--- 0. Srodowisko ---"
Info "OS" ([System.Environment]::OSVersion.VersionString)
Info "PowerShell" $PSVersionTable.PSVersion.ToString()
Info "USERPROFILE" $env:USERPROFILE
Info "repo root" $RepoRoot
$isWin = ($env:OS -eq "Windows_NT")
$devMode = $null
if ($isWin) {
    try { $devMode = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" -ErrorAction Stop).AllowDevelopmentWithoutDevLicense } catch { $devMode = "brak klucza" }
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Info "Developer Mode (AllowDevelopmentWithoutDevLicense)" "$devMode  (1 = wlaczony)"
    Info "Uruchomiono jako administrator" ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
}
$gitVer = & git --version 2>&1
Info "git" "$gitVer"
Info "git core.autocrlf" ("$(& git config --get core.autocrlf 2>&1)")
Check "skillsync istnieje w repo" (Test-Path -LiteralPath $Skillsync) "skrypt skillsync jest w korzeniu repo" "$Skillsync"

# ---------------------------------------------------------------- 1. Python
Out-Report "`n--- 1. Python ---"
$py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    try {
        $exe = $cand[0]; $extra = @($cand | Select-Object -Skip 1)
        $out = & $exe @extra -c "import sys; print(sys.version_info[0])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "3") { $py = $cand; break }
    } catch { }
}
if ($py) { $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1) } else { $pyExe = $null; $pyArgs = @() }
Check "Python 3 dostepny (py -3 / python / python3)" ($null -ne $py) "znaleziono interpreter 3.x" ("$($py -join ' ')")
if (-not $py) {
    Out-Report "`nBrak Pythona 3 => bootstrapper uzyje trybu legacy PowerShell (bez CLAUDE.md, .claude\*, push)."
    Out-Report "Zainstaluj Python 3 (python.org lub winget install Python.Python.3.12) i uruchom skrypt ponownie."
    $script:Lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    exit 2
}
Info "wersja Pythona" ("$(& $pyExe @pyArgs --version 2>&1)")
& $pyExe @pyArgs -c "import yaml" 2>$null
Check "PyYAML zainstalowany" ($LASTEXITCODE -eq 0) "import yaml dziala (jesli FAIL: $pyExe -m pip install --user pyyaml)" "exit=$LASTEXITCODE"
$hasYaml = ($LASTEXITCODE -eq 0)
Info "python_command do config.yaml" $pyExe

# ---------------------------------------------------------------- 2. symlinki
Out-Report "`n--- 2. Symlinki ---"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("skillsync-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$symTarget = Join-Path $work "sym-target"; $symLink = Join-Path $work "sym-link"
New-Item -ItemType Directory -Force -Path $symTarget | Out-Null
$psSymlinks = $false
try { New-Item -ItemType SymbolicLink -Path $symLink -Target $symTarget -ErrorAction Stop | Out-Null; $psSymlinks = $true } catch { $psSymErr = $_.Exception.Message }
if ($psSymlinks) { $got = "utworzono" } else { $got = "blad: $psSymErr" }
Check "PowerShell New-Item -ItemType SymbolicLink" $true "PASS gdy Developer Mode/admin; FAIL = brak uprawnien (to informacja, nie blad) => skillsync uzyje kopiowania" $got
Info "symlinki dostepne dla PowerShell" "$psSymlinks"
$pySymCode = "import os,sys; os.symlink(sys.argv[1], sys.argv[2], target_is_directory=True)"
$symLink2 = Join-Path $work "sym-link-py"
& $pyExe @pyArgs -c $pySymCode $symTarget $symLink2 2>$null
$pySymlinks = ($LASTEXITCODE -eq 0)
Info "symlinki dostepne dla Pythona (os.symlink)" "$pySymlinks  (musi byc zgodne z PowerShell)"
Check "spojnosc PowerShell vs Python co do symlinkow" ($psSymlinks -eq $pySymlinks) "obie wartosci takie same" "ps=$psSymlinks py=$pySymlinks"

# ---------------------------------------------------------------- 3. testy jednostkowe
Out-Report "`n--- 3. Testy jednostkowe (unittest) ---"
if ($hasYaml) {
    Push-Location $RepoRoot
    $ut = & $pyExe @pyArgs -m unittest "tests.test_skillsync" 2>&1 | Out-String
    $utExit = $LASTEXITCODE
    Pop-Location
    $tail = (($ut -split "`r?`n" | Where-Object { $_ -match '^(Ran \d+ tests|OK|FAILED|FAIL:|ERROR:)' }) -join " | ")
    Check "python -m unittest tests.test_skillsync" ($utExit -eq 0) "OK (pominięcia testów symlinków dopuszczalne)" $tail
    if ($utExit -ne 0) { Out-Report $ut }
} else { Skip "unittest" "brak PyYAML" }

# ---------------------------------------------------------------- 4. e2e na kopii biblioteki
Out-Report "`n--- 4. End-to-end (kopia biblioteki w %TEMP%, prawdziwa biblioteka nietknieta) ---"
$realLib = Join-Path $env:USERPROFILE ".local/copilot-library"
Check "prawdziwa biblioteka istnieje" (Test-Path -LiteralPath $realLib) "$realLib jest (klon repo CopilotSkills)" "$(Test-Path -LiteralPath $realLib)"
$lib = Join-Path $work "library"
foreach ($d in "agents", "hooks", "skills/feature", "instructions") { New-Item -ItemType Directory -Force -Path (Join-Path $lib $d) | Out-Null }
$realHook = Join-Path $realLib "hooks/check-tests.py"
$realAgent = Join-Path $realLib "agents/implementer.md"
$realSkill = Join-Path $realLib "skills/feature/SKILL.md"
$haveNew = (Test-Path -LiteralPath $realHook) -and (Test-Path -LiteralPath $realAgent) -and (Test-Path -LiteralPath $realSkill)
Check "biblioteka zawiera nowe pliki (agents/, hooks/, skills/feature)" $haveNew `
    "TAK - jesli FAIL: zmiany z Linuksa nie sa jeszcze skomitowane/wypchniete do CopilotSkills (albo brak git pull)" "$haveNew"
if ($haveNew) {
    Copy-Item -LiteralPath $realHook -Destination (Join-Path $lib "hooks/check-tests.py")
    Copy-Item -LiteralPath $realAgent -Destination (Join-Path $lib "agents/implementer.md")
    Copy-Item -LiteralPath $realSkill -Destination (Join-Path $lib "skills/feature/SKILL.md")
} else {
    Write-Lf (Join-Path $lib "agents/implementer.md") "---`nname: implementer`n---`n"
    Write-Lf (Join-Path $lib "skills/feature/SKILL.md") "---`nname: feature`n---`nv1`n"
    Skip "kopiowanie prawdziwych plikow" "uzywam atrap; test hooka bedzie pominiety"
}
Write-Lf (Join-Path $lib "instructions/_base.md") "## Base`nzasady`n<!-- SKILLSYNC:BASE-END -->`n"
Write-Lf (Join-Path $lib "instructions/proj.md") "## Projekt`nlokalne`n"

$proj = Join-Path $work "proj"
New-Item -ItemType Directory -Force -Path (Join-Path $proj ".github"), (Join-Path $proj ".claude") | Out-Null
$testCmd = "$pyExe -c `"import sys; sys.exit(1)`""
Write-Lf (Join-Path $proj ".github/.skillsync.yaml") @"
version: 1
skills: [feature]
codex: false
instructions:
  merge: [_base]
  template: proj
sync: {method: symlink}
agents: [implementer]
hooks: {stop_tests: true}
tests:
  command: '$testCmd'
"@
# istniejacy settings.json Z CRLF i uprawnieniami - musza przetrwac
[System.IO.File]::WriteAllText((Join-Path $proj ".claude/settings.json"), "{`r`n  `"permissions`": {`r`n    `"allow`": [`"Bash(git fetch *)`"]`r`n  }`r`n}`r`n", $Utf8NoBom)
Push-Location $proj; & git init -q 2>&1 | Out-Null; & git config user.email t@t; & git config user.name t; Pop-Location

$cfgFile = Join-Path $work "config.yaml"
$libFwd = $lib -replace '\\', '/'
# Config bez klucza biblioteki (D41); warstwy dopisuje `layers migrate --adopt-legacy` (most K15, tylko odczyt).
Write-Lf $cfgFile "discover_paths: []`ntags: {}`nvariables: {}`nremotes: {}`npython_command: $pyExe`n"
$env:SKILLSYNC_CONFIG = $cfgFile
Remove-Item Env:\SKILLSYNC_NO_SYMLINK -ErrorAction SilentlyContinue

function Run-Skillsync([string[]]$ArgList) {
    $o = & $pyExe @pyArgs $Skillsync @ArgList 2>&1 | Out-String
    return @{ Out = $o.Trim(); Code = $LASTEXITCODE }
}
# Linie wyjscia poza ostrzezeniem mostu (`[warn] layers: ... legacy-bridge ...`), ktore wypisuje kazdy link.
function Out-WithoutBridge([string]$Text) {
    return ((($Text -split "`r?`n") | Where-Object { $_ -and ($_ -notmatch '^\[warn\] layers: .*legacy-bridge') }) -join "`n").Trim()
}

$cfgBefore = [System.IO.File]::ReadAllText($cfgFile)
$rm0 = Run-Skillsync @("layers", "migrate", "--adopt-legacy", $libFwd)
Check "layers migrate --adopt-legacy (dry-run) nic nie zapisuje" (($rm0.Code -eq 0) -and ([System.IO.File]::ReadAllText($cfgFile) -eq $cfgBefore)) "exit 0, config bez zmian" "exit=$($rm0.Code)"
$rm = Run-Skillsync @("layers", "migrate", "--adopt-legacy", $libFwd, "--apply")
$cfgAfter = [System.IO.File]::ReadAllText($cfgFile)
Check "layers migrate --adopt-legacy --apply dopisuje warstwe mostu" (($rm.Code -eq 0) -and ($cfgAfter -match '(?m)^layers:') -and ($cfgAfter -match 'synthetic:') -and ($cfgAfter -match '(?m)^layer_rules:')) "exit 0; layers z 'synthetic', layer_rules" "exit=$($rm.Code) $($rm.Out -split "`n" | Select-Object -First 1)"
$rm2 = Run-Skillsync @("layers", "migrate", "--adopt-legacy", $libFwd, "--apply")
Check "layers migrate jest idempotentne" (($rm2.Code -eq 0) -and ([System.IO.File]::ReadAllText($cfgFile) -eq $cfgAfter)) "exit 0, config bez zmian" "exit=$($rm2.Code)"
$rc = Run-Skillsync @("layers", "check")
Check "layers check z mostem: PASS z ostrzezeniem legacy-bridge" (($rc.Code -eq 0) -and ($rc.Out -match "legacy-bridge")) "exit 0, 'legacy-bridge' w outpucie" "exit=$($rc.Code)"

$claudeSkill = Join-Path $proj ".claude/skills/feature"
$stateFile = Join-Path $proj ".github/.skillsync-state.json"

$r = Run-Skillsync @("link", $proj)
Check "skillsync link (tryb domyslny)" ($r.Code -eq 0) "exit 0" "exit=$($r.Code) $($r.Out)"
$item = Get-Item -LiteralPath $claudeSkill -ErrorAction SilentlyContinue
$isLink = ($null -ne $item) -and ($item.LinkType -eq "SymbolicLink")
Check ".claude\skills\feature istnieje" ($null -ne $item) "katalog/symlink jest" "$($null -ne $item)"
if ($pySymlinks) { $expectMode = "symlink (Developer Mode wlaczony)" } else { $expectMode = "KOPIA + plik stanu (brak symlinkow) i ostrzezenie 'Symlinks unavailable'" }
$modeOk = ($isLink -eq $pySymlinks)
if ($isLink) { $gotMode = "symlink" } else { $gotMode = "kopia" }
Check "tryb umieszczenia zgodny z mozliwosciami systemu" $modeOk $expectMode "$gotMode; state-file=$(Test-Path -LiteralPath $stateFile)"
if (-not $pySymlinks) {
    Check "ostrzezenie o braku symlinkow wypisane" ($r.Out -match "Symlinks unavailable") "tekst 'Symlinks unavailable' w outpucie" ($r.Out -split "`n" | Select-Object -First 1)
    Check "plik stanu istnieje i jest w .github\.gitignore" ((Test-Path -LiteralPath $stateFile) -and ((Get-Content -LiteralPath (Join-Path $proj ".github/.gitignore")) -contains ".skillsync-state.json")) "oba TAK" "$(Test-Path -LiteralPath $stateFile)"
}

$r2 = Run-Skillsync @("link", $proj)
$r2Rest = Out-WithoutBridge $r2.Out
Check "drugi link jest cichy (idempotencja)" (($r2.Code -eq 0) -and ($r2Rest -eq "")) "exit 0, poza ostrzezeniem mostu (legacy-bridge) pusty output" "exit=$($r2.Code) out='$r2Rest'"

# separatory, CRLF
$genFiles = @(".github/.gitignore", ".claude/.gitignore", "CLAUDE.md", ".github/copilot-instructions.md")
foreach ($f in $genFiles) {
    $full = Join-Path $proj $f
    if (Test-Path -LiteralPath $full) { Check "brak CR (LF-only) w $f" (-not (Has-CR $full)) "zero bajtow 0x0D" "CR=$(Has-CR $full)" } else { Check "plik $f istnieje" $false "jest" "brak" }
}
$giClaude = Get-Content -LiteralPath (Join-Path $proj ".claude/.gitignore") -ErrorAction SilentlyContinue
Check ".claude\.gitignore: wpisy z '/' (nie '\') i per-plik" (($giClaude -contains "skills/feature") -and ($giClaude -contains "agents/implementer.md") -and -not ($giClaude -match '\\')) "skills/feature, agents/implementer.md, bez backslashy" ($giClaude -join ", ")
Check "CLAUDE.md zawiera naglowek i tresc (scalenie _base + proj)" ((Get-Content -LiteralPath (Join-Path $proj "CLAUDE.md") -Raw) -match "(?s)# CLAUDE.md.*## Base.*## Projekt") "obie sekcje po naglowku" "ok?"

# settings.json: scalanie
$sj = Get-Content -LiteralPath (Join-Path $proj ".claude/settings.json") -Raw | ConvertFrom-Json
$allow = @($sj.permissions.allow)
Check "settings.json: istniejace 'permissions' zachowane" ($allow -contains "Bash(git fetch *)") "Bash(git fetch *) nadal jest" ($allow -join ", ")
Check "feature nie dodaje zdublowanego testowego hooka Stop" (($sj.hooks.Stop | ConvertTo-Json -Depth 8) -notmatch 'check-tests.py') "brak testowego hooka" "sprawdzono settings.json"
Check "settings.local.json nietkniety (nie istnieje)" (-not (Test-Path -LiteralPath (Join-Path $proj ".claude/settings.local.json"))) "brak pliku (nic go nie tworzy)" "ok"

# Sam skrypt hooka: osobny test legacy, poza workflow feature.
function Invoke-Hook($HookCmd, [string]$StdinJson, [string]$Cwd) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $HookCmd.command
    $psi.Arguments = (@($HookCmd.args) | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardError = $true; $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute = $false; $psi.WorkingDirectory = $Cwd
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Write($StdinJson); $p.StandardInput.Close()
    $err = $p.StandardError.ReadToEnd(); [void]$p.StandardOutput.ReadToEnd(); $p.WaitForExit()
    return @{ Code = $p.ExitCode; Err = $err }
}
if ($haveNew) {
    New-Item -ItemType Directory -Force -Path (Join-Path $proj ".claude/hooks") | Out-Null
    Copy-Item -LiteralPath (Join-Path $lib "hooks/check-tests.py") -Destination (Join-Path $proj ".claude/hooks/check-tests.py")
    $hookCmd = @{ command = $pyExe; args = @(".claude/hooks/check-tests.py", $testCmd) }
    "cos" | Set-Content -LiteralPath (Join-Path $proj "zmiana.txt")
    $h1 = Invoke-Hook $hookCmd (@{ cwd = $proj; stop_hook_active = $false } | ConvertTo-Json -Compress) $proj
    Check "hook Stop: porazka testow => exit 2 + stderr" ($h1.Code -eq 2) "exit 2, stderr zaczyna sie od 'Testy nie przechodza'" "exit=$($h1.Code) stderr='$(($h1.Err.Trim() -split "`n")[0])'"
    $h2 = Invoke-Hook $hookCmd (@{ cwd = $proj; stop_hook_active = $true } | ConvertTo-Json -Compress) $proj
    Check "hook Stop: stop_hook_active=true => exit 0 (ochrona przed petla)" ($h2.Code -eq 0) "exit 0" "exit=$($h2.Code)"
} else { Skip "test hooka Stop" "brak prawdziwego check-tests.py w bibliotece" }

# tryb wymuszonej kopii + wykrywanie zmian + odmowa push do mostu (na kopii biblioteki)
Out-Report "`n--- 5. Fallback kopiowania: wykrywanie zmian i push do mostu tylko do odczytu (SKILLSYNC_NO_SYMLINK=1) ---"
$proj2 = Join-Path $work "proj2"
New-Item -ItemType Directory -Force -Path (Join-Path $proj2 ".github") | Out-Null
Copy-Item -LiteralPath (Join-Path $proj ".github/.skillsync.yaml") -Destination (Join-Path $proj2 ".github/.skillsync.yaml")
$env:SKILLSYNC_NO_SYMLINK = "1"
$null = Run-Skillsync @("link", $proj2)
$copyDir = Join-Path $proj2 ".claude/skills/feature"
$ci = Get-Item -LiteralPath $copyDir -ErrorAction SilentlyContinue
if ($ci -and ($ci.LinkType -eq "SymbolicLink")) { $ciKind = "symlink" } elseif ($ci) { $ciKind = "zwykly katalog (kopia)" } else { $ciKind = "brak" }
Check "wymuszony fallback: skill jest kopia (nie symlink)" (($null -ne $ci) -and ($ci.LinkType -ne "SymbolicLink")) "zwykly katalog (kopia)" $ciKind
Add-Content -LiteralPath (Join-Path $copyDir "SKILL.md") -Value "edycja lokalna (Windows)"
$rl = Run-Skillsync @("link", $proj2)
Check "link po lokalnej edycji NIE nadpisuje jej" ((Get-Content -LiteralPath (Join-Path $copyDir "SKILL.md") -Raw) -match "edycja lokalna") "edycja nadal w pliku + '[info] Local edits ... not pushed yet'" (($rl.Out -split "`n" | Where-Object { $_ -match "Local edits" }) -join "")
$libSkillBefore = [System.IO.File]::ReadAllText((Join-Path $lib "skills/feature/SKILL.md"))
$rp = Run-Skillsync @("push", $proj2)
$libSkill = [System.IO.File]::ReadAllText((Join-Path $lib "skills/feature/SKILL.md"))
Check "push do mostu: BLOCKED (exit 2), biblioteka bez zmian (D63)" (($rp.Code -eq 2) -and ($libSkill -eq $libSkillBefore) -and -not ($libSkill -match "edycja lokalna")) "exit 2, komunikat 'most tylko do odczytu', plik biblioteki nietkniety" "exit=$($rp.Code) $(($rp.Out -split "`n" | Where-Object { $_ -match 'read-only|tylko do odczytu' } | Select-Object -First 1) -join '')"
Check "edycja lokalna nadal w kopii po odmowie push" ((Get-Content -LiteralPath (Join-Path $copyDir "SKILL.md") -Raw) -match "edycja lokalna") "edycja jest w .claude\skills\feature\SKILL.md" "ok?"
Remove-Item Env:\SKILLSYNC_NO_SYMLINK -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 5b. odmowa bootstrapu na configu bez layers
Out-Report "`n--- 5b. skillsync-windows.ps1 odmawia pracy na configu bez 'layers:' (D41) ---"
# Izolacja: osobny USERPROFILE z lokalna atrapa starej biblioteki (git bez remote: pull sie nie uda, bez klonowania
# z sieci), osobny config i projekt w %TEMP%. Prawdziwy config i biblioteka nietkniete.
$bootHome = Join-Path $work "boot-home"
$bootLib = Join-Path $bootHome ".local/copilot-library"
New-Item -ItemType Directory -Force -Path (Join-Path $bootLib "skills/feature") | Out-Null
Write-Lf (Join-Path $bootLib "skills/feature/SKILL.md") "---`nname: feature`n---`n"
Push-Location $bootLib; & git init -q 2>&1 | Out-Null; Pop-Location
$bootProj = Join-Path $work "boot-proj"
Write-Lf (Join-Path $bootProj ".github/.skillsync.yaml") "version: 1`nskills: [feature]`ninstructions:`n  merge: [_base]`n"
$bootCfg = Join-Path $work "boot-config.yaml"
$bootCfgText = "# stary format bez layers`ndiscover_paths: []`n"
Write-Lf $bootCfg $bootCfgText
$bootScript = Join-Path $RepoRoot "skillsync-windows.ps1"
$psExe = (Get-Process -Id $PID).Path
$prevProfile = $env:USERPROFILE; $prevCfg = $env:SKILLSYNC_CONFIG
$env:USERPROFILE = $bootHome; $env:SKILLSYNC_CONFIG = $bootCfg
try {
    $bootOut = & $psExe -NoProfile -ExecutionPolicy Bypass -File $bootScript $bootProj 2>&1 | Out-String
    $bootCode = $LASTEXITCODE
} finally {
    $env:USERPROFILE = $prevProfile; $env:SKILLSYNC_CONFIG = $prevCfg
}
$bootCfgAfter = [System.IO.File]::ReadAllText($bootCfg)
Check "bootstrap: config bez 'layers:' => exit 2, config bez zmian, instrukcja 'layers migrate'" (($bootCode -eq 2) -and ($bootCfgAfter -eq $bootCfgText) -and ($bootOut -match "layers migrate --adopt-legacy")) "exit 2; config nietkniety; komenda migracji w outpucie" "exit=$bootCode config-zmieniony=$($bootCfgAfter -ne $bootCfgText)"
Check "bootstrap: odmowa nie linkuje projektu" (-not (Test-Path -LiteralPath (Join-Path $bootProj ".claude"))) "brak .claude w projekcie" "$(Test-Path -LiteralPath (Join-Path $bootProj '.claude'))"

# ---------------------------------------------------------------- 6. rozwijanie ~ w config
Out-Report "`n--- 6. Rozwijanie ~ w config.yaml ---"
$cfgTilde = Join-Path $work "config-tilde.yaml"
if ($lib.StartsWith($env:USERPROFILE, [System.StringComparison]::OrdinalIgnoreCase)) {
    $relLib = $lib.Substring($env:USERPROFILE.Length).TrimStart('\', '/') -replace '\\', '/'
    Write-Lf $cfgTilde "discover_paths: []`n"
    $env:SKILLSYNC_CONFIG = $cfgTilde
    $null = Run-Skillsync @("layers", "migrate", "--adopt-legacy", "~/$relLib", "--apply")
    $rt = Run-Skillsync @("list")
    Check "sciezka warstwy ~/... rozwijana przez skillsync" (($rt.Code -eq 0) -and ($rt.Out -match "feature")) "'skillsync list' widzi skill feature" ($rt.Out -split "`n" | Select-Object -First 2 | Out-String).Trim()
    $env:SKILLSYNC_CONFIG = $cfgFile
} else { Skip "rozwijanie ~" "katalog tymczasowy poza USERPROFILE ($work)" }

# ---------------------------------------------------------------- 7. synchronizacja biblioteki
Out-Report "`n--- 7. Skad Windows bierze biblioteke / ryzyko konfliktow push ---"
if (Test-Path -LiteralPath (Join-Path $realLib ".git")) {
    Info "remote origin" ("$(& git -C $realLib remote get-url origin 2>&1)")
    Check "origin to ten sam repo co na Linuksie" ((& git -C $realLib remote get-url origin 2>&1) -match "BATUSystems/skillsync") "https://github.com/BATUSystems/skillsync.git" ("$(& git -C $realLib remote get-url origin 2>&1)")
    Info "galaz / ahead-behind (bez fetch)" ("$(& git -C $realLib status -sb 2>&1 | Select-Object -First 1)")
    $dirty = @(& git -C $realLib status --porcelain 2>&1)
    Check "biblioteka bez niezacommitowanych zmian (inaczej push/pull sie zderza)" ($dirty.Count -eq 0) "0 zmienionych plikow" "$($dirty.Count) zmian: $(($dirty | Select-Object -First 5) -join '; ')"
    Info "ostatni commit" ("$(& git -C $realLib log -1 --format='%h %ad %s' --date=short 2>&1)")
    Info "core.autocrlf w bibliotece" ("$(& git -C $realLib config --get core.autocrlf 2>&1)")
    $hookRaw = Join-Path $realLib "hooks/check-tests.py"
    if (Test-Path -LiteralPath $hookRaw) { Info "check-tests.py w bibliotece ma CR (autocrlf)?" "$(Has-CR $hookRaw)  (Python znosi CRLF; tylko informacja)" }
} else { Skip "sprawdzenie repo biblioteki" "$realLib nie jest klonem git" }

# ---------------------------------------------------------------- 8. projekt (tylko odczyt)
if ($ProjectPath) {
    Out-Report "`n--- 8. projekt: $ProjectPath (tylko odczyt) ---"
    $nm = Join-Path $ProjectPath ".github/.skillsync.yaml"
    Check "projekt ma manifest .github\.skillsync.yaml" (Test-Path -LiteralPath $nm) "plik istnieje" "$(Test-Path -LiteralPath $nm)"
    if (Test-Path -LiteralPath $nm) {
        Out-Report "----- manifest (pierwsze 40 linii) -----"
        Get-Content -LiteralPath $nm -TotalCount 40 | ForEach-Object { Out-Report "    $_" }
        Info "manifest ma CR (CRLF)" "$(Has-CR $nm)  (PyYAML znosi CRLF; tylko informacja)"
        Info "manifest ma sekcje agents/hooks/tests" "$((Select-String -LiteralPath $nm -Pattern '^(agents|hooks|tests):' | ForEach-Object { $_.Line }) -join ', ')"
    }
    foreach ($f in ".github/.gitignore", ".gitignore", ".claude/settings.json") {
        $ff = Join-Path $ProjectPath $f
        if (Test-Path -LiteralPath $ff) { Info "$f" "istnieje, CR=$(Has-CR $ff)" } else { Info "$f" "brak" }
    }
    $sk = Join-Path $ProjectPath ".github/skills"
    if (Test-Path -LiteralPath $sk) {
        $items = @(Get-ChildItem -LiteralPath $sk -Force)
        $links = @($items | Where-Object { $_.LinkType -eq "SymbolicLink" }).Count
        Info ".github\skills" "$($items.Count) pozycji, w tym symlinkow: $links (0 symlinkow przy >0 pozycji = kopie => potrzebny stan/push)"
    }
    Push-Location $ProjectPath; Info "projekt git status --short (pierwsze 10)" (((& git status --short 2>&1) | Select-Object -First 10) -join "; "); Pop-Location
}

# ---------------------------------------------------------------- podsumowanie
Remove-Item Env:\SKILLSYNC_CONFIG -ErrorAction SilentlyContinue
Remove-Item Env:\SKILLSYNC_NO_SYMLINK -ErrorAction SilentlyContinue
Out-Report "`n=== PODSUMOWANIE: FAIL=$($script:Fail) ==="
Out-Report "Katalog roboczy (mozna usunac): $work"
$script:Lines | Set-Content -LiteralPath $ReportPath -Encoding UTF8
Write-Host "Raport zapisany: $ReportPath"
if ($script:Fail -gt 0) { exit 1 } else { exit 0 }
