"""WebSocket handshake auth for the tentacle server.

The mother-brain WS server binds 0.0.0.0 (phones connect over the LAN)
and used to register and execute for ANY device that sent device/hello —
no secret. These tests lock the token handshake: loopback-without-token
stays open for local dev, but a routable bind rejects every connection
that doesn't present the configured token, and an unauthenticated client
can't reach task/execute at all.

(_handle_connection unregisters the device in its finally when the
stream ends, so we assert on the registration callback / close state
rather than connected_count after the call returns.)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from runtime.tentacle.transport.ws_server import TentacleWebSocketServer


class _FakeWs:
    """Minimal stand-in for a websockets server connection."""

    def __init__(self, incoming: list[Any], remote: tuple[str, int] = ("192.168.1.50", 5555)):
        self._incoming = list(incoming)
        self.remote_address = remote
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    def __aiter__(self) -> _FakeWs:
        return self

    async def __anext__(self) -> Any:
        if self.closed or not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


def _hello(token: str | None = None, tentacle_id: str = "android-1") -> str:
    params: dict[str, Any] = {"tentacle_id": tentacle_id, "model": "Pixel"}
    if token is not None:
        params["token"] = token
    return json.dumps({"jsonrpc": "2.0", "method": "device/hello", "params": params, "id": "1"})


def _task_execute() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "task/execute",
            "params": {"task_id": "t1", "task": "do thing"},
            "id": "9",
        }
    )


def _make_server(**kw: Any) -> tuple[TentacleWebSocketServer, list[str]]:
    """Server whose on_device_hello records every registered device id."""
    registered: list[str] = []

    async def _on_hello(hello: Any, ws: Any) -> None:
        registered.append(hello.tentacle_id)

    return TentacleWebSocketServer(on_device_hello=_on_hello, **kw), registered


@pytest.mark.asyncio
async def test_loopback_without_token_allows_hello():
    server, registered = _make_server(host="127.0.0.1", auth_token=None)
    ws = _FakeWs([_hello()], remote=("127.0.0.1", 4000))
    await server._handle_connection(ws, "/")
    assert not ws.closed
    assert registered == ["android-1"]


@pytest.mark.asyncio
async def test_routable_without_token_rejects_everything():
    # 0.0.0.0 with no configured secret: nothing to authenticate against.
    server, registered = _make_server(host="0.0.0.0", auth_token=None)
    ws = _FakeWs([_hello()])
    await server._handle_connection(ws, "/")
    assert ws.closed
    assert ws.close_code == 1008
    assert registered == []


@pytest.mark.asyncio
async def test_routable_with_token_requires_matching_token():
    server, registered = _make_server(host="0.0.0.0", auth_token="s3cret")

    ok = _FakeWs([_hello(token="s3cret")])
    await server._handle_connection(ok, "/")
    assert not ok.closed
    assert registered == ["android-1"]

    bad = _FakeWs([_hello(token="wrong")], remote=("192.168.1.51", 6666))
    await server._handle_connection(bad, "/")
    assert bad.closed
    assert bad.close_code == 1008

    missing = _FakeWs([_hello(token=None)], remote=("192.168.1.52", 7777))
    await server._handle_connection(missing, "/")
    assert missing.closed

    assert registered == ["android-1"]  # only the correct-token device


@pytest.mark.asyncio
async def test_unauthenticated_cannot_reach_task_execute():
    # The original hole: a client could send task/execute without ever
    # registering. The gate must reject it before dispatch.
    seen: list[Any] = []

    async def _on_task(req: Any, ws: Any) -> None:
        seen.append(req)

    server = TentacleWebSocketServer(
        host="0.0.0.0",
        auth_token="s3cret",
        on_task_execute=_on_task,
    )
    ws = _FakeWs([_task_execute()])
    await server._handle_connection(ws, "/")
    assert ws.closed
    assert ws.close_code == 1008
    assert seen == []  # decision engine never invoked


@pytest.mark.asyncio
async def test_env_var_token_is_honored(monkeypatch: Any):
    monkeypatch.setenv("ECHO_TENTACLE_TOKEN", "from-env")
    server, registered = _make_server(host="0.0.0.0")
    ok = _FakeWs([_hello(token="from-env")])
    await server._handle_connection(ok, "/")
    assert registered == ["android-1"]
    assert not ok.closed
