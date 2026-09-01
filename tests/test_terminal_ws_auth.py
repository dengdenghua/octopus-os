"""Auth on the persistent-shell terminal WebSocket.

/api/terminal/ws opens a real shell, and used to ``accept()`` and spawn
it for anyone. These tests lock the security guarantee: when require_auth
is set, an unauthenticated (or wrong-token) client is closed with 4401
BEFORE a process is ever created.

(The accepted-token path spawns a real subprocess whose lifetime spans
the TestClient's event loop, which makes deterministic cleanup flaky in
a unit test; the rejection path — the actual security boundary — needs
no subprocess and is what we lock here.)
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.terminal_router import mount_terminal_routes


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(
        Identity(actor_id="alice", roles=("operator",)),
        api_key_plaintext="sk-alice",
    )
    return store


def _client(require_auth: bool, store: IdentityStore | None = None) -> TestClient:
    app = FastAPI()
    mount_terminal_routes(app, identity_store=store, require_auth=require_auth)
    return TestClient(app)


def test_ws_rejects_missing_token_when_required():
    client = _client(require_auth=True, store=_store())
    with (
        pytest.raises(WebSocketDisconnect) as ei,
        client.websocket_connect("/api/terminal/ws/s1") as ws,
    ):
        ws.receive_text()
    assert ei.value.code == 4401  # closed before any shell spawned


def test_ws_rejects_wrong_token_when_required():
    client = _client(require_auth=True, store=_store())
    with (
        pytest.raises(WebSocketDisconnect) as ei,
        client.websocket_connect("/api/terminal/ws/s1?token=nope") as ws,
    ):
        ws.receive_text()
    assert ei.value.code == 4401


def test_ws_require_auth_without_identity_store_rejects():
    # require_auth set but no identity store wired → fail closed, not open.
    client = _client(require_auth=True, store=None)
    with (
        pytest.raises(WebSocketDisconnect) as ei,
        client.websocket_connect("/api/terminal/ws/s1?token=anything") as ws,
    ):
        ws.receive_text()
    assert ei.value.code == 4401


def test_terminal_kill_requires_auth_when_required():
    client = _client(require_auth=True, store=_store())
    unauth = client.post("/api/terminal/kill/s1")
    assert unauth.status_code == 401
    ok = client.post(
        "/api/terminal/kill/s1",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert ok.status_code == 200
