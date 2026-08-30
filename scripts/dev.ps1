[CmdletBinding()]
param(
    [ValidateSet("mock", "google-adk")]
    [string]$AgentRuntime = "mock"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot ".open-agent-world"
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null

$env:OPEN_AGENT_WORLD_AGENT_RUNTIME = $AgentRuntime
$backendOut = Join-Path $runtimeDirectory "backend.out.log"
$backendError = Join-Path $runtimeDirectory "backend.err.log"
$backendArguments = @(
    "run", "--project", "backend", "uvicorn", "backend.main:app",
    "--host", "127.0.0.1", "--port", "8000"
)

Push-Location $projectRoot
try {
    $backend = Start-Process `
        -FilePath "uv" `
        -ArgumentList $backendArguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendError `
        -WindowStyle Hidden `
        -PassThru

    Start-Sleep -Milliseconds 700
    if ($backend.HasExited) {
        $detail = Get-Content -Raw $backendError -ErrorAction SilentlyContinue
        throw "The backend did not start. $detail"
    }

    Write-Host "Open Agent World: http://127.0.0.1:5173/"
    Write-Host "Backend activity: $backendOut"
    & npm.cmd --prefix frontend run dev
}
finally {
    if ($null -ne $backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
    Pop-Location
}
