# Capability and verification notes

This document describes the capabilities implemented by this repository, the checks included to validate them, and the boundaries that still depend on Windows, the target application, and the MCP client.

Machine-specific verification reports are written under ignored `.run/` paths and are intentionally not committed. Treat current behavior as verified only after running the checks on the Windows machine and configuration you plan to use.

## Implemented surface

The server exposes **24 tools**: the 20 tools provided by the pinned Windows-MCP 0.8.5 runtime, plus `Command`, `FileTransfer`, `UploadFile`, and `ComputerCapabilities` from this project. Commands and file paths have no application-level allowlist or workspace sandbox. Effective access is determined by the server process's Windows token, the target application, and the MCP client.

The regression suite covers release packaging, file exports, expiry and revocation, filename/path handling, MCP resources, binary content, ranged downloads, manager privilege selection, stale process identities, UAC cancellation, origin handling, proxy streaming, command jobs, stdio integration, and Windows input behavior. Ruff is configured for the Python source and tests.

The verifier can exercise the MCP inventory, screenshot decoding, access facts, system reads, shell stdin/stdout/stderr/exit status, file and registry round trips, reconnect behavior, and an optional native desktop fixture. Desktop and live remote checks require an interactive Windows session and, for ngrok, the operator's own account.

## Findings addressed by this project

| Finding | Change and effect |
| --- | --- |
| An unpinned or incomplete source checkout could produce dependency drift. | The runtime is pinned to Windows-MCP 0.8.5 with Python 3.13 setup and a complete `uv.lock`. Setup also generates a machine-specific stdio configuration. |
| The proxy could stop after the first SSE event, which may precede the final result. | It forwards the complete stream, including priming events, progress and final responses. |
| Chunked uploads, slash redirects and header handling could break MCP traffic. | Added strict body framing, chunk decoding, `/mcp/` normalization, Accept repair and hop-header removal. Session/protocol/authentication/Origin headers are preserved. |
| Failed streams could trigger a second HTTP response. | After response headers are sent, failures close/log the stream instead of sending another status line. |
| Startup/stop behavior could affect unrelated ngrok processes or rely on fragile process matching. | Added per-instance state, atomic writes, locking, Windows argv quoting and identity checks using PID, start time and executable path. |
| Starting and managing the MCP required PowerShell commands. | Added a Python/Tkinter desktop manager with Start, Stop, Copy link, live status, connection checks, settings and logs. It calls the same lifecycle scripts and requests normal Windows elevation when needed. See the [launcher guide](launcher.md). |
| Verification failures were not reliable startup failures. | Local and public checks fail startup on a nonzero result. Recorded components are rolled back on failure. Missing runtimes, occupied ports, incomplete source overrides and required elevation fail explicitly. |
| Inherited configuration could restrict tool discovery. | Standard startup clears inherited tool filters and explicitly enables the complete upstream tool set. Telemetry and UTF-8 settings are configured before imports. |
| A synchronous shell call is insufficient for long work, stdin, and complete stdout plus stderr. | Added `Command`: optional timeouts, disk-backed output, byte paging, stdin, native argv/cwd/environment, exit status, reconnect discovery and process-group termination. |
| Text-oriented file tools do not provide general binary transfer. | Added base64 `FileTransfer` reads/writes with explicit offsets, 1 MiB chunks and no application-level total file-size cap. Invalid input is rejected before overwriting files. |
| A remote agent could read laptop files but lacked a downloadable delivery route. | Added `UploadFile`: snapshot copies, expiring HTTP(S) links through the active endpoint, MCP resources, optional embedded bytes, filename/MIME/hash metadata, byte-range downloads and revocation. Native conversation storage remains client-controlled. See [file sharing](file-sharing.md). |
| Advertised capability alone does not reveal actual privileges. | Added `ComputerCapabilities` reporting elevation, desktop, session, drives, runtimes and limits. Required elevation is also verified over MCP during startup. |
| Text replacement needed reliable Unicode input and recovery. | Added native Unicode input, literal braces/surrogate pairs, document-selection keys, and key-release recovery after partial input. `MultiEdit` uses the same typing path. |
| Browser Origin handling needed explicit protection. | Added an Origin guard that rejects untrusted browser origins. It is a browser-oriented safeguard, not client authentication. |
| Basic verification did not cover enough of the integrated surface. | Added tool-schema checks, decoded images, OS reads, shell behavior, reconnects, file/registry round trips and an optional native desktop fixture. |

