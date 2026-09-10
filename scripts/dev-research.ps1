[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$researchRoot = Split-Path -Parent $PSScriptRoot
$env:OPEN_AGENT_WORLD_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'OpenAgentWorld-Research-v2'
$env:OAW_XRD_ROOT = Join-Path (Split-Path -Parent $researchRoot) 'XRD'
& uv pip install --python (Join-Path $researchRoot 'backend/.venv/Scripts/python.exe') -e (Join-Path $researchRoot 'plugins/library') -e (Join-Path $researchRoot 'plugins/xrd')
if ($LASTEXITCODE -ne 0) { throw 'Plugin dependencies could not be installed' }
# dev.ps1 calls uv run: retain local editable plugin dependencies.
$env:UV_NO_SYNC = '1'
& (Join-Path $PSScriptRoot 'dev.ps1') -AgentRuntime google-adk
