"""Additional OS capabilities. Paths and commands have no application-level sandbox."""

from __future__ import annotations

import atexit
import base64
import codecs
import ctypes
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import psutil
import win32api
import win32con
import win32job
import win32process


class ProcessGroup:
    """Own a Windows Job Object; descendants remain tracked after their parent exits."""

    def __init__(self):
        self.lock = threading.Lock()
        self.handle = win32job.CreateJobObject(None, "windows-mcp-" + uuid.uuid4().hex)
        info = win32job.QueryInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation, info
        )

    def attach_and_resume(self, process):
        # Start suspended so even very short commands cannot spawn untracked children.
        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
        )
        try:
            win32job.AssignProcessToJobObject(self.handle, handle)
        finally:
            handle.Close()
        threads = psutil.Process(process.pid).threads()
        if len(threads) != 1:
            raise RuntimeError("Cannot identify the suspended process's initial thread")
        thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
        try:
            win32process.ResumeThread(thread)
        finally:
            thread.Close()

    def active(self):
        with self.lock:
            if self.handle is None:
                return 0
            return win32job.QueryInformationJobObject(
                self.handle, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]

    def terminate(self):
        with self.lock:
            if self.handle is not None:
                win32job.TerminateJobObject(self.handle, 1)

    def close(self):
        with self.lock:
            if self.handle is not None:
                self.handle.Close()
                self.handle = None


def absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute path, including the drive letter or UNC share")
    return path.resolve()


