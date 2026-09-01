"""Project OS projection writes for :mod:`collaboration_store`.

This private module keeps the canonical collaboration store focused on its
general room, task, and message APIs. Callers should continue using
``CollaborationStore`` rather than importing these helpers directly.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any, Protocol

from runtime.memory.cowork.ids import optional_cowork_id, require_cowork_id


class _ProjectionStore(Protocol):
    _lock: threading.Lock

    def _connect(self) -> sqlite3.Connection: ...


def assert_project_projection_writable(conn: sqlite3.Connection, project_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM collaboration_deleted_projects WHERE project_id=?",
        (project_id,),
    ).fetchone():
        raise RuntimeError("project collaboration projection was deleted")


def set_room_project_metadata(
    store: _ProjectionStore,
    session_id: str,
    project_id: str | None,
    *,
    expected_project_id: str | None = None,
    generation: int | None = None,
) -> dict[str, Any] | None:
    """Add or remove only a room's optional Project OS projection."""

    # Imported lazily to keep the storage/normalization source of truth in the
    # public store module without creating an import cycle during module load.
    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _load,
        _normalize_room_payload,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    desired = optional_cowork_id(project_id, label="project_id") or ""
    expected = optional_cowork_id(expected_project_id, label="project_id") or ""
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if desired:
            assert_project_projection_writable(conn, desired)
        row = conn.execute(
            "SELECT room_id, room_json FROM collaboration_rooms WHERE session_id=?",
            (session_id,),
        ).fetchone()
        payload = _load(str(row[1])) if row else None
        if row is not None and payload is None:
            raise ValueError("invalid collaboration room")
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        json_id = str(
            metadata.get("project_id")
            or (payload.get("project_id") if isinstance(payload, dict) else "")
            or ""
        ).strip()
        raw_json_generation = metadata.get("project_binding_generation")
        binding_row = conn.execute(
            "SELECT project_id, generation FROM collaboration_project_generations "
            "WHERE session_id=?",
            (session_id,),
        ).fetchone()
        has_generation = binding_row is not None or isinstance(raw_json_generation, int)
        current_id = str(binding_row[0]).strip() if binding_row else json_id
        current_generation = (
            max(0, int(binding_row[1]))
            if binding_row
            else max(0, raw_json_generation)
            if isinstance(raw_json_generation, int)
            else 0
        )
        if generation is not None and (not isinstance(generation, int) or generation < 0):
            raise ValueError("project binding generation must be non-negative")
        incoming_generation = current_generation if generation is None else generation
        if incoming_generation < current_generation:
            raise RuntimeError("stale room project binding generation")
        if incoming_generation == current_generation and has_generation and desired != current_id:
            raise RuntimeError("room project binding generation conflict")
        if generation is None:
            if expected and current_id and current_id != expected:
                raise RuntimeError("room project metadata changed")
            if desired and current_id and current_id != desired:
                raise RuntimeError("room is already projected to another project")
        conn.execute(
            "INSERT INTO collaboration_project_generations(session_id, project_id, generation) "
            "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "project_id=excluded.project_id, generation=excluded.generation",
            (session_id, desired, incoming_generation),
        )
        if payload is None or row is None:
            return None
        owner = conn.execute(
            "SELECT session_id, project_id, generation FROM collaboration_room_owners "
            "WHERE room_id=?",
            (str(row[0]),),
        ).fetchone()
        if owner is not None:
            owner_generation = max(0, int(owner[2] or 0))
            if incoming_generation < owner_generation:
                raise RuntimeError("stale project room binding generation")
            if incoming_generation == owner_generation and (
                str(owner[0]) != session_id or (str(owner[1] or "") and str(owner[1]) != desired)
            ):
                raise RuntimeError("project room binding generation conflict")
        conn.execute(
            "INSERT INTO collaboration_room_owners(room_id, session_id, project_id, generation) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(room_id) DO UPDATE SET "
            "session_id=excluded.session_id, project_id=excluded.project_id, "
            "generation=excluded.generation",
            (str(row[0]), session_id, desired, incoming_generation),
        )
        if current_id and current_id != desired:
            conn.execute(
                "DELETE FROM collaboration_project_room_bindings "
                "WHERE project_id=? AND session_id=?",
                (current_id, session_id),
            )
        if desired:
            conn.execute(
                "INSERT INTO collaboration_project_room_bindings("
                "project_id, session_id, room_id, generation) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(project_id, session_id) DO UPDATE SET "
                "room_id=excluded.room_id, generation=excluded.generation",
                (desired, session_id, str(row[0]), incoming_generation),
            )
        if desired:
            metadata["project_id"] = desired
            metadata.setdefault("source", "projectos")
            payload["project_id"] = desired
            payload["is_project_group"] = True
        else:
            metadata.pop("project_id", None)
            if metadata.get("source") == "projectos":
                metadata.pop("source", None)
            payload["project_id"] = None
            payload["is_project_group"] = False
        metadata["project_binding_generation"] = incoming_generation
        payload["metadata"] = metadata
        payload["updated_at"] = _now()
        normalized = _normalize_room_payload(payload, room_id=str(row[0]))
        conn.execute(
            "UPDATE collaboration_rooms SET room_json=?, updated_at=? WHERE session_id=?",
            (_dump(normalized, label="room"), normalized["updated_at"], session_id),
        )
        return normalized


