#requires -Version 5.0
<#
.SYNOPSIS
    Skillsync synchronization dla Windows
.DESCRIPTION
    Synchronizuje skills ze wspólnej biblioteki do lokalnych projektów
#>

param(
    [Parameter(Position=0)]
    [string]$ProjectPath = "."
)

$ErrorActionPreference = "Stop"
$ConfirmPreference = "None"

# Remote-SSH z Linuxa podaje ścieżki w stylu /C:/Repos/... — normalizuj do Windows
if ($ProjectPath -match '^/[A-Za-z]:/') {
    $ProjectPath = ($ProjectPath.TrimStart('/') -replace '/', '\')
}

# Config
$GITHUB_DIR = (Resolve-Path -LiteralPath $ProjectPath).Path | Join-Path -ChildPath ".github"
$SKILLS_DIR = Join-Path $GITHUB_DIR "skills"
$MANIFEST = Join-Path $GITHUB_DIR ".skillsync.yaml"
$INSTRUCTIONS = Join-Path $GITHUB_DIR "copilot-instructions.md"
$GIT_URL = "https://github.com/BATUSystems/skillsync.git"

# Biblioteka (klon CopilotSkills) w stalym miejscu. Ustawienie biblioteki w manifescie nie jest
# czytane (spec A, K3); config rejestruje ja jako warstwe core przez
# `layers migrate --layer-source core=<biblioteka>` (warstwa wymaga layer.yaml, inaczej link = BLOCKED).
$LIBRARY_PATH = "$env:USERPROFILE\.local\copilot-library"

# Czytaj listę merge z manifestu; fallback do _base
$MERGE_LIST = @()
if (Test-Path $MANIFEST) {
    $yaml_content = Get-Content $MANIFEST -Raw
    $in_instructions = $false
    $in_merge = $false
    foreach ($line in (Get-Content $MANIFEST)) {
        # Wykryj sekcję instructions: (bez wcięcia)
        if ($line -match '^instructions:') { $in_instructions = $true; $in_merge = $false; continue }
        # Wyjście z instructions gdy pojawi się inna sekcja bez wcięcia
        if ($in_instructions -and $line -match '^\S' -and $line -notmatch '^instructions:') { $in_instructions = $false; $in_merge = $false }
        # Wykryj merge: tylko wewnątrz instructions:
        if ($in_instructions -and $line -match '^\s+merge:') { $in_merge = $true; continue }
        # Wyjście z merge gdy pojawi się inny klucz o tym samym wcięciu
        if ($in_merge -and $line -match '^\s{2,}\S' -and $line -notmatch '^\s+-') { $in_merge = $false }
        # Zbieraj wpisy merge
        if ($in_merge -and $line -match '^\s+-\s+(.+)') { $MERGE_LIST += $matches[1].Trim() }
    }
}

if ($MERGE_LIST.Count -eq 0) {
    $MERGE_LIST = @("_base")
}

Write-Host ""
Write-Host "========================================"
Write-Host "Skillsync Windows Sync"
Write-Host "========================================"
Write-Host ""

# === STEP 1: Initialize Library ===
Write-Host "Step 1/5 Library setup..." -ForegroundColor Cyan

# Uruchamia gita bez przerywania skryptu: PS 5.1 przy ErrorActionPreference=Stop zamienia stderr
# programu natywnego w wyjatek, wiec kod wyjscia i komunikat gina. Zwraca kod i wyjscie jako tekst.
function Invoke-LibraryGit {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = @(& git @GitArgs 2>&1 | ForEach-Object { "$_" })
        return @{ Code = $LASTEXITCODE; Output = $out }
    } catch {
        return @{ Code = 1; Output = @("$_") }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Nieudany pull nie przerywa synchronizacji (praca offline), ale wynik koncowy nie moze byc SUCCESS:
# projekt dostaje wtedy artefakty ze starej biblioteki.
$LIBRARY_STALE = $false
$EXIT_LIBRARY_STALE = 3

function Write-LibraryStaleWarning {
    Write-Host ""
    Write-Host "[WARN] Biblioteka NIE zostala zaktualizowana (git pull nie powiodl sie)." -ForegroundColor Yellow
    Write-Host "       Projekt zsynchronizowano ze STAREJ wersji: $LIBRARY_PATH" -ForegroundColor Yellow
    Write-Host "       Sprawdz: git -C `"$LIBRARY_PATH`" status   (typowo: niezacommitowane zmiany albo brak sieci)" -ForegroundColor Yellow
    Write-Host ""
}

if (Test-Path $LIBRARY_PATH) {
    Write-Host "  Library exists, pulling updates..."
    $pull = Invoke-LibraryGit @("-C", $LIBRARY_PATH, "pull", "origin", "master")
    if ($pull.Code -ne 0) {
        $LIBRARY_STALE = $true
        Write-Host "  [WARN] git pull failed (exit $($pull.Code)) - biblioteka pozostaje w starej wersji:" -ForegroundColor Yellow
        foreach ($l in $pull.Output) { Write-Host "    $l" -ForegroundColor Yellow }
    }
} else {
    Write-Host "  Cloning library..."
    $parent = Split-Path $LIBRARY_PATH
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $clone = Invoke-LibraryGit @("clone", $GIT_URL, $LIBRARY_PATH)
    if ($clone.Code -ne 0) {
        foreach ($l in $clone.Output) { Write-Host "    $l" -ForegroundColor Red }
        Write-Error "git clone biblioteki nie powiodl sie (exit $($clone.Code)): $GIT_URL"
        exit 1
    }
}

if ($LIBRARY_STALE) {
    Write-Host "  STALE: $LIBRARY_PATH" -ForegroundColor Yellow
} else {
    Write-Host "  OK: $LIBRARY_PATH"
}

# === STEP 2: Create Manifest ===
Write-Host "Step 2/5 Manifest..." -ForegroundColor Cyan

if (Test-Path $MANIFEST) {
    Write-Host "  Manifest exists"
} else {
    Write-Host "  Creating manifest..."
    $skills_list = Get-ChildItem "$LIBRARY_PATH/skills" -Directory | Select-Object -ExpandProperty Name
    $skills_yaml = ($skills_list | ForEach-Object { "  - $_" }) -join "`n"
    
    $manifest_content = @"
version: 1

skills:
$skills_yaml

instructions:
  merge:
    - _base
    - projekt-instrukcje

sync:
  method: symlink
"@
    
    New-Item -ItemType Directory -Path $GITHUB_DIR -Force | Out-Null
    Set-Content -Path $MANIFEST -Value $manifest_content -Encoding UTF8
    Write-Host "  Created: $MANIFEST"
}

# === STEP 2b: Preferuj wspolna implementacje Python (pelna zgodnosc funkcji z Linuksem) ===
# skillsync (Python, obok tego skryptu w korzeniu repo Skillsync) obsluguje: CLAUDE.md, .claude\agents|skills|hooks, scalanie settings.json,
# (dziala przez wrapper do v1.1.0) push, --dry-run oraz fallback symlink -> kopiowanie. Legacy kroki 3-5 ponizej tego NIE potrafia.
function Find-Python {
    foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
        try {
            $exe = $cand[0]
            $extra = @($cand | Select-Object -Skip 1)
            $out = & $exe @extra -c "import sys; print(sys.version_info[0])" 2>$null
            if ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "3") { return ,$cand }
        } catch { }
    }
    return $null
}

