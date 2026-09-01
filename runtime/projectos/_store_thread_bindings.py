"""Transactional thread/project binding primitives for :mod:`projectos.store`."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from runtime.projectos._store_helpers import (
    _json_dict,
    _normalize_project,
    _project_from_doc,
    _require_id,
    _require_kind,
)
from runtime.projectos._store_project_deletion import (
    _deleted_entity_ids,
    assert_project_not_deleting,
    assert_thread_not_deleting,
    record_project_delete_tombstones,
    require_project_delete_claim,
)
from runtime.projectos.model import Project
from runtime.safety.auth.scope import TenantScope


class ProjectBindingActiveError(RuntimeError):
    """A non-forced detach lost to the project's execution boundary."""

    def __init__(self, project: Project) -> None:
        self.project = project
        super().__init__(f"project is active: {project.id}")


class ProjectBindingMigrationRequiredError(RuntimeError):
    """Legacy multi-binding state requires an explicit cross-store migration."""

    def __init__(self, duplicates: dict[str, tuple[str, ...]]) -> None:
        self.duplicates = duplicates
        projects = ", ".join(sorted(duplicates))
        super().__init__(f"legacy projects have multiple thread bindings: {projects}")


class ProjectAlreadyBoundError(RuntimeError):
    """A project already belongs to another canonical collaboration thread."""

    def __init__(
        self, project: Project, canonical_thread_id: str, requested_thread_id: str
    ) -> None:
        self.project = project
        self.canonical_thread_id = canonical_thread_id
        self.requested_thread_id = requested_thread_id
        super().__init__(f"project is already bound: {project.id} -> {canonical_thread_id}")


class ProjectBindingChangedError(RuntimeError):
    """A write lost the exact thread/project binding generation it observed."""

    def __init__(self, thread_id: str, project_id: str, generation: int) -> None:
        self.thread_id = thread_id
        self.project_id = project_id
        self.generation = generation
        super().__init__(f"thread project binding changed: {thread_id}")


class ProjectClaimActiveError(RuntimeError):
    """Deletion or operator mutation encountered a live worker claim."""

    def __init__(
        self,
        project: Project,
        *,
        task_ids: tuple[str, ...] = (),
        milestone_ids: tuple[str, ...] = (),
    ) -> None:
        self.project = project
        self.task_ids = task_ids
        self.milestone_ids = milestone_ids
        super().__init__(f"project has active claims: {project.id}")


@dataclass(slots=True)
class ProjectBindingRestoreResult:
    project: Project
    restored_thread_ids: tuple[str, ...]
    conflict_project_ids: dict[str, str]
    generations: dict[str, int]
    execution_restored: bool


def _project_is_active(project: Project) -> bool:
    return project.status == "blocked" or (project.status == "running" and bool(project.started_at))


def _target_execution_conflicts(project: Project, thread_id: str) -> bool:
    return (project.status == "blocked" or bool(project.started_at)) and (
        project.execution_thread_id != thread_id
    )


def _binding_generation(conn: Any, thread_id: str) -> int:
    row = conn.execute(
        "SELECT generation FROM thread_project_generations WHERE thread_id=?",
        (thread_id,),
    ).fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError):
        return 0


def _bump_binding_generation(conn: Any, thread_id: str) -> int:
    current = _binding_generation(conn, thread_id)
    generation = current + 1
    conn.execute(
        "INSERT INTO thread_project_generations(thread_id, generation) VALUES (?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET generation=excluded.generation",
        (thread_id, generation),
    )
    return generation


def ensure_single_project_bindings(conn: Any) -> None:
    """Fail closed on legacy duplicates, then enforce one thread per project."""

    duplicate_rows = conn.execute(
        "SELECT project_id FROM thread_projects GROUP BY project_id HAVING COUNT(*) > 1"
    ).fetchall()
    duplicates: dict[str, tuple[str, ...]] = {}
    for (raw_project_id,) in duplicate_rows:
        project_id = _require_id(raw_project_id, label="project_id")
        rows = conn.execute(
            "SELECT thread_id FROM thread_projects WHERE project_id=? ORDER BY thread_id",
            (project_id,),
        ).fetchall()
        duplicates[project_id] = tuple(_require_id(row[0], label="thread_id") for row in rows)
    if duplicates:
        raise ProjectBindingMigrationRequiredError(duplicates)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_projects_single_project "
        "ON thread_projects(project_id)"
    )


