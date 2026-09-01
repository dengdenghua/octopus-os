"""Durable source-write fence for cross-store Project OS deletion sagas."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import uuid4

from runtime.projectos._store_helpers import _json_dict, _require_id, _require_kind
from runtime.projectos.model import Project
from runtime.safety.auth.scope import TenantScope

_DELETE_GUARD_MESSAGE = "project delete in progress"

_DELETE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS project_delete_claims (
    project_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_delete_tombstones (
    project_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    deleted_at REAL NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_deleted_milestones (
    milestone_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_deleted_tasks (
    task_id TEXT PRIMARY KEY,
    milestone_id TEXT NOT NULL,
    project_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_thread_delete_claims (
    thread_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    created_at REAL NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_thread_delete_tombstones (
    thread_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    deleted_at REAL NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS project_delete_guard_project_insert
BEFORE INSERT ON projects
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=NEW.id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_project_update
BEFORE UPDATE ON projects
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id IN (OLD.id, NEW.id))
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_project_delete
BEFORE DELETE ON projects
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=OLD.id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_insert
BEFORE INSERT ON milestones
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=NEW.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_update
BEFORE UPDATE ON milestones
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims WHERE project_id IN (OLD.project_id, NEW.project_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_delete
BEFORE DELETE ON milestones
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=OLD.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_insert
BEFORE INSERT ON tasks
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id=NEW.milestone_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_update
BEFORE UPDATE ON tasks
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id IN (OLD.milestone_id, NEW.milestone_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_delete
BEFORE DELETE ON tasks
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id=OLD.milestone_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_event_insert
BEFORE INSERT ON project_events
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=NEW.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_event_update
BEFORE UPDATE ON project_events
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims WHERE project_id IN (OLD.project_id, NEW.project_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_event_delete
BEFORE DELETE ON project_events
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=OLD.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_binding_insert
BEFORE INSERT ON thread_projects
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=NEW.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_binding_update
BEFORE UPDATE ON thread_projects
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims WHERE project_id IN (OLD.project_id, NEW.project_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_thread_delete_guard_binding_insert
BEFORE INSERT ON thread_projects
WHEN EXISTS (
    SELECT 1 FROM project_thread_delete_claims WHERE thread_id=NEW.thread_id
) OR EXISTS (
    SELECT 1 FROM project_thread_delete_tombstones WHERE thread_id=NEW.thread_id
)
BEGIN SELECT RAISE(ABORT, 'thread delete in progress'); END;
CREATE TRIGGER IF NOT EXISTS project_thread_delete_guard_binding_update
BEFORE UPDATE ON thread_projects
WHEN EXISTS (
    SELECT 1 FROM project_thread_delete_claims WHERE thread_id IN (OLD.thread_id, NEW.thread_id)
) OR EXISTS (
    SELECT 1 FROM project_thread_delete_tombstones
    WHERE thread_id IN (OLD.thread_id, NEW.thread_id)
)
BEGIN SELECT RAISE(ABORT, 'thread delete in progress'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_binding_delete
BEFORE DELETE ON thread_projects
WHEN EXISTS (SELECT 1 FROM project_delete_claims WHERE project_id=OLD.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_claim_insert
BEFORE INSERT ON task_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    INNER JOIN tasks t ON t.milestone_id=m.id
    WHERE t.id=NEW.task_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_claim_update
BEFORE UPDATE ON task_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    INNER JOIN tasks t ON t.milestone_id=m.id
    WHERE t.id IN (OLD.task_id, NEW.task_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_task_claim_delete
BEFORE DELETE ON task_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    INNER JOIN tasks t ON t.milestone_id=m.id
    WHERE t.id=OLD.task_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_claim_insert
BEFORE INSERT ON milestone_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id=NEW.milestone_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_claim_update
BEFORE UPDATE ON milestone_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id IN (OLD.milestone_id, NEW.milestone_id)
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_delete_guard_milestone_claim_delete
BEFORE DELETE ON milestone_claims
WHEN EXISTS (
    SELECT 1 FROM project_delete_claims d
    INNER JOIN milestones m ON m.project_id=d.project_id
    WHERE m.id=OLD.milestone_id
)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_project_insert
BEFORE INSERT ON projects
WHEN EXISTS (SELECT 1 FROM project_delete_tombstones WHERE project_id=NEW.id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_project_update
BEFORE UPDATE ON projects
WHEN EXISTS (SELECT 1 FROM project_delete_tombstones WHERE project_id IN (OLD.id, NEW.id))
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_milestone_insert
BEFORE INSERT ON milestones
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (SELECT 1 FROM project_delete_tombstones WHERE project_id=NEW.project_id)
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones WHERE milestone_id=NEW.id
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_milestone_update
BEFORE UPDATE ON milestones
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (
      SELECT 1 FROM project_delete_tombstones WHERE project_id IN (OLD.project_id, NEW.project_id)
  )
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones WHERE milestone_id IN (OLD.id, NEW.id)
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_task_insert
BEFORE INSERT ON tasks
WHEN NOT EXISTS (SELECT 1 FROM milestones WHERE id=NEW.milestone_id)
  OR EXISTS (SELECT 1 FROM project_deleted_tasks WHERE task_id=NEW.id)
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones WHERE milestone_id=NEW.milestone_id
  )
  OR EXISTS (
      SELECT 1 FROM project_delete_tombstones d
      INNER JOIN milestones m ON m.project_id=d.project_id
      WHERE m.id=NEW.milestone_id
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_task_update
BEFORE UPDATE ON tasks
WHEN NOT EXISTS (SELECT 1 FROM milestones WHERE id=NEW.milestone_id)
  OR EXISTS (SELECT 1 FROM project_deleted_tasks WHERE task_id IN (OLD.id, NEW.id))
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones
      WHERE milestone_id IN (OLD.milestone_id, NEW.milestone_id)
  )
  OR EXISTS (
      SELECT 1 FROM project_delete_tombstones d
      INNER JOIN milestones m ON m.project_id=d.project_id
      WHERE m.id IN (OLD.milestone_id, NEW.milestone_id)
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_event_insert
BEFORE INSERT ON project_events
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (SELECT 1 FROM project_delete_tombstones WHERE project_id=NEW.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_event_update
BEFORE UPDATE ON project_events
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (
      SELECT 1 FROM project_delete_tombstones WHERE project_id IN (OLD.project_id, NEW.project_id)
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_binding_insert
BEFORE INSERT ON thread_projects
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (SELECT 1 FROM project_delete_tombstones WHERE project_id=NEW.project_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_binding_update
BEFORE UPDATE ON thread_projects
WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id=NEW.project_id)
  OR EXISTS (
      SELECT 1 FROM project_delete_tombstones WHERE project_id IN (OLD.project_id, NEW.project_id)
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_task_claim_insert
BEFORE INSERT ON task_claims
WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id)
  OR EXISTS (SELECT 1 FROM project_deleted_tasks WHERE task_id=NEW.task_id)
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_task_claim_update
BEFORE UPDATE ON task_claims
WHEN NOT EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id)
  OR EXISTS (SELECT 1 FROM project_deleted_tasks WHERE task_id IN (OLD.task_id, NEW.task_id))
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;

CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_milestone_claim_insert
BEFORE INSERT ON milestone_claims
WHEN NOT EXISTS (SELECT 1 FROM milestones WHERE id=NEW.milestone_id)
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones WHERE milestone_id=NEW.milestone_id
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
CREATE TRIGGER IF NOT EXISTS project_tombstone_guard_milestone_claim_update
BEFORE UPDATE ON milestone_claims
WHEN NOT EXISTS (SELECT 1 FROM milestones WHERE id=NEW.milestone_id)
  OR EXISTS (
      SELECT 1 FROM project_deleted_milestones
      WHERE milestone_id IN (OLD.milestone_id, NEW.milestone_id)
  )
BEGIN SELECT RAISE(ABORT, '{_DELETE_GUARD_MESSAGE}'); END;
"""


