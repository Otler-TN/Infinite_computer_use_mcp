import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import manager_backend
import onboarding
import pytest
from manager_backend import ElevationCancelled, Manager, Settings, process_matches
from mcp_manager import ManagerWindow


def write_state(manager, state):
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(json.dumps(state), encoding="utf-8")


def test_process_identity_rejects_reused_pid_and_wrong_executable():
    process = subprocess.Popen(
        [sys._base_executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        # Obtain the exact same .NET timestamp representation used by lifecycle scripts.
        script = f"$p = Get-Process -Id {process.pid}; @{{pid=$p.Id; startedTicks=[string]$p.StartTime.ToUniversalTime().Ticks; executable=$p.Path}} | ConvertTo-Json -Compress"
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        record = json.loads(output)
        assert process_matches(record) is True
        assert (
            process_matches({**record, "startedTicks": str(int(record["startedTicks"]) + 1)})
            is False
        )
        assert process_matches({**record, "executable": r"C:\unrelated.exe"}) is False
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert process_matches(record) is False


def test_settings_inherit_running_instance_and_persist(tmp_path):
    manager = Manager(tmp_path)
    assert manager.load_settings() == Settings()
    write_state(
        manager, {"status": "running", "elevated": False, "url": "http://127.0.0.1:8011/mcp"}
    )
    assert manager.load_settings() == Settings(False, False)
    manager.save_settings(Settings(True, False))
    assert Manager(tmp_path).load_settings() == Settings(True, False)
    manager.settings_path.write_text("[]", encoding="utf-8")
    assert manager.load_settings() == Settings(False, False)


def test_status_requires_live_owned_processes_and_listener(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    write_state(
        manager, {"status": "running", "url": "https://example.test/mcp", "processes": [{"pid": 1}]}
    )
    monkeypatch.setattr(manager_backend, "process_matches", lambda _record: False)
    assert manager.snapshot()["status"] == "stopped"
    assert manager.snapshot()["url"] is None
    monkeypatch.setattr(manager_backend, "process_matches", lambda _record: True)

    def refuse(*_args, **_kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(manager_backend.socket, "create_connection", refuse)
    assert manager.snapshot()["status"] == "needs_attention"
    assert manager.snapshot()["has_processes"]
    assert manager.snapshot()["url"] is None


def test_status_uses_listener_health_even_if_launcher_exits(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    write_state(
        manager,
        {
            "status": "running",
            "url": "https://example.test/mcp",
            "proxyPort": 8011,
            "processes": [
                {"pid": 10, "role": "server"},
                {"pid": 11, "role": "proxy-listener"},
                {"pid": 12, "role": "ngrok"},
                {"pid": 13, "role": "ngrok-listener"},
            ],
        },
    )

    def match(record):
        return record.get("role") not in {"server", "ngrok"}

    monkeypatch.setattr(manager_backend, "process_matches", match)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(manager_backend.socket, "create_connection", lambda *_a, **_k: Connection())
    snapshot = manager.snapshot()
    assert snapshot["status"] == "running"
    assert snapshot["url"] == "https://example.test/mcp"


@pytest.mark.parametrize(
    "action,settings,existing_admin,elevates",
    [
        ("start", Settings(True, True), False, True),
        ("start", Settings(False, False), False, False),
        ("stop", Settings(False, False), True, True),
        ("stop", Settings(True, True), False, False),
        ("verify", Settings(True, True), True, False),
    ],
)
def test_actions_use_required_privileges_and_preserve_argv(
    tmp_path, monkeypatch, action, settings, existing_admin, elevates
):
    manager = Manager(tmp_path / "a space & quote's path")
    write_state(manager, {"elevated": existing_admin})
    calls = []
    monkeypatch.setattr(onboarding.Onboarding, "inspect", lambda *_: {"ready": True})
    monkeypatch.setattr(manager_backend, "is_administrator", lambda: False)

    def complete(kind, arguments):
        calls.append((kind, arguments))
        directory = manager.run_dir / "operations"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{manager.operation_id}.json").write_text(
            json.dumps({"status": "complete", "message": "Done"}), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(
        manager_backend, "run_elevated", lambda _exe, args, _cwd: complete("elevated", args)
    )
    monkeypatch.setattr(
        manager_backend.subprocess, "call", lambda args, **_kwargs: complete("regular", args[1:])
    )
    assert manager.execute(action, settings)["status"] == "complete"
    kind, arguments = calls[0]
    assert kind == ("elevated" if elevates else "regular")
    assert arguments[arguments.index("-File") + 1] == str(
        manager.root / "scripts/manage-instance.ps1"
    )
    assert ("-LocalOnly" in arguments) is not settings.remote
    assert ("-RequireAdministrator" in arguments) is elevates


def test_uac_cancel_propagates_without_starting_another_process(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    monkeypatch.setattr(onboarding.Onboarding, "inspect", lambda *_: {"ready": True})
    monkeypatch.setattr(manager_backend, "is_administrator", lambda: False)

    def cancel(*_args):
        raise ElevationCancelled("Cancelled")

    monkeypatch.setattr(manager_backend, "run_elevated", cancel)
    with pytest.raises(ElevationCancelled):
        manager.execute("start", Settings())
    assert not manager.operation_status()
    with pytest.raises(ValueError):
        manager.execute("start; arbitrary command", Settings())


class FakeManager:
    operation_id = None
    root = Path.cwd()

    def __init__(self):
        self.started = threading.Event()
        self.finish = threading.Event()
        self.calls = []
        self.alive = True

    def load_settings(self):
        return Settings()

    def snapshot(self):
        return {
            "status": "running" if self.alive else "stopped",
            "has_processes": self.alive,
            "url": "https://example.test/mcp" if self.alive else None,
            "remote": True,
            "elevated": True,
        }

    def execute(self, action, _settings):
        self.calls.append(action)
        self.started.set()
        self.finish.wait(timeout=5)
        if action == "stop":
            self.alive = False
        return {"status": "complete", "message": "Done"}


def test_ui_disables_duplicate_actions_and_closing_preserves_server():
    root = tk.Tk()
    root.withdraw()
    manager = FakeManager()
    ui = ManagerWindow(root, manager, start_polling=False)
    try:
        ui.current = manager.snapshot()
        ui.render_status()
        assert ui.start_button.instate(["disabled"])
        assert not ui.stop_button.instate(["disabled"])
        ui.run_action("verify")
        assert manager.started.wait(timeout=2)
        assert ui.stop_button.instate(["disabled"])
        ui.run_action("stop")
        assert manager.calls == ["verify"]
        manager.finish.set()
        deadline = time.monotonic() + 3
        while ui.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert ui.busy is None
        assert not ui.copy_button.instate(["disabled"])
    finally:
        manager.finish.set()
        ui.close()
    assert manager.alive and manager.calls == ["verify"]


def test_ui_offers_one_click_restart_for_attention_state():
    root = tk.Tk()
    root.withdraw()
    manager = FakeManager()
    ui = ManagerWindow(root, manager, start_polling=False)
    try:
        ui.current = {
            "status": "needs_attention",
            "has_processes": True,
            "url": None,
            "remote": True,
            "elevated": True,
        }
        ui.render_status()
        assert ui.start_button.cget("text") == "Restart MCP"
        assert not ui.start_button.instate(["disabled"])
    finally:
        manager.finish.set()
        ui.close()


def test_manager_is_standard_library_only():
    # A launcher must open before the MCP virtual environment has been installed.
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, 'scripts'); import mcp_manager",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