def _other_binding_thread(conn: Any, project_id: str, thread_id: str) -> str | None:
    row = conn.execute(
        "SELECT thread_id FROM thread_projects WHERE project_id=? AND thread_id<>? LIMIT 1",
        (project_id, thread_id),
    ).fetchone()
    return _require_id(row[0], label="thread_id") if row else None


def _assert_binding_matches(
    conn: Any,
    *,
    thread_id: str,
    project_id: str,
    generation: int,
) -> None:
    row = conn.execute(
        "SELECT project_id FROM thread_projects WHERE thread_id=?",
        (thread_id,),
    ).fetchone()
    if (
        row is None
        or str(row[0]) != project_id
        or _binding_generation(conn, thread_id) != generation
    ):
        raise ProjectBindingChangedError(thread_id, project_id, generation)


def binding_snapshot(
    store: Any,
    thread_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Project | None, int]:
    """Read the authoritative binding plus its durable tombstone generation."""

    thread = _require_id(thread_id, label="thread_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN")
        generation = _binding_generation(conn, thread)
        row = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread,),
        ).fetchone()
        if row is None:
            return None, generation
        project = store._project_doc_for_scope(conn, str(row[0]), scope)
        return project, generation


def _delete_project_rows(conn: Any, project_id: str) -> None:
    bound_threads = conn.execute(
        "SELECT thread_id FROM thread_projects WHERE project_id=?",
        (project_id,),
    ).fetchall()
    for (thread_id,) in bound_threads:
        _bump_binding_generation(conn, _require_id(thread_id, label="thread_id"))
    conn.execute(
        "DELETE FROM milestone_claims WHERE milestone_id IN "
        "(SELECT id FROM milestones WHERE project_id=?)",
        (project_id,),
    )
    conn.execute(
        "DELETE FROM task_claims WHERE task_id IN "
        "(SELECT id FROM tasks WHERE milestone_id IN "
        "(SELECT id FROM milestones WHERE project_id=?))",
        (project_id,),
    )
    conn.execute(
        "DELETE FROM tasks WHERE milestone_id IN (SELECT id FROM milestones WHERE project_id=?)",
        (project_id,),
    )
    conn.execute("DELETE FROM milestones WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM thread_projects WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM project_events WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def _assert_project_deletable(conn: Any, project: Project) -> None:
    if _project_is_active(project):
        raise ProjectBindingActiveError(project)
    milestone_rows = conn.execute(
        "SELECT mc.milestone_id FROM milestone_claims mc "
        "INNER JOIN milestones m ON m.id=mc.milestone_id "
        "WHERE m.project_id=? ORDER BY mc.milestone_id",
        (project.id,),
    ).fetchall()
    task_rows = conn.execute(
        "SELECT tc.task_id FROM task_claims tc "
        "INNER JOIN tasks t ON t.id=tc.task_id "
        "INNER JOIN milestones m ON m.id=t.milestone_id "
        "WHERE m.project_id=? ORDER BY tc.task_id",
        (project.id,),
    ).fetchall()
    if milestone_rows or task_rows:
        raise ProjectClaimActiveError(
            project,
            task_ids=tuple(str(row[0]) for row in task_rows),
            milestone_ids=tuple(str(row[0]) for row in milestone_rows),
        )


def bind_thread(
    store: Any,
    thread_id: str,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> None:
    bind_thread_versioned(store, thread_id, project_id, scope=scope)


def bind_thread_versioned(
    store: Any,
    thread_id: str,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Project, int]:
    """Atomically move an inactive binding while preserving both project pointers."""

    thread = _require_id(thread_id, label="thread_id")
    project = _require_id(project_id, label="project_id")
    effective = store._effective_scope(scope)
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_thread_not_deleting(conn, thread)
        assert_project_not_deleting(conn, project)
        target = store._project_doc_for_scope(conn, project, scope)
        if target is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        row = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread,),
        ).fetchone()
        if row is not None and str(row[0]) == project:
            return target, _binding_generation(conn, thread)
        canonical_thread = _other_binding_thread(conn, project, thread)
        if canonical_thread is not None:
            raise ProjectAlreadyBoundError(target, canonical_thread, thread)
        if _target_execution_conflicts(target, thread):
            raise ProjectBindingActiveError(target)
        if row is not None and str(row[0]) != project:
            old_project_id = _require_id(row[0], label="project_id")
            assert_project_not_deleting(conn, old_project_id)
            old_project = store._project_doc_for_scope(conn, old_project_id, scope)
            if old_project is None:
                raw_row = conn.execute(
                    "SELECT doc FROM projects WHERE id=?",
                    (old_project_id,),
                ).fetchone()
                raw = _project_from_doc(raw_row[0]) if raw_row else None
                if raw is not None and not store._scope_project_allowed(raw, effective):
                    raise PermissionError("thread is bound to another tenant project")
            else:
                if _project_is_active(old_project):
                    raise ProjectBindingActiveError(old_project)
                if old_project.execution_thread_id == thread:
                    replacement = conn.execute(
                        "SELECT thread_id FROM thread_projects "
                        "WHERE project_id=? AND thread_id<>? "
                        "ORDER BY rowid DESC LIMIT 1",
                        (old_project_id, thread),
                    ).fetchone()
                    old_project.execution_thread_id = str(replacement[0]) if replacement else ""
                    old_project = _normalize_project(old_project)
                    conn.execute(
                        "UPDATE projects SET doc=? WHERE id=?",
                        (
                            json.dumps(old_project.to_dict(), ensure_ascii=False),
                            old_project_id,
                        ),
                    )
        conn.execute(
            "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET project_id=excluded.project_id",
            (thread, project),
        )
        target.execution_thread_id = thread
        target = _normalize_project(target)
        conn.execute(
            "UPDATE projects SET doc=? WHERE id=?",
            (json.dumps(target.to_dict(), ensure_ascii=False), project),
        )
        return target, _bump_binding_generation(conn, thread)


