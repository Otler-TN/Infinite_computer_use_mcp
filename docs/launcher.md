# Set up Infinite Computer Use MCP

Download the GitHub ZIP, choose **Extract All**, and open the extracted folder in a writable location such as Documents. Double-click **Open MCP Manager.cmd**. Intel/AMD 64-bit Windows 10/11 and an internet connection are required. Python, Git, uv and ngrok do **not** need to be installed first.

The first launch downloads a private Python installation with Tkinter if needed, then opens the manager. A setup console shows download progress and explains failures. Windows or an organization may block downloaded programs; follow your device's normal software approval process.

## Complete Setup once

**Setup & settings** opens automatically for a new installation and can be reopened at any time.

1. **Choose how to connect.** Remote uses ngrok; This computer only skips ngrok. Select Administrator if your tasks require it. Windows requests elevation when starting or stopping an elevated instance.
2. **Install / repair.** Install the locked MCP dependencies, generate the local agent configuration, and install ngrok if remote access needs it. The installer verifies ngrok's publisher signature. Existing installations are reused, including the Microsoft Store version.
3. **Connect your ngrok account.** Click **Open ngrok account & authtoken**, sign up or sign in, verify your email, and copy the authtoken. Paste only the token into the masked field and click **Save token**. An API key is different. Existing configured accounts can be reused without re-entering their tokens.
4. Close Setup, click **Start MCP**, and approve the Windows prompt if requested. Wait for **Running**. The launcher verifies the server and real tunnel before reporting success.
5. Click **Copy link**, then **Connect an AI app** for client instructions. Add the URL as Streamable HTTP with No Authentication. Your AI account/workspace must allow custom MCP connections.

The launcher cannot create an ngrok account, verify email, approve a Windows prompt, or change an AI provider's account policy. It guides you through these steps. The configured-token check is local; actual account acceptance is verified when the tunnel starts.

## Everyday use

- **Start MCP / Stop MCP:** connect or disconnect this laptop. Keep the laptop awake and MCP running. Closing the manager leaves MCP running.
- **Check connection:** test the endpoint and tools independently of your AI app.
- **Setup & settings:** change connection mode, save a new authtoken, repair dependencies or create a Desktop shortcut. Changes apply on the next start. Stop MCP before repairing its runtime.
- **Connect an AI app:** client instructions, the official ChatGPT guide, and the generated local stdio configuration.
- **Logs:** current operation or instance logs. **Setup log** is inside Setup.

The manager detects an instance already started from PowerShell. Stop checks saved PID, start time and executable identity. It manages default ports 8010/8011 and ngrok's local inspector on 4040. Remote access intentionally has no authentication: anyone who can reach the link receives the server's Windows privileges.

## Problems and recovery

| What you see | What to do |
| --- | --- |
| Missing software or failed imports | Click Install / repair. Checks cover imports and the dependency lock, not just whether python.exe exists. |
| Failed download or installation | Open Setup log, check internet connectivity and free disk space, then retry. Local mode skips ngrok. |
| Missing authtoken | Use the dashboard button, paste the token and save. No terminal command is needed. |
| Invalid token, email verification, account/session limit, or ngrok outage | Open Logs for the reason. Correct the account/token and retry. Stop other ngrok sessions yourself if your account has reached its limit. |
| Port in use | Close the application using the named port, then Check again. Unrelated processes are left alone. |
| Needs attention | Open Logs, then Stop MCP to clean up the recorded instance before restarting. |
| Windows prompt cancelled | Start or Stop again when ready. The manager does not repeatedly prompt. |
| Connection missing in the AI app | Check custom MCP/developer-mode availability and workspace permissions. |
| Tools missing after an update | Refresh the connection's metadata; the server exposes 24 tools. |

## Local files and updates

Settings, installation receipts, generated configuration, exports and logs remain in ignored `.run/`. A token entered through Setup is saved in `.run/private/ngrok.yml`, restricted to the current Windows user, Administrators and SYSTEM. It never appears in launcher command arguments, settings JSON or setup logs. Existing global ngrok configuration is not overwritten.

To update, stop MCP, replace source files in the same folder, reopen the manager and run Install / repair. Preserve `.run` to keep settings and the token. Downloading into a new folder creates a separate installation.

The bootstrap downloads a pinned [Astral uv release](https://docs.astral.sh/uv/getting-started/installation/) and checks its published SHA-256. Python comes from [uv's managed distributions](https://docs.astral.sh/uv/guides/install-python/); ngrok comes from its [official Windows distribution](https://ngrok.com/download/windows). Setup does not disable UAC, antivirus or firewall protections, or install an automatic startup task.

See the [README](../README.md), [ChatGPT walkthrough](chatgpt-setup.md), and [publishing guide](publishing.md).
