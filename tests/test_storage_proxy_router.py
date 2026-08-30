from __future__ import annotations

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


def test_storage_proxy_returns_503_when_storage_is_unavailable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    app = _app_with_transport(handler, monkeypatch)
    response = TestClient(app).get("/api/storage/v1/manifest")

    assert response.status_code == 503
    assert response.json() == {"detail": "echo-storage unavailable"}
    assert response.headers["retry-after"] == "2"

