"""Canonical Team Room thread binding helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .team_rooms_models import TeamRoomWire
from .team_speaker_policy import _now


def team_thread_binding_helpers(
    *,
    lock: Any,
    teams: dict[str, TeamRoomWire],
    require_admin: Callable[[Any, str], Any],
    group_store: Callable[[], Any],
    save: Callable[[], None],
    room_payload: Callable[[TeamRoomWire], dict[str, Any]],
    http_exception: Any,
) -> tuple[Callable[[Any, str, str], dict[str, Any]], Callable[[Any, str, str], dict[str, Any]]]:
    """Bind the internal cowork bridge without exposing thread ids publicly."""

    def bind(request: Any, team_id: str, thread_id: str) -> dict[str, Any]:
        require_admin(request, team_id)
        canonical_thread_id = str(thread_id or "").strip()
        if not canonical_thread_id:
            raise http_exception(400, "thread_id is required")
        bound_group_store = group_store()
        if bound_group_store is not None:
            canonical_room_id = str(
                getattr(bound_group_store.state(canonical_thread_id), "room_id", "") or ""
            ).strip()
            if canonical_room_id != team_id:
                raise http_exception(
                    409,
                    {
                        "code": "ROOM_LINK_RESERVATION_REQUIRED",
                        "message": "reserve the collaboration room before binding the Team Room",
                        "team_id": team_id,
                        "thread_id": canonical_thread_id,
                    },
                )
        with lock:
            current = teams.get(team_id)
            if current is None:
                raise http_exception(404, f"team not found: {team_id}")
            existing_thread_id = str(current.thread_id or "").strip()
            if existing_thread_id and existing_thread_id != canonical_thread_id:
                raise http_exception(409, "team room is already bound to another thread")
            if existing_thread_id == canonical_thread_id:
                return room_payload(current)
            updated = current.model_copy(
                update={"thread_id": canonical_thread_id, "updated_at": _now()}
            )
            teams[team_id] = updated
            try:
                save()
            except Exception:
                teams[team_id] = current
                raise
            return room_payload(updated)

    def unbind(request: Any, team_id: str, thread_id: str) -> dict[str, Any]:
        require_admin(request, team_id)
        expected_thread_id = str(thread_id or "").strip()
        with lock:
            current = teams.get(team_id)
            if current is None:
                raise http_exception(404, f"team not found: {team_id}")
            if str(current.thread_id or "").strip() != expected_thread_id:
                return room_payload(current)
            updated = current.model_copy(update={"thread_id": None, "updated_at": _now()})
            teams[team_id] = updated
            try:
                save()
            except Exception:
                teams[team_id] = current
                raise
            return room_payload(updated)

    return bind, unbind


__all__ = ["team_thread_binding_helpers"]
