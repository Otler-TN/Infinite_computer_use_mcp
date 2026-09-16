Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$taskRoot = Split-Path -Parent $PSScriptRoot
try { $taskExisting = Resolve-NgrokExecutable $taskRoot } catch { $taskExisting = $null }
if ($taskExisting) {
    & $taskExisting version | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host 'ngrok is already installed.'; return }
}
$taskDownload = Join-Path $taskRoot ('.run\downloads\' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $taskDownload | Out-Null
$taskZip = Join-Path $taskDownload 'ngrok.zip'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host 'Downloading ngrok from its official distribution service...'
Invoke-WebRequest -UseBasicParsing -Uri 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile $taskZip -TimeoutSec 180
Expand-Archive -LiteralPath $taskZip -DestinationPath $taskDownload
$taskBinary = Join-Path $taskDownload 'ngrok.exe'
$taskSignature = Get-AuthenticodeSignature -LiteralPath $taskBinary
if ($taskSignature.Status -ne 'Valid' -or $taskSignature.SignerCertificate.Subject -notmatch 'O="?ngrok, Inc\.') {
    throw 'The ngrok download did not have a valid ngrok publisher signature. It was not installed. Retry or install from ngrok.com/download/windows.'
}
$taskTools = Join-Path $taskRoot '.run\tools'
New-Item -ItemType Directory -Force -Path $taskTools | Out-Null
Copy-Item -LiteralPath $taskBinary -Destination (Join-Path $taskTools 'ngrok.exe') -Force
Write-Host 'ngrok installed. Add your authtoken in Setup to finish.'
