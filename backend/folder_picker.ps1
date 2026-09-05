$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$pickerRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$folderDialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialogOwner = New-Object System.Windows.Forms.Form
try {
    $dialogOwner.TopMost = $true
    $dialogOwner.ShowInTaskbar = $false
    $folderDialog.Description = 'Select a Workspace folder for Open Agent World'
    $folderDialog.SelectedPath = $pickerRequest.initial_path
    $folderDialog.ShowNewFolderButton = $true
    if ($folderDialog.ShowDialog($dialogOwner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write(($folderDialog.SelectedPath | ConvertTo-Json -Compress))
    } else {
        [Console]::Write('null')
    }
} finally {
    $folderDialog.Dispose()
    $dialogOwner.Dispose()
}
