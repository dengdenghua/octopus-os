"""Router-level auth helpers shared by the observability endpoint groups.

Pure structural extraction from ``_observability_router_factory.py`` (no
logic changes). These helpers enforce the ``require_auth`` gate across every
observability endpoint and resolve operator identity for audit-worthy
receipt mutations.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from ._observability_helpers import HTTPException, Request
from ._observability_state import ObservabilityContext

_CROSS_TENANT_SCOPES = frozenset(
    {
        "evolution:cross_tenant",
        "tenant:cross_tenant",
        "global:admin",
        "*",
    }
)


def make_auth_dep(ctx: ObservabilityContext) -> Callable[[Request], None]:
    """Router-level auth gate · mirrors create_browser_router. These
    endpoints expose the journal (file diffs, absolute paths, task
    history) over /api/stream + /api/files/stream. require_auth off
    (default / single-user dev) → _resolve_actor is a no-op so local
    preview + the EventSource-based Observability panel are unchanged;
    require_auth on (deployed / multi-user) → 401 across every endpoint
    instead of leaking the whole work log to any anonymous client.
    """

    def _auth_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        # The dependency authenticates once and leaves the verified principal
        # on request.state. Individual handlers derive their TenantScope from
        # that server-owned principal; query/body identity fields are ignored.
        require_roles(
            request,
            ctx.identity_store,
            ctx.require_auth,
            ("admin", "operator"),
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )

    return _auth_dep


def _observability_scope(
    request: Request,
    ctx: ObservabilityContext,
    *,
    cross_tenant: bool = False,
) -> Any:
    """Return the request-owned journal scope.

    Local single-user mode intentionally keeps the historical global view.
    Authenticated requests are exact tenant+owner projections unless an admin
    explicitly asks for cross-tenant access and also carries a durable
    cross-tenant permission scope.
    """

    from runtime.safety.auth.scope import TenantScope, scope_from_principal

    if not ctx.require_auth:
        return None
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is None:
        raise HTTPException(401, "auth required")
    if not cross_tenant:
        return scope_from_principal(principal)
    if "admin" not in principal.roles or not principal.scopes.intersection(_CROSS_TENANT_SCOPES):
        raise HTTPException(
            403,
            "explicit cross-tenant observability admin permission required",
        )
    return TenantScope(
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        allow_cross_tenant=True,
    )


def _require_global_control(
    request: Request,
    ctx: ObservabilityContext,
    *,
    cross_tenant: bool,
) -> Any:
    """Gate process-global state behind an explicit privileged request."""

    if not ctx.require_auth:
        return None
    if not cross_tenant:
        raise HTTPException(
            403,
            "global control plane requires cross_tenant=true",
        )
    return _observability_scope(request, ctx, cross_tenant=True)


def _event_visible(event: Any, scope: Any) -> bool:
    if scope is None or bool(getattr(scope, "allow_cross_tenant", False)):
        return True
    tenant_id = str(getattr(event, "tenant_id", None) or "").strip()
    owner_actor_id = str(getattr(event, "owner_actor_id", None) or "").strip()
    return bool(
        tenant_id
        and owner_actor_id
        and tenant_id == scope.tenant_id
        and owner_actor_id == scope.actor_id
    )


class _ScopedObservabilityJournal:
    """Fail-closed read/live projection over a shared journal.

    Some tests and third-party journal doubles predate the ``scope=`` keyword.
    In authenticated mode their unscoped result is filtered locally; a normal
    tenant never falls back to returning the global list.
    """

    def __init__(self, journal: Any, scope: Any) -> None:
        self._journal = journal
        self.scope = scope

    def read_all(self, *, scope: Any = None) -> list[Any]:
        del scope  # callers cannot override the request-owned boundary
        if self.scope is None:
            return list(self._journal.read_all())
        try:
            events = list(self._journal.read_all(scope=self.scope))
        except TypeError:
            events = list(self._journal.read_all())
        # Defense in depth for duck backends that accept **kwargs but silently
        # ignore the scope. The request boundary never trusts backend filtering.
        return [event for event in events if _event_visible(event, self.scope)]

    def read_by_type(self, event_type: Any, *, scope: Any = None) -> list[Any]:
        del scope
        if self.scope is None:
            return list(self._journal.read_by_type(event_type))
        try:
            events = list(self._journal.read_by_type(event_type, scope=self.scope))
        except TypeError:
            events = list(self._journal.read_by_type(event_type))
        return [event for event in events if _event_visible(event, self.scope)]

    def read_by_task(self, task_id: Any, *, scope: Any = None) -> list[Any]:
        del scope
        return [
            event
            for event in self.read_all()
            if str(getattr(event, "task_id", "") or "") == str(task_id)
        ]

    def subscribe(self, callback: Callable[[Any], None]) -> Callable[[], None]:
        return self._journal.subscribe(
            lambda event: callback(event) if _event_visible(event, self.scope) else None
        )


def _scoped_observability_journal(journal: Any, scope: Any) -> Any:
    return _ScopedObservabilityJournal(journal, scope)


def _journal_scope_context(scope: Any) -> Any:
    from runtime.memory.journal import journal_context

    if scope is None:
        return journal_context()
    return journal_context(
        tenant_id=scope.tenant_id,
        owner_actor_id=scope.actor_id,
    )


def _operator_identity(request: Request, ctx: ObservabilityContext) -> tuple[str, Any]:
    from runtime.adapters.web_auth import _resolve_actor

    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None:
        return str(principal.actor_id), principal

    actor = _resolve_actor(
        request,
        ctx.identity_store,
        ctx.require_auth,
        jwt_secret=ctx.jwt_secret,
        jwt_issuer=ctx.jwt_issuer,
        jwt_audience=ctx.jwt_audience,
    )
    if not ctx.require_auth:
        return str(actor or "local_operator"), None

    auth = request.headers.get("Authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    identity = None
    if ctx.identity_store is not None and token:
        if ctx.jwt_secret and token.count(".") == 2:
            with contextlib.suppress(Exception):
                identity = ctx.identity_store.verify_jwt(
                    token,
                    secret=ctx.jwt_secret,
                    required_issuer=ctx.jwt_issuer,
                    required_audience=ctx.jwt_audience,
                )
        if identity is None:
            with contextlib.suppress(Exception):
                identity = ctx.identity_store.verify_api_key(token)
    return str(actor or "authenticated_operator"), identity


def _identity_is_admin(identity: Any) -> bool:
    roles = getattr(identity, "roles", ()) or ()
    return "admin" in {str(role).lower() for role in roles}


def _can_authorize_retry(request: Request, ctx: ObservabilityContext) -> bool:
    if not ctx.require_auth:
        return True
    _, identity = _operator_identity(request, ctx)
    return _identity_is_admin(identity)


def _operator_actor(request: Request, ctx: ObservabilityContext) -> str:
    """Resolve an operator and require admin for receipt mutations."""

    actor, identity = _operator_identity(request, ctx)
    if ctx.require_auth and not _identity_is_admin(identity):
        raise HTTPException(403, "admin role required")
    return actor


__all__ = [
    "make_auth_dep",
    "_event_visible",
    "_observability_scope",
    "_require_global_control",
    "_scoped_observability_journal",
    "_journal_scope_context",
    "_operator_identity",
    "_identity_is_admin",
    "_can_authorize_retry",
    "_operator_actor",
]
