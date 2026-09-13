[CmdletBinding()]
param([switch]$Preview, [string]$Profile = "default")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/setup.ps1 first.' }
$launchArguments = @((Join-Path $PSScriptRoot 'start.py'))
if ($Preview) { $launchArguments += @('--mode', 'preview', '--profile', $Profile) }
& $python @launchArguments
if ($LASTEXITCODE -ne 0) { throw "Application exited with code $LASTEXITCODE" }
