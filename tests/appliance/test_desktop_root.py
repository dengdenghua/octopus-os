from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.desktop_root import ApplianceDesktopRootMiddleware


def _agent_app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def agent_dashboard():
        return {"surface": "agent-dashboard"}

    @app.get("/api/state")
    def state():
        return {"surface": "agent-api"}

    return app


def test_configured_os_index_owns_only_the_canonical_root(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Echo OS</title>")
    images = dist / "images"
    images.mkdir()
    (images / "echo.svg").write_text('<svg aria-label="Echo"></svg>')
    app = _agent_app()
    app.add_middleware(ApplianceDesktopRootMiddleware, webui_dist=dist)

    with TestClient(app) as client:
        root = client.get("/")
        index = client.get("/index.html")
        logo = client.get("/images/echo.svg")
        api = client.get("/api/state")

    assert root.status_code == 200
    assert root.text == "<!doctype html><title>Echo OS</title>"
    assert root.headers["cache-control"] == "no-cache"
    assert index.text == root.text
    assert logo.text == '<svg aria-label="Echo"></svg>'
    assert logo.headers["cache-control"] == "public, max-age=3600"
    assert api.json() == {"surface": "agent-api"}


def test_missing_os_distribution_leaves_agent_root_untouched(tmp_path) -> None:
    app = _agent_app()
    app.add_middleware(
        ApplianceDesktopRootMiddleware,
        webui_dist=tmp_path / "missing",
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.json() == {"surface": "agent-dashboard"}


def test_public_asset_resolution_cannot_escape_the_built_distribution(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("Echo OS")
    (tmp_path / "secret.txt").write_text("secret")
    middleware = ApplianceDesktopRootMiddleware(_agent_app(), webui_dist=dist)

    assert middleware._public_file("/../secret.txt") is None
