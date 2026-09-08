"""Agent storage compatibility surface consumed by Echo OS."""

from runtime.sensing.gateway.storage_proxy_router import create_storage_proxy_router
from runtime.storage.desktop_provider import desktop_file_manager

__all__ = ["create_storage_proxy_router", "desktop_file_manager"]
