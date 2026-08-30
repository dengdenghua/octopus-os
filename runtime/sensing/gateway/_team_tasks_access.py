"""Authorization helpers for the persistent team-tasks router."""

from __future__ import annotations

from typing import Any

from runtime.sensing.gateway._team_tasks_helpers import (
    RoomMembershipResolver,
    RoomParticipantResolver,
)


class TeamTaskAccess:
    """Resolve principals and room roles for team-task endpoints."""

    def __init__(
        self,
        *,
        identity_store: Any,
        require_auth: bool,
        jwt_secret: str | None,
        jwt_issuer: str | None,
        jwt_audience: str | None,
        room_membership_resolver: RoomMembershipResolver | None,
        room_participant_resolver: RoomParticipantResolver | None,
        http_exception: Any,
    ) -> None:
        self._identity_store = identity_store
        self._require_auth = require_auth
        self._jwt_secret = jwt_secret
        self._jwt_issuer = jwt_issuer
        self._jwt_audience = jwt_audience
        self._room_membership_resolver = room_membership_resolver
        self._room_participant_resolver = room_participant_resolver
        self._http_exception = http_exception

    def principal(self, request: Any) -> Any:
        from runtime.safety.auth.principal import CurrentPrincipal, resolve_principal

        state = getattr(request, "state", None)
        cached = getattr(state, "principal", None) if state is not None else None
        if isinstance(cached, CurrentPrincipal):
            return cached
        return resolve_principal(
            request,
            self._identity_store,
            self._require_auth,
            jwt_secret=self._jwt_secret,
            jwt_issuer=self._jwt_issuer,
            jwt_audience=self._jwt_audience,
        )

    def identity(self, request: Any) -> tuple[str | None, str]:
        principal = self.principal(request)
        if principal is None:
            return None, "local"
        return principal.actor_id, principal.tenant_id

    def room_role(self, actor: str | None, room_id: str, tenant_id: str) -> str:
        if not actor:
            return ""
        participant_resolver = self._room_participant_resolver
        if participant_resolver is not None:
            try:
                participant = participant_resolver(room_id, actor, tenant_id)
            except TypeError:
                try:
                    participant = participant_resolver(
                        room_id,
                        actor,
                        tenant_id=tenant_id,
                    )
                except Exception:  # noqa: BLE001 - authorization fails closed
                    return ""
            except Exception:  # noqa: BLE001 - authorization fails closed
                return ""
            if not isinstance(participant, dict):
                return ""
            if str(participant.get("status") or "").strip().lower() == "removed":
                return ""
            role = str(participant.get("role") or "viewer").strip().lower()
            return {
                "admin": "owner",
                "collaborator": "member",
                "participant": "member",
                "guest": "viewer",
                "read_only": "viewer",
                "readonly": "viewer",
            }.get(role, role or "viewer")

        membership_resolver = self._room_membership_resolver
        if membership_resolver is None:
            raise self._http_exception(503, "room membership resolver unavailable")
        try:
            members = membership_resolver(room_id) or []
        except (KeyError, ValueError, RuntimeError):
            return ""
        # This legacy callback proves membership but carries no role. Its
        # historical contract allowed task writes, so retain that behavior.
        return "member" if actor in members else ""

    def require_member(
        self,
        actor: str | None,
        room_id: str,
        tenant_id: str,
        *,
        write: bool = False,
    ) -> None:
        if not self._require_auth:
            return
        if self._room_participant_resolver is None and self._room_membership_resolver is None:
            raise self._http_exception(503, "room membership resolver unavailable")
        if not actor:
            raise self._http_exception(401, "authentication required")
        role = self.room_role(actor, room_id, tenant_id)
        if not role:
            raise self._http_exception(403, "actor is not a member of this room")
        if write and role not in {"owner", "member"}:
            raise self._http_exception(403, "viewers cannot modify team tasks")

    def is_member(self, actor: str | None, room_id: str, tenant_id: str) -> bool:
        if not self._require_auth:
            return True
        if self._room_participant_resolver is None and self._room_membership_resolver is None:
            raise self._http_exception(503, "room membership resolver unavailable")
        if not actor:
            raise self._http_exception(401, "authentication required")
        return bool(self.room_role(actor, room_id, tenant_id))


__all__ = ["TeamTaskAccess"]
