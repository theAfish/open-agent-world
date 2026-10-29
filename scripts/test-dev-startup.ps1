# Exercise only the wait function; do not launch or stop application processes.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'dev.ps1'
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$readyFunction = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-BackendReady' }, $true)
Invoke-Expression $readyFunction.Extent.Text
$script:probeStatus = 503
function Invoke-WebRequest {
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
    if ($Uri -ne 'http://127.0.0.1:12345/health/ready') { throw "Wrong readiness endpoint: $Uri" }
    return [pscustomobject]@{ StatusCode = $script:probeStatus }
}
try {
    if (Test-BackendReady -Port 12345) { throw 'An unready application was accepted.' }
    $script:probeStatus = 200
    if (-not (Test-BackendReady -Port 12345)) { throw 'A ready application was rejected.' }
    Write-Host 'PASS: dedicated readiness endpoint and 503 handling.'
} finally {
    Remove-Item -LiteralPath Function:\Invoke-WebRequest
}
$waitFunction = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-BackendListener' }, $true)
Invoke-Expression $waitFunction.Extent.Text

$script:readyAfter = 11
$script:applicationReady = $true
function Test-BackendReady { param([int]$Port) return $script:applicationReady }
$script:elapsed = [Diagnostics.Stopwatch]::StartNew()
function Get-BackendListener {
    param([int]$Port)
    if ($script:elapsed.Elapsed.TotalSeconds -ge $script:readyAfter) { return [pscustomobject]@{ OwningProcess = $PID } }
    return $null
}
$live = Get-Process -Id $PID
$result = Wait-BackendListener -Backend $live -Port 1
if ($null -eq $result -or $script:elapsed.Elapsed.TotalSeconds -lt 10) { throw 'Slow startup was not allowed to finish.' }
$script:readyAfter = 0
$script:applicationReady = $false
$result = Wait-BackendListener -Backend $live -Port 1 -TimeoutMilliseconds 100
if ($null -ne $result) { throw 'A bound socket was treated as a ready application.' }
$script:applicationReady = $true
$script:readyAfter = 999
$script:elapsed.Restart()
$result = Wait-BackendListener -Backend $live -Port 1 -TimeoutMilliseconds 100
if ($null -ne $result -or $script:elapsed.Elapsed.TotalSeconds -gt 3) { throw 'Explicit timeout was not respected.' }
$exited = Start-Process -FilePath $env:ComSpec -ArgumentList '/c exit 0' -WindowStyle Hidden -PassThru
$exited.WaitForExit()
$script:elapsed.Restart()
$result = Wait-BackendListener -Backend $exited -Port 1
if ($null -ne $result -or $script:elapsed.Elapsed.TotalSeconds -gt 3) { throw 'Exited backend was not detected promptly.' }
Write-Host 'PASS: slow startup, explicit timeout, and process exit.'

foreach ($name in @('Read-BackendState', 'Remove-OwnedBackendState')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    Invoke-Expression $definition.Extent.Text
}
$backendStatePath = Join-Path ([IO.Path]::GetTempPath()) ('oaw-process-state-' + [guid]::NewGuid().ToString('N') + '.json')
try {
    $old = [pscustomobject]@{ root = @{ pid = 100; startTimeTicks = [int64]639250591184266567 } }
    $replacement = [pscustomobject]@{ root = @{ pid = 200; startTimeTicks = [int64]639250591184266568 } }
    $replacement | ConvertTo-Json | Set-Content -LiteralPath $backendStatePath -Encoding utf8
    Remove-OwnedBackendState $old
    if (-not (Test-Path -LiteralPath $backendStatePath)) { throw 'Old session erased replacement process ownership.' }
    $recycled = [pscustomobject]@{ root = @{ pid = 200; startTimeTicks = [int64]639250591184266567 } }
    Remove-OwnedBackendState $recycled
    if (-not (Test-Path -LiteralPath $backendStatePath)) { throw 'Recycled PID erased another process ownership.' }
    Remove-OwnedBackendState $replacement
    if (Test-Path -LiteralPath $backendStatePath) { throw 'Owner did not clean up its process record.' }
    Write-Host 'PASS: old sessions and recycled PIDs preserve replacement process ownership.'
} finally {
    Remove-Item -LiteralPath $backendStatePath -Force -ErrorAction SilentlyContinue
}

# Exercise cleanup races with process/taskkill shims; never stop real processes.
foreach ($name in @('Get-ProcessRecord', 'Stop-RecordedProcessTree')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    Invoke-Expression $definition.Extent.Text
}
$script:fixtureStart = [datetime]::UtcNow
$script:cleanupMode = 'exit-during-stop'
$script:stopCalls = 0
$savedExitCode = $global:LASTEXITCODE
function Get-Process {
    param([int]$Id, [object]$ErrorAction)
    if ($script:cleanupMode -eq 'already-exited' -or ($script:cleanupMode -eq 'exit-during-stop' -and $script:stopCalls -gt 0)) { return $null }
    $start = if ($script:cleanupMode -eq 'recycled') { $script:fixtureStart.AddSeconds(1) } else { $script:fixtureStart }
    return [pscustomobject]@{ Id = $Id; StartTime = $start }
}
function taskkill.exe {
    $script:stopCalls++
    Write-Error 'Fixture: process exited between lookup and taskkill.'
    $global:LASTEXITCODE = 128
}
try {
    $record = [pscustomobject]@{ pid = 12345; startTimeTicks = $script:fixtureStart.Ticks }
    foreach ($mode in @('already-exited', 'recycled', 'exit-during-stop', 'still-alive')) {
        $script:cleanupMode = $mode
        $script:stopCalls = 0
        $warnings = @()
        try {
            try { throw 'Original frontend build failure.' }
            finally { Stop-RecordedProcessTree $record 3>&1 | ForEach-Object { $warnings += $_ } }
        } catch {
            if ($_.Exception.Message -ne 'Original frontend build failure.') { throw }
        }
        $expectedCalls = if ($mode -in @('already-exited', 'recycled')) { 0 } else { 1 }
        if ($script:stopCalls -ne $expectedCalls) { throw "Wrong cleanup call count for $mode." }
        if (($warnings.Count -gt 0) -ne ($mode -eq 'still-alive')) { throw "Wrong cleanup warning for $mode." }
        if ($ErrorActionPreference -ne 'Stop') { throw 'Cleanup changed caller error handling.' }
    }
    Write-Host 'PASS: cleanup preserves the original failure, ignores exited/recycled PIDs, and reports a surviving backend.'
} finally {
    Remove-Item -LiteralPath Function:\Get-Process, Function:\taskkill.exe
    $global:LASTEXITCODE = $savedExitCode
}

