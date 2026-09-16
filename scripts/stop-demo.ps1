param([ValidateRange(1, 65534)][int]$Port = 8010)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$instanceDir = Join-Path $repoRoot ".run\instances\$Port"
$statePath = Join-Path $instanceDir 'state.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host "No managed instance is recorded for port $Port. No processes were stopped."
    return
}
$instanceLock = [IO.File]::Open((Join-Path $instanceDir 'instance.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    if ($state.schema -ne 1 -or $state.port -ne $Port) { throw 'Invalid instance state; refusing to stop unverified processes.' }
    Stop-OwnedProcesses $state.processes
    $remaining = @($state.processes | Where-Object { Test-ProcessIdentity $_ })
    if ($remaining.Count -gt 0) { throw 'Some processes could not be stopped. Use an elevated PowerShell if the server was elevated.' }
    $state.status = 'stopped'
    Write-InstanceState $statePath $state
    Write-Host "Stopped the recorded MCP instance on port $Port."
} finally {
    $instanceLock.Dispose()
}
