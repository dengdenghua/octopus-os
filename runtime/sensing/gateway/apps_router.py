from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from runtime.execution.misc.agent_packs import scan_agent_pack
from runtime.platform.process.paths import project_root

_LOG = logging.getLogger(__name__)


def _default_app_roots() -> list[Path]:
    root = project_root(Path(__file__))
    return [
        root / ".echo" / "plugins" / "codex",
        Path.home() / ".echo" / "plugins" / "codex",
    ]


def _author_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        name = value.get("name")
        return str(name).strip() if name else None
    return None


def _app_author(plugin: Any) -> str | None:
    if plugin is None:
        return None
    return _author_name(plugin.metadata.get("author")) or "echo"


def discover_apps(roots: list[Path] | None = None) -> list[dict[str, Any]]:
    apps: dict[str, dict[str, Any]] = {}
    for root in roots or _default_app_roots():
        if not root.is_dir():
            continue
        candidates = [path.parent.parent for path in root.glob("*/.codex-plugin/plugin.json")]
        candidates.extend(path.parent.parent for path in root.glob("*/.claude-plugin/plugin.json"))
        for plugin_dir in sorted(set(candidates)):
            try:
                preview = scan_agent_pack(plugin_dir)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "skipping app plugin pack %s after scan failure: %s",
                    plugin_dir,
                    exc,
                )
                continue
            for app in preview.apps:
                plugin = next(
                    (item for item in preview.plugins if item.id == app.source_plugin), None
                )
                app_id = app.name
                apps[app_id] = {
                    "id": app_id,
                    "name": app.metadata.get("title") or app.name,
                    "description": app.description,
                    "author": _app_author(plugin),
                    "plugin": plugin.name if plugin else app.source_plugin,
                    "source_plugin": app.source_plugin,
                    "path": app.path,
                    "route": app.metadata.get("route"),
                    "entry": app.metadata.get("entry"),
                    "icon": app.metadata.get("icon"),
                    "category": app.metadata.get("category") or "connector",
                    "schema_version": app.metadata.get("schema_version"),
                    "connector_id": app.metadata.get("connector_id"),
                    "permissions": app.metadata.get("permissions") or [],
                    "actions": app.metadata.get("actions") or [],
                    "action_count": app.metadata.get("action_count")
                    or len(app.metadata.get("actions") or []),
                }
    return sorted(apps.values(), key=lambda item: str(item["name"]).lower())


def create_apps_router(
    *,
    app_roots: list[Path] | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    def _auth_dep(request: Request) -> None:
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["apps"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/apps")
    def _apps() -> list[dict[str, Any]]:
        return discover_apps(app_roots)

    @router.get("/api/apps/{app_id}")
    def _app_get(app_id: str) -> dict[str, Any]:
        for app in discover_apps(app_roots):
            if app["id"] == app_id:
                return app
        raise HTTPException(status_code=404, detail="app not found")

    return router


__all__ = ["create_apps_router", "discover_apps"]
