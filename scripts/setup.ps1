[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    $uvArguments = @(
        "sync", "--project", "backend", "--dev", "--inexact",
        "--extra", "adk",
        "--extra", "litellm"
    )
    & uv @uvArguments
    if ($LASTEXITCODE -ne 0) { throw "Python environment setup failed." }
    & backend/.venv/Scripts/python.exe scripts/install-plugins.py
    if ($LASTEXITCODE -ne 0) { throw "Plugin dependency installation failed." }

    & npm.cmd --prefix frontend ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Frontend environment setup failed." }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "Production frontend build failed." }
}
finally {
    Pop-Location
}
