# Infinite_computer_use_mcp

**Give an MCP-capable AI a real pair of hands on Windows.**

`Infinite_computer_use_mcp` is built around a simple idea: an AI assistant becomes dramatically more useful when it can move from *explaining* a task to actually *doing* it on your computer. With one MCP connection, an assistant can inspect the desktop, operate native apps, work with files, run commands, wait for long jobs, transfer binary data, and return files to you when you are away from the PC.

## Imagine the possibilities

This is not just a collection of isolated automation commands. The interesting part is **combining them**. A single task can move through the GUI, filesystem, PowerShell, running processes, clipboard, web content, and downloadable file delivery without forcing you to sit in front of the machine for every step.

Here are a few real examples of what you can ask it to do:

- **See what is on your computer:** list files on the Desktop, inspect folders, check processes, drives, displays, or system capabilities.
- **Bring a remote file back to you:** locate a file on the PC, export it through the MCP server, and return a download link in the conversation.
- **Move private data without reading it:** copy or upload a ZIP as opaque bytes without opening or extracting it, then verify the exact copy with SHA-256.
- **Use normal Windows applications:** open apps, click, type, scroll, use keyboard shortcuts, work with the clipboard, and interact with visible UI elements.
- **Run serious automation:** start PowerShell or command jobs, keep them alive across client reconnects, stream stdout/stderr later, send stdin, and stop managed process groups when needed.
- **Build multi-step workflows:** combine GUI actions, filesystem operations, scripts, registry/process tools, screenshots, file transfer, and application control into higher-level tasks of your own.

That is where the **“Infinite”** in the name comes from: not from pretending Windows has no security boundaries, but from the huge number of useful workflows you can compose from a general-purpose computer-control surface. Windows permissions, UAC, application behavior, the active desktop session, and the tools you choose to expose still define the real limits.

If you build something interesting with it, improve it, or discover a new workflow, share it with the community. **Experiment responsibly, make it your own, and enjoy exploring what becomes possible when your AI can actually use the computer with you.**

> [!CAUTION]
> This project can expose broad control of the Windows account that runs it. Remote mode intentionally uses an **unauthenticated MCP endpoint** behind ngrok. Anyone who can reach that endpoint can invoke the available tools with the server process's Windows privileges. Use remote mode only when you understand and accept that exposure, keep tunnel URLs private, and stop the server when it is not needed. See [Security](docs/security.md).

## What is under the hood

This is a Windows-only launcher and integration layer around [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP). It keeps the upstream tool set available and adds durable command jobs, binary file transfer, downloadable file exports, capability reporting, and a desktop setup/manager experience.

The runtime is pinned to **Windows-MCP 0.8.5** and locked with `uv.lock` for reproducible installs. This repository is an integration/launcher project; Windows-MCP remains a separate upstream project with its own authors and license.

## What this project adds

- A desktop manager with setup, start/stop, connection checks, logs, local/remote mode, and optional elevation.
- `Command` for long-running processes with stdin, separate stdout/stderr, reconnect-safe job IDs, exit status, timeouts, and process-group termination.
- `FileTransfer` for chunked binary reads/writes using base64 with explicit offsets.
- `UploadFile` for temporary downloadable exports through the active HTTP/ngrok endpoint.
- `ComputerCapabilities` for reporting the server's actual elevation, desktop/session context, drives, runtimes, and limits.
- Streamable HTTP compatibility handling, Origin checks, startup verification, process ownership checks, and source/release safety checks.

The project does **not** promise unrestricted or universal control. UAC secure desktop, ACLs, integrity levels, protected processes, locked sessions, application behavior, and client policy can still block operations. See [Capability and verification notes](docs/capability-audit.md).

## Requirements

- 64-bit Windows 10 or Windows 11.
- An interactive desktop session for GUI automation.
- Internet access for first-time dependency setup.
- An ngrok account only if you want remote access.

You do **not** need to preinstall Python, uv, Git, or ngrok for the normal desktop-manager setup.

## Quick start

1. Download the repository with **Code → Download ZIP** and choose **Extract All**.
2. Open the extracted folder and double-click **Open MCP Manager.cmd**.
3. Open **Setup & settings**, choose local or remote access, then select **Install / repair**.
4. For remote access, use the ngrok dashboard button, copy your ngrok authtoken, and save it in the manager.
5. Select **Start MCP** and wait for the status to show **Running**.
6. Use **Connect an AI app** for the current local or remote connection details.

The launcher keeps generated configuration, logs, tokens, exports, and process state under ignored `.run/` paths. See the [launcher guide](docs/launcher.md) for first-run behavior and recovery steps.

## Connection modes

### Local stdio

Setup writes `.run/mcp-stdio.json` with machine-specific absolute paths. MCP clients that support `mcpServers`-style stdio configuration can use that generated entry.

### Local Streamable HTTP

```powershell
.\scripts\start-demo.ps1 -LocalOnly
```

The default client URL is:

```text
http://127.0.0.1:8011/mcp
```

The MCP server listens on port 8010 and the compatibility proxy listens on 8011. Both bind to loopback.

