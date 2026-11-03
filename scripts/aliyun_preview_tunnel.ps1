<# Open the private Preview in this terminal. Ctrl+C closes the loopback listener.
   Use -Plan to inspect arguments without connecting. Server is a literal host,
   not an SSH config alias; no config-provided forwards or commands are loaded. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$')]
    [string]$Server,
    [string]$IdentityFile,
    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 18081,
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
$sshArguments = @(
    '-F', 'none', '-N', '-T', '-a', '-x',
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'GatewayPorts=no',
    '-o', 'ControlMaster=no',
    '-o', 'ControlPath=none',
    '-o', 'PermitLocalCommand=no',
    '-o', 'ConnectTimeout=20',
    '-o', 'ServerAliveInterval=15',
    '-o', 'ServerAliveCountMax=3',
    '-L', "127.0.0.1:${LocalPort}:/run/oaw-preview/gateway.sock"
)
if ($IdentityFile) {
    $identity = Get-Item -LiteralPath $IdentityFile
    if ($identity.PSIsContainer) { throw 'IdentityFile must be an existing file.' }
    $sshArguments += @('-o', 'IdentitiesOnly=yes', '-i', $identity.FullName)
}
$sshArguments += @('--', $Server)

if ($Plan) {
    [pscustomobject]@{
        executable = 'ssh'
        arguments = $sshArguments
        origin = "http://127.0.0.1:$LocalPort"
    } | ConvertTo-Json -Depth 3
    return
}

Write-Host "Private Preview: http://127.0.0.1:$LocalPort (keep this terminal open; Ctrl+C closes it)."
& ssh @sshArguments
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel exited with code $LASTEXITCODE." }
