"""HTTP request backstops: bounded timeout + bounded concurrency (audit P-06).

The app previously had only a gzip middleware — a hung endpoint held its
thread indefinitely and a burst of requests could exhaust the worker pool.
Two middlewares close that:

* ``RequestTimeoutMiddleware`` — bounds each non-streaming HTTP request with
  ``asyncio.wait_for``; on expiry it answers 504 and the downstream task is
  cancelled. Streaming/SSE (``/stream``), websockets, and the OpenAI-compat
  ``/v1/*`` surface (whose streaming is governed by the model deadlines) are
  explicitly untouched.
* ``ConcurrencyCapMiddleware`` — a semaphore caps concurrent in-flight
  requests; saturated callers get an immediate 503 instead of queuing
  unbounded work that would exhaust the thread pool.

Both are pure ASGI middlewares and safe to disable by env:
``ECHO_HTTP_REQUEST_TIMEOUT_S=0`` / ``ECHO_HTTP_MAX_CONCURRENCY=0``.
"""

from __future__ import annotations

import asyncio
import logging
import os

from starlette.types import ASGIApp, Receive, Scope, Send

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_MAX_CONCURRENCY = 64


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _is_streaming_scope(scope: Scope) -> bool:
    """True for scopes a request timeout must never touch: websockets,
    SSE/stream paths, and the OpenAI-compat /v1 surface."""
    if scope["type"] != "http":
        return True
    path = scope.get("path") or ""
    if "/stream" in path:
        return True
    return path.startswith("/v1/")


class RequestTimeoutMiddleware:
    """Answer 504 when a non-streaming request outlives the timeout."""

    def __init__(self, app: ASGIApp, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.app = app
        self.timeout_s = max(0.0, timeout_s)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.timeout_s <= 0 or _is_streaming_scope(scope):
            await self.app(scope, receive, send)
            return

        async def _app_wrapper() -> None:
            await self.app(scope, receive, send)

        try:
            await asyncio.wait_for(_app_wrapper(), timeout=self.timeout_s)
        except TimeoutError:
            _log.warning(
                "http request timed out after %.0fs: %s %s",
                self.timeout_s,
                scope.get("method", ""),
                scope.get("path", ""),
            )
            await _send_simple(scope, send, 504, "request timed out")


class ConcurrencyCapMiddleware:
    """Reject with 503 when concurrent in-flight requests exceed the cap."""

    def __init__(self, app: ASGIApp, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        self.app = app
        self.max_concurrency = max(0, int(max_concurrency))
        self._semaphore: asyncio.Semaphore | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.max_concurrency <= 0 or _is_streaming_scope(scope):
            await self.app(scope, receive, send)
            return
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        if self._semaphore.locked():
            _log.warning(
                "http concurrency cap reached (%d): 503 %s %s",
                self.max_concurrency,
                scope.get("method", ""),
                scope.get("path", ""),
            )
            await _send_simple(scope, send, 503, "server busy: too many concurrent requests")
            return
        async with self._semaphore:
            await self.app(scope, receive, send)


async def _send_simple(scope: Scope, send: Send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
