param(
    [Parameter(Mandatory)][ValidateSet('start', 'stop', 'verify')][string]$Action,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{32}$')][string]$OperationId,
    [ValidateRange(1, 65534)][int]$Port = 8010,
    [string]$PythonPath = '',
    [switch]$LocalOnly,
    [switch]$RequireAdministrator
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$taskRepoRoot = Split-Path -Parent $PSScriptRoot
$taskOperations = Join-Path $taskRepoRoot '.run\manager\operations'
New-Item -ItemType Directory -Force -Path $taskOperations | Out-Null
$taskResultPath = Join-Path $taskOperations "$OperationId.json"
$taskLogPath = Join-Path $taskOperations "$OperationId.log"
$taskTranscript = $false
$taskResult = [ordered]@{ action = $Action; status = 'running'; message = ''; log = $taskLogPath }
Write-InstanceState $taskResultPath $taskResult
try {
    Start-Transcript -LiteralPath $taskLogPath -Force | Out-Null
    $taskTranscript = $true
    Set-Location -LiteralPath $taskRepoRoot
    if ($RequireAdministrator -and -not (Test-Administrator)) { throw 'Windows did not grant Administrator access. Try again and approve the Windows prompt.' }
    if ($Action -eq 'stop') {
        & (Join-Path $PSScriptRoot 'stop-demo.ps1') -Port $Port
    }
    if ($Action -eq 'start') {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRepoRoot '.venv\Scripts\python.exe'))) {
            $taskResult.message = 'Installing the MCP runtime. This can take a few minutes.'
            Write-InstanceState $taskResultPath $taskResult
            & (Join-Path $PSScriptRoot 'setup.ps1') -PythonPath $PythonPath
        }
        $taskOptions = @{ Port = $Port; LocalOnly = $LocalOnly; RequireAdministrator = $RequireAdministrator }
        & (Join-Path $PSScriptRoot 'start-demo.ps1') @taskOptions
    }
    if ($Action -eq 'verify') {
        $taskState = Get-Content -Raw -LiteralPath (Join-Path $taskRepoRoot ".run\instances\$Port\state.json") | ConvertFrom-Json
        if ($taskState.status -ne 'running' -or -not $taskState.url) { throw 'Start MCP before checking its connection.' }
        & (Get-RuntimePython $taskRepoRoot) (Join-Path $PSScriptRoot 'verify_mcp.py') $taskState.url --report (Join-Path $taskOperations "$OperationId-verification.json")
        if ($LASTEXITCODE -ne 0) { throw 'The connection check failed. Open Logs for the individual results.' }
    }
    $taskResult.status = 'complete'
    $taskResult.message = switch ($Action) {
        'start' { 'MCP is ready. Copy the link into your AI app.' }
        'stop' { 'MCP stopped.' }
        'verify' { 'Connection checked successfully.' }
    }
} catch {
    $taskResult.status = 'failed'
    $taskResult.message = $_.Exception.Message
    Write-Host $taskResult.message
} finally {
    Write-InstanceState $taskResultPath $taskResult
    if ($taskTranscript) { Stop-Transcript | Out-Null }
}
if ($taskResult.status -eq 'failed') { exit 1 }