def upsert_project_task(
    store: _ProjectionStore,
    *,
    session_id: str,
    room_id: str,
    project_id: str,
    milestone_id: str,
    task: dict[str, Any],
    binding_generation: int | None = None,
) -> dict[str, Any]:
    """Write a task only while its room projection is on the same generation."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _normalize_task_payload,
        _now,
    )

    session_id = require_cowork_id(session_id, label="session_id")
    room_id = require_cowork_id(room_id, label="room_id")
    project_id = require_cowork_id(project_id, label="project_id")
    milestone_id = require_cowork_id(milestone_id, label="milestone_id")
    if binding_generation is not None and (
        not isinstance(binding_generation, int) or binding_generation < 0
    ):
        raise ValueError("project binding generation must be non-negative")
    payload = dict(task or {})
    payload["kind"] = "project"
    payload["room_id"] = room_id
    payload["project_id"] = project_id
    payload["milestone_id"] = milestone_id
    raw_metadata = payload.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    payload["metadata"] = {
        **metadata,
        "source": "projectos",
        "project_id": project_id,
        "milestone_id": milestone_id,
        **(
            {"project_binding_generation": binding_generation}
            if binding_generation is not None
            else {}
        ),
    }
    task_id = require_cowork_id(
        payload.get("id") or payload.get("task_id") or "",
        label="task_id",
    )
    payload = _normalize_task_payload(
        payload,
        task_id=task_id,
        room_id=room_id,
        session_id=session_id,
    )
    now = _now()
    created_at = str(payload.get("created_at") or now)
    updated_at = str(payload.get("updated_at") or now)
    status = str(payload.get("status") or "pending")
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_project_projection_writable(conn, project_id)
        if binding_generation is not None:
            room_row = conn.execute(
                "SELECT room_id FROM collaboration_rooms WHERE session_id=?",
                (session_id,),
            ).fetchone()
            binding_row = conn.execute(
                "SELECT project_id, generation FROM collaboration_project_generations "
                "WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if (
                room_row is None
                or str(room_row[0]) != room_id
                or binding_row is None
                or str(binding_row[0]) != project_id
                or int(binding_row[1]) != binding_generation
            ):
                raise RuntimeError("stale project task binding generation")
            owner = conn.execute(
                "SELECT session_id, project_id, generation FROM collaboration_room_owners "
                "WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if (
                owner is None
                or str(owner[0]) != session_id
                or str(owner[1]) != project_id
                or int(owner[2]) != binding_generation
            ):
                raise RuntimeError("stale project task room generation")
        conn.execute(
            "INSERT INTO collaboration_tasks("
            "task_id, session_id, room_id, status, task_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "session_id=excluded.session_id, room_id=excluded.room_id, "
            "status=excluded.status, task_json=excluded.task_json, updated_at=excluded.updated_at",
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


def delete_project_tasks(
    store: _ProjectionStore,
    *,
    session_id: str,
    project_id: str,
    source: str = "projectos",
) -> int:
    """Delete one failed project projection without touching other history."""

    session_id = require_cowork_id(session_id, label="session_id")
    project_id = require_cowork_id(project_id, label="project_id")
    source = str(source or "").strip()
    if source != "projectos":
        raise ValueError("project task source must be projectos")
    with store._lock, store._connect() as conn:
        deleted = conn.execute(
            "DELETE FROM collaboration_tasks "
            "WHERE session_id = ? AND json_valid(task_json) "
            "AND json_extract(task_json, '$.kind') = 'project' "
            "AND json_extract(task_json, '$.project_id') = ? "
            "AND json_extract(task_json, '$.metadata.source') = ?",
            (session_id, project_id, source),
        )
        return max(0, int(deleted.rowcount))


def delete_project_tasks_for_project(
    store: _ProjectionStore,
    *,
    project_id: str,
    source: str = "projectos",
) -> int:
    """Atomically remove one Project OS task projection from every session."""

    project_id = require_cowork_id(project_id, label="project_id")
    source = str(source or "").strip()
    if source != "projectos":
        raise ValueError("project task source must be projectos")
    with store._lock, store._connect() as conn:
        sessions = conn.execute(
            "SELECT DISTINCT session_id FROM collaboration_tasks "
            "WHERE json_valid(task_json) "
            "AND json_extract(task_json, '$.kind') = 'project' "
            "AND json_extract(task_json, '$.project_id') = ? "
            "AND json_extract(task_json, '$.metadata.source') = ?",
            (project_id, source),
        ).fetchall()
        deleted_count = 0
        for row in sessions:
            session_id = require_cowork_id(row[0], label="session_id")
            deleted = conn.execute(
                "DELETE FROM collaboration_tasks "
                "WHERE session_id = ? AND json_valid(task_json) "
                "AND json_extract(task_json, '$.kind') = 'project' "
                "AND json_extract(task_json, '$.project_id') = ? "
                "AND json_extract(task_json, '$.metadata.source') = ?",
                (session_id, project_id, source),
            )
            deleted_count += max(0, int(deleted.rowcount))
        return deleted_count


def tombstone_project_projection(
    store: _ProjectionStore,
    *,
    project_id: str,
    token: str,
) -> None:
    """Clear every project room/task surface and permanently fence late writers."""

    from runtime.memory.cowork.collaboration_store import (
        _dump,
        _load,
        _normalize_room_payload,
        _now,
    )

    project_id = require_cowork_id(project_id, label="project_id")
    token = require_cowork_id(token, label="project_delete_token")
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        tombstone = conn.execute(
            "SELECT token FROM collaboration_deleted_projects WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if tombstone is not None:
            if str(tombstone[0]) != token:
                raise RuntimeError("project collaboration delete token changed")
            return
        rows = conn.execute(
            "SELECT session_id, room_id, room_json FROM collaboration_rooms "
            "WHERE session_id IN ("
            "SELECT session_id FROM collaboration_project_generations WHERE project_id=? "
            "UNION SELECT session_id FROM collaboration_project_room_bindings "
            "WHERE project_id=?"
            ") OR (json_valid(room_json) AND ("
            "json_extract(room_json, '$.project_id')=? OR "
            "json_extract(room_json, '$.metadata.project_id')=?))",
            (project_id, project_id, project_id, project_id),
        ).fetchall()
        for raw_session_id, raw_room_id, raw_room_json in rows:
            session_id = require_cowork_id(raw_session_id, label="session_id")
            room_id = require_cowork_id(raw_room_id, label="room_id")
            payload = _load(str(raw_room_json))
            if payload is None:
                raise ValueError("invalid collaboration room")
            binding = conn.execute(
                "SELECT project_id, generation FROM collaboration_project_generations "
                "WHERE session_id=?",
                (session_id,),
            ).fetchone()
            current_id = str(binding[0] or "") if binding else ""
            generation = max(0, int(binding[1])) if binding else 0
            metadata = payload.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            json_id = str(metadata.get("project_id") or payload.get("project_id") or "")
            if current_id not in {"", project_id} or json_id not in {"", project_id}:
                continue
            next_generation = generation + 1
            metadata.pop("project_id", None)
            if metadata.get("source") == "projectos":
                metadata.pop("source", None)
            metadata["project_binding_generation"] = next_generation
            payload["metadata"] = metadata
            payload["project_id"] = None
            payload["is_project_group"] = False
            payload["updated_at"] = _now()
            normalized = _normalize_room_payload(payload, room_id=room_id)
            conn.execute(
                "UPDATE collaboration_rooms SET room_json=?, updated_at=? WHERE session_id=?",
                (_dump(normalized, label="room"), normalized["updated_at"], session_id),
            )
            conn.execute(
                "INSERT INTO collaboration_project_generations(session_id, project_id, generation) "
                "VALUES (?, '', ?) ON CONFLICT(session_id) DO UPDATE SET "
                "project_id='', generation=excluded.generation",
                (session_id, next_generation),
            )
            conn.execute(
                "INSERT INTO collaboration_room_owners(room_id, session_id, project_id, generation) "
                "VALUES (?, ?, '', ?) ON CONFLICT(room_id) DO UPDATE SET "
                "session_id=excluded.session_id, project_id='', generation=excluded.generation",
                (room_id, session_id, next_generation),
            )
        conn.execute(
            "DELETE FROM collaboration_project_room_bindings WHERE project_id=?",
            (project_id,),
        )
        conn.execute(
            "DELETE FROM collaboration_tasks WHERE json_valid(task_json) "
            "AND json_extract(task_json, '$.kind')='project' "
            "AND json_extract(task_json, '$.project_id')=? "
            "AND json_extract(task_json, '$.metadata.source')='projectos'",
            (project_id,),
        )
        conn.execute(
            "INSERT INTO collaboration_deleted_projects(project_id, token, deleted_at) "
            "VALUES (?, ?, ?)",
            (project_id, token, datetime.now(UTC).isoformat()),
        )


def finalize_project_projection_tombstone(
    store: _ProjectionStore,
    *,
    project_id: str,
    token: str,
) -> None:
    """Validate that the permanent external deletion fence is durable."""

    project_id = require_cowork_id(project_id, label="project_id")
    token = require_cowork_id(token, label="project_delete_token")
    with store._lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        tombstone = conn.execute(
            "SELECT token FROM collaboration_deleted_projects WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if tombstone is None or str(tombstone[0]) != token:
            raise RuntimeError("project collaboration delete token changed")


def project_projection_tombstone_token(
    store: _ProjectionStore,
    *,
    project_id: str,
) -> str:
    project_id = require_cowork_id(project_id, label="project_id")
    with store._lock, store._connect() as conn:
        row = conn.execute(
            "SELECT token FROM collaboration_deleted_projects WHERE project_id=?",
            (project_id,),
        ).fetchone()
    return str(row[0]) if row else ""


__all__ = [
    "delete_project_tasks",
    "delete_project_tasks_for_project",
    "assert_project_projection_writable",
    "finalize_project_projection_tombstone",
    "project_projection_tombstone_token",
    "set_room_project_metadata",
    "tombstone_project_projection",
    "upsert_project_task",
]
