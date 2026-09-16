Set-StrictMode -Version Latest

function Get-SetupUv {
    param([string]$RepoRoot)
    $destination = Join-Path $RepoRoot '.run\bootstrap'
    $executable = Join-Path $destination 'uv.exe'
    if (Test-Path -LiteralPath $executable) {
        $version = & $executable --version
        if ($LASTEXITCODE -eq 0 -and $version -match '^uv 0\.12\.13\b') { return $executable }
    }
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $archive = Join-Path $destination 'uv.zip'
    $url = 'https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host 'Downloading the setup helper from Astral...'
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive -TimeoutSec 180
    $checksum = (Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256" -TimeoutSec 60).Content
    if ($checksum -is [byte[]]) { $checksum = [Text.Encoding]::UTF8.GetString($checksum) }
    $expected = ([string]$checksum).Trim().Split(' ')[0]
    if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
        throw 'Setup download verification failed. Check your connection and try again.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
    if (-not (Test-Path -LiteralPath $executable)) { throw 'The setup helper could not be extracted.' }
    return $executable
}
