"""Debug diagnostics router · ``/api/debug/session-info``.

Returns the current execution context for a thread so the frontend
can show users exactly what the agent sees: cwd, scope roots,
sandbox mode, project type, session metadata, rejected paths, etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, Query, Request
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = Any  # type: ignore[assignment, misc]
    BaseModel = object  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


def create_debug_router(
    *,
    store: Any = None,
    stack: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["debug"], dependencies=[Depends(_operator_dep)])

    @router.get("/api/debug/session-info")
    def api_debug_session_info(
        thread_id: str = Query(default=""),
        workspace_path: str = Query(default=""),
    ) -> dict[str, Any]:
        info: dict[str, Any] = {
            "thread_id": thread_id,
            "workspace_path": workspace_path,
        }

        if workspace_path:
            root = Path(workspace_path)
            info["workspace_exists"] = root.is_dir()
            info["workspace_resolved"] = str(root.resolve()) if root.exists() else None

            try:
                from runtime.execution.suckers.verify_skills import detect_project

                profile = detect_project(workspace_path)
                info["project"] = {
                    "kind": profile.kind,
                    "checks": [c["name"] for c in profile.checks],
                }
            except Exception:
                info["project"] = {"kind": "unknown", "checks": []}

            rules_files = [".echo/rules.md", ".cursorrules", "CLAUDE.md"]
            info["rules_file"] = None
            for name in rules_files:
                if (root / name).is_file():
                    info["rules_file"] = name
                    break

            info["git_initialized"] = (root / ".git").is_dir()

        if store and thread_id:
            try:
                state = store.get(thread_id)
                meta = state.get("metadata", {}) if state else {}
                info["thread_metadata"] = {
                    "mode": meta.get("mode"),
                    "sandbox_mode": meta.get("sandbox_mode"),
                    "workspace_path": meta.get("workspace_path"),
                    "extra_workspaces": meta.get("extra_workspaces"),
                    "agent_name": meta.get("agent_name"),
                }
            except Exception:
                info["thread_metadata"] = None

        if stack:
            try:
                scope_info: dict[str, Any] = {}
                from runtime.platform.process.session import Session

                session = Session(
                    thread_id=thread_id or "debug",
                    metadata={
                        "workspace_path": workspace_path,
                        "mode": "code",
                        "sandbox_mode": "full",
                    },
                )
                from runtime.platform.process.scope import resolve_write_scope

                scope = resolve_write_scope(session)
                scope_info["mode"] = scope.mode
                scope_info["roots"] = [str(r) for r in scope.roots]
                scope_info["requested_mode"] = scope.requested_mode
                info["write_scope"] = scope_info
            except Exception as exc:
                info["write_scope"] = {"error": str(exc)}

        import os
        import sys

        info["server_cwd"] = os.getcwd()
        info["python_executable"] = sys.executable

        return info

    return router


__all__ = ["create_debug_router"]
