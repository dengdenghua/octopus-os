"""Human invitation HTTP surface for Team Rooms.

Kept separate from ``team_rooms_router`` so the room lifecycle, realtime
transport, and invitation security boundaries remain independently reviewable.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.memory.cowork.team_invitation_store import (
    InvitationError,
    InvitationExhausted,
    InvitationExpired,
    InvitationNotFound,
    InvitationRevoked,
    JoinRequestConflict,
    JoinRequestNotFound,
    TeamInvitationStore,
)
from runtime.platform.io import JsonMutation, TransactionalFileError, mutate_json_file

from .team_rooms_models import (
    CreateTeamInviteRequest,
    JoinInviteRequest,
    RejectTeamJoinRequest,
    TeamParticipantWire,
    TeamRoomWire,
    UpdateTeamJoinPolicyRequest,
)
from .team_speaker_policy import _now

try:
    from fastapi import HTTPException, Request, Response
except ImportError:  # pragma: no cover - parent router enforces the optional dependency
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    Response = None  # type: ignore[assignment,misc]


def _admin_invite_payload(invitation: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "team_id",
        "role",
        "created_by",
        "created_at",
        "expires_at",
        "max_uses",
        "use_count",
        "remaining_uses",
        "status",
        "last_used_at",
        "revoked_at",
        "revoked_by",
    )
    return {key: invitation.get(key) for key in keys}


def _preview_invite_payload(invitation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": invitation["id"],
        "role": invitation["role"],
        "expires_at": invitation["expires_at"],
        "status": invitation["status"],
        "remaining_uses": invitation["remaining_uses"],
    }


def _join_request_payload(application: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    common = {
        "id": application["id"],
        "invite_id": application["invite_id"],
        "team_id": application["team_id"],
        "display_name": application["display_name"],
        "role": application["role"],
        "status": application["status"],
        "created_at": application["created_at"],
        "updated_at": application["updated_at"],
        "expires_at": application["expires_at"],
        "decided_at": application["decided_at"],
        "decision_reason": application["decision_reason"],
        "participant_id": application["participant_id"],
    }
    if admin:
        common["actor_id"] = application["actor_id"]
        common["decided_by"] = application["decided_by"]
    return common


def _request_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, JoinRequestNotFound):
        return HTTPException(404, "join request not found")
    if isinstance(exc, JoinRequestConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, InvitationNotFound):
        return HTTPException(404, "invite not found")
    if isinstance(exc, (InvitationExpired, InvitationRevoked, InvitationExhausted)):
        return HTTPException(410, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "join request transition failed")


def _usable_invitation(
    store: TeamInvitationStore,
    token: str,
    tenant_id: str,
) -> dict[str, Any]:
    try:
        return store.require_usable_token(token, tenant_id=tenant_id)
    except InvitationNotFound as exc:
        raise HTTPException(404, "invite not found") from exc
    except (InvitationExpired, InvitationRevoked, InvitationExhausted) as exc:
        raise HTTPException(410, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, "invite not found") from exc


def _create_invite_response(
    invitation: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    invite_path = f"/workspace/team/join?token={token}"
    return {
        # Backwards-compatible singular-invite response fields.
        "team_id": invitation["team_id"],
        "invite_id": invitation["id"],
        "invite_token": token,
        "invite_role": invitation["role"],
        "invite_path": invite_path,
        "invite_hash_path": f"/#/workspace/team/join?token={token}",
        # Lifecycle fields used by the plural invitation manager.
        "role": invitation["role"],
        "created_at": invitation["created_at"],
        "expires_at": invitation["expires_at"],
        "max_uses": invitation["max_uses"],
        "use_count": invitation["use_count"],
        "status": invitation["status"],
        "invite": _admin_invite_payload(invitation),
    }


def register_team_invitation_routes(
    *,
    router: Any,
    teams: dict[str, TeamRoomWire],
    lock: Any,
    store: TeamInvitationStore,
    require_auth: bool,
    principal_for: Callable[[Any], Any],
    tenant_for: Callable[[Any], str],
    require_admin: Callable[[Any, str], tuple[str | None, str]],
    require_member: Callable[[Any, str], str | None],
    save_rooms: Callable[[], None],
    room_payload: Callable[[TeamRoomWire], dict[str, Any]],
    join_policy_for: Callable[[TeamRoomWire], str],
    project_id_for: Callable[[TeamRoomWire], str | None],
    refresh_rooms: Callable[[], None],
) -> None:
    """Attach create/list/revoke/preview/join routes to a Team Room router."""

    @router.post("/api/teams/{team_id}/invites")
    @router.post("/api/teams/{team_id}/invite")
    def create_invite(
        request: Request,
        team_id: str,
        body: CreateTeamInviteRequest | None = None,
    ) -> dict[str, Any]:
        actor, tenant_id = require_admin(request, team_id)
        request_body = body or CreateTeamInviteRequest()
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            if team.tenant_id != tenant_id:
                raise HTTPException(403, f"not a member of team {team_id}")
            invitation, token = store.create(
                tenant_id=tenant_id,
                room_id=team_id,
                role=request_body.role,
                created_by=actor or "local",
                expires_in_seconds=request_body.expires_in_seconds,
                max_uses=request_body.max_uses,
            )
            join_policy = join_policy_for(team)
        return {
            **_create_invite_response(invitation, token),
            "join_policy": join_policy,
        }

    @router.get("/api/teams/{team_id}/invites")
    def list_invites(request: Request, team_id: str) -> dict[str, Any]:
        _actor, tenant_id = require_admin(request, team_id)
        invitations = store.list_for_room(tenant_id=tenant_id, room_id=team_id)
        items = [_admin_invite_payload(invitation) for invitation in invitations]
        return {"team_id": team_id, "invites": items, "count": len(items)}

    @router.delete("/api/teams/{team_id}/invites/{invite_id}")
    def revoke_invite(request: Request, team_id: str, invite_id: str) -> dict[str, Any]:
        actor, tenant_id = require_admin(request, team_id)
        invitation = store.revoke(
            invite_id,
            tenant_id=tenant_id,
            room_id=team_id,
            revoked_by=actor or "local",
        )
        if invitation is None:
            raise HTTPException(404, "invite not found")
        return {"ok": True, "team_id": team_id, "invite": _admin_invite_payload(invitation)}

    @router.get("/api/teams/{team_id}/join-policy")
    def get_join_policy(request: Request, team_id: str) -> dict[str, Any]:
        require_member(request, team_id)
        tenant_id = tenant_for(request)
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            if team.tenant_id != tenant_id:
                raise HTTPException(403, f"not a member of team {team_id}")
            project_id = project_id_for(team)
            return {
                "team_id": team.id,
                "join_policy": join_policy_for(team),
                "is_project_group": project_id is not None,
                "project_id": project_id,
                "overridden": team.join_policy_override is not None,
            }

    @router.patch("/api/teams/{team_id}/join-policy")
    def update_join_policy(
        request: Request,
        team_id: str,
        body: UpdateTeamJoinPolicyRequest,
    ) -> dict[str, Any]:
        _actor, tenant_id = require_admin(request, team_id)
        with lock:
            team = teams.get(team_id)
            if team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            if team.tenant_id != tenant_id:
                raise HTTPException(403, f"not a member of team {team_id}")
            updated = team.model_copy(
                update={
                    "join_policy_override": body.join_policy,
                    "updated_at": _now(),
                }
            )
            teams[team_id] = updated
            try:
                save_rooms()
            except Exception:
                teams[team_id] = team
                raise
            project_id = project_id_for(updated)
            return {
                "ok": True,
                "team_id": updated.id,
                "join_policy": join_policy_for(updated),
                "is_project_group": project_id is not None,
                "project_id": project_id,
                "overridden": True,
                "team": room_payload(updated),
            }

    @router.get("/api/teams/{team_id}/join-requests")
    def list_join_requests(
        request: Request,
        team_id: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        _actor, tenant_id = require_admin(request, team_id)
        try:
            applications = store.list_join_requests(
                tenant_id=tenant_id,
                room_id=team_id,
                status=status,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        items = [_join_request_payload(item, admin=True) for item in applications]
        return {"team_id": team_id, "join_requests": items, "count": len(items)}

    @router.post("/api/teams/{team_id}/join-requests/{request_id}/reject")
    def reject_join_request(
        request: Request,
        team_id: str,
        request_id: str,
        body: RejectTeamJoinRequest | None = None,
    ) -> dict[str, Any]:
        actor, tenant_id = require_admin(request, team_id)
        try:
            application, changed = store.reject_join_request(
                request_id,
                tenant_id=tenant_id,
                room_id=team_id,
                decided_by=actor or "local",
                reason=(body.reason if body is not None else ""),
            )
        except (InvitationError, ValueError) as exc:
            raise _request_failure(exc) from exc
        return {
            "ok": True,
            "changed": changed,
            "team_id": team_id,
            "join_request": _join_request_payload(application, admin=True),
        }

    @router.post("/api/teams/{team_id}/join-requests/{request_id}/approve")
    def approve_join_request(
        request: Request,
        team_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        actor, tenant_id = require_admin(request, team_id)
        application = store.get_join_request(
            request_id,
            tenant_id=tenant_id,
            room_id=team_id,
        )
        if application is None:
            raise HTTPException(404, "join request not found")
        participant_id = f"actor-{application['actor_id']}"

        with lock:
            before_team = teams.get(team_id)
            if before_team is None:
                raise HTTPException(404, f"team not found: {team_id}")
            if before_team.tenant_id != tenant_id:
                raise HTTPException(403, f"not a member of team {team_id}")
            existing_actor = next(
                (
                    participant
                    for participant in before_team.participants
                    if participant.actor_id == application["actor_id"]
                ),
                None,
            )
            if existing_actor is not None and existing_actor.status == "removed":
                raise HTTPException(409, "removed participant cannot be approved")
            has_reservation = store.has_consumption_reservation(
                invite_id=str(application["invite_id"]),
                actor_id=str(application["actor_id"]),
            )
            if existing_actor is not None and not has_reservation:
                try:
                    approved, changed = store.approve_existing_membership(
                        request_id,
                        tenant_id=tenant_id,
                        room_id=team_id,
                        decided_by=actor or "local",
                        participant_id=existing_actor.id,
                    )
                except (InvitationError, ValueError) as exc:
                    raise _request_failure(exc) from exc
                return {
                    "ok": True,
                    "changed": changed,
                    "outcome": "joined",
                    "join_policy": join_policy_for(before_team),
                    "team": room_payload(before_team),
                    "participant": existing_actor.model_dump(),
                    "join_request": _join_request_payload(approved, admin=True),
                    "thread_id": before_team.thread_id,
                }

            def _apply_membership(
                consumed: dict[str, Any],
                approved: dict[str, Any],
            ) -> tuple[TeamRoomWire, TeamParticipantWire]:
                current_team = teams.get(team_id) or before_team
                reserved_participant_id = str(consumed["reservation_participant_id"])
                current_participant = next(
                    (
                        participant
                        for participant in current_team.participants
                        if participant.actor_id == approved["actor_id"]
                    ),
                    None,
                )
                if current_participant is not None:
                    if (
                        current_participant.id != reserved_participant_id
                        or current_participant.status == "removed"
                    ):
                        raise HTTPException(409, "reserved membership is no longer active")
                    return current_team, current_participant
                now = _now()
                participant = TeamParticipantWire(
                    id=reserved_participant_id,
                    display_name=str(approved["display_name"]),
                    role=str(consumed["role"]),
                    actor_id=str(approved["actor_id"]),
                    joined_at=now,
                    last_seen_at=now,
                )
                updated_team = current_team.model_copy(
                    update={
                        "participants": [*current_team.participants, participant],
                        "updated_at": now,
                    }
                )
                teams[team_id] = updated_team
                try:
                    save_rooms()
                except Exception:
                    teams[team_id] = current_team
                    raise
                return updated_team, participant

            try:
                consumed, approved, result, changed = store.approve_join_request_with(
                    request_id,
                    tenant_id=tenant_id,
                    room_id=team_id,
                    decided_by=actor or "local",
                    participant_id=participant_id,
                    audit_request_id=(
                        request.state.principal.request_id
                        if getattr(request.state, "principal", None) is not None
                        else uuid4().hex
                    ),
                    apply=_apply_membership,
                    membership_already_applied=existing_actor is not None,
                )
            except (InvitationError, ValueError) as exc:
                raise _request_failure(exc) from exc
            if result is None:
                # Idempotent retry of an already-approved request. Resolve its
                # durable participant instead of consuming a second use.
                current = teams.get(team_id) or before_team
                participant = next(
                    (
                        item
                        for item in current.participants
                        if item.actor_id == application["actor_id"] and item.status != "removed"
                    ),
                    None,
                )
                if participant is None:
                    raise HTTPException(409, "approved membership is no longer active")
                updated_team = current
            else:
                updated_team, participant = result
            return {
                "ok": True,
                "changed": changed,
                "outcome": "joined",
                "join_policy": join_policy_for(updated_team),
                "team": room_payload(updated_team),
                "participant": participant.model_dump(),
                "invite": _preview_invite_payload(consumed),
                "join_request": _join_request_payload(approved, admin=True),
                "thread_id": updated_team.thread_id,
            }

    @router.get("/api/team-invites/{token}")
    def inspect_invite(request: Request, token: str) -> dict[str, Any]:
        refresh_rooms()
        principal = principal_for(request)
        actor = principal.actor_id if principal is not None else None
        tenant_id = tenant_for(request)
        invitation = _usable_invitation(store, token, tenant_id)
        with lock:
            team = teams.get(str(invitation["room_id"]))
            if team is None or team.tenant_id != tenant_id:
                raise HTTPException(404, "invite not found")
            active_participants = [
                participant for participant in team.participants if participant.status != "removed"
            ]
            join_policy = join_policy_for(team)
            already_member = bool(
                actor
                and (
                    actor == team.owner_id
                    or any(
                        participant.actor_id == actor and participant.status != "removed"
                        for participant in team.participants
                    )
                )
            )
            return {
                "invite": _preview_invite_payload(invitation),
                # Project destinations are an authorization boundary: an
                # applicant receives the canonical thread only after approval.
                "thread_id": (
                    team.thread_id if join_policy == "direct_join" or already_member else None
                ),
                "join_policy": join_policy,
                "team": {
                    "id": team.id,
                    "name": team.name,
                    "member_count": len(team.members),
                    "participant_count": len(active_participants),
                },
            }

    @router.post("/api/team-invites/{token}/join")
    def join_invite(
        request: Request,
        token: str,
        body: JoinInviteRequest,
        response: Response,
    ) -> dict[str, Any]:
        refresh_rooms()
        principal = principal_for(request)
        actor = principal.actor_id if principal is not None else None
        tenant_id = principal.tenant_id if principal is not None else "local"
        try:
            invitation = store.find_by_token(token, tenant_id=tenant_id)
        except ValueError as exc:
            raise HTTPException(404, "invite not found") from exc
        if invitation is None:
            raise HTTPException(404, "invite not found")
        room_id = str(invitation["room_id"])

        with lock:
            current_team = teams.get(room_id)
            if current_team is None or current_team.tenant_id != tenant_id:
                raise HTTPException(404, "invite not found")
            if require_auth:
                if not actor:  # resolve_principal normally raises first; defensive for custom auth.
                    raise HTTPException(401, "authentication required")
                existing_actor = next(
                    (
                        participant
                        for participant in current_team.participants
                        if participant.actor_id == actor
                    ),
                    None,
                )
                participant_id = (
                    existing_actor.id if existing_actor is not None else f"actor-{actor}"
                )
            else:
                participant_id = str(body.participant_id or f"guest-{uuid4().hex[:10]}").strip()
                if (
                    not participant_id
                    or len(participant_id) > 240
                    or any(ord(char) < 32 or ord(char) == 127 for char in participant_id)
                ):
                    raise HTTPException(400, "invalid participant_id")
                existing_actor = next(
                    (
                        participant
                        for participant in current_team.participants
                        if participant.id == participant_id
                    ),
                    None,
                )
            if existing_actor is not None and existing_actor.status == "removed":
                raise HTTPException(403, "participant was removed from this team")
            reservation_actor_id = actor or participant_id
            has_reservation = store.has_consumption_reservation(
                invite_id=str(invitation["id"]),
                actor_id=reservation_actor_id,
            )
            if existing_actor is not None and not has_reservation:
                return {
                    "outcome": "joined",
                    "join_policy": join_policy_for(current_team),
                    "team": room_payload(current_team),
                    "participant": existing_actor.model_dump(),
                    "invite": _preview_invite_payload(invitation),
                    "thread_id": current_team.thread_id,
                }

            display_name = (body.display_name or actor or "Guest").strip() or "Guest"
            join_policy = join_policy_for(current_team)
            if join_policy == "apply_then_join":
                if not actor and require_auth:
                    raise HTTPException(401, "authentication required")
                try:
                    _invite, application, created = store.create_join_request(
                        token,
                        tenant_id=tenant_id,
                        room_id=room_id,
                        actor_id=actor or participant_id,
                        display_name=display_name,
                    )
                except (InvitationError, ValueError) as exc:
                    raise _request_failure(exc) from exc
                outcome = (
                    "pending_approval"
                    if application["status"] == "pending"
                    else application["status"]
                )
                if application["status"] == "pending":
                    response.status_code = 202
                return {
                    "ok": True,
                    "created": created,
                    "outcome": outcome,
                    "join_policy": join_policy,
                    "join_request": _join_request_payload(application, admin=False),
                    "team": {
                        "id": current_team.id,
                        "name": current_team.name,
                        "member_count": len(current_team.members),
                        "participant_count": len(
                            [p for p in current_team.participants if p.status != "removed"]
                        ),
                    },
                    "thread_id": None,
                }

            if not has_reservation:
                invitation = _usable_invitation(store, token, tenant_id)
            before_team = current_team

            def _apply_membership(
                consumed: dict[str, Any],
            ) -> tuple[TeamRoomWire, TeamParticipantWire]:
                latest_team = teams.get(room_id) or before_team
                reserved_participant_id = str(consumed["reservation_participant_id"])
                current_participant = next(
                    (
                        participant
                        for participant in latest_team.participants
                        if (
                            participant.actor_id == actor
                            and participant.id == reserved_participant_id
                            if actor is not None
                            else participant.id == reserved_participant_id
                        )
                    ),
                    None,
                )
                if current_participant is not None:
                    if current_participant.status == "removed":
                        raise HTTPException(403, "participant was removed from this team")
                    return latest_team, current_participant
                conflicting_actor = next(
                    (
                        participant
                        for participant in latest_team.participants
                        if actor is not None and participant.actor_id == actor
                    ),
                    None,
                )
                if conflicting_actor is not None:
                    raise HTTPException(409, "reserved membership identity changed")
                now = _now()
                participant = TeamParticipantWire(
                    id=reserved_participant_id,
                    display_name=display_name,
                    role=str(consumed["role"]),
                    actor_id=actor,
                    joined_at=now,
                    last_seen_at=now,
                )
                updated_team = latest_team.model_copy(
                    update={
                        "participants": [*latest_team.participants, participant],
                        "updated_at": now,
                    }
                )
                teams[room_id] = updated_team
                try:
                    save_rooms()
                except Exception:
                    teams[room_id] = latest_team
                    raise
                return updated_team, participant

            try:
                consumed, result = store.consume_with(
                    token,
                    tenant_id=tenant_id,
                    room_id=room_id,
                    actor_id=actor or participant_id,
                    request_id=principal.request_id if principal is not None else uuid4().hex,
                    apply=_apply_membership,
                    participant_id=participant_id,
                    membership_already_applied=existing_actor is not None,
                )
            except (InvitationError, ValueError) as exc:
                raise _request_failure(exc) from exc
            if result is None:
                updated_team = teams.get(room_id) or before_team
                participant = next(
                    (
                        item
                        for item in updated_team.participants
                        if (
                            item.actor_id == actor
                            if actor is not None
                            else item.id == consumed["reservation_participant_id"]
                        )
                        and item.status != "removed"
                    ),
                    None,
                )
                if participant is None:
                    raise HTTPException(409, "joined membership is no longer active")
            else:
                updated_team, participant = result
            return {
                "outcome": "joined",
                "join_policy": join_policy,
                "team": room_payload(updated_team),
                "participant": participant.model_dump(),
                "invite": _preview_invite_payload(consumed),
                "thread_id": updated_team.thread_id,
            }

    @router.get("/api/team-invites/{token}/join-request")
    def get_own_join_request(request: Request, token: str) -> dict[str, Any]:
        refresh_rooms()
        principal = principal_for(request)
        if principal is None or not principal.actor_id:
            raise HTTPException(401, "authentication required")
        try:
            application = store.join_request_for_actor_token(
                token,
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
            )
        except ValueError as exc:
            raise HTTPException(404, "join request not found") from exc
        if application is None:
            raise HTTPException(404, "join request not found")
        with lock:
            team = teams.get(str(application["room_id"]))
            if team is None or team.tenant_id != principal.tenant_id:
                raise HTTPException(404, "join request not found")
            participant = next(
                (
                    item
                    for item in team.participants
                    if item.actor_id == principal.actor_id and item.status != "removed"
                ),
                None,
            )
            # Membership is authoritative.  A room may switch from approval
            # to direct join after a historical rejection/withdrawal; the
            # audit row keeps that history while the polling surface must not
            # tell an active member they are still outside the room.
            joined = participant is not None
            participant_payload = participant.model_dump() if participant is not None else None
            return {
                "outcome": "joined" if joined else application["status"],
                "join_policy": join_policy_for(team),
                "join_request": _join_request_payload(application, admin=False),
                "participant": participant_payload,
                "team": room_payload(team) if joined else {"id": team.id, "name": team.name},
                "thread_id": team.thread_id if joined else None,
            }

    @router.delete("/api/team-invites/{token}/join-request")
    def withdraw_own_join_request(request: Request, token: str) -> dict[str, Any]:
        principal = principal_for(request)
        if principal is None or not principal.actor_id:
            raise HTTPException(401, "authentication required")
        try:
            application = store.withdraw_join_request(
                token,
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
            )
        except (InvitationError, ValueError) as exc:
            raise _request_failure(exc) from exc
        return {
            "ok": True,
            "outcome": application["status"],
            "join_request": _join_request_payload(application, admin=False),
        }


def scrub_legacy_room_invites(path: Path) -> None:
    """Remove plaintext invite fields from old room snapshots in place."""

    def _scrub(raw: Any) -> JsonMutation[None]:
        items = raw.get("teams") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return JsonMutation(None, changed=False)
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ("invite_token", "invite_role", "invite_created_at"):
                if field in item:
                    item.pop(field, None)
                    changed = True
        return JsonMutation(None, changed=changed)

    try:
        mutate_json_file(
            path,
            default_factory=dict,
            validate=lambda _raw: None,
            mutate=_scrub,
            indent=2,
        )
    except TransactionalFileError:
        # Preserve the prior tolerant startup behavior for corrupt/unreadable
        # legacy files. The strict room loader remains the runtime authority.
        return


__all__ = ["register_team_invitation_routes", "scrub_legacy_room_invites"]
