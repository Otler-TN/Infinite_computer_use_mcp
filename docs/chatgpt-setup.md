# Connect ChatGPT to the laptop MCP

## Start and verify the endpoint

The easiest path is to extract the GitHub ZIP and double-click **Open MCP Manager.cmd**. Complete **Setup & settings**, select remote access, install missing software and save your ngrok authtoken. Click **Start MCP**, approve Windows elevation if requested, and wait for **Running**. **Connect an AI app** provides the remaining instructions. No Python or ngrok installation is required beforehand. See the [first-run guide](launcher.md).

Alternatively, from PowerShell in this repository:

```powershell
.\scripts\setup.ps1
.\scripts\preflight.ps1
.\scripts\start-demo.ps1
```

ngrok must already have its account token configured. The launcher prints `MCP URL:` followed by the public HTTPS address ending in `/mcp`. It verifies local and public endpoints before reporting success. Omit `-WindowsMcpPath` to use the locked, tested package.

For administrative Windows operations, start from PowerShell opened with **Run as administrator** and add `-RequireAdministrator`. Stop an existing instance first with `stop-demo.ps1` from an appropriately elevated shell.

## Add the connection

The official setup flow is: enable Developer mode under **Settings → Security and login**, open **ChatGPT Plugins**, use the plus button, and create a developer-mode app using the public MCP URL. Availability depends on account and workspace policy. See [OpenAI's connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt).

For this repository use the public `/mcp` endpoint with streaming HTTP and **No Authentication**. Developer mode supports read and write tools, subject to ChatGPT's confirmation settings. See [OpenAI's Developer mode documentation](https://developers.openai.com/api/docs/guides/developer-mode).

Review the discovered tools: there should be **24**, including `Command`, `FileTransfer`, `UploadFile`, and `ComputerCapabilities`. After updating an existing server, refresh the connection's metadata and start a new conversation with it enabled. The MCP cannot enable tools that the client or workspace has disabled.

## Confirm access and vision

Ask the agent:

```text
Use this Windows MCP connection. First call ComputerCapabilities and report
the elevated and interactive_desktop_available values. Then call Screenshot.
```

`Screenshot` returns an image. `Snapshot` extracts UI information by default; to request an image with it, use:

```json
{"use_vision": true, "use_ui_tree": false}
```

Set `use_ui_tree=true` when you also need UI elements. Do not assume every upstream tool returns the same structured `ok` field; check MCP errors and each tool's documented result.

For long commands or complete stdout/stderr, use `Command` and poll the returned job ID. Use `UploadFile(path)` to get a real download link for a laptop file, and `FileTransfer` for binary chunks. [File sharing](file-sharing.md) explains how agents can return the exported file in a conversation. The [README](../README.md) covers the remaining tools.

## Verify independently of ChatGPT

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py https://YOUR-DOMAIN.ngrok-free.dev/mcp --exercise --desktop --report .run\verification-public.json
```

`--desktop` briefly opens and operates a disposable test window. This verifies the MCP endpoint directly; it does not prove that a particular model will select the right tool for every task.

Stop this instance with:

```powershell
.\scripts\stop-demo.ps1
```

Anyone who can reach the public endpoint can invoke its tools with the server's Windows privileges. See [access and exposure](security.md).
