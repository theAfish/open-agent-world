[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DataRoot,
    [Parameter(Mandatory=$true)][string]$ModelSource,
    [Parameter(Mandatory=$true)][string]$Python,
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$targetRoot = [IO.Path]::GetFullPath($DataRoot)
$sourceRoot = (Resolve-Path -LiteralPath $ModelSource).Path
$manifest = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'reading-scorer/scorer-manifest.json') | ConvertFrom-Json
# Verify the pinned local files before copying or switching configuration.
foreach ($entry in $manifest.files.PSObject.Properties) {
    $file = Join-Path $sourceRoot $entry.Name
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value.sha256) {
        throw "Scorer asset hash mismatch: $($entry.Name)"
    }
}
$runtime = Join-Path $targetRoot 'tools/reading-scorer'
$model = Join-Path $targetRoot 'models/smollm2-135m'
New-Item -ItemType Directory -Force -Path $runtime,$model | Out-Null
$venv = Join-Path $runtime '.venv'
$interpreter = Join-Path $venv 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) {
    & uv venv --python $Python $venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated reading scorer runtime' }
}
$syncArgs = @('pip','sync','--python',$interpreter,'--extra-index-url','https://download.pytorch.org/whl/cpu',(Join-Path $PSScriptRoot 'reading-scorer/requirements.lock.txt'))
if ($Offline) { $syncArgs += '--offline' }
& uv @syncArgs
if ($LASTEXITCODE -ne 0) { throw 'Cannot install pinned reading scorer dependencies; current configuration was preserved' }
foreach ($entry in $manifest.files.PSObject.Properties) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $entry.Name) -Destination (Join-Path $model $entry.Name)
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'reading-scorer/scorer-manifest.json') -Destination (Join-Path $model 'scorer-manifest.json')
$config = Join-Path $targetRoot 'reading-scorer.json'
if (Test-Path -LiteralPath $config) { Copy-Item -LiteralPath $config -Destination ($config + '.' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.bak') }
@{python=$interpreter;model_dir=$model;threads=1} | ConvertTo-Json | Set-Content -LiteralPath ($config + '.new') -Encoding utf8NoBOM
Move-Item -LiteralPath ($config + '.new') -Destination $config -Force
Write-Host 'Local reading scorer configured. It loads only when ADHD scoring is requested.'
