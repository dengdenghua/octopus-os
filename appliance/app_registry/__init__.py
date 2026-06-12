"""App registry: NAS 桌面启动器的数据源(Docker 容器 → 应用卡片)。"""

from appliance.app_registry.catalog import ApplianceApp, build_catalog
from appliance.app_registry.docker_client import DockerClient, DockerUnavailable
from appliance.app_registry.router import create_appliance_router

__all__ = [
    "ApplianceApp",
    "DockerClient",
    "DockerUnavailable",
    "build_catalog",
    "create_appliance_router",
]
