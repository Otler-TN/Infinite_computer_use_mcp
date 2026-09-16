param([string]$PythonPath = '', [switch]$Production)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-common.ps1')
$uvPath = Get-SetupUv $repoRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $repoRoot '.run\python'
$options = @('sync', '--locked', '--project', $repoRoot)
if ($Production) { $options += '--no-dev' }
if ($PythonPath) { $options += @('--python', $PythonPath) }
& $uvPath @options
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; see uv output above.' }
$runDir = Join-Path $repoRoot '.run'
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$stdioConfig = Join-Path $runDir 'stdio.toml'
[IO.File]::WriteAllText($stdioConfig, "[server]`ntransport = `"stdio`"`n`n[tools]`nexclude = []`n", [Text.UTF8Encoding]::new($false))
$clientConfig = @{
    mcpServers = @{
        'windows-laptop' = @{
            command = Join-Path $repoRoot '.venv\Scripts\python.exe'
            args = @((Join-Path $PSScriptRoot 'full_server.py'), 'serve', '--transport', 'stdio', '--config', $stdioConfig)
            env = @{ ANONYMIZED_TELEMETRY = 'false'; PYTHONIOENCODING = 'utf-8'; WINDOWS_MCP_TOOLS = ''; WINDOWS_MCP_EXCLUDE_TOOLS = ''; WINDOWS_MCP_INSTANCE_STATE = ''; WINDOWS_MCP_PUBLIC_BASE_URL = '' }
        }
    }
}
[IO.File]::WriteAllText((Join-Path $runDir 'mcp-stdio.json'), ($clientConfig | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $runDir 'installed-lock.sha256'), (Get-FileHash -LiteralPath (Join-Path $repoRoot 'uv.lock') -Algorithm SHA256).Hash)
Write-Host 'Installed the locked Windows-MCP runtime.'
Write-Host 'Local agent configuration: .run\mcp-stdio.json'
Write-Host 'Local:  .\scripts\start-demo.ps1 -LocalOnly'
Write-Host 'Remote: .\scripts\start-demo.ps1 (requires an installed, authenticated ngrok)'
