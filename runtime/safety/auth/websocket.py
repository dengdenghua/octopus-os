"""Shared browser-safe WebSocket bearer-token transport helpers."""

from __future__ import annotations

import base64
import binascii
from typing import Any

WEBSOCKET_BEARER_B64_PROTOCOL = "bearer.b64"
WEBSOCKET_BEARER_LEGACY_PROTOCOL = "bearer"


def offered_websocket_subprotocols(connection: Any) -> list[str]:
    """Return the handshake protocols in client order.

    Starlette exposes a parsed list on ``scope``. Small test doubles and
    alternate ASGI wrappers may expose only the raw header, so retain a
    header fallback instead of making every router duplicate it.
    """

    scope = getattr(connection, "scope", None)
    if isinstance(scope, dict):
        offered = scope.get("subprotocols")
        if isinstance(offered, (list, tuple)):
            # Some WebSocket reverse proxies preserve the whole
            # ``Sec-WebSocket-Protocol`` header as one scope entry instead of
            # exposing one entry per protocol. Normalise both shapes here so
            # callers see the same list for direct and proxied handshakes.
            return [
                protocol
                for item in offered
                for protocol in (part.strip() for part in str(item).split(","))
                if protocol
            ]

    headers = getattr(connection, "headers", {}) or {}
    raw = str(headers.get("sec-websocket-protocol") or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def websocket_bearer_token(connection: Any) -> str | None:
    """Decode a bearer token offered through WebSocket subprotocols.

    Current browser clients send ``bearer.b64, <base64url(token)>`` so any
    UTF-8 credential is legal in the handshake without entering the URL.
    ``bearer, <token>`` remains accepted for rolling upgrades.
    """

    offered = offered_websocket_subprotocols(connection)
    for marker in (WEBSOCKET_BEARER_B64_PROTOCOL, WEBSOCKET_BEARER_LEGACY_PROTOCOL):
        for index, item in enumerate(offered[:-1]):
            if item.lower() != marker:
                continue
            value = offered[index + 1]
            if marker == WEBSOCKET_BEARER_LEGACY_PROTOCOL:
                return value or None
            try:
                encoded = value.encode("ascii")
                padding = b"=" * (-len(encoded) % 4)
                decoded = base64.b64decode(
                    encoded + padding,
                    altchars=b"-_",
                    validate=True,
                ).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError):
                return None
            return decoded or None
    return None


def accepted_auth_subprotocol(connection: Any) -> str | None:
    """Select only the non-secret auth marker from the client's offer."""

    offered = offered_websocket_subprotocols(connection)
    for marker in (WEBSOCKET_BEARER_B64_PROTOCOL, WEBSOCKET_BEARER_LEGACY_PROTOCOL):
        for item in offered:
            if item.lower() == marker:
                # WebSocket protocol selection is case-sensitive. Echo the
                # exact spelling supplied by the browser, never the token.
                return item
    return None


__all__ = [
    "WEBSOCKET_BEARER_B64_PROTOCOL",
    "WEBSOCKET_BEARER_LEGACY_PROTOCOL",
    "accepted_auth_subprotocol",
    "offered_websocket_subprotocols",
    "websocket_bearer_token",
]
