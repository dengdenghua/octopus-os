"""Authenticated read-only projection of the Agent capability catalog.

Echo and Agent can use different JWT signing domains in appliance mode.  The
browser must therefore not forward or translate credentials between them.
This adapter authenticates with the Echo session and calls Agent's existing
``CloudCatalog`` abstraction in-process.  Agent remains the owner of catalog
and installation state; Echo never opens Agent's private database files.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from appliance.agent_api.catalog import create_agent_cloud_catalog
from appliance.security import ApplianceAuthenticator, resolve_authenticator


class AgentCloudCatalog(Protocol):
    def list(self, **kwargs: Any) -> dict[str, Any]: ...

    def installed_plugins(self) -> list[str]: ...

    def installed_skills(self) -> list[str]: ...

    def plugin_statuses(self) -> dict[str, dict[str, Any]]: ...


CatalogFactory = Callable[[str], AgentCloudCatalog]

_PUBLIC_FIELDS: dict[str, tuple[tuple[str, int], ...]] = {
    "plugins": (
        ("id", 256),
        ("plugin", 256),
        ("name", 256),
        ("name_zh", 256),
        ("description", 1_000),
        ("source", 512),
        ("version", 64),
        ("kind", 32),
        ("category", 128),
        ("author", 256),
        ("release_summary", 1_000),
        ("host_api", 160),
    ),
    "skills": (
        ("name", 256),
        ("description", 1_000),
        ("source", 512),
        ("author", 256),
        ("version", 64),
    ),
}

_PLUGIN_KINDS = frozenset({"plugin", "connector", "workbench"})
_PLUGIN_STATES = frozenset({"available", "enabled", "disabled", "update_available", "broken"})
_PLUGIN_STATE_SOURCES = frozenset({"factory", "cloud"})
_PLUGIN_TRUST_LEVELS = frozenset(
    {"system", "publisher", "local_integrity", "catalog", "unverified"}
)
_PLUGIN_COMPATIBILITY = frozenset({"compatible", "incompatible", "not_checked"})
_PLUGIN_PERMISSIONS = frozenset(
    {
        "account.credentials",
        "content.read",
        "content.write",
        "interaction.user",
        "network.remote",
        "process.local",
    }
)
_PLUGIN_AUTH_MODES = frozenset(
    {"connected-account", "mcp", "oauth", "oneid-token", "server-side", "token"}
)


def _default_catalog_factory(kind: str) -> AgentCloudCatalog:
    return create_agent_cloud_catalog(kind)


def _public_text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    return normalized


def _bounded_public_list(
    payload: Any,
    *,
    maximum_items: int = 64,
    maximum_text: int = 160,
) -> list[str] | None:
    if payload is None:
        return []
    if not isinstance(payload, list) or len(payload) > maximum_items:
        return None
    result: list[str] = []
    for item in payload:
        value = _public_text(item, maximum=maximum_text)
        if value is None:
            return None
        if value not in result:
            result.append(value)
    return result


def _bounded_requirements(payload: Any) -> dict[str, list[str]] | None:
    if not isinstance(payload, dict):
        return {
            "permissions": [],
            "authModes": [],
            "dependencies": [],
            "runtimeDependencies": [],
            "connectors": [],
        }
    permissions = _bounded_public_list(payload.get("permissions"))
    auth_modes = _bounded_public_list(payload.get("auth_modes"))
    dependencies = _bounded_public_list(payload.get("dependencies"))
    runtime_dependencies = _bounded_public_list(payload.get("runtime_dependencies"))
    connectors = _bounded_public_list(payload.get("connectors"))
    if (
        permissions is None
        or auth_modes is None
        or dependencies is None
        or runtime_dependencies is None
        or connectors is None
        or any(value not in _PLUGIN_PERMISSIONS for value in permissions)
        or any(value not in _PLUGIN_AUTH_MODES for value in auth_modes)
    ):
        return None
    return {
        "permissions": permissions,
        "authModes": auth_modes,
        "dependencies": dependencies,
        "runtimeDependencies": runtime_dependencies,
        "connectors": connectors,
    }


def _public_item(item: Any, *, kind: str) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(item, dict):
        return None
    projected = {
        field: normalized
        for field, maximum in _PUBLIC_FIELDS[kind]
        if (normalized := _public_text(item.get(field), maximum=maximum)) is not None
    }
    identity = (
        projected.get("plugin") or projected.get("id")
        if kind == "plugins"
        else projected.get("name")
    )
    if identity is None:
        return None
    if kind == "plugins" and projected.get("kind") not in _PLUGIN_KINDS:
        projected.pop("kind", None)
    if kind == "plugins":
        requirements = _bounded_requirements(item)
        if requirements is None:
            return None
        projected.update(requirements)
    return identity, projected


def _bounded_items(payload: Any, *, kind: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload["items"][:limit]:
        public = _public_item(item, kind=kind)
        if public is None:
            continue
        identity, projected = public
        if identity in seen:
            continue
        seen.add(identity)
        items.append(projected)
    return items


def _bounded_names(payload: Any, *, limit: int = 500) -> list[str]:
    if not isinstance(payload, (list, tuple, set, frozenset)):
        return []
    names = {value for item in payload if (value := _public_text(item, maximum=256)) is not None}
    return sorted(names)[:limit]


def _bounded_trust(
    payload: Any,
    *,
    source: str,
    installed: bool,
) -> dict[str, Any]:
    fallback = {
        "trustLevel": "system" if source == "factory" else "unverified" if installed else "catalog",
        "integrityVerified": False,
        "publisherVerified": False,
    }
    if not isinstance(payload, dict):
        return fallback
    level = _public_text(payload.get("level"), maximum=32)
    integrity_verified = payload.get("integrity_verified")
    publisher_verified = payload.get("publisher_verified")
    if (
        level not in _PLUGIN_TRUST_LEVELS
        or not isinstance(integrity_verified, bool)
        or not isinstance(publisher_verified, bool)
        or publisher_verified
        and (not integrity_verified or level != "publisher")
        or level == "publisher"
        and not publisher_verified
        or level == "local_integrity"
        and (not integrity_verified or publisher_verified)
        or level in {"catalog", "unverified"}
        and (integrity_verified or publisher_verified)
        or level == "system"
        and source != "factory"
    ):
        return fallback
    result = {
        "trustLevel": level,
        "integrityVerified": integrity_verified,
        "publisherVerified": publisher_verified,
    }
    publisher = _public_text(payload.get("publisher_id"), maximum=256)
    if publisher is not None and publisher_verified:
        result["publisher"] = publisher
    return result


def _bounded_compatibility(
    payload: Any,
    *,
    plugin_kind: str,
) -> dict[str, str]:
    fallback = {"compatibility": "not_checked"}
    if not isinstance(payload, dict):
        return fallback
    status = _public_text(payload.get("status"), maximum=32)
    if status not in _PLUGIN_COMPATIBILITY:
        return fallback
    result = {"compatibility": status}
    host_api = _public_text(payload.get("host_api"), maximum=160)
    if host_api is not None:
        result["hostApi"] = host_api
    return result


def _bounded_plugin_states(
    payload: Any,
    *,
    public_plugins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    allowed = {
        identity
        for item in public_plugins
        if (identity := item.get("plugin") or item.get("id")) is not None
    }
    states: list[dict[str, Any]] = []
    seen: set[str] = set()
    for identity, value in payload.items():
        plugin_id = _public_text(identity, maximum=256)
        if (
            plugin_id is None
            or plugin_id not in allowed
            or plugin_id in seen
            or not isinstance(value, dict)
        ):
            continue
        catalog_id = _public_text(value.get("catalog_id"), maximum=256) or plugin_id
        plugin_kind = _public_text(value.get("kind"), maximum=32)
        lifecycle_state = _public_text(value.get("lifecycle_state"), maximum=32)
        source = _public_text(value.get("source"), maximum=32)
        installed = value.get("installed")
        enabled = value.get("enabled")
        if (
            plugin_kind not in _PLUGIN_KINDS
            or lifecycle_state not in _PLUGIN_STATES
            or source not in _PLUGIN_STATE_SOURCES
            or not isinstance(installed, bool)
            or not isinstance(enabled, bool)
            or enabled
            and not installed
        ):
            continue
        recoveries = value.get("recoveries")
        recovery_count = min(len(recoveries), 1_000) if isinstance(recoveries, list) else 0
        requirements = _bounded_requirements(value)
        if requirements is None:
            continue
        permissions_granted = _bounded_public_list(value.get("permissions_granted"))
        permission_review_required = value.get("permission_review_required")
        permission_active = value.get("permission_active")
        if (
            permissions_granted is None
            or any(value not in _PLUGIN_PERMISSIONS for value in permissions_granted)
            or not set(permissions_granted) <= set(requirements["permissions"])
            or not isinstance(permission_review_required, bool)
            or not isinstance(permission_active, bool)
            or permission_active
            and (
                not installed
                or not enabled
                or set(permissions_granted) != set(requirements["permissions"])
            )
            or permission_review_required
            and (not installed or set(permissions_granted) == set(requirements["permissions"]))
        ):
            continue
        state: dict[str, Any] = {
            "id": plugin_id,
            "catalogId": catalog_id,
            "kind": plugin_kind,
            "source": source,
            "state": lifecycle_state,
            "installed": installed,
            "enabled": enabled,
            "rollbackAvailable": value.get("rollback_available") is True,
            "recoveryCount": recovery_count,
            "permissionsGranted": permissions_granted,
            "permissionReviewRequired": permission_review_required,
            "permissionActive": permission_active,
            **_bounded_trust(
                value.get("trust"),
                source=source,
                installed=installed,
            ),
            **_bounded_compatibility(
                value.get("compatibility"),
                plugin_kind=plugin_kind,
            ),
            **requirements,
        }
        version = _public_text(value.get("version"), maximum=64)
        available_version = _public_text(value.get("available_version"), maximum=64)
        release_summary = _public_text(value.get("release_summary"), maximum=1_000)
        if version is not None:
            state["version"] = version
        if available_version is not None:
            state["availableVersion"] = available_version
        if release_summary is not None:
            state["releaseSummary"] = release_summary
        states.append(state)
        seen.add(plugin_id)
    return states


class AgentAssetCatalogService:
    """Read a bounded Agent catalog without duplicating its persistence."""

    def __init__(self, catalog_factory: CatalogFactory | None = None) -> None:
        self._catalog_factory = catalog_factory or _default_catalog_factory

    def catalog(self, *, limit: int = 80) -> dict[str, Any]:
        items: dict[str, list[dict[str, Any]]] = {"plugins": [], "skills": []}
        installed: dict[str, list[str]] = {"plugins": [], "skills": []}
        plugin_states: list[dict[str, Any]] = []
        errors: list[str] = []
        readable: set[str] = set()

        for kind in ("plugins", "skills"):
            try:
                catalog = self._catalog_factory(kind)
                listing = catalog.list(limit=limit)
                if kind == "plugins":
                    # Workbench applications are part of the unified app
                    # directory, not an accidental tail of the much larger
                    # plugin list. Reserve catalog space for them so a bounded
                    # page cannot make the two Hub surfaces disagree.
                    workbench_listing = catalog.list(kind="workbench", limit=limit)
                    workbench_items = (
                        workbench_listing.get("items", [])
                        if isinstance(workbench_listing, dict)
                        else []
                    )
                    regular_items = listing.get("items", []) if isinstance(listing, dict) else []
                    listing = {"items": [*workbench_items, *regular_items]}
                items[kind] = _bounded_items(
                    listing,
                    kind=kind,
                    limit=limit,
                )
                readable.add(kind)
                state = (
                    catalog.installed_plugins() if kind == "plugins" else catalog.installed_skills()
                )
                installed[kind] = _bounded_names(state)
            except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                errors.append(kind)
                continue
            if kind == "plugins":
                try:
                    statuses = getattr(catalog, "plugin_statuses", None)
                    if not callable(statuses):
                        raise TypeError("Agent plugin lifecycle projection is unavailable")
                    plugin_states = _bounded_plugin_states(
                        statuses(),
                        public_plugins=items["plugins"],
                    )
                except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    errors.append("plugin-statuses")

        return {
            "schema": "echo.agent-assets.v6",
            "available": bool(readable),
            "plugins": items["plugins"],
            "skills": items["skills"],
            "installed": installed,
            "pluginStates": plugin_states,
            "unavailableSources": errors,
        }


def create_agent_assets_router(
    service: AgentAssetCatalogService | None = None,
    *,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    catalog = service or AgentAssetCatalogService()
    require_auth = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).dependency()
    router = APIRouter(
        prefix="/api/appliance/agent-assets",
        tags=["appliance", "agent-assets"],
        dependencies=[Depends(require_auth)],
    )

    @router.get("/catalog")
    async def read_catalog(
        limit: int = Query(default=80, ge=1, le=80),
    ) -> dict[str, Any]:
        return await run_in_threadpool(catalog.catalog, limit=limit)

    return router


__all__ = ["AgentAssetCatalogService", "create_agent_assets_router"]
