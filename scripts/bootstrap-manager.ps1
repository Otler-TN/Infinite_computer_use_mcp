param([switch]$PrepareOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap-common.ps1')
. (Join-Path $PSScriptRoot 'common.ps1')
$taskRoot = Split-Path -Parent $PSScriptRoot
try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This release requires 64-bit Intel/AMD Windows 10 or 11.' }
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'uv.lock'))) { throw 'Extract the whole downloaded ZIP first, then open the manager from the extracted folder.' }
    Set-Location -LiteralPath $taskRoot
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot '.run\manager') | Out-Null
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    $taskProbe = 'import sys, tkinter; assert sys.version_info >= (3, 10); w=tkinter.Tk(); w.withdraw(); w.update(); w.destroy()'
    $taskReady = $false
    if (Test-Path -LiteralPath $taskPython) {
        $taskProbeProcess = Start-Process -FilePath $taskPython -ArgumentList @('-c', (ConvertTo-NativeArgument $taskProbe)) -WindowStyle Hidden -Wait -PassThru -RedirectStandardError (Join-Path $taskRoot '.run\manager\python-probe.log')
        $taskReady = $taskProbeProcess.ExitCode -eq 0
        if ($taskReady) {
            # Run the GUI outside .venv so Install / repair can replace that environment.
            $taskPython = (& $taskPython -c 'import sys; print(sys._base_executable)').Trim()
        }
    }
    if (-not $taskReady) {
        Write-Host 'Preparing Infinite Computer Use MCP Manager. The first launch needs an internet connection.'
        $taskUv = Get-SetupUv $taskRoot
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot '.run\python'
        Write-Host 'Installing Python and its window toolkit. No system Python changes are needed.'
        & $taskUv python install 3.13 --no-registry --no-bin
        if ($LASTEXITCODE -ne 0) { throw 'Python download failed. Check your internet connection, then open the manager again.' }
        $taskPython = (& $taskUv python find 3.13 --managed-python --no-project).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'The downloaded Python could not be located.' }
        & $taskPython -c $taskProbe
        if ($LASTEXITCODE -ne 0) { throw 'Python window support failed. Install Python 3.13 with Tcl/Tk from python.org, or retry the download.' }
    }
    [IO.File]::WriteAllText((Join-Path $taskRoot '.run\manager\bootstrap-python.txt'), $taskPython)
    if (-not $PrepareOnly) {
        $taskPythonw = Join-Path (Split-Path -Parent $taskPython) 'pythonw.exe'
        Start-Process -FilePath $taskPythonw -ArgumentList (ConvertTo-NativeArgument (Join-Path $taskRoot 'launcher.pyw')) -WorkingDirectory $taskRoot
    }
} catch {
    Write-Host "`nSetup could not finish: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host 'Extract the project into a writable folder, such as Documents. Retry when connected to the internet.'
    if (-not $PrepareOnly) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}
