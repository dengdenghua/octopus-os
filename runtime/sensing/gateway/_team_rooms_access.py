"""Room membership and administration checks for the Team Rooms router."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .team_rooms_models import TeamRoomWire
from .team_speaker_policy import _caller_is_team_admin, _normalize_participant_role


class TeamRoomAccess:
    """Keep room ACL decisions independent from HTTP route registration."""

    def __init__(
        self,
        *,
        teams: dict[str, TeamRoomWire],
        lock: Any,
        require_auth: bool,
        principal: Callable[[Any], Any],
        tenant: Callable[[Any], str],
        http_exception: Any,
        refresh: Callable[[], None],
    ) -> None:
        self._teams = teams
        self._lock = lock
        self._require_auth = require_auth
        self._principal = principal
        self._tenant = tenant
        self._http_exception = http_exception
        self._refresh = refresh

    def _current_participant(
        self,
        room_id: str,
        actor_id: str,
        tenant_id: str | None,
    ) -> dict[str, Any] | None:
        with self._lock:
            team = self._teams.get(room_id)
            if team is None or (tenant_id is not None and team.tenant_id != tenant_id):
                return None
            matched = next(
                (
                    participant
                    for participant in team.participants
                    if participant.actor_id == actor_id
                ),
                None,
            )
            if matched is not None:
                if matched.status == "removed":
                    return None
                return {
                    **matched.model_dump(),
                    "room_id": team.id,
                    "tenant_id": team.tenant_id,
                }
            if actor_id != team.owner_id:
                return None
            return {
                "id": f"owner-{actor_id}",
                "display_name": actor_id,
                "role": "owner",
                "actor_id": actor_id,
                "status": "active",
                "room_id": team.id,
                "tenant_id": team.tenant_id,
            }

    def list_room_members(self, team_id: str) -> list[str]:
        """Return actor ids allowed to operate on ``team_id``."""

        self._refresh()
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                return []
            actors: list[str] = []
            owner = getattr(team, "owner_id", None)
            if owner:
                actors.append(owner)
            for participant in team.participants:
                if participant.status == "removed":
                    continue
                actor_id = getattr(participant, "actor_id", None)
                if not actor_id and not self._require_auth:
                    # Local rooms historically used participant ids as their
                    # only identity. Shared mode never treats that as proof.
                    actor_id = participant.id
                if actor_id and actor_id not in actors:
                    actors.append(actor_id)
            return actors

    def get_room_participant(
        self,
        room_id: str,
        actor_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve durable membership; transient presence is not authorization."""

        actor_id = str(actor_id or "").strip()
        if not actor_id:
            return None
        self._refresh()
        return self._current_participant(room_id, actor_id, tenant_id)

    def can_access_room(
        self,
        room_id: str,
        actor_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        return self.get_room_participant(room_id, actor_id, tenant_id) is not None

    def require_member(self, request: Any, team_id: str) -> str | None:
        self._refresh()
        principal = self._principal(request)
        actor = principal.actor_id if principal is not None else None
        if not self._require_auth:
            return actor
        if principal is None or not actor:
            raise self._http_exception(401, "authentication required")
        if self._current_participant(team_id, actor, principal.tenant_id) is None:
            raise self._http_exception(403, f"not a member of team {team_id}")
        return actor

    def require_owner(self, request: Any, team_id: str) -> str | None:
        self._refresh()
        principal = self._principal(request)
        actor = principal.actor_id if principal is not None else None
        if not self._require_auth:
            return actor
        if principal is None or not actor:
            raise self._http_exception(401, "authentication required")
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                raise self._http_exception(404, f"team not found: {team_id}")
            if team.tenant_id != principal.tenant_id:
                raise self._http_exception(403, f"not a member of team {team_id}")
            if actor != getattr(team, "owner_id", None):
                raise self._http_exception(403, "only the team owner can delete the team")
        return actor

    def require_invite_admin(self, request: Any, team_id: str) -> tuple[str | None, str]:
        actor = self.require_member(request, team_id)
        tenant_id = self._tenant(request)
        if not self._require_auth:
            return actor, tenant_id
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                raise self._http_exception(404, f"team not found: {team_id}")
            if team.tenant_id != tenant_id or not _caller_is_team_admin(team, actor):
                raise self._http_exception(
                    403,
                    "only a team owner or admin can manage invitations",
                )
        return actor, tenant_id

    def require_room_editor(self, request: Any, team_id: str) -> str | None:
        actor = self.require_member(request, team_id)
        if not self._require_auth:
            return actor
        principal = self._principal(request)
        participant = (
            self._current_participant(team_id, actor or "", principal.tenant_id)
            if principal is not None
            else None
        )
        if participant is None or _normalize_participant_role(participant.get("role")) == "viewer":
            raise self._http_exception(403, "viewers cannot modify the team room")
        return actor