### Remote through ngrok

```powershell
ngrok config add-authtoken YOUR_NGROK_TOKEN
.\scripts\preflight.ps1
.\scripts\start-demo.ps1
```

The launcher reports an HTTPS URL ending in `/mcp`. Configure the client for **Streamable HTTP / No Authentication**.

```text
Remote MCP client -> ngrok HTTPS -> 127.0.0.1:8011 -> 127.0.0.1:8010 -> Windows
Local MCP client  -> stdio, or the same local HTTP endpoint
```

For a project-local ngrok installation, the launcher can use `.run\tools\ngrok.exe`. `-NgrokPath` selects a different executable explicitly.

## Administrator mode

To require an elevated server:

```powershell
.\scripts\start-demo.ps1 -RequireAdministrator
```

Add `-LocalOnly` for a local-only HTTP endpoint. The option verifies elevation after Windows grants it; it does not bypass UAC. A stdio server inherits the Windows token of the process that launches it.

Use `ComputerCapabilities` to inspect the privileges of the running server rather than assuming administrative access.

## Tool surface

| Area | Tools |
| --- | --- |
| Screens, displays and UI elements | `Screenshot`, `Snapshot`, `DisplayInventory` |
| Mouse, keyboard and multi-field input | `Click`, `Move`, `Scroll`, `Type`, `Shortcut`, `MultiSelect`, `MultiEdit` |
| Apps, waits and clipboard | `App`, `Wait`, `WaitFor`, `Clipboard` |
| Files, processes and registry | `FileSystem`, `Process`, `Registry` |
| Scripting, web content and notifications | `PowerShell`, `Scrape`, `Notification` |
| Added by this project | `Command`, `FileTransfer`, `UploadFile`, `ComputerCapabilities` |

PowerShell and `Command` can invoke installed programs and Windows APIs to the extent allowed by the server's Windows token. A separate MCP tool is not required for every operating-system API.

### Durable commands

Start a job with `Command`, retain its `job_id`, then poll it with `action="read"`. Output is kept separately for stdout and stderr and can be paged using returned offsets. `action="write"` sends UTF-8 stdin, `action="list"` rediscovers jobs after reconnects, and `action="terminate"` stops the managed process group.

Jobs have no execution deadline unless `timeout_seconds` is supplied. They survive MCP client disconnects but end when the server stops or crashes. The tool provides pipes, not a terminal/ConPTY.

### Binary files and downloadable exports

`FileTransfer` reads and writes arbitrary files in chunks of up to 1 MiB using explicit offsets. Use `FileSystem` for normal text and directory operations.

`UploadFile` snapshots an existing file into `.run/uploads/` and returns metadata plus a temporary download URL when HTTP/ngrok is active. Links can be revoked and expire automatically. Native conversation attachment storage remains controlled by the MCP client. See [File sharing](docs/file-sharing.md).

## Install or repair from PowerShell

```powershell
.\scripts\setup.ps1
.\scripts\preflight.ps1 -LocalOnly
```

Setup creates `.venv` from `uv.lock` using Python 3.13 and downloads the required bootstrap components when they are absent. It does not change system execution policy, UAC settings, or antivirus configuration.

## Verify the project

```powershell
.\.venv\Scripts\python.exe -m ruff check scripts tests launcher.pyw
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py http://127.0.0.1:8011/mcp --exercise --report .run\verification.json
```

Add `--desktop` to exercise a temporary native window and GUI interaction. Add `--require-admin` when validating an elevated instance. Run desktop checks only in an available interactive Windows session.

GitHub Actions runs lint, regression tests, and release-package validation on Windows. Desktop/UI and live ngrok account checks remain manual because hosted CI does not provide the same interactive environment.

## Project layout

```text
.github/                 GitHub Actions and contribution templates
docs/                    setup, security, screenshots, troubleshooting and audit notes
scripts/                 launcher, proxy, tools, setup and verification code
tests/                   regression tests
launcher.pyw             GUI bootstrap
Open MCP Manager.cmd     double-click entry point
pyproject.toml           project/development dependencies
uv.lock                  locked Windows runtime
```

## Documentation

- [Launcher and first run](docs/launcher.md)
- [ChatGPT / remote setup](docs/chatgpt-setup.md)
- [File sharing](docs/file-sharing.md)
- [Security model](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Capability and verification notes](docs/capability-audit.md)
- [Publishing and release checks](docs/publishing.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Development and releases

Development dependencies are defined in `pyproject.toml`. On Windows:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\python.exe -m ruff check scripts tests launcher.pyw
.\.venv\Scripts\python.exe -m pytest -q
```

Release ZIPs are built only from Git-tracked, allowlisted source files:

```powershell
.\.venv\Scripts\python.exe .\scripts\build-release.py
```

The output is written under `.run/releases/` together with a SHA-256 file. Runtime state, credentials, logs, exports, caches, and virtual environments are excluded from Git and release packages. See [Publishing](docs/publishing.md).

## License and attribution

This repository is licensed under the [MIT License](LICENSE). The upstream Windows-MCP project remains the work of its own authors and retains its separate licensing and attribution.
