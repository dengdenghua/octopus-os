"""Persistent SQLite/JSON store for Project OS state.
Project metadata enforces HTTP isolation while legacy documents stay unowned.
All engine writes pass through here and reads return typed dataclasses.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.projectos._store_helpers import (
    _MAX_NAME_LENGTH,
    _available_milestone_id,
    _json_dict,
    _milestone_from_doc,
    _milestone_has_unfinished_tasks,
    _normalize_milestone,
    _normalize_project,
    _normalize_task,
    _optional_id,
    _project_from_doc,
    _require_id,
    _require_kind,
    _task_from_doc,
    _text,
)
from runtime.projectos._store_message_actions import ProjectMessageActionStoreMixin
from runtime.projectos._store_project_deletion import (
    ProjectDeletedError as _ProjectDeletedError,
)
from runtime.projectos._store_project_deletion import (
    ProjectDeleteInProgressError as _ProjectDeleteInProgressError,
)
from runtime.projectos._store_project_deletion import (
    ProjectDeletionStoreMixin,
    assert_project_not_deleting,
    assert_task_not_deleted,
    ensure_project_delete_schema,
)
from runtime.projectos._store_project_deletion import (
    ProjectThreadBoundError as _ProjectThreadBoundError,
)
from runtime.projectos._store_project_deletion import (
    ProjectThreadDeletingError as _ProjectThreadDeletingError,
)
from runtime.projectos._store_task_claims import ProjectClaimStoreMixin
from runtime.projectos._store_thread_bindings import (
    ProjectAlreadyBoundError as _ProjectAlreadyBoundError,
)
from runtime.projectos._store_thread_bindings import (
    ProjectBindingActiveError as _ProjectBindingActiveError,
)
from runtime.projectos._store_thread_bindings import (
    ProjectBindingChangedError as _ProjectBindingChangedError,
)
from runtime.projectos._store_thread_bindings import (
    ProjectBindingMigrationRequiredError as _ProjectBindingMigrationRequiredError,
)
from runtime.projectos._store_thread_bindings import (
    ProjectBindingStoreMixin,
    _assert_binding_matches,
    ensure_single_project_bindings,
)
from runtime.projectos._store_thread_bindings import (
    ProjectClaimActiveError as _ProjectClaimActiveError,
)
from runtime.projectos._store_thread_bindings import (
    delete_project as _delete_project,
)
from runtime.projectos.model import Milestone, Project, Task
from runtime.safety.auth.scope import TenantScope

_TERMINAL_PROJECT_STATUSES = frozenset({"blocked", "done", "failed"})
_TERMINAL_MILESTONE_STATUSES = frozenset({"blocked", "done", "failed"})
_TERMINAL_TASK_STATUSES = frozenset({"blocked", "done", "failed", "rejected"})
ProjectBindingActiveError = _ProjectBindingActiveError
ProjectAlreadyBoundError = _ProjectAlreadyBoundError
ProjectBindingChangedError = _ProjectBindingChangedError
ProjectBindingMigrationRequiredError = _ProjectBindingMigrationRequiredError
ProjectClaimActiveError = _ProjectClaimActiveError
ProjectDeleteInProgressError = _ProjectDeleteInProgressError
ProjectDeletedError = _ProjectDeletedError
ProjectThreadBoundError = _ProjectThreadBoundError
ProjectThreadDeletingError = _ProjectThreadDeletingError
_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, milestone_id TEXT NOT NULL, doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_claims (
    task_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, claimed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS milestone_claims (
    milestone_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, claimed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_projects (
    thread_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_project_generations (
    thread_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS project_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ms_project ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_task_ms ON tasks(milestone_id);
CREATE INDEX IF NOT EXISTS idx_project_events_project
    ON project_events(project_id, created_at);
"""


def _default_dir() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "projectos"


