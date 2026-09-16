param([string]$WindowsMcpPath = '', [switch]$LocalOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$failed = $false
function Write-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    if ($Ok) { Write-Host "[OK]   $Name $Detail" }
    else { Write-Host "[FAIL] $Name $Detail"; $script:failed = $true }
}
Push-Location $repoRoot
try {
    try {
        $python = Get-RuntimePython $repoRoot
        & $python -c "import sys; sys.path.insert(0, 'scripts'); from importlib.metadata import version; from windows_mcp.__main__ import _build_mcp; from fastmcp.client import Client; import laptop_tools; assert version('windows-mcp') == '0.8.5'; _build_mcp(); print('Windows-MCP ' + version('windows-mcp') + ', FastMCP ' + version('fastmcp'))" 2>&1 | ForEach-Object { Write-Host $_ }
        Write-Check 'Runtime imports' ($LASTEXITCODE -eq 0) '(run setup.ps1 to repair)'
    } catch { Write-Check 'Runtime installed' $false $_.Exception.Message }
    if ($WindowsMcpPath) {
        Write-Check 'Local checkout source' (Test-Path -LiteralPath (Join-Path $WindowsMcpPath 'src\windows_mcp\__main__.py')) $WindowsMcpPath
    }
    if (-not $LocalOnly) {
        try {
            $ngrok = Resolve-NgrokExecutable -RepoRoot $repoRoot
            & $ngrok version
            $ngrokConfigArguments = @(Get-NgrokConfigArguments $repoRoot)
            & $ngrok config check @ngrokConfigArguments
            Write-Check 'ngrok configuration' ($LASTEXITCODE -eq 0) '(account authentication is tested when the tunnel starts)'
        } catch { Write-Check 'ngrok available' $false $_.Exception.Message }
    }
    Write-Host "[INFO] Administrator: $(Test-Administrator). Elevation must be granted by Windows before starting the server."
    foreach ($script in Get-ChildItem -LiteralPath $PSScriptRoot -Filter *.ps1) {
        $parseErrors = $null
        [Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$null, [ref]$parseErrors) | Out-Null
        Write-Check "PowerShell syntax: $($script.Name)" (-not $parseErrors)
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    Write-Check 'Git available for source scan' ([bool]$git)
    if ($git) {
        $files = @(& git ls-files --cached --others --exclude-standard | Sort-Object -Unique)
        $patterns = @(
            'sk-[A-Za-z0-9_-]{20,}', 'ghp_[A-Za-z0-9_]{20,}',
            'github_pat_[A-Za-z0-9_]{20,}', 'xox[baprs]-[A-Za-z0-9-]{20,}',
            'AKIA[0-9A-Z]{16}',
            '(?i)(api[_-]?key|secret|password|passwd|token|authtoken)\s*[:=]\s*[''"]?[A-Za-z0-9_./+=-]{20,}'
        )
        $findings = @()
        foreach ($file in $files) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
            $content = Get-Content -Raw -LiteralPath $file
            foreach ($pattern in $patterns) {
                if ($content -match $pattern) { $findings += $file; break }
            }
        }
        Write-Check 'Source secret scan' ($findings.Count -eq 0) '(tracked and untracked, excluding ignored runtime files)'
        foreach ($file in $findings) { Write-Host "       Review $file (matched value withheld)" }
    }
    if ($failed) { exit 1 }
    Write-Host 'Preflight passed.'
} finally { Pop-Location }
