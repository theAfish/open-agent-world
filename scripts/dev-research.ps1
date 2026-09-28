[CmdletBinding()]
param(
    [string]$DataRoot,
    [string]$XrdRoot = $env:OAW_XRD_ROOT,
    [switch]$SkipBuild,
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
$researchRoot = Split-Path -Parent $PSScriptRoot
if ($DataRoot) { $env:OPEN_AGENT_WORLD_DATA_ROOT = $DataRoot }
else {
$env:OPEN_AGENT_WORLD_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'OpenAgentWorld-Research-v2'
# The desktop app may have created this store through Windows package redirection.
# Use that existing store from both Codex and the desktop shortcut.
$packagedStore = Join-Path $env:LOCALAPPDATA 'Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/OpenAgentWorld-Research-v2'
if (Test-Path -LiteralPath (Join-Path $packagedStore 'database/world.sqlite3')) {
    $env:OPEN_AGENT_WORLD_DATA_ROOT = $packagedStore
}
}
$url = 'http://127.0.0.1:5173/'
$listener = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::') } | Select-Object -First 1
if ($listener) {
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    $expectedFrontend = Join-Path $researchRoot 'frontend\dist'
    $command = ([string]$serverProcess.CommandLine).Replace('/', '\')
    if ($command -notmatch 'backend\.launcher' -or
        $command.IndexOf($expectedFrontend, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'Port 5173 belongs to another application or checkout. It was left running.'
    }
    $world = Invoke-RestMethod -Uri ($url + 'api/world') -TimeoutSec 10
    $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200 -or $null -eq $world.PSObject.Properties['nodes'] -or
        $null -eq $world.PSObject.Properties['edges']) { throw 'OAW is not ready; no duplicate was started.' }
    Write-Host "OAW is already running (PID $($listener.OwningProcess)): $url"
    if (-not $NoOpen) { Start-Process $url }
    return
}
if (-not $XrdRoot) {
    $candidate = Join-Path (Split-Path -Parent $researchRoot) 'XRD'
    if (Test-Path -LiteralPath $candidate) { $XrdRoot = $candidate }
}
if ($XrdRoot) {
    if (-not (Test-Path -LiteralPath $XrdRoot)) { throw "XRD root does not exist: $XrdRoot" }
    $env:OAW_XRD_ROOT = $XrdRoot
}
if (-not $SkipBuild) {
& uv pip install --python (Join-Path $researchRoot 'backend/.venv/Scripts/python.exe') -e (Join-Path $researchRoot 'plugins/library') -e (Join-Path $researchRoot 'plugins/literature') -e (Join-Path $researchRoot 'plugins/xrd') -e (Join-Path $researchRoot 'plugins/atomsculptor')
if ($LASTEXITCODE -ne 0) { throw 'Plugin dependencies could not be installed' }
# dev.ps1 calls uv run: retain local editable plugin dependencies.
# Research uses the persistent store, not the resettable development profile.
# Serve the current built frontend and API together at the familiar address.
& npm.cmd --prefix (Join-Path $researchRoot 'frontend') run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
$launchArguments = @('-m', 'backend.launcher', '--frontend', (Join-Path $researchRoot 'frontend/dist'), '--port', '5173', '--strict-port')
if (-not $NoOpen) { $launchArguments += '--open' }
Push-Location $researchRoot
try { & (Join-Path $researchRoot 'backend/.venv/Scripts/python.exe') @launchArguments }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'Research application exited with an error' }
