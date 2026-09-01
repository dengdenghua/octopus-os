"""Atomic source commits for collaboration-message Project OS actions."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol, cast

from runtime.projectos._store_helpers import (
    _json_dict,
    _milestone_from_doc,
    _normalize_milestone,
    _normalize_task,
    _require_id,
    _require_kind,
    _task_from_doc,
)
from runtime.projectos._store_project_deletion import assert_project_not_deleting
from runtime.projectos._store_thread_bindings import _assert_binding_matches
from runtime.projectos.model import Task
from runtime.safety.auth.scope import TenantScope

_CLOSED_PROJECT_STATUSES = frozenset({"blocked", "done", "failed"})
_CLOSED_MILESTONE_STATUSES = frozenset({"blocked", "done", "failed"})


class _MessageActionStore(Protocol):
    _lock: Any

    def _conn(self) -> Any: ...

    def _effective_scope(self, scope: TenantScope | None) -> TenantScope | None: ...

    def _project_doc_for_scope(
        self,
        conn: Any,
        project_id: str,
        scope: TenantScope | None,
    ) -> Any: ...


def commit_message_action(
    store: _MessageActionStore,
    project_id: str,
    *,
    event_id: str,
    kind: str,
    payload: dict[str, Any],
    expected_thread_id: str,
    expected_binding_generation: int,
    task: Task | None = None,
    scope: TenantScope | None = None,
) -> tuple[dict[str, Any], Task | None, bool]:
    """Commit an optional new task and its audit/outbox event together."""

    project_id = _require_id(project_id, label="project_id")
    event_id = _require_id(event_id, label="event_id")
    kind = _require_kind(kind)
    payload = _json_dict(payload, label="event payload")
    thread_id = _require_id(expected_thread_id, label="thread_id")
    if expected_binding_generation < 0:
        raise ValueError("expected binding generation must be non-negative")
    candidate = _normalize_task(task) if task is not None else None
    created_at = time.time()
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_project_not_deleting(conn, project_id)
        _assert_binding_matches(
            conn,
            thread_id=thread_id,
            project_id=project_id,
            generation=expected_binding_generation,
        )
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        existing_event = conn.execute(
            "SELECT project_id, kind, payload, created_at FROM project_events WHERE id=?",
            (event_id,),
        ).fetchone()
        if existing_event is not None:
            if str(existing_event[0]) != project_id or str(existing_event[1]) != kind:
                raise ValueError("project action id is already in use")
            stored_payload = _json_dict(
                json.loads(existing_event[2]),
                label="event payload",
            )
            if stored_payload != payload:
                raise ValueError("project action id already belongs to different input")
            stored_task = None
            if candidate is not None:
                task_row = conn.execute(
                    "SELECT doc, milestone_id FROM tasks WHERE id=?",
                    (candidate.id,),
                ).fetchone()
                if task_row is None or str(task_row[1]) != candidate.milestone_id:
                    raise ValueError("project action task is unavailable")
                stored_task = _task_from_doc(str(task_row[0]))
                if stored_task is None:
                    raise ValueError("project action task is corrupt")
            return (
                {
                    "id": event_id,
                    "project_id": project_id,
                    "kind": kind,
                    "payload": stored_payload,
                    "created_at": float(existing_event[3]),
                },
                stored_task,
                False,
            )

        stored_task = None
        if candidate is not None:
            if project.status in _CLOSED_PROJECT_STATUSES:
                raise ValueError("cannot add a task to a terminal project")
            milestone_row = conn.execute(
                "SELECT doc, project_id FROM milestones WHERE id=?",
                (candidate.milestone_id,),
            ).fetchone()
            if milestone_row is None or str(milestone_row[1]) != project_id:
                raise ValueError("milestone does not belong to project")
            milestone = _milestone_from_doc(str(milestone_row[0]))
            if milestone is None:
                raise ValueError("project action milestone is corrupt")
            if milestone.status in _CLOSED_MILESTONE_STATUSES:
                raise ValueError("cannot add a task to a terminal milestone")
            task_row = conn.execute(
                "SELECT doc, milestone_id FROM tasks WHERE id=?",
                (candidate.id,),
            ).fetchone()
            if task_row is not None:
                if str(task_row[1]) != candidate.milestone_id:
                    raise ValueError("task is already attached to another milestone")
                stored_task = _task_from_doc(str(task_row[0]))
                if stored_task is None:
                    raise ValueError("project action task is corrupt")
            else:
                stored_task = candidate
                conn.execute(
                    "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, ?)",
                    (
                        candidate.id,
                        candidate.milestone_id,
                        json.dumps(candidate.to_dict(), ensure_ascii=False),
                    ),
                )
            if candidate.id not in milestone.task_ids:
                milestone.task_ids.append(candidate.id)
                milestone = _normalize_milestone(milestone)
                conn.execute(
                    "UPDATE milestones SET doc=? WHERE id=?",
                    (json.dumps(milestone.to_dict(), ensure_ascii=False), milestone.id),
                )
        event = {
            "id": event_id,
            "project_id": project_id,
            "kind": kind,
            "payload": payload,
            "created_at": created_at,
        }
        conn.execute(
            "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, project_id, kind, json.dumps(payload, ensure_ascii=False), created_at),
        )
        return event, stored_task, True


class ProjectMessageActionStoreMixin:
    def commit_message_action(
        self,
        project_id: str,
        *,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
        expected_thread_id: str,
        expected_binding_generation: int,
        task: Task | None = None,
        scope: TenantScope | None = None,
    ) -> tuple[dict[str, Any], Task | None, bool]:
        return commit_message_action(
            cast(_MessageActionStore, self),
            project_id,
            event_id=event_id,
            kind=kind,
            payload=payload,
            expected_thread_id=expected_thread_id,
            expected_binding_generation=expected_binding_generation,
            task=task,
            scope=scope,
        )


__all__ = ["ProjectMessageActionStoreMixin", "commit_message_action"]
