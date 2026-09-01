"""Tentacle Dashboard —— 移动设备控制面板 REST API.

提供 Web Dashboard 所需的 HTTP 端点：
  GET  /api/tentacle/devices          — 列出已连接设备
  GET  /api/tentacle/devices/{id}     — 设备详情
  POST /api/tentacle/task             — 提交任务（自然语言）
  GET  /api/tentacle/stats            — 协调器统计
  GET  /api/tentacle/dashboard        — Dashboard HTML 页面
  POST /api/tentacle/devices/{id}/analyze — VLM 视觉分析设备屏幕
  GET  /api/tentacle/devices/{id}/screenshot — 获取设备最新截图
  POST /api/tentacle/screen/subscribe — 返回 WebSocket URL
  WS   /api/tentacle/screen/stream    — 屏幕流 WebSocket 端点
  GET  /api/tentacle/pc-screen/stream — PC屏幕流 WebSocket 端点
  POST /api/tentacle/pc-screen/start  — 启动PC屏幕捕获
  POST /api/tentacle/pc-screen/stop   — 停止PC屏幕捕获
  GET  /api/tentacle/pc-screen/stats  — PC屏幕捕获统计
  POST /api/tentacle/remote-input     — 远程输入事件（手机→PC）
  GET  /api/tentacle/mcp/sse          — MCP SSE 连接端点
  POST /api/tentacle/mcp/message      — MCP 消息接收端点
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import Request as FastAPIRequest
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from runtime.safety.auth import resolve_principal
from runtime.safety.auth.websocket import accepted_auth_subprotocol, websocket_bearer_token
from runtime.tentacle._dashboard_helpers import _auto_detect_vlm_config
from runtime.tentacle._dashboard_html import _DASHBOARD_HTML
from runtime.tentacle.base import ToolCall
from runtime.tentacle.coordinator import TentacleCoordinator
from runtime.tentacle.fleet import broadcast as fleet_broadcast

logger = logging.getLogger(__name__)


def create_tentacle_router(
    coordinator: TentacleCoordinator,
    *,
    identity_store: Any = None,
    require_auth: bool = True,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """创建 Tentacle Dashboard 路由.

    Args:
        coordinator: 已启动的 TentacleCoordinator 实例
    """
    router = APIRouter(prefix="/api/tentacle", tags=["tentacle"])

    # ``require_auth`` is the deployment boundary.  Missing credentials must
    # never silently turn an explicitly protected dashboard into an anonymous
    # one; local callers that want the historical unauthenticated dashboard
    # opt into it by leaving ``require_auth`` disabled.
    _enforce_auth = require_auth

    def _require_http_auth(request: FastAPIRequest) -> None:
        """FastAPI dependency: enforce auth on HTTP endpoints when enabled."""
        if not _enforce_auth:
            return
        if identity_store is None:
            raise HTTPException(401, "tentacle identity store unavailable")
        token: str | None = None
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            raise HTTPException(401, "missing tentacle auth token")
        if jwt_secret and token.count(".") == 2:
            identity = identity_store.verify_jwt(
                token,
                secret=jwt_secret,
                required_issuer=jwt_issuer,
                required_audience=jwt_audience,
            )
            if identity is not None:
                return
        if identity_store.verify_api_key(token) is not None:
            return
        raise HTTPException(401, "invalid tentacle auth token")

    # 任务历史记录（内存，重启清空）
    _task_history: list[dict[str, Any]] = []

    def _resolve_ws_actor(ws: WebSocket) -> str | None:
        if identity_store is None:
            if _enforce_auth:
                raise PermissionError("identity store required for tentacle auth")
            return None
        token: str | None = None
        auth_header = ""
        try:
            auth_header = ws.headers.get("authorization") or ""
        except Exception:  # noqa: BLE001
            auth_header = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if token is None:
            token = websocket_bearer_token(ws)
        if not token:
            if _enforce_auth:
                raise PermissionError("missing tentacle auth token")
            return None
        if jwt_secret and token.count(".") == 2:
            identity = identity_store.verify_jwt(
                token,
                secret=jwt_secret,
                required_issuer=jwt_issuer,
                required_audience=jwt_audience,
            )
            if identity is not None:
                return identity.actor_id
            if _enforce_auth:
                raise PermissionError("invalid jwt")
        identity = identity_store.verify_api_key(token)
        if identity is not None:
            return identity.actor_id
        if _enforce_auth:
            raise PermissionError("invalid token")
        return None

    # ── Dashboard HTML ──────────────────────────────────

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(_DASHBOARD_HTML)

    # ── 设备列表 ────────────────────────────────────────

    @router.get("/devices", dependencies=[Depends(_require_http_auth)])
    async def list_devices() -> list[dict[str, Any]]:
        coordinator.pool.all_online()
        all_devices = list(coordinator.pool._tentacles.values())
        result = []
        for d in all_devices:
            result.append(
                {
                    "tentacle_id": d.tentacle_id,
                    "type": d.tentacle_type.value,
                    "platform": getattr(d, "platform", "unknown"),
                    "status": d.status.value,
                    "is_online": d.is_online,
                    "is_busy": d.is_busy,
                    "capabilities": d.capabilities[:5]
                    if len(d.capabilities) > 5
                    else d.capabilities,
                    "total_capabilities": len(d.capabilities),
                    "last_used_ago": round(time.time() - d.last_used_at, 1)
                    if d.last_used_at > 0
                    else None,
                    "meta": d.meta if hasattr(d, "meta") else {},
                }
            )
        return result

    # ── 设备详情 ────────────────────────────────────────

    @router.get("/devices/{tentacle_id}", dependencies=[Depends(_require_http_auth)])
    async def device_detail(tentacle_id: str) -> dict[str, Any]:
        device = coordinator.pool.get(tentacle_id)
        if device is None:
            raise HTTPException(404, f"Device {tentacle_id} not found")
        return {
            "tentacle_id": device.tentacle_id,
            "type": device.tentacle_type.value,
            "platform": getattr(device, "platform", "unknown"),
            "status": device.status.value,
            "is_online": device.is_online,
            "capabilities": device.capabilities,
            "meta": device.meta if hasattr(device, "meta") else {},
        }

    # ── 提交任务 ────────────────────────────────────────

    @router.post("/task", dependencies=[Depends(_require_http_auth)])
    async def submit_task(body: dict[str, Any]) -> dict[str, Any]:
        task = body.get("task", "").strip()
        tentacle_id = body.get("tentacle_id", "")
        if not task:
            raise HTTPException(400, "Missing 'task' field")
        if not tentacle_id:
            # 自动选择第一个在线设备
            devices = coordinator.pool.all_online()
            if not devices:
                raise HTTPException(404, "No online devices")
            tentacle_id = devices[0].tentacle_id

        device = coordinator.pool.get(tentacle_id)
        if device is None:
            raise HTTPException(404, f"Device {tentacle_id} not found")
        if not device.is_online:
            raise HTTPException(400, f"Device {tentacle_id} is offline")

        if coordinator._decision_engine is None:
            raise HTTPException(400, "No decision engine configured")

        task_id = f"web-{int(time.time() * 1000)}"
        start = time.time()

        try:
            tool_calls = await coordinator._decision_engine(task, device)
        except Exception as e:
            _task_history.append(
                {
                    "task_id": task_id,
                    "task": task,
                    "tentacle_id": tentacle_id,
                    "success": False,
                    "error": str(e),
                    "steps": 0,
                    "duration_ms": int((time.time() - start) * 1000),
                    "timestamp": time.time(),
                }
            )
            raise HTTPException(500, f"Decision engine error: {e}") from e

        results = []
        for call in tool_calls:
            result = await device.execute(call)
            results.append(
                {
                    "call_id": call.call_id,
                    "tool": call.tool,
                    "args": call.args,
                    "success": result.success,
                    "data": result.data,
                    "error": result.error_message,
                    "duration_ms": result.duration_ms,
                }
            )
            if not result.success:
                break

        success = all(r["success"] for r in results) if results else False
        duration = int((time.time() - start) * 1000)

        record = {
            "task_id": task_id,
            "task": task,
            "tentacle_id": tentacle_id,
            "success": success,
            "steps": len(results),
            "results": results,
            "duration_ms": duration,
            "timestamp": time.time(),
        }
        _task_history.append(record)
        # 只保留最近 100 条
        if len(_task_history) > 100:
            _task_history.pop(0)

        return record

    # ── 群发（一对多群控） ──────────────────────────────

    @router.post("/broadcast", dependencies=[Depends(_require_http_auth)])
    async def broadcast_task(body: dict[str, Any]) -> dict[str, Any]:
        """把同一任务并发下发到多台设备并聚合结果。

        body: ``{task, tentacle_ids?, max_concurrency?}``——``tentacle_ids`` 省略
        或为 null 时对所有在线设备群发。单台失败被隔离，不影响其它设备。
        """
        task = body.get("task", "").strip()
        if not task:
            raise HTTPException(400, "Missing 'task' field")
        tentacle_ids = body.get("tentacle_ids")
        if tentacle_ids is not None and not isinstance(tentacle_ids, list):
            raise HTTPException(400, "'tentacle_ids' must be a list or omitted")
        try:
            max_concurrency = int(body.get("max_concurrency", 8) or 8)
        except (TypeError, ValueError):
            raise HTTPException(400, "'max_concurrency' must be an integer") from None

        start = time.time()
        result = await fleet_broadcast(
            coordinator, task, tentacle_ids, max_concurrency=max_concurrency
        )

        record = {
            "task_id": f"web-bcast-{int(time.time() * 1000)}",
            "task": task,
            "broadcast": True,
            "success": result["ok"],
            "total": result["total"],
            "succeeded": result["succeeded"],
            "failed": result["failed"],
            "results": result["results"],
            "duration_ms": int((time.time() - start) * 1000),
            "timestamp": time.time(),
        }
        _task_history.append(record)
        if len(_task_history) > 100:
            _task_history.pop(0)
        return record

    # ── 任务历史 ────────────────────────────────────────

    @router.get("/tasks", dependencies=[Depends(_require_http_auth)])
    async def list_tasks() -> list[dict[str, Any]]:
        return list(reversed(_task_history))

    # ── 协调器统计 ──────────────────────────────────────

    @router.get("/stats", dependencies=[Depends(_require_http_auth)])
    async def stats() -> dict[str, Any]:
        return coordinator.stats()

    # ── VLM 视觉分析 ────────────────────────────────────

    @router.post("/devices/{tentacle_id}/analyze", dependencies=[Depends(_require_http_auth)])
    async def vlm_analyze(tentacle_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """手动触发 VLM 分析设备屏幕.

        请求体::

            {"task": "描述你想做什么"}

        返回::

            {
                "description": "屏幕内容描述",
                "suggested_actions": [...],
                "current_app": "当前应用",
                "screen_state": "屏幕状态",
                "vlm_usage": {...}
            }
        """
        task = body.get("task", "").strip()
        if not task:
            raise HTTPException(400, "Missing 'task' field")

        device = coordinator.pool.get(tentacle_id)
        if device is None:
            raise HTTPException(404, f"Device {tentacle_id} not found")
        if not device.is_online:
            raise HTTPException(400, f"Device {tentacle_id} is offline")

        # 1. 截图
        screenshot_call = ToolCall(
            call_id=f"vlm-analyze-{int(time.time() * 1000)}",
            tentacle_id=tentacle_id,
            tool="android.take_screenshot",
            args={},
        )
        screenshot_result = await device.execute(screenshot_call)
        if not screenshot_result.success:
            raise HTTPException(500, f"Screenshot failed: {screenshot_result.error_message}")

        # 获取截图 base64
        screenshot_b64 = ""
        if isinstance(screenshot_result.data, dict):
            screenshot_b64 = screenshot_result.data.get("screenshot", "")
        elif isinstance(screenshot_result.data, str):
            screenshot_b64 = screenshot_result.data

        if not screenshot_b64:
            raise HTTPException(500, "Screenshot data is empty")

        # 2. 获取无障碍树（可选）
        screen_info = None
        screen_info_call = ToolCall(
            call_id=f"vlm-screeninfo-{int(time.time() * 1000)}",
            tentacle_id=tentacle_id,
            tool="android.get_screen_info",
            args={},
        )
        screen_info_result = await device.execute(screen_info_call)
        if screen_info_result.success and isinstance(screen_info_result.data, dict):
            screen_info = screen_info_result.data

        # 3. 调用 VLM 分析
        try:
            from runtime.tentacle.mobile.vlm import VlmClient

            # 从协调器获取 VLM 客户端（如果已配置）
            vlm_client: VlmClient | None = getattr(coordinator, "_vlm_client", None)
            if vlm_client is None:
                # 尝试从环境变量自动配置
                vlm_config = _auto_detect_vlm_config()
                if vlm_config is None:
                    raise HTTPException(
                        400,
                        "VLM not configured. Set VLM_API_KEY env var or use with_vlm() factory.",
                    )
                vlm_client = VlmClient(vlm_config)
                coordinator._vlm_client = vlm_client  # 缓存到协调器

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

            result = analysis.to_dict()
            result["vlm_usage"] = vlm_client.usage.to_dict()
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.exception("VLM analysis failed")
            raise HTTPException(500, f"VLM analysis failed: {e}") from e

    # ── 屏幕流：获取设备最新截图 ────────────────────────

    @router.get("/devices/{tentacle_id}/screenshot", dependencies=[Depends(_require_http_auth)])
    async def device_screenshot(tentacle_id: str) -> Response:
        """获取设备最新截图（JPEG）.

        返回设备最近推送的 JPEG 帧，若无截图返回 404.
        """
        screen_relay = getattr(coordinator, "screen_relay", None)
        if screen_relay is None:
            raise HTTPException(503, "Screen relay not available")

        jpeg_data = screen_relay.get_screenshot(tentacle_id)
        if jpeg_data is None:
            raise HTTPException(404, f"No screenshot available for device {tentacle_id}")

        return Response(
            content=jpeg_data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache, no-store"},
        )

    # ── 屏幕流：获取 WebSocket URL ──────────────────────

    @router.post("/screen/subscribe", dependencies=[Depends(_require_http_auth)])
    async def screen_subscribe(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """返回屏幕流 WebSocket URL 供前端连接.

        客户端连接 WS 后发送 JSON 订阅/取消订阅消息.
        """
        screen_relay = getattr(coordinator, "screen_relay", None)
        if screen_relay is None:
            raise HTTPException(503, "Screen relay not available")

        # 构建 WebSocket URL
        ws_url = f"ws://localhost:{coordinator._dashboard_port}/api/tentacle/screen/stream"

        return {
            "ws_url": ws_url,
            "protocol": "binary",
            "frame_format": {
                "header": "4 bytes (uint16 tid_len + uint8 frame_type + uint8 flags) + tid_bytes",
                "frame_types": {
                    "0x01": "H.264",
                    "0x02": "JPEG",
                    "0x03": "WebP",
                },
            },
            "subscribe_message": {"action": "subscribe", "tentacle_id": "xxx"},
            "unsubscribe_message": {"action": "unsubscribe", "tentacle_id": "xxx"},
        }

    # ── 屏幕流：WebSocket 端点 ──────────────────────────

    @router.websocket("/screen/stream")
    async def screen_stream(ws: WebSocket) -> None:
        """屏幕流 WebSocket 端点.

        浏览器客户端连接此端点接收屏幕流.

        客户端发送 JSON:
          - {"action": "subscribe", "tentacle_id": "xxx"} — 订阅设备屏幕流
          - {"action": "unsubscribe", "tentacle_id": "xxx"} — 取消订阅

        服务端推送二进制帧（格式同 ScreenRelay）.
        """
        try:
            _resolve_ws_actor(ws)
        except PermissionError as exc:
            with suppress(Exception):
                await ws.close(code=4401, reason=str(exc))
            return
        screen_relay = getattr(coordinator, "screen_relay", None)
        if screen_relay is None:
            await ws.close(code=1011, reason="Screen relay not available")
            return

        await ws.accept(subprotocol=accepted_auth_subprotocol(ws))
        logger.info("screen stream client connected: %s", ws.client)

        try:
            # 持续接收客户端的控制消息
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                action = msg.get("action", "")
                tentacle_id = msg.get("tentacle_id", "")

                if not tentacle_id:
                    await ws.send_json({"type": "error", "message": "Missing tentacle_id"})
                    continue

                if action == "subscribe":
                    # 检查设备是否存在
                    device = coordinator.pool.get(tentacle_id)
                    if device is None or not device.is_online:
                        # 设备不在线时，尝试启动 mock 流
                        await screen_relay.add_subscriber(tentacle_id, ws)
                        await screen_relay.start_mock_stream(tentacle_id)
                        await ws.send_json(
                            {
                                "type": "subscribed",
                                "tentacle_id": tentacle_id,
                                "mode": "mock",
                                "message": "Device offline, using mock stream",
                            }
                        )
                    else:
                        await screen_relay.add_subscriber(tentacle_id, ws)
                        await ws.send_json(
                            {
                                "type": "subscribed",
                                "tentacle_id": tentacle_id,
                                "mode": "live",
                            }
                        )
                    logger.info(
                        "screen subscribe: ws=%s device=%s",
                        ws.client,
                        tentacle_id,
                    )

                elif action == "unsubscribe":
                    await screen_relay.unsubscribe(tentacle_id, ws)
                    await ws.send_json(
                        {
                            "type": "unsubscribed",
                            "tentacle_id": tentacle_id,
                        }
                    )
                    logger.info(
                        "screen unsubscribe: ws=%s device=%s",
                        ws.client,
                        tentacle_id,
                    )

                else:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown action: {action}",
                        }
                    )

        except WebSocketDisconnect:
            logger.info("screen stream client disconnected: %s", ws.client)
        except Exception as e:
            logger.warning("screen stream error: %s", e)
        finally:
            # 清理：移除该客户端的所有订阅
            await screen_relay.remove_subscriber(ws)

    # ── PC 屏幕流 ────────────────────────────────────────────

    @router.websocket("/pc-screen/stream")
    async def pc_screen_stream(ws: WebSocket) -> None:
        """PC 屏幕流 WebSocket 端点.

        浏览器客户端连接此端点接收 PC 屏幕画面.
        协议与设备屏幕流相同（二进制帧格式）.
        """
        try:
            _resolve_ws_actor(ws)
        except PermissionError as exc:
            with suppress(Exception):
                await ws.close(code=4401, reason=str(exc))
            return
        screen_relay = getattr(coordinator, "screen_relay", None)
        if screen_relay is None:
            await ws.close(code=1011, reason="Screen relay not available")
            return

        await ws.accept(subprotocol=accepted_auth_subprotocol(ws))
        logger.info("PC screen stream client connected: %s", ws.client)

        # 自动订阅 pc-host
        from runtime.tentacle.mobile.pc_screen_capture import PC_HOST_ID

        await screen_relay.add_subscriber(PC_HOST_ID, ws)

        try:
            # 持续保持连接，等待客户端断开
            while True:
                # 接收客户端消息（心跳/控制）
                await ws.receive_text()
                # 忽略客户端消息，只用于保持连接
        except WebSocketDisconnect:
            logger.info("PC screen stream client disconnected: %s", ws.client)
        except Exception as e:
            logger.warning("PC screen stream error: %s", e)
        finally:
            await screen_relay.unsubscribe(PC_HOST_ID, ws)

    @router.post("/pc-screen/start", dependencies=[Depends(_require_http_auth)])
    async def pc_screen_start(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """启动 PC 屏幕捕获."""
        from runtime.tentacle.mobile.pc_screen_capture import PcScreenCapture, PcScreenConfig

        if coordinator.pc_screen_capture is not None and coordinator.pc_screen_capture.is_running:
            return {"status": "already_running", "stats": coordinator.pc_screen_capture.stats}

        # 解析可选配置
        config = PcScreenConfig()
        if body:
            if "fps" in body:
                config.fps = int(body["fps"])
            if "scale" in body:
                config.scale = float(body["scale"])
            if "quality" in body:
                config.jpeg_quality = int(body["quality"])

        try:
            coordinator.pc_screen_capture = PcScreenCapture(coordinator.screen_relay, config=config)
            await coordinator.pc_screen_capture.start()
            return {"status": "started", "stats": coordinator.pc_screen_capture.stats}
        except Exception as e:
            raise HTTPException(500, f"Failed to start PC screen capture: {e}") from e

    @router.post("/pc-screen/stop", dependencies=[Depends(_require_http_auth)])
    async def pc_screen_stop() -> dict[str, Any]:
        """停止 PC 屏幕捕获."""
        if coordinator.pc_screen_capture is None:
            return {"status": "not_running"}

        stats = coordinator.pc_screen_capture.stats
        await coordinator.pc_screen_capture.stop()
        coordinator.pc_screen_capture = None
        return {"status": "stopped", "last_stats": stats}

    @router.get("/pc-screen/stats", dependencies=[Depends(_require_http_auth)])
    async def pc_screen_stats() -> dict[str, Any]:
        """获取 PC 屏幕捕获统计."""
        if coordinator.pc_screen_capture is None:
            return {"running": False}
        return coordinator.pc_screen_capture.stats

    # ── 远程输入（手机→PC控制）──────────────────────────────

    @router.post("/remote-input", dependencies=[Depends(_require_http_auth)])
    async def remote_input(body: dict[str, Any]) -> dict[str, Any]:
        """远程输入事件端点.

        接收来自前端/手机的输入事件，转发为PC鼠标/键盘操作.

        请求体::

            {
                "action": "tap",       # tap|double_tap|long_press|swipe|type_text|key_press
                "x": 0.5, "y": 0.3,   # 归一化坐标 [0,1]
                "x2": 0.8, "y2": 0.6,  # swipe 终点
                "duration_ms": 300,
                "text": "hello",
                "key": "enter"
            }
        """
        handler = coordinator.remote_input_handler
        if handler is None:
            raise HTTPException(
                400, "Remote input not enabled. Start coordinator with remote_input=True"
            )

        return await handler.handle_input(body)

    # ── MCP SSE Transport ────────────────────────────────────

    # SSE 会话管理器（延迟初始化）
    _mcp_session_manager: Any | None = None

    def _get_mcp_session_manager() -> Any:
        """获取或创建 MCP SSE 会话管理器."""
        nonlocal _mcp_session_manager
        if _mcp_session_manager is None:
            from runtime.tentacle.mobile.mcp_server import SseSessionManager, TentacleMcpServer

            mcp_server = TentacleMcpServer(coordinator=coordinator)
            _mcp_session_manager = SseSessionManager(mcp_server)
        return _mcp_session_manager

    @router.get("/mcp/sse")
    async def mcp_sse(request: FastAPIRequest) -> StreamingResponse:
        """MCP SSE 连接端点.

        客户端连接此端点获取 SSE 事件流，服务端首先发送 endpoint 事件
        告知客户端消息发送 URL，然后持续推送 JSON-RPC 响应。

        账号登录鉴权：网关开启 ``require_auth`` 时，Claude Desktop /
        Cursor 等 MCP 客户端必须携带账号登录后的 Bearer Token
        （``Authorization`` 请求头或 ``?token=`` 查询参数）。会话会
        绑定到该账号，后续 ``/mcp/message`` 也要求同一账号凭证。
        """
        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        actor_id = principal.actor_id if principal is not None else None

        manager = _get_mcp_session_manager()
        session = manager.create_session(actor_id=actor_id)

        # 构建 message endpoint URL
        scheme = "https" if request.url.scheme == "https" else "http"
        host = request.headers.get("host", f"localhost:{coordinator._dashboard_port}")
        message_url = f"{scheme}://{host}/api/tentacle/mcp/message?session_id={session.session_id}"

        async def _sse_generator():
            """SSE 事件流生成器."""
            try:
                # 首先发送 endpoint 事件
                yield f"event: endpoint\ndata: {message_url}\n\n"
                # 然后持续推送 JSON-RPC 响应
                async for chunk in session.event_stream():
                    yield chunk
            finally:
                # 客户端断开（含异常退出）时清理会话，避免泄漏
                manager.remove_session(session.session_id)

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/mcp/message")
    async def mcp_message(request: FastAPIRequest) -> dict[str, Any]:
        """MCP 消息接收端点.

        客户端通过此端点发送 JSON-RPC 请求，服务端处理后通过 SSE 推送响应。
        与 ``/mcp/sse`` 一致要求账号凭证；若会话已绑定账号，则必须由
        同一账号携带相同凭证调用。
        """
        session_id = request.query_params.get("session_id", "")
        if not session_id:
            raise HTTPException(400, "Missing session_id parameter")

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        actor_id = principal.actor_id if principal is not None else None

        manager = _get_mcp_session_manager()
        session = manager.get_session(session_id)
        if session is None:
            raise HTTPException(404, f"Session not found: {session_id}")
        if session.actor_id is not None and session.actor_id != actor_id:
            raise HTTPException(403, "Session belongs to a different account")

        try:
            body = await request.json()
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON: {e}") from e

        # 异步处理请求并通过 SSE 推送响应
        asyncio.create_task(session.handle_message(body))

        return {"status": "ok"}

    return router
