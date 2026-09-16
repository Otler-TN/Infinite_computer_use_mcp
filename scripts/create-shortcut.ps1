$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskShell = New-Object -ComObject WScript.Shell
$taskLinkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Infinite Computer Use MCP.lnk'
$taskLink = $taskShell.CreateShortcut($taskLinkPath)
if ((Test-Path -LiteralPath $taskLinkPath) -and $taskLink.WorkingDirectory -ne $taskRoot) { throw 'A shortcut with this name belongs to another folder.' }
$taskLink.TargetPath = Join-Path $taskRoot 'Open MCP Manager.cmd'
$taskLink.WorkingDirectory = $taskRoot
$taskLink.Description = 'Set up, start and stop Infinite Computer Use MCP'
$taskLink.IconLocation = Join-Path $env:SystemRoot 'System32\shell32.dll,15'
$taskLink.Save()
