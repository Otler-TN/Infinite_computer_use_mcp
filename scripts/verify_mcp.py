"""Exercise actual MCP calls; failures produce a nonzero exit and an optional JSON report."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastmcp.client import Client, StreamableHttpTransport

REQUIRED_TOOLS = {
    "App",
    "DisplayInventory",
    "PowerShell",
    "FileSystem",
    "Snapshot",
    "Screenshot",
    "Click",
    "Type",
    "Scroll",
    "Move",
    "Shortcut",
    "Wait",
    "WaitFor",
    "Scrape",
    "MultiSelect",
    "MultiEdit",
    "Clipboard",
    "Process",
    "Notification",
    "Registry",
    "ComputerCapabilities",
    "Command",
    "FileTransfer",
    "UploadFile",
}


def result_text(result) -> str:
    return "\n".join(item.text for item in result.content if item.type == "text")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def download_fixture(url, headers=None):
    request = Request(url, headers={"ngrok-skip-browser-warning": "true", **(headers or {})})
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


class Verification:
    def __init__(self, client, url=None):
        self.client = client
        self.url = url
        self.checks = []
        self.capabilities = {}
        self.tools = []

    async def call(self, name, arguments=None):
        result = await self.client.call_tool(name, arguments or {}, raise_on_error=False)
        require(
            not result.is_error, f"{name} returned an MCP tool error: {result_text(result)[:300]}"
        )
        structured = result.structured_content or {}
        require(structured.get("ok") is not False, f"{name} reported ok=false")
        return result

    async def check(self, name, operation):
        started = time.monotonic()
        try:
            await asyncio.wait_for(operation(), timeout=90)
            check = {"name": name, "passed": True}
        except Exception as exc:
            check = {"name": name, "passed": False, "error": str(exc)}
        check["seconds"] = round(time.monotonic() - started, 2)
        self.checks.append(check)
        print(
            f"[{'PASS' if check['passed'] else 'FAIL'}] {name}"
            + (f": {check['error']}" if not check["passed"] else ""),
            flush=True,
        )

    async def discover(self):
        tools = await self.client.list_tools()
        self.tools = sorted(t.name for t in tools)
        missing = REQUIRED_TOOLS - set(self.tools)
        require(not missing, f"Missing required tools: {', '.join(sorted(missing))}")
        print(f"Tool count: {len(tools)}", flush=True)
        require(all(t.input_schema for t in tools), "A tool has no input schema")

    async def capabilities_check(self, require_admin=False):
        result = await self.call("ComputerCapabilities")
        self.capabilities = result.structured_content
        require(isinstance(self.capabilities, dict), "Capability report is not structured")
        if require_admin:
            require(self.capabilities.get("elevated"), "Server is not running as Administrator")
        print(
            f"Administrator: {self.capabilities.get('elevated')}; desktop: {self.capabilities.get('input_desktop')}"
        )

    async def screenshot(self, name, args):
        result = await self.call(name, args)
        images = [item for item in result.content if item.type == "image"]
        require(bool(images), f"{name} did not return an image")
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(base64.b64decode(images[0].data))) as capture:
            capture.verify()

    async def shell(self):
        marker = "mcp-shell-" + uuid.uuid4().hex
        result = await self.call(
            "PowerShell", {"command": f"Write-Output '{marker}'", "timeout": 15}
        )
        output = result_text(result)
        require(
            marker in output and "Status Code: 0" in output,
            "PowerShell did not return the expected successful output",
        )

    async def read_tools(self):
        displays = result_text(await self.call("DisplayInventory"))
        require(bool(displays) and "error" not in displays[:20].lower(), "Display inventory failed")
        processes = result_text(await self.call("Process", {"mode": "list", "limit": 3}))
        require(bool(processes) and not processes.startswith("Error"), "Process list failed")
        registry = result_text(
            await self.call(
                "Registry",
                {
                    "mode": "get",
                    "path": r"HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                    "name": "ProductName",
                },
            )
        )
        require("Windows" in registry and not registry.startswith("Error"), "Registry read failed")
        home_dir = self.capabilities.get("home_directory")
        require(home_dir, "Missing server home directory")
        file_info = result_text(await self.call("FileSystem", {"mode": "info", "path": home_dir}))
        require(bool(file_info) and not file_info.startswith("Error"), "Filesystem metadata failed")

    async def command(self):
        result = await self.call(
            "Command",
            {
                "action": "start",
                "program": self.capabilities["python_executable"],
                "arguments": [
                    "-u",
                    "-c",
                    "import sys,time; print('out-marker'); print('err-marker',file=sys.stderr); time.sleep(1); print(sys.stdin.readline().strip()); sys.exit(7)",
                ],
                "wait_seconds": 0,
            },
        )
        job_id = result.structured_content["job_id"]
        try:
            await self.call(
                "Command",
                {
                    "action": "write",
                    "job_id": job_id,
                    "text": "stdin-marker\n",
                    "close_stdin": True,
                },
            )
            result = await self.call(
                "Command", {"action": "read", "job_id": job_id, "wait_seconds": 10}
            )
            state = result.structured_content
            require(state["exit_code"] == 7, "Child exit code was lost")
            require(
                "out-marker" in state["stdout"]["text"]
                and "stdin-marker" in state["stdout"]["text"],
                "stdout or stdin was lost",
            )
            require("err-marker" in state["stderr"]["text"], "stderr was lost")
            result = await self.call(
                "Command",
                {
                    "action": "read",
                    "job_id": job_id,
                    "stdout_offset": state["stdout"]["next_offset"],
                    "stderr_offset": state["stderr"]["next_offset"],
                },
            )
            require(
                not result.structured_content["stdout"]["text"],
                "Output offsets replayed already consumed bytes",
            )
        finally:
            await self.call("Command", {"action": "terminate", "job_id": job_id})

    async def reconnect(self):
        transport = StreamableHttpTransport(
            self.url, headers={"ngrok-skip-browser-warning": "true"}
        )
        async with Client(transport, timeout=30) as other_client:
            started = await other_client.call_tool(
                "Command",
                {
                    "action": "start",
                    "program": self.capabilities["python_executable"],
                    "arguments": [
                        "-u",
                        "-c",
                        "import time; time.sleep(1); print('survived-disconnect')",
                    ],
                    "wait_seconds": 0,
                },
            )
            job_id = started.structured_content["job_id"]
        try:
            result = (
                await self.call("Command", {"action": "read", "job_id": job_id, "wait_seconds": 10})
            ).structured_content
            require(
                result["exit_code"] == 0 and "survived-disconnect" in result["stdout"]["text"],
                "Job did not survive the originating client's disconnect",
            )
        finally:
            await self.call("Command", {"action": "terminate", "job_id": job_id})

    async def upload_file(self):
        path = (
            self.capabilities["home_directory"]
            + r"\windows-mcp-upload-verification-"
            + uuid.uuid4().hex
            + ".bin"
        )
        raw = bytes(range(256)) * 7
        upload_id = None
        created = False
        try:
            await self.call(
                "FileTransfer",
                {"mode": "write", "path": path, "data_base64": base64.b64encode(raw).decode()},
            )
            created = True
            exported = await self.call(
                "UploadFile", {"path": path, "expires_in_seconds": 60, "include_content": True}
            )
            info = exported.structured_content
            upload_id = info["upload_id"]
            require(info["sha256"] == hashlib.sha256(raw).hexdigest(), "Upload SHA-256 differs")
            embedded = next((c for c in exported.content if c.type == "resource"), None)
            require(
                embedded and base64.b64decode(embedded.resource.blob) == raw,
                "Embedded file differs",
            )
            resource = await self.client.read_resource(info["resource_uri"])
            require(base64.b64decode(resource[0].blob) == raw, "MCP resource bytes differ")
            require(info["download_url"], "No HTTP download URL configured")
            require(
                info["download_headers"].get("ngrok-skip-browser-warning"),
                "Download request headers missing",
            )
            status, headers, body = await asyncio.to_thread(
                download_fixture, info["download_url"], info["download_headers"]
            )
            require(status == 200 and body == raw, "HTTP download bytes differ")
            require(
                headers.get("content-disposition", "").startswith("attachment;"),
                "Missing download filename",
            )
            status, _, body = await asyncio.to_thread(
                download_fixture, info["download_url"], {"Range": "bytes=11-99"}
            )
            require(status == 206 and body == raw[11:100], "Range download failed")
            await self.call("UploadFile", {"mode": "revoke", "upload_id": upload_id})
            upload_id = None
            status, _, _ = await asyncio.to_thread(download_fixture, info["download_url"])
            require(status == 404, "Revoked upload remains downloadable")
        finally:
            if upload_id:
                await self.call("UploadFile", {"mode": "revoke", "upload_id": upload_id})
            if created:
                await self.call("FileSystem", {"mode": "delete", "path": path})

    async def round_trips(self):
        marker = uuid.uuid4().hex
        directory_name = "windows-mcp-verification-" + marker
        registry_path = r"HKCU:\Software\WindowsMcpVerification-" + marker
        create = await self.call(
            "Command",
            {
                "action": "start",
                "command": f"$testPath = Join-Path ([IO.Path]::GetTempPath()) '{directory_name}'\nNew-Item -ItemType Directory -Path $testPath -ErrorAction Stop | ForEach-Object FullName",
                "wait_seconds": 10,
            },
        )
        state = create.structured_content
        require(state["exit_code"] == 0, "Could not create verification directory")
        directory = state["stdout"]["text"].strip()
        require(directory.endswith(directory_name), "Unexpected verification directory")
        text_path = directory + r"\text.txt"
        binary_path = directory + r"\binary.bin"
        cleanup_paths = []
        registry_created = False
        try:
            cleanup_paths.append(text_path)
            await self.call(
                "FileSystem", {"mode": "write", "path": text_path, "content": "MCP text " + marker}
            )
            read = result_text(await self.call("FileSystem", {"mode": "read", "path": text_path}))
            require(marker in read, "Text file round trip failed")
            raw = bytes(range(256)) * 5
            cleanup_paths.append(binary_path)
            await self.call(
                "FileTransfer",
                {
                    "mode": "write",
                    "path": binary_path,
                    "data_base64": base64.b64encode(raw[:512]).decode(),
                },
            )
            await self.call(
                "FileTransfer",
                {
                    "mode": "write",
                    "path": binary_path,
                    "offset": 512,
                    "data_base64": base64.b64encode(raw[512:]).decode(),
                },
            )
            downloaded = bytearray()
            offset = 0
            while True:
                part = (
                    await self.call(
                        "FileTransfer",
                        {"mode": "read", "path": binary_path, "offset": offset, "length": 311},
                    )
                ).structured_content
                downloaded.extend(base64.b64decode(part["data_base64"]))
                offset = part["next_offset"]
                if part["eof"]:
                    break
            require(bytes(downloaded) == raw, "Binary bytes changed during upload/download")
            registry_created = True
            await self.call(
                "Registry",
                {
                    "mode": "set",
                    "path": registry_path,
                    "name": "Probe",
                    "value": marker,
                    "type": "String",
                },
            )
            read = result_text(
                await self.call("Registry", {"mode": "get", "path": registry_path, "name": "Probe"})
            )
            require(marker in read, "Registry round trip failed")
        finally:
            cleanup_errors = []
            if registry_created:
                try:
                    result = await self.call("Registry", {"mode": "delete", "path": registry_path})
                    require(
                        not result_text(result).startswith("Error"),
                        "Registry fixture cleanup failed",
                    )
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            for path in [*cleanup_paths, directory]:
                try:
                    result = await self.call("FileSystem", {"mode": "delete", "path": path})
                    require(
                        not result_text(result).startswith("Error"),
                        f"Fixture cleanup failed: {path}",
                    )
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            require(not cleanup_errors, "; ".join(cleanup_errors))

    async def desktop_controls(self):
        marker = uuid.uuid4().hex[:12]
        expected_text = marker + "é😀{}"
        title = "MCP Desktop Verification " + marker
        # A disposable native Windows Forms window. Short input avoids using the clipboard.
        script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$form = New-Object System.Windows.Forms.Form
$form.Text = '__TITLE__'
$form.Size = New-Object System.Drawing.Size(430, 220)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$first = New-Object System.Windows.Forms.TextBox
$first.Location = New-Object System.Drawing.Point(30, 30)
$first.Size = New-Object System.Drawing.Size(350, 30)
$second = New-Object System.Windows.Forms.TextBox
$second.Location = New-Object System.Drawing.Point(30, 70)
$second.Size = New-Object System.Drawing.Size(350, 30)
$button = New-Object System.Windows.Forms.Button
$button.Text = 'Verify input'
$button.Location = New-Object System.Drawing.Point(30, 115)
$button.Size = New-Object System.Drawing.Size(150, 30)
$form.Controls.AddRange(@($first, $second, $button))
$button.Add_Click({ [Console]::WriteLine('SUBMITTED:' + $first.Text + '|' + $second.Text) })
$form.Add_Shown({
    $form.Activate()
    $first.Focus()
    $a = $first.PointToScreen([Drawing.Point]::new(15, 12))
    $b = $second.PointToScreen([Drawing.Point]::new(15, 12))
    $c = $button.PointToScreen([Drawing.Point]::new(50, 15))
    [Console]::WriteLine((@{first=@($a.X,$a.Y);second=@($b.X,$b.Y);button=@($c.X,$c.Y)} | ConvertTo-Json -Compress))
})
try { $form.ShowDialog() | Out-Null } finally { $form.Dispose() }
""".replace("__TITLE__", title)
        started = await self.call(
            "Command",
            {
                "action": "start",
                "program": self.capabilities["executables"]["powershell"],
                "arguments": [
                    "-NoProfile",
                    "-STA",
                    "-EncodedCommand",
                    base64.b64encode(script.encode("utf-16-le")).decode(),
                ],
                "wait_seconds": 1,
                "timeout_seconds": 60,
            },
        )
        job_id = started.structured_content["job_id"]
        try:
            coords = None
            for _ in range(10):
                state = (
                    await self.call(
                        "Command", {"action": "read", "job_id": job_id, "wait_seconds": 1}
                    )
                ).structured_content
                for line in state["stdout"]["text"].splitlines():
                    if line.startswith("{"):
                        coords = json.loads(line)
                if coords:
                    break
            require(coords, "Desktop fixture did not become ready")
            snapshot = await self.call("Snapshot", {"use_vision": True, "use_ui_tree": True})
            require(title in result_text(snapshot), "Fixture window was not observed in Snapshot")
            await self.call("App", {"mode": "switch", "name": title})
            await self.call(
                "WaitFor", {"condition": "active_window", "window_name": title, "timeout": 5}
            )
            await self.call(
                "MultiEdit", {"locs": [[*coords["first"], "first"], [*coords["second"], "second"]]}
            )
            await self.call("Move", {"loc": coords["first"]})
            await self.call("Click", {"loc": coords["first"]})
            await self.call("Shortcut", {"shortcut": "ctrl+a"})
            await self.call("Type", {"loc": coords["first"], "text": expected_text, "clear": True})
            await self.call("Click", {"loc": coords["button"]})
            state = (
                await self.call("Command", {"action": "read", "job_id": job_id, "wait_seconds": 1})
            ).structured_content
            require(
                "SUBMITTED:" + expected_text + "|second" in state["stdout"]["text"],
                "Mouse, keyboard or multi-field input did not reach the fixture",
            )
        finally:
            await self.call("Command", {"action": "terminate", "job_id": job_id})