def computer_capabilities() -> dict:
    """Report facts without reading environment values, credentials, or user files."""
    from file_delivery import configured_base_url

    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    session = wintypes.DWORD()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    desktop = user32.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS
    desktop_name = None
    if desktop:
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if user32.GetUserObjectInformationW(
                desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                desktop_name = name.value
        finally:
            user32.CloseDesktop(desktop)
    return {
        "ok": True,
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "versions": {name: version(name) for name in ("windows-mcp", "fastmcp", "psutil")},
        "server_pid": os.getpid(),
        "session_id": session.value,
        "elevated": elevated,
        "input_desktop": desktop_name,
        "interactive_desktop_available": desktop_name == "Default" and session.value != 0,
        "home_directory": str(Path.home()),
        "drives": [p.mountpoint for p in psutil.disk_partitions()],
        "executables": {
            n: shutil.which(n)
            for n in ("pwsh", "powershell", "cmd", "python", "git", "node", "winget")
        },
        "command_allowlist": None,
        "filesystem_sandbox": None,
        "command_jobs_survive_client_disconnect": True,
        "command_jobs_survive_server_restart": False,
        "terminal_emulation": False,
        "file_delivery": {
            "tool": "UploadFile",
            "http_download_links": bool(configured_base_url()),
            "mcp_resources": True,
            "native_chat_attachment": "client-controlled",
        },
        "limits": [
            "All operations use this process's Windows access token and installed software.",
            "Administrator operations require launching the server from an elevated PowerShell.",
            "UAC secure desktop, locked sessions, protected processes and inaccessible ACLs remain Windows boundaries.",
            "Apps must expose usable UI automation or accept mouse/keyboard input; some protected surfaces cannot be captured.",
            "Agents share one desktop, clipboard and filesystem; coordinate concurrent UI actions.",
            "Client tool permissions, context limits and supported MCP features remain client-controlled.",
        ],
    }


@dataclass
class Job:
    id: str
    process: subprocess.Popen
    directory: Path
    started_at: float
    timeout_seconds: float | None
    group: ProcessGroup
    inputs: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=16))
    stdin_closed: bool = False
    stdin_error: str | None = None
    timed_out: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class CommandJobs:
    def __init__(self, root: Path):
        self.root = root
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(
        self,
        *,
        command=None,
        program=None,
        arguments=None,
        cwd=None,
        env=None,
        timeout_seconds=None,
        wait_seconds=1,
        max_bytes=65536,
        **_,
    ) -> dict:
        if bool(command) == bool(program):
            raise ValueError(
                "Provide exactly one of command (PowerShell script) or program (executable)"
            )
        if command and arguments:
            raise ValueError("arguments is only valid with program")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive, or null for no execution deadline")
        self._validate_read(0, 0, max_bytes, wait_seconds)
        working_dir = absolute_path(cwd) if cwd else Path.home()
        if not working_dir.is_dir():
            raise ValueError("cwd must be an existing directory")
        from windows_mcp.powershell.service import _prepare_env

        child_env = _prepare_env()
        child_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"})
        child_env.update(env or {})
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True)
        if command:
            shell = shutil.which("pwsh", path=child_env.get("PATH")) or shutil.which(
                "powershell", path=child_env.get("PATH")
            )
            if not shell:
                raise FileNotFoundError("PowerShell was not found")
            script = directory / "command.ps1"
            # A file avoids Windows' command-line length limit. BOM also supports PS 5.1.
            script.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                + command
                + "\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n",
                encoding="utf-8-sig",
            )
            args = [
                shell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ]
        else:
            executable = shutil.which(program, path=child_env.get("PATH")) or program
            args = [executable, *(arguments or [])]
        with (
            (directory / "stdout.bin").open("wb") as stdout,
            (directory / "stderr.bin").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=working_dir,
                env=child_env,
                creationflags=subprocess.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED,
                shell=False,
            )
        group = None
        try:
            group = ProcessGroup()
            group.attach_and_resume(process)
        except Exception:
            process.kill()
            process.wait()
            process.stdin.close()
            if group is not None:
                group.close()
            raise
        job = Job(job_id, process, directory, time.time(), timeout_seconds, group)
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._input_worker, args=(job,), daemon=True).start()
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return self.read(job_id, wait_seconds=wait_seconds, max_bytes=max_bytes)

    def get(self, job_id: str) -> Job:
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Unknown job_id (jobs belong to this server instance)")
            return self.jobs[job_id]

    def _watch(self, job: Job):
        try:
            deadline = time.monotonic() + job.timeout_seconds if job.timeout_seconds else None
            while job.group.active():
                if deadline is not None and time.monotonic() >= deadline:
                    job.timed_out = True
                    self.terminate(job.id)
                    break
                time.sleep(0.05)
        finally:
            job.group.close()
            # Wake an idle stdin worker. Do not wait on a blocked writer here.
            try:
                job.inputs.put_nowait(None)
            except queue.Full:
                pass

    @staticmethod
    def _input_worker(job: Job):
        try:
            while job.process.poll() is None:
                item = job.inputs.get()
                if item is None:
                    break
                job.process.stdin.write(item)
                job.process.stdin.flush()
        except (OSError, ValueError) as exc:
            job.stdin_error = str(exc)
        finally:
            try:
                job.process.stdin.close()
            except OSError:
                pass
            job.stdin_closed = True

    def write(self, job_id: str, text: str = "", close_stdin: bool = False) -> dict:
        job = self.get(job_id)
        data = text.encode("utf-8")
        if len(data) > 1024 * 1024:
            raise ValueError("Send stdin in chunks of at most 1 MiB")
        with job.lock:
            if job.stdin_closed or job.process.poll() is not None:
                raise ValueError("Job stdin is closed or the process has exited")
            required = int(bool(data)) + int(close_stdin)
            if job.inputs.qsize() + required > job.inputs.maxsize:
                raise ValueError("Stdin queue is full; read job status before retrying")
            if data:
                job.inputs.put_nowait(data)
            if close_stdin:
                job.stdin_closed = True
                job.inputs.put_nowait(None)
        return {
            "ok": True,
            "job_id": job_id,
            "queued_bytes": len(data),
            "stdin_closed": job.stdin_closed,
        }

    @staticmethod
    def _validate_read(stdout_offset, stderr_offset, max_bytes, wait_seconds):
        if min(stdout_offset, stderr_offset) < 0:
            raise ValueError("Output offsets cannot be negative")
        if not 4 <= max_bytes <= 1024 * 1024:
            raise ValueError("max_bytes must be between 4 and 1048576 per stream")
        if not 0 <= wait_seconds <= 30:
            raise ValueError("wait_seconds must be between 0 and 30; poll again for longer jobs")

    @staticmethod
    def _output(path: Path, offset: int, limit: int, finished: bool) -> dict:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if offset > size:
                raise ValueError("Output offset is past the end of the log")
            stream.seek(offset)
            data = stream.read(limit)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(data, final=finished and offset + len(data) >= size)
        # Keep partial UTF-8 characters for the next read rather than corrupting them.
        pending, _ = decoder.getstate()
        consumed = len(data) - len(pending)
        return {
            "text": text,
            "next_offset": offset + consumed,
            "total_bytes": size,
            "has_more": offset + consumed < size,
        }

    def read(
        self, job_id: str, stdout_offset=0, stderr_offset=0, max_bytes=65536, wait_seconds=0
    ) -> dict:
        self._validate_read(stdout_offset, stderr_offset, max_bytes, wait_seconds)
        job = self.get(job_id)
        deadline = time.monotonic() + wait_seconds
        try:
            job.process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            pass
        while job.group.active() and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        active = job.group.active()
        code = job.process.poll()
        return {
            "ok": True,
            "job_id": job_id,
            "pid": job.process.pid,
            "state": "running" if active else "finished",
            "active_processes": active,
            "exit_code": code,
            "timed_out": job.timed_out,
            "started_at": job.started_at,
            "timeout_seconds": job.timeout_seconds,
            "stdin_error": job.stdin_error,
            "stdout": self._output(
                job.directory / "stdout.bin", stdout_offset, max_bytes, not active
            ),
            "stderr": self._output(
                job.directory / "stderr.bin", stderr_offset, max_bytes, not active
            ),
            "log_directory": str(job.directory),
        }

    def terminate(self, job_id: str) -> dict:
        job = self.get(job_id)
        with job.lock:
            job.group.terminate()
        return self.read(job_id, wait_seconds=3)

    def list(self) -> dict:
        with self.lock:
            return {
                "ok": True,
                "jobs": [
                    {
                        "job_id": j.id,
                        "pid": j.process.pid,
                        "exit_code": j.process.poll(),
                        "state": "running" if j.group.active() else "finished",
                        "log_directory": str(j.directory),
                    }
                    for j in self.jobs.values()
                ],
            }

    def close(self):
        with self.lock:
            ids = list(self.jobs)
        for job_id in ids:
            self.terminate(job_id)
            self.get(job_id).group.close()


