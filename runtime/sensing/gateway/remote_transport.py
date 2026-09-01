"""
Remote Transport · connect a desktop session to a remote
echo-agent runtime over SSH-tunneled HTTP.

The desktop binary runs locally but routes every API call to a backend
process running on a different host. The user sees the same UI;
the work runs where the data is.

Scope of this module
--------------------
This is the **scaffold**. It provides:

  * The ``RemoteBackend`` data model (one named remote target with
    SSH params + cached health status).
  * A ``BackendRegistry`` that persists the list to disk via
    ``atomic_write_json``.
  * An ``HTTPProxy`` helper that makes outbound calls to a remote
    backend's HTTP/SSE endpoint, optionally tunneled through SSH.
  * Health-check helpers (one-shot + periodic).

What this module does NOT do (yet)
----------------------------------
  * Bidirectional SSE/WebSocket streaming. SSE proxy is sketched
    via httpx streaming but the existing routers haven't been
    rewired to use it.
  * SSH key generation / host enrollment workflow. Remote setup is
    user's responsibility for now (point at an existing
    reachable HTTP endpoint).
  * Failover.

Both follow-ups are isolated by ``ui.remote_transport`` feature
flag — until that ships, all routes return 403.

Storage
-------
``data/remote_backends.json``::

    {
      "backends": [
        { "id": "...", "name": "...", "url": "https://host:8000",
          "ssh": null | { "host": "...", "user": "...", "port": 22,
                          "identity_file": "..." },
          "added_at": "...", "last_health": "ok" | "error" | null,
          "last_health_at": "..." }
      ]
    }
"""

from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter

# Inbound anti-abuse ceiling on the relayed client WS, mirroring the
# team-rooms handler. The relay forwards every frame to the remote
# backend, so an unbounded local client could push arbitrary memory at
# the upstream; bounding the frame size and rate keeps a single bad
# client from amplifying a flood. Lenient — legit JSON-RPC frames are a
# few KB.
_PROXY_WS_MAX_INBOUND_BYTES = 256 * 1024
_PROXY_WS_MSG_PER_SEC = 30

_LOG = logging.getLogger("echo.remote_transport")


# ═══════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════


