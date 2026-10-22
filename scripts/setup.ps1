[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')]
    [string]$Profile,
    [string[]]$PluginPath = @(),
    [switch]$SkipBuild,
    [switch]$CoreOnly,
    [switch]$WithPlugins,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardArguments = @()
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Missing prerequisite: uv.' }
$setupArguments = @('run', '--no-project', '--python', '>=3.11', (Join-Path $PSScriptRoot 'setup.py'))
if ($Profile) { $setupArguments += @('--profile', $Profile) }
foreach ($path in $PluginPath) { $setupArguments += @('--plugin-path', $path) }
if ($SkipBuild) { $setupArguments += '--skip-build' }
if ($PSBoundParameters['Verbose']) { $setupArguments += '--verbose' }
if ($CoreOnly) { $setupArguments += '--core-only' }
if ($WithPlugins) { $setupArguments += '--with-plugins' }
$setupArguments += $ForwardArguments
& uv @setupArguments
if ($LASTEXITCODE -eq 3) {
    Write-Warning 'Setup partially complete: Core is usable; repair the failed optional components listed above.'
    exit 3
}
if ($LASTEXITCODE -ne 0) { throw "Setup failed (exit code $LASTEXITCODE)." }
