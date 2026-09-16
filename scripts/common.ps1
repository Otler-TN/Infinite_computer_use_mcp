Set-StrictMode -Version Latest
# A GUI opened from PowerShell 7 can inherit incompatible modules into Windows PowerShell 5.1.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    $env:PSModulePath = "$(Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WindowsPowerShell\Modules');$env:ProgramFiles\WindowsPowerShell\Modules;$PSHOME\Modules"
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    # Start-Process joins ArgumentList without quoting. Use Windows CRT quoting.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Get-RuntimePython {
    param([string]$RepoRoot)
    $python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Runtime missing. Run .\scripts\setup.ps1 first.' }
    return $python
}

function Resolve-NgrokExecutable {
    param([string]$RepoRoot, [string]$ExplicitPath = '')
    if ($ExplicitPath) { return (Resolve-Path -LiteralPath $ExplicitPath).Path }
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $local = Join-Path $RepoRoot '.run\tools\ngrok.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    $winget = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $winget) {
        $candidate = Get-ChildItem -LiteralPath $winget -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    throw 'ngrok is missing. Install ngrok, supply -NgrokPath, or use -LocalOnly.'
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-NgrokConfigArguments {
    param([string]$RepoRoot)
    $privateConfig = Join-Path $RepoRoot '.run\private\ngrok.yml'
    if (Test-Path -LiteralPath $privateConfig) { return @('--config', $privateConfig) }
    return @()
}

function Get-NgrokSetupStatus {
    param([string]$RepoRoot)
    $result = @{ installed = $false; configured = $false; valid = $false; path = ''; detail = 'Install ngrok to enable remote connections.' }
    try {
        $ngrok = Resolve-NgrokExecutable $RepoRoot
        $result.installed = $true
        $result.path = $ngrok
        $options = @(Get-NgrokConfigArguments $RepoRoot)
        $output = (& $ngrok config check @options 2>&1 | Out-String)
        $result.valid = $LASTEXITCODE -eq 0
        if ($result.valid -and $output -match 'Valid configuration file at\s+([^\r\n]+)') {
            $configPath = $Matches[1].Trim()
            # Store installations virtualize LOCALAPPDATA while reporting its original path.
            if (-not (Test-Path -LiteralPath $configPath) -and $configPath.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) {
                $package = Get-AppxPackage -Name ngrok.ngrok -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($package) {
                    $suffix = $configPath.Substring($env:LOCALAPPDATA.Length).TrimStart('\')
                    $configPath = Join-Path $env:LOCALAPPDATA "Packages\$($package.PackageFamilyName)\LocalCache\Local\$suffix"
                }
            }
            $config = Get-Content -Raw -LiteralPath $configPath
            $result.configured = [bool]($config -match '(?m)^\s*authtoken:\s*[\x22\x27]?[A-Za-z0-9_-]{16,}')
        }
        if ($env:NGROK_AUTHTOKEN) { $result.configured = $true }
        $result.detail = if ($result.configured) { 'Authtoken found. Account access is checked when MCP starts.' } else { 'Add your ngrok authtoken below, or choose This computer only.' }
    } catch { $result.detail = 'ngrok needs setup. Install it or add a valid authtoken below.' }
    return $result
}

function Get-ProcessIdentity {
    param([int]$ProcessId, [string]$Role)
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    return [pscustomobject]@{
        role = $Role; pid = $ProcessId
        startedTicks = [string]$process.StartTime.ToUniversalTime().Ticks
        executable = $process.Path
    }
}

function Test-ProcessIdentity {
    param($Record)
    $current = Get-ProcessIdentity -ProcessId $Record.pid -Role $Record.role
    return ($null -ne $current -and $current.startedTicks -eq $Record.startedTicks -and $current.executable -eq $Record.executable)
}

function Stop-OwnedProcesses {
    param($Records)
    $recordsArray = @($Records | ForEach-Object { $_ })
    for ($recordIndex = $recordsArray.Count - 1; $recordIndex -ge 0; $recordIndex--) {
        $record = $recordsArray[$recordIndex]
        if ($null -eq $record -or -not (Test-ProcessIdentity $record)) { continue }
        $all = @(Get-CimInstance Win32_Process)
        $root = $all | Where-Object ProcessId -eq $record.pid | Select-Object -First 1
        if (-not $root) { continue }
        $pending = [System.Collections.Generic.List[object]]::new()
        $pending.Add($root)
        for ($index = 0; $index -lt $pending.Count; $index++) {
            $parent = $pending[$index]
            foreach ($child in @($all | Where-Object { $_.ParentProcessId -eq $parent.ProcessId -and $_.CreationDate -ge $parent.CreationDate })) {
                $pending.Add($child)
            }
        }
        for ($index = $pending.Count - 1; $index -ge 0; $index--) {
            $candidate = $pending[$index]
            $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.ProcessId)" -ErrorAction SilentlyContinue
            if ($current -and $current.CreationDate -eq $candidate.CreationDate) {
                Stop-Process -Id $candidate.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Write-InstanceState {
    param([string]$Path, $State)
    $temp = "$Path.tmp"
    $json = $State | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($temp, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Get-PortListener {
    param([int]$Port)
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Assert-PortAvailable {
    param([int]$Port)
    $listener = Get-PortListener $Port
    if ($listener) { throw "Port $Port is in use by PID $($listener.OwningProcess). Choose another port or stop its owning instance." }
}

function Wait-ManagedPort {
    param([int]$Port, [Diagnostics.Process]$Process, [int]$TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) { throw "Process $($Process.Id) exited with code $($Process.ExitCode) before port $Port became ready. Check its error log." }
        $listener = Get-PortListener $Port
        if ($listener) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
            if ($owner.ProcessId -eq $Process.Id -or $owner.ParentProcessId -eq $Process.Id) {
                return $listener.OwningProcess
            }
            throw "Port $Port was acquired by an unrelated process during startup."
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Process $($Process.Id) did not listen on port $Port within $TimeoutSeconds seconds."
}
