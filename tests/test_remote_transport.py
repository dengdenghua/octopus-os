"""Tests for runtime.sensing.gateway.remote_transport + remote_backends_router."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.platform import feature_flags as ff
from runtime.sensing.gateway.remote_backends_router import (
    create_remote_backends_router,
)
from runtime.sensing.gateway.remote_transport import (
    BackendRegistry,
    RemoteBackend,
    SshTunnel,
    _validate_url,
    health_check,
    proxy_request,
)


@pytest.fixture(autouse=True)
def _reset_flags() -> Iterator[None]:
    original = dict(ff._SPECS)
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original)
    ff._SNAPSHOT = None
    ff._FILE_PATH = None


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "remote_backends.json"


# ─── URL validation ─────────────────────────────────────────


def test_url_must_have_scheme() -> None:
    with pytest.raises(ValueError):
        _validate_url("example.com")


def test_url_strips_trailing_slash() -> None:
    assert _validate_url("https://example.com/") == "https://example.com"


def test_url_rejects_empty() -> None:
    with pytest.raises(ValueError):
        _validate_url("   ")


# ─── Registry round-trip ────────────────────────────────────


def test_registry_starts_empty(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    assert reg.list() == []


def test_add_then_list(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    b = reg.add(name="prod", url="https://api.example.com:8000")
    assert b.id
    assert b.name == "prod"
    assert b.url == "https://api.example.com:8000"
    assert reg.list() == [b]


def test_add_persists_across_instances(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    reg.add(name="staging", url="http://staging.local:8000")

    reloaded = BackendRegistry(store_path)
    backends = reloaded.list()
    assert len(backends) == 1
    assert backends[0].name == "staging"


def test_add_rejects_duplicate_name(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    reg.add(name="dup", url="https://a.example.com")
    with pytest.raises(ValueError):
        reg.add(name="dup", url="https://b.example.com")


def test_add_with_ssh_tunnel_persists(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    reg.add(
        name="prod",
        url="http://localhost:8000",
        ssh=SshTunnel(host="bastion.example.com", user="ops", port=22),
    )
    reloaded = BackendRegistry(store_path)
    backend = reloaded.list()[0]
    assert backend.ssh is not None
    assert backend.ssh.host == "bastion.example.com"
    assert backend.ssh.user == "ops"


def test_remove(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    b = reg.add(name="x", url="https://x.example.com")
    assert reg.remove(b.id) is True
    assert reg.list() == []
    assert reg.remove("nonexistent") is False


def test_update_health(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    b = reg.add(name="x", url="https://x.example.com")
    assert b.last_health is None

    updated = reg.update_health(b.id, status="ok")
    assert updated is not None
    assert updated.last_health == "ok"
    assert updated.last_health_at is not None

    failed = reg.update_health(b.id, status="error", detail="HTTP 500")
    assert failed.last_health == "error"
    assert failed.health_detail == "HTTP 500"


def test_update_health_rejects_invalid_status(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    b = reg.add(name="x", url="https://x.example.com")
    with pytest.raises(ValueError):
        reg.update_health(b.id, status="weird")


def test_get_by_name(store_path: Path) -> None:
    reg = BackendRegistry(store_path)
    b = reg.add(name="prod", url="https://example.com")
    assert reg.get_by_name("prod") == b
    assert reg.get_by_name("missing") is None


# ─── Health check (HTTP mocked) ─────────────────────────────


class _StubResponse:
    def __init__(self, status_code: int, json_data: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self) -> Any:
        return self._json


class _StubClient:
    def __init__(self, response: _StubResponse | Exception) -> None:
        self._response = response

    def get(self, url: str, **kwargs: Any) -> _StubResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_health_check_ok() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    status, detail = health_check(backend, http_client=_StubClient(_StubResponse(200)))
    assert status == "ok"
    assert detail is None


def test_health_check_non_2xx() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    status, detail = health_check(backend, http_client=_StubClient(_StubResponse(503)))
    assert status == "error"
    assert "503" in (detail or "")


def test_health_check_network_error() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    status, detail = health_check(backend, http_client=_StubClient(ConnectionRefusedError("nope")))
    assert status == "error"
    assert "ConnectionRefusedError" in (detail or "")


# ─── Proxy ──────────────────────────────────────────────────


def test_proxy_returns_status_and_body() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    response = _StubResponse(201, json_data={"ok": True})
    result = proxy_request(
        backend,
        method="POST",
        path="/api/echo",
        json={"hi": 1},
        http_client=_StubClient(response),
    )
    assert result["status_code"] == 201
    assert result["body"] == {"ok": True}


def test_proxy_rejects_unsupported_method() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    with pytest.raises(ValueError):
        proxy_request(
            backend,
            method="OPTIONS",
            path="/x",
            http_client=_StubClient(_StubResponse(200)),
        )


def test_proxy_wraps_exceptions() -> None:
    backend = RemoteBackend(id="x", name="x", url="https://example.com")
    result = proxy_request(
        backend,
        method="GET",
        path="/x",
        http_client=_StubClient(TimeoutError("slow")),
    )
    assert result["status_code"] == 0
    assert result["body"]["error"] == "proxy_failed"
    assert "TimeoutError" in result["body"]["detail"]


# ─── Router endpoints ──────────────────────────────────────


@pytest.fixture
def client(store_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_remote_backends_router(store_path=store_path))
    return TestClient(app)


def test_get_works_with_flag_off(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_UI_REMOTE_TRANSPORT", raising=False)
    ff.reload()
    r = client.get("/api/remote-backends")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["backends"] == []


def test_post_blocked_when_flag_off(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_FF_UI_REMOTE_TRANSPORT", raising=False)
    ff.reload()
    r = client.post(
        "/api/remote-backends",
        json={"name": "x", "url": "https://example.com"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "remote_transport_disabled"


def test_post_then_get_with_flag_on(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    r = client.post(
        "/api/remote-backends",
        json={"name": "prod", "url": "https://api.example.com"},
    )
    assert r.status_code == 200
    bid = r.json()["backend"]["id"]

    listing = client.get("/api/remote-backends").json()
    assert listing["enabled"] is True
    assert any(b["id"] == bid for b in listing["backends"])


def test_post_validates_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    r = client.post("/api/remote-backends", json={"url": "https://x.com"})
    assert r.status_code == 400
    r = client.post("/api/remote-backends", json={"name": "x"})
    assert r.status_code == 400
    r = client.post("/api/remote-backends", json={"name": "x", "url": "no-scheme"})
    assert r.status_code == 400


def test_post_with_invalid_ssh_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    r = client.post(
        "/api/remote-backends",
        json={
            "name": "x",
            "url": "https://x.com",
            "ssh": {"user": "ops"},  # missing host
        },
    )
    assert r.status_code == 400


def test_delete_known_backend(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    bid = client.post(
        "/api/remote-backends",
        json={"name": "x", "url": "https://x.com"},
    ).json()["backend"]["id"]
    r = client.delete(f"/api/remote-backends/{bid}")
    assert r.status_code == 200
    r = client.delete(f"/api/remote-backends/{bid}")
    assert r.status_code == 404


def test_health_endpoint_records_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    bid = client.post(
        "/api/remote-backends",
        json={"name": "x", "url": "https://x.com"},
    ).json()["backend"]["id"]

    with patch(
        "runtime.sensing.gateway.remote_backends_router.health_check",
        return_value=("ok", None),
    ):
        r = client.post(f"/api/remote-backends/{bid}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    listing = client.get("/api/remote-backends").json()
    assert listing["backends"][0]["last_health"] == "ok"


def test_proxy_endpoint_forwards(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    bid = client.post(
        "/api/remote-backends",
        json={"name": "x", "url": "https://x.com"},
    ).json()["backend"]["id"]

    with patch(
        "runtime.sensing.gateway.remote_backends_router.proxy_request",
        return_value={"status_code": 200, "body": {"echo": True}},
    ):
        r = client.post(
            f"/api/remote-backends/{bid}/proxy",
            json={"method": "GET", "path": "/api/health"},
        )
    assert r.status_code == 200
    assert r.json()["body"] == {"echo": True}


def test_proxy_404_for_unknown_backend(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_TRANSPORT", "1")
    ff.reload()
    r = client.post(
        "/api/remote-backends/missing/proxy",
        json={"method": "GET", "path": "/x"},
    )
    assert r.status_code == 404
