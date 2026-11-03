# Real PowerShell parameter binding and invocation; SSH itself is a local stub.
$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'aliyun_preview_tunnel.ps1'
function Assert($condition, $message) {
    if (-not $condition) { throw $message }
}
$plan = & $helper -Server 'developer@118.31.119.91' -Plan | ConvertFrom-Json
$argv = @($plan.arguments)
Assert ($plan.origin -eq 'http://127.0.0.1:18081') 'Incorrect loopback origin.'
Assert ($argv[0] -eq '-F' -and $argv[1] -eq 'none') 'SSH configuration may inject forwards.'
Assert ($argv[$argv.IndexOf('-L') + 1] -eq '127.0.0.1:18081:/run/oaw-preview/gateway.sock') 'Incorrect socket destination or bind.'
Assert ($argv[-2] -eq '--' -and $argv[-1] -eq 'developer@118.31.119.91') 'Server must follow option termination.'
foreach ($required in @('-N', '-T', '-a', '-x', 'StrictHostKeyChecking=yes', 'ExitOnForwardFailure=yes',
                        'GatewayPorts=no', 'ControlMaster=no', 'ControlPath=none', 'PermitLocalCommand=no')) {
    Assert ($argv -ccontains $required) "Missing SSH restriction $required"
}
Assert (-not ($argv -ccontains '-g') -and -not ($argv -ccontains '-R') -and -not ($argv -ccontains '-f')) 'Unexpected public/reverse/background tunnel.'
$effective = @(& ssh '-G' @argv)
Assert ($LASTEXITCODE -eq 0) 'Installed OpenSSH rejected the tunnel syntax.'
Assert (@($effective | Where-Object { $_ -match '^localforward\s+.*127\.0\.0\.1.*:18081\s+/run/oaw-preview/gateway\.sock$' }).Count -eq 1) 'OpenSSH did not resolve the expected loopback-to-Unix forward.'

$rejected = 0
foreach ($server in @('-oProxyCommand=cmd', 'dev@host;whoami', 'ssh://host', 'dev@host extra', 'dev@host`n-L')) {
    try { & $helper -Server $server -Plan | Out-Null } catch { $rejected++; continue }
    throw "Unsafe server accepted: $server"
}
foreach ($port in @(0, 80, 1023, 65536)) {
    try { & $helper -Server 'host.example' -LocalPort $port -Plan | Out-Null } catch { $rejected++; continue }
    throw "Invalid port accepted: $port"
}

$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('oaw-tunnel-' + [guid]::NewGuid())
[System.IO.Directory]::CreateDirectory($temporary) | Out-Null
try {
    $identity = Join-Path $temporary 'identity with spaces.pem'
    [System.IO.File]::WriteAllText($identity, 'synthetic key path; never used by SSH')
    $withIdentity = & $helper -Server 'developer@host.example' -IdentityFile $identity -LocalPort 19081 -Plan | ConvertFrom-Json
    Assert ($withIdentity.arguments[$withIdentity.arguments.IndexOf('-i') + 1] -eq $identity) 'Identity path lost argument boundaries.'
    Assert ($withIdentity.origin -eq 'http://127.0.0.1:19081') 'Custom port not pinned.'
    function ssh { $global:OawTunnelTestArguments = @($args); $global:LASTEXITCODE = 0 }
    & $helper -Server 'developer@host.example' -IdentityFile $identity -LocalPort 19081
    Assert (($global:OawTunnelTestArguments -join "`n") -ceq ($withIdentity.arguments -join "`n")) 'Execution differs from the reviewed plan.'
} finally {
    Remove-Item -LiteralPath (Join-Path $temporary 'identity with spaces.pem') -ErrorAction SilentlyContinue
    [System.IO.Directory]::Delete($temporary)
    Remove-Item Function:\ssh -ErrorAction SilentlyContinue
    Remove-Variable -Name OawTunnelTestArguments -Scope Global -ErrorAction SilentlyContinue
}
Write-Host "Preview tunnel checks passed ($rejected rejected inputs; plan and stub launch checked)."
