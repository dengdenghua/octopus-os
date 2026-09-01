"""Atomic Team Room creation primitives."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from fastapi import HTTPException

from ._team_rooms_state import _slug_id, _unique_team_id
from .team_rooms_models import (
    CreateTeamRoomRequest,
    TeamParticipantWire,
    TeamRoomWire,
)
from .team_speaker_policy import _now


def create_team_for_actor(
    *,
    actor: str | None,
    tenant_id: str,
    body: CreateTeamRoomRequest,
    exact_id: bool,
    lock: Lock,
    teams: dict[str, TeamRoomWire],
    save: Callable[[], Any],
    assert_creatable: Callable[[str, str, str], None],
) -> TeamRoomWire:
    """Create once under the state lock; exact ids are idempotent, never suffixed."""

    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    members = [member for member in body.members if member.name.strip()]
    if not members:
        raise HTTPException(400, "members must include at least one agent")
    leader_id = body.leaderId or members[0].name
    if leader_id not in {member.name for member in members}:
        leader_id = members[0].name
    owner = actor or "local"
    requested_thread_id = (body.thread_id or "").strip() or None
    with lock:
        candidate = body.id or _slug_id(name)
        team_id = _unique_team_id(candidate, {}) if exact_id else _unique_team_id(candidate, teams)
        assert_creatable(team_id, tenant_id, owner)
        if exact_id:
            if requested_thread_id is None:
                raise HTTPException(400, "thread_id is required for exact room creation")
            current = teams.get(team_id)
            if current is not None:
                if (
                    current.thread_id == requested_thread_id
                    and current.owner_id == owner
                    and current.tenant_id == tenant_id
                ):
                    return current
                raise HTTPException(
                    409,
                    {
                        "code": "TEAM_ROOM_ID_CONFLICT",
                        "message": "reserved room id belongs to another collaboration",
                        "team_id": team_id,
                        "thread_id": current.thread_id,
                    },
                )
        now = _now()
        team = TeamRoomWire(
            id=team_id,
            name=name,
            members=members,
            leaderId=leader_id,
            owner_id=owner,
            tenant_id=tenant_id,
            thread_id=requested_thread_id,
            created_at=now,
            updated_at=now,
            participants=[
                TeamParticipantWire(
                    id=f"owner-{owner}",
                    display_name=owner,
                    role="owner",
                    actor_id=actor,
                    joined_at=now,
                    last_seen_at=now,
                ),
            ],
        )
        teams[team.id] = team
        try:
            save()
        except Exception:
            if teams.get(team.id) is team:
                teams.pop(team.id, None)
            raise
        return team


__all__ = ["create_team_for_actor"]
