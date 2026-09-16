"""First-run checks and installation, usable before third-party packages exist."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path


def powershell(script: Path, *arguments: str) -> list[str]:
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return [
        str(shell),
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *arguments,
    ]


def runtime_ready(root: Path) -> bool:
    python = root / ".venv/Scripts/python.exe"
    if not python.is_file():
        return False
    try:
        lock = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
        if (root / ".run/installed-lock.sha256").read_text().strip().lower() != lock:
            return False
        probe = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; sys.path.insert(0, 'scripts'); import tkinter, laptop_tools, file_delivery; from windows_mcp.__main__ import _build_mcp; from importlib.metadata import version; assert version('windows-mcp') == '0.8.5'",
            ],
            cwd=root,
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Onboarding:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.root
        self.log_path = self.root / ".run/manager/setup.log"

    def inspect(self, settings) -> dict:
        issues = []
        writable = True
        try:
            with tempfile.TemporaryFile(dir=self.root):
                pass
        except OSError:
            writable = False
            issues.append("Move the extracted project to a writable folder, such as Documents.")
        runtime = runtime_ready(self.root)
        if not runtime:
            issues.append("Install or repair the MCP runtime below.")
        ngrok = {
            "installed": False,
            "configured": False,
            "valid": False,
            "detail": "Not needed for a local connection.",
        }
        if settings.remote:
            try:
                result = subprocess.run(
                    powershell(self.root / "scripts/ngrok-status.ps1"),
                    capture_output=True,
                    timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                ngrok = json.loads(result.stdout.decode("utf-8-sig"))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                ngrok["detail"] = "Could not check ngrok. Retry, or select This computer only."
            if not ngrok["installed"]:
                issues.append("Install ngrok below.")
            elif not ngrok["configured"] or not ngrok["valid"]:
                issues.append("Add your ngrok authtoken below.")
        snapshot = self.manager.snapshot()
        if not snapshot["has_processes"]:
            for port in (
                [self.manager.port, self.manager.port + 1, 4040]
                if settings.remote
                else [self.manager.port, self.manager.port + 1]
            ):
                try:
                    with socket.socket() as connection:
                        connection.bind(("127.0.0.1", port))
                except OSError:
                    issues.append(
                        f"Port {port} is already in use. Close the app using it, then check again."
                    )
        return {
            "runtime": runtime,
            "ngrok": ngrok,
            "writable": writable,
            "issues": issues,
            "ready": not issues,
            "running": snapshot["has_processes"],
        }

    def install(self, settings, progress=lambda _message: None) -> None:
        if self.manager.snapshot()["has_processes"]:
            raise RuntimeError("Stop MCP before installing or repairing its runtime.")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as log:
            steps = [
                ("Installing Python and the locked MCP runtime…", "setup.ps1", ["-Production"])
            ]
            if settings.remote:
                steps.append(("Installing ngrok if it is missing…", "install-ngrok.ps1", []))
            for message, script, args in steps:
                progress(message)
                result = subprocess.run(
                    powershell(self.root / "scripts" / script, *args),
                    cwd=self.root,
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode:
                    raise RuntimeError(
                        "Installation could not finish. Open Setup log, check your connection, then retry. You can also select This computer only to skip ngrok."
                    )

    def save_token(self, token: str) -> None:
        token = token.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
            raise ValueError("Paste only your ngrok authtoken, not the command or an API key.")
        result = subprocess.run(
            powershell(self.root / "scripts/configure-ngrok.ps1"),
            input=token.encode("ascii"),
            capture_output=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(
                "Could not save the authtoken. Check that the project folder is writable."
            )

    def shortcut(self):
        result = subprocess.run(
            powershell(self.root / "scripts/create-shortcut.ps1"),
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(
                "Could not create the Desktop shortcut. You can still double-click Open MCP Manager.cmd."
            )