@dataclass
class SshTunnel:
    """SSH transport descriptor. Mirrors ``SshBackend`` config so an
    existing SSH-trusted host can be reused without re-entering
    credentials.
    """

    host: str
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    connect_timeout: int = 10

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SshTunnel | None:
        try:
            host = str(raw.get("host") or "").strip()
            if not host:
                return None
            return cls(
                host=host,
                user=raw.get("user") or None,
                port=int(raw.get("port") or 22),
                identity_file=raw.get("identity_file") or None,
                connect_timeout=int(raw.get("connect_timeout") or 10),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class RemoteBackend:
    """One named remote echo-agent runtime."""

    id: str
    name: str
    url: str  # http(s)://host:port
    ssh: SshTunnel | None = None
    added_at: str = ""
    last_health: str | None = None  # "ok" | "error" | None (untested)
    last_health_at: str | None = None
    health_detail: str | None = None  # last error message

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ssh"] = self.ssh.to_dict() if self.ssh else None
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RemoteBackend | None:
        try:
            bid = str(raw["id"]).strip()
            name = str(raw["name"]).strip()
            url = str(raw["url"]).strip()
            if not bid or not name or not url:
                return None
            ssh_raw = raw.get("ssh")
            ssh = SshTunnel.from_dict(ssh_raw) if isinstance(ssh_raw, dict) else None
            return cls(
                id=bid,
                name=name,
                url=url,
                ssh=ssh,
                added_at=str(raw.get("added_at") or ""),
                last_health=raw.get("last_health"),
                last_health_at=raw.get("last_health_at"),
                health_detail=raw.get("health_detail"),
            )
        except (KeyError, TypeError, ValueError):
            return None


# ═══════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════


def _validate_url(url: str) -> str:
    """Return the canonicalized URL or raise ValueError."""
    text = (url or "").strip().rstrip("/")
    if not text:
        raise ValueError("url is required")
    if not (text.startswith("http://") or text.startswith("https://")):
        raise ValueError(
            f"url must start with http:// or https:// (got {text!r})",
        )
    return text


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ═══════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════


class BackendRegistry:
    """Process-wide cache of registered remote backends, persisted
    to ``<data>/remote_backends.json``.

    Single global lock — registrations happen rarely (interactive
    UI), no need for fine-grained concurrency.
    """

    def __init__(self, store_path: str | Path) -> None:
        self._path = Path(store_path)
        self._lock = threading.RLock()
        self._backends: dict[str, RemoteBackend] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        raw = read_json_with_backup(self._path, default={})
        if not isinstance(raw, dict):
            return
        for entry in raw.get("backends") or []:
            if isinstance(entry, dict):
                b = RemoteBackend.from_dict(entry)
                if b is not None:
                    self._backends[b.id] = b

    def _flush(self) -> None:
        payload = {
            "backends": [b.to_dict() for b in self._backends.values()],
        }
        atomic_write_json(self._path, payload)

    def list(self) -> list[RemoteBackend]:
        with self._lock:
            return list(self._backends.values())

    def get(self, backend_id: str) -> RemoteBackend | None:
        with self._lock:
            return self._backends.get(backend_id)

    def get_by_name(self, name: str) -> RemoteBackend | None:
        with self._lock:
            for b in self._backends.values():
                if b.name == name:
                    return b
            return None

    def add(
        self,
        *,
        name: str,
        url: str,
        ssh: SshTunnel | None = None,
    ) -> RemoteBackend:
        canonical_url = _validate_url(url)
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        with self._lock:
            if any(b.name == clean_name for b in self._backends.values()):
                raise ValueError(f"backend named {clean_name!r} already exists")
            backend = RemoteBackend(
                id=uuid4().hex,
                name=clean_name,
                url=canonical_url,
                ssh=ssh,
                added_at=_now_iso(),
            )
            self._backends[backend.id] = backend
            self._flush()
            return backend

    def remove(self, backend_id: str) -> bool:
        with self._lock:
            if backend_id not in self._backends:
                return False
            del self._backends[backend_id]
            self._flush()
            return True

    def update_health(
        self,
        backend_id: str,
        *,
        status: str,
        detail: str | None = None,
    ) -> RemoteBackend | None:
        if status not in {"ok", "error"}:
            raise ValueError(f"status must be 'ok' or 'error' (got {status!r})")
        with self._lock:
            backend = self._backends.get(backend_id)
            if backend is None:
                return None
            backend.last_health = status
            backend.last_health_at = _now_iso()
            backend.health_detail = detail
            self._flush()
            return backend


# ═══════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════


def health_check(
    backend: RemoteBackend,
    *,
    timeout_seconds: float = 5.0,
    http_client: Any = None,
) -> tuple[str, str | None]:
    """Hit ``<url>/api/health`` and return (status, detail).

    ``http_client`` lets tests inject a stub. Production passes
    ``None`` and we lazy-import ``httpx``. We keep this function
    sync — caller can run it in a worker thread or asyncio
    executor as needed.
    """
    target = backend.url.rstrip("/") + "/api/health"
    try:
        if http_client is None:
            from runtime.safety.auth.url_guard import safe_httpx_request

            resp = safe_httpx_request("GET", target, timeout=timeout_seconds)
            ok = 200 <= resp.status_code < 300
            detail = None if ok else f"HTTP {resp.status_code}"
        else:
            resp = http_client.get(target, timeout=timeout_seconds)
            ok = 200 <= getattr(resp, "status_code", 0) < 300
            detail = None if ok else f"HTTP {resp.status_code}"
    except (ConnectionError, TimeoutError) as exc:
        return "error", f"{type(exc).__name__}: {exc}"
    return ("ok" if ok else "error"), detail


# ═══════════════════════════════════════════════════════════
# HTTP proxy (one-shot, non-streaming)
# ═══════════════════════════════════════════════════════════


def proxy_request(
    backend: RemoteBackend,
    *,
    method: str,
    path: str,
    json: Any = None,
    timeout_seconds: float = 30.0,
    http_client: Any = None,
) -> dict[str, Any]:
    """Forward a request to a remote backend.

    Returns ``{"status_code": int, "body": dict | str}``.

    Streaming endpoints (SSE, WebSocket) are intentionally NOT
    handled here — those need a different proxy (buffer-free
    pump) and live behind the ``ui.remote_transport`` flag until
    that landing happens. Routes that need streaming should fall
    through to the local handler when the backend is remote and
    surface a "streaming over remote backend not yet supported"
    error in the UI.
    """
    method = (method or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"unsupported method {method!r}")
    target = backend.url.rstrip("/") + "/" + path.lstrip("/")
    try:
        if http_client is None:
            from runtime.safety.auth.url_guard import safe_httpx_request

            resp = safe_httpx_request(
                method,
                target,
                json=json,
                timeout=timeout_seconds,
            )
        else:
            resp = http_client.request(
                method,
                target,
                json=json,
                timeout=timeout_seconds,
            )
        status = int(getattr(resp, "status_code", 0) or 0)
        try:
            body: Any = resp.json() if hasattr(resp, "json") else resp.text
            if callable(body):
                body = body()
        except (json.JSONDecodeError, AttributeError):
            body = getattr(resp, "text", "") if hasattr(resp, "text") else ""
        return {"status_code": status, "body": body}
    except (ConnectionError, TimeoutError) as exc:
        return {
            "status_code": 0,
            "body": {
                "error": "proxy_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        }


__all__ = [
    "BackendRegistry",
    "RemoteBackend",
    "SshTunnel",
    "health_check",
    "proxy_request",
    "proxy_websocket",
]


# ═══════════════════════════════════════════════════════════
# WebSocket proxy (bidirectional, JSON-RPC 2.0)
# ═══════════════════════════════════════════════════════════
#
# The remote echo-agent runtime exposes its realtime gateway at
# ``<url>/api/realtime``. We tunnel a desktop client's WebSocket
# session through to that endpoint by:
#
#   1. Translate ``http(s)://`` → ``ws(s)://`` for the upstream URL
#   2. Open an ``httpx.AsyncClient`` WebSocket (or ``websockets``
#      package — both are stdlib-adjacent; we use ``websockets``
#      as it's already a starlette dependency)
#   3. Pump messages in both directions until either side closes
#
# Two relay tasks: client→remote and remote→client. ``asyncio.wait``
# with ``FIRST_COMPLETED`` lets us detect either side's close and
# tear down cleanly. No buffering — every frame is forwarded as it
# arrives, so JSON-RPC request/response/notification semantics pass
# through transparently.


def _to_ws_url(http_url: str, path: str) -> str:
    """Translate an HTTP backend URL to its WebSocket form, joined
    with ``path``. ``https://`` becomes ``wss://``, ``http://``
    becomes ``ws://``.
    """
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        # Already a ws:// URL or something weird — pass through.
        ws_base = base
    return ws_base + "/" + path.lstrip("/")


async def proxy_websocket(
    backend: RemoteBackend,
    client_ws: Any,
    *,
    path: str = "/api/realtime",
    upstream_factory: Any = None,
) -> None:
    """Bidirectionally relay a WebSocket session to the remote
    backend's realtime gateway.

    ``client_ws`` is the local ``starlette.websockets.WebSocket``
    that the operator's browser/Electron has opened to *us*. We
    open a second WebSocket to the remote backend and pump frames
    in both directions.

    Returns when either side closes. Caller is responsible for
    accepting the client websocket BEFORE calling (so error
    responses can still be sent if the upstream connect fails).

    ``upstream_factory`` lets tests inject a stub. It must be an
    async context manager yielding an object with
    ``send(text: str)`` / ``recv() -> str`` / ``close()``.
    """
    upstream_url = _to_ws_url(backend.url, path)
    from urllib.parse import urlparse

    from runtime.safety.auth.url_guard import check_url

    parsed_upstream = urlparse(upstream_url)
    http_equivalent = parsed_upstream._replace(
        scheme="https" if parsed_upstream.scheme == "wss" else "http"
    ).geturl()
    verdict = check_url(http_equivalent, allow_private=False) if upstream_factory is None else None
    if verdict is not None and not verdict.allow:
        await client_ws.send_text(_ws_error_envelope(f"url_guard rejected: {verdict.reason}"))
        await client_ws.close(code=1008)
        return

    # Lazy-import websockets (heavy dep, server-only).
    if upstream_factory is None:
        assert verdict is not None
        try:
            import websockets  # type: ignore[import-not-found]
        except ImportError as exc:
            await client_ws.send_text(
                _ws_error_envelope(
                    "websockets package not available — pip install websockets",
                ),
            )
            await client_ws.close(code=1011)
            raise RuntimeError("websockets package required") from exc
        connect_kwargs: dict[str, Any] = {"max_size": None, "proxy": None}
        if verdict.resolved_ip:
            # websockets uses ``host`` for the TCP dial target while retaining
            # the URI hostname for the WebSocket Host/SNI identity. This pins
            # the connection to the address that passed the guard.
            connect_kwargs["host"] = verdict.resolved_ip
        connect = websockets.connect(upstream_url, **connect_kwargs)
    else:
        connect = upstream_factory(upstream_url)

    try:
        async with connect as upstream:
            # Per-connection inbound guard, local to this pump so it's
            # freed when the connection closes. Oversized frames are
            # dropped before relay; a runaway client's sustained flood is
            # shed without forwarding. Mirrors ``team_rooms_ws``.
            _inbound_limiter = SlidingWindowLimiter(
                limit=_PROXY_WS_MSG_PER_SEC,
                window_s=1.0,
            )

            async def _client_to_remote() -> None:
                while True:
                    try:
                        msg = await client_ws.receive_text()
                    except Exception as exc:
                        _LOG.debug("client_ws receive_text broke: %s", exc)
                        break
                    if len(msg) > _PROXY_WS_MAX_INBOUND_BYTES:
                        _LOG.warning(
                            "proxy: dropping %d-byte inbound frame (limit %d)",
                            len(msg),
                            _PROXY_WS_MAX_INBOUND_BYTES,
                        )
                        continue
                    if not _inbound_limiter.allow("inbound"):
                        _LOG.debug("proxy: shedding over-rate inbound frame")
                        continue
                    try:
                        await upstream.send(msg)
                    except Exception as exc:
                        _LOG.debug("upstream send broke: %s", exc)
                        break

            async def _remote_to_client() -> None:
                while True:
                    try:
                        msg = await upstream.recv()
                    except Exception as exc:
                        _LOG.debug("upstream recv broke: %s", exc)
                        break
                    if not isinstance(msg, str):
                        # Binary frames — re-encode.
                        try:
                            msg = msg.decode("utf-8")  # type: ignore[union-attr]
                        except (AttributeError, UnicodeDecodeError) as exc:
                            _LOG.debug("binary decode skipped: %s", exc)
                            continue
                    try:
                        await client_ws.send_text(msg)
                    except Exception as exc:
                        _LOG.debug("client_ws send_text broke: %s", exc)
                        break

            import asyncio

            t1 = asyncio.create_task(_client_to_remote())
            t2 = asyncio.create_task(_remote_to_client())
            done, pending = await asyncio.wait(
                {t1, t2},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(Exception):
                    await task
    except Exception as exc:
        _LOG.warning("websocket proxy failed: %s", exc)
        with contextlib.suppress(Exception):
            await client_ws.send_text(
                _ws_error_envelope(f"upstream failed: {type(exc).__name__}: {exc}"),
            )


def _ws_error_envelope(message: str) -> str:
    """Compose a JSON-RPC 2.0 error notification frame.

    Clients of the realtime gateway already parse JSON-RPC envelopes,
    so wrapping the proxy error in the same shape lets the existing
    error handler render it without a separate code path.
    """
    import json

    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "proxy/error",
            "params": {"message": message},
        }
    )


# ═══════════════════════════════════════════════════════════
# Streaming proxy removed; realtime uses the WebSocket relay.
# ═══════════════════════════════════════════════════════════
#
# The WebSocket relay at ``proxy_websocket`` /
# ``/api/remote-backends/{id}/realtime`` is the sole live-turn path.
