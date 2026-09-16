"""Double-click to open the Infinite Computer Use MCP manager. No third-party GUI packages needed."""

import ctypes
import sys
import traceback
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "scripts"))

try:
    from mcp_manager import main

    main()
except Exception:
    details = traceback.format_exc()
    log = root / ".run" / "manager" / "launcher-error.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(details, encoding="utf-8")
        message = f"Could not open the MCP manager.\n\nDetails: {log}\n\nInstall Python with Tcl/Tk support, then try again."
    except OSError:
        message = details
    ctypes.windll.user32.MessageBoxW(None, message, "Infinite Computer Use MCP Manager", 0x10)
