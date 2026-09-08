"""Bind rollback reads and writes to existing, currently authorized workspaces.

The request's project_root is an assertion, never an authorization grant.
Task-store writers share its existing platform lock during a rollback. Outside
programs do not participate; the file ledger must still enforce path/hash checks.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ._fs_router_paths import _allowed_fs_roots
from .thread_workspace import verified_managed_workspace

_ROLLBACK_LOCK = threading.Lock()
_TERMINAL = frozenset({"completed", "cancelled", "failed", "disconnected"})


def _deny(code: str, status: int = 403) -> None:
    raise HTTPException(status, {"error": code})


def checked_project_root(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _deny("invalid_project_root", 400)
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        _deny("absolute_project_root_required", 400)
    return str(path)


def _resolve(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        _deny("rollback_workspace_unavailable")
    path = Path(value).expanduser()
    if not path.is_absolute():
        _deny("rollback_workspace_unavailable")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _deny("rollback_workspace_unavailable")
    raise AssertionError("unreachable")


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _records(ctx: Any) -> list[Any]:
    store = getattr(ctx.task_supervisor, "store", None)
    if store is None:
        _deny("rollback_task_authority_unavailable")
    page = store.list_page(limit=10_000, include_unowned=True)
    records = page["items"]
    if page["total"] != len(records):
        _deny("rollback_task_inventory_incomplete", 409)
    return records


def _thread_root(ctx: Any, request: Any, record: Any, scope: Any) -> Path:
    thread_id = record.thread_id
    if not thread_id or ctx.thread_store is None:
        _deny("rollback_task_thread_unbound")
    thread = _current_thread(ctx.thread_store, thread_id)
    if not isinstance(thread, dict):
        _deny("rollback_thread_unavailable")
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        _deny("rollback_thread_unavailable")
    if ctx.require_auth:
        principal = request.state.principal
        cross_tenant = bool(getattr(scope, "allow_cross_tenant", False))
        if not cross_tenant and (
            record.owner_id != principal.actor_id
            or (metadata.get("owner_actor_id") or metadata.get("actor_id")) != principal.actor_id
            or metadata.get("tenant_id") != principal.tenant_id
        ):
            _deny("rollback_workspace_access_revoked")
    if ctx.require_auth and not ctx.allow_local_workspace_access:
        root = verified_managed_workspace(
            ctx.workspace_root, thread_id=thread_id, metadata=metadata
        )
        if root is None:
            _deny("managed_workspace_required")
        allowed = [root.resolve()]
    else:
        allowed = [_resolve(metadata.get("workspace_path"))]
        extra = metadata.get("extra_workspaces")
        if isinstance(extra, list):
            allowed.extend(_resolve(value) for value in extra)
    root = _resolve(record.workspace_path) if record.workspace_path else allowed[0]
    if not root.is_dir() or not any(_within(root, parent) for parent in allowed):
        _deny("rollback_workspace_access_revoked")
    return root


def _current_thread(store: Any, thread_id: str) -> Any:
    from runtime.memory.threads.store import ThreadStateStore, thread_mutation_lock

    if not isinstance(store, ThreadStateStore):
        return store.get(thread_id)
    # get()/get_state() intentionally serve this process's cache. Permission
    # checks for a restore need the latest durable revision from other workers.
    with (
        store._lock,
        thread_mutation_lock(
            journal_path=store._path, per_agent_base=store._per_agent_base, thread_id=thread_id
        ),
    ):
        store._assert_thread_writable_locked(thread_id)
        persisted = store._latest_persisted_locked(thread_id)
        current = persisted.thread if persisted.found else store._threads.get(thread_id)
        return deepcopy(current)


def _check_events(events: list[Any], root: Path) -> None:
    from runtime.memory.runtime_state.file_transactions import build_file_rollback_ledger

    for entry in build_file_rollback_ledger(events):
        try:
            path = Path(entry.path)
            target = (path if path.is_absolute() else root / path).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            _deny("rollback_path_outside_workspace")
        if not _within(target, root):
            _deny("rollback_path_outside_workspace")


def _root_for(
    ctx: Any,
    request: Any,
    scope: Any,
    events: list[Any],
    task_id: str | None,
    project_root: str | None,
) -> Path:
    task_ids = {str(event.task_id) for event in events if getattr(event, "task_id", None)}
    if task_id:
        task_ids.add(task_id)
    if ctx.task_supervisor is None and not ctx.require_auth:
        # Preserve standalone local use, but only under service-owned roots.
        allowed = [_resolve(ctx.workspace_root)] if ctx.workspace_root else _allowed_fs_roots()
        if project_root:
            root = _resolve(project_root)
            if root not in allowed:
                _deny("rollback_workspace_unavailable")
        elif len(allowed) == 1:
            root = allowed[0]
        else:
            _deny("rollback_workspace_ambiguous", 400)
        _check_events(events, root)
        return root
    records = _records(ctx)
    if not task_ids or any(not getattr(event, "task_id", None) for event in events):
        _deny("rollback_task_thread_unbound")
    roots = set()
    for identifier in task_ids:
        matches = [
            record for record in records if identifier in {record.task_id, record.origin_task_id}
        ]
        if len(matches) != 1:
            _deny("rollback_task_thread_unbound")
        record = matches[0]
        if str(record.status) not in _TERMINAL:
            _deny("rollback_task_active", 409)
        roots.add(_thread_root(ctx, request, record, scope))
    if len(roots) != 1:
        _deny("rollback_workspace_ambiguous", 400)
    root = roots.pop()
    if project_root and _resolve(project_root) != root:
        _deny("rollback_project_root_mismatch")
    for record in records:
        # Check every owner: another task may write the same local workspace.
        active = str(record.status) not in _TERMINAL
        leased = record.lease is not None and not record.lease.expired
        if not active and not leased:
            continue
        paths = list(getattr(record.capabilities, "workspace_paths", ()) or ())
        if record.workspace_path:
            paths.append(record.workspace_path)
        else:
            thread = (
                _current_thread(ctx.thread_store, record.thread_id)
                if ctx.thread_store and record.thread_id
                else None
            )
            metadata = thread.get("metadata", {}) if isinstance(thread, dict) else {}
            if metadata.get("workspace_path"):
                paths.append(metadata["workspace_path"])
            if isinstance(metadata.get("extra_workspaces"), list):
                paths.extend(metadata["extra_workspaces"])
        if not paths or any(
            not isinstance(value, str) or not Path(value).is_absolute() for value in paths
        ):
            _deny("rollback_workspace_busy", 409)
        others = [Path(value).expanduser().resolve(strict=False) for value in paths]
        if not any(_within(root, other) or _within(other, root) for other in others):
            continue
        if active:
            _deny("rollback_workspace_busy", 409)
        if leased:
            _deny("rollback_lease_conflict", 409)
    _check_events(events, root)
    return root


@contextmanager
def rollback_scope(
    ctx: Any,
    request: Any,
    scope: Any,
    *,
    events: list[Any],
    task_id: str | None,
    project_root: str | None,
) -> Iterator[str]:
    project_root = checked_project_root(project_root)
    if not _ROLLBACK_LOCK.acquire(blocking=False):
        _deny("rollback_in_progress", 409)
    try:
        store = getattr(ctx.task_supervisor, "store", None)
        with store._write_lock() if store is not None else nullcontext():
            try:
                root = _root_for(ctx, request, scope, events, task_id, project_root)
            except HTTPException:
                raise
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                raise HTTPException(503, {"error": "rollback_scope_unavailable"}) from exc
            yield str(root)
    finally:
        _ROLLBACK_LOCK.release()
