"""Echo iOS —— iPhone/iPad 触手模块.

通过 WebDriverAgent (WDA) HTTP API 直接控制 iOS 设备，无需设备端
自定义客户端。WDA 在设备上以 HTTP server 形式运行（默认端口 8100），
本模块通过 USB 转发或局域网访问 WDA 端点。

与 Android (mobile) 模块的对称性：

- 共享：Tentacle Protocol、TentaclePool、SKILL.md 单源、MCP 暴露
- 差异：iOS 走 WDA HTTP（标准协议），Android 走 WebSocket（自定义协议）
"""

from .capabilities import ios_capabilities, ios_skills_root
from .device import IOS_CAPABILITIES, IOSDevice
from .wda_client import WdaClient, WdaError

__all__ = [
    # device
    "IOSDevice",
    "IOS_CAPABILITIES",
    # capabilities
    "ios_capabilities",
    "ios_skills_root",
    # wda client
    "WdaClient",
    "WdaError",
]
