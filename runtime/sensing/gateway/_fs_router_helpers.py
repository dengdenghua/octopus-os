"""Shared helpers for the filesystem router factory.

Extracted from ``fs_router.py`` (god-file reduction). These were the
closures nested inside ``create_fs_router``; they are reconstructed as
module-level functions that take a ``_FsContext`` carrying the router's
wired stores so the endpoint registration module can call them without
capturing the factory's closure state.
"""

from __future__ import annotations

import base64
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from ._fs_router_models import TREE_IGNORED_DIRS
from ._fs_router_paths import (
    _allowed_fs_roots,
    _assert_within_allowed_roots,
)
from .thread_workspace import verified_managed_workspace


@dataclass
class _FsContext:
    """Stores / flags wired into ``create_fs_router`` and threaded through
    the endpoint handlers."""

    thread_store: Any = None
    identity_store: Any = None
    require_auth: bool = False
    allow_local_workspace_access: bool = False
    jwt_secret: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    workspace_root: Any = None
    workspace_store: Any = None
    lease_store: Any = None
    mount_registry: Any = None
    group_store: Any = None


def _resolved_path(path_value: str | Path) -> Path:
    return Path(path_value).expanduser().resolve(strict=False)


def _add_scope_root(roots: list[Path], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        roots.append(_resolved_path(value.strip()))


def _scope_roots(
    ctx: _FsContext,
    *,
    thread_id: str | None = None,
    workspace_path: str | None = None,
) -> list[Path]:
    # Shared mode has exactly one source of truth: a structurally verified
    # server allocation on an owned thread. Client query/body paths and legacy
    # metadata are intentionally ignored.
    if ctx.require_auth and not ctx.allow_local_workspace_access:
        if ctx.thread_store is None or not thread_id:
            return []
        try:
            thread = None
            if hasattr(ctx.thread_store, "get"):
                thread = ctx.thread_store.get(thread_id)
            if thread is None and hasattr(ctx.thread_store, "get_state"):
                thread = ctx.thread_store.get_state(thread_id)
            metadata = (thread or {}).get("metadata", {}) if thread else {}
            if not isinstance(metadata, dict):
                return []
            managed = verified_managed_workspace(
                ctx.workspace_root,
                thread_id=thread_id,
                metadata=metadata,
            )
            return [managed] if managed is not None else []
        except (OSError, RuntimeError, TypeError, ValueError):
            return []

    roots: list[Path] = []
    if ctx.thread_store is not None and thread_id:
        try:
            thread = None
            if hasattr(ctx.thread_store, "get"):
                thread = ctx.thread_store.get(thread_id)
            if thread is None and hasattr(ctx.thread_store, "get_state"):
                thread = ctx.thread_store.get_state(thread_id)
            metadata = (thread or {}).get("metadata", {}) if thread else {}
            if isinstance(metadata, dict):
                _add_scope_root(roots, metadata.get("workspace_path"))
                extra = metadata.get("extra_workspaces")
                if isinstance(extra, list):
                    for root in extra:
                        _add_scope_root(roots, root)
        except (OSError, TypeError, ValueError):  # noqa: BLE001 — scope root resolution failed; fall through to workspace
            pass
    _add_scope_root(roots, workspace_path)

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _path_in_root(path: Path, root: Path) -> bool:
    if path == root:
        return True
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_in_scope(
    ctx: _FsContext,
    path: Path,
    *,
    thread_id: str | None = None,
    workspace_path: str | None = None,
) -> Path:
    resolved = _resolved_path(path)
    roots = _scope_roots(
        ctx,
        thread_id=thread_id,
        workspace_path=workspace_path,
    )
    if not roots:
        if ctx.require_auth and not ctx.allow_local_workspace_access:
            raise HTTPException(
                403,
                {
                    "error": "managed_workspace_required",
                    "thread_id": thread_id,
                    "hint": "use a server-created authenticated thread workspace",
                },
            )
        # No per-thread workspace scope (the common case for the
        # desktop file browser). Fail CLOSED to the process-wide
        # allowed fs roots (data dir / home / project / explicit
        # ECHO_FS_ALLOWED_ROOTS) instead of returning any absolute
        # path the caller named — the latter was an arbitrary-file
        # read/write primitive on an unauthenticated endpoint.
        return _assert_within_allowed_roots(resolved)
    if any(_path_in_root(resolved, root) for root in roots):
        return resolved
    raise HTTPException(
        403,
        {
            "error": "path_outside_workspace",
            "path": str(resolved),
            "workspace_roots": [str(root) for root in roots],
            "thread_id": thread_id,
        },
    )


def _assert_local_request_scope(
    ctx: _FsContext,
    request: Request,
    *,
    thread_id: str | None,
    workspace_path: str | None,
) -> None:
    """Authorize authenticated local FS use for the configured deployment scope.

    Loopback-local deployments may choose directories before a thread exists.
    Shared deployments require an owned, server-managed thread workspace.
    """
    if not ctx.require_auth or getattr(getattr(request, "state", None), "principal", None) is None:
        return
    principal = request.state.principal
    if not thread_id:
        if ctx.allow_local_workspace_access:
            return
        raise HTTPException(
            403,
            {
                "error": "thread_scope_required",
                "hint": "authenticated local filesystem access requires thread_id",
            },
        )
    thread = None
    if ctx.thread_store is not None:
        if hasattr(ctx.thread_store, "get"):
            try:
                thread = ctx.thread_store.get(thread_id)
            except Exception as exc:
                from runtime.memory.threads import ThreadPermanentlyDeletedError

                if not isinstance(exc, ThreadPermanentlyDeletedError):
                    raise
                raise HTTPException(
                    403,
                    {
                        "error": "managed_workspace_required",
                        "thread_id": thread_id,
                        "hint": "deleted thread workspaces cannot be recreated",
                    },
                ) from exc
        if thread is None and hasattr(ctx.thread_store, "get_state"):
            thread = ctx.thread_store.get_state(thread_id)
    if not isinstance(thread, dict):
        raise HTTPException(404, f"thread not found: {thread_id}")
    raw_metadata = thread.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    owner = metadata.get("owner_actor_id") or metadata.get("actor_id")
    privileged = bool(principal.roles.intersection({"admin", "operator"}))
    if owner != principal.actor_id and not privileged:
        raise HTTPException(404, f"thread not found: {thread_id}")
    stored_tenant = str(metadata.get("tenant_id") or "").strip()
    principal_tenant = str(getattr(principal, "tenant_id", "") or "").strip()
    if not privileged:
        if (
            principal_tenant
            and not principal_tenant.startswith("legacy:")
            and stored_tenant != principal_tenant
        ):
            raise HTTPException(404, f"thread not found: {thread_id}")
        if principal_tenant and stored_tenant and stored_tenant != principal_tenant:
            raise HTTPException(404, f"thread not found: {thread_id}")
    if ctx.allow_local_workspace_access:
        return
    managed = verified_managed_workspace(
        ctx.workspace_root,
        thread_id=thread_id,
        metadata=metadata,
    )
    if managed is None:
        raise HTTPException(
            403,
            {
                "error": "managed_workspace_required",
                "thread_id": thread_id,
                "hint": "thread workspace is missing or not server-managed",
            },
        )
    if workspace_path:
        requested = _resolved_path(workspace_path)
        if not _path_in_root(requested, managed):
            raise HTTPException(403, "workspace_path is outside the thread workspace scope")


def _require_local_thread_scope(
    ctx: _FsContext,
    request: Request,
    *,
    thread_id: str | None,
    workspace_path: str | None = None,
) -> list[Path]:
    """Return the server-authorized local roots for an authenticated thread.

    This is used by endpoints that do not operate on a single path first
    (roots, picker, and import).  They must establish the same owner check as
    read/write before they can expose or create anything on the host.
    """
    _assert_local_request_scope(
        ctx,
        request,
        thread_id=thread_id,
        workspace_path=workspace_path,
    )
    roots = _scope_roots(
        ctx,
        thread_id=thread_id,
        workspace_path=workspace_path,
    )
    if ctx.require_auth and not ctx.allow_local_workspace_access and not roots:
        raise HTTPException(
            403,
            {
                "error": "managed_workspace_required",
                "thread_id": thread_id,
                "hint": "use a server-created authenticated thread workspace",
            },
        )
    return roots


def _parse_workspace_path(
    value: str | None,
) -> tuple[str | None, str]:
    """Split ``workspace_id:/path/to/file`` into ``(workspace_id, path)``.

    Returns ``(None, value)`` when ``value`` doesn't carry a
    ``workspace_id:`` prefix, when the prefix is a single Windows drive
    letter (``C:/...``), or when either side of the ``:`` is empty.

    The workspace_id is *not* validated against ``workspace_store`` here
    — that happens in ``_resolve_remote_workspace`` so callers can choose
    to treat an unknown prefix as a local path (fail-open) or raise.
    """
    if not value or not isinstance(value, str):
        return (None, value or "")
    # Skip Windows drive letters (e.g. "C:/Users/...").
    if (
        len(value) >= 2
        and value[1] == ":"
        and value[0].isalpha()
        and (len(value) == 2 or value[2] in ("/", "\\"))
    ):
        return (None, value)
    if ":" not in value:
        return (None, value)
    workspace_id, _, rest = value.partition(":")
    if not workspace_id or not rest:
        return (None, value)
    return (workspace_id, rest)


def _resolve_remote_workspace(
    ctx: _FsContext,
    workspace_id: str | None,
) -> Any:
    """Look up the Workspace row. Returns ``None`` if the workspace is
    unknown or remote-workspace support is not wired.
    """
    if not workspace_id or ctx.workspace_store is None:
        return None
    try:
        return ctx.workspace_store.get_workspace(workspace_id)
    except Exception:  # noqa: BLE001 — store errors fall through to local path
        return None


def _remote_backend_for(ctx: _FsContext, ws: Any):
    """Get or create the cached MountBackend for a Workspace row.

    Returns ``None`` if no registry is wired or the mount_type has no
    registered adapter.
    """
    if ctx.mount_registry is None or ws is None:
        return None
    try:
        return ctx.mount_registry.get_or_create(
            ws.id, ws.mount_type, ws.mount_target, ws.mount_options
        )
    except KeyError:
        return None
    except Exception:  # noqa: BLE001 — backend init failure
        return None


def _extract_user_id(
    request: Request | None,
    body: dict[str, Any] | None = None,
) -> str | None:
    """User identity for ACL.

    In shared mode the verified Principal is authoritative. The legacy query,
    header, and body forms remain available only for local/dev mode and any
    supplied value must match the Principal when one is present.
    """
    principal = getattr(getattr(request, "state", None), "principal", None)
    principal_actor = getattr(principal, "actor_id", None)
    uid = request.query_params.get("user_id") if request is not None else None
    if not uid and request is not None:
        uid = request.headers.get("X-User-Id") or request.headers.get("x-user-id")
    if not uid and isinstance(body, dict):
        uid = body.get("user_id")
    if isinstance(uid, str):
        uid = uid.strip()
        if principal_actor and uid and uid != principal_actor:
            raise HTTPException(403, "user_id must match the authenticated actor")
        if principal_actor:
            return principal_actor
        return uid if uid else None
    if principal_actor:
        return principal_actor
    return None


def _check_acl(
    ctx: _FsContext,
    request: Request,
    workspace_id: str,
    *,
    write: bool,
    body: dict[str, Any] | None = None,
) -> str:
    """Enforce workspace-level ACL.

    Returns the member's role on success. Raises ``HTTPException(403)``
    if the user is not a member (or ``write`` is requested but the role
    is below ``editor``).
    """
    if ctx.workspace_store is None:
        # Remote-workspace support not wired — caller should have
        # short-circuited before reaching ACL. Fail closed.
        raise HTTPException(
            403,
            {
                "error": "workspace_store_not_configured",
                "workspace_id": workspace_id,
            },
        )
    user_id = _extract_user_id(request, body)
    if not user_id:
        raise HTTPException(
            403,
            {
                "error": "user_id_required",
                "workspace_id": workspace_id,
                "hint": "pass ?user_id= or X-User-Id header",
            },
        )
    principal = getattr(getattr(request, "state", None), "principal", None)
    if principal is not None and getattr(principal, "roles", frozenset()).intersection(
        {"admin", "operator"}
    ):
        return "operator"
    role = ctx.workspace_store.get_member_role(workspace_id, user_id)
    if role is None:
        raise HTTPException(
            403,
            {
                "error": "not_a_member",
                "workspace_id": workspace_id,
                "user_id": user_id,
            },
        )
    if write and role not in ("owner", "editor"):
        raise HTTPException(
            403,
            {
                "error": "write_requires_editor",
                "workspace_id": workspace_id,
                "user_id": user_id,
                "role": role,
                "required": ["owner", "editor"],
            },
        )
    return role


def _check_lease_conflict_or_acquire(
    ctx: _FsContext,
    workspace_id: str,
    file_path: str,
    holder_id: str | None,
) -> None:
    """Pre-write lease gate (Task 6.3).

    If ``holder_id`` is supplied and another holder currently owns an
    exclusive lease on this file, raise ``HTTPException(409)`` with the
    conflict details. If ``holder_id`` is supplied and no conflict
    exists, auto-acquire (or renew) the lease so the write is
    protected against concurrent writers.

    If ``lease_store`` is None or ``holder_id`` is empty, this is a
    no-op — the write proceeds without lease protection (back-compat
    for callers that haven't adopted leases yet).
    """
    if ctx.lease_store is None or not holder_id:
        return
    existing = ctx.lease_store.get_by_path(workspace_id, file_path)
    if existing is not None and existing.holder_id != holder_id:
        raise HTTPException(
            409,
            {
                "error": "lease_conflict",
                "workspace_id": workspace_id,
                "file_path": file_path,
                "holder_id": existing.holder_id,
                "expires_at": existing.expires_at,
                "lease_id": existing.lease_id,
            },
        )
    # Auto-acquire (or renew-in-place for the same holder) so the
    # write is exclusive for the lease TTL window.
    with suppress(Exception):  # noqa: BLE001 — lease acquisition must not block the write
        ctx.lease_store.acquire(
            workspace_id=workspace_id,
            file_path=file_path,
            holder_id=holder_id,
            ttl_seconds=1800,
            kind="exclusive",
        )


def _broadcast_file_written(
    ctx: _FsContext,
    workspace_id: str,
    file_path: str,
    writer_id: str,
    thread_id: str | None,
) -> None:
    """Best-effort broadcast of a ``file_written`` event on the bound
    cowork group's blackboard. Silently skips when ``group_store`` or
    ``thread_id`` is missing."""
    if ctx.group_store is None or not thread_id:
        return
    try:
        from runtime.workspace.cowork_bridge import broadcast_file_written

        broadcast_file_written(
            ctx.group_store,
            thread_id,
            file_path,
            writer_id,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 — broadcast failures must not fail the write
        pass


def _dir_entry_to_tree(
    entry: Any,
    *,
    depth: int,
) -> dict[str, Any]:
    """Convert a MountBackend ``DirEntry`` to the ``FsTreeEntry`` shape."""
    is_dir = bool(getattr(entry, "is_dir", False))
    return {
        "name": getattr(entry, "name", "") or getattr(entry, "path", ""),
        "path": getattr(entry, "path", ""),
        "type": "dir" if is_dir else "file",
        "depth": depth,
        "size": getattr(entry, "size", None),
    }


def _tree_depth_of(entry_path: str, base_path: str) -> int:
    """Compute the relative depth of a MountBackend DirEntry.path
    against the requested base. ``list_dir`` already filters by depth
    internally, so this is a best-effort rendering hint for the UI."""
    try:
        rel = (entry_path or "").strip("/")
        base = (base_path or "").strip("/")
        if base:
            rel = rel[len(base) :].lstrip("/") if rel.startswith(base) else rel
        return rel.count("/") if rel else 0
    except Exception:  # noqa: BLE001
        return 0


# Names that should never appear in a remote tree listing — mirrors
# the local ``TREE_IGNORED_DIRS`` filter for ``.git`` / ``node_modules``
# / ``.echo`` / ``logs`` so a remote workspace gets the same noise
# suppression as a local one.
_REMOTE_IGNORED_DIRS = {  # noqa: N806 — intentional constant
    ".git",
    "node_modules",
    ".echo",
    "logs",
}


def _is_ignored_remote_dir(entry: Any) -> bool:
    name = (getattr(entry, "name", "") or "").casefold()
    return bool(getattr(entry, "is_dir", False)) and name in _REMOTE_IGNORED_DIRS


def _serialize_tree_entry(
    root: Path,
    entry: Path,
    *,
    depth: int,
) -> dict[str, Any]:
    rel_path = entry.relative_to(root).as_posix()
    is_dir = entry.is_dir()
    size = None if is_dir else entry.stat().st_size
    return {
        "name": entry.name,
        "path": rel_path,
        "type": "dir" if is_dir else "file",
        "depth": depth,
        "size": size,
    }


def _should_skip_tree_entry(entry: Path, *, include_ignored: bool) -> bool:
    if include_ignored or not entry.is_dir():
        return False
    return entry.name.casefold() in TREE_IGNORED_DIRS


def _walk_tree(
    root: Path,
    *,
    max_depth: int,
    include_ignored: bool = False,
) -> list[dict[str, Any]]:
    if not root.exists() or not root.is_dir():
        raise HTTPException(404, f"directory not found: {root}")

    entries: list[dict[str, Any]] = []

    def _recurse(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(
                current.iterdir(),
                # Dirs first, then files, both alpha-sorted.
                key=lambda c: (not c.is_dir(), c.name.lower()),
            )
        except OSError as exc:
            if depth == 0:
                raise HTTPException(
                    500,
                    f"failed to list directory: {exc}",
                ) from exc
            return
        for child in children:
            if _should_skip_tree_entry(
                child,
                include_ignored=include_ignored,
            ):
                continue
            try:
                serialized = _serialize_tree_entry(root, child, depth=depth)
                child_is_dir = child.is_dir()
            except OSError:
                continue
            entries.append(serialized)
            if child_is_dir and depth < max_depth:
                _recurse(child, depth + 1)

    _recurse(root, 0)
    return entries


def _filesystem_roots(scope_roots: list[Path] | None = None) -> list[dict[str, Any]]:
    # Never enumerate filesystem roots (/, drive letters, or $HOME).  The
    # browser may only start at policy roots, or at the already-authorized
    # workspace roots supplied by the authenticated thread endpoint.
    candidates = list(scope_roots) if scope_roots is not None else _allowed_fs_roots()

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            root = candidate.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if not root.exists() or not root.is_dir():
            continue
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        name = root.anchor.rstrip("\\/") or root.name or str(root)
        entries.append(
            {
                "name": name,
                "path": str(root),
                "type": "dir",
                "depth": 0,
                "size": None,
            },
        )
    return entries


def _pick_directory_windows(default_path: str | None) -> str | None:
    selected_path = ""
    if default_path:
        try:
            candidate = Path(default_path).expanduser().resolve(strict=False)
            if candidate.exists() and candidate.is_dir():
                selected_path = str(candidate)
        except (OSError, RuntimeError):
            selected_path = ""

    selected_path_b64 = base64.b64encode(selected_path.encode("utf-8")).decode("ascii")
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择工作区文件夹'
$dialog.ShowNewFolderButton = $true
$selectedPath = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{selected_path_b64}'))
if ($selectedPath) {{ $dialog.SelectedPath = $selectedPath }}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::Out.Write($dialog.SelectedPath)
}}
"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "folder picker failed")
    value = completed.stdout.strip()
    return value or None


def _pick_directory_macos(default_path: str | None) -> str | None:
    script = """
on run argv
  set startPath to item 1 of argv
  try
    activate
    if startPath is "" then
      set pickedFolder to choose folder with prompt "选择工作区文件夹"
    else
      set pickedFolder to choose folder with prompt "选择工作区文件夹" default location POSIX file startPath
    end if
    return POSIX path of pickedFolder
  on error number -128
    return ""
  end try
end run
"""
    completed = subprocess.run(
        ["osascript", "-e", script, default_path or ""],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "folder picker failed")
    value = completed.stdout.strip()
    if value != "/":
        value = value.rstrip("/")
    return value or None


def _pick_directory_tk(default_path: str | None) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        kwargs: dict[str, Any] = {"title": "选择工作区文件夹"}
        if default_path:
            kwargs["initialdir"] = default_path
        selected = filedialog.askdirectory(**kwargs)
        return selected or None
    finally:
        root.destroy()
