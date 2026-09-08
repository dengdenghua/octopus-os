from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers import storage_skills
from runtime.sensing.gateway.storage_proxy_router import create_storage_proxy_router


class _AsyncBytes(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        yield self.content


def _app_with_transport(handler, monkeypatch) -> FastAPI:
    monkeypatch.setattr(storage_skills, "_base_url", lambda: "http://127.0.0.1:8767")
    monkeypatch.setattr(storage_skills, "_storage_token", lambda: "private-storage-token")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = FastAPI()
    app.include_router(create_storage_proxy_router(http_client=client))
    return app


def test_storage_proxy_injects_private_token_and_preserves_query(monkeypatch) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            stream=_AsyncBytes(b'{"service":"echo-storage"}'),
            headers={"Content-Type": "application/json", "ETag": '"manifest-v1"'},
        )

    app = _app_with_transport(handler, monkeypatch)
    response = TestClient(app).get(
        "/api/storage/v1/manifest?detail=1",
        headers={"Authorization": "Bearer browser-session-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"service": "echo-storage"}
    assert observed == {
        "url": "http://127.0.0.1:8767/v1/manifest?detail=1",
        "authorization": "Bearer private-storage-token",
    }
    assert response.headers["etag"] == '"manifest-v1"'


def test_storage_proxy_streams_range_responses(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=2-5"
        return httpx.Response(
            206,
            stream=_AsyncBytes(b"2345"),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 2-5/10",
                "Accept-Ranges": "bytes",
            },
        )

    app = _app_with_transport(handler, monkeypatch)
    response = TestClient(app).get(
        "/api/storage/v1/files/asset/content",
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["accept-ranges"] == "bytes"


def test_storage_proxy_rejects_non_v1_and_oversized_requests(monkeypatch) -> None:
    app = _app_with_transport(
        lambda _request: httpx.Response(200, json={"unexpected": True}),
        monkeypatch,
    )

    assert TestClient(app).get("/api/storage/admin").status_code == 404
    oversized = TestClient(app).post(
        "/api/storage/v1/search",
        headers={"Content-Length": str(16 * 1024 * 1024 + 1)},
        content=b"{}",
    )
    assert oversized.status_code == 413


def test_storage_proxy_rejects_unreviewed_v1_routes_before_upstream(monkeypatch) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"unexpected": True})

    app = _app_with_transport(handler, monkeypatch)
    browser = TestClient(app)

    assert (
        browser.post("/api/storage/v1/admin/reindex", content=b"secret").status_code
        == 404
    )
    assert browser.get("/api/storage/v1/models/alpha/delete").status_code == 404
    assert called is False


def test_storage_proxy_returns_503_when_storage_is_unavailable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    app = _app_with_transport(handler, monkeypatch)
    response = TestClient(app).get("/api/storage/v1/manifest")

    assert response.status_code == 503
    assert response.json() == {"detail": "echo-storage unavailable"}
    assert response.headers["retry-after"] == "2"


def test_storage_proxy_normalizes_browse_and_search_resource_identity(monkeypatch) -> None:
    from runtime.safety.storage_privacy import storage_policy

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/policy":
            payload = storage_policy()
        elif request.url.path == "/v1/models":
            payload = []
        elif request.url.path == "/v1/browse":
            payload = [{"name": "合同.pdf", "path": "/授权/合同.pdf", "source_id": "source-a"}]
        else:
            payload = {
                "hits": [{"title": "合同", "path": "/授权/合同.pdf", "source_id": "source-a"}]
            }
        return httpx.Response(
            200,
            stream=_AsyncBytes(json.dumps(payload).encode()),
            headers={"Content-Type": "application/json", "ETag": '"stale"'},
        )

    app = _app_with_transport(handler, monkeypatch)
    browser = TestClient(app)

    browse = browser.get("/api/storage/v1/browse?path=%2F授权")
    search = browser.post("/api/storage/v1/search", json={"query": "合同"})

    browse_id = browse.json()[0]["resource_id"]
    search_id = search.json()["hits"][0]["resource_id"]
    assert browse.status_code == search.status_code == 200
    assert browse_id == search_id
    assert browse_id.startswith("storage-file:v1:")
    assert "授权" not in browse_id
    assert "etag" not in browse.headers


def test_storage_proxy_normalizes_file_assets_with_same_resource_identity(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_AsyncBytes(
                json.dumps(
                    [{
                        "asset_id": "asset-1",
                        "source_id": "source-a",
                        "name": "合同.pdf",
                        "path": "/授权/合同.pdf",
                        "extension": ".pdf",
                        "kind": "document",
                        "size": 12,
                        "mtime_ns": 1,
                    }]
                ).encode()
            ),
            headers={"Content-Type": "application/json", "ETag": '"stale"'},
        )

    app = _app_with_transport(handler, monkeypatch)
    response = TestClient(app).get("/api/storage/v1/files?kind=document")

    assert response.status_code == 200
    assert response.json()[0]["resource_id"].startswith("storage-file:v1:")
    assert "授权" not in response.json()[0]["resource_id"]
    assert "etag" not in response.headers


def test_desktop_storage_fallback_keeps_browse_search_and_content_unified(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "desktop-files"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "plan.md").write_text("release plan for Echo", encoding="utf-8")
    (root / "photo.png").write_bytes(b"png")
    monkeypatch.setenv("ECHO_DESKTOP", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    app = _app_with_transport(handler, monkeypatch)
    browser = TestClient(app)

    manifest = browser.get("/api/storage/v1/manifest")
    sources = browser.get("/api/storage/v1/sources")
    browse = browser.get("/api/storage/v1/browse?path=docs")
    assets = browser.get("/api/storage/v1/files?kind=document")
    search = browser.post("/api/storage/v1/search", json={"query": "release"})

    assert manifest.status_code == sources.status_code == browse.status_code == 200
    assert manifest.json()["role"] == "embedded"
    assert sources.json()[0]["display_name"] == "本机文件"
    entry = browse.json()[0]
    asset = assets.json()[0]
    assert entry["resource_id"] == asset["resource_id"]
    assert search.status_code == 200
    assert search.json()["hits"][0]["resource_id"] == asset["resource_id"]

    content = browser.get(
        f"/api/storage/v1/files/{asset['asset_id']}/content"
    )
    assert content.status_code == 200
    assert content.content == b"release plan for Echo"
