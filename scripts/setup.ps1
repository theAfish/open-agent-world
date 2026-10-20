[CmdletBinding()]
param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')]
    [string]$Profile,
    [string[]]$PluginPath = @(),
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Missing prerequisite: uv.' }
$setupArguments = @('run', '--no-project', '--python', '>=3.11', (Join-Path $PSScriptRoot 'setup.py'))
if ($Profile) { $setupArguments += @('--profile', $Profile) }
foreach ($path in $PluginPath) { $setupArguments += @('--plugin-path', $path) }
if ($SkipBuild) { $setupArguments += '--skip-build' }
& uv @setupArguments
if ($LASTEXITCODE -ne 0) { throw "Setup failed (exit code $LASTEXITCODE)." }
