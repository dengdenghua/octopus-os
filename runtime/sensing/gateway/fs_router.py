"""
Filesystem router · ``/api/fs/{tree,read,write}``.

Extracted from the monolithic ``runtime/platform/ui/app.py`` in the
app.py-split campaign. Hosts the raw directory-tree / read-file /
write-file endpoints used by the desktop workspace's file browser
and editor panels.

Endpoints
---------

    GET  /api/fs/tree   · directory tree (bounded depth)
    GET  /api/fs/read   · file contents (bounded line count)
    POST /api/fs/write  · overwrite / create file

Scope note
----------

In local single-user mode the file browser can keep its historical
user-chosen-directory behaviour, bounded by ``ECHO_FS_ALLOWED_ROOTS``.
An authenticated UI bound to loopback can explicitly retain that same local
behaviour. In authenticated shared deployments every local operation is bound
to an owned thread workspace and remains inside the configured allowed roots.
Remote workspaces use their own membership ACL.

If that separation ever changes, add a ``scope`` parameter to
``create_fs_router`` and route through ``resolve_write_scope`` at
handler entry. The tests in ``test_app_fs_endpoints.py`` would
then get updated to assert rejection of out-of-scope writes.

Destructive endpoints (/api/fs/revert) go through
``_assert_writable_root`` which restricts the target to the
configured allowed roots (``ECHO_FS_ALLOWED_ROOTS`` env var,
colon- or semicolon-separated; falls back to ``$ECHO_DATA_DIR``
and CWD).

Split notes
-----------

The god-file body was split into satellite modules (same directory):

    ``_fs_router_models.py``     response schemas + tree-ignore set
    ``_fs_router_paths.py``      allowed-roots / path helpers
    ``_fs_router_diff.py``       unified-diff reverse-apply
    ``_fs_router_helpers.py``    shared endpoint helpers + ``_FsContext``
    ``_fs_router_endpoints.py``  ``/api/fs`` + ``/api/git`` handlers

This module keeps ``create_fs_router`` and re-exports the satellite
symbols so existing importers (including ``realtime_thread_ops``, which
imports ``_reverse_unified_diff`` / ``_DiffApplyConflict`` /
``_DiffFormatError``) are unchanged.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from runtime.safety.auth.principal import resolve_principal
from runtime.sensing._fastapi_guard import require_fastapi

from ._fs_router_diff import (
    _DiffApplyConflict,
    _DiffFormatError,
    _parse_unified_diff,
    _ParsedDiffHunk,
    _reverse_unified_diff,
)
from ._fs_router_endpoints import register_endpoints
from ._fs_router_helpers import _FsContext
from ._fs_router_models import (
    TREE_IGNORED_DIRS,
    FsImportDirectoryResponse,
    FsPickDirectoryResponse,
    FsReadResponse,
    FsRootsResponse,
    FsTreeEntry,
    FsTreeResponse,
    FsWriteResponse,
)
from ._fs_router_paths import (
    _allowed_fs_roots,
    _assert_within_allowed_roots,
    _safe_relative_parts,
)

__all__ = [
    # public entry point
    "create_fs_router",
    # response schemas
    "FsTreeEntry",
    "FsTreeResponse",
    "FsRootsResponse",
    "FsReadResponse",
    "FsWriteResponse",
    "FsImportDirectoryResponse",
    "FsPickDirectoryResponse",
    "TREE_IGNORED_DIRS",
    # path / root helpers
    "_allowed_fs_roots",
    "_safe_relative_parts",
    "_assert_within_allowed_roots",
    # diff helpers (re-exported for realtime_thread_ops)
    "_DiffFormatError",
    "_DiffApplyConflict",
    "_ParsedDiffHunk",
    "_parse_unified_diff",
    "_reverse_unified_diff",
]


def create_fs_router(
    thread_store: Any = None,
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    allow_local_workspace_access: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    workspace_root: Any = None,
    workspace_store: Any = None,
    lease_store: Any = None,
    mount_registry: Any = None,
    group_store: Any = None,
) -> Any:
    """Build the FastAPI router. State is per-request (the path
    parameter); auth, when an identity store is wired and ``require_auth``
    is set, is enforced once at the router level for every fs endpoint.

    Remote-workspace routing (Task 6):

      When ``workspace_store`` + ``mount_registry`` are wired, endpoints
      accept a ``workspace_id:`` prefix on the ``path`` parameter (e.g.
      ``ws-abc123:/src/main.py``). The prefix selects a registered
      Workspace; the remainder is the path within the workspace's mount,
      routed through ``MountBackend.read_file`` / ``write_file`` /
      ``list_dir`` instead of the local filesystem.

      ACL (Task 7) is enforced on remote-workspace ops. In authenticated
      shared mode local paths additionally require an owned thread scope.
      ``user_id`` is only a compatibility input in local/dev mode or is
      checked against the verified Principal; it never overrides identity.

      Lease checks (Task 6.3): on writes, if ``holder_id`` is supplied,
      an existing exclusive lease held by *another* holder returns 409
      with the conflict details; otherwise the lease is auto-acquired
      (or renewed in place for the same holder).

      Broadcast (Task 6.4): after a successful write, if ``group_store``
      + ``thread_id`` are supplied, ``broadcast_file_written`` appends a
      ``file_written`` entry to the thread's shared blackboard.

    The endpoint bodies live in ``_fs_router_endpoints.register_endpoints``
    (see the module docstring for the extraction map).
    """
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        # Router-level gate: applies to every fs endpoint at once. A
        # no-op when require_auth is False (returns None without raising),
        # so unauthenticated local-dev use is unchanged; raises 401 when
        # auth is required and no valid actor is presented.
        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if principal is not None:
            request.state.principal = principal

    router = APIRouter(tags=["fs"], dependencies=[Depends(_auth_dep)])

    ctx = _FsContext(
        thread_store=thread_store,
        identity_store=identity_store,
        require_auth=require_auth,
        allow_local_workspace_access=allow_local_workspace_access,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        workspace_root=workspace_root,
        workspace_store=workspace_store,
        lease_store=lease_store,
        mount_registry=mount_registry,
        group_store=group_store,
    )
    register_endpoints(router, ctx)
    return router
