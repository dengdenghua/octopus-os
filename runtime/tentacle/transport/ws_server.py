"""WebSocket Server —— echo-agent 端接收 Echo Mobile 连接.

协议栈：JSON-RPC 2.0 over WebSocket

消息类型：
  - device/hello    : 设备首次连接时上报元信息
  - device/heartbeat: 每 30s 心跳
  - device/screen   : 屏幕变化事件（增量上报）
  - task/execute    : Mobile → Runtime 请求母体决策任务
  - task/result     : Runtime → Mobile 返回任务结果
  - tool/execute    : Runtime → Mobile 执行工具
  - tool/result     : Mobile → Runtime 返回工具结果
  - error           : 错误响应

设计原则：
  - 无状态：所有设备状态存在 TentaclePool
  - 异步：所有 handler 都是 async
  - 可观测：通过 Nerves 总线广播事件
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # websockets >= 14
    from websockets.asyncio.server import ServerConnection as _WebSocketConnection
    from websockets.asyncio.server import serve
    from websockets.protocol import State as _WebSocketState
except ImportError:  # pragma: no cover - compatibility for older deployments
    from websockets.server import WebSocketServerProtocol as _WebSocketConnection
    from websockets.server import serve

    _WebSocketState = None  # type: ignore[assignment]

from ..base import Heartbeat, ToolCall, ToolResult, now_ms

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})

# Pairing-token brute-force throttle · once an IP accumulates this many failed
# device/hello attempts within the sliding window, further connections from it
# are refused until the failures age out.
_HELLO_MAX_FAILURES = 5
_HELLO_WINDOW_SECONDS = 60.0

WebSocketConnection = _WebSocketConnection


def _ws_is_open(ws: WebSocketConnection) -> bool:
    """Return whether a websockets connection can still send frames."""
    if hasattr(ws, "closed"):
        return not bool(ws.closed)
    if _WebSocketState is not None and hasattr(ws, "state"):
        return ws.state is _WebSocketState.OPEN
    return True


def _ws_remote_address(ws: WebSocketConnection) -> tuple[str, int | None]:
    addr = getattr(ws, "remote_address", None)
    if isinstance(addr, tuple) and addr:
        host = str(addr[0])
        port = addr[1] if len(addr) > 1 and isinstance(addr[1], int) else None
        return host, port
    return "unknown", None


def _ws_remote_label(ws: WebSocketConnection) -> str:
    host, port = _ws_remote_address(ws)
    return f"{host}:{port}" if port is not None else host


def _ws_request_path(ws: WebSocketConnection) -> str:
    request = getattr(ws, "request", None)
    path = getattr(request, "path", None)
    if isinstance(path, str):
        return path
    path = getattr(ws, "path", None)
    return path if isinstance(path, str) else ""


# ── 消息类型常量 ──────────────────────────────────────────

MSG_DEVICE_HELLO = "device/hello"
MSG_DEVICE_HEARTBEAT = "device/heartbeat"
MSG_DEVICE_SCREEN = "device/screen"
MSG_DEVICE_SCREEN_FRAME = "device/screen_frame"  # 设备推送视频帧（二进制）
MSG_SCREEN_SUBSCRIBE = "screen/subscribe"  # 客户端订阅某设备的屏幕流
MSG_SCREEN_UNSUBSCRIBE = "screen/unsubscribe"  # 客户端取消订阅
MSG_REMOTE_INPUT = "remote/input"  # 手机端发送远程输入事件（控制PC）
MSG_PC_SCREEN_SUBSCRIBE = "pc_screen/subscribe"  # 手机端订阅PC屏幕流
MSG_PC_SCREEN_UNSUBSCRIBE = "pc_screen/unsubscribe"  # 手机端取消订阅PC屏幕流
MSG_TASK_EXECUTE = "task/execute"
MSG_TASK_RESULT = "task/result"
MSG_TOOL_EXECUTE = "tool/execute"
MSG_TOOL_RESULT = "tool/result"
MSG_ERROR = "error"
MSG_PING = "ping"
MSG_PONG = "pong"


# ── 数据类 ────────────────────────────────────────────────


class DeviceHello:
    """设备首次连接上报的元信息."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.tentacle_id: str = payload["tentacle_id"]
        self.platform: str = payload.get("platform", "android")
        self.brand: str = payload.get("brand", "Unknown")
        self.model: str = payload.get("model", "Unknown")
        self.android_version: str = payload.get("android_version", "14")
        self.sdk: int = payload.get("sdk", 34)
        self.screen_size: list[int] = payload.get("screen_size", [1080, 2400])
        self.capabilities: list[str] = payload.get("capabilities", [])
        self.version: str = payload.get("version", "0.0.0")
        # iOS 专用字段（仅 platform == "ios" 时填充）
        self.ios_version: str = payload.get("ios_version", "17.0")
        self.udid: str = payload.get("udid", "")
        self.wda_port: int = payload.get("wda_port", 8100)
        self.bundle_id: str | None = payload.get("bundle_id")

    def to_meta(self) -> dict[str, Any]:
        if self.platform == "ios":
            return {
                "platform": "ios",
                "model": self.model,
                "ios_version": self.ios_version,
                "udid": self.udid,
                "wda_url": f"http://localhost:{self.wda_port}",
                "bundle_id": self.bundle_id,
                "screen_size": self.screen_size,
                "version": self.version,
            }
        return {
            "brand": self.brand,
            "model": self.model,
            "android_version": self.android_version,
            "sdk": self.sdk,
            "screen_size": self.screen_size,
            "version": self.version,
        }


