"""Trusted tenant/principal context for marketplace state and credentials."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from runtime.safety.auth.scope import TenantScope

_UNSET = object()
_scope: ContextVar[TenantScope | None | object] = ContextVar(
    "echo_capability_tenant_scope",
    default=_UNSET,
)


def current_capability_scope() -> TenantScope | None:
    """Return an explicit HTTP scope or the trusted active turn scope.

    Ordinary presentation fields are intentionally ignored. Runtime fallback
    accepts only the server-authored Session ownership tuple validated by the
    existing recovery/tenant boundary.
    """

    explicit = _scope.get()
    if explicit is not _UNSET:
        return explicit if isinstance(explicit, TenantScope) else None
    from runtime.platform.process.session import current_session
    from runtime.safety.recovery.tenant_scope import trusted_scope_from_session

    return trusted_scope_from_session(current_session())


@contextmanager
def use_capability_scope(scope: TenantScope | None) -> Iterator[TenantScope | None]:
    token: Token[Any] = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


__all__ = ["current_capability_scope", "use_capability_scope"]
