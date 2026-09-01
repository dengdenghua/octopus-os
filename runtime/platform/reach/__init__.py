"""Native multi-platform reach layer for Echo Agent."""

from .collection import platform_collect
from .doctor import diagnose_reach
from .monitoring import platform_monitor
from .router import platform_read, platform_search

__all__ = [
    "diagnose_reach",
    "platform_collect",
    "platform_monitor",
    "platform_read",
    "platform_search",
]
