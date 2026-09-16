# Security policy

This project intentionally exposes powerful Windows automation capabilities. Read [docs/security.md](docs/security.md) before enabling remote access.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** flow when private vulnerability reporting is enabled for the repository. If that option is unavailable, open a minimal public issue asking the maintainer for a private reporting channel and do **not** include exploit details, credentials, tunnel URLs, private file contents, or other sensitive data.

Include affected versions/commits, reproduction conditions, expected impact, and any safe mitigation you have identified. Do not test against systems or accounts you do not own or have permission to assess.

## Operational security

- Treat the remote MCP URL as a secret capability URL even though it is not authentication.
- Do not commit ngrok tokens, `.run/`, `.venv/`, logs, exports, screenshots, or generated machine-specific configuration.
- Prefer local-only mode when remote access is unnecessary.
- Stop the MCP instance and close the tunnel when it is not in use.
- Run with the least Windows privilege level needed for the task.
- Review generated release ZIPs and the source secret scan before publishing.

The project cannot bypass Windows security boundaries such as UAC secure desktop, ACLs, integrity levels, protected processes, or application-specific restrictions.
