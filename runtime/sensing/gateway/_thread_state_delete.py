"""Fail-closed thread deletion with a durable Project OS binding fence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .thread_workspace import (
    MANAGED_WORKSPACE_DELETION_KEY,
    MANAGED_WORKSPACE_DELETION_MARKER,
    discard_staged_managed_workspace,
    stage_managed_workspace_deletion,
    verified_managed_workspace,
)


def _log_exists(logs_root: Path | str | None, thread_id: str) -> bool:
    if logs_root is None:
        return False
    from runtime.memory.threads.event_log import thread_log_path

    return thread_log_path(logs_root, thread_id).exists()


def _existing_delete_lease(
    project_store: Any,
    thread_id: str,
    *,
    tenant_id: str,
    owner_id: str,
) -> Any:
    if project_store is None:
        return None
    probe = getattr(project_store, "thread_delete_lease", None)
    if not callable(probe):
        raise HTTPException(503, "thread project deletion fence unavailable")
    try:
        return probe(thread_id, tenant_id=tenant_id, owner_id=owner_id)
    except PermissionError as exc:
        raise HTTPException(404, f"thread not found: {thread_id}") from exc


def _begin_delete_lease(
    project_store: Any,
    thread_id: str,
    *,
    tenant_id: str,
    owner_id: str,
) -> Any:
    if project_store is None:
        return None
    from runtime.projectos.store import ProjectThreadBoundError

    begin = getattr(project_store, "begin_thread_delete", None)
    if not callable(begin):
        raise HTTPException(503, "thread project deletion fence unavailable")
    try:
        return begin(thread_id, tenant_id=tenant_id, owner_id=owner_id)
    except ProjectThreadBoundError as exc:
        project = exc.project
        raise HTTPException(
            409,
            {
                "code": "THREAD_PROJECT_BOUND",
                "message": "detach the project before deleting this thread",
                "thread_id": thread_id,
                "project_id": project.id,
                "project_status": project.status,
                "project_active": bool(
                    project.started_at or project.status in {"running", "blocked"}
                ),
            },
        ) from exc
    except PermissionError as exc:
        raise HTTPException(404, f"thread not found: {thread_id}") from exc


def _finalize_delete_lease(project_store: Any, thread_id: str, lease: Any) -> None:
    if project_store is None or lease is None:
        return
    finalize = getattr(project_store, "finalize_thread_delete", None)
    if not callable(finalize):
        raise HTTPException(503, "thread project deletion fence unavailable")
    try:
        finalize(thread_id, lease.token)
    except Exception as exc:  # noqa: BLE001 - durable retry must retain the lease
        raise HTTPException(503, "thread project deletion finalization failed") from exc


def _cancel_project_preflight(project_store: Any, thread_id: str, lease: Any) -> None:
    if project_store is None or lease is None:
        return
    cancel = getattr(project_store, "cancel_thread_delete_preflight", None)
    if not callable(cancel) or not cancel(thread_id, lease.token):
        raise HTTPException(503, "thread project deletion preflight could not be released")


def _begin_group_delete(
    group_store: Any,
    project_store: Any,
    thread_id: str,
    project_lease: Any,
    *,
    tenant_id: str,
    owner_id: str,
) -> Any:
    if group_store is None:
        return None
    from runtime.memory.cowork.group_store import (
        GroupThreadActiveWorkError,
        GroupThreadLinkedError,
    )

    begin = getattr(group_store, "begin_thread_delete", None)
    if not callable(begin):
        raise HTTPException(503, "thread group deletion fence unavailable")
    try:
        return begin(thread_id, tenant_id=tenant_id, owner_id=owner_id)
    except GroupThreadActiveWorkError as exc:
        _cancel_project_preflight(project_store, thread_id, project_lease)
        raise HTTPException(
            409,
            {
                "code": "THREAD_ASYNC_WORK_ACTIVE",
                "message": "finish background cowork tasks before deleting thread",
                "thread_id": thread_id,
            },
        ) from exc
    except GroupThreadLinkedError as exc:
        _cancel_project_preflight(project_store, thread_id, project_lease)
        raise HTTPException(
            409,
            {
                "code": "THREAD_ROOM_LINKED" if exc.room_id else "THREAD_GROUP_LINKED",
                "message": "unlink and remove collaboration group state before deleting thread",
                "thread_id": thread_id,
                "room_id": exc.room_id,
            },
        ) from exc
    except PermissionError as exc:
        _cancel_project_preflight(project_store, thread_id, project_lease)
        raise HTTPException(404, f"thread not found: {thread_id}") from exc


def _finalize_group_delete(group_store: Any, thread_id: str, lease: Any) -> None:
    if group_store is None or lease is None:
        return
    finalize = getattr(group_store, "finalize_thread_delete", None)
    if not callable(finalize):
        raise HTTPException(503, "thread group deletion fence unavailable")
    try:
        finalize(thread_id, lease.token)
    except Exception as exc:  # noqa: BLE001 - retain every roll-forward fence
        raise HTTPException(503, "thread group deletion finalization failed") from exc


def _begin_state_delete(
    store: Any,
    thread_id: str,
    existing: Any,
    *,
    tenant_id: str,
    owner_id: str,
    metadata: dict[str, Any] | None,
) -> Any:
    begin = getattr(store, "begin_permanent_delete", None)
    if not callable(begin):
        raise HTTPException(503, "thread permanent deletion fence unavailable")
    marker = (
        {MANAGED_WORKSPACE_DELETION_KEY: MANAGED_WORKSPACE_DELETION_MARKER}
        if metadata is not None
        else None
    )
    try:
        return begin(
            thread_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected=existing,
            metadata=marker,
            status="deleting",
        )
    except PermissionError as exc:
        raise HTTPException(404, f"thread not found: {thread_id}") from exc
    except Exception as exc:  # noqa: BLE001 - fence uncertainty must fail closed
        raise HTTPException(503, "thread permanent deletion fence unavailable") from exc


def _finalize_state_delete(store: Any, thread_id: str, lease: Any) -> None:
    finalize = getattr(store, "finalize_permanent_delete", None)
    if not callable(finalize):
        raise HTTPException(503, "thread permanent deletion finalization unavailable")
    try:
        finalize(thread_id, lease.token)
    except Exception as exc:  # noqa: BLE001 - retain Project/Group claims for retry
        raise HTTPException(503, "thread permanent deletion finalization failed") from exc


def _delete_thread_state_claimed(
    *,
    store: Any,
    thread_id: str,
    existing: Any,
    actor_id: str | None,
    tenant_id: str | None,
    require_auth: bool,
    workspace_root: Path | str | None,
    logs_root: Path | str | None,
    is_archived: Callable[[str], bool],
    project_store: Any = None,
    group_store: Any = None,
    logger: logging.Logger,
) -> None:
    """Delete one authorized thread after reserving it against project binds."""

    owner = str(actor_id or "").strip() if require_auth else ""
    tenant = str(tenant_id or "").strip() if require_auth else ""
    prior_lease = _existing_delete_lease(
        project_store,
        thread_id,
        tenant_id=tenant,
        owner_id=owner,
    )
    state_lease_probe = getattr(store, "thread_delete_lease", None)
    try:
        prior_state_lease = (
            state_lease_probe(thread_id, tenant_id=tenant, owner_id=owner)
            if callable(state_lease_probe)
            else None
        )
    except PermissionError as exc:
        raise HTTPException(404, f"thread not found: {thread_id}") from exc
    except Exception as exc:  # noqa: BLE001 - corrupted fence must block deletion
        raise HTTPException(503, "thread permanent deletion state unavailable") from exc
    if prior_state_lease is not None:
        reader = getattr(store, "thread_for_permanent_delete", None)
        if not callable(reader):
            raise HTTPException(503, "thread permanent deletion retry reader unavailable")
        try:
            existing = reader(thread_id, prior_state_lease.token)
        except Exception as exc:  # noqa: BLE001 - exact-token read must fail closed
            raise HTTPException(503, "thread permanent deletion state unavailable") from exc
    else:
        try:
            existing = store.get(thread_id)
        except Exception as exc:  # noqa: BLE001 - unexpected tombstone/read failure is unsafe
            raise HTTPException(503, "thread state unavailable for deletion") from exc
    target_exists = existing is not None or _log_exists(logs_root, thread_id)
    metadata: dict[str, Any] | None = None

    def _verified_metadata(thread: Any) -> dict[str, Any]:
        raw = thread.get("metadata") if isinstance(thread, dict) else None
        candidate = dict(raw) if isinstance(raw, dict) else {}
        if candidate.get("owner_actor_id") != owner or candidate.get("tenant_id") != tenant:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if (
            verified_managed_workspace(
                workspace_root,
                thread_id=thread_id,
                metadata=candidate,
                allow_deleting=True,
            )
            is None
        ):
            raise HTTPException(409, "managed thread workspace verification failed")
        deletion_marker = candidate.get(MANAGED_WORKSPACE_DELETION_KEY)
        if deletion_marker not in (None, MANAGED_WORKSPACE_DELETION_MARKER):
            raise HTTPException(409, "managed thread workspace deletion state invalid")
        return candidate

    if require_auth:
        if isinstance(existing, dict) and owner and tenant:
            metadata = _verified_metadata(existing)
        elif prior_lease is None and prior_state_lease is None:
            raise HTTPException(404, f"thread not found: {thread_id}")
    elif not target_exists and prior_lease is None and prior_state_lease is None:
        raise HTTPException(404, f"thread not found: {thread_id}")

    lease = _begin_delete_lease(
        project_store,
        thread_id,
        tenant_id=tenant,
        owner_id=owner,
    )
    group_lease = _begin_group_delete(
        group_store,
        project_store,
        thread_id,
        lease,
        tenant_id=tenant,
        owner_id=owner,
    )
    state_lease = _begin_state_delete(
        store,
        thread_id,
        existing,
        tenant_id=tenant,
        owner_id=owner,
        metadata=metadata,
    )

    if require_auth and metadata is not None:
        reader = getattr(store, "thread_for_permanent_delete", None)
        if not callable(reader):
            raise HTTPException(503, "thread permanent deletion retry reader unavailable")
        try:
            current = reader(thread_id, state_lease.token)
        except Exception as exc:  # noqa: BLE001 - exact-token read must fail closed
            raise HTTPException(503, "managed thread workspace deletion state unavailable") from exc
        try:
            metadata = _verified_metadata(current)
        except HTTPException:
            raise HTTPException(
                503,
                "managed thread workspace deletion state unavailable",
            ) from None
        if metadata.get(MANAGED_WORKSPACE_DELETION_KEY) != MANAGED_WORKSPACE_DELETION_MARKER:
            raise HTTPException(503, "managed thread workspace deletion state unavailable")

        try:
            staged = stage_managed_workspace_deletion(
                workspace_root,
                thread_id=thread_id,
                metadata=metadata,
            )
            discard_staged_managed_workspace(staged)
        except PermissionError as exc:
            raise HTTPException(409, "managed thread workspace verification failed") from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error("managed workspace cleanup failed for %s: %s", thread_id, exc)
            raise HTTPException(503, "managed thread workspace cleanup failed") from exc

        if logs_root is not None and not is_archived(thread_id):
            from runtime.memory.threads.event_log import archive_thread

            try:
                archive_thread(logs_root, thread_id)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error("thread log archival failed for %s: %s", thread_id, exc)
                raise HTTPException(503, "thread log archival failed") from exc

        _finalize_state_delete(store, thread_id, state_lease)
        _finalize_group_delete(group_store, thread_id, group_lease)
        _finalize_delete_lease(project_store, thread_id, lease)
        return

    archived_log = False
    if logs_root is not None and not is_archived(thread_id):
        from runtime.memory.threads.event_log import archive_thread

        archived_log = archive_thread(logs_root, thread_id)
    if not archived_log and prior_lease is None and prior_state_lease is None and not target_exists:
        raise HTTPException(404, f"thread not found: {thread_id}")
    _finalize_state_delete(store, thread_id, state_lease)
    _finalize_group_delete(group_store, thread_id, group_lease)
    _finalize_delete_lease(project_store, thread_id, lease)


def delete_thread_state(
    *,
    store: Any,
    thread_id: str,
    existing: Any,
    actor_id: str | None,
    tenant_id: str | None,
    require_auth: bool,
    workspace_root: Path | str | None,
    logs_root: Path | str | None,
    is_archived: Callable[[str], bool],
    project_store: Any = None,
    group_store: Any = None,
    logger: logging.Logger,
) -> None:
    """Serialize deletion against every live realtime turn before re-reading state."""

    claim_context: Any = nullcontext()
    if logs_root is not None:
        from runtime.platform.process.thread_turn_claim import (
            ThreadTurnClaimConflict,
            ThreadTurnClaimUnavailable,
            acquire_thread_turn_claim,
        )

        try:
            claim_context = acquire_thread_turn_claim(logs_root, thread_id)
        except ThreadTurnClaimConflict as exc:
            raise HTTPException(
                409,
                {
                    "code": "THREAD_TURN_ACTIVE",
                    "message": "wait for the active turn to finish before deleting this thread",
                    "thread_id": thread_id,
                    "active_turn_id": exc.active_turn_id,
                },
            ) from exc
        except ThreadTurnClaimUnavailable as exc:
            raise HTTPException(503, "thread turn deletion lock unavailable") from exc
    with claim_context:
        _delete_thread_state_claimed(
            store=store,
            thread_id=thread_id,
            existing=existing,
            actor_id=actor_id,
            tenant_id=tenant_id,
            require_auth=require_auth,
            workspace_root=workspace_root,
            logs_root=logs_root,
            is_archived=is_archived,
            project_store=project_store,
            group_store=group_store,
            logger=logger,
        )


__all__ = ["delete_thread_state"]
