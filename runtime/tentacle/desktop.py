"""DesktopDevice —— 桌面触手（自指）.

把 echo-agent 所在机器的本地桌面包装为一根触手。
让 Cerebrum 用统一接口（WebSocket + JSON-RPC）与本地桌面交互，
与远程手机触手用同一套协议。

Phase 0 占位实现 —— Phase 5 期间真正实现。
详见 ``docs/architecture/organs/tentacle-desktop.md``。
"""

from __future__ import annotations

import logging
import platform as _platform
import socket
import time
from typing import Any

from .base import (
    Heartbeat,
    TentacleStatus,
    TentacleType,
    ToolCall,
    ToolResult,
    now_ms,
)

logger = logging.getLogger(__name__)


# 桌面端能力声明（与 desktop_operator_arm 11 个 skills 对应）
DESKTOP_CAPABILITIES: list[str] = [
    "screen_capture",
    "screen_info",
    "mouse_click",
    "mouse_move",
    "keyboard_type",
    "keyboard_press",
    "computer_observe",
    "computer_plan_next",
    "computer_preview_action",
    "computer_execute_token",
    "computer_use_loop",
    "computer_uia_status",
    "computer_uia_tree",
    "computer_uia_find",
]


class DesktopDevice:
    """本地桌面触手（自指）—— 把 Runtime 所在机器包装为触手.

    Phase 0 占位实现：不真正调用 pyautogui，只返回 mock 结果。
    Phase 5 实现：包装 desktop_operator_arm。
    """

    def __init__(
        self,
        tentacle_id: str | None = None,
        platform_name: str | None = None,
        desktop_operator_arm: Any = None,
    ) -> None:
        self.tentacle_id = tentacle_id or f"desktop-{socket.gethostname()}"
        self.tentacle_type = TentacleType.DESKTOP
        self.platform = platform_name or _platform.system().lower()  # linux/darwin/windows
        self.meta = {
            "hostname": socket.gethostname(),
            "platform": self.platform,
            "python_version": _platform.python_version(),
        }
        self._desktop_operator_arm = desktop_operator_arm
        self._status: TentacleStatus = TentacleStatus.OFFLINE
        self._last_used_at: float = 0.0
        self._capabilities: list[str] = list(DESKTOP_CAPABILITIES)

    @property
    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    @property
    def status(self) -> TentacleStatus:
        return self._status

    @property
    def is_online(self) -> bool:
        return self._status in (TentacleStatus.ONLINE, TentacleStatus.BUSY)

    @property
    def is_busy(self) -> bool:
        return self._status == TentacleStatus.BUSY

    @property
    def last_used_at(self) -> float:
        return self._last_used_at

    async def connect(self) -> None:
        """本地"自指"无网络握手，直接 ONLINE."""
        self._status = TentacleStatus.ONLINE
        logger.info("DesktopDevice connected id=%s platform=%s", self.tentacle_id, self.platform)

    async def disconnect(self) -> None:
        self._status = TentacleStatus.OFFLINE
        logger.info("DesktopDevice disconnected id=%s", self.tentacle_id)

    async def heartbeat(self) -> Heartbeat:
        return Heartbeat(
            tentacle_id=self.tentacle_id,
            ts=now_ms(),
            online=True,
            current_app=None,
            screen_on=True,
            last_screen_tree_hash=None,
            running_tasks=0,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        """执行工具调用.

        Phase 0: Mock.
        Phase 5: 转发给 self._desktop_operator_arm.execute(call).
        """
        if not self.is_online:
            return ToolResult.fail(call.call_id, -32011, "Device offline", 0)
        if call.tool not in self._capabilities:
            return ToolResult.fail(call.call_id, -32003, f"Unknown tool: {call.tool}", 0)

        self._status = TentacleStatus.BUSY
        try:
            if self._desktop_operator_arm is None:
                return ToolResult.ok(
                    call.call_id,
                    data=f"[DESKTOP-MOCK] {call.tool}({call.args}) succeeded",
                    duration_ms=5,
                )
            # Phase 5: 真正调用
            # result = await self._desktop_operator_arm.execute(call)
            return ToolResult.ok(
                call.call_id,
                data=f"[DESKTOP-WIRED] {call.tool}({call.args})",
                duration_ms=10,
            )
        finally:
            self._status = TentacleStatus.ONLINE
            self._last_used_at = time.time()

    def __repr__(self) -> str:
        return (
            f"DesktopDevice(id={self.tentacle_id!r}, "
            f"platform={self.platform!r}, "
            f"status={self._status.value!r})"
        )