class _DeletionStore(Protocol):
    _lock: threading.Lock
    _scope: TenantScope | None

    def _conn(self) -> sqlite3.Connection: ...

    def _project_doc_for_scope(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        scope: TenantScope | None,
    ) -> Project | None: ...


class ProjectDeleteInProgressError(RuntimeError):
    """A normal source mutation encountered a durable delete claim."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"project delete in progress: {project_id}")


class ProjectDeletedError(RuntimeError):
    """A source mutation attempted to reuse a permanently deleted project id."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"project was deleted: {project_id}")


class ProjectThreadBoundError(RuntimeError):
    """A thread delete lost to an existing canonical project binding."""

    def __init__(self, thread_id: str, project: Project) -> None:
        self.thread_id = thread_id
        self.project = project
        super().__init__(f"thread is bound to project: {thread_id} -> {project.id}")


class ProjectThreadDeletingError(RuntimeError):
    """A project binding attempted to use a deleting/deleted thread."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"thread delete in progress: {thread_id}")


@dataclass(slots=True, frozen=True)
class ProjectDeleteLease:
    project: Project
    token: str
    thread_ids: tuple[str, ...]
    resumed: bool


@dataclass(slots=True, frozen=True)
class ProjectThreadDeleteLease:
    thread_id: str
    token: str
    resumed: bool
    finalized: bool
    tenant_id: str = ""
    owner_id: str = ""


def ensure_project_delete_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DELETE_SCHEMA)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(project_delete_tombstones)")}
    if "tenant_id" not in columns:
        conn.execute(
            "ALTER TABLE project_delete_tombstones ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''"
        )
    if "owner_id" not in columns:
        conn.execute(
            "ALTER TABLE project_delete_tombstones ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''"
        )
    for table in ("project_thread_delete_claims", "project_thread_delete_tombstones"):
        thread_columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if "tenant_id" not in thread_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''")
        if "owner_id" not in thread_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")


def _delete_token(conn: sqlite3.Connection, project_id: str) -> str:
    row = conn.execute(
        "SELECT token FROM project_delete_claims WHERE project_id=?",
        (project_id,),
    ).fetchone()
    return str(row[0]) if row else ""


def assert_project_not_deleting(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    token: str = "",
) -> None:
    if conn.execute(
        "SELECT 1 FROM project_delete_tombstones WHERE project_id=?",
        (project_id,),
    ).fetchone():
        raise ProjectDeletedError(project_id)
    current = _delete_token(conn, project_id)
    if current and current != token:
        raise ProjectDeleteInProgressError(project_id)


def assert_task_not_deleted(
    conn: sqlite3.Connection,
    task_id: str,
    milestone_id: str,
) -> None:
    row = conn.execute(
        "SELECT project_id FROM project_deleted_tasks WHERE task_id=? "
        "UNION ALL "
        "SELECT project_id FROM project_deleted_milestones WHERE milestone_id=? LIMIT 1",
        (task_id, milestone_id),
    ).fetchone()
    if row is not None:
        raise ProjectDeletedError(str(row[0]))


def assert_milestone_not_deleted(conn: sqlite3.Connection, milestone_id: str) -> None:
    row = conn.execute(
        "SELECT project_id FROM project_deleted_milestones WHERE milestone_id=?",
        (milestone_id,),
    ).fetchone()
    if row is not None:
        raise ProjectDeletedError(str(row[0]))


def assert_thread_not_deleting(conn: sqlite3.Connection, thread_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM project_thread_delete_claims WHERE thread_id=? "
        "UNION ALL SELECT 1 FROM project_thread_delete_tombstones WHERE thread_id=? LIMIT 1",
        (thread_id, thread_id),
    ).fetchone():
        raise ProjectThreadDeletingError(thread_id)


def _thread_delete_scope_allowed(
    stored_tenant_id: object,
    stored_owner_id: object,
    *,
    tenant_id: str,
    owner_id: str,
) -> bool:
    stored_tenant = str(stored_tenant_id or "").strip()
    stored_owner = str(stored_owner_id or "").strip()
    if not tenant_id and not owner_id:
        return True
    return stored_tenant == tenant_id and stored_owner == owner_id


def thread_delete_lease(
    store: _DeletionStore,
    thread_id: str,
    *,
    tenant_id: str = "",
    owner_id: str = "",
) -> ProjectThreadDeleteLease | None:
    """Read an authorized durable thread-deletion claim or tombstone."""

    thread_id = _require_id(thread_id, label="thread_id")
    tenant_id = str(tenant_id or "").strip()
    owner_id = str(owner_id or "").strip()
    with store._lock, store._conn() as conn:
        tombstone = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_thread_delete_tombstones "
            "WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if tombstone is not None:
            if not _thread_delete_scope_allowed(
                tombstone[1],
                tombstone[2],
                tenant_id=tenant_id,
                owner_id=owner_id,
            ):
                raise PermissionError("thread deletion belongs to another principal")
            return ProjectThreadDeleteLease(
                thread_id=thread_id,
                token=str(tombstone[0]),
                resumed=True,
                finalized=True,
                tenant_id=str(tombstone[1] or ""),
                owner_id=str(tombstone[2] or ""),
            )
        claim = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_thread_delete_claims WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if claim is None:
            return None
        if not _thread_delete_scope_allowed(
            claim[1],
            claim[2],
            tenant_id=tenant_id,
            owner_id=owner_id,
        ):
            raise PermissionError("thread deletion belongs to another principal")
        return ProjectThreadDeleteLease(
            thread_id=thread_id,
            token=str(claim[0]),
            resumed=True,
            finalized=False,
            tenant_id=str(claim[1] or ""),
            owner_id=str(claim[2] or ""),
        )


def begin_thread_delete(
    store: _DeletionStore,
    thread_id: str,
    *,
    tenant_id: str = "",
    owner_id: str = "",
) -> ProjectThreadDeleteLease:
    """Reserve an unbound thread against every concurrent project bind."""

    thread_id = _require_id(thread_id, label="thread_id")
    tenant_id = str(tenant_id or "").strip()
    owner_id = str(owner_id or "").strip()
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        tombstone = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_thread_delete_tombstones "
            "WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if tombstone is not None:
            if not _thread_delete_scope_allowed(
                tombstone[1],
                tombstone[2],
                tenant_id=tenant_id,
                owner_id=owner_id,
            ):
                raise PermissionError("thread deletion belongs to another principal")
            return ProjectThreadDeleteLease(
                thread_id=thread_id,
                token=str(tombstone[0]),
                resumed=True,
                finalized=True,
                tenant_id=str(tombstone[1] or ""),
                owner_id=str(tombstone[2] or ""),
            )
        claim = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_thread_delete_claims WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if claim is not None:
            if not _thread_delete_scope_allowed(
                claim[1],
                claim[2],
                tenant_id=tenant_id,
                owner_id=owner_id,
            ):
                raise PermissionError("thread deletion belongs to another principal")
            return ProjectThreadDeleteLease(
                thread_id=thread_id,
                token=str(claim[0]),
                resumed=True,
                finalized=False,
                tenant_id=str(claim[1] or ""),
                owner_id=str(claim[2] or ""),
            )
        binding = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if binding is not None:
            project_id = _require_id(binding[0], label="project_id")
            project = store._project_doc_for_scope(conn, project_id, None)
            if project is None:
                project = Project(id=project_id, name="Bound project", goal="")
            raise ProjectThreadBoundError(thread_id, project)
        token = f"TD-{uuid4().hex}"
        conn.execute(
            "INSERT INTO project_thread_delete_claims"
            "(thread_id, token, created_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
            (thread_id, token, time.time(), tenant_id, owner_id),
        )
        return ProjectThreadDeleteLease(
            thread_id=thread_id,
            token=token,
            resumed=False,
            finalized=False,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )


def finalize_thread_delete(store: _DeletionStore, thread_id: str, token: str) -> bool:
    """Convert the exact thread reservation into a permanent binding tombstone."""

    thread_id = _require_id(thread_id, label="thread_id")
    token = _require_id(token, label="thread_delete_token")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        tombstone = conn.execute(
            "SELECT token FROM project_thread_delete_tombstones WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if tombstone is not None:
            if str(tombstone[0]) != token:
                raise ProjectThreadDeletingError(thread_id)
            return True
        claim = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_thread_delete_claims WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if claim is None or str(claim[0]) != token:
            raise ProjectThreadDeletingError(thread_id)
        deleted = conn.execute(
            "DELETE FROM project_thread_delete_claims WHERE thread_id=? AND token=?",
            (thread_id, token),
        )
        if deleted.rowcount != 1:
            raise ProjectThreadDeletingError(thread_id)
        conn.execute(
            "INSERT INTO project_thread_delete_tombstones"
            "(thread_id, token, deleted_at, tenant_id, owner_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, token, time.time(), str(claim[1] or ""), str(claim[2] or "")),
        )
        return True


def cancel_thread_delete_preflight(
    store: _DeletionStore,
    thread_id: str,
    token: str,
) -> bool:
    """CAS-cancel a preflight-only claim before any other delete fence exists."""

    thread_id = _require_id(thread_id, label="thread_id")
    token = _require_id(token, label="thread_delete_token")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM project_thread_delete_tombstones WHERE thread_id=?",
            (thread_id,),
        ).fetchone():
            return False
        deleted = conn.execute(
            "DELETE FROM project_thread_delete_claims WHERE thread_id=? AND token=?",
            (thread_id, token),
        )
        return deleted.rowcount == 1


def consume_project_delete_claim(
    conn: sqlite3.Connection,
    project_id: str,
    token: str,
) -> None:
    safe_token = _require_id(token, label="project_delete_token")
    deleted = conn.execute(
        "DELETE FROM project_delete_claims WHERE project_id=? AND token=?",
        (project_id, safe_token),
    )
    if deleted.rowcount != 1:
        raise ProjectDeleteInProgressError(project_id)


def require_project_delete_claim(
    conn: sqlite3.Connection,
    project_id: str,
    token: str,
) -> None:
    safe_token = _require_id(token, label="project_delete_token")
    if _delete_token(conn, project_id) != safe_token:
        raise ProjectDeleteInProgressError(project_id)


def _pending_threads(conn: sqlite3.Connection, project_id: str, event_kind: str) -> set[str]:
    threads = {
        _require_id(row[0], label="thread_id")
        for row in conn.execute(
            "SELECT thread_id FROM thread_projects WHERE project_id=?",
            (project_id,),
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT payload FROM project_events WHERE project_id=? AND kind=?",
        (project_id, event_kind),
    ).fetchall()
    for (raw_payload,) in rows:
        try:
            payload = _json_dict(json.loads(str(raw_payload)), label="event payload")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        raw_threads = payload.get("thread_ids")
        candidates = raw_threads if isinstance(raw_threads, list) else []
        legacy = str(payload.get("thread_id") or "").strip()
        if legacy:
            candidates = [*candidates, legacy]
        for raw_thread in candidates:
            try:
                threads.add(_require_id(raw_thread, label="thread_id"))
            except ValueError:
                continue
    return threads


def _deleted_entity_ids(
    conn: sqlite3.Connection,
    project_id: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    milestones = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM milestones WHERE project_id=? ORDER BY id",
            (project_id,),
        ).fetchall()
    )
    tasks = tuple(
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT t.id, t.milestone_id FROM tasks t "
            "INNER JOIN milestones m ON m.id=t.milestone_id "
            "WHERE m.project_id=? ORDER BY t.id",
            (project_id,),
        ).fetchall()
    )
    return milestones, tasks


def record_project_delete_tombstones(
    conn: sqlite3.Connection,
    project: Project,
    token: str,
    milestone_ids: tuple[str, ...],
    tasks: tuple[tuple[str, str], ...],
) -> None:
    project_id = project.id
    conn.execute(
        "INSERT INTO project_delete_tombstones("
        "project_id, token, deleted_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
        (project_id, token, time.time(), project.tenant_id, project.owner_id),
    )
    conn.executemany(
        "INSERT INTO project_deleted_milestones(milestone_id, project_id) VALUES (?, ?)",
        ((milestone_id, project_id) for milestone_id in milestone_ids),
    )
    conn.executemany(
        "INSERT INTO project_deleted_tasks(task_id, milestone_id, project_id) VALUES (?, ?, ?)",
        ((task_id, milestone_id, project_id) for task_id, milestone_id in tasks),
    )


def begin_project_delete(
    store: _DeletionStore,
    project_id: str,
    *,
    event_kind: str,
    scope: TenantScope | None = None,
) -> ProjectDeleteLease:
    """Persist the cleanup outbox and source-write fence in one transaction."""

    from runtime.projectos._store_thread_bindings import _assert_project_deletable

    project_id = _require_id(project_id, label="project_id")
    event_kind = _require_kind(event_kind)
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_token = _delete_token(conn, project_id)
        assert_project_not_deleting(conn, project_id, token=existing_token)
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        if existing_token:
            return ProjectDeleteLease(
                project=project,
                token=existing_token,
                thread_ids=tuple(sorted(_pending_threads(conn, project_id, event_kind))),
                resumed=True,
            )
        _assert_project_deletable(conn, project)
        threads = tuple(sorted(_pending_threads(conn, project_id, event_kind)))
        conn.execute(
            "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"EV-{uuid4().hex[:12]}",
                project_id,
                event_kind,
                json.dumps({"thread_ids": list(threads)}, ensure_ascii=False),
                time.time(),
            ),
        )
        token = f"PD-{uuid4().hex}"
        conn.execute(
            "INSERT INTO project_delete_claims(project_id, token, created_at) VALUES (?, ?, ?)",
            (project_id, token, time.time()),
        )
        return ProjectDeleteLease(
            project=project,
            token=token,
            thread_ids=threads,
            resumed=False,
        )


def finalize_project_delete(
    store: _DeletionStore,
    project_id: str,
    token: str,
    *,
    scope: TenantScope | None = None,
) -> bool:
    """Delete source rows only while the exact durable delete lease wins."""

    from runtime.projectos._store_thread_bindings import (
        _assert_project_deletable,
        _delete_project_rows,
    )

    project_id = _require_id(project_id, label="project_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            return False
        assert_project_not_deleting(conn, project_id, token=token)
        if not _delete_token(conn, project_id):
            raise ProjectDeleteInProgressError(project_id)
        _assert_project_deletable(conn, project)
        if conn.execute(
            "SELECT 1 FROM thread_projects WHERE project_id=? LIMIT 1",
            (project_id,),
        ).fetchone():
            return False
        milestone_ids, tasks = _deleted_entity_ids(conn, project_id)
        consume_project_delete_claim(conn, project_id, token)
        _delete_project_rows(conn, project_id)
        record_project_delete_tombstones(conn, project, token, milestone_ids, tasks)
        return True


def project_delete_tombstone_token(
    store: _DeletionStore,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> str:
    """Return an authorized finalized token for idempotent external cleanup."""

    project_id = _require_id(project_id, label="project_id")
    with store._lock, store._conn() as conn:
        row = conn.execute(
            "SELECT token, tenant_id, owner_id FROM project_delete_tombstones WHERE project_id=?",
            (project_id,),
        ).fetchone()
    if row is None:
        return ""
    if (
        scope is not None
        and not scope.allow_cross_tenant
        and (str(row[1] or "") != scope.tenant_id or str(row[2] or "") != scope.actor_id)
    ):
        return ""
    return str(row[0])


class ProjectDeletionStoreMixin:
    _scope: TenantScope | None

    def begin_project_delete(
        self,
        project_id: str,
        *,
        event_kind: str,
        scope: TenantScope | None = None,
    ) -> ProjectDeleteLease:
        return begin_project_delete(
            cast(_DeletionStore, self),
            project_id,
            event_kind=event_kind,
            scope=scope,
        )

    def finalize_project_delete(
        self,
        project_id: str,
        token: str,
        *,
        scope: TenantScope | None = None,
    ) -> bool:
        return finalize_project_delete(
            cast(_DeletionStore, self),
            project_id,
            token,
            scope=scope,
        )

    def project_delete_tombstone_token(
        self,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> str:
        return project_delete_tombstone_token(
            cast(_DeletionStore, self),
            project_id,
            scope=self._scope if scope is None else scope,
        )

    def thread_delete_lease(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> ProjectThreadDeleteLease | None:
        return thread_delete_lease(
            cast(_DeletionStore, self),
            thread_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    def begin_thread_delete(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> ProjectThreadDeleteLease:
        return begin_thread_delete(
            cast(_DeletionStore, self),
            thread_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    def finalize_thread_delete(self, thread_id: str, token: str) -> bool:
        return finalize_thread_delete(cast(_DeletionStore, self), thread_id, token)

    def cancel_thread_delete_preflight(self, thread_id: str, token: str) -> bool:
        return cancel_thread_delete_preflight(cast(_DeletionStore, self), thread_id, token)


__all__ = [
    "ProjectDeleteInProgressError",
    "ProjectDeletedError",
    "ProjectDeleteLease",
    "ProjectDeletionStoreMixin",
    "ProjectThreadBoundError",
    "ProjectThreadDeleteLease",
    "ProjectThreadDeletingError",
    "assert_project_not_deleting",
    "assert_milestone_not_deleted",
    "assert_task_not_deleted",
    "assert_thread_not_deleting",
    "cancel_thread_delete_preflight",
    "ensure_project_delete_schema",
    "record_project_delete_tombstones",
    "project_delete_tombstone_token",
    "require_project_delete_claim",
    "thread_delete_lease",
]
