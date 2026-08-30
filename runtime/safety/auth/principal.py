"""Request principal resolution and role gates for shared deployments.

This module is intentionally small and framework-aware only at the HTTP
boundary.  Routers should use the resolved principal for authorization and
must never treat user-controlled body/query fields as identity.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

try:
    from fastapi import HTTPException
except ImportError:  # pragma: no cover - keeps the core auth package optional

    class HTTPException(RuntimeError):  # type: ignore[no-redef]
        def __init__(
            self,
            status_code: int,
            detail: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
            self.headers = headers


from .identity import Identity, IdentityStore
from .websocket import websocket_bearer_token

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SESSION_COOKIE_NAME = "echo_session"
LEGACY_SESSION_COOKIE_NAME = "octo" + "pus_session"
AUTH_EXPIRED_HEADER = "X-Echo-Auth-Expired"
_LEGACY_AGENT_ISSUER = "octo" + "pus-agent"


def authentication_error(detail: str) -> HTTPException:
    """Return a host-session 401 distinguishable from downstream 401s."""

    return HTTPException(401, detail, headers={AUTH_EXPIRED_HEADER: "1"})


def set_session_cookie(
    response: Any,
    request: Any,
    token: str,
    *,
    max_age: int,
) -> None:
    """Persist a browser session without exposing the JWT to JavaScript."""

    url = getattr(request, "url", None)
    secure = str(getattr(url, "scheme", "") or "").lower() == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max(1, int(max_age)),
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Any, request: Any) -> None:
    """Expire the browser session cookie using the same transport policy."""

    url = getattr(request, "url", None)
    secure = str(getattr(url, "scheme", "") or "").lower() == "https"
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=LEGACY_SESSION_COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


@dataclass(frozen=True)
class CurrentPrincipal:
    """Verified request identity used by authorization decisions."""

    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    authn_method: str
    request_id: str


def _request_token(request: Any) -> str:
    headers = getattr(request, "headers", {}) or {}
    authorization = str(headers.get("Authorization") or "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    # Browser WebSocket clients cannot set an Authorization header. Current
    # clients use the shared base64url subprotocol transport; the helper also
    # accepts the original raw ``bearer`` shape for rolling upgrades.
    subprotocol_token = websocket_bearer_token(request)
    if subprotocol_token:
        return subprotocol_token
    cookies = getattr(request, "cookies", {}) or {}
    return str(
        cookies.get(SESSION_COOKIE_NAME) or cookies.get(LEGACY_SESSION_COOKIE_NAME) or ""
    ).strip()


def _request_id(request: Any) -> str:
    headers = getattr(request, "headers", {}) or {}
    candidate = str(headers.get("X-Request-ID") or "").strip()
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _as_string_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(part).strip() for part in value if str(part).strip())
    return frozenset()


def _principal_from_identity(
    request: Any,
    identity: Identity,
    *,
    authn_method: str,
) -> CurrentPrincipal:
    metadata = getattr(identity, "metadata", None) or {}
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    # Legacy identities without tenant metadata get an actor-local namespace
    # until the Phase 1 data migration assigns an explicit tenant.
    if not tenant_id:
        tenant_id = f"legacy:{identity.actor_id}"
    roles = frozenset(
        str(role).strip().lower() for role in (identity.roles or ()) if str(role).strip()
    )
    scopes = _as_string_set(metadata.get("scopes"))
    return CurrentPrincipal(
        tenant_id=tenant_id,
        actor_id=identity.actor_id,
        roles=roles,
        scopes=scopes,
        authn_method=authn_method,
        request_id=_request_id(request),
    )


def resolve_principal(
    request: Any,
    identity_store: IdentityStore | None,
    require_auth: bool,
    *,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    jwt_leeway_seconds: int = 0,
) -> CurrentPrincipal | None:
    """Resolve a principal from a known API key or a registered JWT subject.

    JWT subjects are deliberately not synthesized here.  Role and tenant
    authorization must use an identity known to the configured store.
    """
    if not require_auth and identity_store is None:
        return None
    if identity_store is None:
        raise authentication_error("identity store required for authentication")

    token = _request_token(request)
    if not token:
        if require_auth:
            raise authentication_error("missing Authorization: Bearer <token>")
        return None

    identity: Identity | None = None
    authn_method = "api_key"
    if jwt_secret and token.count(".") == 2:
        identity = identity_store.verify_jwt(
            token,
            secret=jwt_secret,
            leeway_seconds=jwt_leeway_seconds,
            required_issuer=jwt_issuer,
            required_audience=jwt_audience,
            trust_jwt_sub=False,
        )
        if identity is None and jwt_issuer == "echo-agent":
            identity = identity_store.verify_jwt(
                token,
                secret=jwt_secret,
                leeway_seconds=jwt_leeway_seconds,
                required_issuer=_LEGACY_AGENT_ISSUER,
                required_audience=jwt_audience,
                trust_jwt_sub=False,
            )
        authn_method = "jwt"
    if identity is None:
        identity = identity_store.verify_api_key(token)
        authn_method = "api_key"
    if identity is None:
        if require_auth:
            raise authentication_error("invalid token")
        return None

    principal = _principal_from_identity(request, identity, authn_method=authn_method)
    with_context = getattr(request, "state", None)
    if with_context is not None:
        with_context.principal = principal
    return principal


def require_roles(
    request: Any,
    identity_store: IdentityStore | None,
    require_auth: bool,
    allowed_roles: Iterable[str],
    *,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    jwt_leeway_seconds: int = 0,
) -> CurrentPrincipal | None:
    """Require one of *allowed_roles* when shared authentication is active."""
    principal = resolve_principal(
        request,
        identity_store,
        require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        jwt_leeway_seconds=jwt_leeway_seconds,
    )
    if not require_auth:
        return principal
    allowed = {str(role).strip().lower() for role in allowed_roles}
    if principal is None or not principal.roles.intersection(allowed):
        label = "/".join(sorted(allowed)) or "authorized"
        raise HTTPException(403, f"{label} role required")
    return principal


def require_operator(
    request: Any,
    identity_store: IdentityStore | None,
    require_auth: bool,
    *,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    jwt_leeway_seconds: int = 0,
) -> CurrentPrincipal | None:
    """Require an ``operator`` or ``admin`` role for control-plane changes."""
    return require_roles(
        request,
        identity_store,
        require_auth,
        ("admin", "operator"),
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        jwt_leeway_seconds=jwt_leeway_seconds,
    )


__all__ = [
    "CurrentPrincipal",
    "SESSION_COOKIE_NAME",
    "LEGACY_SESSION_COOKIE_NAME",
    "AUTH_EXPIRED_HEADER",
    "authentication_error",
    "clear_session_cookie",
    "require_operator",
    "require_roles",
    "resolve_principal",
    "set_session_cookie",
]
