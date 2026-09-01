"""Small, framework-independent tenant scope primitives.

The scope object is deliberately separate from FastAPI.  Storage adapters and
background workers can use it without importing a request object, while HTTP
routers can derive it from :class:`CurrentPrincipal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .principal import CurrentPrincipal


@dataclass(frozen=True)
class TenantScope:
    """The minimum ownership context required by a tenant-aware store."""

    tenant_id: str
    actor_id: str
    allow_cross_tenant: bool = False

    @property
    def is_legacy(self) -> bool:
        return self.tenant_id.startswith("legacy:")


def scope_from_principal(
    principal: CurrentPrincipal | None,
    *,
    allow_cross_tenant: bool = False,
) -> TenantScope | None:
    if principal is None:
        return None
    return TenantScope(
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        allow_cross_tenant=allow_cross_tenant,
    )


def scope_from_request(request: Any, *, allow_cross_tenant: bool = False) -> TenantScope | None:
    """Read only the server-resolved principal from request state.

    This intentionally does not inspect query/body/header identity fields.
    """

    principal = getattr(getattr(request, "state", None), "principal", None)
    return scope_from_principal(principal, allow_cross_tenant=allow_cross_tenant)


def require_scope(scope: TenantScope | None) -> TenantScope:
    if scope is None:
        raise ValueError("tenant scope is required")
    return scope


def row_visible(
    row: dict[str, Any],
    scope: TenantScope | None,
    *,
    owner_field: str = "owner_actor_id",
) -> bool:
    """Return whether a persisted row may be returned to ``scope``.

    Null tenant/owner columns are legacy rows.  They are never visible to a
    normal tenant principal; only an explicitly cross-tenant operator may
    inspect them during migration.
    """

    if scope is None or scope.allow_cross_tenant:
        return True
    tenant_id = str(row.get("tenant_id") or "").strip()
    owner_id = str(row.get(owner_field) or "").strip()
    return bool(
        tenant_id and owner_id and tenant_id == scope.tenant_id and owner_id == scope.actor_id
    )


def tenant_scoped_path(base_path: str | Path, scope: TenantScope | None) -> Path:
    """Return a filesystem partition for one tenant/owner scope.

    The digest deliberately avoids placing user-controlled tenant or actor
    identifiers into path components.  A missing scope keeps the legacy path
    for local workers and migration tools; authenticated request paths should
    always pass a scope.
    """

    base = Path(base_path)
    if scope is None:
        return base
    key = f"{scope.tenant_id}\x00{scope.actor_id}".encode()
    partition = sha256(key).hexdigest()[:32]
    return base.parent / "tenants" / partition / base.name


__all__ = [
    "TenantScope",
    "require_scope",
    "row_visible",
    "scope_from_principal",
    "scope_from_request",
    "tenant_scoped_path",
]
