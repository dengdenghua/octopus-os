"""Atomic ensure-room workflow for collaboration sessions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from runtime.memory.cowork.group import LEGACY_PROJECT_MODE, MemberEvent
from runtime.memory.cowork.group_store import GroupRoomLinkConflict

from ._cowork_group_models import response_mode
from ._cowork_group_room_link import _write_recovery


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def ensure_session_room_fail_safe(
    *,
    thread_id: str,
    body: Any,
    request: Request,
    group_store: Any,
    team_rooms_router: Any,
    room_snapshot: Callable[[str], dict[str, Any] | None],
    require_room_member: Callable[[str, Request], Any],
    require_owned_thread: Callable[[str, Request], Any],
    room_members_for_projection: Callable[..., list[dict[str, Any]]],
    room_members_from_group: Callable[[str], list[dict[str, Any]]],
    collaboration_store: Callable[[], Any],
    actor: Callable[[Request], str],
    ensure_project_for_thread: Callable[..., Any],
) -> tuple[dict[str, Any], bool]:
    """Reserve a room before exposing Team/Collaboration surfaces."""

    state = group_store.state(thread_id)
    if state.room_id:
        existing_room = await asyncio.to_thread(room_snapshot, state.room_id)
        if existing_room is not None:
            require_room_member(state.room_id, request)
            binder = getattr(team_rooms_router, "bind_team_thread", None)
            room = (
                await _maybe_await(binder(request, state.room_id, thread_id))
                if callable(binder)
                else existing_room
            )
            roster_projector = getattr(
                team_rooms_router,
                "replace_team_agent_members",
                None,
            )
            if callable(roster_projector):
                room = await _maybe_await(
                    roster_projector(
                        request,
                        state.room_id,
                        room_members_for_projection(
                            thread_id,
                            existing=(room or {}).get("members") or [],
                            preferred=body.members,
                        ),
                        body.leaderId or (room or {}).get("leaderId"),
                    )
                )
            await asyncio.to_thread(
                collaboration_store().upsert_room,
                thread_id,
                dict(room),
            )
            return room, False

    require_owned_thread(thread_id, request)
    creator = getattr(team_rooms_router, "create_team_from_payload_exact", None)
    if not callable(creator):
        raise HTTPException(501, "atomic collab room creation is not wired")

    members = [member for member in body.members if str(member.get("name") or "").strip()]
    if not members:
        members = await asyncio.to_thread(room_members_from_group, thread_id)
    if not members:
        raise HTTPException(
            400,
            "collab room needs at least one agent member; invite a collaborator first",
        )

    leader_id = body.leaderId or str(members[0].get("name") or "")
    if leader_id not in {str(member.get("name") or "") for member in members}:
        leader_id = str(members[0].get("name") or "")
    proposed_room_id = state.room_id or body.id or f"collab-{thread_id}"
    try:
        state, _reserved = await asyncio.to_thread(
            group_store.link_room_if_absent,
            thread_id,
            proposed_room_id,
            actor=actor(request),
        )
    except GroupRoomLinkConflict as exc:
        raise HTTPException(
            409,
            {
                "code": "ROOM_LINK_CONFLICT",
                "message": "collaboration thread or room already has another link",
                "thread_id": thread_id,
                "room_id": exc.current_room_id,
                "requested_room_id": proposed_room_id,
            },
        ) from exc
    room_id = str(state.room_id or "")
    payload = {
        "id": room_id,
        "name": body.name.strip() or f"Collaboration · {thread_id}",
        "members": members,
        "leaderId": leader_id,
        "thread_id": thread_id,
    }
    try:
        room = await _maybe_await(creator(request, payload))
        returned_room_id = str((room or {}).get("id") or "")
        if returned_room_id != room_id:
            raise RuntimeError("team room creator did not preserve the reserved room id")
        room = await asyncio.to_thread(
            collaboration_store().upsert_room,
            thread_id,
            dict(room),
        )
    except Exception as exc:
        recovery_recorded = _write_recovery(
            group_store,
            thread_id=thread_id,
            room_id=room_id,
            actor=actor(request),
            status="pending",
            error_kind=type(exc).__name__,
        )
        raise HTTPException(
            409,
            {
                "code": "ROOM_LINK_RECOVERY_PENDING",
                "message": "room reservation requires an idempotent recovery retry",
                "thread_id": thread_id,
                "room_id": room_id,
                "recovery_recorded": recovery_recorded,
            },
        ) from exc
    if body.mode:
        canonical_mode = response_mode(body.mode)
        await asyncio.to_thread(
            group_store.append,
            thread_id,
            MemberEvent(action="mode", actor=actor(request), mode=canonical_mode),
        )
        if body.mode == LEGACY_PROJECT_MODE:
            await asyncio.to_thread(ensure_project_for_thread, thread_id, request)
    return room, True


__all__ = ["ensure_session_room_fail_safe"]
