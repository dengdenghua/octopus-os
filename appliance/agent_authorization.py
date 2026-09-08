"""Request-bound authority for Agent tools reading appliance-owned data.

The private context holds only verified immutable claims, never a bearer token.
It follows normal contextvars propagation into asyncio tasks and copied worker
contexts. Persisted/restarted tasks without this authority must authenticate
again; a stored Session actor is not an appliance credential.
"""

from __future__ import annotations

import math
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from appliance.agent_api.authorization import authorization_token, validate_runtime_actor
from appliance.security import DEVELOPMENT_ACTOR, ApplianceAuthenticator


class ApplianceAgentAuthorizationError(PermissionError):
    """An Agent tool has no current matching appliance authorization."""


@dataclass(frozen=True, slots=True)
class _Authorization:
    actor: str
    issued_at: int | None
    expires_at: float | None
    account_security: Any

    def is_current(self) -> bool:
        if self.expires_at is None:
            return self.actor == DEVELOPMENT_ACTOR and self.account_security is None
        if time.time() >= self.expires_at or self.account_security is None:
            return False
        return bool(
            self.account_security.claims_are_current(
                {"sub": self.actor, "iat": self.issued_at, "exp": self.expires_at}
            )
        )


_authorization: ContextVar[_Authorization | None] = ContextVar(
    "echo_appliance_agent_authorization", default=None
)


def require_appliance_actor(*, expected_security: Any | None = None) -> str:
    """Require live authority; bind service closures with ``expected_security``.

    The optional object is supplied by OS assembly, never by the model-facing
    tool schema. Identity equality alone cannot distinguish two device apps.
    """
    grant = _authorization.get()
    try:
        if grant is None or not grant.is_current():
            raise ApplianceAgentAuthorizationError("appliance authorization is required or expired")
        if expected_security is not None and grant.account_security is not expected_security:
            raise ApplianceAgentAuthorizationError(
                "appliance authorization belongs to another service"
            )
        validate_runtime_actor(grant.actor)
    except ApplianceAgentAuthorizationError:
        raise
    except Exception as exc:
        raise ApplianceAgentAuthorizationError(
            "appliance authorization is no longer valid"
        ) from exc
    return grant.actor


class ApplianceAgentAuthorizationMiddleware:
    """Bind verified HTTP/WS authority while keeping public routes accessible."""

    def __init__(
        self,
        app: Any,
        *,
        authenticator: ApplianceAuthenticator,
        account_security: Any,
    ) -> None:
        self.app = app
        self._authenticator = authenticator
        self._account_security = account_security

    def _grant(self, scope: dict[str, Any]) -> _Authorization | None:
        if not self._authenticator.required:
            return _Authorization(DEVELOPMENT_ACTOR, None, None, None)
        claims = self._authenticator.verified_claims(authorization_token(scope))
        if claims is None:
            return None
        issued_at = claims.get("iat")
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, (int, float))
            or not math.isfinite(issued_at)
            or int(issued_at) != issued_at
        ):
            return None
        grant = _Authorization(
            actor=str(claims["sub"]),
            issued_at=int(issued_at),
            expires_at=float(claims["exp"]),
            account_security=self._account_security,
        )
        return grant if grant.is_current() else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        grant = None
        if scope.get("type") in {"http", "websocket"}:
            try:
                grant = self._grant(scope)
            except Exception:
                # Public pages still render if verification is unavailable;
                # every appliance tool fails closed without a private grant.
                grant = None
        token = _authorization.set(grant)
        try:
            await self.app(scope, receive, send)
        finally:
            _authorization.reset(token)


__all__ = [
    "ApplianceAgentAuthorizationError",
    "ApplianceAgentAuthorizationMiddleware",
    "require_appliance_actor",
]
