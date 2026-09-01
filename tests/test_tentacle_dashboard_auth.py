"""Tentacle Dashboard HTTP 端点鉴权 (audit C1).

验证 ``require_auth=True`` 且提供了 identity store 时, 所有敏感 HTTP
端点(设备列表/任务提交/群发/截图/远程输入/PC 屏幕等)都必须携带有效
凭证; 未提供 identity store 时降级为匿名(本地 loopback 场景)并告警.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.tentacle.dashboard import create_tentacle_router


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        pool=SimpleNamespace(
            all_online=lambda: [],
            get=lambda _id: None,
            _tentacles={},
        ),
        stats=lambda: {"devices": 0},
        screen_relay=None,
        pc_screen_capture=None,
        remote_input_handler=None,
        _dashboard_port=8766,
        _decision_engine=None,
        _vlm_client=None,
    )


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    return store


def _client(*, require_auth: bool, store: IdentityStore | None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_tentacle_router(_coordinator(), identity_store=store, require_auth=require_auth)
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "path, method",
    [
        ("/api/tentacle/devices", "get"),
        ("/api/tentacle/tasks", "get"),
        ("/api/tentacle/stats", "get"),
        ("/api/tentacle/task", "post"),
        ("/api/tentacle/broadcast", "post"),
        ("/api/tentacle/pc-screen/start", "post"),
        ("/api/tentacle/pc-screen/stop", "post"),
        ("/api/tentacle/pc-screen/stats", "get"),
        ("/api/tentacle/screen/subscribe", "post"),
        ("/api/tentacle/remote-input", "post"),
    ],
)
def test_http_endpoints_require_auth_when_enabled(path: str, method: str) -> None:
    client = _client(require_auth=True, store=_store())
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401, f"{method.upper()} {path} should be 401 without token"


def test_http_endpoints_accept_valid_api_key() -> None:
    client = _client(require_auth=True, store=_store())
    headers = {"Authorization": "Bearer sk-alice"}
    r = client.get("/api/tentacle/stats", headers=headers)
    assert r.status_code == 200
    r2 = client.get("/api/tentacle/devices", headers=headers)
    assert r2.status_code == 200


def test_invalid_token_rejected() -> None:
    client = _client(require_auth=True, store=_store())
    r = client.get("/api/tentacle/devices", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_require_auth_without_identity_store_fails_closed() -> None:
    """显式要求认证时，缺少 identity store 也不能降级为匿名。"""
    client = _client(require_auth=True, store=None)
    r = client.get("/api/tentacle/stats")
    assert r.status_code == 401


def test_default_require_auth_is_enabled() -> None:
    """安全默认: create_tentacle_router 默认开启 require_auth."""
    app = FastAPI()
    app.include_router(create_tentacle_router(_coordinator(), identity_store=_store()))
    client = TestClient(app)
    r = client.get("/api/tentacle/stats")
    assert r.status_code == 401
    r2 = client.get("/api/tentacle/stats", headers={"Authorization": "Bearer sk-alice"})
    assert r2.status_code == 200

