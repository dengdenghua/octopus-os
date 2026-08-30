"""Agent extension catalog compatibility surface consumed by Echo OS."""

from __future__ import annotations

from typing import Any


def create_agent_cloud_catalog(kind: str) -> Any:
    from runtime.platform.plugins.cloud_catalog import CloudCatalog

    return CloudCatalog(kind)


__all__ = ["create_agent_cloud_catalog"]
