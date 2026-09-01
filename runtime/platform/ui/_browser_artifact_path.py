"""Tenant-aware path resolution for browser screenshot artifacts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException


def resolve_browser_artifact_path(
    filename: str,
    *,
    principal: Any,
    require_auth: bool,
    authorize_legacy: Callable[[], None],
) -> Path:
    from runtime.execution.suckers.browser_act_skills import _artifacts_root

    clean = Path(filename).name
    if not clean.endswith(".png") or "/" in filename or "\\" in filename:
        raise HTTPException(404, "not found")
    if require_auth:
        if principal is None:
            raise HTTPException(401, "authenticated browser principal required")
        path = (
            _artifacts_root(
                tenant_id=principal.tenant_id,
                owner_actor_id=principal.actor_id,
            )
            / clean
        )
        if not path.is_file():
            # Only the control plane may reach pre-tenant legacy artifacts.
            authorize_legacy()
            path = _artifacts_root() / clean
    else:
        path = _artifacts_root() / clean
    if not path.is_file():
        raise HTTPException(404, "artifact not found")
    return path


__all__ = ["resolve_browser_artifact_path"]
