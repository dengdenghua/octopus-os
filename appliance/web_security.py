"""Browser boundary for an Echo appliance exposed on a home network.

The Agent runtime already authenticates appliance APIs and rate-limits local
login failures.  This middleware closes the browser-specific gap around that
authentication: DNS-rebinding Host headers, cross-origin state changes, and
cookie-authenticated WebSocket hijacking.

Direct private/loopback IPs, localhost, mDNS names, and single-label LAN names
work without configuration.  A reverse-proxy/FQDN deployment must explicitly
declare its public host and origin so the trust boundary stays reviewable.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

TRUSTED_HOSTS_ENV = "ECHO_APPLIANCE_TRUSTED_HOSTS"
TRUSTED_ORIGINS_ENV = "ECHO_APPLIANCE_TRUSTED_ORIGINS"
FRAME_ORIGINS_ENV = "ECHO_APPLIANCE_FRAME_ORIGINS"
CONNECT_ORIGINS_ENV = "ECHO_APPLIANCE_CONNECT_ORIGINS"
REMOTE_ACCESS_URL_ENV = "ECHO_REMOTE_ACCESS_URL"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LAN_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def _csv_values(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _normalized_host(value: str) -> str:
    raw = value.strip()
    if not raw or any(ord(char) < 0x20 for char in raw):
        raise ValueError("empty or invalid host")
    parsed = urlsplit(f"//{raw}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("host credentials are forbidden")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid host port") from exc
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("invalid host")
    return hostname


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid origin port") from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("trusted origin must be an http(s) origin without a path")
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname.rstrip(".").casefold()
    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if port in {None, default_port} else f":{port}"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{display_host}{port_suffix}"


def _normalized_source_origin(value: str, *, schemes: frozenset[str]) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid source origin port") from exc
    scheme = parsed.scheme.casefold()
    if (
        scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("source must be an allowed origin without a path")
    hostname = parsed.hostname.rstrip(".").casefold()
    default_port = 443 if scheme in {"https", "wss"} else 80
    port_suffix = "" if port in {None, default_port} else f":{port}"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{display_host}{port_suffix}"


def _origin_from_url(value: str) -> str:
    """Extract an HTTP(S) origin from a configured service URL with a path."""
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("configured service URL must use http(s)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("configured service URL credentials are forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid configured service URL port") from exc
    hostname = parsed.hostname.rstrip(".").casefold()
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    port_suffix = "" if port in {None, default_port} else f":{port}"
    return f"{parsed.scheme.casefold()}://{display_host}{port_suffix}"


def _header(scope: dict[str, Any], name: str) -> str:
    encoded = name.casefold().encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key.lower() == encoded:
            return value.decode("latin-1").strip()
    return ""


def _is_default_lan_host(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname.endswith(".local") or bool(_LAN_HOSTNAME.fullmatch(hostname))
    return address.is_private or address.is_loopback or address.is_link_local


def _scope_origin(scope: dict[str, Any], host_header: str) -> str:
    scheme = str(scope.get("scheme") or "http").casefold()
    if scheme not in {"http", "https"}:
        scheme = "http"
    parsed_host = urlsplit(f"//{host_header}")
    hostname = parsed_host.hostname or ""
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed_host.port
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port in {None, default_port} else f":{port}"
    return _normalized_origin(f"{scheme}://{display_host}{suffix}")


class ApplianceWebSecurityMiddleware:
    """Reject untrusted browser entry points before they reach appliance APIs."""

    def __init__(
        self,
        app: Any,
        *,
        trusted_hosts: Iterable[str] | None = None,
        trusted_origins: Iterable[str] | None = None,
        frame_origins: Iterable[str] | None = None,
        connect_origins: Iterable[str] | None = None,
        storage_url: str | None = None,
    ) -> None:
        self.app = app
        raw_hosts = (
            tuple(trusted_hosts)
            if trusted_hosts is not None
            else _csv_values(os.environ.get(TRUSTED_HOSTS_ENV))
        )
        raw_origins = (
            tuple(trusted_origins)
            if trusted_origins is not None
            else _csv_values(os.environ.get(TRUSTED_ORIGINS_ENV))
        )
        raw_frame_origins = (
            tuple(frame_origins)
            if frame_origins is not None
            else _csv_values(os.environ.get(FRAME_ORIGINS_ENV))
        )
        raw_connect_origins = (
            tuple(connect_origins)
            if connect_origins is not None
            else _csv_values(os.environ.get(CONNECT_ORIGINS_ENV))
        )
        configured_storage = (
            storage_url if storage_url is not None else os.environ.get("ECHO_STORAGE_URL", "")
        ).strip()
        configured_remote_access = os.environ.get(REMOTE_ACCESS_URL_ENV, "").strip()
        try:
            hosts = {_normalized_host(item) for item in raw_hosts}
            origins = {_normalized_origin(item) for item in raw_origins}
            frames = {
                _normalized_source_origin(item, schemes=frozenset({"http", "https"}))
                for item in raw_frame_origins
            }
            connects = {
                _normalized_source_origin(
                    item,
                    schemes=frozenset({"http", "https", "ws", "wss"}),
                )
                for item in raw_connect_origins
            }
            if configured_storage:
                connects.add(_origin_from_url(configured_storage))
            if configured_remote_access:
                remote_origin = _normalized_origin(configured_remote_access)
                origins.add(remote_origin)
                hosts.add(_normalized_host(urlsplit(remote_origin).netloc))
            self.trusted_hosts = frozenset(hosts)
            self.trusted_origins = frozenset(origins)
            self.frame_origins = frozenset(frames)
            self.connect_origins = frozenset(connects)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid Echo appliance browser trust configuration: {exc}"
            ) from exc

    def _content_security_policy(
        self,
        scope: dict[str, Any],
        host_header: str,
    ) -> str:
        frame_sources = ["'self'", *sorted(self.frame_origins)]
        connect_sources = ["'self'", *sorted(self.connect_origins)]
        try:
            page_origin = _scope_origin(scope, host_header)
        except ValueError:
            page_origin = ""
        if page_origin:
            websocket_origin = (
                f"wss://{page_origin.removeprefix('https://')}"
                if page_origin.startswith("https://")
                else f"ws://{page_origin.removeprefix('http://')}"
            )
            connect_sources.append(websocket_origin)

        directives = (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "form-action 'self'",
            "frame-ancestors 'self'",
            "script-src 'self' 'wasm-unsafe-eval'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            "media-src 'self' data: blob: https:",
            "worker-src 'self' blob:",
            f"connect-src {' '.join(dict.fromkeys(connect_sources))}",
            f"frame-src {' '.join(frame_sources)}",
            "manifest-src 'self'",
        )
        return "; ".join(directives)

    def _response_send(self, scope: dict[str, Any], host_header: str, send: Any) -> Any:
        security_headers = {
            b"content-security-policy": self._content_security_policy(scope, host_header).encode(
                "ascii"
            ),
            b"referrer-policy": b"no-referrer",
            b"x-content-type-options": b"nosniff",
            b"x-frame-options": b"SAMEORIGIN",
        }

        async def _send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                original = list(message.get("headers") or ())
                original = [
                    (key, value) for key, value in original if key.lower() not in security_headers
                ]
                message = {
                    **message,
                    "headers": [*original, *security_headers.items()],
                }
            await send(message)

        return _send

    async def _http_error(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            {"detail": detail},
            status_code=status_code,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
        await response(scope, receive, send)

    @staticmethod
    async def _close_websocket(send: Any) -> None:
        await send({"type": "websocket.close", "code": 1008, "reason": "origin rejected"})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        host_header = _header(scope, "host")
        response_send = (
            self._response_send(scope, host_header, send) if scope_type == "http" else send
        )
        try:
            hostname = _normalized_host(host_header)
        except ValueError:
            hostname = ""
        if not hostname or (
            hostname not in self.trusted_hosts and not _is_default_lan_host(hostname)
        ):
            if scope_type == "websocket":
                await self._close_websocket(send)
            else:
                await self._http_error(
                    scope,
                    receive,
                    response_send,
                    status_code=400,
                    detail="untrusted Host header",
                )
            return

        origin = _header(scope, "origin")
        if origin:
            try:
                normalized_origin = _normalized_origin(origin)
                allowed_origins = self.trusted_origins | {_scope_origin(scope, host_header)}
            except ValueError:
                normalized_origin = ""
                allowed_origins = self.trusted_origins
            if normalized_origin not in allowed_origins:
                if scope_type == "websocket":
                    await self._close_websocket(send)
                else:
                    await self._http_error(
                        scope,
                        receive,
                        response_send,
                        status_code=403,
                        detail="cross-origin request rejected",
                    )
                return

        if scope_type == "websocket":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        requested_method = _header(scope, "access-control-request-method").upper()
        changes_state = method in _UNSAFE_METHODS or (
            method == "OPTIONS" and requested_method in _UNSAFE_METHODS
        )
        fetch_site = _header(scope, "sec-fetch-site").casefold()
        path = str(scope.get("path") or "")
        if (
            changes_state
            and path.startswith("/api/")
            and not origin
            and fetch_site in {"cross-site", "same-site"}
        ):
            await self._http_error(
                scope,
                receive,
                response_send,
                status_code=403,
                detail="browser Origin header required",
            )
            return

        await self.app(scope, receive, response_send)


__all__ = [
    "ApplianceWebSecurityMiddleware",
    "CONNECT_ORIGINS_ENV",
    "FRAME_ORIGINS_ENV",
    "REMOTE_ACCESS_URL_ENV",
    "TRUSTED_HOSTS_ENV",
    "TRUSTED_ORIGINS_ENV",
]
