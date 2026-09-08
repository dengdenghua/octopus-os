"""Compatibility helpers for Agent's trusted identity and token transports."""

from __future__ import annotations

from typing import Any

from starlette.requests import HTTPConnection

from runtime.platform.capabilities.tenant_context import current_capability_scope
from runtime.platform.process.session import current_session
from runtime.safety.auth.principal import LEGACY_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME
from runtime.safety.auth.websocket import websocket_bearer_token
from runtime.safety.recovery.tenant_scope import (
    trusted_scope_from_session,
    trusted_scope_from_user_context,
)


def websocket_token(scope: dict[str, Any]) -> str | None:
    """Use the same strict bearer/bearer.b64 decoder as the Agent gateway."""
    return websocket_bearer_token(HTTPConnection(scope))


def authorization_token(scope: dict[str, Any]) -> str:
    """Match Agent credential precedence; query parameters are never authority."""
    connection = HTTPConnection(scope)
    header = connection.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    if scope.get("type") == "websocket":
        token = websocket_token(scope)
        if token:
            return token
    return str(
        connection.cookies.get(SESSION_COOKIE_NAME)
        or connection.cookies.get(LEGACY_SESSION_COOKIE_NAME)
        or ""
    ).strip()


def validate_runtime_actor(actor: str) -> None:
    """Reject a different or malformed active Agent identity when one exists."""
    session = current_session()
    scopes = (
        current_capability_scope(),
        trusted_scope_from_session(session),
        trusted_scope_from_user_context(getattr(session, "metadata", None)),
    )
    for scope in scopes:
        if scope is not None and (scope.allow_cross_tenant or scope.actor_id != actor):
            raise ValueError("Agent scope does not match appliance authorization")
    session_actor = str(getattr(session, "actor", None) or "").strip()
    if session_actor and session_actor != actor:
        raise ValueError("Agent actor does not match appliance authorization")


__all__ = ["authorization_token", "validate_runtime_actor", "websocket_token"]
