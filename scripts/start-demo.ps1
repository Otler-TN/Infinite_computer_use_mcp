param(
    [ValidateRange(1, 65534)][int]$Port = 8010,
    [ValidateSet('127.0.0.1', 'localhost')][string]$HostName = '127.0.0.1',
    [string]$WindowsMcpPath = '',
    [string]$NgrokPath = '',
    [ValidateRange(0, 65535)][int]$ProxyPort = 0,
    [ValidateRange(0.1, 1.0)][double]$ScreenshotScale = 0.5,
    [switch]$NoAcceptProxy,
    [switch]$SkipVerify,
    [switch]$LocalOnly,
    [switch]$RequireAdministrator
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Get-RuntimePython $repoRoot
$elevated = Test-Administrator
if ($RequireAdministrator -and -not $elevated) {
    throw 'Administrator access requested. Open PowerShell using Run as administrator and run this same command. Windows UAC approval is required.'
}
if ($ProxyPort -eq 0) { $ProxyPort = $Port + 1 }
if (-not $NoAcceptProxy -and $ProxyPort -eq $Port) { throw 'Port and ProxyPort must be different.' }
$instanceDir = Join-Path $repoRoot ".run\instances\$Port"
New-Item -ItemType Directory -Force -Path $instanceDir | Out-Null
$statePath = Join-Path $instanceDir 'state.json'
$instanceLock = [IO.File]::Open((Join-Path $instanceDir 'instance.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$state = $null
$savedEnv = @{}
try {
    if (Test-Path -LiteralPath $statePath) {
        $old = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
        foreach ($record in @($old.processes)) {
            if (Test-ProcessIdentity $record) { throw "Instance $Port is running. Stop it using .\scripts\stop-demo.ps1 -Port $Port." }
        }
    }
    Assert-PortAvailable $Port
    if (-not $NoAcceptProxy) { Assert-PortAvailable $ProxyPort }
    $ngrok = $null
    if (-not $LocalOnly) {
        $ngrok = Resolve-NgrokExecutable -RepoRoot $repoRoot -ExplicitPath $NgrokPath
        Assert-PortAvailable 4040
        $ngrokConfigArguments = @(Get-NgrokConfigArguments $repoRoot)
        & $ngrok config check @ngrokConfigArguments 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'ngrok config check failed. Configure ngrok before starting, or use -LocalOnly.' }
    }
    $sourcePath = $null
    if ($WindowsMcpPath) {
        $checkout = (Resolve-Path -LiteralPath $WindowsMcpPath).Path
        $sourcePath = Join-Path $checkout 'src'
        if (-not (Test-Path -LiteralPath (Join-Path $sourcePath 'windows_mcp\__main__.py'))) {
            throw "Windows-MCP source is missing in $checkout. Omit -WindowsMcpPath to use the locked package."
        }
    }
    $environment = @{
        WINDOWS_MCP_SCREENSHOT_SCALE = $ScreenshotScale.ToString([Globalization.CultureInfo]::InvariantCulture)
        ANONYMIZED_TELEMETRY = 'false'
        PYTHONIOENCODING = 'utf-8'
        PYTHONUTF8 = '1'
        WINDOWS_MCP_TOOLS = $null
        WINDOWS_MCP_EXCLUDE_TOOLS = $null
        WINDOWS_MCP_AUTH_KEY = $null
        WINDOWS_MCP_IP_ALLOWLIST = $null
        WINDOWS_MCP_CORS_ORIGINS = $null
        WINDOWS_MCP_STATELESS_HTTP = $null
        WINDOWS_MCP_INSTANCE_STATE = $statePath
        PYTHONPATH = $sourcePath
    }
    foreach ($name in $environment.Keys) {
        $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
    }
    $configPath = Join-Path $instanceDir 'server.toml'
    $config = "[server]`ntransport = `"streamable-http`"`nhost = `"$HostName`"`nport = $Port`n`n[tools]`nexclude = []`n"
    [IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
    $state = [ordered]@{
        schema = 1; port = $Port; proxyPort = $(if ($NoAcceptProxy) { $null } else { $ProxyPort })
        elevated = $elevated; status = 'starting'; url = $null
        processes = [System.Collections.Generic.List[object]]::new()
    }
    Write-InstanceState $statePath $state
    function Start-Component {
        param([string]$Role, [string]$Executable, [string[]]$Arguments)
        $nativeArgs = ($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
        $process = Start-Process -FilePath $Executable -ArgumentList $nativeArgs -WorkingDirectory $repoRoot `
            -RedirectStandardOutput (Join-Path $instanceDir "$Role.log") `
            -RedirectStandardError (Join-Path $instanceDir "$Role.error.log") -WindowStyle Hidden -PassThru
        $identity = Get-ProcessIdentity -ProcessId $process.Id -Role $Role
        if ($identity) { $state.processes.Add($identity) }
        Write-InstanceState $statePath $state
        return $process
    }
    function Record-Listener {
        param([int]$ListenerPid, [string]$Role)
        if ($ListenerPid -notin @($state.processes | ForEach-Object pid)) {
            $state.processes.Add((Get-ProcessIdentity -ProcessId $ListenerPid -Role $Role))
            Write-InstanceState $statePath $state
        }
    }
    $server = Start-Component 'server' $python @((Join-Path $PSScriptRoot 'full_server.py'), 'serve', '--config', $configPath, '--transport', 'streamable-http', '--host', $HostName, '--port', [string]$Port)
    Record-Listener (Wait-ManagedPort $Port $server) 'server-listener'
    $endpointPort = $Port
    if (-not $NoAcceptProxy) {
        $proxy = Start-Component 'proxy' $python @((Join-Path $PSScriptRoot 'mcp_accept_proxy.py'), '--listen-host', $HostName, '--listen-port', [string]$ProxyPort, '--target-host', $HostName, '--target-port', [string]$Port)
        Record-Listener (Wait-ManagedPort $ProxyPort $proxy) 'proxy-listener'
        $endpointPort = $ProxyPort
    }
    $state.url = "http://$HostName`:$endpointPort/mcp"
    Write-InstanceState $statePath $state
    $verifyArguments = @()
    if ($RequireAdministrator) { $verifyArguments += '--require-admin' }
    if (-not $SkipVerify) {
        & $python (Join-Path $PSScriptRoot 'verify_mcp.py') $state.url @verifyArguments --report (Join-Path $instanceDir 'verification-local.json')
        if ($LASTEXITCODE -ne 0) { throw 'Local MCP verification failed; see verification-local.json and component logs.' }
    }
    if ($ngrok) {
        $upstream = "http://$HostName`:$endpointPort"
        $tunnelProcess = Start-Component 'ngrok' $ngrok (@('http', $upstream, "--host-header=$HostName`:$endpointPort", '--log=stdout') + $ngrokConfigArguments)
        Record-Listener (Wait-ManagedPort 4040 $tunnelProcess) 'ngrok-listener'
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        $publicUrl = $null
        while ([DateTime]::UtcNow -lt $deadline) {
            $tunnelProcess.Refresh()
            if ($tunnelProcess.HasExited) { throw 'ngrok exited; see ngrok.error.log.' }
            try {
                $response = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3
                $httpsTunnels = @($response.tunnels | Where-Object { $_.proto -eq 'https' })
                $tunnel = $httpsTunnels | Where-Object { $_.config.addr.TrimEnd('/') -eq $upstream } | Select-Object -First 1
                # ngrok versions differ slightly in how they normalize config.addr. This
                # process owns port 4040, so a single HTTPS tunnel is unambiguous.
                if (-not $tunnel -and $httpsTunnels.Count -eq 1) { $tunnel = $httpsTunnels[0] }
                if ($tunnel) { $publicUrl = $tunnel.public_url; break }
            } catch { Write-Verbose $_ }
            Start-Sleep -Milliseconds 500
        }
        if (-not $publicUrl) { throw 'No matching ngrok HTTPS tunnel appeared; check ngrok logs and authentication.' }
        $state.url = "$publicUrl/mcp"
        Write-InstanceState $statePath $state
        if (-not $SkipVerify) {
            & $python (Join-Path $PSScriptRoot 'verify_mcp.py') $state.url @verifyArguments --report (Join-Path $instanceDir 'verification-public.json')
            if ($LASTEXITCODE -ne 0) { throw 'Public MCP verification failed.' }
        }
    }
    $state.status = 'running'
    Write-InstanceState $statePath $state
    Write-Host "MCP URL: $($state.url)"
    Write-Host "Administrator: $elevated | Authentication: None | Tools: full upstream set + laptop extensions"
    Write-Host "State and logs: $instanceDir"
    Write-Host "Stop: .\scripts\stop-demo.ps1 -Port $Port"
} catch {
    if ($null -ne $state) {
        Stop-OwnedProcesses $state.processes
        $state.status = 'failed'
        Write-InstanceState $statePath $state
    }
    throw
} finally {
    foreach ($name in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], 'Process') }
    $instanceLock.Dispose()
}