$SKILLSYNC_PY = Join-Path $PSScriptRoot "skillsync"
$PY = Find-Python
if ($PY -and (Test-Path -LiteralPath $SKILLSYNC_PY)) {
    Write-Host "Step 2b/5 Python skillsync ($($PY -join ' '))..." -ForegroundColor Cyan
    $pyExe = $PY[0]
    $pyArgs = @($PY | Select-Object -Skip 1)

    & $pyExe @pyArgs -c "import yaml" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Instaluje pyyaml..."
        & $pyExe @pyArgs -m pip install --user pyyaml
        if ($LASTEXITCODE -ne 0) { Write-Error "pip install pyyaml nie powiodl sie"; exit 1 }
    }

    $cfgFile = $env:SKILLSYNC_CONFIG
    if (-not $cfgFile) { $cfgFile = Join-Path $env:USERPROFILE ".config\skillsync\config.yaml" }
    $cfgDir = Split-Path $cfgFile -Parent
    $libFwd = $LIBRARY_PATH -replace '\\', '/'
    if (-not (Test-Path -LiteralPath $cfgFile)) {
        # Nowy config bez klucza biblioteki (D41): warstwy dopisuje `skillsync layers migrate`.
        New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
        $projectRoot = (Split-Path $GITHUB_DIR -Parent) -replace '\\', '/'
        $homeFwd = $env:USERPROFILE -replace '\\', '/'
        $cfgContent = @"
discover_paths:
  - "$projectRoot"
tags: {}
variables:
  username: $env:USERNAME
  home: "$homeFwd"
  os: Windows
remotes: {}
python_command: $pyExe
"@
        [System.IO.File]::WriteAllText($cfgFile, ($cfgContent -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))
        Write-Host "  Utworzono config: $cfgFile"
        # Biblioteka jako warstwa core (layers, layer_rules `**`, isolation, cel straznika core).
        & $pyExe @pyArgs $SKILLSYNC_PY layers migrate --layer-source "core=$libFwd" --apply
        if ($LASTEXITCODE -ne 0) { Write-Error "skillsync layers migrate nie powiodl sie (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
    } elseif (-not (Select-String -LiteralPath $cfgFile -Pattern '^layers:' -Quiet)) {
        # Config bez `layers` (stary klucz biblioteki albo pusty): skrypt go nie przepisuje sam.
        Write-Host "  [BLOCKED] $cfgFile nie ma 'layers:' (stary format configu, D41). Skrypt nie przepisuje go sam." -ForegroundColor Red
        Write-Host "  Podglad migracji (nic nie zapisuje):" -ForegroundColor Yellow
        Write-Host "    $($PY -join ' ') `"$SKILLSYNC_PY`" layers migrate --layer-source `"core=$libFwd`"" -ForegroundColor Yellow
        Write-Host "  Zapis (z kopia zapasowa configu):" -ForegroundColor Yellow
        Write-Host "    $($PY -join ' ') `"$SKILLSYNC_PY`" layers migrate --layer-source `"core=$libFwd`" --apply" -ForegroundColor Yellow
        Write-Host "  (gdy stara biblioteka z klucza configu ma layer.yaml, --layer-source mozna pominac)" -ForegroundColor Yellow
        exit 2
    }
    if (-not (Select-String -LiteralPath $cfgFile -Pattern '^python_command:' -Quiet)) {
        Write-Host "  [WARN] $cfgFile nie ma 'python_command:' - hook Stop uzyje 'python3', ktorego moze tu nie byc. Dopisz: python_command: $pyExe" -ForegroundColor Yellow
    }

    & $pyExe @pyArgs $SKILLSYNC_PY link (Split-Path $GITHUB_DIR -Parent)
    if ($LASTEXITCODE -ne 0) { Write-Error "skillsync (Python) zakonczyl sie bledem $LASTEXITCODE"; exit $LASTEXITCODE }
    if ($LIBRARY_STALE) { Write-LibraryStaleWarning; exit $EXIT_LIBRARY_STALE }
    Write-Host ""
    Write-Host "SUCCESS: Synchronization complete (Python skillsync)!" -ForegroundColor Green
    exit 0
}
Write-Host "[WARN] Python 3 lub skrypt skillsync (korzen repo Skillsync) niedostepny - tryb legacy (bez CLAUDE.md, .claude\*, push)." -ForegroundColor Yellow

