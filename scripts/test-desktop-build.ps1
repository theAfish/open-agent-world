# Exercise real resource staging/cleanup while replacing only npm's build.
$ErrorActionPreference = 'Stop'
$originalExitCode = $global:LASTEXITCODE
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixture = Join-Path $workspace ('.tmp/desktop build fixture ' + [guid]::NewGuid().ToString('N'))
$originalLocation = (Get-Location).Path
$global:OawDesktopBuildFixture = @{ exitCode = 0; aliasRoot = $null; longTarget = $null }
$savedEnvironment = @{}
foreach ($name in @('UV_CACHE_DIR', 'CARGO_HOME', 'npm_config_cache')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'scripts'), (Join-Path $fixture 'desktop/src-tauri'), (Join-Path $fixture '.open-agent-world/desktop-payload') | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build-desktop.ps1') -Destination (Join-Path $fixture 'scripts/build-desktop.ps1')
    Set-Content -LiteralPath (Join-Path $fixture '.open-agent-world/desktop-payload/source.txt') -Value 'payload fixture'
    $deepRelative = 'deep/' + ('a' * 60) + '/' + ('b' * 60) + '/' + ('c' * 60) + '/source.txt'
    $deepSource = Join-Path $fixture ('.open-agent-world/desktop-payload/' + $deepRelative)
    if ($deepSource.Length -le 300) { throw 'Fixture must exercise a long source path' }
    [IO.Directory]::CreateDirectory('\\?\' + (Split-Path -Parent $deepSource)) | Out-Null
    [IO.File]::WriteAllText('\\?\' + $deepSource, 'long payload fixture')
    $global:OawDesktopBuildFixture.longTarget = 'payload/' + $deepRelative
    $configPath = Join-Path $fixture 'desktop/src-tauri/tauri.conf.json'
    $originalConfig = '{"version":"1.2.3","bundle":{"resources":{"../../.open-agent-world/desktop-payload/":"payload/"},"windows":{"certificateThumbprint":"test"}},"plugins":{"updater":{"pubkey":"test"}}}'
    [IO.File]::WriteAllText($configPath, $originalConfig)
    function npm.cmd {
        $global:LASTEXITCODE = 0
        if ($args[0] -eq 'ci') { return }
        if (($args -join ' ') -ne 'run build') { throw 'Large config must not use the CLI environment override' }
        $config = Get-Content -Raw -LiteralPath 'src-tauri/tauri.conf.json' | ConvertFrom-Json
        $properties = @($config.bundle.resources.PSObject.Properties)
        if ($properties.Count -ne 2) { throw 'Unexpected resource mappings' }
        $alias = $properties | Where-Object Value -eq 'payload/source.txt'
        $sourcePath = [IO.Path]::GetFullPath((Join-Path (Get-Location) ('src-tauri/' + $alias.Name)))
        $global:OawDesktopBuildFixture.aliasRoot = Split-Path -Parent $sourcePath
        if ((Get-Content -LiteralPath $sourcePath) -ne 'payload fixture') { throw 'Alias does not point to payload' }
        if ((Get-Item -LiteralPath $sourcePath).LinkType -ne 'HardLink') { throw 'Payload was copied instead of linked' }
        $longAlias = $properties | Where-Object Value -eq $global:OawDesktopBuildFixture.longTarget
        if (-not $longAlias) { throw 'Long resource lost its original installed path' }
        $longSource = [IO.Path]::GetFullPath((Join-Path (Get-Location) ('src-tauri/' + $longAlias.Name)))
        if ($longSource.Length -ge 260 -or (Get-Content -LiteralPath $longSource) -ne 'long payload fixture') { throw 'Long resource has no usable short source' }
        if ($config.version -ne '1.2.3' -or $config.bundle.windows.certificateThumbprint -ne 'test' -or $config.plugins.updater.pubkey -ne 'test') { throw 'Build changed version/signing/updater configuration' }
        $global:LASTEXITCODE = $global:OawDesktopBuildFixture.exitCode
    }
    foreach ($code in @(0, 7)) {
        $global:OawDesktopBuildFixture.exitCode = $code
        $global:OawDesktopBuildFixture.aliasRoot = $null
        $failure = $null
        try { & (Join-Path $fixture 'scripts/build-desktop.ps1') -SkipFrontend -SkipPayload }
        catch { $failure = $_ }
        if (($code -eq 0) -and $failure) { throw $failure }
        if (($code -ne 0) -and (!$failure -or $failure.Exception.Message -ne 'Desktop installer build failed.')) { throw 'Build failure was not propagated' }
        if (!$global:OawDesktopBuildFixture.aliasRoot -or (Test-Path -LiteralPath $global:OawDesktopBuildFixture.aliasRoot)) { throw 'Temporary resource links were not removed' }
        if ([IO.File]::ReadAllText($configPath) -cne $originalConfig) { throw 'Original config bytes were not restored' }
        if ((Get-Location).Path -ne $originalLocation) { throw 'Build changed caller location' }
    }
    Write-Host 'PASS: real payload hard links, isolated config, failure propagation and cleanup.'
}
finally {
    Remove-Item -LiteralPath Function:\npm.cmd -ErrorAction SilentlyContinue
    Remove-Variable -Name OawDesktopBuildFixture -Scope Global
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name]) }
    $global:LASTEXITCODE = $originalExitCode
    if (Test-Path -LiteralPath $fixture) {
        $resolved = (Resolve-Path -LiteralPath $fixture).Path
        if (-not $resolved.StartsWith($workspace + '\.tmp\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
