"""Persistent team rooms API.

This is the backend foundation for cross-device Team mode and, later,
human collaboration rooms. It deliberately stores fixed AI team config
and human participants separately: AI team members drive agent routing;
participants represent real people who may join through invite links.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import Lock
from typing import Any

from runtime.memory.cowork.team_invitation_store import TeamInvitationStore
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.principal import CurrentPrincipal, resolve_principal

from ._team_rooms_access import TeamRoomAccess
from ._team_rooms_state import (
    _load_state,
    _room_payload,
    _team_project_helpers,
)
from ._team_rooms_state import (
    _room_storage_payload as _room_storage_payload,
)
from ._team_rooms_state import (
    _save_state as _save_state,
)
from .team_invitations_router import (
    register_team_invitation_routes,
    scrub_legacy_room_invites,
)
from .team_rooms_models import (
    CreateTeamInviteRequest,
    CreateTeamRoomRequest,
    JoinInviteRequest,
    RejectTeamJoinRequest,
    TeamMemberWire,
    TeamParticipantWire,
    TeamRoomWire,
    UpdateDelegationRequest,
    UpdateSpeakerPolicyRequest,
    UpdateTeamJoinPolicyRequest,
    UpdateTeamParticipantRequest,
)
from .team_rooms_ws import (
    TeamRoomWsContext,
    broadcast_authorized_team_sockets,
    team_room_ws,
)
from .team_speaker_policy import (
    _authorized_to_speak_for,
    _caller_is_team_admin,
    _initial_floor_state,
    _next_speaker,
    _normalize_participant_role,
    _normalize_participant_status,
    _normalize_speak_mode,
    _normalize_speaker_policy,
    _now,
    _participant_can_speak,
    _resolve_moderator,
)

_LOG = logging.getLogger("echo.team_rooms")

try:
    from fastapi import APIRouter, HTTPException, Request, WebSocket

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    WebSocket = None  # type: ignore[assignment,misc]

from runtime.sensing._fastapi_guard import require_fastapi  # noqa: E402, I001 — after FASTAPI_AVAILABLE flag


def create_team_rooms_router(
    *,
    state_path: Path | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    reset_callback: Any = None,
    room_message_store: Any = None,
    room_projection: Callable[[dict[str, Any]], None] | None = None,
    room_delete_projection: Callable[[str], None] | None = None,
    room_message_projection: Callable[[str, dict[str, Any]], None] | None = None,
    room_message_provider: Callable[[str, int, int, str], list[dict[str, Any]]] | None = None,
    invitation_store: TeamInvitationStore | None = None,
    project_store: Any = None,
    group_store: Any = None,
    twin_responder: (
        Callable[
            [TeamRoomWire, TeamParticipantWire, list[dict[str, Any]]],
            Awaitable[str | None],
        ]
        | None
    ) = None,
) -> Any:
    """Create `/api/teams/*` routes.

    ``twin_responder`` (optional) bridges the room to an agent runtime: when
    the turn-engine floor lands on a participant who bound a digital-twin
    agent, the WS handler calls it for a short line and emits it on that
    participant's behalf. Injected as a callback so this gateway module never
    imports the model/execution layer (it stays an import leaf). None
    disables twin speaking — the human/host paths are unaffected.
    """
    require_fastapi(__name__)

    router: Any = APIRouter(tags=["team-rooms"])
    path = state_path or (app_paths().data_dir / "team_rooms.json")
    lock = Lock()
    scrub_legacy_room_invites(path)

    def _legacy_tenant_for_owner(owner_id: str | None) -> str:
        owner = str(owner_id or "").strip()
        if not require_auth:
            return "local"
        identity = identity_store.get(owner) if identity_store is not None and owner else None
        tenant = str((getattr(identity, "metadata", None) or {}).get("tenant_id") or "").strip()
        return tenant or (f"legacy:{owner}" if owner else "local")

    teams: dict[str, TeamRoomWire] = _load_state(
        path,
        legacy_tenant_for_owner=_legacy_tenant_for_owner,
    )
    persisted_teams: dict[str, TeamRoomWire] = dict(teams)
    invite_store = invitation_store or TeamInvitationStore(
        path.parent / "team_invitations.db" if state_path is not None else None
    )
    project_store_holder: dict[str, Any] = {"store": project_store}
    group_store_holder: dict[str, Any] = {"store": group_store}
    live_sockets: dict[str, dict[str, WebSocket]] = {}
    socket_loops: dict[str, dict[str, asyncio.AbstractEventLoop]] = {}

    def _project_binding_for_room(team: TeamRoomWire) -> tuple[str | None, bool]:
        bound_store = project_store_holder.get("store")
        thread_id = str(team.thread_id or "").strip()
        resolver = getattr(bound_store, "project_for_thread", None)
        if not thread_id or not callable(resolver):
            return None, False
        try:
            project = resolver(thread_id)
        except Exception:  # noqa: BLE001 - policy resolution must fail closed
            _LOG.warning("project binding lookup failed for team %s", team.id, exc_info=True)
            return None, True
        if project is None:
            return None, False
        project_tenant = str(getattr(project, "tenant_id", "") or "").strip()
        if require_auth and project_tenant != team.tenant_id:
            return None, False
        if not require_auth and project_tenant and project_tenant not in {"local", team.tenant_id}:
            return None, False
        project_id = str(getattr(project, "id", "") or "").strip()
        return project_id or None, False

    def _project_id_for_room(team: TeamRoomWire) -> str | None:
        project_id, _lookup_failed = _project_binding_for_room(team)
        return project_id

    def _join_policy_for_room(team: TeamRoomWire) -> str:
        override = str(team.join_policy_override or "").strip()
        if override in {"direct_join", "apply_then_join"}:
            return override
        project_id, lookup_failed = _project_binding_for_room(team)
        return "apply_then_join" if project_id is not None or lookup_failed else "direct_join"

    def _public_room_payload(team: TeamRoomWire) -> dict[str, Any]:
        project_id, lookup_failed = _project_binding_for_room(team)
        override = str(team.join_policy_override or "").strip()
        join_policy = (
            override
            if override in {"direct_join", "apply_then_join"}
            else ("apply_then_join" if project_id is not None or lookup_failed else "direct_join")
        )
        return _room_payload(
            team,
            join_policy=join_policy,
            project_id=project_id,
        )

    def _principal(request: Any) -> CurrentPrincipal | None:
        state = getattr(request, "state", None)
        cached = getattr(state, "principal", None) if state is not None else None
        if isinstance(cached, CurrentPrincipal):
            return cached
        return resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _auth(request: Any) -> str | None:
        principal = _principal(request)
        return principal.actor_id if principal is not None else None

    def _tenant(request: Any) -> str:
        principal = _principal(request)
        return principal.tenant_id if principal is not None else "local"

    def _refresh_state() -> None:
        nonlocal persisted_teams

        from ._team_room_persistence import refresh_team_room_state

        with lock:
            durable = refresh_team_room_state(
                path=path,
                legacy_tenant_for_owner=_legacy_tenant_for_owner,
            )
            teams.clear()
            teams.update(durable)
            persisted_teams = dict(durable)

    def _assert_room_creatable(team_id: str, tenant_id: str, owner_id: str) -> None:
        del tenant_id, owner_id
        bound_group_store = group_store_holder.get("store")
        if bound_group_store is None:
            return
        lease = bound_group_store.room_delete_lease(team_id)
        if lease is None:
            return
        code = "TEAM_ROOM_DELETED" if lease.finalized else "TEAM_ROOM_DELETE_IN_PROGRESS"
        raise HTTPException(
            409,
            {
                "code": code,
                "message": "a deleted Team Room id cannot be reused",
                "team_id": team_id,
            },
        )

    room_access = TeamRoomAccess(
        teams=teams,
        lock=lock,
        require_auth=require_auth,
        principal=_principal,
        tenant=_tenant,
        http_exception=HTTPException,
        refresh=_refresh_state,
    )
    _list_room_members = room_access.list_room_members
    _get_room_participant = room_access.get_room_participant
    _can_access_room = room_access.can_access_room
    _require_member = room_access.require_member
    _require_owner = room_access.require_owner
    _require_invite_admin = room_access.require_invite_admin
    _require_room_editor = room_access.require_room_editor

    def _save() -> None:
        nonlocal persisted_teams

        from ._team_room_persistence import merge_team_room_state

        merged = merge_team_room_state(
            path=path,
            local=dict(teams),
            baseline=dict(persisted_teams),
            legacy_tenant_for_owner=_legacy_tenant_for_owner,
        )
        teams.clear()
        teams.update(merged)
        persisted_teams = dict(merged)
        if room_projection is not None:
            for team in list(merged.values()):
                try:
                    room_projection(_public_room_payload(team))
                except Exception:  # noqa: BLE001 - projection must not block room writes
                    _LOG.warning("team room projection failed for %s", team.id, exc_info=True)

    def _project_room_delete(room_id: str) -> None:
        if room_delete_projection is None:
            return
        room_delete_projection(room_id)

    def _delete_reserved_team_state(
        team_id: str,
        tenant_id: str,
        owner_id: str,
    ) -> TeamRoomWire | None:
        nonlocal persisted_teams

        from ._team_room_persistence import delete_reserved_team_room_state

        merged, deleted = delete_reserved_team_room_state(
            path=path,
            team_id=team_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            legacy_tenant_for_owner=_legacy_tenant_for_owner,
        )
        teams.clear()
        teams.update(merged)
        persisted_teams = dict(merged)
        return deleted

    def _reset_state() -> None:
        with lock:
            teams.clear()
            live_sockets.clear()
            invite_store.clear()
        if callable(reset_callback):
            reset_callback()

    def _create_team_for_actor(
        actor: str | None,
        tenant_id: str,
        body: CreateTeamRoomRequest,
        *,
        exact_id: bool = False,
    ) -> TeamRoomWire:
        from ._team_room_creation import create_team_for_actor

        _refresh_state()
        return create_team_for_actor(
            actor=actor,
            tenant_id=tenant_id,
            body=body,
            exact_id=exact_id,
            lock=lock,
            teams=teams,
            save=_save,
            assert_creatable=_assert_room_creatable,
        )

    def _create_team_from_payload(
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = CreateTeamRoomRequest.model_validate(payload)
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        tenant_id = principal.tenant_id if principal is not None else "local"
        return _public_room_payload(_create_team_for_actor(actor, tenant_id, body))

    def _create_team_from_payload_exact(
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = CreateTeamRoomRequest.model_validate(payload)
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        tenant_id = principal.tenant_id if principal is not None else "local"
        return _public_room_payload(_create_team_for_actor(actor, tenant_id, body, exact_id=True))

    from ._team_room_binding import team_thread_binding_helpers

    _bind_team_thread, _unbind_team_thread = team_thread_binding_helpers(
        lock=lock,
        teams=teams,
        require_admin=_require_invite_admin,
        group_store=lambda: group_store_holder.get("store"),
        save=_save,
        room_payload=_public_room_payload,
        http_exception=HTTPException,
    )

    async def _update_team_from_payload(
        request: Request,
        team_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_room_editor(request, team_id)
        body = CreateTeamRoomRequest.model_validate(payload)
        with lock:
            current = teams.get(team_id)
            if current is None:
                raise HTTPException(404, f"team not found: {team_id}")
            name = body.name.strip() or current.name
            members = [m for m in body.members if m.name.strip()] or current.members
            leader_id = body.leaderId or current.leaderId or members[0].name
            if leader_id not in {m.name for m in members}:
                leader_id = members[0].name
            updated = current.model_copy(
                update={
                    "name": name,
                    "members": members,
                    "leaderId": leader_id,
                    "updated_at": _now(),
                }
            )
            teams[team_id] = updated
            _save()
        await _broadcast_team_update(team_id, updated)
        return _public_room_payload(updated)

    def _presence_payload(team_id: str) -> dict[str, Any]:
        team = teams.get(team_id)
        participants = team.participants if team else []
        online_ids = set(live_sockets.get(team_id, {}).keys())
        return {
            "type": "presence",
            "team_id": team_id,
            "participants": [
                p.model_dump() for p in participants if p.status == "active" and p.id in online_ids
            ],
            "count": len(online_ids),
            "server_time": _now(),
        }

    async def _broadcast(
        team_id: str,
        payload: dict[str, Any] | Callable[[], dict[str, Any]],
        *,
        exclude: str | None = None,
        include: str | None = None,
    ) -> None:
        await broadcast_authorized_team_sockets(
            team_id=team_id,
            payload=payload,
            teams=teams,
            lock=lock,
            live_sockets=live_sockets,
            socket_loops=socket_loops,
            refresh=_refresh_state,
            exclude=exclude,
            include=include,
        )

    async def _broadcast_presence(team_id: str) -> None:
        await _broadcast(team_id, lambda: _presence_payload(team_id))

    async def _broadcast_team_update(team_id: str, team: TeamRoomWire) -> None:
        await _broadcast(
            team_id,
            {
                "type": "team:update",
                "team_id": team_id,
                "team": _public_room_payload(team),
                "server_time": _now(),
            },
        )

    async def _broadcast_floor(team_id: str, team: TeamRoomWire) -> None:
        """Push the current turn-engine floor state so clients can render
        whose turn it is and the raised-hands queue."""
        await _broadcast(
            team_id,
            {
                "type": "floor",
                "team_id": team_id,
                "speaker_policy": _normalize_speaker_policy(team.speaker_policy),
                "current_speaker_id": getattr(team, "current_speaker_id", None),
                "moderator_id": _resolve_moderator(team),
                "floor_requests": list(getattr(team, "floor_requests", []) or []),
                "server_time": _now(),
            },
        )

    def _active_participant(team_id: str, participant_id: str) -> TeamParticipantWire | None:
        team = teams.get(team_id)
        if team is None:
            return None
        participant = next((p for p in team.participants if p.id == participant_id), None)
        if participant is None or participant.status == "removed":
            return None
        return participant

    @router.get("/api/teams")
    def list_teams(request: Request) -> dict[str, Any]:
        _refresh_state()
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        with lock:
            all_teams = sorted(
                teams.values(),
                key=lambda team: team.updated_at,
                reverse=True,
            )
            # When auth is disabled (single-user dev mode) or the
            # request has no resolvable actor, return everything —
            # backward-compat for unauthenticated dashboards.
            # When require_auth=True AND we resolved an actor, filter
            # to only teams where the caller is a member/owner. This
            # prevents cross-tenant team enumeration.
            if require_auth and principal is not None and actor:
                visible = [
                    team
                    for team in all_teams
                    if team.tenant_id == principal.tenant_id
                    and (
                        actor == getattr(team, "owner_id", None)
                        or any(
                            getattr(p, "actor_id", None) == actor and p.status != "removed"
                            for p in team.participants
                        )
                    )
                ]
            else:
                visible = list(all_teams)
            return {
                "teams": [_public_room_payload(team) for team in visible],
                "count": len(visible),
            }

    @router.post("/api/teams")
    def create_team(
        request: Request,
        body: CreateTeamRoomRequest,
    ) -> dict[str, Any]:
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        tenant_id = principal.tenant_id if principal is not None else "local"
        # Canonical thread binding is an internal cowork bridge concern. A
        # public room-create body must not point an invite at an arbitrary task.
        team = _create_team_for_actor(actor, tenant_id, body.model_copy(update={"thread_id": None}))
        return _public_room_payload(team)

    @router.get("/api/teams/{team_id}")
    def get_team(request: Request, team_id: str) -> dict[str, Any]:
        _require_member(request, team_id)
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            return _public_room_payload(team)

    @router.put("/api/teams/{team_id}")
    def update_team(
        request: Request,
        team_id: str,
        body: CreateTeamRoomRequest,
    ) -> dict[str, Any]:
        _require_room_editor(request, team_id)
        with lock:
            current = teams.get(team_id)
            if current is None:
                raise HTTPException(404, f"team not found: {team_id}")
            name = body.name.strip() or current.name
            members = [m for m in body.members if m.name.strip()] or current.members
            leader_id = body.leaderId or current.leaderId or members[0].name
            if leader_id not in {m.name for m in members}:
                leader_id = members[0].name
            updated = current.model_copy(
                update={
                    "name": name,
                    "members": members,
                    "leaderId": leader_id,
                    "updated_at": _now(),
                }
            )
            teams[team_id] = updated
            _save()
            return _public_room_payload(updated)

    @router.delete("/api/teams/{team_id}")
    def delete_team(request: Request, team_id: str) -> dict[str, Any]:
        from ._team_room_delete import delete_team_room_fail_safe

        return delete_team_room_fail_safe(
            request=request,
            team_id=team_id,
            teams=teams,
            lock=lock,
            group_store=group_store_holder.get("store"),
            principal=_principal,
            require_owner=_require_owner,
            invite_store=invite_store,
            save=_save,
            delete_reserved_state=_delete_reserved_team_state,
            delete_projection=_project_room_delete,
        )

    def _bind_project_store(bound_store: Any) -> None:
        """Late-bind the ProjectStore created after this router at app boot."""

        project_store_holder["store"] = bound_store

    def _bind_group_store(bound_store: Any) -> None:
        """Late-bind the canonical GroupStore used by collaboration routes."""

        group_store_holder["store"] = bound_store

    def _replace_team_agent_members(
        request: Any,
        team_id: str,
        members: list[Any],
        leader_id: str | None = None,
    ) -> dict[str, Any]:
        """Project a canonical GroupStore AI roster into a linked TeamRoom.

        Human participants, the canonical thread binding, governance fields,
        and join-policy override are untouched.  The owner/admin gate keeps
        this internal seam from becoming a roster privilege escalation.
        """

        _actor, tenant_id = _require_invite_admin(request, team_id)
        normalized: list[TeamMemberWire] = []
        seen: set[str] = set()
        for raw in members:
            try:
                item = (
                    raw if isinstance(raw, TeamMemberWire) else TeamMemberWire.model_validate(raw)
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "invalid team agent member") from exc
            name = item.name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(item.model_copy(update={"name": name}))
        with lock:
            current = teams.get(team_id)
            if current is None:
                raise HTTPException(404, f"team not found: {team_id}")
            if current.tenant_id != tenant_id:
                raise HTTPException(403, f"not a member of team {team_id}")
            candidate_leader: str | None = str(leader_id or current.leaderId or "").strip() or None
            names = {item.name for item in normalized}
            if candidate_leader not in names:
                candidate_leader = normalized[0].name if normalized else None
            updated = current.model_copy(
                update={
                    "members": normalized,
                    "leaderId": candidate_leader,
                    "updated_at": _now(),
                }
            )
            teams[team_id] = updated
            try:
                _save()
            except Exception:
                teams[team_id] = current
                raise
            return _public_room_payload(updated)

    router.broadcast = _broadcast
    router.create_team_from_payload = _create_team_from_payload
    router.create_team_from_payload_exact = _create_team_from_payload_exact
    router.delete_team_from_payload = delete_team
    router.bind_team_thread = _bind_team_thread
    router.unbind_team_thread = _unbind_team_thread
    router.update_team_from_payload = _update_team_from_payload
    router.list_room_members = _list_room_members
    router.get_room_participant = _get_room_participant
    router.can_access_room = _can_access_room
    router.bind_project_store = _bind_project_store
    router.bind_group_store = _bind_group_store
    router.refresh_project_binding, router.team_snapshot = _team_project_helpers(
        lock=lock,
        teams=teams,
        public_room_payload=_public_room_payload,
        room_projection=room_projection,
        refresh=_refresh_state,
    )
    router.replace_team_agent_members = _replace_team_agent_members
    router.join_policy_for_room = _join_policy_for_room
    router.reset_state = _reset_state

    @router.patch("/api/teams/{team_id}/participants/{participant_id}")
    async def update_participant(
        request: Request,
        team_id: str,
        participant_id: str,
        body: UpdateTeamParticipantRequest,
    ) -> dict[str, Any]:
        actor = _require_member(request, team_id)
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            current = next((p for p in team.participants if p.id == participant_id), None)
            if current is None:
                raise HTTPException(404, f"participant not found: {participant_id}")
            next_role = (
                _normalize_participant_role(body.role)
                if body.role is not None
                else _normalize_participant_role(current.role)
            )
            next_status = _normalize_participant_status(body.status or current.status)
            next_muted = body.muted if body.muted is not None else bool(current.muted)
            # Authorization (only meaningful when auth is enforced — local
            # single-user mode has no distinct actors to protect against).
            # A plain member may edit only their OWN display_name; changing
            # any role/status/mute, or touching another member's entry, is
            # owner-only. This is the security boundary, not just UX — the
            # frontend's hidden controls are not a substitute. (Self-unmute
            # is privileged too, else a muted member could silence-bust.)
            if require_auth and actor is not None and not _caller_is_team_admin(team, actor):
                is_self = getattr(current, "actor_id", None) == actor
                changing_role = next_role != _normalize_participant_role(current.role)
                changing_status = next_status != _normalize_participant_status(current.status)
                changing_muted = next_muted != bool(current.muted)
                if changing_role or changing_status or changing_muted:
                    raise HTTPException(
                        403,
                        "only the team owner can change a participant's role, status, or mute",
                    )
                if not is_self:
                    raise HTTPException(403, "you can only update your own participant entry")
            if current.role == "owner" and (next_role != "owner" or next_status == "removed"):
                other_owners = [
                    p
                    for p in team.participants
                    if p.id != participant_id
                    and p.status != "removed"
                    and _normalize_participant_role(p.role) == "owner"
                ]
                if not other_owners:
                    raise HTTPException(400, "team must keep at least one owner")
            next_name = (
                body.display_name.strip()
                if body.display_name is not None and body.display_name.strip()
                else current.display_name
            )
            now = _now()
            updated_participant = current.model_copy(
                update={
                    "display_name": next_name,
                    "role": next_role,
                    "status": next_status,
                    "muted": next_muted,
                    "last_seen_at": now,
                }
            )
            participants = [
                updated_participant if p.id == participant_id else p for p in team.participants
            ]
            team = team.model_copy(
                update={
                    "participants": participants,
                    "updated_at": now,
                }
            )
            teams[team_id] = team
            if next_status == "removed":
                live_sockets.get(team_id, {}).pop(participant_id, None)
            _save()
        await _broadcast_team_update(team_id, team)
        await _broadcast_presence(team_id)
        return {"team": _public_room_payload(team), "participant": updated_participant.model_dump()}

    @router.patch("/api/teams/{team_id}/speaker-policy")
    async def update_speaker_policy(
        request: Request,
        team_id: str,
        body: UpdateSpeakerPolicyRequest,
    ) -> dict[str, Any]:
        # Whole-room governance is owner-only — a member must not be able
        # to silence the room or lift a lock the owner imposed.
        _require_owner(request, team_id)
        policy = _normalize_speaker_policy(body.speaker_policy)
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            team = team.model_copy(
                update={
                    "speaker_policy": policy,
                    **_initial_floor_state(team, policy),
                    "updated_at": _now(),
                }
            )
            teams[team_id] = team
            _save()
        await _broadcast_team_update(team_id, team)
        return {"team": _public_room_payload(team), "speaker_policy": policy}

    @router.patch("/api/teams/{team_id}/participants/{participant_id}/delegation")
    async def update_delegation(
        request: Request,
        team_id: str,
        participant_id: str,
        body: UpdateDelegationRequest,
    ) -> dict[str, Any]:
        # SELF-ONLY opt-in. Unlike mute (owner-only), delegation is the
        # bound person's own choice — letting an admin bind a twin/host to
        # someone else would be impersonation. So the caller must BE the
        # participant. No-op gate under local single-user mode.
        actor = _require_member(request, team_id)
        mode = _normalize_speak_mode(body.speak_mode)
        twin = (body.twin_agent_id or "").strip() or None
        host = (body.host_id or "").strip() or None
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            current = next((p for p in team.participants if p.id == participant_id), None)
            if current is None:
                raise HTTPException(404, f"participant not found: {participant_id}")
            if require_auth and actor is not None and getattr(current, "actor_id", None) != actor:
                raise HTTPException(
                    403, "only the participant themselves can set their speaking delegation"
                )
            if mode == "twin" and not twin:
                raise HTTPException(400, "twin mode requires twin_agent_id")
            if mode == "hosted" and not host:
                raise HTTPException(400, "hosted mode requires host_id")
            updated = current.model_copy(
                update={
                    "speak_mode": mode,
                    "twin_agent_id": twin if mode == "twin" else None,
                    "host_id": host if mode == "hosted" else None,
                    "last_seen_at": _now(),
                }
            )
            participants = [updated if p.id == participant_id else p for p in team.participants]
            team = team.model_copy(update={"participants": participants, "updated_at": _now()})
            teams[team_id] = team
            _save()
        await _broadcast_team_update(team_id, team)
        return {"team": _public_room_payload(team), "participant": updated.model_dump()}

    @router.delete("/api/teams/{team_id}/participants/{participant_id}")
    async def remove_participant(
        request: Request,
        team_id: str,
        participant_id: str,
    ) -> dict[str, Any]:
        actor = _require_member(request, team_id)
        socket: WebSocket | None = None
        socket_loop: asyncio.AbstractEventLoop | None = None
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            current = next((p for p in team.participants if p.id == participant_id), None)
            if current is None:
                raise HTTPException(404, f"participant not found: {participant_id}")
            # A plain member may remove only themselves (leave the room);
            # kicking anyone else is owner-only. No-op under local
            # single-user mode where auth is not enforced.
            if (
                require_auth
                and actor is not None
                and not _caller_is_team_admin(team, actor)
                and getattr(current, "actor_id", None) != actor
            ):
                raise HTTPException(403, "only the team owner can remove other participants")
            if current.role == "owner":
                other_owners = [
                    p
                    for p in team.participants
                    if p.id != participant_id
                    and p.status != "removed"
                    and _normalize_participant_role(p.role) == "owner"
                ]
                if not other_owners:
                    raise HTTPException(400, "team must keep at least one owner")
            now = _now()
            participants = [
                p.model_copy(update={"status": "removed", "last_seen_at": now})
                if p.id == participant_id
                else p
                for p in team.participants
            ]
            team = team.model_copy(
                update={
                    "participants": participants,
                    "updated_at": now,
                }
            )
            teams[team_id] = team
            socket = live_sockets.get(team_id, {}).pop(participant_id, None)
            socket_loop = socket_loops.get(team_id, {}).pop(participant_id, None)
            _save()
        if socket is not None:
            with contextlib.suppress(Exception):
                current_loop = asyncio.get_running_loop()
                if socket_loop is None or socket_loop is current_loop:
                    await socket.close(code=4403)
                elif not socket_loop.is_closed():
                    closed = asyncio.run_coroutine_threadsafe(socket.close(code=4403), socket_loop)
                    await asyncio.wrap_future(closed)
        await _broadcast_team_update(team_id, team)
        await _broadcast_presence(team_id)
        return {"ok": True, "team": _public_room_payload(team), "participant_id": participant_id}

    register_team_invitation_routes(
        router=router,
        teams=teams,
        lock=lock,
        store=invite_store,
        require_auth=require_auth,
        principal_for=_principal,
        tenant_for=_tenant,
        require_admin=_require_invite_admin,
        require_member=_require_member,
        save_rooms=_save,
        room_payload=_public_room_payload,
        join_policy_for=_join_policy_for_room,
        project_id_for=_project_id_for_room,
        refresh_rooms=_refresh_state,
    )
    if room_message_store is None:
        from runtime.memory.cowork.room_messages import RoomMessageStore

        room_message_store = RoomMessageStore(
            base_dir=(state_path.parent / "teamroom") if state_path else None,
        )

    _ws_ctx = TeamRoomWsContext(
        teams=teams,
        lock=lock,
        live_sockets=live_sockets,
        socket_loops=socket_loops,
        auth=_auth,
        save=_save,
        broadcast=_broadcast,
        broadcast_presence=_broadcast_presence,
        broadcast_floor=_broadcast_floor,
        active_participant=_active_participant,
        refresh=_refresh_state,
        require_auth=require_auth,
        twin_responder=twin_responder,
        message_store=room_message_store,
        message_projection=room_message_projection,
    )

    @router.get("/api/teams/{team_id}/messages")
    def get_room_messages(
        request: Request,
        team_id: str,
        limit: int = 200,
        after_seq: int = 0,
        q: str = "",
    ) -> dict[str, Any]:
        """Durable room transcript — reconnect catch-up (``after_seq``) and
        search (``q``). Closes the gap where room chat was live-only / a 20-line
        in-memory ring."""
        _require_member(request, team_id)
        messages: list[dict[str, Any]] = []
        if room_message_provider is not None:
            try:
                messages = room_message_provider(team_id, limit, after_seq, q)
            except Exception:  # noqa: BLE001 - canonical transcript lookup is best-effort
                messages = []
        if not messages:
            if q.strip():
                messages = room_message_store.search(team_id, q, limit=limit)
            else:
                messages = room_message_store.history(team_id, limit=limit, after_seq=after_seq)
        return {"team_id": team_id, "messages": messages}

    @router.websocket("/api/teams/{team_id}/ws")
    async def team_room_ws_route(ws: WebSocket, team_id: str) -> None:
        """Realtime Team Room presence + event broadcast — see
        team_rooms_ws.team_room_ws (split out to keep this module small)."""
        await team_room_ws(_ws_ctx, ws, team_id)

    return router


__all__ = [
    "CreateTeamInviteRequest",
    "CreateTeamRoomRequest",
    "JoinInviteRequest",
    "RejectTeamJoinRequest",
    "TeamMemberWire",
    "TeamParticipantWire",
    "TeamRoomWire",
    "UpdateDelegationRequest",
    "UpdateTeamJoinPolicyRequest",
    "UpdateSpeakerPolicyRequest",
    "UpdateTeamParticipantRequest",
    "create_team_rooms_router",
    # Preserve existing speaker-policy imports after the split.
    "_authorized_to_speak_for",
    "_next_speaker",
    "_participant_can_speak",
]
