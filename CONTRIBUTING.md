# Contributing

Contributions are welcome when they keep the project reproducible, explicit about its security model, and compatible with the supported Windows runtime.

## Before opening a pull request

1. Create a focused branch and keep unrelated cleanup out of the change.
2. Do not commit `.run/`, `.venv/`, ngrok configuration, tokens, logs, exported user files, screenshots, or machine-specific verification artifacts.
3. Add or update tests for behavior changes.
4. Update the relevant documentation when setup, security, CLI options, tools, or user-visible behavior changes.
5. Run the Windows validation commands below.

```powershell
.\scripts\setup.ps1
.\scripts\preflight.ps1 -LocalOnly
.\.venv\Scripts\python.exe -m ruff check scripts tests launcher.pyw
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\scripts\build-release.py
```

Changes that affect GUI automation, elevation, Streamable HTTP behavior, or remote access should also be exercised manually in an interactive Windows session. Live ngrok checks require the contributor's own account and should never expose tokens in logs, issues, or pull requests.

## Pull requests

Describe what changed, why it changed, how it was tested, and any security or compatibility impact. Keep generated runtime files out of patches. If a dependency is upgraded, update the lockfile and re-run the complete regression and desktop verification relevant to that dependency.

## Security issues

Do not disclose credentials, tunnel URLs, private files, or exploitable vulnerability details in a public issue. Follow [SECURITY.md](SECURITY.md) for responsible reporting guidance.
