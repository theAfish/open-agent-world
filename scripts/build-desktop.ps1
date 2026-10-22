[CmdletBinding()]
param([switch]$SkipFrontend, [switch]$SkipPayload)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-cache'
    $env:CARGO_HOME = Join-Path $projectRoot '.open-agent-world/cargo'
    $env:npm_config_cache = Join-Path $projectRoot '.open-agent-world/npm-cache'
    if (-not $SkipFrontend) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not $SkipPayload) {
        & (Join-Path $projectRoot 'backend/.venv/Scripts/python.exe') scripts/package-backend.py
        if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
    }
    # NSIS's compiler cannot open long source paths. Use temporary flat hard
    # links on the same volume, mapping each file to its original installed
    # path. Relative resource keys also avoid Tauri stripping Windows drives.
    # No payload bytes are copied or removed; signing/updater config is retained.
    $payloadRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.open-agent-world/desktop-payload')).Path
    $aliasName = 'nsis-' + [guid]::NewGuid().ToString('N')
    $aliasRoot = Join-Path $projectRoot ".open-agent-world/$aliasName"
    $configPath = Join-Path $projectRoot 'desktop/src-tauri/tauri.conf.json'
    $originalConfig = [IO.File]::ReadAllBytes($configPath)
    try {
        New-Item -ItemType Directory -Path $aliasRoot | Out-Null
        $resources = @{}
        $index = 0
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | ForEach-Object {
            $name = "$index$($_.Extension)"
            $sourceFile = $_.FullName
            # CreateHardLinkW still needs an extended path when the calling
            # shell does not opt into long paths, even though .NET can list it.
            $nativeSource = if ($sourceFile.StartsWith('\\?\')) { $sourceFile }
                elseif ($sourceFile.StartsWith('\\')) { '\\?\UNC\' + $sourceFile.Substring(2) }
                else { '\\?\' + $sourceFile }
            try { New-Item -ItemType HardLink -Path (Join-Path $aliasRoot $name) -Value $nativeSource | Out-Null }
            catch { throw "Cannot stage NSIS resource '$sourceFile': $($_.Exception.Message)" }
            $relative = $sourceFile.Substring($payloadRoot.Length + 1).Replace('\', '/')
            $resources["../../.open-agent-world/$aliasName/$name"] = "payload/$relative"
            $index++
        }
        if ($index -eq 0) { throw 'Cannot package an empty payload.' }
        # --config is forwarded through TAURI_CONFIG, which has a 32K limit on
        # Windows. Keep the large resource map in the config file instead, and
        # restore its exact bytes (including release signing settings) below.
        $config = [Text.Encoding]::UTF8.GetString($originalConfig) | ConvertFrom-Json
        $config.bundle.resources = $resources
        [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 100), [Text.UTF8Encoding]::new($false))
        Push-Location (Join-Path $projectRoot 'desktop')
        try {
            & npm.cmd ci --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Desktop installer build failed.' }
        }
        finally { Pop-Location }
    }
    finally {
        [IO.File]::WriteAllBytes($configPath, $originalConfig)
        if (Test-Path -LiteralPath $aliasRoot) {
            $resolved = (Resolve-Path -LiteralPath $aliasRoot).Path
            $buildRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.open-agent-world')).Path
            if ($resolved -ne (Join-Path $buildRoot $aliasName)) { throw 'Unsafe NSIS staging cleanup path.' }
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
    Write-Host 'Installer: desktop/src-tauri/target/release/bundle/nsis/'
}
finally { Pop-Location }