async def run(args):
    report = {"ok": False, "checks": []}
    try:
        transport = StreamableHttpTransport(
            args.url.rstrip("/"), headers={"ngrok-skip-browser-warning": "true"}
        )
        async with Client(transport, timeout=60) as client:
            verify = Verification(client, args.url.rstrip("/"))
            await verify.check("Complete tool inventory", verify.discover)
            await verify.check(
                "Actual access report", lambda: verify.capabilities_check(args.require_admin)
            )
            await verify.check("Screenshot image", lambda: verify.screenshot("Screenshot", {}))
            await verify.check(
                "Snapshot image",
                lambda: verify.screenshot("Snapshot", {"use_vision": True, "use_ui_tree": False}),
            )
            await verify.check("PowerShell execution", verify.shell)
            await verify.check(
                "Displays, processes, registry and filesystem reads", verify.read_tools
            )
            await verify.check(
                "Command stdin, stdout, stderr, exit code and pagination", verify.command
            )
            await verify.check("Command survives MCP client disconnect", verify.reconnect)
            if args.exercise:
                await verify.check(
                    "Text, binary and registry round trips with cleanup", verify.round_trips
                )
                await verify.check(
                    "File upload, HTTP download, MCP resource and revocation", verify.upload_file
                )
            if args.desktop:
                await verify.check(
                    "Native desktop window, UI tree, mouse, keyboard and multi-field input",
                    verify.desktop_controls,
                )
            report.update(
                checks=verify.checks, tools=verify.tools, capabilities=verify.capabilities
            )
            report["ok"] = all(check["passed"] for check in verify.checks)
    except Exception as exc:
        report["error"] = str(exc)
        print(f"[FAIL] MCP connection: {exc}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--exercise",
        action="store_true",
        help="Create and clean up temporary file and HKCU registry fixtures",
    )
    parser.add_argument("--require-admin", action="store_true")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Open a temporary test window and exercise mouse/keyboard/UI automation",
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
