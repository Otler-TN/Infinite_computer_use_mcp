import asyncio

import pytest
from full_server import OriginGuard


@pytest.mark.parametrize(
    ("origins", "allowed", "expected"),
    [
        ([], [], 204),
        (["http://127.0.0.1:8010"], [], 204),
        (["https://agent.example"], ["https://agent.example"], 204),
        (["https://hostile.example"], [], 403),
        (["null"], [], 403),
        (["http://127.0.0.1:8010", "https://hostile.example"], [], 403),
    ],
)
def test_origin_guard(origins, allowed, expected):
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "scheme": "http",
        "headers": [
            (b"host", b"127.0.0.1:8010"),
            *[(b"origin", value.encode()) for value in origins],
        ],
    }
    asyncio.run(OriginGuard(app, allowed)(scope, receive, send))
    assert messages[0]["status"] == expected
    if expected == 403:
        assert int(dict(messages[0]["headers"])[b"content-length"]) == len(messages[1]["body"])
