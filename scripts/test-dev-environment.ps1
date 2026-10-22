# Exercise the real Windows preflight function with a disposable Python shim.
# No application process is started/stopped and no packages are downloaded.
$ErrorActionPreference = 'Stop'
$savedExitCode = $global:LASTEXITCODE
$projectRoot = Split-Path -Parent $PSScriptRoot
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'dev.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Initialize-BackendEnvironment' }, $true)
Invoke-Expression $definition.Extent.Text

$testBase = [IO.Path]::GetFullPath((Join-Path $projectRoot '.tmp'))
$testRoot = Join-Path $testBase ('dev environment ' + [guid]::NewGuid().ToString('N'))
$venv = Join-Path $testRoot 'backend/.venv'
$python = Join-Path $venv 'Scripts/python.exe'
$extra = Join-Path $testRoot 'extra plugin'
New-Item -ItemType Directory -Force -Path (Split-Path $python), (Join-Path $testRoot 'scripts'), (Join-Path $testRoot 'plugins/example'), $extra | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'backend/.venv/Scripts/python.exe') -Destination $python
Copy-Item -LiteralPath (Join-Path $projectRoot 'backend/.venv/pyvenv.cfg') -Destination (Join-Path $venv 'pyvenv.cfg')
Set-Content -LiteralPath (Join-Path $testRoot 'plugins/example/pyproject.toml') -Value '[project]'
@'
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
print('Check progress must not become part of the returned Python path.')
(root / 'receipt.json').write_text(json.dumps({'python': sys.executable, 'prefix': sys.prefix, 'plugins': sys.argv[1:]}))
sys.exit(4 if (root / 'fail-plugin').exists() else 0)
'@ | Set-Content -LiteralPath (Join-Path $testRoot 'scripts/check-source-environment.py') -Encoding utf8
function uv { throw 'Dev must never invoke a package manager.' }
$script:nodeExitCode = 0
function node {
    $script:nodeArguments = @($args)
    $global:LASTEXITCODE = $script:nodeExitCode
}

try {
    $actual = Initialize-BackendEnvironment -Root $testRoot -DevelopmentProfile 'fixture-profile' -AdditionalPlugins @($extra)
    if ($actual -isnot [string] -or $actual -ne $python) { throw 'Installer output contaminated the backend executable.' }
    $receipt = Get-Content -Raw -LiteralPath (Join-Path $testRoot 'receipt.json') | ConvertFrom-Json
    if ($receipt.python -ne $python -or $receipt.prefix -ne $venv) { throw 'Setup used a different interpreter from server startup.' }
    if ($receipt.plugins.Count -ne 4 -or $receipt.plugins[0] -ne '--profile' -or $receipt.plugins[1] -ne 'fixture-profile' -or $receipt.plugins[2] -ne '--plugin-path' -or $receipt.plugins[3] -ne $extra) { throw "Profile or plugin paths with spaces were not preserved: $($receipt.plugins | ConvertTo-Json -Compress)" }
    if ($script:nodeArguments[-1] -ne (Join-Path $testRoot 'frontend/scripts/prepare-dev.mjs')) { throw 'Frontend did not use shared preparation.' }
    $frontend = $ast.Find({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'node' -and $node.Extent.Text -match 'vite/bin/vite.js' }, $true)
    if ($null -eq $frontend) { throw 'Frontend must start Vite directly after preflight.' }
    # A second launch must perform the same read-only checks, with no uv call.
    Initialize-BackendEnvironment -Root $testRoot -DevelopmentProfile 'fixture-profile' | Out-Null
    $start = $ast.Find({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Start-Process' -and $node.Extent.Text -match '-RedirectStandardError \$backendError' }, $true)
    if ($start.Extent.Text -notmatch '-FilePath \$backendPython') { throw 'Backend launch bypasses the prepared interpreter.' }

    $script:nodeExitCode = 3
    try {
        Initialize-BackendEnvironment -Root $testRoot | Out-Null
        throw 'Expected frontend check failure.'
    } catch {
        if ($_.Exception.Message -ne 'Frontend preparation failed. See the dependency or build error above.') { throw }
    }
    $script:nodeExitCode = 0
    New-Item -ItemType File -Path (Join-Path $testRoot 'fail-plugin') | Out-Null
    try {
        Initialize-BackendEnvironment -Root $testRoot | Out-Null
        throw 'Expected plugin setup failure.'
    } catch {
        if ($_.Exception.Message -ne 'Source environment check failed. Run setup for this profile; see the error above.') { throw }
    }
    Write-Host 'PASS: repeat launches never install, persistent interpreter, spaced plugin paths, direct server launch, and preflight failures.'
} finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if ([IO.Path]::GetDirectoryName($resolved) -ne $testBase) { throw 'Test cleanup escaped the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

# Both shell setup entrypoints delegate installation to setup.py. Verify the
# PowerShell wrapper using uv as an argument recorder, without running uv.
$global:OawTestSetupExitCode = 0
function uv {
    $global:OawTestSetupArguments = @($args)
    $global:LASTEXITCODE = $global:OawTestSetupExitCode
}
& (Join-Path $PSScriptRoot 'setup.ps1') -Profile 'fixture-profile' -PluginPath @('external plugin with spaces') -SkipBuild
$expected = @('run', '--no-project', '--python', '>=3.11', (Join-Path $PSScriptRoot 'setup.py'), '--profile', 'fixture-profile', '--plugin-path', 'external plugin with spaces', '--skip-build')
if (($global:OawTestSetupArguments | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw 'Setup wrapper changed arguments.' }
& (Join-Path $PSScriptRoot 'setup.ps1') -Verbose --runtime adk --skip-build
$expected = @('run', '--no-project', '--python', '>=3.11', (Join-Path $PSScriptRoot 'setup.py'), '--verbose', '--runtime', 'adk', '--skip-build')
if (($global:OawTestSetupArguments | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw 'Setup wrapper did not forward portable Python flags.' }
$global:OawTestSetupExitCode = 7
try {
    & (Join-Path $PSScriptRoot 'setup.ps1')
    throw 'Expected setup failure.'
} catch {
    if ($_.Exception.Message -ne 'Setup failed (exit code 7).') { throw }
}
Write-Host 'PASS: setup wrapper preserves profile, spaced plugin paths, build mode, and failure status.'
Remove-Variable OawTestSetupArguments, OawTestSetupExitCode -Scope Global
Remove-Item -LiteralPath Function:\uv
$global:LASTEXITCODE = $savedExitCode