def file_transfer(
    mode: Literal["read", "write", "info"],
    path: str,
    offset: int = 0,
    length: int = 262144,
    data_base64: str | None = None,
    overwrite: bool = False,
) -> dict:
    target = absolute_path(path)
    if offset < 0 or not 1 <= length <= 1024 * 1024:
        raise ValueError("offset must be non-negative; length must be 1..1048576 bytes")
    if mode == "write":
        if data_base64 is None:
            raise ValueError("data_base64 is required for write")
        if len(data_base64) > 4 * ((1024 * 1024 + 2) // 3):
            raise ValueError("Write at most 1 MiB per call")
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > 1024 * 1024:
            raise ValueError("Write at most 1 MiB per call")
        if overwrite and offset != 0:
            raise ValueError("overwrite is only valid at offset=0")
        if offset == 0:
            stream = target.open("wb" if overwrite else "xb")
        else:
            stream = target.open("r+b")
        with stream:
            if offset > os.fstat(stream.fileno()).st_size:
                raise ValueError("offset exceeds file size; upload chunks in order")
            stream.seek(offset)
            stream.write(data)
        return {
            "ok": True,
            "path": str(target),
            "bytes_written": len(data),
            "next_offset": offset + len(data),
            "size": target.stat().st_size,
        }
    if mode == "read":
        with target.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if offset > size:
                raise ValueError("offset exceeds file size")
            stream.seek(offset)
            data = stream.read(length)
        return {
            "ok": True,
            "path": str(target),
            "data_base64": base64.b64encode(data).decode("ascii"),
            "next_offset": offset + len(data),
            "size": size,
            "eof": offset + len(data) >= size,
        }
    if mode != "info":
        raise ValueError("mode must be read, write, or info")
    stat = target.stat()
    return {
        "ok": True,
        "path": str(target),
        "size": stat.st_size,
        "is_file": target.is_file(),
        "modified_ns": stat.st_mtime_ns,
    }


def register(mcp, run_dir: Path) -> CommandJobs:
    from mcp.types import ToolAnnotations

    jobs = CommandJobs(run_dir / "jobs" / uuid.uuid4().hex)
    atexit.register(jobs.close)
    mcp.tool(
        name="ComputerCapabilities",
        description="Inspect actual Windows elevation, desktop availability, drives, runtimes and access limits. No command allowlist or filesystem sandbox is imposed. Call this to diagnose access failures.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )(computer_capabilities)
    mcp.tool(
        name="FileTransfer",
        description="Read or write ANY Windows-accessible binary file in base64 chunks up to 1 MiB. Absolute paths required. No directory allowlist. Upload: write offset=0 creates a new file (overwrite=true replaces); subsequent chunks use next_offset. Download: repeat read using next_offset until eof. Parent directory must exist. FileSystem handles text, directories, copy/move/delete.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )(file_transfer)

    @mcp.tool(
        name="Command",
        description="Run unrestricted PowerShell scripts or ANY executable with argv, cwd and environment overrides. start returns a job_id; jobs survive client disconnects and have no execution deadline unless timeout_seconds is set. read retrieves separate UTF-8 stdout/stderr using byte offsets (default zero replays output), exit_code and state. write queues UTF-8 stdin; close_stdin sends EOF. terminate kills the job process tree; list discovers jobs. Output is spooled to disk; max_bytes limits each response, not total output. wait_seconds is 0..30; poll longer jobs. Pipes, not a terminal/PTY. Jobs end when this server stops. No command/path allowlist; Windows access rights apply.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    )
    def command_tool(
        action: Literal["start", "read", "write", "terminate", "list"],
        job_id: str | None = None,
        command: str | None = None,
        program: str | None = None,
        arguments: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        wait_seconds: float = 1,
        stdout_offset: int = 0,
        stderr_offset: int = 0,
        max_bytes: int = 65536,
        text: str = "",
        close_stdin: bool = False,
    ) -> dict:
        if action == "start":
            return jobs.start(
                command=command,
                program=program,
                arguments=arguments,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
                wait_seconds=wait_seconds,
                max_bytes=max_bytes,
            )
        if action == "list":
            return jobs.list()
        if not job_id:
            raise ValueError("job_id is required for this action")
        if action == "read":
            return jobs.read(job_id, stdout_offset, stderr_offset, max_bytes, wait_seconds)
        if action == "write":
            return jobs.write(job_id, text, close_stdin)
        return jobs.terminate(job_id)

    return jobs
