"""Generation-fenced collaboration projection for Project OS message actions."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Protocol, cast

from runtime.memory.cowork.ids import (
    normalize_display_name,
    require_cowork_id,
    require_message_text,
)


class ProjectMessageProjectionStale(RuntimeError):
    """The collaboration room no longer represents the source binding."""


class _ActionProjectionStore(Protocol):
    _lock: threading.Lock

    def _connect(self) -> sqlite3.Connection: ...


def _require_projection_generation(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    room_id: str,
    project_id: str,
    generation: int,
) -> None:
    room = conn.execute(
        "SELECT room_id FROM collaboration_rooms WHERE session_id=?",
        (session_id,),
    ).fetchone()
    binding = conn.execute(
        "SELECT project_id, generation FROM collaboration_project_generations WHERE session_id=?",
        (session_id,),
    ).fetchone()
    owner = conn.execute(
        "SELECT session_id, project_id, generation FROM collaboration_room_owners WHERE room_id=?",
        (room_id,),
    ).fetchone()
    if (
        room is None
        or str(room[0]) != room_id
        or binding is None
        or str(binding[0]) != project_id
        or int(binding[1]) != generation
        or owner is None
        or str(owner[0]) != session_id
        or str(owner[1]) != project_id
        or int(owner[2]) != generation
    ):
        raise ProjectMessageProjectionStale(
            "collaboration project binding changed before action projection"
        )


def _project_task_payload(
    task: dict[str, Any],
    *,
    session_id: str,
    room_id: str,
    project_id: str,
    milestone_id: str,
    generation: int,
) -> dict[str, Any]:
    from runtime.memory.cowork.collaboration_store import _normalize_task_payload

    payload = dict(task or {})
    task_id = require_cowork_id(
        payload.get("id") or payload.get("task_id") or "",
        label="task_id",
    )
    payload.update(
        {
            "kind": "project",
            "room_id": room_id,
            "project_id": project_id,
            "milestone_id": milestone_id,
        }
    )
    raw_metadata = payload.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    payload["metadata"] = {
        **metadata,
        "source": "projectos",
        "project_id": project_id,
        "milestone_id": milestone_id,
        "project_binding_generation": generation,
    }
    return _normalize_task_payload(
        payload,
        task_id=task_id,
        room_id=room_id,
        session_id=session_id,
    )


def commit_project_message_action(
    store: _ActionProjectionStore,
    *,
    session_id: str,
    room_id: str,
    project_id: str,
    binding_generation: int,
    source_message_seq: int,
    source_metadata: dict[str, Any],
    card_text: str,
    card_metadata: dict[str, Any],
    task: dict[str, Any] | None = None,
    milestone_id: str | None = None,
) -> dict[str, Any]:
    """Atomically project one committed action onto its exact room generation."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _load,
        _merge_message_metadata,
        _message_from_row,
        _normalize_message_metadata,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    room_id = require_cowork_id(room_id, label="room_id")
    project_id = require_cowork_id(project_id, label="project_id")
    if not isinstance(binding_generation, int) or binding_generation < 0:
        raise ValueError("project binding generation must be non-negative")
    if int(source_message_seq) < 1:
        raise ValueError("source_message_seq must be positive")
    source_patch = _normalize_message_metadata(source_metadata)
    normalized_card_metadata = _normalize_message_metadata(card_metadata)
    card_source_id = require_cowork_id(
        normalized_card_metadata.get("source_message_id") or "",
        label="source_message_id",
    )
    normalized_card_text = require_message_text(card_text)
    normalized_task = None
    if task is not None:
        milestone_id = require_cowork_id(milestone_id or "", label="milestone_id")
        normalized_task = _project_task_payload(
            task,
            session_id=session_id,
            room_id=room_id,
            project_id=project_id,
            milestone_id=milestone_id,
            generation=binding_generation,
        )

    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_projection_generation(
            conn,
            session_id=session_id,
            room_id=room_id,
            project_id=project_id,
            generation=binding_generation,
        )
        source_row = conn.execute(
            "SELECT session_id, seq, room_id, participant_id, display_name, text, ts, "
            "metadata_json FROM collaboration_messages WHERE session_id=? AND seq=?",
            (session_id, int(source_message_seq)),
        ).fetchone()
        if source_row is None or str(source_row[2]) != room_id:
            raise ProjectMessageProjectionStale(
                "source message no longer belongs to the project room generation"
            )
        source_metadata_before = _load(str(source_row[7] or "{}")) or {}
        source_metadata_after = _merge_message_metadata(source_metadata_before, source_patch)
        conn.execute(
            "UPDATE collaboration_messages SET metadata_json=? WHERE session_id=? AND seq=?",
            (
                _dump(source_metadata_after, label="message metadata"),
                session_id,
                int(source_message_seq),
            ),
        )

        projected_task = None
        if normalized_task is not None:
            task_id = str(normalized_task["id"])
            existing_task_row = conn.execute(
                "SELECT task_json, created_at FROM collaboration_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            existing_task = _load(str(existing_task_row[0])) if existing_task_row else None
            existing_metadata = (
                existing_task.get("metadata") if isinstance(existing_task, dict) else None
            )
            existing_project_id = (
                str(existing_task.get("project_id") or "")
                if isinstance(existing_task, dict)
                else ""
            )
            existing_generation = (
                int(existing_metadata.get("project_binding_generation", -1))
                if isinstance(existing_metadata, dict)
                else -1
            )
            if existing_task_row is not None and (
                not isinstance(existing_task, dict)
                or not isinstance(existing_metadata, dict)
                or existing_metadata.get("source") != "projectos"
                or existing_project_id != project_id
                or existing_generation != binding_generation
            ):
                raise RuntimeError("project action task id is already in use")
            now = _now()
            created_at = (
                str(existing_task_row[1])
                if existing_task_row is not None
                else str(normalized_task.get("created_at") or now)
            )
            updated_at = str(normalized_task.get("updated_at") or now)
            conn.execute(
                "INSERT INTO collaboration_tasks("
                "task_id, session_id, room_id, status, task_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                "session_id=excluded.session_id, room_id=excluded.room_id, "
                "status=excluded.status, task_json=excluded.task_json, "
                "updated_at=excluded.updated_at",
                (
                    task_id,
                    session_id,
                    room_id,
                    str(normalized_task.get("status") or "pending"),
                    _dump(normalized_task, label="task"),
                    created_at,
                    updated_at,
                ),
            )
            projected_task = normalized_task

        card_row = conn.execute(
            "SELECT session_id, seq, room_id, participant_id, display_name, text, ts, "
            "metadata_json FROM collaboration_messages WHERE session_id=? "
            "AND CASE WHEN json_valid(metadata_json) "
            "THEN json_extract(metadata_json, '$.source_message_id') END=?",
            (session_id, card_source_id),
        ).fetchone()
        card_created = False
        if card_row is not None:
            if str(card_row[2]) != room_id or str(card_row[5]) != normalized_card_text:
                raise RuntimeError("project action card id is already in use")
        else:
            card_created = True
            timestamp = _now()
            card_seq_row = conn.execute(
                "INSERT INTO collaboration_messages("
                "session_id, seq, room_id, participant_id, display_name, text, ts, metadata_json"
                ") VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 "
                "FROM collaboration_messages WHERE session_id=?), ?, ?, ?, ?, ?, ?) "
                "RETURNING seq",
                (
                    session_id,
                    session_id,
                    room_id,
                    "project-os",
                    normalize_display_name("Project OS"),
                    normalized_card_text,
                    timestamp,
                    _dump(normalized_card_metadata, label="message metadata"),
                ),
            ).fetchone()
            card_seq = int(card_seq_row[0]) if card_seq_row else 0
            card_row = conn.execute(
                "SELECT session_id, seq, room_id, participant_id, display_name, text, ts, "
                "metadata_json FROM collaboration_messages WHERE session_id=? AND seq=?",
                (session_id, card_seq),
            ).fetchone()
        updated_source_row = conn.execute(
            "SELECT session_id, seq, room_id, participant_id, display_name, text, ts, "
            "metadata_json FROM collaboration_messages WHERE session_id=? AND seq=?",
            (session_id, int(source_message_seq)),
        ).fetchone()
        if updated_source_row is None or card_row is None:
            raise RuntimeError("project action collaboration projection was not persisted")
        return {
            "source_message": _message_from_row(updated_source_row),
            "system_card_message": _message_from_row(card_row),
            "task": projected_task,
            "card_created": card_created,
        }


class CollaborationProjectActionStoreMixin:
    def commit_project_message_action(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return commit_project_message_action(cast(_ActionProjectionStore, self), **kwargs)


__all__ = [
    "CollaborationProjectActionStoreMixin",
    "ProjectMessageProjectionStale",
    "commit_project_message_action",
]
