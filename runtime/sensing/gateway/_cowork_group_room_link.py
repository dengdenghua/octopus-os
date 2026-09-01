"""Fail-safe Team Room linking for collaboration sessions."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from fastapi import HTTPException, Request

_RECOVERY_CODE = "ROOM_LINK_RECOVERY_PENDING"


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _recovery_key(room_id: str) -> str:
    return f"system:room_link_recovery:{room_id}"


def _write_recovery(
    group_store: Any,
    *,
    thread_id: str,
    room_id: str,
    actor: str,
    status: str,
    error_kind: str = "",
) -> bool:
    payload = {
        "status": status,
        "code": _RECOVERY_CODE,
        "thread_id": thread_id,
        "room_id": room_id,
        "phase": "group_link",
        "error_kind": error_kind,
        "retry": {
            "method": "POST",
            "path": f"/api/collab/{thread_id}/link-room",
            "body": {"room_id": room_id},
        },
    }
    try:
        group_store.blackboard(thread_id).write(
            _recovery_key(room_id),
            payload,
            writer=actor,
        )
    except Exception:  # noqa: BLE001 - the visible surfaces still require a safe response
        return False
    return True


async def link_session_room_fail_safe(
    *,
    thread_id: str,
    room_id: str,
    request: Request,
    actor: str,
    prior_room: dict[str, Any] | None,
    room_snapshot: Callable[[str], dict[str, Any] | None],
    group_store: Any,
    collaboration: Any,
    team_rooms_router: Any,
) -> Any:
    """Link a room without deleting any surface that became externally visible."""

    from runtime.memory.cowork.group_store import (
        GroupRoomDeletingError,
        GroupRoomLinkConflict,
    )

    try:
        state, _created = group_store.link_room_if_absent(
            thread_id,
            room_id,
            actor=actor,
        )
    except GroupRoomLinkConflict as exc:
        raise HTTPException(
            409,
            {
                "code": "ROOM_LINK_CONFLICT",
                "message": "collaboration thread is already linked to another room",
                "thread_id": thread_id,
                "room_id": exc.current_room_id,
                "requested_room_id": room_id,
            },
        ) from exc
    except GroupRoomDeletingError as exc:
        raise HTTPException(
            409,
            {
                "code": "ROOM_DELETE_IN_PROGRESS",
                "message": "team room is deleting or permanently deleted",
                "thread_id": thread_id,
                "room_id": room_id,
            },
        ) from exc
    except Exception as exc:
        # A storage wrapper can raise after COMMIT. Probe the canonical event
        # stream before allowing any external surface write.
        try:
            state = group_store.state(thread_id)
        except Exception as probe_exc:
            recovery_recorded = _write_recovery(
                group_store,
                thread_id=thread_id,
                room_id=room_id,
                actor=actor,
                status="pending",
                error_kind=type(exc).__name__,
            )
            raise HTTPException(
                409,
                {
                    "code": _RECOVERY_CODE,
                    "message": "room reservation is uncertain and requires an idempotent retry",
                    "thread_id": thread_id,
                    "room_id": room_id,
                    "recovery_recorded": recovery_recorded,
                },
            ) from probe_exc
        if state.room_id and state.room_id != room_id:
            raise HTTPException(
                409,
                {
                    "code": "ROOM_LINK_CONFLICT",
                    "message": "collaboration thread is already linked to another room",
                    "thread_id": thread_id,
                    "room_id": state.room_id,
                    "requested_room_id": room_id,
                },
            ) from exc
        if state.room_id != room_id:
            raise

    binder = getattr(team_rooms_router, "bind_team_thread", None)
    try:
        room = (
            await _maybe_await(binder(request, room_id, thread_id))
            if callable(binder)
            else (prior_room or {"id": room_id})
        )
        collaboration.upsert_room(thread_id, dict(room))
    except Exception as exc:
        # The GroupStore reservation is already durable. Never roll it back:
        # doing so could erase a concurrent retry's winner. A commit-then-raise
        # is considered successful only when both external surfaces agree.
        visibility_uncertain = False
        for _attempt in range(2):
            team_visible = not callable(binder)
            try:
                bound_room = room_snapshot(room_id)
                bound_thread_id = str((bound_room or {}).get("thread_id") or "").strip()
                team_visible = team_visible or bound_thread_id == thread_id
            except Exception:  # noqa: BLE001 - a failed probe must preserve possible state
                visibility_uncertain = True
            collaboration_visible = False
            try:
                collaboration_visible = collaboration.session_id_for_room(room_id) == thread_id
            except Exception:  # noqa: BLE001 - a failed probe must preserve possible state
                visibility_uncertain = True
            if team_visible and collaboration_visible and not visibility_uncertain:
                _write_recovery(
                    group_store,
                    thread_id=thread_id,
                    room_id=room_id,
                    actor=actor,
                    status="resolved",
                )
                return state
        recovery_recorded = _write_recovery(
            group_store,
            thread_id=thread_id,
            room_id=room_id,
            actor=actor,
            status="pending",
            error_kind=type(exc).__name__,
        )
        raise HTTPException(
            409,
            {
                "code": _RECOVERY_CODE,
                "message": "room link requires an idempotent recovery retry",
                "thread_id": thread_id,
                "room_id": room_id,
                "recovery_recorded": recovery_recorded,
                "visibility_uncertain": visibility_uncertain,
                "recovery": {
                    "method": "POST",
                    "path": f"/api/collab/{thread_id}/link-room",
                    "body": {"room_id": room_id},
                },
            },
        ) from exc

    board = group_store.blackboard(thread_id)
    with suppress(Exception):
        if board.read(_recovery_key(room_id)) is not None:
            _write_recovery(
                group_store,
                thread_id=thread_id,
                room_id=room_id,
                actor=actor,
                status="resolved",
            )
    return state


__all__ = ["link_session_room_fail_safe"]
