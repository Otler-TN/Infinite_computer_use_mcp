import hashlib
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import onboarding
import pytest
from manager_backend import Manager, Settings
from onboarding import Onboarding, powershell, runtime_ready


def test_missing_setup_blocks_start_before_uac(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    monkeypatch.setattr(
        Onboarding, "inspect", lambda *_: {"ready": False, "issues": ["Add your ngrok authtoken."]}
    )
    result = manager.execute("start", Settings())
    assert result["status"] == "setup_required"
    assert "authtoken" in result["message"]
    assert manager.operation_id is None


def test_local_mode_never_requires_ngrok_and_reports_occupied_port(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        manager = Manager(tmp_path, port)
        monkeypatch.setattr(onboarding, "runtime_ready", lambda _: True)
        monkeypatch.setattr(
            onboarding.subprocess,
            "run",
            lambda *_a, **_k: pytest.fail("ngrok must not be checked in local mode"),
        )
        result = Onboarding(manager).inspect(Settings(False, False))
        assert not result["ready"]
        assert any(str(port) in issue for issue in result["issues"])
        assert not any("ngrok" in issue for issue in result["issues"])


def test_remote_config_without_token_is_not_ready(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    monkeypatch.setattr(manager, "snapshot", lambda: {"has_processes": True})
    monkeypatch.setattr(onboarding, "runtime_ready", lambda _: True)
    monkeypatch.setattr(
        onboarding.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            stdout=json.dumps({"installed": True, "configured": False, "valid": True}).encode()
        ),
    )
    result = Onboarding(manager).inspect(Settings())
    assert not result["ready"] and "authtoken" in " ".join(result["issues"])


def test_runtime_checks_imports_and_lockfile(tmp_path, monkeypatch):
    python = tmp_path / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / ".run").mkdir()
    (tmp_path / "uv.lock").write_text("lock")
    marker = tmp_path / ".run/installed-lock.sha256"
    marker.write_text(hashlib.sha256(b"lock").hexdigest())
    monkeypatch.setattr(
        onboarding.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1)
    )
    assert not runtime_ready(tmp_path)
    monkeypatch.setattr(
        onboarding.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0)
    )
    assert runtime_ready(tmp_path)
    (tmp_path / "uv.lock").write_text("updated lock")
    assert not runtime_ready(tmp_path)


def test_token_uses_stdin_and_errors_do_not_expose_it(tmp_path, monkeypatch):
    secret = "fixture_" + "x" * 24
    service = Onboarding(Manager(tmp_path))
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout=secret.encode(), stderr=secret.encode())

    monkeypatch.setattr(onboarding.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        service.save_token(secret)
    assert secret not in str(error.value)
    assert all(secret not in arg for arg in calls[0][0])
    assert calls[0][1]["input"] == secret.encode()
    with pytest.raises(ValueError):
        service.save_token("ngrok config add-authtoken " + secret)
    assert len(calls) == 1


def test_token_file_has_restricted_acl_and_shared_config_selection(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("configure-ngrok.ps1", "common.ps1"):
        shutil.copyfile(root / "scripts" / name, scripts / name)
    secret = "fixture_" + "z" * 24
    service = Onboarding(Manager(tmp_path))
    service.save_token(secret)
    config = tmp_path / ".run/private/ngrok.yml"
    assert secret in config.read_text()
    script = scripts / "check.ps1"
    script.write_text(
        ". (Join-Path $PSScriptRoot 'common.ps1')\n$r = Split-Path -Parent $PSScriptRoot\n$p = Join-Path $r '.run/private'\n@{ protected=(Get-Acl -LiteralPath $p).AreAccessRulesProtected; args=@(Get-NgrokConfigArguments $r) } | ConvertTo-Json -Compress",
        encoding="utf-8",
    )
    result = subprocess.run(powershell(script), capture_output=True, timeout=20)
    report = json.loads(result.stdout)
    assert report["protected"]
    assert report["args"] == ["--config", str(config)]
    assert secret.encode() not in result.stdout + result.stderr


def test_install_cannot_repair_a_running_server(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    monkeypatch.setattr(manager, "snapshot", lambda: {"has_processes": True})
    with pytest.raises(RuntimeError, match="Stop MCP"):
        Onboarding(manager).install(Settings())