def bind_thread_if_absent(
    store: Any,
    thread_id: str,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Project, bool]:
    project, inserted, _generation = bind_thread_if_absent_versioned(
        store,
        thread_id,
        project_id,
        scope=scope,
    )
    return project, inserted


def bind_thread_if_absent_versioned(
    store: Any,
    thread_id: str,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Project, bool, int]:
    """Bind once and return the canonical project plus winner status."""

    thread = _require_id(thread_id, label="thread_id")
    project = _require_id(project_id, label="project_id")
    effective = store._effective_scope(scope)
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_thread_not_deleting(conn, thread)
        assert_project_not_deleting(conn, project)
        candidate = store._project_doc_for_scope(conn, project, scope)
        if candidate is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        row = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread,),
        ).fetchone()
        if row is not None:
            existing_id = _require_id(row[0], label="project_id")
            existing = store._project_doc_for_scope(conn, existing_id, scope)
            if existing is None:
                # Never expose or overwrite a cross-tenant binding. Only a
                # dangling legacy row can be healed in place.
                raw_row = conn.execute(
                    "SELECT doc FROM projects WHERE id=?",
                    (existing_id,),
                ).fetchone()
                raw = _project_from_doc(raw_row[0]) if raw_row else None
                if raw is not None and not store._scope_project_allowed(raw, effective):
                    raise PermissionError("thread is bound to another tenant project")
                conn.execute(
                    "DELETE FROM thread_projects WHERE thread_id=? AND project_id=?",
                    (thread, existing_id),
                )
            else:
                if existing.id == project and _target_execution_conflicts(candidate, thread):
                    raise ProjectBindingActiveError(candidate)
                return existing, False, _binding_generation(conn, thread)
        canonical_thread = _other_binding_thread(conn, project, thread)
        if canonical_thread is not None:
            raise ProjectAlreadyBoundError(candidate, canonical_thread, thread)
        if _target_execution_conflicts(candidate, thread):
            raise ProjectBindingActiveError(candidate)
        conn.execute(
            "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?)",
            (thread, project),
        )
        candidate.execution_thread_id = thread
        normalized = _normalize_project(candidate)
        conn.execute(
            "UPDATE projects SET doc=? WHERE id=?",
            (json.dumps(normalized.to_dict(), ensure_ascii=False), project),
        )
        return normalized, True, _bump_binding_generation(conn, thread)


