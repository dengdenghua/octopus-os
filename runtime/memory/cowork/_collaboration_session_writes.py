"""Transactional message and task writes for a collaboration room session."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Protocol

from runtime.memory.cowork._collaboration_room_write import ProjectRoomVersionConflict
from runtime.memory.cowork.ids import (
    normalize_display_name,
    optional_cowork_id,
    require_cowork_id,
    require_message_text,
)


class _SessionStore(Protocol):
    _lock: threading.Lock

    def _connect(self) -> sqlite3.Connection: ...


def _require_room_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    room_id: str,
) -> None:
    room = conn.execute(
        "SELECT session_id FROM collaboration_rooms WHERE room_id=?",
        (room_id,),
    ).fetchone()
    if room is not None:
        if str(room[0]) == session_id:
            return
        raise ProjectRoomVersionConflict("collaboration room moved to another session")
    # Older databases could contain messages/tasks before the unified room
    # snapshot was introduced. Keep those exact orphan streams appendable, but
    # do not let a new writer invent a roomless session.
    legacy = conn.execute(
        "SELECT 1 FROM collaboration_messages WHERE session_id=? AND room_id=? "
        "UNION ALL "
        "SELECT 1 FROM collaboration_tasks WHERE session_id=? AND room_id=? LIMIT 1",
        (session_id, room_id, session_id, room_id),
    ).fetchone()
    if legacy is None:
        raise ProjectRoomVersionConflict("collaboration room is missing")


def upsert_task(
    store: _SessionStore,
    session_id: str,
    task: dict[str, Any],
) -> dict[str, Any]:
    """Write a generic task only while the room still belongs to the session."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _load,
        _normalize_task_payload,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    payload = dict(task or {})
    task_id = require_cowork_id(payload.get("id") or payload.get("task_id") or "", label="task_id")
    room_id = require_cowork_id(payload.get("room_id") or "", label="room_id")
    payload = _normalize_task_payload(
        payload,
        task_id=task_id,
        room_id=room_id,
        session_id=session_id,
    )
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("source") == "projectos" or any(
        key in metadata for key in ("project_binding_generation", "binding_generation")
    ):
        raise RuntimeError("project task projections require the versioned project API")
    now = _now()
    created_at = str(payload.get("created_at") or now)
    updated_at = str(payload.get("updated_at") or now)
    status = str(payload.get("status") or "pending")
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_room_session(conn, session_id=session_id, room_id=room_id)
        existing_row = conn.execute(
            "SELECT task_json FROM collaboration_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        existing = _load(existing_row[0]) if existing_row else None
        existing_metadata = existing.get("metadata") if isinstance(existing, dict) else None
        if isinstance(existing_metadata, dict) and existing_metadata.get("source") == "projectos":
            raise RuntimeError("project task projections require the versioned project API")
        conn.execute(
            "INSERT INTO collaboration_tasks("
            "task_id, session_id, room_id, status, task_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "session_id = excluded.session_id, room_id = excluded.room_id, "
            "status = excluded.status, task_json = excluded.task_json, "
            "updated_at = excluded.updated_at",
            (
                task_id,
                session_id,
                room_id,
                status,
                _dump(payload, label="task"),
                created_at,
                updated_at,
            ),
        )
    return payload


def append_message(
    store: _SessionStore,
    session_id: str,
    *,
    room_id: str,
    text: str,
    participant_id: str = "",
    display_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Append only while the exact room/session ownership is still current."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _normalize_message_metadata,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    room_id = require_cowork_id(room_id, label="room_id")
    participant_id = optional_cowork_id(participant_id, label="participant_id")
    display_name = normalize_display_name(display_name)
    text = require_message_text(text)
    message_metadata = _normalize_message_metadata(metadata)
    ts = _now()
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_room_session(conn, session_id=session_id, room_id=room_id)
        source_message_id = str(message_metadata.get("source_message_id") or "")
        if source_message_id:
            existing = conn.execute(
                "SELECT seq, room_id, participant_id, display_name, text "
                "FROM collaboration_messages "
                "WHERE session_id = ? "
                "AND CASE WHEN json_valid(metadata_json) "
                "THEN json_extract(metadata_json, '$.source_message_id') END = ?",
                (session_id, source_message_id),
            ).fetchone()
            if existing:
                if (
                    str(existing[1] or "") != room_id
                    or str(existing[2] or "") != participant_id
                    or str(existing[3] or "") != display_name
                    or str(existing[4] or "") != text
                ):
                    raise ValueError(
                        "source_message_id already belongs to a different room message"
                    )
                return int(existing[0])
        cur = conn.execute(
            "INSERT INTO collaboration_messages("
            "session_id, seq, room_id, participant_id, display_name, text, ts, metadata_json"
            ") VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM collaboration_messages "
            "WHERE session_id = ?), ?, ?, ?, ?, ?, ?) RETURNING seq",
            (
                session_id,
                session_id,
                room_id,
                participant_id,
                display_name,
                text,
                ts,
                _dump(message_metadata, label="message metadata"),
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


__all__ = ["append_message", "upsert_task"]
