$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $scriptDir "codex_weekly_widget.py"

if (-not (Test-Path $scriptPath)) {
    throw "Cannot find script: $scriptPath"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "Codex Weekly Widget.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "pyw.exe"
$shortcut.Arguments = "-3 `"$scriptPath`""
$shortcut.WorkingDirectory = $scriptDir
$shortcut.IconLocation = "shell32.dll,44"
$shortcut.Description = "Codex Weekly remaining percent widget"
$shortcut.Save()

Write-Host "Startup shortcut created:"
Write-Host $shortcutPath
