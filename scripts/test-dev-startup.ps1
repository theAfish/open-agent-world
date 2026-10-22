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
