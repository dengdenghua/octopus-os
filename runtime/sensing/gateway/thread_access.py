"""Shared authorization for canonical threads linked to Team Rooms.

Thread ownership remains the authority for private conversations and
administrative operations.  A Team Room link adds two deliberately narrower
capabilities for an authenticated, active human participant in the same
tenant: viewers may read the canonical thread and members may collaborate in
it.  Membership is resolved on every request so leaving or removal revokes the
grant immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_WRITE_ROLES = frozenset({"owner", "member"})
_READ_ROLES = frozenset({*_WRITE_ROLES, "viewer"})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _room_tenant(room: Any) -> str:
    if not isinstance(room, dict):
        return ""
    tenant = _clean(room.get("tenant_id"))
    metadata = room.get("metadata")
    if not tenant and isinstance(metadata, dict):
        tenant = _clean(metadata.get("tenant_id"))
    return tenant


def _normalize_role(value: Any) -> str:
    role = _clean(value).lower()
    aliases = {
        "admin": "owner",
        "collaborator": "member",
        "participant": "member",
        "guest": "viewer",
        "read_only": "viewer",
        "readonly": "viewer",
    }
    return aliases.get(role, role or "viewer")


@dataclass(frozen=True, slots=True)
class ThreadAccessDecision:
    """One fresh authorization decision for a canonical thread."""

    thread: dict[str, Any] | None
    owner_actor_id: str = ""
    tenant_id: str = ""
    room_id: str = ""
    room_role: str = ""
    can_manage: bool = False
    can_read: bool = False
    can_write: bool = False


class ThreadAccessResolver:
    """Resolve owner and linked-room access without caching membership."""

    def __init__(
        self,
        *,
        thread_store: Any,
        group_store: Any = None,
        collaboration_store: Any = None,
        team_rooms_router: Any = None,
        identity_store: Any = None,
        allow_anonymous_ownerless: bool = False,
    ) -> None:
        self._thread_store = thread_store
        self._group_store = group_store
        self._collaboration_store = collaboration_store
        self._team_rooms_router = team_rooms_router
        self._identity_store = identity_store
        # Single-user/auth-off runtimes historically persisted threads without
        # an owner or tenant.  Keep that compatibility explicit and opt-in so
        # authenticated deployments and every other resolver remain fail-closed.
        self._allow_anonymous_ownerless = bool(allow_anonymous_ownerless)

    def _principal_tenant(self, actor_id: str, tenant_id: str | None) -> str:
        tenant = _clean(tenant_id)
        if tenant or not actor_id or self._identity_store is None:
            return tenant
        getter = getattr(self._identity_store, "get", None)
        if not callable(getter):
            return ""
        try:
            identity = getter(actor_id)
        except Exception:  # noqa: BLE001 - authorization fails closed
            return ""
        metadata = getattr(identity, "metadata", None)
        if isinstance(metadata, dict):
            tenant = _clean(metadata.get("tenant_id"))
        return tenant or (f"legacy:{actor_id}" if identity is not None else "")

    def _thread(self, thread_id: str) -> dict[str, Any] | None:
        getter = getattr(self._thread_store, "get", None)
        if not callable(getter):
            getter = getattr(self._thread_store, "get_state", None)
        if not callable(getter):
            return None
        try:
            thread = getter(thread_id)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return None
        return thread if isinstance(thread, dict) else None

    def _linked_room_id(self, thread_id: str) -> str:
        if self._group_store is not None:
            state = getattr(self._group_store, "state", None)
            if callable(state):
                try:
                    room_id = _clean(getattr(state(thread_id), "room_id", ""))
                except Exception:  # noqa: BLE001 - fallback to canonical session map
                    room_id = ""
                if room_id:
                    return room_id
        if self._collaboration_store is not None:
            room_for_session = getattr(self._collaboration_store, "room_for_session", None)
            if callable(room_for_session):
                try:
                    room = room_for_session(thread_id)
                except Exception:  # noqa: BLE001 - authorization fails closed
                    room = None
                if isinstance(room, dict):
                    return _clean(room.get("id") or room.get("room_id"))
        return ""

    def _room_snapshot(self, room_id: str) -> dict[str, Any] | None:
        room_by_id = getattr(self._collaboration_store, "room_by_id", None)
        if not callable(room_by_id):
            return None
        try:
            room = room_by_id(room_id)
        except Exception:  # noqa: BLE001 - authorization fails closed
            return None
        return room if isinstance(room, dict) else None

    def _active_room_role(
        self,
        room_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> str:
        """Return the current room role, preferring the authoritative resolver.

        When ``get_room_participant`` exists its negative answer is final.  In
        particular, we must not fall back to a stale collaboration projection
        after a participant has just been removed.
        """

        participant_resolver = getattr(
            self._team_rooms_router,
            "get_room_participant",
            None,
        )
        if callable(participant_resolver):
            try:
                participant = participant_resolver(room_id, actor_id, tenant_id)
            except TypeError:
                try:
                    participant = participant_resolver(
                        room_id,
                        actor_id,
                        tenant_id=tenant_id,
                    )
                except Exception:  # noqa: BLE001 - authorization fails closed
                    return ""
            except Exception:  # noqa: BLE001 - authorization fails closed
                return ""
            if not isinstance(participant, dict):
                return ""
            # ``offline`` is presence, not membership revocation. Only the
            # durable ``removed`` status closes the linked-thread grant.
            if _clean(participant.get("status")).lower() == "removed":
                return ""
            if _clean(participant.get("actor_id")) not in {"", actor_id}:
                return ""
            return _normalize_role(participant.get("role"))

        # Compatibility for embedders built before the role-aware resolver.
        # A canonical room snapshot can still preserve viewer/member roles.
        room = self._room_snapshot(room_id)
        room_tenant = _room_tenant(room)
        if room_tenant and room_tenant != tenant_id:
            return ""
        if isinstance(room, dict):
            if _clean(room.get("owner_id")) == actor_id:
                return "owner"
            participants = room.get("participants")
            if isinstance(participants, list):
                for participant in participants:
                    if not isinstance(participant, dict):
                        continue
                    if _clean(participant.get("actor_id")) != actor_id:
                        continue
                    if _clean(participant.get("status")).lower() == "removed":
                        return ""
                    return _normalize_role(participant.get("role"))

        # The legacy lister proves active membership but not role.  Treat it
        # as read-only so old adapters never accidentally gain write access.
        member_lister = getattr(self._team_rooms_router, "list_room_members", None)
        if callable(member_lister):
            try:
                members = {_clean(item) for item in member_lister(room_id)}
            except Exception:  # noqa: BLE001 - authorization fails closed
                return ""
            if actor_id in members:
                return "viewer"
        return ""

    def resolve(
        self,
        thread_id: str,
        actor_id: str | None,
        tenant_id: str | None = None,
    ) -> ThreadAccessDecision:
        actor = _clean(actor_id)
        tenant = self._principal_tenant(actor, tenant_id)
        thread = self._thread(thread_id)
        if thread is None:
            return ThreadAccessDecision(thread=None)
        raw_metadata = thread.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        owner = _clean(metadata.get("owner_actor_id") or metadata.get("actor_id"))
        stored_tenant = _clean(metadata.get("tenant_id"))

        # Existing owner-only paths historically allow an ownerless legacy
        # thread in an actor-local namespace.  A room-derived grant is stricter:
        # both sides must carry the exact same non-empty tenant id.
        tenant_matches = bool(
            tenant
            and (stored_tenant == tenant or (not stored_tenant and tenant.startswith("legacy:")))
        )
        can_manage = bool(actor and tenant_matches and (not owner or owner == actor))
        if can_manage:
            return ThreadAccessDecision(
                thread=thread,
                owner_actor_id=owner or actor,
                tenant_id=stored_tenant or tenant,
                can_manage=True,
                can_read=True,
                can_write=True,
            )

        room_id = self._linked_room_id(thread_id)
        if (
            self._allow_anonymous_ownerless
            and not actor
            and not owner
            and not stored_tenant
            and not room_id
        ):
            # Auth-off local threads have no principal with which to prove
            # ownership.  Only restore the legacy grant when the canonical row
            # itself is ownerless/tenantless and no Team Room owns it.  In
            # particular, never infer this from a thread-id prefix (``eval-`` is
            # merely one producer of such historical rows).
            return ThreadAccessDecision(
                thread=thread,
                can_manage=True,
                can_read=True,
                can_write=True,
            )
        if not (actor and room_id and stored_tenant and tenant and stored_tenant == tenant):
            return ThreadAccessDecision(
                thread=thread,
                owner_actor_id=owner,
                tenant_id=stored_tenant,
                room_id=room_id,
            )
        role = self._active_room_role(room_id, actor, tenant)
        return ThreadAccessDecision(
            thread=thread,
            owner_actor_id=owner,
            tenant_id=stored_tenant,
            room_id=room_id,
            room_role=role,
            can_read=role in _READ_ROLES,
            can_write=role in _WRITE_ROLES,
        )

    __call__ = resolve


__all__ = ["ThreadAccessDecision", "ThreadAccessResolver"]
