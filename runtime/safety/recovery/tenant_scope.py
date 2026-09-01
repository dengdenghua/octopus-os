"""Tenant-safe journal reads for learning and regeneration.

The core :class:`~runtime.memory.journal.Journal` intentionally preserves its
historical ``scope=None`` behaviour (read every row) for migration and
operator tooling.  Learning is a different trust boundary: a background
worker that was not given an ownership scope must not train on authenticated
tenants merely because they share the same journal file.

Use :func:`read_learning_events` in every trajectory-mining path.  Its
behaviour is deliberately fail-closed:

* an explicit normal ``TenantScope`` delegates to the journal's scoped read;
* an explicit ``allow_cross_tenant`` scope is the only global-learning mode;
* no scope means legacy-only (both ownership fields absent).
"""

from __future__ import annotations

from typing import Any

from runtime.safety.auth.scope import TenantScope

AUTHORITATIVE_SCOPE_CONTEXT_KEY = "_echo_authoritative_tenant_scope"


def authoritative_scope_context(scope: TenantScope) -> dict[str, str]:
    """Serialize a server-resolved scope for trusted in-process context.

    Transport builders must remove any client-supplied value at this key
    before injecting this payload.  Consumers intentionally ignore the
    ordinary ``tenant_id``/``owner_actor_id`` presentation fields.
    """

    return {
        "tenant_id": scope.tenant_id,
        "actor_id": scope.actor_id,
    }


def trusted_scope_from_user_context(user_context: Any) -> TenantScope | None:
    """Recover only the private scope marker stamped by a server boundary."""

    if not isinstance(user_context, dict):
        return None
    raw = user_context.get(AUTHORITATIVE_SCOPE_CONTEXT_KEY)
    if isinstance(raw, TenantScope):
        tenant_id = raw.tenant_id.strip()
        actor_id = raw.actor_id.strip()
    elif isinstance(raw, dict):
        tenant_id = str(raw.get("tenant_id") or "").strip()
        actor_id = str(raw.get("actor_id") or "").strip()
    else:
        return None
    if not tenant_id or not actor_id:
        return None
    # Per-turn serving context is never an operator-wide inspection grant.
    return TenantScope(tenant_id=tenant_id, actor_id=actor_id)


def trusted_scope_from_session(session: Any) -> TenantScope | None:
    """Recover a complete principal tuple from a server-owned Session.

    Session metadata is assembled at the authenticated transport boundary and
    then carried through worker/context hops.  Treat a partially populated
    tuple as an integrity error rather than falling back to legacy data: doing
    the latter would let an authenticated turn read or write the unowned score
    history merely because one identity field was lost in transit.
    """

    if session is None:
        return None
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    actor_id = str(metadata.get("owner_actor_id") or "").strip()
    session_actor = str(getattr(session, "actor", None) or "").strip()
    supplied = bool(tenant_id or actor_id)
    if not supplied:
        return None
    if not tenant_id or not actor_id:
        raise ValueError("authenticated session tenant scope is incomplete")
    if session_actor and session_actor != actor_id:
        raise ValueError("authenticated session owner does not match session actor")
    return TenantScope(tenant_id=tenant_id, actor_id=actor_id)


def is_legacy_unscoped_event(event: Any) -> bool:
    """Return ``True`` only when an event carries no ownership identity."""

    tenant_id = str(getattr(event, "tenant_id", None) or "").strip()
    owner_actor_id = str(getattr(event, "owner_actor_id", None) or "").strip()
    return not tenant_id and not owner_actor_id


def read_learning_events(
    journal: Any,
    event_type: Any,
    *,
    scope: TenantScope | None = None,
) -> list[Any]:
    """Read journal events without implicitly crossing tenant boundaries.

    ``Journal.read_by_type(..., scope=scope)`` already enforces an explicit
    tenant/owner pair.  The extra legacy filter closes its intentionally broad
    compatibility behaviour when ``scope`` is omitted.
    """

    events = list(
        journal.read_by_type(event_type, scope=scope)
        if scope is not None
        else journal.read_by_type(event_type)
    )
    if scope is not None:
        return events
    return [event for event in events if is_legacy_unscoped_event(event)]


def read_learning_journal(
    journal: Any,
    *,
    scope: TenantScope | None = None,
) -> list[Any]:
    """Read every learnable event under the same fail-closed policy."""

    events = list(journal.read_all(scope=scope) if scope is not None else journal.read_all())
    if scope is not None:
        return events
    return [event for event in events if is_legacy_unscoped_event(event)]


__all__ = [
    "AUTHORITATIVE_SCOPE_CONTEXT_KEY",
    "authoritative_scope_context",
    "is_legacy_unscoped_event",
    "read_learning_events",
    "read_learning_journal",
    "trusted_scope_from_session",
    "trusted_scope_from_user_context",
]
