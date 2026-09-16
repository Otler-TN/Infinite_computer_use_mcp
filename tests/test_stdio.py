import asyncio
import base64
import os
import sys
from pathlib import Path

from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport
from verify_mcp import REQUIRED_TOOLS


def test_full_toolset_and_calls_over_stdio(tmp_path):
    config = tmp_path / "stdio config.toml"
    config.write_text('[server]\ntransport="stdio"\n[tools]\nexclude=[]\n', encoding="utf-8")
    env = {
        **os.environ,
        "ANONYMIZED_TELEMETRY": "false",
        "PYTHONIOENCODING": "utf-8",
        "WINDOWS_MCP_TOOLS": "",
        "WINDOWS_MCP_EXCLUDE_TOOLS": "",
    }

    async def verify():
        transport = StdioTransport(
            command=sys.executable,
            args=[
                str(Path(__file__).resolve().parents[1] / "scripts" / "full_server.py"),
                "serve",
                "--transport",
                "stdio",
                "--config",
                str(config),
            ],
            env=env,
        )
        async with Client(transport, timeout=30) as client:
            tools = await client.list_tools()
            assert REQUIRED_TOOLS <= {tool.name for tool in tools}
            report = await client.call_tool("ComputerCapabilities")
            assert isinstance(report.structured_content["elevated"], bool)
            result = await client.call_tool(
                "Command",
                {
                    "action": "start",
                    "program": sys.executable,
                    "arguments": ["-c", "print('stdio-marker')"],
                    "wait_seconds": 10,
                    "max_bytes": 4,
                },
            )
            assert result.structured_content["exit_code"] == 0
            first = result.structured_content["stdout"]
            assert first["text"] == "stdi" and first["next_offset"] == 4 and first["has_more"]
            remaining = await client.call_tool(
                "Command",
                {
                    "action": "read",
                    "job_id": result.structured_content["job_id"],
                    "stdout_offset": 4,
                },
            )
            assert (
                first["text"] + remaining.structured_content["stdout"]["text"]
            ).strip() == "stdio-marker"
            bad = await client.call_tool(
                "FileTransfer", {"mode": "read", "path": "relative-path"}, raise_on_error=False
            )
            assert bad.is_error
            source = tmp_path / "stdio upload.bin"
            source.write_bytes(b"stdio-file\x00\xff")
            exported = await client.call_tool("UploadFile", {"path": str(source)})
            export = exported.structured_content
            try:
                data = await client.read_resource(export["resource_uri"])
                assert base64.b64decode(data[0].blob) == source.read_bytes()
            finally:
                await client.call_tool(
                    "UploadFile", {"mode": "revoke", "upload_id": export["upload_id"]}
                )

    asyncio.run(verify())
