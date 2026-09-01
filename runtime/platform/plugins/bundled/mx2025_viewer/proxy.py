"""Hardened opt-in same-origin proxy for the MX2025 viewer.

The proxy deliberately carries no Echo authentication state. Enabling it
still gives upstream JavaScript this application's origin, so the plugin keeps
the route behind two explicit local-only configuration switches as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import re
from collections.abc import AsyncIterator
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse

_logger = logging.getLogger(__name__)

_REQUEST_BODY_LIMIT = 1024 * 1024
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ALLOWED_PATH_ROOTS = frozenset(
    {"pages", "assets", "static", "api", "img", "uni", "socket.io", "3", "5"}
)
_ALLOWED_ROOT_FILES = frozenset({"favicon.ico", "index.html", "manifest.json"})
_PROXY_PUBLIC_PREFIX = "/api/plugins/mx2025_viewer/origin"
_PROXY_ASSET_VERSION = "7"
_REWRITABLE_CONTENT_TYPES = (
    "text/html",
    "text/css",
    "application/javascript",
    "text/javascript",
)
_ROOT_PATH_RE = re.compile(
    r"(?P<lead>[\"'(=:,\s])(?P<slash>/?)"
    r"(?P<root>assets|static|api|img|uni|pages|socket\.io|3|5)"
    r"(?P<tail>/|(?=[\"']))"
)
_REWRITTEN_ASSET_RE = re.compile(
    re.escape(f"{_PROXY_PUBLIC_PREFIX.lstrip('/')}/assets/") + r"[^\"'\s>,\]]+"
)
_RELATIVE_JS_MODULE_RE = re.compile(r"(?P<quote>[\"'])\./(?P<path>[^\"'?\s]+\.js)(?P=quote)")

_FORWARDED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "content-type",
        "if-modified-since",
        "if-none-match",
        "range",
        # MX application headers. ``authorization`` and ``cookie`` remain
        # forbidden so Echo host credentials can never cross this boundary.
        "token",
        "ad",
        "version",
        "i",
        "user-agent",
    }
)
_FORWARDED_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-encoding",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }
)

# Third-party code is still same-origin when the operator explicitly opts in,
# but it may only load/connect back through this origin. In particular, the
# upstream cannot remove this policy or widen it to arbitrary network targets.
_PROXY_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; media-src 'self'; "
    "frame-src 'self'; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
_SECURITY_RESPONSE_HEADERS = {
    "Content-Security-Policy": _PROXY_CSP,
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
}
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _fully_unquote_path(value: str) -> str | None:
    """Decode nested percent escapes, rejecting intentionally deep ambiguity."""
    decoded = value
    for _ in range(8):
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    return None


def _canonical_host(hostname: str) -> str:
    if "%" in hostname:
        return ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            candidate = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            return ""
        labels = candidate.split(".")
        if not candidate or len(candidate) > 253 or any(not label for label in labels):
            return ""
        if all(label.isdigit() for label in labels):
            # Reject ambiguous non-canonical integer/dotted IPv4 spellings.
            return ""
        if not all(_HOST_LABEL_RE.fullmatch(label) is not None for label in labels):
            return ""
        return candidate
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def secure_upstream_origin(base_url: str) -> str | None:
    """Return a normalized HTTPS base URL, rejecting ambiguous URLs."""
    raw = str(base_url or "").strip()
    if not raw or "\\" in raw or any(ord(ch) < 33 for ch in raw):
        return None
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    canonical_host = _canonical_host(hostname)
    if not canonical_host or port == 0:
        return None

    decoded_path = _fully_unquote_path(parsed.path or "")
    if decoded_path is None:
        return None
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return None
    if "\\" in decoded_path or any(ord(ch) < 32 for ch in decoded_path):
        return None

    netloc = canonical_host
    if port is not None:
        netloc += f":{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def _safe_upstream_path(raw: str) -> str | None:
    """Validate one relative path under the fixed upstream origin."""
    decoded = _fully_unquote_path(str(raw or ""))
    if decoded is None:
        return None
    clean = decoded.strip().lstrip("/")
    if not clean:
        return ""
    trailing_slash = clean.endswith("/")
    if trailing_slash:
        clean = clean.rstrip("/")
        if not clean:
            return ""
    if "\\" in clean or "?" in clean or "#" in clean:
        return None
    if any(ord(ch) < 32 for ch in clean):
        return None
    segments = clean.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    root = segments[0]
    if len(segments) == 1 and root in _ALLOWED_ROOT_FILES:
        return clean + ("/" if trailing_slash else "")
    return clean + ("/" if trailing_slash else "") if root in _ALLOWED_PATH_ROOTS else None


async def _bounded_request_body(request: Request) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length)
        except ValueError as exc:
            raise HTTPException(400, "invalid content-length") from exc
        if declared_length < 0:
            raise HTTPException(400, "invalid content-length")
        if declared_length > _REQUEST_BODY_LIMIT:
            raise HTTPException(413, "request body too large")
    if request.method.upper() not in _BODY_METHODS:
        return b""

    chunks = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        chunks.extend(chunk)
        if len(chunks) > _REQUEST_BODY_LIMIT:
            raise HTTPException(413, "request body too large")
    return bytes(chunks)


def _upstream_headers(request: Request, origin: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_REQUEST_HEADERS
    }
    # Never forward the browser's host Origin/Referer. Fixed upstream values
    # reveal no Echo URL or principal state and keep public upstream checks
    # deterministic.
    parsed = urlsplit(origin)
    headers["Origin"] = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    headers["Referer"] = origin + "/"
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() in _FORWARDED_RESPONSE_HEADERS
    }
    headers.update(_SECURITY_RESPONSE_HEADERS)
    return headers


def _rewrite_proxy_root_paths(body: bytes, content_type: str) -> bytes:
    """Keep root-relative upstream assets/API calls inside the proxy prefix.

    MX's SPA emits absolute paths such as ``/assets/app.js`` and ``/3/api``.
    Without rewriting, a nested plugin page sends those requests to Echo's
    root and the application renders blank. Only executable/style documents
    are transformed; encrypted API payloads and user content remain untouched.
    """
    if not any(kind in content_type.lower() for kind in _REWRITABLE_CONTENT_TYPES):
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    lowered_type = content_type.lower()
    allowed_roots = {"assets", "static", "img", "uni"}
    if "javascript" in lowered_type:
        # /api/* and /pages/* are route arguments joined onto Ex/kx or handled
        # by uni-app's client router. Only the API host prefixes are external.
        allowed_roots.update({"socket.io", "3", "5"})

    def replace(match: re.Match[str]) -> str:
        if match.group("root") not in allowed_roots:
            return match.group(0)
        # Vite's preload table stores relative ``assets/*`` entries and adds
        # its own leading slash at runtime. Preserve that contract to avoid a
        # protocol-relative ``//api/...`` URL; true root paths remain absolute.
        public_prefix = _PROXY_PUBLIC_PREFIX
        if "javascript" in lowered_type and not match.group("slash"):
            public_prefix = public_prefix.lstrip("/")
        return f"{match.group('lead')}{public_prefix}/{match.group('root')}{match.group('tail')}"

    rewritten = _ROOT_PATH_RE.sub(replace, text)
    if "javascript" in lowered_type:
        rewritten = _RELATIVE_JS_MODULE_RE.sub(
            lambda match: (
                f"{match.group('quote')}./{match.group('path')}"
                f"?echo_proxy={_PROXY_ASSET_VERSION}{match.group('quote')}"
            ),
            rewritten,
        )
    if "text/html" in lowered_type or "javascript" in lowered_type:
        rewritten = _REWRITTEN_ASSET_RE.sub(
            lambda match: (
                match.group(0)
                if "?" in match.group(0)
                else f"{match.group(0)}?echo_proxy={_PROXY_ASSET_VERSION}"
            ),
            rewritten,
        )
    return rewritten.encode("utf-8")


def _rewrite_login_host(body: bytes) -> bytes:
    """Route the post-login shard (usually ``/5``) back through the proxy."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    host = payload.get("hosturl")
    if host not in {"/3", "/5"}:
        return body
    payload["hosturl"] = f"{_PROXY_PUBLIC_PREFIX}{host}"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def register_origin_proxy(
    router: APIRouter,
    *,
    base_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Mount ``/origin/*`` only for an unambiguous HTTPS upstream."""
    origin = secure_upstream_origin(base_url)
    if not origin:
        _logger.warning("mx2025_viewer proxy rejected: upstream must be a valid HTTPS URL")
        return False

    @router.websocket("/origin/socket.io/")
    async def proxy_websocket(websocket: WebSocket) -> None:
        """Bridge the upstream Socket.IO transport without exposing host auth."""
        local_origin = websocket.headers.get("origin", "")
        local_host = websocket.headers.get("host", "")
        if local_origin not in {f"http://{local_host}", f"https://{local_host}"}:
            await websocket.close(code=1008)
            return

        parsed = urlsplit(origin)
        upstream_ws = urlunsplit(
            (
                "wss",
                parsed.netloc,
                f"{parsed.path}/socket.io/" if parsed.path else "/socket.io/",
                str(websocket.query_params),
                "",
            )
        )
        upstream_origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

        try:
            import websockets

            upstream = await websockets.connect(
                upstream_ws,
                origin=upstream_origin,
                compression=None,
                proxy=None,
                ping_interval=None,
                open_timeout=10,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed at the WS boundary
            _logger.warning("mx2025_viewer websocket unavailable (%s)", type(exc).__name__)
            await websocket.close(code=1013)
            return

        await websocket.accept()

        async def browser_to_upstream() -> None:
            while True:
                packet = await websocket.receive()
                if packet.get("type") == "websocket.disconnect":
                    return
                if packet.get("text") is not None:
                    await upstream.send(packet["text"])
                elif packet.get("bytes") is not None:
                    await upstream.send(packet["bytes"])

        async def upstream_to_browser() -> None:
            async for packet in upstream:
                if isinstance(packet, bytes):
                    await websocket.send_bytes(packet)
                else:
                    await websocket.send_text(packet)

        tasks = {
            asyncio.create_task(browser_to_upstream()),
            asyncio.create_task(upstream_to_browser()),
        }
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(Exception):
                    task.result()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await upstream.close()
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1000)

    async def proxy_http(request: Request, upstream_path: str):
        safe_path = _safe_upstream_path(upstream_path)
        if safe_path is None:
            raise HTTPException(404, "upstream path not available")
        body = await _bounded_request_body(request)

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
        )
        target = f"{origin}/{safe_path}"
        try:
            upstream_request = client.build_request(
                request.method,
                target,
                params=list(request.query_params.multi_items()),
                headers=_upstream_headers(request, origin),
                content=body or None,
            )
            # A caller-provided client may have default headers/cookies. Strip
            # these again after merging so tests and future reuse cannot
            # accidentally reintroduce host credentials.
            for sensitive_header in ("authorization", "proxy-authorization", "cookie"):
                upstream_request.headers.pop(sensitive_header, None)
            upstream_resp = await client.send(upstream_request, stream=True, auth=None)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if owns_client:
                await client.aclose()
            _logger.warning("mx2025_viewer upstream unavailable (%s)", type(exc).__name__)
            return JSONResponse(
                {"detail": "MX upstream temporarily unavailable"},
                status_code=503,
                headers={"Retry-After": "10", **_SECURITY_RESPONSE_HEADERS},
            )

        content_type = upstream_resp.headers.get("content-type", "")
        if "application/json" in content_type.lower() and safe_path in {
            "api/login",
            "3/api/login",
            "5/api/login",
        }:
            try:
                login_body = _rewrite_login_host(await upstream_resp.aread())
            finally:
                await upstream_resp.aclose()
                if owns_client:
                    await client.aclose()
            login_headers = _response_headers(upstream_resp)
            login_headers.pop("content-encoding", None)
            login_headers.pop("content-length", None)
            return Response(
                login_body,
                status_code=upstream_resp.status_code,
                headers=login_headers,
                media_type=None,
            )
        if any(kind in content_type.lower() for kind in _REWRITABLE_CONTENT_TYPES):
            try:
                rewritten_body = _rewrite_proxy_root_paths(
                    await upstream_resp.aread(),
                    content_type,
                )
            finally:
                await upstream_resp.aclose()
                if owns_client:
                    await client.aclose()
            rewritten_headers = _response_headers(upstream_resp)
            rewritten_headers.pop("content-encoding", None)
            rewritten_headers.pop("content-length", None)
            rewritten_headers["Cache-Control"] = "no-store"
            return Response(
                rewritten_body,
                status_code=upstream_resp.status_code,
                headers=rewritten_headers,
                media_type=None,
            )

        # Mock/custom transports may return an already-buffered response even
        # when ``stream=True``. Production HTTP transports remain streaming;
        # support both without trying to consume a response twice.
        buffered_body = upstream_resp.content if upstream_resp.is_stream_consumed else None

        async def stream_body() -> AsyncIterator[bytes]:
            try:
                if buffered_body is not None:
                    if buffered_body:
                        yield buffered_body
                else:
                    async for chunk in upstream_resp.aiter_raw():
                        if chunk:
                            yield chunk
            finally:
                await upstream_resp.aclose()
                if owns_client:
                    await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream_resp.status_code,
            headers=_response_headers(upstream_resp),
        )

    # One method per route keeps OpenAPI operation IDs deterministic.
    for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        router.add_api_route(
            "/origin/{upstream_path:path}",
            proxy_http,
            methods=[method],
            operation_id=f"mx2025_viewer_proxy_{method.lower()}",
            include_in_schema=False,
        )

    _logger.info("mx2025_viewer origin proxy enabled for a validated HTTPS upstream")
    return True


__all__ = ["register_origin_proxy", "secure_upstream_origin"]