def start_project_if_bound(
    store: Any,
    project_id: str,
    thread_id: str,
    *,
    scope: TenantScope | None = None,
) -> Project | None:
    """Cross the execution boundary only while the expected binding still wins."""

    project = _require_id(project_id, label="project_id")
    thread = _require_id(thread_id, label="thread_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_project_not_deleting(conn, project)
        candidate = store._project_doc_for_scope(conn, project, scope)
        if candidate is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        row = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread,),
        ).fetchone()
        if row is None or str(row[0]) != project:
            return None
        if candidate.status in {"done", "failed"}:
            return None
        candidate.started_at = candidate.started_at or datetime.now(UTC).isoformat(
            timespec="seconds"
        )
        candidate.execution_thread_id = thread
        normalized = _normalize_project(candidate)
        conn.execute(
            "UPDATE projects SET doc=? WHERE id=?",
            (json.dumps(normalized.to_dict(), ensure_ascii=False), project),
        )
        return normalized


def unbind_thread(
    store: Any,
    thread_id: str,
    *,
    expected_project_id: str | None = None,
    event_kind: str | None = None,
    event_payload: dict[str, Any] | None = None,
    reject_active: bool = False,
    scope: TenantScope | None = None,
) -> Project | None:
    project, _generation = unbind_thread_versioned(
        store,
        thread_id,
        expected_project_id=expected_project_id,
        event_kind=event_kind,
        event_payload=event_payload,
        reject_active=reject_active,
        scope=scope,
    )
    return project


def unbind_thread_versioned(
    store: Any,
    thread_id: str,
    *,
    expected_project_id: str | None = None,
    event_kind: str | None = None,
    event_payload: dict[str, Any] | None = None,
    reject_active: bool = False,
    scope: TenantScope | None = None,
) -> tuple[Project | None, int]:
    """Compare-and-delete a binding while retaining all project state."""

    thread = _require_id(thread_id, label="thread_id")
    expected = (
        _require_id(expected_project_id, label="project_id")
        if expected_project_id is not None
        else None
    )
    kind = _require_kind(event_kind) if event_kind is not None else None
    payload = _json_dict(event_payload or {}, label="event payload")
    event_id = f"EV-{uuid4().hex[:12]}" if kind is not None else ""
    created_at = time.time()
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT project_id FROM thread_projects WHERE thread_id=?",
            (thread,),
        ).fetchone()
        if row is None:
            return None, _binding_generation(conn, thread)
        project_id = _require_id(row[0], label="project_id")
        if expected is not None and project_id != expected:
            raise ValueError("thread project binding changed")
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        assert_project_not_deleting(conn, project_id)
        if reject_active and _project_is_active(project):
            raise ProjectBindingActiveError(project)
        deleted = conn.execute(
            "DELETE FROM thread_projects WHERE thread_id=? AND project_id=?",
            (thread, project_id),
        )
        if deleted.rowcount != 1:
            raise RuntimeError("thread project binding changed")
        if project.execution_thread_id == thread:
            replacement = conn.execute(
                "SELECT thread_id FROM thread_projects WHERE project_id=? "
                "ORDER BY thread_id LIMIT 1",
                (project_id,),
            ).fetchone()
            project.execution_thread_id = str(replacement[0]) if replacement else ""
            normalized = _normalize_project(project)
            conn.execute(
                "UPDATE projects SET doc=? WHERE id=?",
                (json.dumps(normalized.to_dict(), ensure_ascii=False), project_id),
            )
            project = normalized
        if kind is not None:
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event_id,
                    project_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
        return project, _bump_binding_generation(conn, thread)


