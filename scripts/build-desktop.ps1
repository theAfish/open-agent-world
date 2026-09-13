[CmdletBinding()]
param([switch]$SkipFrontend, [switch]$SkipPayload)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-cache'
    $env:CARGO_HOME = Join-Path $projectRoot '.open-agent-world/cargo'
    $env:npm_config_cache = Join-Path $projectRoot '.open-agent-world/npm-cache'
    if (-not $SkipFrontend) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not $SkipPayload) {
        & (Join-Path $projectRoot 'backend/.venv/Scripts/python.exe') scripts/package-backend.py
        if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
    }
    Push-Location (Join-Path $projectRoot 'desktop')
    try {
        & npm.cmd ci --ignore-scripts --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Desktop installer build failed.' }
    }
    finally { Pop-Location }
    Write-Host 'Installer: desktop/src-tauri/target/release/bundle/nsis/'
}
finally { Pop-Location }
