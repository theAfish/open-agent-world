[CmdletBinding()]
param(
    [ValidateSet("mock", "google-adk", "litellm")]
    [string]$AgentRuntime = "mock"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot ".open-agent-world"
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
$backendStatePath = Join-Path $runtimeDirectory "backend.process.json"

function Get-ProcessRecord {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }

    try {
        return [pscustomobject]@{
            pid = $process.Id
            startTimeTicks = $process.StartTime.ToUniversalTime().Ticks
        }
    }
    catch {
        return $null
    }
}

function Stop-RecordedProcessTree {
    param([object]$Record)

    if ($null -eq $Record) {
        return
    }

    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return
    }

    try {
        $currentStartTimeTicks = $process.StartTime.ToUniversalTime().Ticks
        if ([int64]$Record.startTimeTicks -ne $currentStartTimeTicks) {
            return
        }
    }
    catch {
        return
    }

    # uv launches Python/Uvicorn as a child. Stop the whole tree so Ctrl+C
    # cannot leave the actual listener behind on the next run.
    & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
}

function Get-BackendListener {
    $pattern = '^\s*TCP\s+127\.0\.0\.1:8000\s+\S+\s+LISTENING\s+(\d+)\s*$'
    $line = netstat.exe -ano -p tcp 2>$null |
        Select-String -Pattern $pattern |
        Select-Object -First 1
    if ($null -eq $line) {
        return $null
    }

    $match = [regex]::Match($line.Line, $pattern)
    if (-not $match.Success) {
        return $null
    }

    return [pscustomobject]@{
        LocalAddress = "127.0.0.1"
        LocalPort = 8000
        OwningProcess = [int]$match.Groups[1].Value
    }
}

function Read-BackendState {
    if (-not (Test-Path -LiteralPath $backendStatePath)) {
        return $null
    }

    try {
        return Get-Content -Raw -LiteralPath $backendStatePath | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

$env:OPEN_AGENT_WORLD_AGENT_RUNTIME = $AgentRuntime
$backendOut = Join-Path $runtimeDirectory "backend.out.log"
$backendError = Join-Path $runtimeDirectory "backend.err.log"
$backendArguments = @(
    "run", "--project", "backend", "uvicorn", "backend.main:app",
    "--host", "127.0.0.1", "--port", "8000"
)

$backend = $null
$backendState = $null

Push-Location $projectRoot
try {
    # Recover a backend left by an earlier invocation. The recorded start time
    # prevents a recycled PID from causing an unrelated process to be killed.
    $previousState = Read-BackendState
    if ($null -ne $previousState) {
        Stop-RecordedProcessTree $previousState.root
        if ($null -ne $previousState.listener -and
            ($null -eq $previousState.root -or
             [int]$previousState.listener.pid -ne [int]$previousState.root.pid)) {
            Stop-RecordedProcessTree $previousState.listener
        }
        Remove-Item -LiteralPath $backendStatePath -Force -ErrorAction SilentlyContinue
    }

    $existingListener = Get-BackendListener
    if ($null -ne $existingListener) {
        throw "Port 8000 is already in use by PID $($existingListener.OwningProcess). Stop that process or use the existing backend before starting the frontend."
    }

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

    $listener = Get-BackendListener
    $backendState = [pscustomobject]@{
        root = Get-ProcessRecord $backend.Id
        listener = if ($null -ne $listener) { Get-ProcessRecord $listener.OwningProcess } else { $null }
    }
    $backendState | ConvertTo-Json | Set-Content -LiteralPath $backendStatePath -Encoding utf8

    Write-Host "Open Agent World: http://127.0.0.1:5173/"
    Write-Host "Backend activity: $backendOut"
    & npm.cmd --prefix frontend run dev
}
finally {
    if ($null -ne $backendState) {
        Stop-RecordedProcessTree $backendState.root
        if ($null -ne $backendState.listener -and
            ($null -eq $backendState.root -or
             [int]$backendState.listener.pid -ne [int]$backendState.root.pid)) {
            Stop-RecordedProcessTree $backendState.listener
        }
    }
    elseif ($null -ne $backend -and -not $backend.HasExited) {
        Stop-RecordedProcessTree (Get-ProcessRecord $backend.Id)
    }
    Remove-Item -LiteralPath $backendStatePath -Force -ErrorAction SilentlyContinue
    Pop-Location
}
