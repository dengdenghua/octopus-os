"""Tentacle (触手) —— 章鱼伸出去的物理肢体.

Echo Mobile 的核心抽象。详见：

- ``docs/biomimetic/tentacle/README.md`` —— 触手器官设计哲学
- ``docs/mobile/architecture.md`` —— 三层三端架构
- ``docs/adr/011-echo-mobile.md`` —— 架构决策记录
- ``docs/naming.md`` —— 双轨命名契约

公开 API（双轨命名，参见 ADR-001）::

    from runtime.tentacle import Tentacle, TentacleType
    from runtime.tentacle import MobileDevice, DesktopDevice, TentaclePool

生物名 ``Tentacle`` 是工程名 ``Tentacle`` 的别名（在本模块中两者同名），
``TentacleType`` 是枚举类型的工程名。
"""

from __future__ import annotations

from .base import Heartbeat, Tentacle, TentacleStatus, TentacleType, ToolCall, ToolResult
from .desktop import DESKTOP_CAPABILITIES, DesktopDevice
from .mobile.device import ANDROID_CAPABILITIES, MobileDevice
from .mobile.mcp_server import TentacleMcpServer, serve_stdio
from .pool import TentaclePool, select_for_affinity

# 仿生学别名（按 ADR-001 双轨命名契约 —— 类名用工程名，生物名仅在文档与别名）
Tentacle = Tentacle  # 同名（生物名 ≡ 工程名，因为概念高度一致）

__all__ = [
    "Tentacle",
    "TentacleType",
    "TentacleStatus",
    "Heartbeat",
    "ToolCall",
    "ToolResult",
    "TentaclePool",
    "MobileDevice",
    "DesktopDevice",
    "select_for_affinity",
    "ANDROID_CAPABILITIES",
    "DESKTOP_CAPABILITIES",
    "TentacleMcpServer",
    "serve_stdio",
]

__version__ = "0.1.0"
