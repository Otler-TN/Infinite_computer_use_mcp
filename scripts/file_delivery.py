"""Deliver explicitly selected Windows files through HTTP and standard MCP resources."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.tools import ToolResult
from mcp.types import (
    BlobResourceContents,
    EmbeddedResource,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse

TOKEN = re.compile(r"[a-f0-9]{48}\Z")
INLINE_LIMIT = 1024 * 1024
RESOURCE_LIMIT = 16 * 1024 * 1024


class ExpiredUpload(ValueError):
    pass


class UploadResult(BaseModel):
    ok: bool = True
    status: Literal["ready", "revoked"]
    upload_id: str
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    sha256: str | None = None
    expires_at: str | None = None
    download_url: str | None = None
    download_headers: dict[str, str] = Field(default_factory=dict)
    resource_uri: str | None = None
    snapshot_path: str | None = None
    content_included: bool = False
    delivery: str


def base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "File delivery base URL must be an HTTP(S) URL without credentials or query"
        )
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def configured_base_url() -> str | None:
    explicit = os.environ.get("WINDOWS_MCP_PUBLIC_BASE_URL")
    if explicit:
        return base_url(explicit)
    state_path = os.environ.get("WINDOWS_MCP_INSTANCE_STATE")
    if state_path:
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8-sig"))
            if state.get("status") in {"starting", "running"} and state.get("url"):
                return base_url(state["url"])
        except (OSError, ValueError):
            pass
    return None


class UploadStore:
    def __init__(self, root: Path, get_base_url=configured_base_url):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.get_base_url = get_base_url

    def paths(self, upload_id: str) -> tuple[Path, Path]:
        if not TOKEN.fullmatch(upload_id):
            raise ValueError("Invalid upload_id")
        # Only generated IDs become filenames; never serve a caller-supplied path.
        return self.root / f"{upload_id}.bin", self.root / f"{upload_id}.json"

    def get(self, upload_id: str) -> dict:
        data_path, metadata_path = self.paths(upload_id)
        try:
            item = json.loads(metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError("Upload not found or revoked") from None
        if item["expires_timestamp"] <= time.time():
            raise ExpiredUpload("Upload expired; publish the original file again")
        if not data_path.is_file():
            raise FileNotFoundError("Upload data is no longer available")
        return item

    def revoke(self, upload_id: str) -> None:
        data_path, metadata_path = self.paths(upload_id)
        # Remove the lookup first so no new download can start. Never touch the source.
        metadata_path.unlink(missing_ok=True)
        try:
            data_path.unlink(missing_ok=True)
        except PermissionError:
            # Windows can keep an in-flight download open. Cleanup retries later.
            pass

    def cleanup(self) -> None:
        for metadata_path in self.root.glob("*.json"):
            if not TOKEN.fullmatch(metadata_path.stem):
                continue
            try:
                item = json.loads(metadata_path.read_text(encoding="utf-8"))
                if item["expires_timestamp"] <= time.time():
                    self.revoke(metadata_path.stem)
            except (OSError, ValueError, KeyError):
                continue
        # Clean copies left by a revoked download after Windows releases its handle.
        for data_path in self.root.glob("*.bin"):
            if TOKEN.fullmatch(data_path.stem) and not data_path.with_suffix(".json").exists():
                try:
                    if data_path.stat().st_mtime < time.time() - 3600:
                        data_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def publish(self, path: str, filename: str | None, expires_in_seconds: int) -> dict:
        source = Path(path).expanduser()
        if not source.is_absolute():
            raise ValueError("Use an absolute Windows file path")
        source = source.resolve(strict=True)
        name = filename if filename is not None else source.name
        if (
            not name
            or name in {".", ".."}
            or len(name) > 240
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " "))
        ):
            raise ValueError(
                "filename must be a single valid download filename, without directories"
            )
        if not 1 <= expires_in_seconds <= 30 * 86400:
            raise ValueError("expires_in_seconds must be 1..2592000 (up to 30 days)")
        # Validate URL configuration before copying, to avoid creating unusable exports.
        configured = self.get_base_url()
        if configured:
            base_url(configured)
        self.cleanup()
        upload_id = secrets.token_hex(24)
        data_path, metadata_path = self.paths(upload_id)
        pending = data_path.with_suffix(".part")
        try:
            with source.open("rb") as incoming, pending.open("xb") as outgoing:
                before = os.fstat(incoming.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("UploadFile requires a regular file; zip directories first")
                digest = hashlib.sha256()
                size = 0
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                after = os.fstat(incoming.fileno())
                if before.st_size != size or before.st_mtime_ns != after.st_mtime_ns:
                    raise ValueError(
                        "The source changed while copying; finish writing it and retry"
                    )
            pending.replace(data_path)
            expires = time.time() + expires_in_seconds
            item = {
                "upload_id": upload_id,
                "filename": name,
                "mime_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "size": size,
                "sha256": digest.hexdigest(),
                "expires_timestamp": expires,
                "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
            }
            temp_metadata = metadata_path.with_suffix(".tmp")
            temp_metadata.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            temp_metadata.replace(metadata_path)
            return item
        except BaseException:
            pending.unlink(missing_ok=True)
            data_path.unlink(missing_ok=True)
            metadata_path.with_suffix(".tmp").unlink(missing_ok=True)
            raise

    def result(self, item: dict, include_content: bool = False) -> ToolResult:
        data_path, _ = self.paths(item["upload_id"])
        resource_uri = f"laptop-upload:///{item['upload_id']}"
        url = self.get_base_url()
        if url:
            url = f"{base_url(url)}/files/{item['upload_id']}/{quote(item['filename'], safe='')}"
        included = include_content and item["size"] <= INLINE_LIMIT
        result = UploadResult(
            status="ready",
            **{
                k: item[k]
                for k in ("upload_id", "filename", "mime_type", "size", "sha256", "expires_at")
            },
            download_url=url,
            download_headers={"ngrok-skip-browser-warning": "true"} if url else {},
            resource_uri=resource_uri,
            snapshot_path=str(data_path),
            content_included=included,
            delivery=(
                "Present download_url as a clickable download link, or fetch it using download_headers into your own file workspace. "
                if url
                else "No HTTP endpoint configured. Read resource_uri through MCP, or use FileTransfer on snapshot_path. "
            )
            + "This is a file export; native conversation attachment storage is controlled by the client. "
            + (
                "Inline content omitted above 1 MiB; use HTTP or resources/read. "
                if include_content and not included
                else ""
            )
            + "On free ngrok, browsers may first show a Visit Site page. The link requires the server/tunnel to remain available until its expiry.",
        )
        payload = result.model_dump()
        content = [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
        content.append(
            ResourceLink(
                type="resource_link",
                uri=url or resource_uri,
                name=item["filename"],
                mimeType=item["mime_type"],
                size=item["size"],
                description="Download the exported file. Use the structured resource_uri for MCP resources/read.",
            )
        )
        if included:
            content.append(
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri=resource_uri,
                        mimeType=item["mime_type"],
                        blob=base64.b64encode(data_path.read_bytes()).decode("ascii"),
                    ),
                )
            )
        return ToolResult(content=content, structured_content=payload)

    async def download(self, request: Request):
        try:
            item = await run_in_threadpool(self.get, request.path_params["upload_id"])
            if request.path_params["filename"] != item["filename"]:
                raise FileNotFoundError("Upload filename does not match")
        except ExpiredUpload:
            return PlainTextResponse(
                "Upload expired", status_code=410, headers={"Cache-Control": "no-store"}
            )
        except (ValueError, OSError, KeyError):
            return PlainTextResponse(
                "Upload not found", status_code=404, headers={"Cache-Control": "no-store"}
            )
        data_path, _ = self.paths(item["upload_id"])
        return FileResponse(
            data_path,
            filename=item["filename"],
            media_type=item["mime_type"],
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Referrer-Policy": "no-referrer",
                "ETag": f'"{item["sha256"]}"',
                "X-Content-SHA256": item["sha256"],
            },
        )


def register_file_delivery(mcp, run_dir: Path) -> UploadStore:
    store = UploadStore(run_dir / "uploads")

    @mcp.tool(
        name="UploadFile",
        title="Upload a laptop file for download",
        description="Use when the user asks to upload, send, attach or download a file FROM this laptop into the conversation. Provide the absolute path. Copies that file and returns a real download_url (HTTP mode), filename, MIME type, SHA-256 and an MCP resource. Present download_url as a clickable link; never invent sandbox links. mode=info retrieves an export; mode=revoke removes its link/copy, leaving the original file unchanged. Links expire after expires_in_seconds (default 24 hours, max 30 days). include_content embeds bytes up to 1 MiB for compatible clients. Larger files stream over HTTP or can be read via resources/read (up to 16 MiB) or FileTransfer on snapshot_path. This does not itself create a native ChatGPT conversation attachment. For files TO the laptop, use FileTransfer.",
        output_schema=UploadResult.model_json_schema(),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    def upload_file(
        path: str | None = None,
        mode: Literal["publish", "info", "revoke"] = "publish",
        upload_id: str | None = None,
        filename: str | None = None,
        expires_in_seconds: int = 86400,
        include_content: bool = False,
    ) -> ToolResult:
        if mode == "publish":
            if not path:
                raise ValueError("path is required when publishing a file")
            item = store.publish(path, filename, expires_in_seconds)
        else:
            if not upload_id:
                raise ValueError("upload_id is required for info or revoke")
            if mode == "revoke":
                store.revoke(upload_id)
                result = UploadResult(
                    status="revoked",
                    upload_id=upload_id,
                    delivery="Export revoked. Original file unchanged; already downloaded copies are unaffected.",
                )
                return ToolResult(structured_content=result.model_dump())
            item = store.get(upload_id)
        return store.result(item, include_content)

    @mcp.resource(
        "laptop-upload:///{upload_id}",
        name="Exported laptop file",
        description="Read an UploadFile export. Binary data up to 16 MiB; use its download_url or FileTransfer for larger files.",
    )
    def read_upload(upload_id: str) -> ResourceResult:
        item = store.get(upload_id)
        if item["size"] > RESOURCE_LIMIT:
            raise ValueError(
                "Resource exceeds 16 MiB; use download_url or FileTransfer on snapshot_path"
            )
        data_path, _ = store.paths(upload_id)
        return ResourceResult(
            [ResourceContent(data_path.read_bytes(), mime_type=item["mime_type"])]
        )

    mcp.custom_route("/files/{upload_id}/{filename}", methods=["GET", "HEAD"])(store.download)
    return store
