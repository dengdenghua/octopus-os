"""Tests for --uds CLI flag and WebSocket proxy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.platform import feature_flags as ff
from runtime.sensing.gateway.remote_backends_router import (
    create_remote_backends_router,
)
from runtime.sensing.gateway.remote_transport import (
    RemoteBackend,
    _to_ws_url,
    _ws_error_envelope,
    proxy_websocket,
)

# ─── _to_ws_url ─────────────────────────────────────────────


def test_to_ws_url_http_becomes_ws() -> None:
    assert _to_ws_url("http://host:8000", "/api/realtime") == ("ws://host:8000/api/realtime")


def test_to_ws_url_https_becomes_wss() -> None:
    assert _to_ws_url("https://host:8000", "/api/realtime") == ("wss://host:8000/api/realtime")


def test_to_ws_url_strips_trailing_slash() -> None:
    assert _to_ws_url("http://host:8000/", "/api/realtime") == ("ws://host:8000/api/realtime")


def test_to_ws_url_path_leading_slash_ok() -> None:
    url = _to_ws_url("http://host:8000", "api/realtime")
    assert url == "ws://host:8000/api/realtime"


# ─── _ws_error_envelope ─────────────────────────────────────


def test_ws_error_envelope_is_valid_jsonrpc() -> None:
    raw = _ws_error_envelope("something went wrong")
    obj = json.loads(raw)
    assert obj["jsonrpc"] == "2.0"
    assert obj["method"] == "proxy/error"
    assert "something went wrong" in obj["params"]["message"]


# ─── proxy_websocket (async, stub upstream) ─────────────────


class _FakeUpstream:
    """Minimal async context manager that yields a fixed sequence
    of messages then raises ConnectionClosed."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def recv(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        raise Exception("connection closed")

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> _FakeUpstream:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakeClientWs:
    """Minimal fake of starlette WebSocket for proxy tests."""

    def __init__(self, outbound: list[str]) -> None:
        self._outbound = list(outbound)
        self.received: list[str] = []
        self.closed = False
        self.close_code: int | None = None

    async def receive_text(self) -> str:
        if self._outbound:
            return self._outbound.pop(0)
        raise Exception("client disconnected")

    async def send_text(self, msg: str) -> None:
        self.received.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


@pytest.mark.asyncio
async def test_proxy_websocket_relays_remote_to_client() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    upstream = _FakeUpstream(["hello from remote"])
    client = _FakeClientWs([])

    def factory(url: str) -> _FakeUpstream:
        return upstream

    await proxy_websocket(backend, client, upstream_factory=factory)
    assert "hello from remote" in client.received


@pytest.mark.asyncio
async def test_proxy_websocket_relays_client_to_remote() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    upstream = _FakeUpstream([])
    client = _FakeClientWs(["client message"])

    def factory(url: str) -> _FakeUpstream:
        return upstream

    await proxy_websocket(backend, client, upstream_factory=factory)
    assert "client message" in upstream.sent


@pytest.mark.asyncio
async def test_proxy_websocket_sends_error_on_upstream_failure() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    client = _FakeClientWs([])

    class _FailUpstream:
        async def __aenter__(self) -> _FailUpstream:
            raise ConnectionRefusedError("refused")

        async def __aexit__(self, *_: Any) -> None:
            pass

    def factory(url: str) -> _FailUpstream:
        return _FailUpstream()

    await proxy_websocket(backend, client, upstream_factory=factory)
    assert any("upstream failed" in m for m in client.received)


# ─── /api/remote-backends/{id}/realtime endpoint ────────────


@pytest.fixture
def _reset_flags(monkeypatch: pytest.MonkeyPatch):
    original = dict(ff._SPECS)
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original)
    ff._SNAPSHOT = None
    ff._FILE_PATH = None


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = tmp_path / "backends.json"
    app = FastAPI()
    app.include_router(create_remote_backends_router(store_path=store))
    return TestClient(app)


def test_realtime_ws_blocked_when_flag_off(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _reset_flags: None,
) -> None:
    monkeypatch.delenv("ECHO_FF_UI_REMOTE_TRANSPORT", raising=False)
    ff.reload()
    with client.websocket_connect("/api/remote-backends/missing/realtime") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["method"] == "proxy/error"
        assert "disabled" in msg["params"]["message"]


def test_realtime_ws_404_for_unknown_backend(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _reset_flags: None,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    with client.websocket_connect("/api/remote-backends/does-not-exist/realtime") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["method"] == "proxy/error"
        assert "not found" in msg["params"]["message"]


# ─── --uds CLI argument parsing ─────────────────────────────


def test_serve_parser_accepts_uds() -> None:
    """Verify --uds is accepted by the serve subcommand parser."""
    import sys

    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "echo-agent",
            "serve",
            "--config",
            "config.local.yaml",
            "--uds",
            "/tmp/test.sock",
        ]
        # Import the main entry point and parse args without running.

        # We can't call main() (it would try to load config), but we
        # can verify the parser accepts the flag by checking the help
        # text contains --uds.
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [_sys.executable, "-m", "runtime", "serve", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--uds" in result.stdout
    finally:
        sys.argv = old_argv


def test_ui_parser_accepts_uds() -> None:
    import subprocess
    import sys as _sys

    result = subprocess.run(
        [_sys.executable, "-m", "runtime", "ui", "--help"],
        capture_output=True,
        text=True,
    )
    assert "--uds" in result.stdout
