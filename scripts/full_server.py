"""Load the pinned upstream server and add capabilities without forking its source."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class OriginGuard:
    """Enforce the MCP Origin check, including behind the Host-rewriting proxy."""

    def __init__(self, app, allowed_origins=None):
        self.app = app
        self.allowed = set(allowed_origins or [])

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            origins = [v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"origin"]
            host = next(
                (v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"host"), ""
            )
            same_origin = f"{scope.get('scheme', 'http')}://{host}"
            if origins and (len(origins) != 1 or origins[0] not in self.allowed | {same_origin}):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain"), (b"content-length", b"16")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Origin forbidden"})
                return
        await self.app(scope, receive, send)


def main():
    # Configure before imports; stdout belongs exclusively to MCP in stdio mode.
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    import windows_mcp.__main__ as upstream
    from file_delivery import register_file_delivery
    from laptop_tools import register
    from windows_input import install

    install()

    original_build = upstream._build_mcp
    original_middleware = upstream._http_middleware
    extended = None

    def middleware(*args, **kwargs):
        from starlette.middleware import Middleware

        return [
            Middleware(OriginGuard, allowed_origins=kwargs.get("cors_origins")),
            *original_middleware(*args, **kwargs),
        ]

    def build():
        nonlocal extended
        if extended is None:
            extended = original_build()
            register(extended, Path(__file__).resolve().parents[1] / ".run")
            register_file_delivery(extended, Path(__file__).resolve().parents[1] / ".run")
            extended.instructions += (
                "\nUse ComputerCapabilities to inspect actual access. Screenshot is the fast visual path. "
                "Use Snapshot(use_vision=true, use_ui_tree=true) for UI element labels. "
                "Use Command for long tasks, stdin and complete stdout/stderr; poll its job_id. "
                "For requests to upload/send/give the user a laptop file, call UploadFile(path) and present "
                "its download_url as a clickable link. It also supplies an MCP resource. Native chat attachment "
                "storage is client-controlled; do not invent sandbox links or claim a native attachment exists. "
                "FileTransfer handles binary chunks to/from the laptop. All paths and commands permitted by Windows are available. "
                "Coordinate agents: the desktop and clipboard are shared. Windows permissions and client "
                "policies still apply. No tool can bypass UAC secure desktop."
            )
        return extended

    # The official CLI retains its transport, auth, origin/host checks and lifecycle.
    # These integration points are covered by tests and pinned in uv.lock.
    upstream._build_mcp = build
    upstream._http_middleware = middleware
    upstream.main()


if __name__ == "__main__":
    main()
