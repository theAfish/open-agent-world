[CmdletBinding()]
param(
    [ValidateSet("google-adk", "mock")]
    [string]$AgentRuntime = "google-adk",

    [string[]]$PluginPath = @(),

    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')]
    [string]$Profile = "default",

    # Zero waits until ready or process exit; migrations can take much longer than normal startup.
    [ValidateRange(0, 2147483)]
    [int]$StartupTimeoutSeconds = 0
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

    # Python may launch child workers. Stop the whole tree so Ctrl+C
    # cannot leave the actual listener behind on the next run.
    # The process can exit after the identity check. Windows PowerShell turns
    # taskkill's stderr into a terminating error under Stop, even with 2>$null.
    # Cleanup must not mask the original build/startup failure in that race.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        $stopExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($stopExitCode -ne 0) {
        $remaining = Get-ProcessRecord $process.Id
        if ($null -ne $remaining -and [int64]$remaining.startTimeTicks -eq [int64]$Record.startTimeTicks) {
            Write-Warning "Unable to stop backend process $($process.Id) (taskkill exit code $stopExitCode)."
        }
    }
}

function Get-BackendListener {
    param([int]$Port)

    $pattern = "^\s*TCP\s+127\.0\.0\.1:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
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
        LocalPort = $Port
        OwningProcess = [int]$match.Groups[1].Value
    }
}

function Get-AvailableLocalPort {
    param(
        [int]$PreferredPort,
        [int]$MaximumAttempts = 100
    )

    for ($offset = 0; $offset -lt $MaximumAttempts; $offset++) {
        $candidate = $PreferredPort + $offset
        if ($candidate -gt 65535) {
            break
        }

        $probe = $null
        try {
            # Binding briefly is more reliable than parsing netstat alone: it
            # also catches listeners bound to all local interfaces.
            $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
            $probe.Start()
            return $candidate
        }
        catch [System.Net.Sockets.SocketException] {
            continue
        }
        finally {
            if ($null -ne $probe) {
                $probe.Stop()
            }
        }
    }

    throw "No available loopback port was found in the range $PreferredPort-$($PreferredPort + $MaximumAttempts - 1)."
}

