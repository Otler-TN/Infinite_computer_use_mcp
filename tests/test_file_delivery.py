import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastmcp import Client, FastMCP
from file_delivery import (
    ExpiredUpload,
    UploadStore,
    base_url,
    configured_base_url,
    register_file_delivery,
)
from starlette.testclient import TestClient


@pytest.fixture
def store(tmp_path):
    return UploadStore(tmp_path / "exports", get_base_url=lambda: "https://laptop.example/mcp")


def test_snapshot_preserves_binary_and_survives_reopen(store, tmp_path):
    source = tmp_path / "report é.bin"
    payload = bytes(range(256)) * 4097
    source.write_bytes(payload)
    item = store.publish(str(source), None, 60)
    source.write_bytes(b"changed later")
    other = UploadStore(store.root, get_base_url=store.get_base_url)
    result = other.result(other.get(item["upload_id"]), include_content=True)
    info = result.structured_content
    assert info["sha256"] == hashlib.sha256(payload).hexdigest()
    assert info["size"] == len(payload)
    assert Path(info["snapshot_path"]).read_bytes() == payload
    assert info["download_url"].endswith("/report%20%C3%A9.bin")
    assert info["download_headers"] == {"ngrok-skip-browser-warning": "true"}
    assert info["content_included"] is False
    assert any(c.type == "resource_link" for c in result.content)
    assert not any(c.type == "resource" for c in result.content)


def test_revoke_removes_only_export_and_is_idempotent(store, tmp_path):
    source = tmp_path / "original.txt"
    source.write_bytes(b"keep original")
    first = store.publish(str(source), None, 60)
    second = store.publish(str(source), None, 60)
    store.revoke(first["upload_id"])
    store.revoke(first["upload_id"])
    with pytest.raises(FileNotFoundError):
        store.get(first["upload_id"])
    assert store.get(second["upload_id"])["filename"] == source.name
    assert source.read_bytes() == b"keep original"


def test_expiry_blocks_reads_and_cleanup_removes_copies(store, tmp_path, monkeypatch):
    source = tmp_path / "fixture.txt"
    source.write_bytes(b"fixture")
    item = store.publish(str(source), None, 60)
    monkeypatch.setattr("file_delivery.time.time", lambda: item["expires_timestamp"] + 1)
    with pytest.raises(ExpiredUpload):
        store.get(item["upload_id"])
    store.cleanup()
    assert not any(store.root.iterdir())
    assert source.exists()


@pytest.mark.parametrize("bad_id", ["../file", "..\\file", "a" * 48 + "/x", "", "%2e%2e", "A" * 48])
def test_ids_cannot_be_paths(store, bad_id):
    with pytest.raises(ValueError, match="upload_id"):
        store.get(bad_id)
    with pytest.raises(ValueError, match="upload_id"):
        store.revoke(bad_id)


@pytest.mark.parametrize("name", ["../x", "x\\y", "x\r\nFake: yes", "", 'x".txt'])
def test_download_names_are_validated_before_copy(store, tmp_path, name):
    source = tmp_path / "source"
    source.write_bytes(b"unchanged")
    with pytest.raises(ValueError, match="filename"):
        store.publish(str(source), name, 60)
    assert not any(store.root.iterdir())


def test_relative_path_and_invalid_expiry_cannot_publish(store, tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        store.publish("relative", None, 60)
    source = tmp_path / "source"
    source.write_bytes(b"unchanged")
    for ttl in (0, -1, 30 * 86400 + 1):
        with pytest.raises(ValueError, match="expires_in_seconds"):
            store.publish(str(source), None, ttl)
    assert not any(store.root.iterdir())


def test_configured_url_follows_instance_without_trusting_request_headers(tmp_path, monkeypatch):
    monkeypatch.delenv("WINDOWS_MCP_PUBLIC_BASE_URL", raising=False)
    state = tmp_path / "state.json"
    monkeypatch.setenv("WINDOWS_MCP_INSTANCE_STATE", str(state))
    assert configured_base_url() is None
    for url in ("http://127.0.0.1:8011/mcp", "https://current-tunnel.example/mcp"):
        state.write_text(json.dumps({"status": "running", "url": url}))
        assert configured_base_url() == url[:-4]
    monkeypatch.setenv("WINDOWS_MCP_PUBLIC_BASE_URL", "https://proxy.example/prefix/")
    assert configured_base_url() == "https://proxy.example/prefix"
    for bad in ("file:///C:/", "https://user:pass@example.com", "https://example.com/?token=x"):
        with pytest.raises(ValueError):
            base_url(bad)


def test_real_mcp_export_resource_download_ranges_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_PUBLIC_BASE_URL", "https://download.example")
    mcp = FastMCP("File delivery test")
    store = register_file_delivery(mcp, tmp_path / "run")
    source = tmp_path / "binary é.pdf"
    payload = b"%PDF-fixture\x00\xff\xfe\x80" + bytes(range(256)) * 1024
    source.write_bytes(payload)

    async def verify():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            upload = next(t for t in tools if t.name == "UploadFile")
            assert upload.output_schema["properties"]["download_url"]
            response = await client.call_tool(
                "UploadFile", {"path": str(source), "include_content": True}
            )
            item = response.structured_content
            assert item["content_included"] and item["filename"] == source.name
            embedded = next(c for c in response.content if c.type == "resource")
            assert base64.b64decode(embedded.resource.blob) == payload
            resource = await client.read_resource(item["resource_uri"])
            assert base64.b64decode(resource[0].blob) == payload
            assert resource[0].mime_type == "application/pdf"
            with TestClient(mcp.http_app()) as http:
                path = urlsplit(item["download_url"]).path
                result = http.get(path)
                assert result.status_code == 200 and result.content == payload
                assert result.headers["content-type"] == "application/pdf"
                assert result.headers["content-disposition"].startswith("attachment;")
                assert "%C3%A9" in result.headers["content-disposition"]
                assert result.headers["x-content-sha256"] == hashlib.sha256(payload).hexdigest()
                assert http.head(path).content == b""
                assert int(http.head(path).headers["content-length"]) == len(payload)
                partial = http.get(path, headers={"Range": "bytes=7-999"})
                assert partial.status_code == 206 and partial.content == payload[7:1000]
                assert http.get(path + "wrong").status_code == 404
                assert http.get("/files/" + "f" * 48 + "/missing").status_code == 404
                metadata_path = store.paths(item["upload_id"])[1]
                metadata = json.loads(metadata_path.read_text())
                metadata["expires_timestamp"] = time.time() - 1
                metadata_path.write_text(json.dumps(metadata))
                assert http.get(path).status_code == 410
                revoke = await client.call_tool(
                    "UploadFile", {"mode": "revoke", "upload_id": item["upload_id"]}
                )
                assert revoke.structured_content["status"] == "revoked"
                assert http.get(path).status_code == 404
            assert source.read_bytes() == payload

    asyncio.run(verify())


def test_stdio_export_has_resource_without_inventing_web_url(tmp_path, monkeypatch):
    monkeypatch.delenv("WINDOWS_MCP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("WINDOWS_MCP_INSTANCE_STATE", raising=False)
    store = UploadStore(tmp_path / "run")
    file = tmp_path / "file.bin"
    file.write_bytes(b"binary\x00\xff")
    result = store.result(store.publish(str(file), None, 60), include_content=True)
    assert result.structured_content["download_url"] is None
    assert result.structured_content["download_headers"] == {}
    link = next(c for c in result.content if c.type == "resource_link")
    assert str(link.uri).startswith("laptop-upload:///")
