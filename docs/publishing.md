# Publishing and updating

The repository's Windows workflow installs the locked environment, runs lint and regression tests, and builds a downloadable source ZIP. Desktop checks requiring user interaction should also be run on an available Windows desktop before a release.

Before publishing, run:

```powershell
.\scripts\setup.ps1
.\scripts\preflight.ps1 -LocalOnly
.\.venv\Scripts\python.exe -m ruff check scripts tests launcher.pyw
.\.venv\Scripts\python.exe -m pytest -q
# Stage reviewed source changes, then build:
.\.venv\Scripts\python.exe .\scripts\build-release.py
```

`build-release.py` packages only Git-tracked source files in the expected project locations and rejects unexpected files and symlinks. Attach `.run/releases/Infinite_computer_use_mcp.zip` and its SHA-256 file to a GitHub Release. Users can also select **Code → Download ZIP**, extract it, and double-click **Open MCP Manager.cmd**. Neither path requires Git, Python, uv, or ngrok to be installed beforehand.

Do not distribute `.run`, `.venv`, ngrok configuration, tokens, screenshots, exported user files, or local logs. Runtime directories and common credential filenames are ignored by Git. The preflight source scan helps catch accidental credentials; review staged files before pushing.

Updates should replace source files in the existing extracted folder while preserving `.run`. Stop MCP first, reopen the manager, and use **Setup & settings → Install / repair**. The launcher detects lockfile changes and validates runtime imports. Its bootstrap runs the UI outside `.venv` so the runtime can be repaired. A new folder is treated as a separate installation and may need its token entered again.

## First-run acceptance checklist

- Extract into a writable folder with spaces in its path.
- Open the launcher without a pre-existing virtual environment, Python, uv, or ngrok on PATH.
- Complete local setup, start, check the connection, copy its link and stop.
- Select remote mode, install ngrok, open its dashboard and enter an authtoken.
- Missing or malformed tokens produce guidance; account authentication is checked by actual tunnel startup.
- Select Administrator mode and verify the normal Windows elevation flow, including cancellation.
- Reopen the manager and verify saved settings and detection of the running instance.
- Check connection instructions, generated stdio configuration, runtime repair and Desktop shortcut.
- Build the source ZIP and confirm that private files and tokens are excluded.

The project intentionally exposes an unauthenticated endpoint. GitHub publication does not make it a hosted MCP service: each user runs their own instance with their own account and privileges.
