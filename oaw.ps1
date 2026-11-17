$ErrorActionPreference = 'Stop'
$oawRoot = $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $oawRoot 'payload/scripts/oaw.py')) {
    $oawRoot = Join-Path $oawRoot 'payload'
}
$oawPython = @(
    (Join-Path $oawRoot 'python/python.exe'),
    (Join-Path $oawRoot 'backend/.venv/Scripts/python.exe'),
    (Join-Path $oawRoot '.open-agent-world/desktop-payload/python/python.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $oawPython) {
    $oawCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $oawCommand) {
        Write-Error 'OAW needs Python 3.11 or newer. Run scripts/setup.ps1 or use the desktop installation.'
        exit 1
    }
    $oawPython = $oawCommand.Source
}

function ConvertTo-OawNativeArgument([string]$Value) {
    # Windows parses backslashes specially only before a quote or the closing quote.
    '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

$oawStart = New-Object System.Diagnostics.ProcessStartInfo
$oawStart.FileName = $oawPython
$oawStart.UseShellExecute = $false
$oawStart.WorkingDirectory = (Get-Location).Path
$oawArguments = @('-I', '-B', (Join-Path $oawRoot 'scripts/oaw.py')) + $args
$oawStart.Arguments = ($oawArguments | ForEach-Object { ConvertTo-OawNativeArgument $_ }) -join ' '
$oawStart.RedirectStandardInput = $MyInvocation.ExpectingInput
$oawStart.RedirectStandardOutput = ($args -contains '--json') -or ($args -contains '--plain') -or ($MyInvocation.PipelinePosition -lt $MyInvocation.PipelineLength)
if ($oawStart.RedirectStandardOutput) {
    $oawStart.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
}
if ($oawStart.RedirectStandardInput) {
    # .NET Framework creates the pipe writer from Console.InputEncoding.
    $oawConsoleEncoding = [Console]::InputEncoding
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
}
try {
    $oawProcess = [System.Diagnostics.Process]::Start($oawStart)
} finally {
    if ($oawStart.RedirectStandardInput) {
        [Console]::InputEncoding = $oawConsoleEncoding
    }
}
try {
    if ($oawStart.RedirectStandardInput) {
        foreach ($oawLine in $input) {
            $oawProcess.StandardInput.WriteLine([string]$oawLine)
        }
        $oawProcess.StandardInput.Close()
    }
    if ($oawStart.RedirectStandardOutput) {
        $oawOutput = $oawProcess.StandardOutput.ReadToEnd()
        if ($oawOutput.Length -gt 0) {
            Write-Output $oawOutput.TrimEnd([char[]]"`r`n")
        }
    }
    $oawProcess.WaitForExit()
    $oawExitCode = $oawProcess.ExitCode
} finally {
    $oawProcess.Dispose()
}
exit $oawExitCode
