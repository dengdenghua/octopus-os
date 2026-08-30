"""Resolve an actor id from an HTTP request's bearer credentials.

A small web-auth helper shared by the FastAPI routers in both
``adapters/integrations`` and ``sensing/gateway``. It used to live in
``sensing/gateway/openai_gateway/request_parser``, so the adapter
integration routers depended upward on the gateway just to authenticate
a request. It only needs the duck-typed request, a caller-supplied
identity store, and ``HTTPException``, so it sits here in the adapter
layer; ``openai_gateway`` re-exports it for the gateway's own routers.
"""

from __future__ import annotations

from typing import Any


def _resolve_actor(
    request: Any,
    identity_store: Any,
    require_auth: bool,
    *,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    jwt_leeway_seconds: int = 0,
    trust_jwt_sub: bool = False,
) -> str | None:
    if request is None:
        return None

    # Keep the legacy actor-returning API for callers, but make the decision
    # through the same registered-identity Principal resolver used by the
    # security-sensitive routers.  In particular, a JWT subject/claims never
    # creates an operator or admin identity by itself.
    from runtime.safety.auth.principal import resolve_principal

    principal = resolve_principal(
        request,
        identity_store,
        require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        jwt_leeway_seconds=jwt_leeway_seconds,
    )
    return principal.actor_id if principal is not None else None
