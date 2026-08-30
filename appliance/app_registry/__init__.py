"""App registry: NAS 桌面启动器的数据源(Docker 容器 → 应用卡片)。"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApplianceApp",
    "DockerClient",
    "DockerUnavailable",
    "build_catalog",
    "create_appliance_router",
]


def __getattr__(name: str) -> Any:
    """Keep Docker-only helpers independent of the private Agent runtime."""

    if name in {"ApplianceApp", "build_catalog"}:
        from appliance.app_registry.catalog import ApplianceApp, build_catalog

        return {"ApplianceApp": ApplianceApp, "build_catalog": build_catalog}[name]
    if name in {"DockerClient", "DockerUnavailable"}:
        from appliance.app_registry.docker_client import DockerClient, DockerUnavailable

        return {"DockerClient": DockerClient, "DockerUnavailable": DockerUnavailable}[name]
    if name == "create_appliance_router":
        from appliance.app_registry.router import create_appliance_router

        return create_appliance_router
    raise AttributeError(name)
