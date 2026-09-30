$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$pickerRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$pickFile = $pickerRequest.kind -eq 'file'
$folderDialog = if ($pickFile) { New-Object System.Windows.Forms.OpenFileDialog } else { New-Object System.Windows.Forms.FolderBrowserDialog }
$dialogOwner = New-Object System.Windows.Forms.Form
try {
    $dialogOwner.TopMost = $true
    $dialogOwner.ShowInTaskbar = $false
    if ($pickFile) {
        $folderDialog.Title = 'Select a file for Open Agent World'
        $folderDialog.InitialDirectory = $pickerRequest.initial_path
        $folderDialog.CheckFileExists = $true
        $folderDialog.Multiselect = $false
    } else {
        $folderDialog.Description = 'Select a folder for Open Agent World'
        $folderDialog.SelectedPath = $pickerRequest.initial_path
        $folderDialog.ShowNewFolderButton = $true
    }
    if ($folderDialog.ShowDialog($dialogOwner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $selectedPath = if ($pickFile) { $folderDialog.FileName } else { $folderDialog.SelectedPath }
        [Console]::Write(($selectedPath | ConvertTo-Json -Compress))
    } else {
        [Console]::Write('null')
    }
} finally {
    $folderDialog.Dispose()
    $dialogOwner.Dispose()
}
