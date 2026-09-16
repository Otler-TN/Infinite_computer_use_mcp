# Troubleshooting

For the desktop manager, begin with **Setup & settings → Check again**. It identifies missing runtime components, ngrok setup and occupied ports. **Install / repair** installs the required software; the masked token field saves ngrok authentication without a terminal command. **Setup log** contains installation details. See the [first-run guide](launcher.md) for the complete recovery flow.

## Start with the runtime and instance state

```powershell
.\scripts\preflight.ps1
Get-Content -Raw .run\instances\8010\state.json
```

For local operation without ngrok, use `preflight.ps1 -LocalOnly` and `start-demo.ps1 -LocalOnly`. Repair missing dependencies with `setup.ps1`; it uses the lockfile. An incomplete sibling Windows-MCP checkout is not needed. Omit `-WindowsMcpPath` unless intentionally testing a compatible, complete source checkout.

For the default instance, logs and startup verification reports are in `.run/instances/8010/`: `server.log`, `server.error.log`, `proxy.log`, `proxy.error.log`, `ngrok.log`, `ngrok.error.log`, and `verification-*.json`. A launcher failure sets `status="failed"` and stops processes recorded as its own. Logs may contain command output or sensitive data.

## Client cannot connect

Verify the local proxy, then the exact public URL printed by the launcher:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py http://127.0.0.1:8011/mcp
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py https://YOUR-DOMAIN.ngrok-free.dev/mcp
```

Defaults are server **8010**, proxy **8011**, and ngrok inspection API **4040**. Inspect tunnel addresses without reading authentication tokens:

```powershell
(Invoke-RestMethod http://127.0.0.1:4040/api/tunnels).tunnels | Select-Object public_url, config
```

Use Streamable HTTP, No Authentication, and the `/mcp` path. The default proxy also normalizes `/mcp/` without a redirect and supplies both required Accept media types. If the client reports `Client must accept both application/json and text/event-stream`, use the proxy endpoint and omit `-NoAcceptProxy`.

If independent verification passes but the agent cannot use tools, refresh its MCP metadata, enable the connection in the conversation, and check tool permissions. [ChatGPT-specific instructions](chatgpt-setup.md) describe that client's setup.

## ngrok configuration or version error

```powershell
ngrok version
ngrok config check
ngrok config add-authtoken YOUR_NGROK_TOKEN
```

Configure the same executable used by the launcher. Microsoft Store and standalone installations can resolve configuration in different locations; switching executables can make an existing token appear missing. `-NgrokPath` selects an executable explicitly. PATH takes precedence over `.run/tools/ngrok.exe` and WinGet fallback discovery. `config check` validates configuration; starting the tunnel also verifies account authentication.

For updates, use the installation's update mechanism or the [official ngrok download](https://ngrok.com/download/windows). The launcher leaves unrelated ngrok processes running. If another instance owns 4040, resolve that instance explicitly before publishing a new tunnel, or use local mode.

## Port already in use or stale state

Stop this managed instance, or choose a free server/proxy pair:

```powershell
.\scripts\stop-demo.ps1 -Port 8010
.\scripts\start-demo.ps1 -Port 8020
```

The second command uses proxy 8021. Stop verifies saved PID, creation time and executable identity; it does not kill arbitrary listeners. Inspect a conflicting port with:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8010,8011 | Select-Object LocalAddress, LocalPort, OwningProcess
```

Do not delete a live instance's state file; stop needs its identity records. Stop an elevated instance from an elevated PowerShell. After a crash, stale identities are ignored when starting again, but an actual conflicting listener still prevents startup.

## Access denied or input goes nowhere

Call `ComputerCapabilities`. Check `elevated`, `session_id`, `input_desktop`, and `interactive_desktop_available`. To obtain Administrator privileges, stop the instance and run this from PowerShell opened with **Run as administrator**:

```powershell
.\scripts\start-demo.ps1 -RequireAdministrator
```

This checks both launcher and server elevation. It does not bypass UAC. UI interaction requires an available interactive desktop; locked sessions and UAC's secure desktop remain Windows boundaries. Input into a higher-integrity app can be rejected by Windows. [Microsoft documents that restriction for SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

Agents and the user share the desktop. Switch to the intended window, obtain fresh coordinates, then interact. Coordinate GUI actions between agents. Permission to launch an application does not guarantee a usable automation tree.

## Snapshot is slow or has no image

Use `Screenshot` for fast vision. For Snapshot images without UI extraction:

```json
{"use_vision": true, "use_ui_tree": false}
```

Use `use_ui_tree=true` for elements. Some applications have slow or inaccessible UI trees. Screenshots and coordinates are the fallback where those applications accept input. Use image coordinate metadata and `DisplayInventory` with scaled images or multiple monitors. Protected capture surfaces may remain blank.

## Command output, timeouts and reconnects

Use `Command` for separate stdout/stderr, stdin, long scripts or reliable exit status. `PowerShell` retains its upstream synchronous result format. `ok=true` acknowledges the Command operation; a command can still have a nonzero `exit_code`. `state` tracks the whole process group and can remain running after the initial process exits.

Read again using each stream's `next_offset`; omitting offsets replays the log. `max_bytes` caps one response per stream, including the initial start response. Full output is retained on disk. For non-UTF-8 output, read the original `.bin` log with `FileTransfer`. Close stdin when the child expects EOF. Programs needing a terminal/ConPTY may not work through pipes.

Jobs have no execution deadline unless you set `timeout_seconds`. They survive client disconnects: `Command(action="list")` finds them again. They end when the server stops; job IDs cannot be used in a new server instance. Programs launched by external services or schedulers have their own lifecycle.

## Repeat the full check

If an agent says it can read a laptop file but cannot send it, refresh the MCP tool metadata and use `UploadFile(path)` to obtain a real download link. The current inventory has 24 tools. A stdio-only connection returns an MCP resource instead of inventing an HTTP URL. See [file sharing](file-sharing.md) for delivery options and native-attachment limits.

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py http://127.0.0.1:8011/mcp --exercise --desktop --report .run\verification-full.json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check scripts tests
```

The desktop test uses a temporary native window. File and registry exercises use disposable fixtures and clean up. Add `--require-admin` to require elevation. A nonzero verifier exit code means at least one check failed; inspect its JSON report.