class TaskExecuteRequest:
    """手机端请求母体执行任务."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.task_id: str = payload.get("task_id", "")
        self.task: str = payload.get("task", "")
        self.intent: str = payload.get("intent", "")
        self.confidence: float = payload.get("confidence", 0.0)
        self.tentacle_id: str = payload.get("tentacle_id", "")


# ── WebSocket Server ──────────────────────────────────────


class TentacleWebSocketServer:
    """触手 WebSocket 服务器.

    负责：
    1. 接收 Echo Mobile 连接
    2. 处理 device/hello 注册设备
    3. 处理 device/heartbeat 保活
    4. 处理 task/execute —— Mobile 请求母体决策
    5. 转发 tool/execute → Mobile，tool/result → Runtime
    6. 断线自动清理 TentaclePool

    Args:
        host: 监听地址（默认 0.0.0.0）
        port: 监听端口（默认 8765）
        on_device_hello: 设备首次连接回调 → 注册到 TentaclePool
        on_device_disconnect: 设备断开回调 → 从 TentaclePool 注销
        on_tool_result: 工具执行结果回调
        on_task_execute: 任务执行请求回调（Mobile → Runtime）
    """

    def __init__(
        self,
        host: str = "0.0.0.0",  # nosec B104 — tentacle WS server, intentional LAN bind
        port: int = 8765,
        *,
        on_device_hello: Callable[[DeviceHello, WebSocketConnection], Awaitable[None]]
        | None = None,
        on_device_disconnect: Callable[[str], Awaitable[None]] | None = None,
        on_tool_result: Callable[[ToolResult], Awaitable[None]] | None = None,
        on_heartbeat: Callable[[Heartbeat], Awaitable[None]] | None = None,
        on_task_execute: Callable[[TaskExecuteRequest, WebSocketConnection], Awaitable[None]]
        | None = None,
        on_screen_frame: Callable[[str, bytes], Awaitable[None]] | None = None,
        on_remote_input: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        on_custom: Callable[[str | None, dict[str, Any], WebSocketConnection], Awaitable[None]]
        | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        # Shared secret a device must present in its ``device/hello`` to
        # be allowed to register and drive the mother brain. Falls back
        # to the ``ECHO_TENTACLE_TOKEN`` env var. When unset AND the
        # bind host is loopback, auth is skipped (local dev). When unset
        # AND bound to a routable interface (the default 0.0.0.0, needed
        # for phones on the LAN), every connection is rejected — an
        # exposed port with no secret to check against is never safe.
        self.auth_token = (
            auth_token if auth_token is not None else os.environ.get("ECHO_TENTACLE_TOKEN") or None
        )
        self.on_device_hello = on_device_hello
        self.on_device_disconnect = on_device_disconnect
        self.on_tool_result = on_tool_result
        self.on_heartbeat = on_heartbeat
        self.on_task_execute = on_task_execute
        self.on_screen_frame = on_screen_frame
        self.on_remote_input = on_remote_input
        self.on_custom = on_custom  # 未识别方法的兜底钩子（webrtc 信令等自定义消息）

        # tentacle_id → WebSocket 连接映射
        self._connections: dict[str, WebSocketConnection] = {}
        # 等待 tool/result 的 future：call_id → asyncio.Future
        self._pending_calls: dict[str, asyncio.Future[ToolResult]] = {}

        self._server: asyncio.Server | None = None
        self._stop_event = asyncio.Event()
        # remote IP → recent failed device/hello timestamps (monotonic), for
        # the pairing-token brute-force throttle (see _hello_rate_limited).
        self._hello_failures: dict[str, list[float]] = {}

    # ── 生命周期 ────────────────────────────────────────────

    def _is_loopback(self) -> bool:
        return self.host in _LOOPBACK_HOSTS

    def _conn_pre_authenticated(self) -> bool:
        """A fresh connection is trusted only on a loopback bind with no
        token configured. Any token (even on loopback) must be presented;
        any non-loopback bind must present a token."""
        return self.auth_token is None and self._is_loopback()

    def _check_auth(self, msg: dict[str, Any]) -> bool:
        """Validate the token carried by a ``device/hello`` message."""
        if self.auth_token is None:
            # No secret configured: only acceptable on a loopback bind.
            return self._is_loopback()
        params = msg.get("params") or {}
        provided = params.get("auth_token") or params.get("token") or ""
        return hmac.compare_digest(str(provided), self.auth_token)

    def _hello_rate_limited(self, ip: str) -> bool:
        """True if ``ip`` has used up its device/hello failure budget within the
        sliding window. Throttles pairing-token brute force: each failed hello
        is recorded; once the window holds _HELLO_MAX_FAILURES attempts the IP
        is refused until older failures age out."""
        now = time.monotonic()
        recent = [t for t in self._hello_failures.get(ip, ()) if now - t < _HELLO_WINDOW_SECONDS]
        if recent:
            self._hello_failures[ip] = recent
        else:
            self._hello_failures.pop(ip, None)
        return len(recent) >= _HELLO_MAX_FAILURES

    def _record_hello_failure(self, ip: str) -> None:
        self._hello_failures.setdefault(ip, []).append(time.monotonic())

    def _clear_hello_failures(self, ip: str) -> None:
        self._hello_failures.pop(ip, None)

    async def start(self) -> None:
        """启动 WebSocket 服务器."""
        if self.auth_token is None and not self._is_loopback():
            logger.warning(
                "TentacleWebSocketServer bound to %s with NO auth token — "
                "every connection will be REJECTED. Set ECHO_TENTACLE_TOKEN "
                "(or pass auth_token=) to allow devices, or bind 127.0.0.1.",
                self.host,
            )
        logger.info("TentacleWebSocketServer starting on %s:%d", self.host, self.port)
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
        )
        logger.info("TentacleWebSocketServer listening on ws://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """停止服务器."""
        self._stop_event.set()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # 取消所有 pending calls
        for future in self._pending_calls.values():
            future.cancel()
        self._pending_calls.clear()
        logger.info("TentacleWebSocketServer stopped")

    # ── 向设备发送指令 ──────────────────────────────────────

    async def send_json(self, tentacle_id: str, obj: dict[str, Any]) -> bool:
        """向指定设备发送一条任意 JSON 消息（webrtc 信令等用）。"""
        ws = self._connections.get(tentacle_id)
        if ws is None or not _ws_is_open(ws):
            return False
        await ws.send(json.dumps(obj))
        return True

    async def send_tool_execute(
        self,
        tentacle_id: str,
        call: ToolCall,
        timeout_ms: int = 15_000,
    ) -> ToolResult:
        """向指定设备发送工具执行请求，等待结果.

        这是 Runtime → Mobile 的核心通路.
        """
        ws = self._connections.get(tentacle_id)
        if ws is None or not _ws_is_open(ws):
            return ToolResult.fail(call.call_id, -32011, f"Device {tentacle_id} not connected", 0)

        # 创建 future 等待结果
        future: asyncio.Future[ToolResult] = asyncio.get_event_loop().create_future()
        self._pending_calls[call.call_id] = future

        # 发送请求
        msg = {
            "jsonrpc": "2.0",
            "method": MSG_TOOL_EXECUTE,
            "params": call.to_dict(),
            "id": call.call_id,
        }
        try:
            await ws.send(json.dumps(msg))
            # 等待结果（带超时）
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000.0)
        except TimeoutError:
            return ToolResult.fail(
                call.call_id, -32012, f"Timeout after {timeout_ms}ms", timeout_ms
            )
        except Exception as e:
            return ToolResult.fail(call.call_id, -32013, f"Send failed: {e}", 0)
        finally:
            self._pending_calls.pop(call.call_id, None)

    async def send_task_result(
        self,
        tentacle_id: str,
        task_id: str,
        success: bool,
        response: str,
        steps: int = 0,
    ) -> bool:
        """向设备发送任务执行结果.

        这是 Runtime → Mobile 的任务结果返回.
        """
        ws = self._connections.get(tentacle_id)
        if ws is None or not _ws_is_open(ws):
            return False

        msg = {
            "jsonrpc": "2.0",
            "method": MSG_TASK_RESULT,
            "params": {
                "task_id": task_id,
                "success": success,
                "response": response,
                "steps": steps,
            },
            "id": task_id,
        }
        try:
            await ws.send(json.dumps(msg))
            return True
        except Exception as e:
            logger.warning("send_task_result failed: %s", e)
            return False

    async def send_ping(self, tentacle_id: str) -> bool:
        """向设备发送 ping，检测连接健康."""
        ws = self._connections.get(tentacle_id)
        if ws is None or not _ws_is_open(ws):
            return False
        try:
            await ws.send(
                json.dumps({"jsonrpc": "2.0", "method": MSG_PING, "id": f"ping-{now_ms()}"})
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            return False

    # ── 连接处理 ────────────────────────────────────────────

    async def _handle_connection(self, ws: WebSocketConnection, path: str | None = None) -> None:
        """处理单个 WebSocket 连接."""
        path = path if path is not None else _ws_request_path(ws)
        remote = _ws_remote_label(ws)
        logger.info("ws connection from %s path=%s", remote, path)
        tentacle_id: str | None = None
        # Until a device authenticates (valid device/hello token), no
        # other method — and no binary frame — is processed. A loopback
        # bind with no token configured starts pre-authenticated.
        authenticated = self._conn_pre_authenticated()

        # Pairing-token brute-force throttle · only bites when auth is actually
        # required. A loopback no-token dev bind is pre-authenticated and skips
        # this entirely.
        client_ip, _client_port = _ws_remote_address(ws)
        if not authenticated and self._hello_rate_limited(client_ip):
            logger.warning("ws hello rate-limited for %s", client_ip)
            with contextlib.suppress(Exception):
                await ws.close(code=1008, reason="rate limited")
            return

        try:
            async for raw in ws:
                if not authenticated:
                    # Only a text device/hello carrying a valid token can
                    # open the connection. Anything else is rejected.
                    if isinstance(raw, bytes):
                        await ws.close(code=1008, reason="unauthorized")
                        return
                    try:
                        hello_msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await self._send_error(ws, None, -32700, "Parse error")
                        continue
                    if hello_msg.get("method") == MSG_DEVICE_HELLO and self._check_auth(hello_msg):
                        authenticated = True
                        self._clear_hello_failures(client_ip)
                        tentacle_id = await self._handle_hello(ws, hello_msg)
                        continue
                    self._record_hello_failure(client_ip)
                    logger.warning("ws unauthorized hello from %s", remote)
                    await self._send_error(
                        ws,
                        hello_msg.get("id"),
                        -32099,
                        "unauthorized: device/hello with a valid token required",
                    )
                    await ws.close(code=1008, reason="unauthorized")
                    return

                # 二进制帧：设备推送 screen_frame
                if isinstance(raw, bytes):
                    await self._handle_binary_frame(ws, raw, tentacle_id)
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send_error(ws, None, -32700, "Parse error")
                    continue

                method = msg.get("method", "")
                msg_id = msg.get("id")

                if method == MSG_DEVICE_HELLO:
                    tentacle_id = await self._handle_hello(ws, msg)
                elif method == MSG_DEVICE_HEARTBEAT:
                    await self._handle_heartbeat(ws, msg)
                elif method == MSG_DEVICE_SCREEN:
                    await self._handle_screen(ws, msg)
                elif method == MSG_TASK_EXECUTE:
                    await self._handle_task_execute(ws, msg)
                elif method == MSG_TOOL_RESULT:
                    await self._handle_tool_result(ws, msg)
                elif method == MSG_REMOTE_INPUT:
                    await self._handle_remote_input(ws, msg, tentacle_id)
                elif method == MSG_PC_SCREEN_SUBSCRIBE:
                    await self._handle_pc_screen_subscribe(ws, msg, tentacle_id)
                elif method == MSG_PC_SCREEN_UNSUBSCRIBE:
                    await self._handle_pc_screen_unsubscribe(ws, msg, tentacle_id)
                elif method == MSG_PONG:
                    # 忽略 pong
                    pass
                elif method == MSG_PING:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "result": "pong", "id": msg_id}))
                elif self.on_custom is not None:
                    await self.on_custom(tentacle_id, msg, ws)
                else:
                    await self._send_error(ws, msg_id, -32601, f"Method not found: {method}")
        except Exception as e:
            logger.warning("ws connection error from %s: %s", remote, e)
        finally:
            if tentacle_id:
                self._connections.pop(tentacle_id, None)
                if self.on_device_disconnect:
                    try:
                        await self.on_device_disconnect(tentacle_id)
                    except Exception as e:
                        logger.warning("on_device_disconnect error: %s", e)
            logger.info("ws connection closed %s", remote)

    # ── 消息处理器 ──────────────────────────────────────────

    async def _handle_hello(self, ws: WebSocketConnection, msg: dict[str, Any]) -> str | None:
        """处理 device/hello —— 设备注册."""
        params = msg.get("params", {})
        hello = DeviceHello(params)

        # 保存连接
        self._connections[hello.tentacle_id] = ws

        # 回调注册
        if self.on_device_hello:
            try:
                await self.on_device_hello(hello, ws)
            except Exception as e:
                logger.warning("on_device_hello error: %s", e)

        # 回复确认
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {"registered": True, "server_time": now_ms()},
                    "id": msg.get("id"),
                }
            )
        )
        logger.info("device registered id=%s model=%s", hello.tentacle_id, hello.model)
        return hello.tentacle_id

    async def _handle_heartbeat(self, ws: WebSocketConnection, msg: dict[str, Any]) -> None:
        """处理 device/heartbeat."""
        params = msg.get("params", {})
        hb = Heartbeat(
            tentacle_id=params.get("tentacle_id", "unknown"),
            ts=params.get("ts", now_ms()),
            online=params.get("online", True),
            current_app=params.get("current_app"),
            screen_on=params.get("screen_on", True),
            battery=params.get("battery"),
            is_charging=params.get("is_charging", False),
            last_screen_tree_hash=params.get("last_screen_tree_hash"),
            running_tasks=params.get("running_tasks", 0),
            extra=params.get("extra", {}),
        )
        if self.on_heartbeat:
            try:
                await self.on_heartbeat(hb)
            except Exception as e:
                logger.warning("on_heartbeat error: %s", e)

    async def _handle_screen(self, ws: WebSocketConnection, msg: dict[str, Any]) -> None:
        """处理 device/screen —— 屏幕变化事件."""
        params = msg.get("params", {})
        # 透发给订阅者（由 TentaclePool 处理）
        logger.debug("screen change from %s: %s", params.get("tentacle_id"), params.get("hash"))

    async def _handle_binary_frame(
        self, ws: WebSocketConnection, data: bytes, tentacle_id: str | None
    ) -> None:
        """处理二进制帧 —— 设备推送 screen_frame.

        二进制帧格式见 screen_relay.py 中的协议定义。
        设备发送的二进制帧包含帧头（含 tentacle_id），无需依赖 JSON 消息中的 ID。

        Args:
            ws: WebSocket 连接
            data: 原始二进制数据
            tentacle_id: 当前连接的设备 ID（可能为 None，未注册时）
        """
        # 尝试解析帧头获取 tentacle_id
        try:
            from ..mobile.screen_relay import decode_frame_header

            parsed_tid, frame_type, flags, _ = decode_frame_header(data)
        except (struct.error, ValueError, IndexError) as e:
            logger.warning("invalid binary frame from %s: %s", _ws_remote_label(ws), e)
            return

        # 使用帧头中的 tentacle_id（优先）或连接中的 ID
        effective_tid = parsed_tid or tentacle_id
        if effective_tid is None:
            logger.warning("binary frame without tentacle_id from %s", _ws_remote_label(ws))
            return

        logger.debug(
            "screen frame from %s type=%s flags=0x%02x size=%d",
            effective_tid,
            frame_type,
            flags,
            len(data),
        )

        # 回调屏幕帧处理
        if self.on_screen_frame:
            try:
                await self.on_screen_frame(effective_tid, data)
            except Exception as e:
                logger.warning("on_screen_frame error: %s", e)

    async def _handle_task_execute(self, ws: WebSocketConnection, msg: dict[str, Any]) -> None:
        """处理 task/execute —— 手机请求母体决策任务.

        这是 Mobile → Runtime 的任务请求通路.

        注意：必须用 create_task 在后台执行，不能 await，
        否则会阻塞消息循环导致 tool/result 无法处理（死锁）。
        """
        params = msg.get("params", {})
        request = TaskExecuteRequest(params)

        if self.on_task_execute:

            async def _run_and_respond():
                try:
                    await self.on_task_execute(request, ws)
                except Exception as e:
                    logger.warning("on_task_execute error: %s", e)
                    with contextlib.suppress(Exception):  # immediate start heartbeat; failure OK
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "method": MSG_TASK_RESULT,
                                    "params": {
                                        "task_id": request.task_id,
                                        "success": False,
                                        "response": f"Server error: {e}",
                                        "steps": 0,
                                    },
                                    "id": request.task_id,
                                }
                            )
                        )

            asyncio.create_task(_run_and_respond())
        else:
            # 未配置回调，返回未实现
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": MSG_TASK_RESULT,
                        "params": {
                            "task_id": request.task_id,
                            "success": False,
                            "response": "Task execution not configured on server",
                            "steps": 0,
                        },
                        "id": request.task_id,
                    }
                )
            )

    async def _handle_tool_result(self, ws: WebSocketConnection, msg: dict[str, Any]) -> None:
        """处理 tool/result —— 工具执行结果."""
        params = msg.get("params", {})
        call_id = params.get("call_id", "")
        result = ToolResult(
            call_id=call_id,
            success=params.get("success", False),
            data=params.get("data"),
            error_code=params.get("error", {}).get("code") if params.get("error") else None,
            error_message=params.get("error", {}).get("message") if params.get("error") else None,
            duration_ms=params.get("duration_ms", 0),
            screenshot_after=params.get("screenshot_after"),
            screen_hash_after=params.get("screen_hash_after"),
        )

        # 唤醒等待的 future
        future = self._pending_calls.pop(call_id, None)
        if future and not future.done():
            future.set_result(result)

        # 回调
        if self.on_tool_result:
            try:
                await self.on_tool_result(result)
            except Exception as e:
                logger.warning("on_tool_result error: %s", e)

    async def _handle_remote_input(
        self, ws: WebSocketConnection, msg: dict[str, Any], tentacle_id: str | None
    ) -> None:
        """处理 remote/input —— 手机端远程控制PC.

        手机端发送触控/输入事件，服务端转发为PC鼠标/键盘操作。
        """
        params = msg.get("params", {})
        msg_id = msg.get("id")

        if self.on_remote_input is None:
            await self._send_error(ws, msg_id, -32020, "Remote input not configured")
            return

        try:
            result = await self.on_remote_input(tentacle_id or "unknown", params)
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": result,
                        "id": msg_id,
                    }
                )
            )
        except Exception as e:
            await self._send_error(ws, msg_id, -32021, f"Remote input error: {e}")

    async def _handle_pc_screen_subscribe(
        self, ws: WebSocketConnection, msg: dict[str, Any], tentacle_id: str | None
    ) -> None:
        """处理 pc_screen/subscribe —— 手机端订阅PC屏幕流."""
        msg_id = msg.get("id")
        # 注册手机端为 PC 屏幕流的订阅者
        # 通过 ScreenRelay 的 add_subscriber 实现
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {"subscribed": True, "source": "pc-host"},
                    "id": msg_id,
                }
            )
        )
        logger.info("PC screen subscribe from device=%s", tentacle_id)

    async def _handle_pc_screen_unsubscribe(
        self, ws: WebSocketConnection, msg: dict[str, Any], tentacle_id: str | None
    ) -> None:
        """处理 pc_screen/unsubscribe —— 手机端取消订阅PC屏幕流."""
        msg_id = msg.get("id")
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {"unsubscribed": True, "source": "pc-host"},
                    "id": msg_id,
                }
            )
        )
        logger.info("PC screen unsubscribe from device=%s", tentacle_id)

    # ── PC屏幕帧推送 ─────────────────────────────────────────

    async def push_pc_frame(self, frame_data: bytes) -> None:
        """向所有订阅了PC屏幕流的设备推送帧.

        Args:
            frame_data: 编码后的二进制帧（含帧头）
        """
        # 向所有已连接设备推送 PC 屏幕帧
        tasks = []
        for _tid, ws in self._connections.items():
            if _ws_is_open(ws):
                tasks.append(self._safe_send(ws, frame_data))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_send(self, ws: WebSocketConnection, data: bytes) -> None:
        """安全发送二进制数据."""
        with contextlib.suppress(Exception):  # best-effort; fail-open
            await ws.send(data)

    # ── 辅助 ────────────────────────────────────────────────

    async def _send_error(
        self,
        ws: WebSocketConnection,
        msg_id: Any,
        code: int,
        message: str,
    ) -> None:
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": code, "message": message},
                    "id": msg_id,
                }
            )
        )

    @property
    def connected_count(self) -> int:
        return len(self._connections)
