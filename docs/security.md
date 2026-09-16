# Access and exposure

This repository intentionally offers the full tool set without MCP authentication. There is no command allowlist or filesystem sandbox. An agent can execute programs, read or modify accessible files and registry keys, operate the desktop, and use installed software and network APIs with the server's Windows access token.

The public ngrok URL is an access route to the laptop. Anyone who can reach it can invoke those capabilities; there is no per-agent identity or authorization layer. Running the server as Administrator extends that same access to administrative operations. TLS protects transport to ngrok but does not authenticate the caller to this MCP.

## What the implementation checks

HTTP components bind to loopback. Remote access passes through the explicitly started ngrok tunnel. Incoming browser Origin headers are validated; untrusted origins receive 403. A non-browser caller can omit that header, so Origin checks do not replace authentication. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) describes these transport requirements and recommends authentication.

Startup verifies the local endpoint before publishing and the public endpoint before reporting success. The launcher tracks process identity and rolls back its recorded processes if startup fails. Stop checks PID, creation time and executable path, then stops that managed tree. It does not terminate unrelated processes simply because their name is ngrok or they own a particular port.

Command jobs use Windows Job Objects for descendant tracking and termination when the server stops or crashes. This is lifecycle management, with no additional command, CPU, memory or directory policy. Programs delegated to a service or scheduler are outside that group. The server does not install a service, scheduled task, or other startup persistence.

## Runtime data

The setup screen sends a supplied ngrok authtoken over a private stdin pipe to its configuration helper. The helper saves `.run/private/ngrok.yml` with inheritance disabled and access limited to the current user, Administrators and SYSTEM. Token values are not included in launcher arguments, settings JSON or setup transcripts. This local configuration takes precedence over the existing global ngrok config without replacing it. Runtime folders and common credential filenames are excluded from Git and release ZIPs.

`UploadFile` creates copies of selected files in `.run/uploads/`. Each download URL contains an unguessable ID and expires (24 hours by default); possession of that URL permits downloading the copy without an MCP session. Revocation blocks new downloads and removes the copy without changing its source. Expired copies are cleaned during subsequent publish operations. Already downloaded data cannot be recalled. Downloads force attachment disposition and disable caching and MIME sniffing.

`.run/` contains instance state, logs, command scripts, command output, generated client configuration and verification reports. `.venv/` contains installed dependencies. Both are ignored by Git. Command output and script files can contain sensitive information and remain on disk until removed. ngrok is launched with traffic inspection disabled. Upstream anonymous telemetry is disabled by standard setup and startup.

The preflight scan looks for common token patterns in tracked and untracked source files, excluding ignored runtime files. It reports filenames without printing matched values. This is a useful check, not a guarantee that source contains no secrets.

## Stop or restrict exposure

```powershell
.\scripts\stop-demo.ps1 -Port 8010
```

Use an elevated PowerShell to stop an elevated instance. For access only from this laptop, use stdio or `start-demo.ps1 -LocalOnly`. See the [README](../README.md) for both methods. No system UAC, antivirus or firewall protections are disabled by setup.
