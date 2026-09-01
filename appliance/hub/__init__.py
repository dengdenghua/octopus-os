"""Echo Hub: curated device application catalog and install planning."""

from __future__ import annotations

from typing import Any

__all__ = ["HubCatalog", "HubCatalogError", "HubService", "create_hub_router"]


def __getattr__(name: str) -> Any:
    """Keep catalog-only release tooling independent of the private Agent runtime."""

    if name in {"HubCatalog", "HubCatalogError"}:
        from appliance.hub.catalog import HubCatalog, HubCatalogError

        return {"HubCatalog": HubCatalog, "HubCatalogError": HubCatalogError}[name]
    if name == "HubService":
        from appliance.hub.service import HubService

        return HubService
    if name == "create_hub_router":
        from appliance.hub.router import create_hub_router

        return create_hub_router
    raise AttributeError(name)