function Test-BackendReady {
    param([int]$Port)

    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health/ready" -UseBasicParsing -TimeoutSec 1
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Wait-BackendListener {
    param(
        [System.Diagnostics.Process]$Backend,
        [int]$Port,
        [int]$TimeoutMilliseconds = 0,
        [string]$LogPath
    )

    $started = [Diagnostics.Stopwatch]::StartNew()
    $nextNotice = 0
    $lastDetail = ""
    Write-Host "Waiting for backend startup (including any scheduled storage migration). Ctrl+C cancels."
    while ($TimeoutMilliseconds -eq 0 -or $started.ElapsedMilliseconds -lt $TimeoutMilliseconds) {
        $listener = Get-BackendListener -Port $Port
        # The launcher binds before application initialization. A listening
        # socket alone does not mean persisted state loaded successfully.
        if ($null -ne $listener -and (Test-BackendReady -Port $Port)) {
            return $listener
        }
        if ($Backend.HasExited) {
            return $null
        }
        if ($started.Elapsed.TotalSeconds -ge $nextNotice) {
            $detail = if ($LogPath) { Get-Content -LiteralPath $LogPath -Tail 1 -ErrorAction SilentlyContinue } else { "" }
            if ($detail -and $detail -ne $lastDetail) {
                Write-Host $detail
                $lastDetail = $detail
            } else {
                $activity = if ($lastDetail) { " Last activity: $lastDetail" } else { "" }
                Write-Host ("Backend is still starting ({0:n0}s elapsed).{1}" -f $started.Elapsed.TotalSeconds, $activity)
            }
            $nextNotice = $started.Elapsed.TotalSeconds + 5
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
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

function Remove-OwnedBackendState {
    param([object]$ExpectedState)

    if ($null -eq $ExpectedState) { return }
    $current = Read-BackendState
    if ($null -eq $current) { return }
    # A previous terminal may reach finally after a replacement has already
    # written its process record. It must not erase the replacement's identity.
    if ([string]$current.root.pid -eq [string]$ExpectedState.root.pid -and
        [string]$current.root.startTimeTicks -eq [string]$ExpectedState.root.startTimeTicks) {
        Remove-Item -LiteralPath $backendStatePath -Force -ErrorAction SilentlyContinue
    }
}

function Initialize-BackendEnvironment {
    param([string]$Root, [string]$DevelopmentProfile = "default", [string[]]$AdditionalPlugins = @())

    $python = Join-Path $Root 'backend/.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw "Backend Python is missing. Run scripts/setup.ps1 -Profile $DevelopmentProfile -SkipBuild first." }
    $checkArguments = @((Join-Path $Root 'scripts/check-source-environment.py'), '--profile', $DevelopmentProfile)
    foreach ($path in $AdditionalPlugins) { $checkArguments += @('--plugin-path', $path) }
    & $python @checkArguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Source environment check failed. Run setup for this profile; see the error above.' }
    & node (Join-Path $Root 'frontend/scripts/prepare-dev.mjs') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Frontend preparation failed. See the dependency or build error above.' }
    return $python
}

$env:OPEN_AGENT_WORLD_AGENT_RUNTIME = $AgentRuntime
$env:OPEN_AGENT_WORLD_MODE = "development"
$backendOut = Join-Path $runtimeDirectory "backend.out.log"
$backendError = Join-Path $runtimeDirectory "backend.err.log"

$backend = $null
$backendState = $null

Push-Location $projectRoot
try {
    $resolvedPluginPaths = @(foreach ($candidate in $PluginPath) {
        $candidatePath = if ([System.IO.Path]::IsPathRooted($candidate)) {
            $candidate
        } else {
            Join-Path $projectRoot $candidate
        }
        $resolved = Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop
        if (-not (Test-Path -LiteralPath (Join-Path $resolved.Path 'pyproject.toml'))) {
            throw "Plugin path must contain a pyproject.toml: $($resolved.Path)"
        }
        $resolved.Path
    })
    $backendPython = Initialize-BackendEnvironment -Root $projectRoot -DevelopmentProfile $Profile -AdditionalPlugins $resolvedPluginPaths
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
        Remove-OwnedBackendState $previousState
    }

    $backendPort = Get-AvailableLocalPort -PreferredPort 8000
    $frontendPort = Get-AvailableLocalPort -PreferredPort 5173
    $backendHttpUrl = "http://127.0.0.1:$backendPort"
    $env:OAW_DEV_BACKEND_HTTP_URL = $backendHttpUrl
    $env:OAW_DEV_BACKEND_WS_URL = "ws://127.0.0.1:$backendPort"
    $backendArguments = @(
        "-m", "backend.launcher", "--mode", "development",
        "--profile", $Profile, "--port", $backendPort, "--strict-port"
    )

    $existingListener = Get-BackendListener -Port $backendPort
    if ($null -ne $existingListener) {
        throw "Selected backend port $backendPort is already in use by PID $($existingListener.OwningProcess). Please rerun the script."
    }

    $backend = Start-Process `
        -FilePath $backendPython `
        -ArgumentList $backendArguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendError `
        -WindowStyle Hidden `
        -PassThru

    # Record ownership before waiting, so an interrupted startup can also be
    # recovered by the next invocation.
    $backendState = [pscustomobject]@{
        root = Get-ProcessRecord $backend.Id
        listener = $null
    }
    $backendState | ConvertTo-Json | Set-Content -LiteralPath $backendStatePath -Encoding utf8

    $listener = Wait-BackendListener -Backend $backend -Port $backendPort -TimeoutMilliseconds ($StartupTimeoutSeconds * 1000) -LogPath $backendError
    if ($null -eq $listener) {
        $detail = Get-Content -Raw $backendError -ErrorAction SilentlyContinue
        if (-not $detail) {
            $detail = Get-Content -Raw $backendOut -ErrorAction SilentlyContinue
        }
        $failure = if ($backend.HasExited) {
            "The backend exited before becoming available on $backendHttpUrl (exit code $($backend.ExitCode))."
        } else {
            "The backend did not become available on $backendHttpUrl within $StartupTimeoutSeconds seconds. A storage migration may still be running; omit -StartupTimeoutSeconds to wait without a time limit."
        }
        Stop-RecordedProcessTree (Get-ProcessRecord $backend.Id)
        $backend = $null
        throw "$failure $detail"
    }

    $backendState = [pscustomobject]@{
        root = Get-ProcessRecord $backend.Id
        listener = if ($null -ne $listener) { Get-ProcessRecord $listener.OwningProcess } else { $null }
    }
    $backendState | ConvertTo-Json | Set-Content -LiteralPath $backendStatePath -Encoding utf8

    Write-Host "Open Agent World: http://127.0.0.1:$frontendPort/"
    Write-Host "Backend API: $backendHttpUrl"
    Write-Host "Development profile: $Profile; press F3 for debugging and reset controls."
    if ($resolvedPluginPaths.Count -gt 0) {
        Write-Host "Backend plugins: $($resolvedPluginPaths -join ', ')"
    }
    Write-Host "Backend activity: $backendOut"
    # Shared predev preparation already completed. Start Vite directly, without
    # repeating npm hooks or passing ignore-scripts into Pack build children.
    & node (Join-Path $projectRoot 'frontend/node_modules/vite/bin/vite.js') (Join-Path $projectRoot 'frontend') --host 127.0.0.1 --port $frontendPort --strictPort
    if ($LASTEXITCODE -ne 0) { throw "Frontend development server failed (exit code $LASTEXITCODE). See the build error above." }
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
    Remove-OwnedBackendState $backendState
    Pop-Location
}
