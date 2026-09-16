import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp_accept_proxy import McpAcceptProxy, merged_accept


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/mcp?stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Mcp-Session-Id", "session-preserved")
            self.end_headers()
            # Three events in distinct chunks: priming, progress, final result.
            for event in (
                b"id: 1\ndata: \n\n",
                b'data: {"method":"notifications/progress"}\n\n',
                b'data: {"id":1,"result":{"ok":true}}\n\n',
            ):
                self.wfile.write(f"{len(event):X}\r\n".encode() + event + b"\r\n")
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        data = json.dumps(
            {"body": body.decode(), "headers": dict(self.headers), "path": self.path}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "X-Private")
        self.send_header("X-Private", "must disappear")
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST
    do_DELETE = do_POST


@pytest.fixture
def proxy():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    handler = type(
        "Proxy",
        (McpAcceptProxy,),
        {
            "target_host": "127.0.0.1",
            "target_port": upstream.server_port,
            "max_body_bytes": 1024,
            "log_message": lambda *args: None,
        },
    )
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    for server in (upstream, bridge):
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()
    yield bridge.server_port
    for server in (bridge, upstream):
        server.shutdown()
        server.server_close()


def request(port, body=b"{}", headers=None, path="/mcp", method="POST", **kwargs):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {}, **kwargs)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_entire_sse_stream_survives(proxy):
    status, headers, body = request(proxy, path="/mcp?stream")
    assert status == 200
    assert b"notifications/progress" in body
    assert b'"result":{"ok":true}' in body
    assert body.count(b"\n\n") == 3
    assert headers["Mcp-Session-Id"] == "session-preserved"
    assert "Transfer-Encoding" not in headers


def test_headers_are_case_insensitive_and_session_is_preserved(proxy):
    status, headers, body = request(
        proxy,
        headers={
            "accept": "application/json;q=0",
            "Mcp-Session-Id": "abc",
            "MCP-Protocol-Version": "2025-11-25",
            "Authorization": "Bearer test-fixture",
            "Origin": "https://client.example",
            "Connection": "X-Strip",
            "X-Strip": "remove",
        },
    )
    received = {k.lower(): v for k, v in json.loads(body)["headers"].items()}
    assert status == 200
    assert received["accept"] == "application/json, text/event-stream"
    assert received["mcp-session-id"] == "abc"
    assert received["mcp-protocol-version"] == "2025-11-25"
    assert received["authorization"] == "Bearer test-fixture"
    assert received["origin"] == "https://client.example"
    assert "x-strip" not in received and "X-Private" not in headers


def test_chunked_upload_and_trailing_slash(proxy):
    status, _, body = request(
        proxy, body=iter([b'{"a":', b'"hello"}']), path="/mcp/?query=1", encode_chunked=True
    )
    value = json.loads(body)
    assert status == 200
    assert value["body"] == '{"a":"hello"}'
    assert value["path"] == "/mcp?query=1"
    assert value["headers"]["Content-Length"] == "13"


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"Content-Length": "-1"}, b"", 400),
        ({"Content-Length": "abc"}, b"", 400),
        ({"Content-Length": "9999"}, b"", 413),
        ({"Content-Length": "0", "Transfer-Encoding": "chunked"}, b"", 400),
        ({"Transfer-Encoding": "gzip"}, b"", 501),
        ({"Transfer-Encoding": "chunked"}, b"Z\r\n", 400),
        ({"Transfer-Encoding": "chunked"}, b"FFFF\r\n", 413),
    ],
)
def test_bad_request_framing(proxy, headers, body, status):
    assert request(proxy, body=body, headers=headers)[0] == status


def test_get_and_delete(proxy):
    for method in ("GET", "DELETE"):
        status, _, body = request(proxy, method=method, headers={"Mcp-Session-Id": "abc"})
        assert status == 200
        assert json.loads(body)["headers"]["Mcp-Session-Id"] == "abc"


def test_upstream_failure_is_one_502():
    handler = type(
        "Offline",
        (McpAcceptProxy,),
        {"target_host": "127.0.0.1", "target_port": 1, "log_message": lambda *args: None},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    try:
        status, _, body = request(server.server_port)
        assert status == 502
        assert b"HTTP/1.1" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_merge_retains_other_types():
    assert (
        merged_accept("text/plain, TEXT/EVENT-STREAM;q=0")
        == "text/plain, application/json, text/event-stream"
    )
