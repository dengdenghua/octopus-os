"""Unit tests for the static-asset gzip middleware.

These drive the ASGI protocol directly (rather than through an HTTP client)
so we can assert on the raw wire behaviour — in particular that SSE streams
pass through chunk-by-chunk and are never buffered or re-encoded.
"""

from __future__ import annotations

import asyncio
import gzip
from typing import Any

from runtime.platform.ui.compression import GzipStaticMiddleware


def _scope(
    accept_encoding: str | None = "gzip", *, extra: dict[str, str] | None = None
) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if accept_encoding is not None:
        headers.append((b"accept-encoding", accept_encoding.encode()))
    for key, value in (extra or {}).items():
        headers.append((key.encode(), value.encode()))
    return {"type": "http", "method": "GET", "path": "/x", "headers": headers}


def _inner(
    status: int,
    content_type: str,
    body: bytes,
    *,
    chunks: list[bytes] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        hdrs = [(b"content-type", content_type.encode())]
        for key, value in (extra_headers or {}).items():
            hdrs.append((key.encode(), value.encode()))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        if chunks is None:
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        for index, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(chunks) - 1,
                }
            )

    return app


async def _drive(inner: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await GzipStaticMiddleware(inner)(scope, receive, send)
    return sent


def _run(inner: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    return asyncio.run(_drive(inner, scope))


def _headers(sent: list[dict[str, Any]]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


def _body(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_gzip_compresses_large_javascript() -> None:
    payload = ("console.log('hello world');\n" * 2000).encode()
    sent = _run(_inner(200, "text/javascript", payload), _scope())
    headers = _headers(sent)
    assert headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in headers.get("vary", "").lower()
    assert int(headers["content-length"]) < len(payload)
    assert gzip.decompress(_body(sent)) == payload


def test_sse_stream_is_never_compressed_or_buffered() -> None:
    chunks = [b"data: 1\n\n", b"data: 2\n\n", b"data: 3\n\n"]
    sent = _run(_inner(200, "text/event-stream", b"", chunks=chunks), _scope())
    headers = _headers(sent)
    assert "content-encoding" not in headers
    # Each event must be forwarded as its own body message, unchanged — proof
    # the stream is not collapsed into a single buffered frame.
    body_messages = [m for m in sent if m["type"] == "http.response.body"]
    assert len(body_messages) == len(chunks)
    assert _body(sent) == b"".join(chunks)


def test_small_body_is_not_compressed() -> None:
    payload = b'{"ok":true}'
    sent = _run(_inner(200, "application/json", payload), _scope())
    headers = _headers(sent)
    assert "content-encoding" not in headers
    assert _body(sent) == payload


def test_no_gzip_when_client_does_not_accept() -> None:
    payload = ("a" * 4000).encode()
    sent = _run(_inner(200, "text/css", payload), _scope(accept_encoding="identity"))
    headers = _headers(sent)
    assert "content-encoding" not in headers
    assert _body(sent) == payload


def test_non_compressible_type_passes_through() -> None:
    payload = ("a" * 4000).encode()
    sent = _run(_inner(200, "image/png", payload), _scope())
    headers = _headers(sent)
    assert "content-encoding" not in headers
    assert _body(sent) == payload


def test_non_200_passes_through() -> None:
    payload = ("a" * 4000).encode()
    sent = _run(_inner(500, "application/json", payload), _scope())
    headers = _headers(sent)
    assert "content-encoding" not in headers
    assert _body(sent) == payload


def test_range_request_passes_through() -> None:
    payload = ("a" * 4000).encode()
    sent = _run(
        _inner(206, "text/css", payload),
        _scope(extra={"range": "bytes=0-100"}),
    )
    headers = _headers(sent)
    assert "content-encoding" not in headers
    assert _body(sent) == payload


def test_already_encoded_passes_through_unchanged() -> None:
    payload = gzip.compress(("a" * 4000).encode())
    sent = _run(
        _inner(
            200,
            "application/javascript",
            payload,
            extra_headers={"content-encoding": "gzip"},
        ),
        _scope(),
    )
    headers = _headers(sent)
    assert headers.get("content-encoding") == "gzip"
    assert _body(sent) == payload

