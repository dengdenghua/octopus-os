"""Content-type-aware gzip compression for the FastAPI app.

Starlette's stock :class:`~starlette.middleware.gzip.GZipMiddleware` compresses
*every* response — including ``text/event-stream`` SSE streams, which it chunks
in a way that stalls live token streaming. This middleware instead compresses
only a small allowlist of static, bounded, highly-compressible content types
(JS/CSS/HTML/JSON/SVG/XML/plain text) and explicitly *never* touches SSE,
websockets, range requests, already-encoded bodies, or non-200 responses.

That keeps the ~18 MB of Vite-built UI assets (and ordinary JSON API responses)
on the wire at roughly a third of their size while leaving the streaming hot
paths — ``/api/**/stream``, the realtime SSE/chat channels — byte-for-byte
untouched.
"""

from __future__ import annotations

import gzip

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Media types worth gzipping. Everything else — already-compressed images,
# octet streams, and crucially ``text/event-stream`` — is passed through
# unmodified.
_COMPRESSIBLE: frozenset[str] = frozenset(
    {
        "text/html",
        "text/css",
        "text/plain",
        "text/xml",
        "text/javascript",
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/xml",
        "application/xhtml+xml",
        "image/svg+xml",
    }
)

# gzip's ~20-byte framing overhead makes compressing tiny payloads
# counter-productive, so anything below this stays raw.
_MIN_SIZE = 860


async def _unattached_send(message: Message) -> None:  # pragma: no cover
    raise RuntimeError("send awaitable not set")


class GzipStaticMiddleware:
    """Gzip only safe, bounded, compressible HTTP responses."""

    def __init__(self, app: ASGIApp, minimum_size: int = _MIN_SIZE) -> None:
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Websockets and lifespan events are never our concern.
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        accept_encoding = headers.get("accept-encoding", "").lower()
        # Skip when the client can't take gzip, and never re-encode a range
        # request (the 206/partial body would be corrupted).
        if "gzip" not in accept_encoding or "range" in headers:
            await self.app(scope, receive, send)
            return

        responder = _GzipResponder(self.app, self.minimum_size)
        await responder(scope, receive, send)


class _GzipResponder:
    def __init__(self, app: ASGIApp, minimum_size: int) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.send: Send = _unattached_send
        self.compress = False
        self.start_message: Message | None = None
        self.body = bytearray()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self._send)

    async def _send(self, message: Message) -> None:
        message_type = message["type"]

        if message_type == "http.response.start":
            self.start_message = message
            headers = Headers(raw=message["headers"])
            media_type = headers.get("content-type", "").split(";")[0].strip().lower()
            already_encoded = "content-encoding" in headers
            self.compress = (
                message["status"] == 200 and media_type in _COMPRESSIBLE and not already_encoded
            )
            # Defer emitting ``start`` only when compressing — we need the final
            # Content-Length first. Otherwise pass it straight through.
            if not self.compress:
                await self.send(message)
            return

        if message_type != "http.response.body" or not self.compress:
            await self.send(message)
            return

        # Compress path: accumulate the (bounded, non-streaming) body, then emit
        # a single gzip frame once the response is complete.
        self.body.extend(message.get("body", b""))
        if message.get("more_body", False):
            return

        assert self.start_message is not None
        raw = bytes(self.body)
        if len(raw) < self.minimum_size:
            # Too small to be worth it — replay the original response verbatim.
            await self.send(self.start_message)
            await self.send({"type": "http.response.body", "body": raw, "more_body": False})
            return

        compressed = gzip.compress(raw, compresslevel=6)
        headers = MutableHeaders(raw=self.start_message["headers"])
        headers["content-encoding"] = "gzip"
        headers["content-length"] = str(len(compressed))
        vary = headers.get("vary")
        if not vary:
            headers["vary"] = "Accept-Encoding"
        elif "accept-encoding" not in vary.lower():
            headers["vary"] = f"{vary}, Accept-Encoding"

        await self.send(self.start_message)
        await self.send({"type": "http.response.body", "body": compressed, "more_body": False})
