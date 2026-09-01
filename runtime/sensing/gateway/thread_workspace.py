"""Server-managed workspace paths for authenticated thread filesystem access.

Authenticated clients may supply presentation metadata, but never the host path
that an API thread can read or write.  This module is the shared contract between
the thread-state router (which allocates a workspace) and the filesystem router
(which verifies it before touching the host filesystem).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.runtime_policy.workspaces import (
    MANAGED_WORKSPACE_DELETION_KEY,
    MANAGED_WORKSPACE_DELETION_MARKER,
    MANAGED_WORKSPACE_MARKER,
    MANAGED_WORKSPACE_METADATA_KEY,
    PROTECTED_WORKSPACE_METADATA_KEYS,
    managed_workspace_metadata,
    managed_workspace_path,
    strip_client_workspace_metadata,
    verified_managed_workspace,
)

_DELETION_TRASH_DIR = ".trash"


@dataclass(frozen=True)
class ManagedWorkspaceDeletion:
    """A server-derived, retryable workspace deletion staging area."""

    workspace: Path
    container: Path
    payload: Path
    payload_identity: tuple[int, int] | None


def _directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise RuntimeError("managed workspace deletion path is not a directory")
    return current.st_dev, current.st_ino


def _deletion_paths(
    workspace_root: str | Path,
    workspace: Path,
) -> tuple[Path, Path]:
    root = Path(workspace_root).expanduser().resolve(strict=False)
    try:
        relative = workspace.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("managed workspace deletion escaped its root") from exc
    container = root / _DELETION_TRASH_DIR / relative
    payload = container / "workspace"
    # Refuse a pre-existing symlink anywhere in the reserved deletion tree.
    if container.resolve(strict=False) != container:
        raise RuntimeError("managed workspace deletion path contains a symlink")
    return container, payload


def stage_managed_workspace_deletion(
    workspace_root: str | Path | None,
    *,
    thread_id: str,
    metadata: dict[str, Any],
) -> ManagedWorkspaceDeletion:
    """Atomically isolate a verified workspace below the managed trash root.

    Both the source and tombstone are derived from server-persisted scope
    metadata.  Client paths are never accepted.  A deterministic tombstone
    makes an interrupted deletion retryable without following symlinks or
    reaching outside ``workspace_root``.
    """
    if workspace_root is None:
        raise RuntimeError("managed workspace deletion service unavailable")
    workspace = verified_managed_workspace(
        workspace_root,
        thread_id=thread_id,
        metadata=metadata,
        allow_deleting=True,
    )
    if workspace is None:
        raise PermissionError("managed workspace deletion verification failed")
    if metadata.get(MANAGED_WORKSPACE_DELETION_KEY) != MANAGED_WORKSPACE_DELETION_MARKER:
        raise RuntimeError("managed workspace deletion was not persisted")

    container, payload = _deletion_paths(workspace_root, workspace)
    workspace_identity = _directory_identity(workspace)
    payload_identity = _directory_identity(payload)
    if workspace_identity is not None and payload_identity is not None:
        raise RuntimeError("managed workspace exists in both active and deletion paths")
    if payload_identity is not None:
        return ManagedWorkspaceDeletion(workspace, container, payload, payload_identity)
    if workspace_identity is None:
        # A previous attempt may have removed the payload but failed before the
        # state tombstone was deleted.  The empty container is safe to reap.
        if _directory_identity(container) is not None:
            try:
                container.rmdir()
            except OSError as exc:
                raise RuntimeError("managed workspace deletion tombstone is not empty") from exc
        return ManagedWorkspaceDeletion(workspace, container, payload, None)

    container.parent.mkdir(parents=True, exist_ok=True)
    if container.parent.resolve(strict=False) != container.parent:
        raise RuntimeError("managed workspace deletion parent contains a symlink")
    try:
        container.mkdir()
    except FileExistsError:
        # A concurrent/retried request owns the deterministic tombstone.  It
        # either completes shortly or the next request resumes it.
        if _directory_identity(container) is None:
            raise RuntimeError("managed workspace deletion tombstone disappeared") from None
        payload_identity = _directory_identity(payload)
        if payload_identity is not None and _directory_identity(workspace) is None:
            return ManagedWorkspaceDeletion(workspace, container, payload, payload_identity)
        raise RuntimeError("managed workspace deletion is already in progress") from None

    try:
        os.rename(workspace, payload)
    except OSError:
        with contextlib.suppress(OSError):
            container.rmdir()
        raise
    moved_identity = _directory_identity(payload)
    if moved_identity != workspace_identity:
        # The source was swapped between lstat and rename.  Never recurse into
        # the replacement; restore it only when the original name is free.
        if _directory_identity(workspace) is None:
            with contextlib.suppress(OSError):
                os.rename(payload, workspace)
        raise RuntimeError("managed workspace changed during deletion staging")
    return ManagedWorkspaceDeletion(workspace, container, payload, moved_identity)


def discard_staged_managed_workspace(token: ManagedWorkspaceDeletion) -> None:
    """Remove one staged managed workspace, raising on any residual data."""
    current_identity = _directory_identity(token.payload)
    if current_identity is not None:
        if token.payload_identity is not None and current_identity != token.payload_identity:
            raise RuntimeError("managed workspace deletion tombstone was replaced")
        shutil.rmtree(token.payload)
    if _directory_identity(token.payload) is not None:
        raise RuntimeError("managed workspace deletion left residual data")
    try:
        token.container.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("managed workspace deletion tombstone is not empty") from exc
    # These parents contain only opaque scope segments.  Empty-parent cleanup
    # is best-effort and never crosses the reserved trash directory.
    trash_root = token.container.parents[2]
    for parent in (token.container.parent, token.container.parent.parent):
        if parent == trash_root:
            break
        try:
            parent.rmdir()
        except OSError:
            break


def _create_workspace_directory(path: Path) -> tuple[int, int]:
    """Create *path* and return the identity of the directory we created.

    The device/inode pair lets failure compensation distinguish this request's
    empty directory from a path that another process replaced or took over.
    Parent directories are deliberately outside the cleanup token: they may be
    shared by other actors and are never removed by rollback.
    """
    path.mkdir(parents=True, exist_ok=False)
    created = path.lstat()
    if stat.S_ISLNK(created.st_mode) or not stat.S_ISDIR(created.st_mode):
        raise RuntimeError("managed workspace allocation did not create a directory")
    return created.st_dev, created.st_ino


def _remove_workspace_directory_if_unchanged(
    path: Path,
    identity: tuple[int, int],
) -> bool:
    """Remove only the same, still-empty directory created by this request."""
    try:
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            return False
        if (current.st_dev, current.st_ino) != identity:
            return False
        path.rmdir()
    except (FileNotFoundError, NotADirectoryError, OSError):
        # ``rmdir`` intentionally fails for non-empty/taken-over directories.
        # Never recurse during compensation: their contents may belong to a
        # concurrent request or to an operator investigating the failure.
        return False
    return True


def ensure_managed_thread_workspace(
    workspace_root: str | Path | None,
    *,
    thread_id: str,
    actor_id: str,
    tenant_id: str,
    store: Any,
) -> Path:
    """Return (and, for a new thread, allocate) its authenticated workspace.

    This is the realtime counterpart of the authenticated HTTP thread-create
    boundary.  Existing server allocations are structurally re-verified; an
    unallocated legacy/new thread is claimed for the authenticated principal
    and assigned the deterministic tenant/actor/thread path.  Any forged
    marker, ownership mismatch or unavailable persistence service fails
    closed.
    """
    if workspace_root is None or store is None:
        raise RuntimeError("authenticated thread workspace service unavailable")
    actor = str(actor_id or "").strip()
    tenant = str(tenant_id or "").strip()
    if not actor or not tenant:
        raise RuntimeError("authenticated thread principal is incomplete")
    if (
        not hasattr(store, "get")
        or not hasattr(store, "ensure_thread")
        or not hasattr(store, "update_state")
    ):
        raise RuntimeError("authenticated thread workspace persistence unavailable")

    def _metadata(thread: Any) -> dict[str, Any]:
        raw = thread.get("metadata") if isinstance(thread, dict) else None
        return dict(raw) if isinstance(raw, dict) else {}

    existing = store.get(thread_id)
    metadata = _metadata(existing)
    stored_actor = str(metadata.get("owner_actor_id") or metadata.get("actor_id") or "").strip()
    stored_tenant = str(metadata.get("tenant_id") or "").strip()
    if stored_actor and stored_actor != actor:
        raise PermissionError("authenticated thread workspace ownership mismatch")
    if stored_tenant and stored_tenant != tenant:
        raise PermissionError("authenticated thread workspace tenant mismatch")

    marker = metadata.get(MANAGED_WORKSPACE_METADATA_KEY)
    if marker is not None:
        if metadata.get(MANAGED_WORKSPACE_DELETION_KEY) == MANAGED_WORKSPACE_DELETION_MARKER:
            raise RuntimeError("authenticated thread workspace deletion is in progress")
        workspace = verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=metadata,
        )
        if workspace is None or stored_actor != actor or stored_tenant != tenant:
            raise PermissionError("authenticated thread workspace verification failed")
        return workspace

    allocation = managed_workspace_metadata(
        workspace_root,
        tenant_id=tenant,
        actor_id=actor,
        thread_id=thread_id,
    )
    workspace = Path(allocation["workspace_path"])
    try:
        directory_identity = _create_workspace_directory(workspace)
    except FileExistsError:
        # A concurrent HTTP create is acceptable only when it also persisted
        # the exact server allocation.  Otherwise a pre-created directory is
        # ambiguous (and could be an attempted path-alias attack).
        current = store.get(thread_id)
        current_metadata = _metadata(current)
        verified = verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=current_metadata,
        )
        if (
            verified is not None
            and current_metadata.get("owner_actor_id") == actor
            and current_metadata.get("tenant_id") == tenant
        ):
            return verified
        raise RuntimeError("managed thread workspace already exists") from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("managed thread workspace allocation failed") from exc

    def _recover_persisted_workspace() -> Path | None:
        """Accept an update that committed before its adapter raised."""
        try:
            current = store.get(thread_id)
            current_metadata = _metadata(current)
            verified = verified_managed_workspace(
                workspace_root,
                thread_id=thread_id,
                metadata=current_metadata,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        if (
            verified is not None
            and current_metadata.get("owner_actor_id") == actor
            and current_metadata.get("tenant_id") == tenant
        ):
            return verified
        return None

    authoritative_metadata: dict[str, Any] = {
        **allocation,
        "owner_actor_id": actor,
        "tenant_id": tenant,
        # Clear legacy path grants when migrating a thread. ThreadStateStore's
        # metadata updates are merge-based, so omission alone would retain
        # these old client-controlled authorities.
        "extra_workspaces": [],
        "personal_workspace_path": "",
        "allowed_write_paths": [],
        "attachment_read_roots": [],
        "_artifact_output_root": "",
        "cwd": "",
    }
    try:
        if existing is None:
            current = store.ensure_thread(thread_id, metadata=authoritative_metadata)
            current_metadata = _metadata(current)
            # ``ensure_thread`` may have raced with another creator. Do not
            # overwrite a principal that appeared after our first read.
            raced_actor = str(
                current_metadata.get("owner_actor_id") or current_metadata.get("actor_id") or ""
            ).strip()
            raced_tenant = str(current_metadata.get("tenant_id") or "").strip()
            if raced_actor and raced_actor != actor:
                raise PermissionError("authenticated thread workspace ownership mismatch")
            if raced_tenant and raced_tenant != tenant:
                raise PermissionError("authenticated thread workspace tenant mismatch")
            if current_metadata.get(MANAGED_WORKSPACE_METADATA_KEY) != MANAGED_WORKSPACE_MARKER:
                store.update_state(thread_id, metadata=authoritative_metadata)
        else:
            store.update_state(thread_id, metadata=authoritative_metadata)
        persisted = store.get(thread_id)
    except Exception as exc:  # noqa: BLE001 - compensate every ordinary adapter failure
        recovered = _recover_persisted_workspace()
        if recovered is not None:
            return recovered
        _remove_workspace_directory_if_unchanged(workspace, directory_identity)
        if isinstance(exc, PermissionError):
            raise
        raise RuntimeError("managed thread workspace persistence failed") from exc

    persisted_metadata = _metadata(persisted)
    verified = verified_managed_workspace(
        workspace_root,
        thread_id=thread_id,
        metadata=persisted_metadata,
    )
    if (
        verified is None
        or persisted_metadata.get("owner_actor_id") != actor
        or persisted_metadata.get("tenant_id") != tenant
    ):
        # A custom store may return a stale/empty value immediately after a
        # successful update. Re-read before compensating so a committed
        # allocation is accepted instead of having its directory removed.
        recovered = _recover_persisted_workspace()
        if recovered is not None:
            return recovered
        _remove_workspace_directory_if_unchanged(workspace, directory_identity)
        raise RuntimeError("managed thread workspace persistence verification failed")
    return verified


__all__ = [
    "MANAGED_WORKSPACE_DELETION_KEY",
    "MANAGED_WORKSPACE_DELETION_MARKER",
    "MANAGED_WORKSPACE_MARKER",
    "MANAGED_WORKSPACE_METADATA_KEY",
    "PROTECTED_WORKSPACE_METADATA_KEYS",
    "ManagedWorkspaceDeletion",
    "discard_staged_managed_workspace",
    "ensure_managed_thread_workspace",
    "managed_workspace_metadata",
    "managed_workspace_path",
    "strip_client_workspace_metadata",
    "stage_managed_workspace_deletion",
    "verified_managed_workspace",
]
