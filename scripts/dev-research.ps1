[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$researchRoot = Split-Path -Parent $PSScriptRoot
$env:OPEN_AGENT_WORLD_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'OpenAgentWorld-Research-v2'
# The desktop app may have created this store through Windows package redirection.
# Use that existing store from both Codex and the desktop shortcut.
$packagedStore = Join-Path $env:LOCALAPPDATA 'Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/OpenAgentWorld-Research-v2'
if (Test-Path -LiteralPath (Join-Path $packagedStore 'database/world.sqlite3')) {
    $env:OPEN_AGENT_WORLD_DATA_ROOT = $packagedStore
}
$env:OAW_XRD_ROOT = Join-Path (Split-Path -Parent $researchRoot) 'XRD'
& (Join-Path $researchRoot 'backend/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'check-source-environment.py')
if ($LASTEXITCODE -ne 0) { throw 'Run setup.ps1 with this research data root before starting.' }
# Research uses the persistent store, not the resettable development profile.
# Serve the current built frontend and API together at the familiar address.
& node (Join-Path $researchRoot 'frontend/scripts/build-source-frontend.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Frontend preparation failed. See the dependency or build error above.' }
& (Join-Path $researchRoot 'backend/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'start.py') --port 5173 --strict-port
if ($LASTEXITCODE -ne 0) { throw 'Research application exited with an error' }