# === STEP 3: Git Ignore ===
Write-Host "Step 3/5 Gitignore..." -ForegroundColor Cyan

$gitignore = Join-Path $GITHUB_DIR ".gitignore"
$ignore_entries = @("copilot-instructions.md", "skills/", ".skillsync.yaml")

$gitignore_content = ""
if (Test-Path $gitignore) {
    $gitignore_content = Get-Content $gitignore -Raw
}

foreach ($entry in $ignore_entries) {
    if (-not ($gitignore_content -match [regex]::Escape($entry))) {
        $gitignore_content += "`n$entry`n"
    }
}

Set-Content -Path $gitignore -Value $gitignore_content -Encoding UTF8
Write-Host "  Updated: $gitignore"

# === STEP 4: Link Skills ===
Write-Host "Step 4/5 Skills linking..." -ForegroundColor Cyan

New-Item -ItemType Directory -Path $SKILLS_DIR -Force | Out-Null

$lib_skills = "$LIBRARY_PATH/skills"
Get-ChildItem $lib_skills -Directory | ForEach-Object {
    $skill_name = $_.Name
    $skill_src = $_.FullName
    $skill_dst = Join-Path $SKILLS_DIR $skill_name
    
    if (Test-Path $skill_dst) {
        Remove-Item $skill_dst -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    try {
        New-Item -ItemType SymbolicLink -Path $skill_dst -Target $skill_src -Force -ErrorAction Stop | Out-Null
        Write-Host "  [OK] $skill_name (symlink)"
    } catch {
        New-Item -ItemType Directory -Path $skill_dst -Force | Out-Null
        Copy-Item -Path "$skill_src/*" -Destination $skill_dst -Recurse -Force -ErrorAction Stop | Out-Null
        Write-Host "  [OK] $skill_name (copied)"
    }
}

# === STEP 5: Build Instructions ===
Write-Host "Step 5/5 Building instructions..." -ForegroundColor Cyan

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$parts = @()
foreach ($name in $MERGE_LIST) {
    $f = Join-Path $LIBRARY_PATH "instructions\$name.md"
    if (Test-Path $f) {
        # PS 5.1: Get-Content domyślnie używa Windows-1252; czytamy przez .NET z jawnym UTF-8
        $parts += [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
        Write-Host "  [OK] $name.md included"
    } else {
        Write-Host "  [SKIP] $name.md not found in $LIBRARY_PATH\instructions" -ForegroundColor Yellow
    }
}

if ($parts.Count -eq 0) {
    Write-Error "No instruction parts found! Library: $LIBRARY_PATH"
    exit 1
}

$final_content = $parts -join "`r`n`r`n"
[System.IO.File]::WriteAllText($INSTRUCTIONS, $final_content, $utf8NoBom)
Write-Host "  Created: $INSTRUCTIONS ($($final_content.Length) chars)"

if ($LIBRARY_STALE) { Write-LibraryStaleWarning; exit $EXIT_LIBRARY_STALE }
Write-Host ""
Write-Host "SUCCESS: Synchronization complete!" -ForegroundColor Green
Write-Host "Next: git add . && git commit -m 'skillsync: initial setup'"
Write-Host ""
