"""Scan-to-join: /api/tentacle/join-info builds a phone connect string + token."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _client(auth_token: str | None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.sensing.gateway.tentacle_join_router import (
        create_tentacle_join_router,
    )

    app = FastAPI()
    app.include_router(create_tentacle_join_router(ws_port=8765, auth_token=auth_token))
    return TestClient(app)


def test_join_info_with_token() -> None:
    r = _client("secret123").get("/api/tentacle/join-info").json()
    assert r["ws_port"] == 8765
    assert r["ws_url"].startswith("ws://") and r["ws_url"].endswith(":8765")
    assert r["token"] == "secret123"
    cs = r["connect_string"]
    assert cs.startswith("echo://join?")
    # parseable on the phone: ws + token round-trip
    qs = parse_qs(urlparse(cs).query)
    assert qs["ws"][0] == r["ws_url"]
    assert qs["token"][0] == "secret123"


def test_join_info_loopback_no_token() -> None:
    r = _client(None).get("/api/tentacle/join-info").json()
    assert r["token"] == ""
    assert "token=" not in r["connect_string"]  # tokenless (loopback joins)


def test_router_exposes_route() -> None:
    from runtime.sensing.gateway.tentacle_join_router import (
        create_tentacle_join_router,
    )

    router = create_tentacle_join_router(ws_port=8765, auth_token=None)
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/api/tentacle/join-info" in paths


def test_create_app_can_disable_tentacle_routes() -> None:
    from fastapi.testclient import TestClient

    from runtime.platform.ui import create_app

    app = create_app(journal_path=None, tentacle_enabled=False)
    r = TestClient(app).get("/api/tentacle/join-info")

    assert r.status_code == 404


def test_token_persists_across_calls(monkeypatch, tmp_path) -> None:
    import runtime.platform.process.paths as paths_mod
    from runtime.tentacle.team_bridge import get_or_create_tentacle_token

    class _Paths:
        data_dir = tmp_path

    monkeypatch.setattr(paths_mod, "app_paths", lambda: _Paths())
    first = get_or_create_tentacle_token()
    assert first and get_or_create_tentacle_token() == first  # stable
    assert (tmp_path / "tentacle_token").read_text(encoding="utf-8").strip() == first

