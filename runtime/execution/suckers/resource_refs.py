"""Attach portable workspace identities to Agent file observations.

File tools still expose their resolved path for compatibility, but a task can
also carry a stable ``workspace-file:v1`` reference when the host supplied a
managed workspace.  The helper is deliberately additive and conservative:
paths outside the session workspace, paths without a thread identity, and
unrecognised workspace areas simply keep their legacy path-only response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from echo_runtime.resource_identity import workspace_file_resource_id
from runtime.platform.process.scope import ExecutionScope, resolve_execution_scope
from runtime.platform.process.session import Session, current_session

_AREA_PREFIXES: tuple[tuple[str, str], ...] = (
    ("output/final", "final"),
    ("output/stages", "stages"),
    ("output", "output"),
    ("deploy", "deploy"),
    ("upload", "upload"),
)


def workspace_resource_scope(session: Session | None = None) -> ExecutionScope | None:
    """Resolve the current read scope once for one tool invocation."""

    active = session or current_session()
    return resolve_execution_scope(active) if active is not None else None


def workspace_resource_id(
    path: str | Path,
    *,
    session: Session | None = None,
    execution_scope: ExecutionScope | None = None,
) -> str | None:
    """Return a resource id for an authorised file in the active workspace."""

    active = session or current_session()
    if active is None or not active.thread_id:
        return None
    metadata = active.metadata or {}
    raw_root = metadata.get("workspace_path")
    if not isinstance(raw_root, str) or not raw_root.strip():
        # Some legacy realtime sessions only carry the server-owned final
        # artifact root.  Recovering its two-level workspace parent is safe
        # because the candidate must still pass the resolved execution scope.
        artifact_root = metadata.get("_artifact_output_root")
        if not isinstance(artifact_root, str) or not artifact_root.strip():
            return None
        try:
            raw_root = str(Path(artifact_root).expanduser().resolve().parent.parent)
        except (OSError, RuntimeError, ValueError):
            return None
    try:
        root = Path(raw_root).expanduser().resolve()
        candidate = Path(path).expanduser().resolve()
        if candidate.is_dir():
            return None
        relative = candidate.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None

    # Keep identity generation behind the same read boundary used by tools.
    try:
        scope = execution_scope or resolve_execution_scope(active)
        if not scope.allows_read(candidate):
            return None
    except (OSError, RuntimeError, ValueError):
        return None

    for prefix, area in _AREA_PREFIXES:
        if relative == prefix:
            return None
        marker = f"{prefix}/"
        if relative.startswith(marker):
            locator = relative[len(marker) :]
            if not locator:
                return None
            try:
                return workspace_file_resource_id(active.thread_id, area, locator)
            except ValueError:
                return None
    return None


def with_workspace_resource(
    result: dict[str, Any],
    path: str | Path,
    *,
    session: Session | None = None,
    execution_scope: ExecutionScope | None = None,
) -> dict[str, Any]:
    """Add one portable identity without replacing a server-provided one.

    Some native tool adapters use camelCase while the wire contract uses
    snake_case. Treat both spellings as the same identity so an adapter does
    not emit redundant fields for one file.
    """

    if not isinstance(result, dict) or result.get("error"):
        return result
    if result.get("resource_id") or result.get("resourceId"):
        return result
    resource_id = workspace_resource_id(
        path,
        session=session,
        execution_scope=execution_scope,
    )
    if resource_id is None:
        return result
    return {**result, "resource_id": resource_id}


__all__ = [
    "workspace_resource_id",
    "workspace_resource_scope",
    "with_workspace_resource",
]
