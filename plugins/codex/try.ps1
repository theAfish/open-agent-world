[CmdletBinding()]
param(
    [string]$WorkspacePath = "",
    [string]$Model = "default",
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if ($Stop) {
    $trialRoot = Join-Path $projectRoot '.open-agent-world/codex-card-demo'
    if (Test-Path -LiteralPath $trialRoot) {
        Set-Content -LiteralPath (Join-Path $trialRoot 'stop.request') -Value 'stop'
        Write-Host 'Requested Codex trial shutdown.'
    }
    return
}
if (-not $WorkspacePath) { $WorkspacePath = $projectRoot }
$workspace = (Resolve-Path -LiteralPath $WorkspacePath).Path
if (-not $env:OAW_CODEX_COMMAND) {
    $nativeCodex = Get-Command codex.exe -ErrorAction SilentlyContinue
    if ($null -ne $nativeCodex) { $env:OAW_CODEX_COMMAND = $nativeCodex.Source }
}
Push-Location $projectRoot
try {
    & uv run --project backend --with-editable $PSScriptRoot python -m oaw_codex demo --repo $projectRoot --workspace $workspace --model $Model
    if ($LASTEXITCODE -ne 0) { throw "Codex trial exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