Multiple-event streaming, Accept handling and Origin checks are designed around the [MCP Streamable HTTP specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

## Capability coverage

| Area | Availability and verification coverage |
| --- | --- |
| Vision and UI | `Screenshot`, `Snapshot` and display inventory are available. The verifier decodes returned images and can use a temporary native test window for UI-tree checks. Protected capture surfaces and individual UI trees remain application-dependent. |
| Mouse and keyboard | Click, move, scroll, shortcuts, type, multi-select and multi-edit are available. Tests cover native Unicode typing, replacement behavior and key recovery; the optional desktop fixture exercises real input against temporary controls. |
| Apps and timing | App launch/switch/resize, `Wait` and `WaitFor` are available. The optional desktop fixture exercises foreground switching and an active-window wait. |
| Files | Text/directory operations and arbitrary binary transfer are available. Tests cover disposable text/binary round trips, overwrite protection, invalid base64 and offsets. |
| Registry | Read/enumerate/write/delete are available subject to Windows permissions. Exercise mode uses a disposable HKCU key/value and cleans it up. |
| Processes | Process inspection/control and managed command groups are available. Tests cover child processes, surviving descendants, timeouts and crash cleanup. |
| Shell | PowerShell scripts and installed executables can use argv, cwd, environment and stdin. Tests cover independent stdout/stderr, nonzero exit codes, UTF-8 paging and long scripts. |
| Long work | Start/poll/input/terminate/rediscover behavior is implemented for `Command`. Jobs survive MCP client disconnects while the server remains alive; server restart ends managed jobs. |
| Clipboard and notifications | Upstream Clipboard and Notification tools are exposed. User clipboard contents and notification preferences are not modified by the default automated regression suite. |
| Network and Windows APIs | `Scrape` plus arbitrary commands can use network APIs, COM, WMI/CIM, services and tasks as permitted by Windows. `Scrape` retains upstream URL restrictions; `Command` has no URL allowlist. |
| Connections | The repository includes stdio integration tests plus verification for local Streamable HTTP and public HTTPS endpoints. Client metadata refresh, image support and provider policy remain client responsibilities. |
| Lifecycle and protocol | Tests cover PowerShell parsing/argv/identity behavior, complete SSE forwarding, framing, headers, Origin rules and upstream errors. Startup verification and stop logic are part of the Windows acceptance checklist. |

The implementation wraps the pinned [CursorTouch Windows-MCP](https://github.com/CursorTouch/Windows-MCP) package without modifying its installed source. It uses private upstream builder/middleware hooks and replaces in-process typing/shortcut methods. Dependency upgrades should rerun the regression suite and the relevant desktop checks.

## Remaining boundaries

- **Windows privileges:** `-RequireAdministrator` verifies elevation; Windows must grant it before startup. UAC secure desktop, locked sessions, protected processes and inaccessible resources are not universally controllable even by an elevated server.
- **Input:** Windows restricts injected input by integrity level. Some applications ignore synthetic input or expose incomplete automation. Rejected input can be reported, but the server cannot force every application to accept it. [Microsoft documents SendInput restrictions](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
- **Shared desktop:** agents share the desktop, clipboard and filesystem. Multi-step workflows are not isolated per agent; coordinate GUI actions and refresh visual context.
- **Process lifetime:** Job Objects track directly created descendants and terminate them on server exit. Work delegated to a service, WMI process creation or Task Scheduler is outside that group. [Microsoft describes Job Objects and child inheritance](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- **Client behavior:** the server cannot force correct model choices, alter client policies or account restrictions, or add image support to an incompatible client.
- **Resources:** per-request chunks can be paged, but disk capacity, memory, networks and installed software remain finite. Logs have no automatic retention policy. `Command` provides pipes, not terminal/ConPTY emulation.
- **Upstream behavior:** PowerShell retains its synchronous result format; use `Command` for separate complete stdout/stderr. Slow or inaccessible Snapshot trees may require Screenshot and coordinates where input works.
- **Exposure:** every caller able to reach the no-auth tunnel receives the server's effective privileges. Origin checks are not authentication. See [Security](security.md).

## Reproduce the checks

```powershell
.\scripts\setup.ps1
.\scripts\preflight.ps1
.\.venv\Scripts\python.exe -m ruff check scripts tests launcher.pyw
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py http://127.0.0.1:8011/mcp --exercise --desktop --report .run\verification-full.json
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py https://YOUR-DOMAIN.ngrok-free.dev/mcp --exercise --desktop --report .run\verification-public-full.json
```

Run desktop checks in an available interactive session. Add `--require-admin` when validating an elevated server. Failed checks produce a nonzero exit code and individual errors. Verification artifacts stay local because they can contain machine-specific details.
