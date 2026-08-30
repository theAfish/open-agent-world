[CmdletBinding()]
param(
    [switch]$SkipNativeSandbox
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    & uv run --project backend pytest -p no:cacheprovider tests backend/tests
    if ($LASTEXITCODE -ne 0) { throw "Backend verification failed." }

    if (-not $SkipNativeSandbox) {
        & uv run --project backend python -m backend.scripts.native_sandbox_smoke
        if ($LASTEXITCODE -ne 0) { throw "Native Sandbox verification failed." }
    }

    & npm.cmd --prefix frontend test
    if ($LASTEXITCODE -ne 0) { throw "Frontend verification failed." }

    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
}
finally {
    Pop-Location
}
