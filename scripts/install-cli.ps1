param([string]$BinDir, [switch]$NoPath, [switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$oawInstallRoot = Split-Path -Parent $PSScriptRoot
$oawInstallPython = @(
    (Join-Path $oawInstallRoot 'python/python.exe'),
    (Join-Path $oawInstallRoot 'backend/.venv/Scripts/python.exe'),
    (Join-Path $oawInstallRoot '.open-agent-world/desktop-payload/python/python.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $oawInstallPython) {
    $oawInstallPython = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
$oawInstallArguments = @('-I', '-B', (Join-Path $PSScriptRoot 'install-cli.py'), '--json')
if ($BinDir) { $oawInstallArguments += @('--bin-dir', $BinDir) }
if ($NoPath) { $oawInstallArguments += '--no-path' }
if ($Uninstall) { $oawInstallArguments += '--uninstall' }
$oawInstallOutputEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $oawInstallResult = (& $oawInstallPython @oawInstallArguments) | ConvertFrom-Json
} finally {
    [Console]::OutputEncoding = $oawInstallOutputEncoding
}
if ($LASTEXITCODE -ne 0) { throw $oawInstallResult.error }
$oawInstallDirectory = $oawInstallResult.bin_dir
if (-not $oawInstallResult.path_retained) {
    $oawInstallPaths = @($env:PATH -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $oawInstallDirectory.TrimEnd('\') })
    if (-not $Uninstall) { $oawInstallPaths = @($oawInstallDirectory) + $oawInstallPaths }
    $env:PATH = $oawInstallPaths -join ';'
}
if ($Uninstall) {
    Write-Host 'Removed this OAW command.'
} else {
    Write-Host 'Installed oaw. Try: oaw --help'
}