def unbind_thread_for_delete(
    store: Any,
    thread_id: str,
    project_id: str,
    delete_token: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Project | None, int]:
    """Remove one binding under a delete lease without mutating the project doc."""

    thread = _require_id(thread_id, label="thread_id")
    project_id = _require_id(project_id, label="project_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        require_project_delete_claim(conn, project_id, delete_token)
        claim = conn.execute(
            "SELECT token, created_at FROM project_delete_claims WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if claim is None:
            raise RuntimeError("project delete claim disappeared")
        conn.execute(
            "DELETE FROM project_delete_claims WHERE project_id=? AND token=?",
            (project_id, str(claim[0])),
        )
        deleted = conn.execute(
            "DELETE FROM thread_projects WHERE thread_id=? AND project_id=?",
            (thread, project_id),
        )
        conn.execute(
            "INSERT INTO project_delete_claims(project_id, token, created_at) VALUES (?, ?, ?)",
            (project_id, str(claim[0]), float(claim[1])),
        )
        if deleted.rowcount != 1:
            return None, _binding_generation(conn, thread)
        return project, _bump_binding_generation(conn, thread)


def restore_thread_bindings(
    store: Any,
    project_id: str,
    thread_ids: list[str] | tuple[str, ...],
    *,
    original_execution_thread_id: str = "",
    scope: TenantScope | None = None,
) -> ProjectBindingRestoreResult:
    """Atomically restore one saga's removed bindings without pointer drift."""

    project_id = _require_id(project_id, label="project_id")
    threads = tuple(sorted({_require_id(thread_id, label="thread_id") for thread_id in thread_ids}))
    original_execution = (
        _require_id(original_execution_thread_id, label="thread_id")
        if original_execution_thread_id
        else ""
    )
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        assert_project_not_deleting(conn, project_id)
        if original_execution and original_execution in threads:
            threads = (
                original_execution,
                *(thread for thread in threads if thread != original_execution),
            )
        restored: list[str] = []
        conflicts: dict[str, str] = {}
        generations: dict[str, int] = {}
        for thread in threads:
            assert_thread_not_deleting(conn, thread)
            row = conn.execute(
                "SELECT project_id FROM thread_projects WHERE thread_id=?",
                (thread,),
            ).fetchone()
            if row is None:
                canonical_row = conn.execute(
                    "SELECT thread_id FROM thread_projects WHERE project_id=? LIMIT 1",
                    (project_id,),
                ).fetchone()
                if canonical_row is not None and str(canonical_row[0]) != thread:
                    conflicts[thread] = project_id
                    generations[thread] = _binding_generation(conn, thread)
                    continue
                conn.execute(
                    "INSERT INTO thread_projects(thread_id, project_id) VALUES (?, ?)",
                    (thread, project_id),
                )
                generations[thread] = _bump_binding_generation(conn, thread)
                restored.append(thread)
                continue
            winner_id = _require_id(row[0], label="project_id")
            generations[thread] = _binding_generation(conn, thread)
            if winner_id == project_id:
                restored.append(thread)
            else:
                conflicts[thread] = winner_id

        active_execution_conflict = False
        current_execution = project.execution_thread_id
        if original_execution and current_execution and current_execution != original_execution:
            current_row = conn.execute(
                "SELECT project_id FROM thread_projects WHERE thread_id=?",
                (current_execution,),
            ).fetchone()
            active_execution_conflict = (
                _project_is_active(project)
                and current_row is not None
                and str(current_row[0]) == project_id
            )
        execution_restored = not original_execution
        if original_execution:
            row = conn.execute(
                "SELECT project_id FROM thread_projects WHERE thread_id=?",
                (original_execution,),
            ).fetchone()
            execution_restored = (
                not active_execution_conflict and row is not None and str(row[0]) == project_id
            )
        if execution_restored:
            project.execution_thread_id = original_execution
        elif not active_execution_conflict:
            project.status = "blocked"
            current_row = (
                conn.execute(
                    "SELECT project_id FROM thread_projects WHERE thread_id=?",
                    (current_execution,),
                ).fetchone()
                if current_execution
                else None
            )
            if current_row is None or str(current_row[0]) != project_id:
                replacement = conn.execute(
                    "SELECT thread_id FROM thread_projects WHERE project_id=? "
                    "ORDER BY thread_id LIMIT 1",
                    (project_id,),
                ).fetchone()
                project.execution_thread_id = str(replacement[0]) if replacement else ""
        if not execution_restored:
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"EV-{uuid4().hex[:12]}",
                    project_id,
                    "project.binding_restore_conflict",
                    json.dumps(
                        {
                            "original_execution_thread_id": original_execution,
                            "active_execution_preserved": active_execution_conflict,
                            "conflict_project_ids": conflicts,
                        },
                        ensure_ascii=False,
                    ),
                    time.time(),
                ),
            )
        normalized = _normalize_project(project)
        conn.execute(
            "UPDATE projects SET doc=? WHERE id=?",
            (json.dumps(normalized.to_dict(), ensure_ascii=False), project_id),
        )
        return ProjectBindingRestoreResult(
            project=normalized,
            restored_thread_ids=tuple(restored),
            conflict_project_ids=conflicts,
            generations=generations,
            execution_restored=execution_restored,
        )


