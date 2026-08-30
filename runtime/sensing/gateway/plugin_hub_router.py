"""PluginHub management REST API.

Provides CRUD + lifecycle control endpoints for the frontend plugin page.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from runtime.platform.plugins.plugin_hub import PluginHub


def create_plugin_hub_router(
    hub: PluginHub | None = None,
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create a FastAPI router with PluginHub management endpoints.

    Parameters
    ----------
    hub : PluginHub or None
        The PluginHub instance. If ``None``, uses the singleton.
    """
    if hub is None:
        hub = PluginHub.get()

    def _auth_dep(request: Request) -> None:
        # Plugin lifecycle/config control is an operator-only surface in
        # shared deployments. Dev mode remains open when require_auth is off.
        if require_auth and identity_store is None:
            raise HTTPException(401, "identity store required for plugin hub auth")
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        prefix="/api/plugin-hub",
        tags=["plugin-hub"],
        dependencies=[Depends(_auth_dep)],
    )

    # ── List / Discover ────────────────────────────────────────

    @router.get("/plugins")
    def list_plugins():
        """Return metadata for all loaded plugins."""
        return hub.list_plugins()

    @router.get("/plugins/discover", dependencies=[Depends(_operator_dep)])
    def discover_plugins():
        """Scan plugin directories and return unloaded plugin candidates."""
        return hub.discover()

    @router.get("/contributions")
    def list_contributions(kind: str | None = None, owner: str | None = None):
        """List public metadata for active Echo plugin contributions."""

        return [row.to_public() for row in hub.contribution_registry.list(kind=kind, owner=owner)]

    # ── Lifecycle control ──────────────────────────────────────

    def _lifecycle_error(exc: Exception) -> HTTPException:
        message = str(exc)
        if isinstance(exc, KeyError):
            return HTTPException(404, message)
        if isinstance(exc, FileExistsError) or "not installed" in message:
            return HTTPException(409, message)
        if isinstance(exc, ValueError):
            return HTTPException(400, message)
        return HTTPException(400, message)

    def _payload_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
        if key not in payload:
            return default
        value = payload[key]
        if not isinstance(value, bool):
            raise HTTPException(400, f"{key} must be a boolean")
        return value

    @router.post("/plugins/{name}/install", dependencies=[Depends(_operator_dep)])
    def install_plugin(name: str, body: dict[str, Any] | None = None):
        """Activate a factory workbench and optionally restore trashed works."""

        payload = body or {}
        recovery_value = payload.get("recovery_id")
        if recovery_value is not None and not isinstance(recovery_value, str):
            raise HTTPException(400, "recovery_id must be a string")
        try:
            return hub.install_plugin(
                name,
                enabled=_payload_bool(payload, "enabled", True),
                restore_data=_payload_bool(payload, "restore_data", False),
                recovery_id=recovery_value,
            )
        except (KeyError, ValueError, FileExistsError, RuntimeError) as exc:
            raise _lifecycle_error(exc) from exc

    @router.post("/plugins/{name}/enable", dependencies=[Depends(_operator_dep)])
    def enable_plugin(name: str):
        try:
            return hub.enable_plugin(name)
        except (KeyError, ValueError, FileExistsError, RuntimeError) as exc:
            raise _lifecycle_error(exc) from exc

    @router.post("/plugins/{name}/disable", dependencies=[Depends(_operator_dep)])
    def disable_plugin(name: str):
        try:
            return hub.disable_plugin(name)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise _lifecycle_error(exc) from exc

    @router.delete("/plugins/{name}/install", dependencies=[Depends(_operator_dep)])
    def uninstall_persisted_plugin(
        name: str,
        data_policy: str = Query(default="keep", pattern="^(keep|trash)$"),
        confirm_data_move: bool = Query(default=False),
    ):
        try:
            return hub.uninstall_plugin(
                name,
                data_policy=data_policy,
                confirm_data_move=confirm_data_move,
            )
        except (KeyError, ValueError, RuntimeError) as exc:
            raise _lifecycle_error(exc) from exc

    @router.post("/plugins/{name}/load", dependencies=[Depends(_operator_dep)])
    def load_plugin(name: str):
        """Load a discovered plugin by name."""
        if hub.load(name):
            return {"ok": True, "name": name}
        raise HTTPException(400, f"Failed to load plugin: {name}")

    @router.post("/plugins/{name}/start", dependencies=[Depends(_operator_dep)])
    def start_plugin(name: str):
        """Start a loaded plugin."""
        if hub.start(name):
            return {"ok": True, "name": name}
        raise HTTPException(400, f"Failed to start plugin: {name}")

    @router.post("/plugins/{name}/stop", dependencies=[Depends(_operator_dep)])
    def stop_plugin(name: str):
        """Stop a started plugin."""
        if hub.stop(name):
            return {"ok": True, "name": name}
        raise HTTPException(400, f"Failed to stop plugin: {name}")

    @router.post("/plugins/{name}/unload", dependencies=[Depends(_operator_dep)])
    def unload_plugin(name: str):
        """Unload a plugin."""
        if hub.unload(name):
            return {"ok": True, "name": name}
        raise HTTPException(400, f"Failed to unload plugin: {name}")

    # ── Config management ──────────────────────────────────────

    @router.get("/plugins/{name}/config", dependencies=[Depends(_operator_dep)])
    def get_config(name: str):
        """Get the current configuration for a plugin."""
        config = hub.get_plugin_config(name)
        if config is None:
            raise HTTPException(404, f"Plugin not found: {name}")
        return config

    @router.put("/plugins/{name}/config", dependencies=[Depends(_operator_dep)])
    def update_config(name: str, body: dict[str, Any]):
        """Update the configuration for a plugin."""
        if hub.update_plugin_config(name, body):
            return {"ok": True}
        raise HTTPException(404, f"Plugin not found: {name}")

    # ── Single plugin detail ───────────────────────────────────

    @router.get("/plugins/{name}")
    def get_plugin_detail(name: str):
        """Return full metadata for a single plugin."""
        detail = (
            hub.get_plugin_detail(name)
            if hasattr(hub, "get_plugin_detail")
            else next((p for p in hub.list_plugins() if p["id"] == name), None)
        )
        if detail is not None:
            return detail
        raise HTTPException(404, f"Plugin not found: {name}")

    return router
