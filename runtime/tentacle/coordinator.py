"""TentacleCoordinator —— 触手协调器.

把 TentaclePool + TentacleWebSocketServer + MobileDevice 连接起来，
提供一键启动/停止的便利接口。

新增 Phase 1 能力：
  - task/execute 处理：手机请求母体决策 → Cerebrum 生成 tool_call 列表 →
    逐个下发给手机执行 → 收集结果 → 返回任务结果

用法::

    coordinator = TentacleCoordinator(host="0.0.0.0", port=8765)
    await coordinator.start()

    # 获取设备池统计
    stats = coordinator.pool.stats()

    # 向指定设备发送指令
    device = coordinator.pool.get("android-abc123")
    if device:
        result = await device.execute(ToolCall(...))

    await coordinator.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .mobile.vlm import VlmConfig

from runtime.pet import PetUdpBridge

from .base import Heartbeat, ToolCall, ToolResult
from .mobile.cerebrum_adapter import CerebrumDecisionAdapter
from .mobile.device import MobileDevice
from .mobile.pc_screen_capture import (
    PcScreenCapture,
    PcScreenConfig,
    RemoteInputHandler,
)
from .mobile.screen_relay import ScreenRelay
from .pool import TentaclePool
from .transport import DeviceHello, TaskExecuteRequest, TentacleWebSocketServer
from .transport.ws_server import WebSocketConnection

logger = logging.getLogger(__name__)

# 决策器类型别名：接收任务描述 + 设备 → 返回 tool_call 列表
DecisionEngine = Callable[[str, MobileDevice], Awaitable[list[ToolCall]]]


def _build_device_from_hello(
    hello: DeviceHello, ws_server: TentacleWebSocketServer | None
) -> MobileDevice | Any:
    """根据 device/hello 的平台字段创建设备实例.

    - ``platform == "ios"`` → :class:`runtime.tentacle.ios.device.IOSDevice`
      （通过 WDA HTTP 直接控制，无需 ws_server）
    - 其他平台 → :class:`MobileDevice`（Android / 兼容回退）

    Args:
        hello: 设备上报的元信息（含 platform / ios 专属字段）
        ws_server: WebSocket 服务器（Android 设备用于收发指令，iOS 忽略）
    """
    if hello.platform == "ios":
        from .ios.device import IOSDevice

        return IOSDevice(
            tentacle_id=hello.tentacle_id,
            base_url=f"http://localhost:{hello.wda_port}",
            bundle_id=hello.bundle_id,
            device_meta=hello.to_meta(),
        )

    return MobileDevice(
        tentacle_id=hello.tentacle_id,
        device_meta=hello.to_meta(),
        ws_server=ws_server,
    )


class TentacleCoordinator:
    """触手协调器 —— 一键启动所有触手基础设施.

    负责：
    1. 启动 WebSocket Server 接收手机连接
    2. device/hello 时自动创建 MobileDevice 并注册到 TentaclePool
    3. device/heartbeat 时更新设备元信息
    4. device/disconnect 时自动注销
    5. task/execute 时调用决策引擎，生成 tool_call 列表并下发执行
    6. tool/result 时透传给等待的调用方
    """

    def __init__(
        self,
        host: str = "0.0.0.0",  # nosec B104 — tentacle server, intentional LAN bind
        port: int = 8765,
        *,
        decision_engine: DecisionEngine | None = None,
        dashboard_port: int | None = 8766,
        dashboard_host: str = "127.0.0.1",  # loopback by default: unauthenticated dashboard must not face the LAN
        screen_max_fps: int = 15,
        mcp_server: bool = False,
        pc_screen: bool = False,
        pc_screen_config: PcScreenConfig | None = None,
        remote_input: bool = False,
        auth_token: str | None = None,
        dashboard_require_auth: bool = True,
        identity_store: Any = None,
        dashboard_jwt_secret: str | None = None,
        dashboard_jwt_issuer: str | None = None,
        dashboard_jwt_audience: str | None = None,
    ) -> None:
        self.pool = TentaclePool()
        self._pet_bridge = PetUdpBridge()
        self.pool.subscribe_screen_changes(self._pet_bridge.on_pool_event)
        # 屏幕流中继服务
        self.screen_relay = ScreenRelay(max_fps=screen_max_fps)
        # 远程输入处理器（手机→PC控制）
        self.remote_input_handler: RemoteInputHandler | None = None
        if remote_input:
            self.remote_input_handler = RemoteInputHandler()
        # PC屏幕捕获
        self.pc_screen_capture: PcScreenCapture | None = None
        self._pc_screen_enabled = pc_screen
        self._pc_screen_config = pc_screen_config
        self.ws_server = TentacleWebSocketServer(
            host=host,
            port=port,
            auth_token=auth_token,
            on_device_hello=self._on_device_hello,
            on_device_disconnect=self._on_device_disconnect,
            on_heartbeat=self._on_heartbeat,
            on_tool_result=self._on_tool_result,
            on_task_execute=self._on_task_execute,
            on_screen_frame=self._on_screen_frame,
            on_remote_input=self._on_remote_input,
        )
        self._decision_engine = decision_engine
        self._dashboard_port = dashboard_port
        self._dashboard_host = dashboard_host
        self._dashboard_server: Any | None = None
        self._mcp_server_enabled = mcp_server
        self._dashboard_require_auth = dashboard_require_auth
        self._identity_store = identity_store
        self._dashboard_jwt_secret = dashboard_jwt_secret
        self._dashboard_jwt_issuer = dashboard_jwt_issuer
        self._dashboard_jwt_audience = dashboard_jwt_audience

    def _dashboard_host_is_loopback(self) -> bool:
        host = (self._dashboard_host or "127.0.0.1").strip().lower()
        return host in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}

    async def start(self) -> None:
        """启动协调器（WebSocket + 可选 Dashboard + 可选 PC屏幕捕获）."""
        await self.ws_server.start()
        logger.info("TentacleCoordinator started (ws port=%d)", self.ws_server.port)

        # 启动 PC 屏幕捕获
        if self._pc_screen_enabled:
            try:
                self.pc_screen_capture = PcScreenCapture(
                    self.screen_relay,
                    config=self._pc_screen_config or PcScreenConfig(),
                )
                await self.pc_screen_capture.start()
                logger.info("PC screen capture enabled")
            except Exception as e:
                logger.warning("PC screen capture failed to start: %s", e)

        # 启动 Dashboard HTTP 服务
        if self._dashboard_port is not None:
            try:
                import uvicorn
                from fastapi import FastAPI

                from runtime.tentacle.dashboard import create_tentacle_router

                # If auth is required but no identity store is available, build
                # one from the WebSocket auth token so the dashboard is never
                # exposed anonymously. A loopback-only dashboard with auth
                # disabled stays allowed for local development.
                identity_store = self._identity_store
                if self._dashboard_require_auth and identity_store is None:
                    from runtime.safety.auth import Identity, IdentityStore

                    if self._dashboard_require_auth and not self._dashboard_host_is_loopback():
                        raise RuntimeError(
                            "Tentacle dashboard requires authentication but no "
                            "identity_store/auth_token is configured; refusing to "
                            "bind an unauthenticated dashboard to the network"
                        )
                    token = self.ws_server.auth_token
                    if token:
                        store = IdentityStore()
                        store.add(Identity(actor_id="tentacle-dashboard"), api_key_plaintext=token)
                        identity_store = store
                    else:
                        logger.warning(
                            "Tentacle dashboard auth enabled but no credentials; "
                            "dashboard runs unauthenticated (loopback only)"
                        )

                app = FastAPI(title="Echo Tentacle Dashboard")
                app.include_router(
                    create_tentacle_router(
                        self,
                        require_auth=self._dashboard_require_auth,
                        identity_store=identity_store,
                        jwt_secret=self._dashboard_jwt_secret,
                        jwt_issuer=self._dashboard_jwt_issuer,
                        jwt_audience=self._dashboard_jwt_audience,
                    )
                )
                config = uvicorn.Config(
                    app,
                    host=self._dashboard_host,  # loopback unless explicitly opened via dashboard_host
                    port=self._dashboard_port,
                    log_level="warning",
                )
                self._dashboard_server = uvicorn.Server(config)
                asyncio.create_task(self._dashboard_server.serve())
                logger.info(
                    "Dashboard started at http://%s:%d/api/tentacle/dashboard",
                    self._dashboard_host,
                    self._dashboard_port,
                )
            except ImportError:
                logger.warning("Dashboard disabled: fastapi/uvicorn not installed")

    async def stop(self) -> None:
        """停止协调器."""
        # 停止 PC 屏幕捕获
        if self.pc_screen_capture is not None:
            await self.pc_screen_capture.stop()
        # 断开所有设备
        for t in self.pool.all():
            await t.disconnect()
        await self.ws_server.stop()
        self._pet_bridge.close()
        # 停止 Dashboard
        if self._dashboard_server is not None:
            self._dashboard_server.should_exit = True
            self._dashboard_server = None
        logger.info("TentacleCoordinator stopped")

    # ── 回调：设备连接 ──────────────────────────────────────

    async def _on_device_hello(self, hello: DeviceHello, ws: WebSocketConnection) -> None:
        """设备首次连接 —— 创建设备并注册到 Pool."""
        # 检查是否已存在
        existing = self.pool.get(hello.tentacle_id)
        if existing is not None:
            logger.warning("device reconnected id=%s", hello.tentacle_id)
            await existing.disconnect()
            await self.pool.unregister(hello.tentacle_id)

        # 创建新设备（按平台分发）
        device = _build_device_from_hello(hello, ws_server=self.ws_server)
        device.mark_online()

        # 注册到 Pool
        await self.pool.register(device)
        logger.info(
            "device registered via hello id=%s model=%s caps=%d",
            hello.tentacle_id,
            hello.model,
            len(hello.capabilities),
        )

    async def _on_device_disconnect(self, tentacle_id: str) -> None:
        """设备断开 —— 从 Pool 注销，清理屏幕流订阅."""
        device = self.pool.get(tentacle_id)
        if device is not None:
            await device.disconnect()
        await self.pool.unregister(tentacle_id)
        # 清理屏幕流订阅
        await self.screen_relay.on_device_disconnect(tentacle_id)
        logger.info("device disconnected id=%s", tentacle_id)

    async def _on_heartbeat(self, hb: Heartbeat) -> None:
        """心跳 —— 更新设备元信息."""
        device = self.pool.get(hb.tentacle_id)
        if device is None:
            return
        # 更新元信息
        if hb.current_app:
            device.meta["current_app"] = hb.current_app
        if hb.battery is not None:
            device.meta["battery"] = hb.battery
        if hb.last_screen_tree_hash:
            device.meta["last_screen_tree_hash"] = hb.last_screen_tree_hash
        logger.debug(
            "heartbeat from %s app=%s battery=%s", hb.tentacle_id, hb.current_app, hb.battery
        )

    async def _on_tool_result(self, result: ToolResult) -> None:
        """工具结果 —— 已由 ws_server 唤醒 future，此处只打日志."""
        logger.debug("tool result call_id=%s success=%s", result.call_id, result.success)

    async def _on_screen_frame(self, tentacle_id: str, data: bytes) -> None:
        """屏幕帧回调 —— 将设备推送的帧中继给订阅者.

        Args:
            tentacle_id: 设备 ID
            data: 二进制帧数据（含帧头）
        """
        await self.screen_relay.relay_frame(tentacle_id, data)

    async def _on_remote_input(self, tentacle_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """远程输入回调 —— 手机端控制PC.

        Args:
            tentacle_id: 发送输入事件的设备 ID
            event: 输入事件字典

        Returns:
            执行结果
        """
        if self.remote_input_handler is None:
            return {"success": False, "error": "Remote input not enabled"}
        return await self.remote_input_handler.handle_input(event)

    async def _on_task_execute(self, request: TaskExecuteRequest, ws: WebSocketConnection) -> None:
        """处理 task/execute —— 手机请求母体决策任务.

        流程：
        1. 查找设备
        2. 调用决策引擎生成 tool_call 列表
        3. 逐个下发 tool/execute 给手机
        4. 收集结果
        5. 返回 task/result 给手机
        """
        logger.info(
            "task execute request id=%s task='%s' device=%s",
            request.task_id,
            request.task,
            request.tentacle_id,
        )

        device = self.pool.get(request.tentacle_id)
        if device is None:
            # 设备不存在，直接通过原始 ws 回复（send_task_result 找不到连接）
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "task/result",
                        "params": {
                            "task_id": request.task_id,
                            "success": False,
                            "response": f"Device {request.tentacle_id} not found",
                            "steps": 0,
                        },
                        "id": request.task_id,
                    }
                )
            )
            return

        if self._decision_engine is None:
            # 无决策引擎，返回简单确认
            await self.ws_server.send_task_result(
                request.tentacle_id,
                request.task_id,
                success=True,
                response=f"Task received: {request.task}. No decision engine configured.",
                steps=0,
            )
            return

        try:
            # 调用决策引擎生成 tool_call 列表
            tool_calls = await self._decision_engine(request.task, device)
            logger.info("decision engine generated %d tool calls", len(tool_calls))

            # 逐个执行
            results: list[ToolResult] = []
            for call in tool_calls:
                result = await device.execute(call)
                results.append(result)
                if not result.success:
                    logger.warning("tool call failed: %s error=%s", call.tool, result.error_message)
                    break  # 有错误时停止

            # 汇总结果
            success = all(r.success for r in results)
            response = self._summarize_results(request.task, results)

            await self.ws_server.send_task_result(
                request.tentacle_id,
                request.task_id,
                success=success,
                response=response,
                steps=len(tool_calls),
            )
            logger.info(
                "task result sent id=%s success=%s steps=%d",
                request.task_id,
                success,
                len(tool_calls),
            )

        except Exception as e:
            logger.exception("task execute failed id=%s", request.task_id)
            await self.ws_server.send_task_result(
                request.tentacle_id,
                request.task_id,
                success=False,
                response=f"Task execution failed: {e}",
            )

    # ── 便捷方法 ────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """获取协调器统计."""
        return {
            "pool": self.pool.stats(),
            "ws_connected": self.ws_server.connected_count,
            "screen_relay": self.screen_relay.stats(),
            "pc_screen": self.pc_screen_capture.stats if self.pc_screen_capture else None,
            "remote_input": self.remote_input_handler is not None,
        }

    @classmethod
    def with_cerebrum(
        cls,
        host: str = "0.0.0.0",  # nosec B104 — tentacle server, intentional LAN bind
        port: int = 8765,
        *,
        rules: list | None = None,
        fallback_skill: str | None = None,
    ) -> TentacleCoordinator:
        """使用 Cerebrum 静态规划器作为决策引擎的工厂方法.

        Args:
            host: WebSocket 监听地址
            port: WebSocket 监听端口
            rules: 静态规则列表（见 StaticPlanner.Rule）
            fallback_skill: 无规则匹配时的回退技能

        用法::

            coordinator = TentacleCoordinator.with_cerebrum(
                port=8765,
                rules=[
                    Rule(
                        name="open_wechat",
                        keywords=["微信", "wechat"],
                        skill_sequence=["android.open_app", "android.find_and_tap"],
                        node_args_templates=[{"package": "com.tencent.mm"}, None],
                    ),
                ],
                fallback_skill="android.find_and_tap",
            )
            await coordinator.start()
        """
        from runtime.core.cerebrum.planner import StaticPlanner

        planner = StaticPlanner(
            rules=rules,
            fallback_skill=fallback_skill,
        )
        adapter = CerebrumDecisionAdapter(planner=planner)

        async def _decision_engine(task: str, device: MobileDevice) -> list[ToolCall]:
            return await adapter.decide(task, device)

        return cls(
            host=host,
            port=port,
            decision_engine=_decision_engine,
        )

    @classmethod
    def with_vlm(
        cls,
        vlm_config: VlmConfig,
        host: str = "0.0.0.0",  # nosec B104 — tentacle server, intentional LAN bind
        port: int = 8765,
        *,
        dashboard_port: int | None = 8766,
    ) -> TentacleCoordinator:
        """使用 VLM 作为决策引擎的工厂方法.

        流程：
        1. 收到任务 → 先截图
        2. 截图 + 任务描述 → VLM 分析
        3. VLM 返回建议操作 → 转为 ToolCall 列表
        4. 逐个执行 ToolCall
        5. 每步执行后再截图 → VLM 验证 → 继续或调整

        Args:
            vlm_config: VLM 配置（如 VlmConfig.qwen_vl("sk-xxx")）
            host: WebSocket 监听地址
            port: WebSocket 监听端口
            dashboard_port: Dashboard HTTP 端口

        用法::

            from .mobile.vlm import VlmConfig

            coordinator = TentacleCoordinator.with_vlm(
                vlm_config=VlmConfig.qwen_vl("sk-xxx"),
                port=8765,
            )
            await coordinator.start()
        """
        from .mobile.vlm import VlmClient

        vlm_client = VlmClient(vlm_config)

        async def _vlm_decision_engine(task: str, device: MobileDevice) -> list[ToolCall]:
            """VLM 决策引擎：截图 → VLM 分析 → 转为 ToolCall 列表 → 逐个执行并验证."""
            # 1. 截图
            screenshot_call = ToolCall(
                call_id=f"vlm-screenshot-{int(time.time() * 1000)}",
                tentacle_id=device.tentacle_id,
                tool="android.take_screenshot",
                args={},
            )
            screenshot_result = await device.execute(screenshot_call)
            if not screenshot_result.success:
                logger.warning("VLM 决策引擎：截图失败 %s", screenshot_result.error_message)
                return []

            # 获取截图 base64
            screenshot_b64 = ""
            if isinstance(screenshot_result.data, dict):
                screenshot_b64 = screenshot_result.data.get("screenshot", "")
            elif isinstance(screenshot_result.data, str):
                screenshot_b64 = screenshot_result.data

            if not screenshot_b64:
                logger.warning("VLM 决策引擎：截图数据为空")
                return []

            # 2. 获取无障碍树（可选）
            screen_info_call = ToolCall(
                call_id=f"vlm-screeninfo-{int(time.time() * 1000)}",
                tentacle_id=device.tentacle_id,
                tool="android.get_screen_info",
                args={},
            )
            screen_info = None
            screen_info_result = await device.execute(screen_info_call)
            if screen_info_result.success and isinstance(screen_info_result.data, dict):
                screen_info = screen_info_result.data

            # 3. VLM 分析
            try:
                if screen_info:
                    analysis = vlm_client.analyze_with_tree(
                        screenshot_base64=screenshot_b64,
                        tree_data=screen_info,
                        task=task,
                    )
                else:
                    analysis = vlm_client.analyze_screenshot(
                        screenshot_base64=screenshot_b64,
                        task=task,
                    )
            except Exception:  # noqa: BLE001 — best-effort; logged
                logger.exception("VLM 分析失败")
                return []

            logger.info(
                "VLM 分析结果: description=%s actions=%d app=%s",
                analysis.description[:50],
                len(analysis.suggested_actions),
                analysis.current_app,
            )

            # 4. 转为 ToolCall 列表
            from .mobile.vlm.react_with_vision import VisionReAct

            return VisionReAct.suggested_action_to_tool_calls(
                analysis.suggested_actions,
                tentacle_id=device.tentacle_id,
            )

        return cls(
            host=host,
            port=port,
            decision_engine=_vlm_decision_engine,
            dashboard_port=dashboard_port,
        )

    # ── 内部辅助 ────────────────────────────────────────────

    @staticmethod
    def _summarize_results(task: str, results: list[ToolResult]) -> str:
        """汇总工具执行结果为自然语言响应."""
        if not results:
            return f"No actions taken for task: {task}"

        lines = [f"Task: {task}"]
        for i, r in enumerate(results, 1):
            if r.success:
                lines.append(f"  Step {i}: ✓ {r.data or 'OK'}")
            else:
                lines.append(f"  Step {i}: ✗ {r.error_message or 'Failed'}")
                break

        return "\n".join(lines)
