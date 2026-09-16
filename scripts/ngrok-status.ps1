Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'common.ps1')
Get-NgrokSetupStatus (Split-Path -Parent $PSScriptRoot) | ConvertTo-Json -Compress