class ProjectStore(
    ProjectBindingStoreMixin,
    ProjectClaimStoreMixin,
    ProjectMessageActionStoreMixin,
    ProjectDeletionStoreMixin,
):
    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        d = Path(base_dir) if base_dir else _default_dir()
        d.mkdir(parents=True, exist_ok=True)
        self._db = d / "projectos.db"
        self._lock = threading.Lock()
        self._scope = scope
        with self._lock, sqlite3.connect(str(self._db)) as conn:
            conn.executescript(_SCHEMA)
            ensure_project_delete_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO thread_project_generations(thread_id, generation) "
                "SELECT thread_id, 0 FROM thread_projects"
            )
            ensure_single_project_bindings(conn)

    def with_scope(self, scope: TenantScope | None) -> ProjectStore:
        """Return a scoped view sharing this store's DB and write lock."""
        view = object.__new__(ProjectStore)
        view._db = self._db
        view._lock = self._lock
        view._scope = scope
        return view

    def _effective_scope(self, scope: TenantScope | None) -> TenantScope | None:
        return self._scope if scope is None else scope

    @staticmethod
    def _scope_project_allowed(project: Project, scope: TenantScope | None) -> bool:
        if scope is None or scope.allow_cross_tenant:
            return True
        return bool(
            project.tenant_id
            and project.owner_id
            and project.tenant_id == scope.tenant_id
            and project.owner_id == scope.actor_id
        )

    def _prepare_project(self, project: Project, scope: TenantScope | None) -> Project:
        effective = self._effective_scope(scope)
        if effective is None or effective.allow_cross_tenant:
            return project
        if project.tenant_id and project.tenant_id != effective.tenant_id:
            raise PermissionError("project belongs to another tenant")
        if project.owner_id and project.owner_id != effective.actor_id:
            raise PermissionError("project belongs to another actor")
        project.tenant_id = effective.tenant_id
        project.owner_id = effective.actor_id
        return project

    def _project_doc_for_scope(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        scope: TenantScope | None,
    ) -> Project | None:
        row = conn.execute("SELECT doc FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        project = _project_from_doc(row[0])
        if project is None:
            return None
        return (
            project if self._scope_project_allowed(project, self._effective_scope(scope)) else None
        )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db), timeout=10.0)

    # ── projects ─────────────────────────────────────────────────────────────
    def save_project(
        self,
        project: Project,
        *,
        allow_terminal_rewrite: bool = False,
        scope: TenantScope | None = None,
    ) -> Project:
        """Persist ``project``.

        Terminal project rows are immutable by default so a stale tick cannot
        downgrade a completed project back to ``running``/``blocked``. Operator
        recovery paths must pass ``allow_terminal_rewrite=True`` explicitly.
        """
        project = self._prepare_project(_normalize_project(project), scope)
        effective = self._effective_scope(scope)
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute(
                "SELECT doc FROM projects WHERE id=?",
                (project.id,),
            ).fetchone()
            assert_project_not_deleting(conn, project.id)
            if existing_row and not allow_terminal_rewrite:
                existing = _project_from_doc(existing_row[0])
                if existing is None:
                    raise ValueError(f"corrupt existing project row: {project.id}")
                if not self._scope_project_allowed(existing, effective):
                    raise PermissionError("project belongs to another tenant")
                if existing.status in _TERMINAL_PROJECT_STATUSES:
                    return existing
            elif existing_row:
                existing = _project_from_doc(existing_row[0])
                if existing is None:
                    raise ValueError(f"corrupt existing project row: {project.id}")
                if not self._scope_project_allowed(existing, effective):
                    raise PermissionError("project belongs to another tenant")
            if existing_row:
                assert existing is not None
                project.owner_id = existing.owner_id or project.owner_id
                project.tenant_id = existing.tenant_id or project.tenant_id
                project.created_at = existing.created_at or project.created_at
                project.started_at = existing.started_at or project.started_at
                project.finished_at = existing.finished_at or project.finished_at
                project.execution_thread_id = existing.execution_thread_id
                project = self._prepare_project(_normalize_project(project), scope)
            conn.execute(
                "INSERT INTO projects(id, doc) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
                (project.id, json.dumps(project.to_dict(), ensure_ascii=False)),
            )
        return project

    def create_project_plan(
        self,
        project: Project,
        milestones: list[Milestone],
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Project, list[Milestone]]:
        """Atomically persist a new project, its milestones, and plan event.

        Planner milestone ids are project-local in practice but global keys in
        the legacy schema. The first unused id is preserved for compatibility;
        collisions are namespaced and every dependency is rewritten to the
        resolved id before the transaction starts writing.
        """

        candidate_project = self._prepare_project(_normalize_project(project), scope)
        candidates = [_normalize_milestone(milestone) for milestone in milestones]
        if not candidates:
            raise ValueError("project plan requires at least one milestone")
        event_id = f"EV-{uuid4().hex[:12]}"
        created_at = time.time()

        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM projects WHERE id=?",
                (candidate_project.id,),
            ).fetchone():
                raise ValueError("project already exists")
            assert_project_not_deleting(conn, candidate_project.id)

            used_ids = {
                str(row[0])
                for row in conn.execute(
                    "SELECT id FROM milestones "
                    "UNION SELECT milestone_id FROM project_deleted_milestones"
                ).fetchall()
            }
            resolved_ids: list[str] = []
            id_map: dict[str, str] = {}
            for milestone in candidates:
                resolved_id = _available_milestone_id(
                    candidate_project.id,
                    milestone.id,
                    used_ids=used_ids,
                )
                used_ids.add(resolved_id)
                resolved_ids.append(resolved_id)
                # Duplicate planner ids are ambiguous; dependencies retain the
                # first occurrence, matching the legacy planner interpretation.
                id_map.setdefault(milestone.id, resolved_id)

            resolved: list[Milestone] = []
            for milestone, resolved_id in zip(candidates, resolved_ids, strict=True):
                raw = milestone.to_dict()
                raw["id"] = resolved_id
                raw["dependencies"] = [
                    id_map.get(dependency, dependency) for dependency in milestone.dependencies
                ]
                resolved.append(_normalize_milestone(Milestone.from_dict(raw)))

            candidate_project.milestone_ids = resolved_ids
            if candidate_project.current_ms:
                candidate_project.current_ms = id_map.get(
                    candidate_project.current_ms,
                    candidate_project.current_ms,
                )
            candidate_project = self._prepare_project(
                _normalize_project(candidate_project),
                scope,
            )
            event_payload = _json_dict(
                {
                    "name": candidate_project.name,
                    "goal": candidate_project.goal,
                    "milestone_ids": resolved_ids,
                    "milestone_count": len(resolved),
                },
                label="event payload",
            )

            conn.execute(
                "INSERT INTO projects(id, doc) VALUES (?, ?)",
                (
                    candidate_project.id,
                    json.dumps(candidate_project.to_dict(), ensure_ascii=False),
                ),
            )
            for milestone in resolved:
                conn.execute(
                    "INSERT INTO milestones(id, project_id, doc) VALUES (?, ?, ?)",
                    (
                        milestone.id,
                        candidate_project.id,
                        json.dumps(milestone.to_dict(), ensure_ascii=False),
                    ),
                )
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, 'project.planned', ?, ?)",
                (
                    event_id,
                    candidate_project.id,
                    json.dumps(event_payload, ensure_ascii=False),
                    created_at,
                ),
            )
        return candidate_project, resolved

    def get_project(self, project_id: str, *, scope: TenantScope | None = None) -> Project | None:
        project_id = _require_id(project_id, label="project_id")
        with self._lock, self._conn() as conn:
            return self._project_doc_for_scope(conn, project_id, scope)

    def list_projects(self, *, scope: TenantScope | None = None) -> list[Project]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT doc FROM projects ORDER BY id").fetchall()
        projects: list[Project] = []
        for row in rows:
            project = _project_from_doc(row[0])
            if project is not None and self._scope_project_allowed(
                project, self._effective_scope(scope)
            ):
                projects.append(project)
        return projects

    def delete_project(self, project_id: str, *, scope: TenantScope | None = None) -> bool:
        return _delete_project(self, project_id, scope=scope)

    def delete_project_if_unbound(
        self, project_id: str, *, scope: TenantScope | None = None
    ) -> bool:
        return _delete_project(self, project_id, require_unbound=True, scope=scope)

    # ── audit events ────────────────────────────────────────────────────────
    def append_event(
        self,
        project_id: str,
        *,
        kind: str,
        payload: dict,
        event_id: str | None = None,
        created_at: float | None = None,
        expected_thread_id: str | None = None,
        expected_binding_generation: int | None = None,
        scope: TenantScope | None = None,
    ) -> dict:
        project = _require_id(project_id, label="project_id")
        event_kind = _require_kind(kind)
        event_payload = _json_dict(payload, label="event payload")
        safe_event_id = (
            _require_id(event_id, label="event_id")
            if event_id is not None
            else f"EV-{uuid4().hex[:12]}"
        )
        event = {
            "id": safe_event_id,
            "project_id": project,
            "kind": event_kind,
            "payload": event_payload,
            "created_at": float(created_at if created_at is not None else time.time()),
        }
        expected_thread = (
            _require_id(expected_thread_id, label="thread_id")
            if expected_thread_id is not None
            else None
        )
        if (expected_thread is None) != (expected_binding_generation is None):
            raise ValueError("expected thread and binding generation must be provided together")
        if expected_binding_generation is not None and expected_binding_generation < 0:
            raise ValueError("expected binding generation must be non-negative")
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert_project_not_deleting(conn, project)
            if expected_thread is not None and expected_binding_generation is not None:
                _assert_binding_matches(
                    conn,
                    thread_id=expected_thread,
                    project_id=project,
                    generation=expected_binding_generation,
                )
            if self._project_doc_for_scope(conn, project, scope) is None:
                raise PermissionError("project belongs to another tenant or does not exist")
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["project_id"],
                    event["kind"],
                    json.dumps(event["payload"], ensure_ascii=False),
                    event["created_at"],
                ),
            )
        return event

    def events_for_project(
        self,
        project_id: str,
        *,
        limit: int = 100,
        scope: TenantScope | None = None,
    ) -> list[dict]:
        project = _require_id(project_id, label="project_id")
        bounded_limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._conn() as conn:
            if (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, project, scope) is None
            ):
                return []
            rows = conn.execute(
                "SELECT id, project_id, kind, payload, created_at "
                "FROM project_events WHERE project_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project, bounded_limit),
            ).fetchall()
        events = []
        for row in rows:
            try:
                events.append(
                    {
                        "id": _require_id(row[0], label="event_id"),
                        "project_id": _require_id(row[1], label="project_id"),
                        "kind": _require_kind(row[2]),
                        "payload": _json_dict(json.loads(row[3]), label="event payload"),
                        "created_at": float(row[4]),
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        events.reverse()
        return events

    def artifacts_for_project(
        self,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> list[dict[str, Any]]:
        """Build the durable artifact read model from published events.

        Artifact events are the source of truth, so this projection survives
        process restarts without introducing a second writable artifact store.
        Newest events win when an artifact id is published more than once.
        """

        project = _require_id(project_id, label="project_id")
        with self._lock, self._conn() as conn:
            if (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, project, scope) is None
            ):
                return []
            rows = conn.execute(
                "SELECT id, payload FROM project_events "
                "WHERE project_id=? AND kind='project.artifact_published' "
                "ORDER BY created_at DESC, id DESC",
                (project,),
            ).fetchall()

        artifacts: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for event_id, raw_payload in rows:
            try:
                payload = _json_dict(json.loads(raw_payload), label="event payload")
                raw_artifact = payload.get("artifact")
                if not isinstance(raw_artifact, dict):
                    continue
                artifact_id = _optional_id(raw_artifact.get("id"), label="artifact id")
                if artifact_id is None:
                    # Older hand-authored events did not always include an
                    # artifact id.  The immutable event id is a stable fallback.
                    artifact_id = _require_id(event_id, label="event_id")
                if artifact_id in seen_ids:
                    continue
                name = _text(
                    raw_artifact.get("name")
                    or raw_artifact.get("title")
                    or raw_artifact.get("path")
                    or raw_artifact.get("url")
                    or artifact_id,
                    label="artifact name",
                    max_length=_MAX_NAME_LENGTH,
                    default=artifact_id,
                )
                artifact: dict[str, Any] = {"id": artifact_id, "name": name}
                for key, max_length in (
                    ("kind", 128),
                    ("path", 4096),
                    ("url", 4096),
                    ("summary", 4096),
                    ("task_id", 240),
                    ("milestone_id", 240),
                ):
                    value = raw_artifact.get(key)
                    if value in (None, "") or isinstance(value, (dict, list)):
                        continue
                    normalized = _text(
                        value,
                        label=f"artifact {key}",
                        max_length=max_length,
                    )
                    if normalized:
                        artifact[key] = normalized
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            seen_ids.add(artifact_id)
            artifacts.append(artifact)
        return artifacts

    def decisions_for_project(
        self,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> list[dict[str, Any]]:
        """Build the durable project-decision read model from audit events.

        Existing message actions store ``decision`` as a string. Structured
        payloads are also accepted for forward compatibility. Newest events
        win when multiple events identify the same logical decision.
        """

        project = _require_id(project_id, label="project_id")
        with self._lock, self._conn() as conn:
            if (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, project, scope) is None
            ):
                return []
            rows = conn.execute(
                "SELECT id, payload, created_at FROM project_events "
                "WHERE project_id=? AND kind='project.decision_recorded' "
                "ORDER BY created_at DESC, id DESC",
                (project,),
            ).fetchall()

        decisions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for event_id, raw_payload, raw_created_at in rows:
            try:
                safe_event_id = _require_id(event_id, label="event_id")
                payload = _json_dict(json.loads(raw_payload), label="event payload")
                raw_decision = payload.get("decision")
                detail = raw_decision if isinstance(raw_decision, dict) else {}
                try:
                    decision_id = _optional_id(
                        detail.get("id") or payload.get("decision_id"),
                        label="decision id",
                    )
                except ValueError:
                    decision_id = None
                decision_id = decision_id or safe_event_id
                if decision_id in seen_ids:
                    continue

                decision_text = _text(
                    detail.get("decision")
                    or detail.get("text")
                    or detail.get("value")
                    or (raw_decision if not isinstance(raw_decision, dict) else ""),
                    label="decision",
                )
                if not decision_text:
                    continue
                title = _text(
                    detail.get("title") or payload.get("title") or decision_text.splitlines()[0],
                    label="decision title",
                )[:_MAX_NAME_LENGTH]
                summary = _text(
                    detail.get("summary")
                    or payload.get("summary")
                    or payload.get("rationale")
                    or decision_text,
                    label="decision summary",
                )[:4096]
                actor = _text(
                    detail.get("actor") or payload.get("actor"),
                    label="decision actor",
                    max_length=256,
                )
                created_at = datetime.fromtimestamp(float(raw_created_at), UTC).isoformat()
                decision: dict[str, Any] = {
                    "id": decision_id,
                    "title": title,
                    "summary": summary,
                    "decision": decision_text,
                    "actor": actor,
                    "created_at": created_at,
                }
                raw_source = detail.get("source_message") or payload.get("source_message")
                source = raw_source if isinstance(raw_source, dict) else {}
                source_message_id = _text(
                    source.get("source_message_id"),
                    label="source_message_id",
                    max_length=240,
                )
                if source_message_id:
                    decision["source_message_id"] = source_message_id
                try:
                    milestone_id = _optional_id(
                        detail.get("milestone_id") or payload.get("milestone_id"),
                        label="milestone_id",
                    )
                except ValueError:
                    milestone_id = None
                if milestone_id:
                    decision["milestone_id"] = milestone_id
            except (OSError, OverflowError, TypeError, ValueError, json.JSONDecodeError):
                continue
            seen_ids.add(decision_id)
            decisions.append(decision)
        return decisions

    def get_event(
        self,
        event_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> dict | None:
        """Fetch one audit event by id for idempotent external actions."""

        safe_event_id = _require_id(event_id, label="event_id")
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id, project_id, kind, payload, created_at FROM project_events WHERE id=?",
                (safe_event_id,),
            ).fetchone()
            if not row or (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, str(row[1]), scope) is None
            ):
                return None
        try:
            return {
                "id": _require_id(row[0], label="event_id"),
                "project_id": _require_id(row[1], label="project_id"),
                "kind": _require_kind(row[2]),
                "payload": _json_dict(json.loads(row[3]), label="event payload"),
                "created_at": float(row[4]),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    # ── milestones ───────────────────────────────────────────────────────────
    def save_milestone(
        self,
        project_id: str,
        ms: Milestone,
        *,
        allow_terminal_rewrite: bool = False,
        scope: TenantScope | None = None,
    ) -> Milestone:
        """Persist ``ms``.

        Terminal milestone rows are immutable by default for the same reason as
        terminal tasks/projects: a stale engine tick must not reopen or block a
        milestone that another tick has already completed.
        """
        project_id = _require_id(project_id, label="project_id")
        ms = _normalize_milestone(ms)
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert_project_not_deleting(conn, project_id)
            if self._project_doc_for_scope(conn, project_id, scope) is None:
                raise PermissionError("project belongs to another tenant or does not exist")
            existing_row = conn.execute(
                "SELECT doc, project_id FROM milestones WHERE id=?",
                (ms.id,),
            ).fetchone()
            if existing_row:
                existing = _milestone_from_doc(existing_row[0])
                if existing is None:
                    raise ValueError(f"corrupt existing milestone row: {ms.id}")
                if str(existing_row[1]) != project_id:
                    raise ValueError("milestone is already attached to another project")
                active_claim = conn.execute(
                    "SELECT 1 FROM milestone_claims WHERE milestone_id=?",
                    (ms.id,),
                ).fetchone()
                if active_claim is not None:
                    if allow_terminal_rewrite:
                        project = self._project_doc_for_scope(conn, project_id, scope)
                        if project is None:
                            raise RuntimeError("claimed milestone project is unavailable")
                        raise _ProjectClaimActiveError(project, milestone_ids=(ms.id,))
                    return existing
                if not allow_terminal_rewrite and (
                    existing.status in _TERMINAL_MILESTONE_STATUSES
                    or (ms.status == "done" and _milestone_has_unfinished_tasks(conn, ms.id))
                ):
                    return existing
                # Task membership is append-only through this generic writer.
                # An engine tick may hold an older milestone snapshot while a
                # message action atomically adds a task; never let that stale
                # save orphan the durable task row by shrinking ``task_ids``.
                ms.task_ids = [
                    *existing.task_ids,
                    *(task_id for task_id in ms.task_ids if task_id not in existing.task_ids),
                ]
            if existing_row and str(existing_row[1]) != project_id:
                raise ValueError("milestone is already attached to another project")
            conn.execute(
                "INSERT INTO milestones(id, project_id, doc) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, project_id=excluded.project_id",
                (ms.id, project_id, json.dumps(ms.to_dict(), ensure_ascii=False)),
            )
        return ms

    def get_milestone(self, ms_id: str, *, scope: TenantScope | None = None) -> Milestone | None:
        ms_id = _require_id(ms_id, label="milestone_id")
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT doc, project_id FROM milestones WHERE id=?", (ms_id,)
            ).fetchone()
            if not row or (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, str(row[1]), scope) is None
            ):
                return None
        return _milestone_from_doc(row[0])

    def milestones_for(
        self, project_id: str, *, scope: TenantScope | None = None
    ) -> list[Milestone]:
        project_id = _require_id(project_id, label="project_id")
        with self._lock, self._conn() as conn:
            if (
                self._effective_scope(scope) is not None
                and self._project_doc_for_scope(conn, project_id, scope) is None
            ):
                return []
            rows = conn.execute(
                "SELECT doc FROM milestones WHERE project_id=?", (project_id,)
            ).fetchall()
        milestones: list[Milestone] = []
        for row in rows:
            milestone = _milestone_from_doc(row[0])
            if milestone is not None:
                milestones.append(milestone)
        return milestones

    # ── tasks ────────────────────────────────────────────────────────────────
    def save_task(
        self,
        task: Task,
        *,
        allow_terminal_rewrite: bool = False,
        scope: TenantScope | None = None,
    ) -> Task:
        """Persist ``task``.

        Terminal task rows are immutable by default so a stale worker callback
        cannot downgrade ``done`` to ``failed`` or replace a failed task's
        diagnostic output. Recovery/operator actions that intentionally reopen
        work must pass ``allow_terminal_rewrite=True``.
        """
        task = _normalize_task(task)
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT project_id FROM milestones WHERE id=?", (task.milestone_id,)
            ).fetchone()
            if not parent:
                assert_task_not_deleted(conn, task.id, task.milestone_id)
                raise PermissionError("task milestone does not exist")
            assert_project_not_deleting(conn, str(parent[0]))
            if self._project_doc_for_scope(conn, str(parent[0]), scope) is None:
                raise PermissionError("task belongs to another tenant or does not exist")
            existing_row = conn.execute(
                "SELECT doc, milestone_id FROM tasks WHERE id=?",
                (task.id,),
            ).fetchone()
            if existing_row:
                existing = _task_from_doc(existing_row[0])
                if existing is None:
                    raise ValueError(f"corrupt existing task row: {task.id}")
                active_claim = conn.execute(
                    "SELECT 1 FROM task_claims WHERE task_id=?",
                    (task.id,),
                ).fetchone()
                if active_claim is not None:
                    project = (
                        self._project_doc_for_scope(conn, str(parent[0]), scope) if parent else None
                    )
                    if project is None:
                        raise RuntimeError("claimed task project is unavailable")
                    raise _ProjectClaimActiveError(project, task_ids=(task.id,))
                if not allow_terminal_rewrite and existing.status in _TERMINAL_TASK_STATUSES:
                    return existing
            if existing_row and str(existing_row[1]) != task.milestone_id:
                raise ValueError("task is already attached to another milestone")
            conn.execute(
                "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, milestone_id=excluded.milestone_id",
                (task.id, task.milestone_id, json.dumps(task.to_dict(), ensure_ascii=False)),
            )
        return task

    def add_task_to_milestone(
        self,
        project_id: str,
        task: Task,
        *,
        expected_thread_id: str | None = None,
        expected_binding_generation: int | None = None,
        scope: TenantScope | None = None,
    ) -> tuple[Task, bool]:
        """Atomically create a task and attach it to its milestone.

        Message-to-project actions use this instead of creating a Team Task and
        syncing it backwards.  Project OS remains authoritative, while the
        collaboration task row is populated afterwards as a read projection.
        Reusing the same task id is idempotent when it already belongs to the
        requested milestone.
        """

        safe_project_id = _require_id(project_id, label="project_id")
        task = _normalize_task(task)
        expected_thread = (
            _require_id(expected_thread_id, label="thread_id")
            if expected_thread_id is not None
            else None
        )
        if (expected_thread is None) != (expected_binding_generation is None):
            raise ValueError("expected thread and binding generation must be provided together")
        if expected_binding_generation is not None and expected_binding_generation < 0:
            raise ValueError("expected binding generation must be non-negative")
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert_project_not_deleting(conn, safe_project_id)
            if expected_thread is not None and expected_binding_generation is not None:
                _assert_binding_matches(
                    conn,
                    thread_id=expected_thread,
                    project_id=safe_project_id,
                    generation=expected_binding_generation,
                )
            project = self._project_doc_for_scope(conn, safe_project_id, scope)
            if project is None:
                raise PermissionError("project belongs to another tenant or does not exist")
            if project.status in _TERMINAL_PROJECT_STATUSES:
                raise ValueError("cannot add a task to a terminal project")
            milestone_row = conn.execute(
                "SELECT doc, project_id FROM milestones WHERE id=?",
                (task.milestone_id,),
            ).fetchone()
            if not milestone_row or str(milestone_row[1]) != safe_project_id:
                raise ValueError("milestone does not belong to project")
            milestone = _milestone_from_doc(str(milestone_row[0]))
            if milestone is None:
                raise ValueError(f"corrupt existing milestone row: {task.milestone_id}")
            if milestone.status in _TERMINAL_MILESTONE_STATUSES:
                raise ValueError("cannot add a task to a terminal milestone")

            existing_row = conn.execute(
                "SELECT doc, milestone_id FROM tasks WHERE id=?",
                (task.id,),
            ).fetchone()
            created = existing_row is None
            if existing_row:
                if str(existing_row[1]) != task.milestone_id:
                    raise ValueError("task is already attached to another milestone")
                existing = _task_from_doc(str(existing_row[0]))
                if existing is None:
                    raise ValueError(f"corrupt existing task row: {task.id}")
                task = existing
            else:
                conn.execute(
                    "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, ?)",
                    (task.id, task.milestone_id, json.dumps(task.to_dict(), ensure_ascii=False)),
                )

            if task.id not in milestone.task_ids:
                milestone.task_ids.append(task.id)
                milestone = _normalize_milestone(milestone)
                conn.execute(
                    "UPDATE milestones SET doc=? WHERE id=?",
                    (json.dumps(milestone.to_dict(), ensure_ascii=False), milestone.id),
                )
        return task, created

    def get_task(self, task_id: str, *, scope: TenantScope | None = None) -> Task | None:
        task_id = _require_id(task_id, label="task_id")
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT doc, milestone_id FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                return None
            parent = conn.execute(
                "SELECT project_id FROM milestones WHERE id=?", (str(row[1]),)
            ).fetchone()
            if self._effective_scope(scope) is not None and (
                not parent or self._project_doc_for_scope(conn, str(parent[0]), scope) is None
            ):
                return None
        return _task_from_doc(row[0])

    def tasks_for_milestone(self, ms_id: str, *, scope: TenantScope | None = None) -> list[Task]:
        ms_id = _require_id(ms_id, label="milestone_id")
        with self._lock, self._conn() as conn:
            parent = conn.execute(
                "SELECT project_id FROM milestones WHERE id=?", (ms_id,)
            ).fetchone()
            if self._effective_scope(scope) is not None and (
                not parent or self._project_doc_for_scope(conn, str(parent[0]), scope) is None
            ):
                return []
            rows = conn.execute("SELECT doc FROM tasks WHERE milestone_id=?", (ms_id,)).fetchall()
        tasks: list[Task] = []
        for row in rows:
            task = _task_from_doc(row[0])
            if task is not None:
                tasks.append(task)
        return tasks
