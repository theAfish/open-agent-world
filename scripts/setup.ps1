[CmdletBinding()]
param(
    [switch]$WithAdk
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    $uvArguments = @("sync", "--project", "backend", "--dev")
    if ($WithAdk) {
        $uvArguments += @("--extra", "adk")
    }
    & uv @uvArguments
    if ($LASTEXITCODE -ne 0) { throw "Python environment setup failed." }

    & npm.cmd --prefix frontend ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Frontend environment setup failed." }
}
finally {
    Pop-Location
}
