"""账号登录鉴权 on the tentacle MCP SSE endpoints used by Claude Desktop.

``/api/tentacle/mcp/sse`` 与 ``/api/tentacle/mcp/message`` 是 Claude
Desktop / Cursor 通过 MCP 协议连接 echo 的入口。网关开启
``require_auth`` 时，客户端必须携带账号凭证（JWT 或 API Key）；
SSE 会话绑定到创建它的账号，``/mcp/message`` 只接受同一账号的调用。

starlette 1.5.0 的 TestClient 会缓冲完整响应体，无法消费无限 SSE 流，
因此这里用可注入的假会话管理器来验证端点的鉴权逻辑。
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore, encode_jwt_hs256
from runtime.tentacle.dashboard import create_tentacle_router

_JWT_SECRET = "test-mcp-secret"
_JWT_ISSUER = "echo-test"
_JWT_AUDIENCE = "echo-clients"


class _DummyCoordinator:
    screen_relay = None
    _dashboard_port = 8000


class _FakeSession:
    """可注入的假 SSE 会话：event_stream 立即结束，让 TestClient 能拿到完整响应."""

    session_id = "fake-session-001"

    def __init__(self, *, actor_id: str | None = None) -> None:
        self.actor_id = actor_id

    async def event_stream(self):
        if False:  # pragma: no cover — 空生成器即可立即结束
            yield None

    async def handle_message(self, request: dict) -> None:
        pass


class _FakeManager:
    """可注入的假会话管理器：记录实例，测试可直接向会话表里播种."""

    instances: list[_FakeManager] = []

    def __init__(self, server=None) -> None:
        self.server = server
        self._sessions: dict[str, _FakeSession] = {}
        _FakeManager.instances.append(self)

    def create_session(self, *, actor_id: str | None = None) -> _FakeSession:
        session = _FakeSession(actor_id=actor_id)
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> _FakeSession | None:
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")
    return store


def _client(
    require_auth: bool,
    store: IdentityStore | None = None,
    *,
    jwt: bool = False,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_tentacle_router(
            _DummyCoordinator(),
            identity_store=store,
            require_auth=require_auth,
            jwt_secret=_JWT_SECRET if jwt else None,
            jwt_issuer=_JWT_ISSUER if jwt else None,
            jwt_audience=_JWT_AUDIENCE if jwt else None,
        )
    )
    return TestClient(app)


def _alice() -> dict[str, str]:
    return {"Authorization": "Bearer sk-alice"}


def _bob() -> dict[str, str]:
    return {"Authorization": "Bearer sk-bob"}


def _alice_jwt() -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": "alice",
            "exp": int(time.time()) + 60,
            "iss": _JWT_ISSUER,
            "aud": _JWT_AUDIENCE,
        },
        secret=_JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _use_fake_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeManager.instances.clear()
    monkeypatch.setattr("runtime.tentacle.mobile.mcp_server.SseSessionManager", _FakeManager)


def _seed_bound_session(client: TestClient) -> str:
    """触发会话管理器初始化并播种一个绑定 alice 的会话，返回其 session_id."""
    r = client.post(
        "/api/tentacle/mcp/message?session_id=deadbeef",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers=_alice(),
    )
    assert r.status_code == 404  # 证明走到了会话查找（鉴权已通过）
    manager = _FakeManager.instances[-1]
    session = _FakeSession(actor_id="alice")
    manager._sessions[session.session_id] = session
    return session.session_id


_PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


# ── /mcp/sse 鉴权 ─────────────────────────────────────────────


def test_mcp_sse_requires_token_when_auth_required() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.get("/api/tentacle/mcp/sse")
    assert r.status_code == 401


def test_mcp_sse_rejects_invalid_token_when_auth_required() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.get("/api/tentacle/mcp/sse", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_mcp_sse_accepts_account_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=True, store=_store())
    r = client.get("/api/tentacle/mcp/sse", headers=_alice())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: endpoint" in r.text
    assert "session_id=fake-session-001" in r.text


def test_mcp_sse_accepts_account_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=True, store=_store(), jwt=True)
    r = client.get("/api/tentacle/mcp/sse", headers=_alice_jwt())
    assert r.status_code == 200
    assert "event: endpoint" in r.text
    assert "session_id=fake-session-001" in r.text


def test_mcp_sse_anonymous_when_auth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # require_auth 关闭（本地/默认）时保持匿名可连接，不破坏既有行为
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=False, store=_store())
    r = client.get("/api/tentacle/mcp/sse")
    assert r.status_code == 200
    assert "event: endpoint" in r.text
    assert "session_id=fake-session-001" in r.text


# ── /mcp/message 鉴权 ─────────────────────────────────────────


def test_mcp_message_requires_token_when_auth_required() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.post("/api/tentacle/mcp/message?session_id=deadbeef", json=_PING)
    assert r.status_code == 401


def test_mcp_message_requires_session_id() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.post("/api/tentacle/mcp/message", json=_PING, headers=_alice())
    assert r.status_code == 400


def test_mcp_message_unknown_session_is_404() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.post(
        "/api/tentacle/mcp/message?session_id=deadbeef",
        json=_PING,
        headers=_alice(),
    )
    assert r.status_code == 404


def test_mcp_message_rejects_other_account_when_session_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=True, store=_store())
    session_id = _seed_bound_session(client)

    resp = client.post(
        f"/api/tentacle/mcp/message?session_id={session_id}",
        json=_PING,
        headers=_bob(),
    )
    assert resp.status_code == 403


def test_mcp_message_rejects_anonymous_when_session_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 会话绑定账号后，即使未开启 require_auth，匿名调用也应被拒绝
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=False, store=_store())
    session_id = _seed_bound_session(client)

    resp = client.post(
        f"/api/tentacle/mcp/message?session_id={session_id}",
        json=_PING,
    )
    assert resp.status_code == 403


def test_mcp_message_accepts_session_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake_manager(monkeypatch)
    client = _client(require_auth=True, store=_store())
    session_id = _seed_bound_session(client)

    resp = client.post(
        f"/api/tentacle/mcp/message?session_id={session_id}",
        json=_PING,
        headers=_alice(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

