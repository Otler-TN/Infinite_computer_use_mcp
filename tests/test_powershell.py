import json
import shutil
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [p for p in (shutil.which("powershell"), shutil.which("pwsh")) if p]


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_script(shell, tmp_path, code):
    script = tmp_path / "script with spaces.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n" + code, encoding="utf-8-sig")
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("shell", SHELLS)
def test_all_scripts_parse_on_supported_shells(shell, tmp_path):
    run_script(
        shell,
        tmp_path,
        f"""
    Get-ChildItem -LiteralPath {ps_string(ROOT / "scripts")} -Filter *.ps1 | ForEach-Object {{
        $errors = $null
        [Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$errors) | Out-Null
        if ($errors) {{ throw ($errors | Out-String) }}
    }}
    """,
    )


@pytest.mark.parametrize("shell", SHELLS)
def test_native_arguments_round_trip_without_shell_interpretation(shell, tmp_path):
    probe = tmp_path / "arguments probe.py"
    probe.write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
    arguments = [
        "",
        "two words",
        'embedded "quote"',
        "trailing\\",
        "space and trailing\\",
        "é漢字",
        "$(Write-Output oops)",
        "`backtick`",
        "single'quote",
    ]
    stdout = tmp_path / "argv.json"
    code = f"""
    . {ps_string(ROOT / "scripts" / "common.ps1")}
    $argv = @({",".join(ps_string(x) for x in [str(probe), *arguments])})
    $native = ($argv | ForEach-Object {{ ConvertTo-NativeArgument $_ }}) -join ' '
    $child = Start-Process -FilePath {ps_string(sys.executable)} -ArgumentList $native -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput {ps_string(stdout)}
    if ($child.ExitCode -ne 0) {{ throw 'Argument probe failed' }}
    """
    run_script(shell, tmp_path, code)
    assert json.loads(stdout.read_text(encoding="utf-8-sig")) == arguments


@pytest.mark.parametrize("shell", SHELLS)
def test_stop_uses_process_identity_and_preserves_unrelated_process(shell, tmp_path):
    owned = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        code = f"""
        . {ps_string(ROOT / "scripts" / "common.ps1")}
        $record = Get-ProcessIdentity -ProcessId {owned.pid} -Role 'fixture'
        $record = $record | ConvertTo-Json | ConvertFrom-Json
        if (-not (Test-ProcessIdentity $record)) {{ throw 'JSON round trip changed identity' }}
        $ticks = $record.startedTicks
        $record.startedTicks = '0'
        Stop-OwnedProcesses @($record)
        if (-not (Get-Process -Id {owned.pid} -ErrorAction SilentlyContinue)) {{ throw 'Stopped mismatched identity' }}
        $record.startedTicks = $ticks
        Stop-OwnedProcesses @($record)
        if (Test-ProcessIdentity $record) {{ throw 'Owned process survived' }}
        if (-not (Get-Process -Id {unrelated.pid} -ErrorAction SilentlyContinue)) {{ throw 'Unrelated process was stopped' }}
        Stop-OwnedProcesses @()
        """
        run_script(shell, tmp_path, code)
        assert unrelated.poll() is None
    finally:
        for process in (owned, unrelated):
            if process.poll() is None:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                process.kill()
            process.wait(timeout=5)
