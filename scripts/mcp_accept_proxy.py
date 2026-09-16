from __future__ import annotations

import argparse
import http.client
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

REQUIRED_ACCEPT_TYPES = ("application/json", "text/event-stream")


def merged_accept(value: str) -> str:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    # Replace even q=0 variants, and handle case-insensitive header/media-type names.
    parts = [p for p in parts if p.split(";", 1)[0].strip().lower() not in REQUIRED_ACCEPT_TYPES]
    return ", ".join([*parts, *REQUIRED_ACCEPT_TYPES])


def hop_headers(headers) -> set[str]:
    return HOP_BY_HOP_HEADERS | {
        part.strip().lower()
        for value in headers.get_all("Connection", [])
        for part in value.split(",")
    }


class InvalidRequest(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class McpAcceptProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    target_host: str
    target_port: int
    upstream_timeout: float = 300
    request_timeout: float = 30
    max_body_bytes: int = 64 * 1024 * 1024

    def do_HEAD(self) -> None:
        self.forward()

    def do_GET(self) -> None:
        self.forward()

    def do_POST(self) -> None:
        self.forward()

    def do_DELETE(self) -> None:
        self.forward()

    def do_OPTIONS(self) -> None:
        self.forward()

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write(f"{self.log_date_time_string()} - {format % args}\n")
        sys.stdout.flush()

    def forward(self) -> None:
        connection = None
        response_started = False
        self.close_connection = True
        self.connection.settimeout(self.request_timeout)
        try:
            path = urlsplit(self.path)
            if path.scheme or path.netloc or not self.path.startswith("/"):
                raise InvalidRequest("Only origin-form request paths are supported")
            body = self._read_body()
            headers = self._forward_headers(len(body))
            # Clients disagree about the trailing slash; avoid a POST redirect.
            target_path = self.path
            if path.path == "/mcp/":
                target_path = "/mcp" + ("?" + path.query if path.query else "")
            connection = http.client.HTTPConnection(
                self.target_host, self.target_port, timeout=self.upstream_timeout
            )
            connection.putrequest(
                self.command,
                target_path,
                skip_host=True,
                skip_accept_encoding=True,
            )
            connection.putheader("Host", f"{self.target_host}:{self.target_port}")
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders(body if body else None)

            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            blocked = hop_headers(response.headers)
            for name, value in response.getheaders():
                if name.lower() not in blocked | {"server", "date"}:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            if self.command != "HEAD":
                # SSE may contain priming events, progress, sampling requests, then
                # the actual result. Forward the entire stream unchanged.
                self._stream_response(response)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # A client disconnect is not another HTTP response.
        except (InvalidRequest, OSError, http.client.HTTPException) as exc:
            if not response_started:
                status = exc.status if isinstance(exc, InvalidRequest) else 502
                if isinstance(exc, (TimeoutError, socket.timeout)):
                    status = 504 if connection else 408
                message = (
                    str(exc) if isinstance(exc, InvalidRequest) else "MCP upstream unavailable"
                )
                try:
                    self.send_error(status, message)
                except OSError:
                    pass
            else:
                self.log_message("Upstream stream ended: %s", type(exc).__name__)
        finally:
            if connection is not None:
                connection.close()

    def _stream_response(self, response: http.client.HTTPResponse) -> None:
        reader = getattr(response, "read1", response.read)
        while True:
            chunk = reader(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

    def _read_exact(self, count: int) -> bytes:
        data = self.rfile.read(count)
        if len(data) != count:
            raise InvalidRequest("Incomplete request body")
        return data

    def _read_line(self) -> bytes:
        line = self.rfile.readline(8193)
        if len(line) > 8192 or not line.endswith(b"\r\n"):
            raise InvalidRequest("Invalid chunk framing")
        return line

    def _read_body(self) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        encodings = self.headers.get_all("Transfer-Encoding", [])
        if len(lengths) > 1 or (lengths and encodings):
            raise InvalidRequest("Ambiguous request body framing")
        if encodings:
            if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
                raise InvalidRequest("Only chunked Transfer-Encoding is supported", 501)
            body = bytearray()
            while True:
                size_text = self._read_line().split(b";", 1)[0].strip()
                if not size_text or any(c not in b"0123456789abcdefABCDEF" for c in size_text):
                    raise InvalidRequest("Invalid chunk size")
                size = int(size_text, 16)
                if size == 0:
                    # Consume, but never forward, untrusted trailer headers.
                    for _ in range(100):
                        if self._read_line() == b"\r\n":
                            return bytes(body)
                    raise InvalidRequest("Too many trailers")
                if len(body) + size > self.max_body_bytes:
                    raise InvalidRequest("Request body too large", 413)
                body.extend(self._read_exact(size))
                if self._read_exact(2) != b"\r\n":
                    raise InvalidRequest("Invalid chunk terminator")
        if lengths:
            value = lengths[0].strip()
            if not value.isascii() or not value.isdigit():
                raise InvalidRequest("Invalid Content-Length")
            size = int(value)
            if size > self.max_body_bytes:
                raise InvalidRequest("Request body too large", 413)
            return self._read_exact(size)
        return b""

    def _forward_headers(self, body_length: int) -> dict[str, str]:
        headers: dict[str, str] = {}

        blocked = hop_headers(self.headers) | {"host", "content-length", "accept", "expect"}
        for name, value in self.headers.items():
            lower_name = name.lower()
            if lower_name in blocked:
                continue
            headers[name] = value

        accept = ", ".join(self.headers.get_all("Accept", []))
        if urlsplit(self.path).path.rstrip("/") == "/mcp":
            accept = merged_accept(accept)
        if accept:
            headers["Accept"] = accept
        if body_length or self.command == "POST":
            headers["Content-Length"] = str(body_length)

        return headers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forward MCP HTTP traffic and add missing Streamable HTTP Accept media types."
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--upstream-timeout", type=float, default=300)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    handler = type(
        "ConfiguredMcpAcceptProxy",
        (McpAcceptProxy,),
        {
            "target_host": args.target_host,
            "target_port": args.target_port,
            "upstream_timeout": args.upstream_timeout,
        },
    )

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), handler)
    print(
        "MCP accept proxy listening on "
        f"http://{args.listen_host}:{args.listen_port} -> "
        f"http://{args.target_host}:{args.target_port}",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
