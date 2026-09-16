"""Standard-library controller for the desktop manager; existing scripts own lifecycle."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Settings:
    remote: bool = True
    administrator: bool = True


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def is_administrator() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def process_matches(record: dict) -> bool | None:
    """Read-only identity check, including elevated processes, without psutil."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    try:
        pid = int(record["pid"])
        expected_ticks = int(record["startedTicks"])
        expected_path = record["executable"]
    except (KeyError, TypeError, ValueError):
        return False
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None if ctypes.get_last_error() == 5 else False
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        if exited.dwHighDateTime or exited.dwLowDateTime:
            return False
        # Manifest stores .NET ticks (year 1); Windows FILETIME starts in 1601.
        epoch_ticks = (datetime(1601, 1, 1) - datetime(1, 1, 1)).days * 86400 * 10_000_000
        ticks = (created.dwHighDateTime << 32) + created.dwLowDateTime + epoch_ticks
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return None
        return ticks == expected_ticks and os.path.normcase(path.value) == os.path.normcase(
            expected_path or ""
        )
    finally:
        kernel.CloseHandle(handle)


class ElevationCancelled(Exception):
    pass


def run_elevated(executable: str, arguments: list[str], cwd: Path) -> int:
    """Ask Windows for elevation; keep only the operation helper elevated."""

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.lpDirectory = str(cwd)
    info.nShow = 0  # SW_HIDE; Windows controls the UAC prompt.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise ElevationCancelled("Administrator request cancelled. Nothing changed.")
        raise ctypes.WinError(error)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        while kernel.WaitForSingleObject(info.hProcess, 250) == 0x102:
            pass
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel.CloseHandle(info.hProcess)


class Manager:
    def __init__(self, root: Path, port: int = 8010):
        if not 1 <= port <= 65534:
            raise ValueError("Port must be between 1 and 65534")
        self.root = root.resolve()
        self.port = port
        self.run_dir = self.root / ".run" / "manager"
        self.state_path = self.root / ".run" / "instances" / str(port) / "state.json"
        self.settings_path = self.run_dir / "settings.json"
        self.operation_id: str | None = None

    def load_settings(self) -> Settings:
        saved = read_json(self.settings_path)
        if saved:
            return Settings(
                remote=bool(saved.get("remote", True)),
                administrator=bool(saved.get("administrator", True)),
            )
        current = read_json(self.state_path)
        if current.get("status") == "running":
            return Settings(
                remote=str(current.get("url", "")).startswith("https://"),
                administrator=bool(current.get("elevated")),
            )
        return Settings()

    def save_settings(self, settings: Settings):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        pending = self.settings_path.with_suffix(".tmp")
        pending.write_text(json.dumps(asdict(settings)), encoding="utf-8")
        pending.replace(self.settings_path)

    def snapshot(self) -> dict:
        state = read_json(self.state_path)
        records = [record for record in state.get("processes", []) if isinstance(record, dict)]
        checked = [(record, process_matches(record)) for record in records]
        present = any(match is not False for _record, match in checked)

        # A component launcher may legitimately exit after handing the listening socket to a
        # child process (this is especially common with packaged ngrok builds).  The old
        # health check required every launcher PID to remain alive forever, which could turn
        # a healthy remote instance into "Needs attention" and hide its working URL.
        listener_records = [
            (record, match)
            for record, match in checked
            if str(record.get("role", "")).endswith("-listener")
        ]
        if listener_records:
            endpoint_role = "proxy-listener" if state.get("proxyPort") else "server-listener"
            required_roles = {endpoint_role}
            remote = str(state.get("url", "")).startswith("https://")
            if remote:
                required_roles.add("ngrok-listener")
            listeners_ready = all(
                any(
                    record.get("role") == role and match is not False
                    for record, match in listener_records
                )
                for role in required_roles
            )
        else:
            # Backward compatibility for state files created by older releases.
            listeners_ready = bool(checked) and all(
                match is not False for _record, match in checked
            )

        running = state.get("status") == "running" and listeners_ready
        if running:
            try:
                with socket.create_connection(
                    ("127.0.0.1", state.get("proxyPort") or self.port), timeout=0.4
                ):
                    pass
            except OSError:
                running = False
        status = "running" if running else ("needs_attention" if present else "stopped")
        if state.get("status") == "starting" and present:
            status = "starting"
        return {
            "status": status,
            "url": state.get("url") if running else None,
            "elevated": bool(state.get("elevated")),
            "has_processes": present,
            "remote": str(state.get("url", "")).startswith("https://"),
        }

    def operation_status(self) -> dict:
        if not self.operation_id:
            return {}
        return read_json(self.run_dir / "operations" / f"{self.operation_id}.json")

    def execute(self, action: str, settings: Settings) -> dict:
        if action not in {"start", "stop", "verify"}:
            raise ValueError("Unknown manager action")
        if action == "start":
            from onboarding import Onboarding

            report = Onboarding(self).inspect(settings)
            if not report["ready"]:
                return {
                    "status": "setup_required",
                    "message": "Finish Setup before starting MCP. " + " ".join(report["issues"]),
                }
        self.save_settings(settings)
        self.operation_id = uuid.uuid4().hex
        shell = str(
            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        args = [
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.root / "scripts/manage-instance.ps1"),
            "-Action",
            action,
            "-OperationId",
            self.operation_id,
            "-Port",
            str(self.port),
            "-PythonPath",
            str(python),
        ]
        if not settings.remote:
            args.append("-LocalOnly")
        existing_elevated = bool(read_json(self.state_path).get("elevated"))
        need_admin = (action == "start" and settings.administrator) or (
            action == "stop" and existing_elevated
        )
        if need_admin:
            args.append("-RequireAdministrator")
        if need_admin and not is_administrator():
            code = run_elevated(shell, args, self.root)
        else:
            code = subprocess.call(
                [shell, *args],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        result = self.operation_status()
        if not result or result.get("status") == "running":
            result = {
                "status": "failed",
                "message": f"The operation ended unexpectedly (code {code}). Open Logs for details.",
            }
        return result

    def logs_path(self) -> Path:
        last = self.operation_status().get("log")
        if last and Path(last).is_file():
            return Path(last)
        directory = self.root / ".run" / "instances" / str(self.port)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
