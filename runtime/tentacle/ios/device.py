"""IOSDevice —— iPhone/iPad 触手实现.

与 :class:`runtime.tentacle.mobile.device.MobileDevice` 对称，实现
:class:`runtime.tentacle.base.Tentacle` 协议，但通过 WebDriverAgent
(WDA) HTTP API 直接控制设备，而非 WebSocket 中转。

连接拓扑::

    ┌─────────────────┐     HTTP      ┌──────────────────┐
    │  IOSDevice      │ ─────────────▶│  WDA on device   │
    │  (agent side)   │  localhost:   │  (port 8100 via  │
    │                 │   8100 via    │   iproxy/USB or  │
    │                 │   iproxy/USB  │   LAN)           │
    └─────────────────┘               └──────────────────┘

工具调用流程::

    ToolCall(tool="ios.tap", args={x,y})
        → IOSDevice.execute()
        → _dispatch_tool() 路由到具体 WDA 方法
        → WdaClient.tap(x, y)
        → ToolResult.ok(...)

无需 ws_server（WDA 即是端点）；保留可选 ``ws_server`` 参数仅为与
``MobileDevice`` 接口对称，用于未来远程代理场景。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..base import (
    Heartbeat,
    TentacleStatus,
    TentacleType,
    ToolCall,
    ToolResult,
    now_ms,
)
from .capabilities import ios_capabilities
from .wda_client import DEFAULT_WDA_PORT, DEFAULT_WDA_TIMEOUT, WdaClient, WdaError

logger = logging.getLogger(__name__)

# The SKILL.md manifests are the single source of truth. Keep this public
# list-shaped export for symmetry with mobile.device.ANDROID_CAPABILITIES.
IOS_CAPABILITIES: list[str] = list(ios_capabilities())


class IOSDevice:
    """iOS 设备触手.

    Args:
        tentacle_id: 全局唯一 ID（如 ``ios-<udid>``）
        base_url: WDA HTTP 端点（默认 ``http://localhost:8100``）
        bundle_id: 可选，会话启动时自动拉起的 app bundle id
        device_meta: 设备元信息（udid、ios_version、screen_size 等）
        wda_timeout: WDA HTTP 请求超时（秒）
    """

    def __init__(
        self,
        tentacle_id: str,
        *,
        base_url: str = f"http://localhost:{DEFAULT_WDA_PORT}",
        bundle_id: str | None = None,
        device_meta: dict[str, Any] | None = None,
        wda_timeout: float = DEFAULT_WDA_TIMEOUT,
    ) -> None:
        self.tentacle_id = tentacle_id
        self.tentacle_type = TentacleType.MOBILE  # iOS 仍是 mobile 类型触手
        self.platform = "ios"
        self.meta = device_meta or {
            "udid": "unknown",
            "ios_version": "17.0",
            "model": "iPhone",
            "screen_size": [390, 844],  # logical points (iPhone 14 default)
        }
        self._wda = WdaClient(
            base_url=base_url,
            bundle_id=bundle_id,
            timeout=wda_timeout,
        )

        # 状态
        self._status: TentacleStatus = TentacleStatus.OFFLINE
        self._last_used_at: float = 0.0
        self._capabilities: list[str] = list(IOS_CAPABILITIES)
        self._meta_lock = asyncio.Lock()

    # ── Tentacle 接口实现 ──────────────────────────────────

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

    @property
    def wda(self) -> WdaClient:
        """暴露 WDA 客户端供高级用例直接调用."""
        return self._wda

    async def connect(self) -> None:
        """连接设备 —— 建立 WDA session 并拉起 bundle_id（如配置）."""
        self._status = TentacleStatus.CONNECTING
        try:
            await self._wda.connect()
            # 主动探测窗口尺寸，验证 WDA 真正可用
            try:
                width, height = await self._wda.window_size()
                if width > 0 and height > 0:
                    self.meta["screen_size"] = [width, height]
            except WdaError as exc:
                logger.warning("IOSDevice window_size probe failed: %s", exc)
            self._status = TentacleStatus.ONLINE
            logger.info("IOSDevice online id=%s url=%s", self.tentacle_id, self._wda.base_url)
        except WdaError as exc:
            self._status = TentacleStatus.ERROR
            logger.error("IOSDevice connect failed id=%s: %s", self.tentacle_id, exc)
            raise

    def mark_online(self) -> None:
        """显式标记为在线（用于 ws_server 回调场景）."""
        self._status = TentacleStatus.ONLINE

    async def disconnect(self) -> None:
        """断开设备 —— 关闭 WDA session."""
        await self._wda.disconnect()
        self._status = TentacleStatus.OFFLINE
        logger.info("IOSDevice disconnected id=%s", self.tentacle_id)

    async def heartbeat(self) -> Heartbeat:
        """心跳 —— 探测 WDA ``/status`` 端点."""
        if not self.is_online:
            return Heartbeat(tentacle_id=self.tentacle_id, ts=now_ms(), online=False)
        online = True
        try:
            await self._wda.status()
        except WdaError:
            online = False
            self._status = TentacleStatus.ERROR
        return Heartbeat(
            tentacle_id=self.tentacle_id,
            ts=now_ms(),
            online=online,
            current_app=self.meta.get("current_app"),
            screen_on=self.meta.get("screen_on", True),
            battery=self.meta.get("battery"),
            is_charging=self.meta.get("is_charging", False),
            last_screen_tree_hash=self.meta.get("last_screen_tree_hash"),
            running_tasks=self.meta.get("running_tasks", 0),
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        """执行工具调用 —— 路由到对应 WDA 方法."""
        start = time.time()
        if not self.is_online:
            return ToolResult.fail(call.call_id, -32011, "Device offline", 0)
        if call.tool not in self._capabilities:
            return ToolResult.fail(call.call_id, -32003, f"Unknown tool: {call.tool}", 0)

        self._status = TentacleStatus.BUSY
        try:
            data = await self._dispatch_tool(call.tool, call.args)
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult.ok(call.call_id, data=data, duration_ms=duration_ms)
        except WdaError as exc:
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult.fail(
                call.call_id,
                exc.status or -32015,
                f"WDA error: {exc}",
                duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 — surface any unexpected failure
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult.fail(
                call.call_id,
                -32015,
                f"Execute error: {exc}",
                duration_ms,
            )
        finally:
            self._status = TentacleStatus.ONLINE
            self._last_used_at = time.time()

    # ── 工具路由表 ─────────────────────────────────────────

    async def _dispatch_tool(self, tool: str, args: dict[str, Any]) -> Any:
        """Map an ``ios.*`` skill id to its WDA call.

        Each branch reads only the documented SKILL.md parameters. Unknown
        keys are ignored so callers can pass ``wait_after``/``trace_id`` etc.
        without tripping the dispatcher.
        """
        if tool == "ios.tap":
            await self._wda.tap(int(args["x"]), int(args["y"]))
            return {"tapped": True, "x": int(args["x"]), "y": int(args["y"])}

        if tool == "ios.double_tap":
            await self._wda.double_tap(int(args["x"]), int(args["y"]))
            return {"double_tapped": True, "x": int(args["x"]), "y": int(args["y"])}

        if tool == "ios.long_press":
            duration = float(args.get("duration_s", args.get("duration", 2.0)))
            await self._wda.long_press(int(args["x"]), int(args["y"]), duration=duration)
            return {"long_pressed": True, "x": int(args["x"]), "y": int(args["y"])}

        if tool == "ios.swipe":
            duration = float(args.get("duration_s", args.get("duration", 0.5)))
            await self._wda.swipe(
                int(args["x1"]),
                int(args["y1"]),
                int(args["x2"]),
                int(args["y2"]),
                duration=duration,
            )
            return {
                "swiped": True,
                "from": [int(args["x1"]), int(args["y1"])],
                "to": [int(args["x2"]), int(args["y2"])],
            }

        if tool == "ios.input_text":
            text = str(args["text"])
            await self._wda.input_text(text)
            return {"entered": text}

        if tool == "ios.take_screenshot":
            scale = float(args.get("scale", 1.0))
            screenshot = await self._wda.screenshot()
            # WDA 不原生支持 scale 参数；保留参数以与 android.take_screenshot 对称
            return {"screenshot": screenshot, "scale": scale}

        if tool == "ios.home":
            await self._wda.home()
            return {"home": True}

        if tool == "ios.find_element":
            return await self._wda.find_element(
                accessibility_id=args.get("accessibility_id"),
                class_name=args.get("class_name"),
                xpath=args.get("xpath"),
                partial=bool(args.get("partial", False)),
            )

        if tool == "ios.get_screen_info":
            source = await self._wda.source()
            width, height = await self._wda.window_size()
            return {
                "source": source,
                "window_size": {"width": width, "height": height},
            }

        if tool == "ios.open_app":
            bundle_id = str(args["bundle_id"])
            await self._wda.launch_app(bundle_id)
            self.meta["current_app"] = bundle_id
            return {"launched": bundle_id}

        if tool == "ios.close_app":
            bundle_id = str(args["bundle_id"])
            await self._wda.terminate_app(bundle_id)
            return {"terminated": bundle_id}

        if tool == "ios.get_active_app":
            return await self._wda.active_app_info()

        if tool == "ios.wait":
            duration_ms = int(args.get("duration_ms", 1000))
            await asyncio.sleep(duration_ms / 1000.0)
            return {"waited_ms": duration_ms}

        raise WdaError(f"Unimplemented ios skill: {tool}")

    def __repr__(self) -> str:
        return (
            f"IOSDevice(id={self.tentacle_id!r}, "
            f"status={self._status.value!r}, "
            f"caps={len(self._capabilities)})"
        )


__all__ = [
    "IOS_CAPABILITIES",
    "IOSDevice",
]