# Run the real orchestration in an isolated runspace. Only preparation and
# process launch are replaced; no application process is started or stopped.
$definitions = ($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] } | ForEach-Object { $_.Extent.Text }) -join "`n"
$main = ($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] }).Extent.Text
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('oaw-dev-outcomes-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    foreach ($mode in @('preflight-failure', 'backend-failure', 'cancelled')) {
        $caseRoot = Join-Path $testRoot $mode
        New-Item -ItemType Directory -Path $caseRoot | Out-Null
        $logPath = Join-Path $caseRoot 'dev.log'
        Set-Content -LiteralPath $logPath -Value 'Earlier session must survive.'
        foreach ($name in @('backend.out.log', 'backend.err.log')) {
            Set-Content -LiteralPath (Join-Path $caseRoot $name) -Value 'Previous backend attempt.'
        }
        $runner = [powershell]::Create()
        try {
            $null = $runner.AddScript({
                param($definitions, $main, $caseRoot, $mode)
                $ErrorActionPreference = 'Stop'
                Invoke-Expression $definitions
                $projectRoot = $caseRoot
                $backendStatePath = Join-Path $caseRoot 'backend.process.json'
                $devLogPath = Join-Path $caseRoot 'dev.log'
                $backendOut = Join-Path $caseRoot 'backend.out.log'
                $backendError = Join-Path $caseRoot 'backend.err.log'
                $backend = $null
                $backendState = $null
                $devPhase = 'preparing source environments'
                $devOutcome = 'interrupted'
                $PluginPath = @()
                $Profile = 'fixture'
                $StartupTimeoutSeconds = 0
                function Initialize-BackendEnvironment {
                    if ($mode -eq 'preflight-failure') { throw 'Fixture preflight failure.' }
                    if ($mode -eq 'cancelled') { Start-Sleep -Seconds 30 }
                    return $env:ComSpec
                }
                function Get-BackendListener { return $null }
                function Get-AvailableLocalPort { param([int]$PreferredPort) return $PreferredPort }
                function Start-Process {
                    Set-Content -LiteralPath $backendError -Value 'Fixture backend failure.'
                    $process = Microsoft.PowerShell.Management\Start-Process -FilePath $env:ComSpec -ArgumentList '/c exit 7' -WindowStyle Hidden -PassThru
                    $process.WaitForExit()
                    return $process
                }
                Push-Location $projectRoot
                Invoke-Expression $main
            }).AddArgument($definitions).AddArgument($main).AddArgument($caseRoot).AddArgument($mode)
            $pending = $runner.BeginInvoke()
            if ($mode -eq 'cancelled') {
                $timer = [Diagnostics.Stopwatch]::StartNew()
                while ((Get-Content -Raw -LiteralPath $logPath) -notmatch 'Starting development session' -and $timer.Elapsed.TotalSeconds -lt 5) {
                    Start-Sleep -Milliseconds 20
                }
                if ($timer.Elapsed.TotalSeconds -ge 5) { throw 'Fixture did not enter startup.' }
                $runner.Stop()
            }
            $failure = $null
            try { $runner.EndInvoke($pending) | Out-Null } catch { $failure = $_ }
            if ($null -eq $failure) { throw "Expected $mode to terminate the pipeline." }
            $log = Get-Content -Raw -LiteralPath $logPath
            if ($log -notmatch 'Earlier session must survive') { throw 'A retry erased earlier session diagnostics.' }
            if ($mode -eq 'cancelled') {
                if ($log -notmatch 'Interrupted while preparing source environments; backend before cleanup: not launched' -or $log -notmatch 'outcome=interrupted' -or $runner.Streams.Warning.Count -ne 1) { throw 'Cancellation exited without a durable reason and visible warning.' }
            } else {
                $expected = if ($mode -eq 'backend-failure') { 'exit code 7' } else { 'Fixture preflight failure' }
                if ($failure.Exception.Message -notmatch $expected -or $log -notmatch $expected -or $log -notmatch 'outcome=failed' -or $runner.Streams.Warning.Count -ne 0) { throw "Original $mode was lost or misreported as cancellation." }
            }
            if ($mode -eq 'backend-failure') {
                foreach ($name in @('backend.out.log', 'backend.err.log')) {
                    if ((Get-Content -Raw -LiteralPath (Join-Path $caseRoot "$name.previous")) -notmatch 'Previous backend attempt') { throw 'A retry erased previous backend output.' }
                }
            }
        } finally {
            $runner.Dispose()
        }
    }
    Write-Host 'PASS: preflight/backend failures and pipeline cancellation retain their reasons; retries preserve previous logs.'
} finally {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($testRoot)) -ne [IO.Path]::GetTempPath().TrimEnd('\')) { throw 'Test cleanup escaped the temporary directory.' }
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
