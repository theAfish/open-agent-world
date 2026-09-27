# Exercise only the wait function; do not launch or stop application processes.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'dev.ps1'
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
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
