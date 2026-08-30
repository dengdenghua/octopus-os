"""Transactional generic and Project OS room upserts."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Protocol

from runtime.memory.cowork.ids import require_cowork_id


class _RoomStore(Protocol):
    _lock: threading.Lock

    def _connect(self) -> sqlite3.Connection: ...


class ProjectRoomVersionConflict(RuntimeError):
    """A generic room write attempted to replace Project OS-owned state."""


def upsert_room(store: _RoomStore, session_id: str, room: dict[str, Any]) -> dict[str, Any]:
    """Merge ordinary room fields without moving a generation-owned room."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _fence_project_room_merge,
        _load,
        _merge_room_payload,
        _normalize_room_payload,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    incoming = dict(room or {})
    room_id = require_cowork_id(
        incoming.get("id") or incoming.get("room_id") or f"collab-{session_id}",
        label="room_id",
    )
    incoming = _normalize_room_payload(incoming, room_id=room_id)
    now = _now()
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT room_id, room_json, created_at FROM collaboration_rooms WHERE session_id=?",
            (session_id,),
        ).fetchone()
        current_owner = (
            conn.execute(
                "SELECT project_id, generation FROM collaboration_room_owners WHERE room_id=?",
                (str(row[0]),),
            ).fetchone()
            if row is not None and str(row[0]) != room_id
            else None
        )
        current_binding = conn.execute(
            "SELECT project_id, generation FROM collaboration_project_generations "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if (
            row is not None
            and str(row[0]) != room_id
            and (
                (current_owner is not None and str(current_owner[0] or ""))
                or (current_binding is not None and str(current_binding[0] or ""))
            )
        ):
            raise ProjectRoomVersionConflict(
                "project-bound collaboration room must be detached before replacement"
            )
        existing_payload = _load(row[1]) if row is not None and str(row[0]) == room_id else None
        existing_room = conn.execute(
            "SELECT session_id, room_json FROM collaboration_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if existing_payload is None and existing_room is not None:
            existing_payload = _load(existing_room[1])
        owner = conn.execute(
            "SELECT session_id, project_id, generation FROM collaboration_room_owners "
            "WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if (
            owner is not None
            and str(owner[0]) != session_id
            and (str(owner[1] or "") or int(owner[2] or 0) > 0)
        ):
            raise RuntimeError("project room migration requires the versioned project API")
        incoming = _fence_project_room_merge(existing_payload, incoming)
        payload = _merge_room_payload(existing_payload, incoming, room_id=room_id)
        created_at = str(row[2]) if row else str(payload.get("created_at") or now)
        payload.setdefault("created_at", created_at)
        payload["updated_at"] = str(payload.get("updated_at") or now)
        if existing_room and str(existing_room[0]) != session_id:
            conn.execute(
                "UPDATE collaboration_tasks SET session_id=? WHERE room_id=?",
                (session_id, room_id),
            )
            conn.execute(
                "UPDATE collaboration_messages SET session_id=? WHERE room_id=?",
                (session_id, room_id),
            )
            conn.execute("DELETE FROM collaboration_rooms WHERE room_id=?", (room_id,))
        if row is not None and str(row[0]) != room_id:
            conn.execute(
                "DELETE FROM collaboration_room_owners WHERE room_id=?",
                (str(row[0]),),
            )
        conn.execute(
            "INSERT INTO collaboration_rooms(session_id, room_id, room_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "room_id=excluded.room_id, room_json=excluded.room_json, updated_at=excluded.updated_at",
            (
                session_id,
                room_id,
                _dump(payload, label="room"),
                created_at,
                payload["updated_at"],
            ),
        )
        binding = conn.execute(
            "SELECT project_id, generation FROM collaboration_project_generations "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        owner_project = str(binding[0] or "") if binding else ""
        owner_generation = max(0, int(binding[1])) if binding else 0
        conn.execute(
            "INSERT INTO collaboration_room_owners(room_id, session_id, project_id, generation) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(room_id) DO UPDATE SET "
            "session_id=excluded.session_id, project_id=excluded.project_id, "
            "generation=excluded.generation",
            (room_id, session_id, owner_project, owner_generation),
        )
    return payload


def upsert_project_room(
    store: _RoomStore,
    *,
    session_id: str,
    room: dict[str, Any],
    project_id: str,
    generation: int,
) -> dict[str, Any]:
    """Atomically promote/move a room at one authoritative binding generation."""

    from runtime.memory.cowork._collaboration_project_projection import (
        assert_project_projection_writable,
    )
    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _fence_project_room_merge,
        _load,
        _merge_room_payload,
        _normalize_room_payload,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    project_id = require_cowork_id(project_id, label="project_id")
    if not isinstance(generation, int) or generation < 0:
        raise ValueError("project binding generation must be non-negative")
    incoming = dict(room or {})
    room_id = require_cowork_id(
        incoming.get("id") or incoming.get("room_id") or f"collab-{session_id}",
        label="room_id",
    )
    incoming = _normalize_room_payload(incoming, room_id=room_id)
    now = _now()
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_project_projection_writable(conn, project_id)
        if (
            generation == 0
            and session_id == f"project:{project_id}"
            and conn.execute(
                "SELECT 1 FROM collaboration_project_room_bindings "
                "WHERE project_id=? AND session_id<>? LIMIT 1",
                (project_id, session_id),
            ).fetchone()
        ):
            raise RuntimeError("standalone project room was superseded by a bound session")
        row = conn.execute(
            "SELECT room_id, room_json, created_at FROM collaboration_rooms WHERE session_id=?",
            (session_id,),
        ).fetchone()
        existing_room = conn.execute(
            "SELECT session_id, room_json, created_at FROM collaboration_rooms WHERE room_id=?",
            (room_id,),
        ).fetchone()
        owner = conn.execute(
            "SELECT session_id, project_id, generation FROM collaboration_room_owners "
            "WHERE room_id=?",
            (room_id,),
        ).fetchone()
        if owner is not None:
            owner_generation = max(0, int(owner[2] or 0))
            if generation < owner_generation:
                raise RuntimeError("stale project room binding generation")
            if generation == owner_generation and (
                str(owner[0]) != session_id or str(owner[1] or "") not in {"", project_id}
            ):
                raise RuntimeError("project room binding generation conflict")
        binding = conn.execute(
            "SELECT project_id, generation FROM collaboration_project_generations "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if binding is not None:
            current_generation = max(0, int(binding[1]))
            if generation < current_generation:
                raise RuntimeError("stale room project binding generation")
            if generation == current_generation and str(binding[0] or "") != project_id:
                raise RuntimeError("room project binding generation conflict")
        existing_payload = (
            _load(row[1])
            if row is not None and str(row[0]) == room_id
            else _load(existing_room[1])
            if existing_room is not None
            else None
        )
        incoming = _fence_project_room_merge(existing_payload, incoming)
        payload = _merge_room_payload(existing_payload, incoming, room_id=room_id)
        metadata = payload.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata.update(
            {
                "project_id": project_id,
                "project_binding_generation": generation,
                "source": "projectos",
            }
        )
        payload.update(
            {
                "project_id": project_id,
                "is_project_group": True,
                "metadata": metadata,
                "updated_at": str(payload.get("updated_at") or now),
            }
        )
        created_at = (
            str(row[2])
            if row is not None and str(row[0]) == room_id
            else str(existing_room[2])
            if existing_room is not None
            else str(payload.get("created_at") or now)
        )
        payload.setdefault("created_at", created_at)
        if existing_room and str(existing_room[0]) != session_id:
            conn.execute(
                "UPDATE collaboration_tasks SET session_id=? WHERE room_id=?",
                (session_id, room_id),
            )
            conn.execute(
                "UPDATE collaboration_messages SET session_id=? WHERE room_id=?",
                (session_id, room_id),
            )
            conn.execute("DELETE FROM collaboration_rooms WHERE room_id=?", (room_id,))
        conn.execute(
            "INSERT INTO collaboration_rooms(session_id, room_id, room_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "room_id=excluded.room_id, room_json=excluded.room_json, updated_at=excluded.updated_at",
            (
                session_id,
                room_id,
                _dump(payload, label="room"),
                created_at,
                payload["updated_at"],
            ),
        )
        conn.execute(
            "INSERT INTO collaboration_project_generations(session_id, project_id, generation) "
            "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "project_id=excluded.project_id, generation=excluded.generation",
            (session_id, project_id, generation),
        )
        conn.execute(
            "INSERT INTO collaboration_room_owners(room_id, session_id, project_id, generation) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(room_id) DO UPDATE SET "
            "session_id=excluded.session_id, project_id=excluded.project_id, "
            "generation=excluded.generation",
            (room_id, session_id, project_id, generation),
        )
        if session_id != f"project:{project_id}":
            conn.execute(
                "DELETE FROM collaboration_project_room_bindings "
                "WHERE project_id=? AND session_id=?",
                (project_id, f"project:{project_id}"),
            )
        conn.execute(
            "INSERT INTO collaboration_project_room_bindings("
            "project_id, session_id, room_id, generation) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_id, session_id) DO UPDATE SET "
            "room_id=excluded.room_id, generation=excluded.generation",
            (project_id, session_id, room_id, generation),
        )
    return payload


__all__ = ["ProjectRoomVersionConflict", "upsert_project_room", "upsert_room"]