def delete_project(
    store: Any,
    project_id: str,
    *,
    require_unbound: bool = False,
    scope: TenantScope | None = None,
) -> bool:
    """Delete atomically, optionally refusing a project with any live binding."""

    project = _require_id(project_id, label="project_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidate = store._project_doc_for_scope(conn, project, scope)
        if candidate is None:
            return False
        assert_project_not_deleting(conn, project)
        _assert_project_deletable(conn, candidate)
        if (
            require_unbound
            and conn.execute(
                "SELECT 1 FROM thread_projects WHERE project_id=? LIMIT 1",
                (project,),
            ).fetchone()
        ):
            return False
        milestone_ids, tasks = _deleted_entity_ids(conn, project)
        delete_token = f"PD-{uuid4().hex}"
        _delete_project_rows(conn, project)
        record_project_delete_tombstones(conn, candidate, delete_token, milestone_ids, tasks)
    return True


def assert_project_deletable(
    store: Any,
    project_id: str,
    *,
    scope: TenantScope | None = None,
) -> Project:
    project_id = _require_id(project_id, label="project_id")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        assert_project_not_deleting(conn, project_id)
        _assert_project_deletable(conn, project)
        return project


class ProjectBindingStoreMixin:
    """Public ProjectStore binding API kept out of the persistent-store god file."""

    _lock: Any
    _conn: Any
    _project_doc_for_scope: Any

    def binding_snapshot(
        self, thread_id: str, *, scope: TenantScope | None = None
    ) -> tuple[Project | None, int]:
        return binding_snapshot(self, thread_id, scope=scope)

    def assert_project_deletable(
        self, project_id: str, *, scope: TenantScope | None = None
    ) -> Project:
        return assert_project_deletable(self, project_id, scope=scope)

    def bind_thread(
        self,
        thread_id: str,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        bind_thread(self, thread_id, project_id, scope=scope)

    def bind_thread_versioned(
        self,
        thread_id: str,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Project, int]:
        return bind_thread_versioned(self, thread_id, project_id, scope=scope)

    def bind_thread_if_absent(
        self,
        thread_id: str,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Project, bool]:
        return bind_thread_if_absent(self, thread_id, project_id, scope=scope)

    def bind_thread_if_absent_versioned(
        self,
        thread_id: str,
        project_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Project, bool, int]:
        return bind_thread_if_absent_versioned(self, thread_id, project_id, scope=scope)

    def start_project_if_bound(
        self,
        project_id: str,
        thread_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> Project | None:
        return start_project_if_bound(self, project_id, thread_id, scope=scope)

    def unbind_thread(
        self,
        thread_id: str,
        *,
        expected_project_id: str | None = None,
        event_kind: str | None = None,
        event_payload: dict[str, Any] | None = None,
        reject_active: bool = False,
        scope: TenantScope | None = None,
    ) -> Project | None:
        return unbind_thread(
            self,
            thread_id,
            expected_project_id=expected_project_id,
            event_kind=event_kind,
            event_payload=event_payload,
            reject_active=reject_active,
            scope=scope,
        )

    def unbind_thread_versioned(
        self,
        thread_id: str,
        *,
        expected_project_id: str | None = None,
        event_kind: str | None = None,
        event_payload: dict[str, Any] | None = None,
        reject_active: bool = False,
        scope: TenantScope | None = None,
    ) -> tuple[Project | None, int]:
        return unbind_thread_versioned(
            self,
            thread_id,
            expected_project_id=expected_project_id,
            event_kind=event_kind,
            event_payload=event_payload,
            reject_active=reject_active,
            scope=scope,
        )

    def restore_thread_bindings(
        self,
        project_id: str,
        thread_ids: list[str] | tuple[str, ...],
        *,
        original_execution_thread_id: str = "",
        scope: TenantScope | None = None,
    ) -> ProjectBindingRestoreResult:
        return restore_thread_bindings(
            self,
            project_id,
            thread_ids,
            original_execution_thread_id=original_execution_thread_id,
            scope=scope,
        )

    def unbind_thread_for_delete(
        self,
        thread_id: str,
        project_id: str,
        delete_token: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Project | None, int]:
        return unbind_thread_for_delete(
            self,
            thread_id,
            project_id,
            delete_token,
            scope=scope,
        )

    def project_for_thread(
        self, thread_id: str, *, scope: TenantScope | None = None
    ) -> Project | None:
        return binding_snapshot(self, thread_id, scope=scope)[0]

    def thread_for_project(
        self, project_id: str, *, scope: TenantScope | None = None
    ) -> str | None:
        project_id = _require_id(project_id, label="project_id")
        with self._lock, self._conn() as conn:
            project = self._project_doc_for_scope(conn, project_id, scope)
            if project is None:
                return None
            preferred = project.execution_thread_id
            if (
                preferred
                and conn.execute(
                    "SELECT 1 FROM thread_projects WHERE project_id=? AND thread_id=?",
                    (project_id, preferred),
                ).fetchone()
            ):
                return preferred
            row = conn.execute(
                "SELECT thread_id FROM thread_projects WHERE project_id=? "
                "ORDER BY thread_id LIMIT 1",
                (project_id,),
            ).fetchone()
        try:
            return _require_id(row[0], label="thread_id") if row else None
        except ValueError:
            return None

    def thread_project_map(self, *, scope: TenantScope | None = None) -> dict[str, str]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT tp.thread_id, tp.project_id FROM thread_projects tp "
                "INNER JOIN projects p ON p.id=tp.project_id"
            ).fetchall()
            out: dict[str, str] = {}
            for thread_id, project_id in rows:
                try:
                    safe_thread = _require_id(thread_id, label="thread_id")
                    safe_project = _require_id(project_id, label="project_id")
                except ValueError:
                    continue
                if self._project_doc_for_scope(conn, safe_project, scope) is not None:
                    out[safe_thread] = safe_project
        return out


__all__ = [
    "ProjectAlreadyBoundError",
    "ProjectBindingActiveError",
    "ProjectBindingChangedError",
    "ProjectBindingMigrationRequiredError",
    "ProjectBindingRestoreResult",
    "ProjectBindingStoreMixin",
    "ProjectClaimActiveError",
    "assert_project_deletable",
    "binding_snapshot",
    "bind_thread",
    "bind_thread_if_absent",
    "bind_thread_if_absent_versioned",
    "bind_thread_versioned",
    "delete_project",
    "ensure_single_project_bindings",
    "restore_thread_bindings",
    "start_project_if_bound",
    "unbind_thread",
    "unbind_thread_for_delete",
    "unbind_thread_versioned",
]
