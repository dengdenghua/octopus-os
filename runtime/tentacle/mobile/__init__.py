"""Echo Mobile —— 手机触手模块.

将 mobile 相关的所有子模块统一导出，外部只需:

    from runtime.tentacle.mobile import MobileDevice, TentacleMcpServer, ...
"""

from .cerebrum_adapter import CerebrumDecisionAdapter
from .device import ANDROID_CAPABILITIES, MobileDevice
from .mcp_server import SseSession, SseSessionManager, TentacleMcpServer, serve_stdio
from .pc_screen_capture import PC_HOST_ID, PcScreenCapture, PcScreenConfig, RemoteInputHandler
from .run_server import main as run_server_main
from .screen_relay import FrameFlags, FrameType, ScreenRelay

__all__ = [
    # device
    "MobileDevice",
    "ANDROID_CAPABILITIES",
    # cerebrum
    "CerebrumDecisionAdapter",
    # mcp
    "TentacleMcpServer",
    "serve_stdio",
    "SseSession",
    "SseSessionManager",
    # screen relay
    "ScreenRelay",
    "FrameType",
    "FrameFlags",
    # pc screen capture
    "PcScreenCapture",
    "PcScreenConfig",
    "RemoteInputHandler",
    "PC_HOST_ID",
    # run_server
    "run_server_main",
]
