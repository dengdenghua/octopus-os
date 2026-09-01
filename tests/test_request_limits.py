"""Audit P-06: HTTP timeout + concurrency-cap middlewares."""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from runtime.platform.ui.request_limits import (
    ConcurrencyCapMiddleware,
    RequestTimeoutMiddleware,
)


def _app_with(*middlewares) -> FastAPI:
    """``middlewares`` are ``(cls, kwargs)`` pairs passed to add_middleware."""
    app = FastAPI()

    @app.get("/fast")
    def fast() -> dict:
        return {"ok": True}

    @app.get("/slow")
    def slow() -> dict:
        time.sleep(2)
        return {"ok": True}

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def gen():
            for _ in range(4):
                time.sleep(0.05)
                yield "x\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    for mw_cls, mw_kwargs in middlewares:
        app.add_middleware(mw_cls, **mw_kwargs)
    return app


def test_timeout_middleware_answers_504_on_hung_endpoint() -> None:
    app = _app_with((RequestTimeoutMiddleware, {"timeout_s": 0.1}))
    client = TestClient(app)
    resp = client.get("/slow")
    assert resp.status_code == 504
    assert "timed out" in resp.text


def test_timeout_middleware_lets_fast_requests_through() -> None:
    app = _app_with((RequestTimeoutMiddleware, {"timeout_s": 0.1}))
    client = TestClient(app)
    resp = client.get("/fast")
    assert resp.status_code == 200


def test_timeout_middleware_skips_streaming_paths() -> None:
    app = _app_with((RequestTimeoutMiddleware, {"timeout_s": 0.05}))
    client = TestClient(app)
    # The stream takes ~0.2s > the timeout; because /stream is skipped it
    # must complete normally instead of being cut at 504.
    resp = client.get("/stream")
    assert resp.status_code == 200
    assert resp.text == "x\n" * 4


def test_timeout_disabled_by_zero() -> None:
    app = _app_with((RequestTimeoutMiddleware, {"timeout_s": 0.0}))
    client = TestClient(app)
    resp = client.get("/slow")
    assert resp.status_code == 200


def test_concurrency_cap_rejects_when_saturated() -> None:
    app = _app_with((ConcurrencyCapMiddleware, {"max_concurrency": 1}))
    client = TestClient(app)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def hit() -> None:
        barrier.wait()
        try:
            results.append(client.get("/slow").status_code)
        except Exception:  # noqa: BLE001
            results.append(-1)

    t1 = threading.Thread(target=hit)
    t2 = threading.Thread(target=hit)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert sorted(results) == [200, 503], results


def test_concurrency_cap_disabled_by_zero() -> None:
    app = _app_with((ConcurrencyCapMiddleware, {"max_concurrency": 0}))
    client = TestClient(app)
    assert client.get("/fast").status_code == 200

