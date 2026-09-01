"""Narrow auth-middleware exception for the opted-in local paper workbench."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui._app_auth import _install_legacy_control_plane_auth
from runtime.safety.auth import IdentityStore


def _app(*, trusted_local_proxy: bool) -> FastAPI:
    app = FastAPI()
    app.state.paper_trading_trusted_single_user_local_proxy = trusted_local_proxy

    @app.get("/api/plugins/paper-trading/page")
    def page() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/watch")
    def watch() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/watch.js")
    def watch_script() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/origin/{upstream_path:path}")
    def origin_get(upstream_path: str) -> dict[str, str]:
        return {"path": upstream_path}

    @app.post("/api/plugins/paper-trading/origin/{upstream_path:path}")
    def origin_post(upstream_path: str) -> dict[str, str]:
        return {"path": upstream_path}

    @app.post("/api/plugins/paper-trading/check-in")
    def check_in() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/check-in/status")
    def check_in_status() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/account")
    def account() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/page/child")
    def page_child() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/paper-trading/check-in-evil")
    def check_in_lookalike() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/plugins/another-plugin/page")
    def another_plugin() -> dict[str, bool]:
        return {"ok": True}

    _install_legacy_control_plane_auth(
        app,
        identity_store=IdentityStore(),
        require_auth=True,
        jwt_secret=None,
        jwt_issuer=None,
        jwt_audience=None,
    )
    return app


def test_exception_is_closed_by_default() -> None:
    client = TestClient(_app(trusted_local_proxy=False))

    # Exact inert landing pages remain public so iframe navigation can explain
    # why the shared-account routes are unavailable on an authenticated host.
    assert client.get("/api/plugins/paper-trading/page").status_code == 200
    assert client.get("/api/plugins/paper-trading/watch").status_code == 200
    assert client.get("/api/plugins/paper-trading/watch.js").status_code == 401
    assert client.get("/api/plugins/paper-trading/origin/trade/").status_code == 401
    assert client.post("/api/plugins/paper-trading/check-in").status_code == 401


def test_exception_allows_only_scoped_paper_trading_http_paths() -> None:
    client = TestClient(_app(trusted_local_proxy=True))

    assert client.get("/api/plugins/paper-trading/page").status_code == 200
    assert client.get("/api/plugins/paper-trading/watch").status_code == 200
    assert client.get("/api/plugins/paper-trading/watch.js").status_code == 200
    assert client.get("/api/plugins/paper-trading/origin/trade/").status_code == 200
    assert client.post("/api/plugins/paper-trading/origin/api/session").status_code == 200
    assert client.post("/api/plugins/paper-trading/check-in").status_code == 200
    assert client.get("/api/plugins/paper-trading/check-in/status").status_code == 200

    # No sibling paper-trading API, prefix lookalike, child landing route, or
    # other plugin inherits the localhost exception.
    assert client.get("/api/plugins/paper-trading/account").status_code == 401
    assert client.get("/api/plugins/paper-trading/page/child").status_code == 401
    assert client.get("/api/plugins/paper-trading/check-in-evil").status_code == 401
    assert client.get("/api/plugins/another-plugin/page").status_code == 401

