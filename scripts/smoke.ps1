# Quick smoke test of the generator (K13), Windows/PowerShell twin of scripts/smoke.sh:
# --help, `layers list --json`, `link --dry-run`. Config, layer and project live in a temporary
# directory (isolated HOME/USERPROFILE and SKILLSYNC_CONFIG); nothing else is touched.
# Usage: pwsh -NoProfile -File scripts/smoke.ps1      Exit code: 0 = PASS, 1 = FAIL.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$py = if ($env:PYTHON) { $env:PYTHON } elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('skillsync-smoke-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp | Out-Null

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "[smoke] $Name"
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "step '$Name' failed (exit $LASTEXITCODE)" }
}

$savedHome = $env:HOME; $savedProfile = $env:USERPROFILE; $savedConfig = $env:SKILLSYNC_CONFIG
try {
    $fwd = $tmp -replace '\\', '/'
    $env:HOME = Join-Path $tmp 'home'
    $env:USERPROFILE = $env:HOME
    $env:SKILLSYNC_CONFIG = Join-Path $tmp 'config.yaml'
    foreach ($dir in @('home', 'core/instructions', 'core/skills/feature', 'proj/.github')) {
        New-Item -ItemType Directory -Path (Join-Path $tmp $dir) -Force | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    function Write-Text([string]$Rel, [string]$Text) {
        [System.IO.File]::WriteAllText((Join-Path $tmp $Rel), $Text, $utf8)
    }
    Write-Text 'core/layer.yaml' "schema: 1`nid: core`nkind: core`nversion: 1.0.0`ncore_min: 0.9.0`n"
    Write-Text 'core/README.md' "# core`n"
    Write-Text 'core/CHANGELOG.md' "# Changelog`n"
    Write-Text 'core/instructions/_base.md' "base`n"
    Write-Text 'core/skills/feature/SKILL.md' "---`nname: feature`ndescription: smoke`n---`nsmoke`n"
    Write-Text 'config.yaml' ("layers:`n  - {id: core, kind: core, path: '$fwd/core', source: '$fwd/core'}`n" +
        "layer_rules:`n  - {match: [`"**`"], use: [core]}`ndiscover_paths: []`ntags: {}`npython_command: $py`n")
    Write-Text 'proj/.github/.skillsync.yaml' ''

    $script = Join-Path $root 'skillsync'
    Invoke-Step '--help' { & $py $script --help | Out-Null }
    $json = Join-Path $tmp 'layers.json'
    Invoke-Step 'layers list --json' {
        & $py $script layers list --project (Join-Path $tmp 'proj') --json | Set-Content -Path $json -Encoding utf8
    }
    $report = Get-Content -Raw $json | ConvertFrom-Json
    if ($report.status -eq 'BLOCKED') { throw "layers list BLOCKED: $($report.reason)" }
    if ((Get-Content -Raw $json) -notmatch '"core"') { throw 'layers list does not mention the core layer' }
    Invoke-Step 'link --dry-run' { & $py $script link (Join-Path $tmp 'proj') --dry-run }
    $written = Get-ChildItem -Recurse -Force (Join-Path $tmp 'proj') | Where-Object { -not $_.PSIsContainer -and $_.Name -ne '.skillsync.yaml' }
    if ($written) { throw '--dry-run wrote into the project' }
    Write-Host '[smoke] PASS'
}
catch {
    Write-Host "[smoke] FAIL: $_"
    exit 1
}
finally {
    $env:HOME = $savedHome; $env:USERPROFILE = $savedProfile; $env:SKILLSYNC_CONFIG = $savedConfig
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
