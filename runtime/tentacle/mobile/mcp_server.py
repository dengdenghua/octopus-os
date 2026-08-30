"""TentacleMcpServer —— 将手机工具暴露为 MCP tools.

兼容 MCP 协议规范，支持 Claude Desktop / Cursor / OpenClaw 等客户端。

两种传输模式：
1. Stdio transport — 本地命令行启动（`echo-agent mcp serve --stdio`）
2. SSE transport — HTTP SSE 远程连接（`api/tentacle/mcp/sse`）

用法（Claude Desktop 配置）::

    {
      "mcpServers": {
        "echo-tentacle": {
          "command": "echo-agent",
          "args": ["mcp", "serve", "--stdio"]
        }
      }
    }

或远程 SSE 模式（网关开启账号鉴权时需携带账号登录 Token）::

    {
      "mcpServers": {
        "echo-tentacle": {
          "url": "http://localhost:8766/api/tentacle/mcp/sse",
          "headers": {
            "Authorization": "Bearer <账号登录后获取的 Token>"
          }
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .vlm import VlmConfig

from ..base import ToolCall, ToolResult
from ..coordinator import TentacleCoordinator
from ._mcp_skill_tools import (
    _build_management_tools,
    _find_skills_roots,  # noqa: F401  # re-export
    _platform_prefix,  # noqa: F401  # re-export
    _screenshot_tool_for,  # noqa: F401  # re-export
    load_all_skill_tools,
    skill_to_mcp_tool,  # noqa: F401  # re-export
)
from .device import ANDROID_CAPABILITIES

logger = logging.getLogger(__name__)

# ── MCP Server 核心 ────────────────────────────────────────────


class TentacleMcpServer:
    """Tentacle MCP Server —— 将手机工具暴露为 MCP tools.

    兼容 MCP 协议规范（JSON-RPC 2.0），支持 Claude Desktop / Cursor / OpenClaw 等客户端。

    两种传输模式：
    1. Stdio transport — 本地命令行启动
    2. SSE transport — HTTP SSE 远程连接
    """

    def __init__(
        self,
        coordinator: TentacleCoordinator | None = None,
        skills_root: Path | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.skills_root = skills_root

        # 缓存的 tool 列表
        self._skill_tools: list[dict[str, Any]] | None = None
        self._management_tools: list[dict[str, Any]] | None = None

        # MCP 协议信息
        self._server_info = {
            "name": "echo-tentacle",
            "version": "0.1.0",
        }
        self._capabilities = {
            "tools": {},
            "resources": {},
        }

    # ── Tool 列表 ──────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """返回所有 MCP tool 定义（30 个手机工具 + 管理类工具）."""
        if self._skill_tools is None:
            self._skill_tools = load_all_skill_tools(self.skills_root)
        if self._management_tools is None:
            self._management_tools = _build_management_tools()
        return self._skill_tools + self._management_tools

    # ── Tool 调用 ──────────────────────────────────────────

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP tool.

        Args:
            name: MCP tool 名称（如 "android_tap"）
            args: 工具参数

        Returns:
            MCP tool 调用结果::

                {
                    "content": [{"type": "text", "text": "..."}],
                    "isError": false
                }
        """
        # 提取 _tentacle_id（不传给实际工具）。复制一份，避免修改调用方传入的 dict。
        tool_args = dict(args)
        tentacle_id = tool_args.pop("_tentacle_id", None)

        # 管理类工具
        if name in ("list_devices", "get_device_status", "take_screenshot", "analyze_screen"):
            return await self._call_management_tool(name, tool_args, tentacle_id)

        skill_name = self._skill_name_for_mcp_tool(name)
        if skill_name is None:
            return self._error_result(f"Unknown tool: {name}")

        # 获取目标设备
        device = self._resolve_device(tentacle_id)
        if device is None:
            return self._error_result(
                "No device available. "
                f"Requested: {tentacle_id or 'auto'}. "
                "Please connect a device or specify a valid _tentacle_id."
            )

        # 构建 ToolCall
        call_id = f"mcp-{uuid.uuid4().hex[:12]}"
        tool_call = ToolCall(
            call_id=call_id,
            tentacle_id=device.tentacle_id,
            tool=skill_name,
            args=tool_args,
        )

        # 执行
        try:
            result: ToolResult = await device.execute(tool_call)
        except Exception as e:
            logger.exception("tool call failed: %s", skill_name)
            return self._error_result(f"Tool execution error: {e}")

        # 转换为 MCP 结果
        if result.success:
            content_text = json.dumps(result.data, ensure_ascii=False, default=str)
            return {
                "content": [{"type": "text", "text": content_text}],
                "isError": False,
            }
        return self._error_result(
            f"Tool {skill_name} failed: {result.error_message or 'Unknown error'}"
        )

    async def _call_management_tool(
        self, name: str, args: dict[str, Any], tentacle_id: str | None
    ) -> dict[str, Any]:
        """调用管理类工具."""
        if self.coordinator is None:
            return self._error_result("Coordinator not available (standalone mode)")

        if name == "list_devices":
            online_only = args.get("online_only", False)
            if online_only:
                devices = self.coordinator.pool.all_online()
            else:
                devices = self.coordinator.pool.all()
            result = [
                {
                    "tentacle_id": d.tentacle_id,
                    "type": d.tentacle_type.value,
                    "platform": getattr(d, "platform", "unknown"),
                    "status": d.status.value,
                    "is_online": d.is_online,
                    "capabilities_count": len(d.capabilities),
                }
                for d in devices
            ]
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            }

        if name == "get_device_status":
            tid = args.get("tentacle_id", "")
            device = self.coordinator.pool.get(tid)
            if device is None:
                return self._error_result(f"Device not found: {tid}")
            result = {
                "tentacle_id": device.tentacle_id,
                "type": device.tentacle_type.value,
                "platform": getattr(device, "platform", "unknown"),
                "status": device.status.value,
                "is_online": device.is_online,
                "is_busy": device.is_busy,
                "capabilities": device.capabilities,
                "meta": getattr(device, "meta", {}),
            }
            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                ],
                "isError": False,
            }

        if name == "take_screenshot":
            device = self._resolve_device(tentacle_id)
            if device is None:
                return self._error_result("No device available for screenshot")
            call_id = f"mcp-ss-{uuid.uuid4().hex[:12]}"
            tool_call = ToolCall(
                call_id=call_id,
                tentacle_id=device.tentacle_id,
                tool="android.take_screenshot",
                args={},
            )
            result = await device.execute(tool_call)
            if not result.success:
                return self._error_result(f"Screenshot failed: {result.error_message}")

            # 提取 base64 截图数据
            screenshot_b64 = ""
            if isinstance(result.data, dict):
                screenshot_b64 = result.data.get("screenshot", "")
            elif isinstance(result.data, str):
                screenshot_b64 = result.data

            if not screenshot_b64:
                return self._error_result("Screenshot data is empty")

            return {
                "content": [
                    {
                        "type": "image",
                        "data": screenshot_b64,
                        "mimeType": "image/jpeg",
                    }
                ],
                "isError": False,
            }

        if name == "analyze_screen":
            task = args.get("task", "")
            if not task:
                return self._error_result("Missing 'task' parameter for screen analysis")
            device = self._resolve_device(tentacle_id)
            if device is None:
                return self._error_result("No device available for screen analysis")

            try:
                from runtime.tentacle.mobile.vlm import (
                    VlmClient,  # noqa: F401 — VlmConfig used conditionally
                )

                # 尝试获取 VLM 客户端
                vlm_client: VlmClient | None = getattr(self.coordinator, "_vlm_client", None)
                if vlm_client is None:
                    vlm_config = self._auto_detect_vlm_config()
                    if vlm_config is None:
                        return self._error_result(
                            "VLM not configured. Set VLM_API_KEY env var or use coordinator.with_vlm()."
                        )
                    vlm_client = VlmClient(vlm_config)
                    self.coordinator._vlm_client = vlm_client

                # 截图
                ss_call = ToolCall(
                    call_id=f"mcp-vlm-ss-{uuid.uuid4().hex[:12]}",
                    tentacle_id=device.tentacle_id,
                    tool="android.take_screenshot",
                    args={},
                )
                ss_result = await device.execute(ss_call)
                if not ss_result.success:
                    return self._error_result(f"Screenshot failed: {ss_result.error_message}")

                screenshot_b64 = ""
                if isinstance(ss_result.data, dict):
                    screenshot_b64 = ss_result.data.get("screenshot", "")
                elif isinstance(ss_result.data, str):
                    screenshot_b64 = ss_result.data

                if not screenshot_b64:
                    return self._error_result("Screenshot data is empty")

                # VLM 分析
                analysis = vlm_client.analyze_screenshot(
                    screenshot_base64=screenshot_b64,
                    task=task,
                )
                result_data = analysis.to_dict()
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result_data, ensure_ascii=False, default=str),
                        }
                    ],
                    "isError": False,
                }

            except ImportError:
                return self._error_result("VLM module not available")
            except Exception as e:
                logger.exception("VLM analysis failed")
                return self._error_result(f"Screen analysis failed: {e}")

        return self._error_result(f"Unknown management tool: {name}")

    # ── Resources ──────────────────────────────────────────

    def list_resources(self) -> list[dict[str, Any]]:
        """返回 MCP resources 列表（设备列表）."""
        resources: list[dict[str, Any]] = []

        if self.coordinator is not None:
            for device in self.coordinator.pool.all():
                resources.append(
                    {
                        "uri": f"tentacle://device/{device.tentacle_id}",
                        "name": f"Device {device.tentacle_id}",
                        "description": f"{device.tentacle_type.value} device ({device.status.value})",
                        "mimeType": "application/json",
                    }
                )

        return resources

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """读取 MCP resource.

        支持的 URI：
        - tentacle://device/{id} — 设备状态
        - tentacle://device/{id}/screenshot — 设备截图
        """
        if not uri.startswith("tentacle://device/"):
            return {"error": {"code": -32602, "message": f"Unknown resource URI: {uri}"}}

        parts = uri[len("tentacle://device/") :].split("/")
        tentacle_id = parts[0]

        if self.coordinator is None:
            return {"error": {"code": -32000, "message": "Coordinator not available"}}

        device = self.coordinator.pool.get(tentacle_id)
        if device is None:
            return {"error": {"code": -32001, "message": f"Device not found: {tentacle_id}"}}

        # 截图资源
        if len(parts) > 1 and parts[1] == "screenshot":
            call_id = f"mcp-res-ss-{uuid.uuid4().hex[:12]}"
            tool_call = ToolCall(
                call_id=call_id,
                tentacle_id=device.tentacle_id,
                tool="android.take_screenshot",
                args={},
            )
            result = await device.execute(tool_call)
            if result.success:
                screenshot_b64 = ""
                if isinstance(result.data, dict):
                    screenshot_b64 = result.data.get("screenshot", "")
                elif isinstance(result.data, str):
                    screenshot_b64 = result.data
                return {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "image/jpeg",
                            "blob": screenshot_b64,
                        }
                    ]
                }
            return {
                "error": {"code": -32002, "message": f"Screenshot failed: {result.error_message}"}
            }

        # 设备状态
        status = {
            "tentacle_id": device.tentacle_id,
            "type": device.tentacle_type.value,
            "platform": getattr(device, "platform", "unknown"),
            "status": device.status.value,
            "is_online": device.is_online,
            "capabilities": device.capabilities,
            "meta": getattr(device, "meta", {}),
        }
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(status, ensure_ascii=False, default=str),
                }
            ]
        }

    # ── JSON-RPC 消息处理 ──────────────────────────────────

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """处理 MCP JSON-RPC 请求.

        支持的方法：
        - initialize — 初始化握手
        - initialized — 初始化确认（无响应）
        - tools/list — 列出工具
        - tools/call — 调用工具
        - resources/list — 列出资源
        - resources/read — 读取资源
        - ping — 心跳
        """
        method = request.get("method", "")
        params = request.get("params", {}) or {}
        req_id = request.get("id")

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "initialized":
                # 通知，无需响应
                return {}  # 空响应表示通知
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {}) or {}
                result = await self.call_tool(tool_name, tool_args)
            elif method == "resources/list":
                result = {"resources": self.list_resources()}
            elif method == "resources/read":
                uri = params.get("uri", "")
                result = await self.read_resource(uri)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }

            # 通知（无 id）不返回响应
            if req_id is None:
                return {}

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }

        except Exception as e:
            logger.exception("MCP request handling failed: %s", method)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {e}",
                },
            }

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """处理 initialize 请求."""
        client_info = params.get("clientInfo", {})
        logger.info(
            "MCP client connected: %s v%s",
            client_info.get("name", "unknown"),
            client_info.get("version", "?"),
        )

        return {
            "protocolVersion": "2024-11-05",
            "capabilities": self._capabilities,
            "serverInfo": self._server_info,
        }

    # ── 内部辅助 ──────────────────────────────────────────

    def _resolve_device(self, tentacle_id: str | None):
        """解析目标设备.

        策略：
        1. 指定 tentacle_id → 查找该设备
        2. 未指定 → 自动选择第一个在线设备
        """
        if self.coordinator is None:
            return None

        if tentacle_id:
            device = self.coordinator.pool.get(tentacle_id)
            if device is not None and device.is_online:
                return device
            return None

        # 自动选择
        online_devices = self.coordinator.pool.all_online()
        if online_devices:
            return online_devices[0]
        return None

    def _skill_name_for_mcp_tool(self, mcp_name: str) -> str | None:
        """Resolve an MCP-safe tool name back to the canonical Android skill id.

        ``skill_to_mcp_tool`` stores the original skill name in ``_meta``. Use
        that as the source of truth instead of guessing with underscore
        replacement, because names like ``android.browser.install_extension``
        intentionally contain both dots and underscores.
        """
        if mcp_name in ANDROID_CAPABILITIES:
            return mcp_name

        for tool in self.list_tools():
            if tool.get("name") != mcp_name:
                continue
            skill_name = (tool.get("_meta") or {}).get("skill_name")
            if isinstance(skill_name, str) and skill_name in ANDROID_CAPABILITIES:
                return skill_name
            return None

        # Backward-compatible fallback for callers that pass legacy names not
        # present in the loaded SKILL.md list.
        candidates = [mcp_name.replace("_", ".", 1), mcp_name.replace("_", ".")]
        for candidate in candidates:
            if candidate in ANDROID_CAPABILITIES:
                return candidate
        return None

    @staticmethod
    def _error_result(message: str) -> dict[str, Any]:
        """构建 MCP 错误结果."""
        return {
            "content": [{"type": "text", "text": f"Error: {message}"}],
            "isError": True,
        }

    @staticmethod
    def _auto_detect_vlm_config() -> VlmConfig | None:
        """从环境变量自动检测 VLM 配置."""
        import os

        from runtime.tentacle.mobile.vlm import VlmConfig

        vlm_key = os.environ.get("VLM_API_KEY", "").strip()
        if vlm_key:
            return VlmConfig(
                base_url=os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1"),
                api_key=vlm_key,
                model=os.environ.get("VLM_MODEL", "gpt-4o"),
            )

        qwen_key = os.environ.get("QWEN_API_KEY", "").strip()
        if qwen_key:
            return VlmConfig.qwen_vl(qwen_key)

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            return VlmConfig.openai_vl(openai_key)

        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if deepseek_key:
            return VlmConfig.deepseek_vl(deepseek_key)

        glm_key = os.environ.get("GLM_API_KEY", "").strip()
        if glm_key:
            return VlmConfig.glm_vl(glm_key)

        return None


# ── Stdio Transport ────────────────────────────────────────────


async def serve_stdio(coordinator: TentacleCoordinator | None = None) -> None:
    """启动 stdio 模式的 MCP server.

    通过 stdin/stdout 通信，兼容 Claude Desktop 的 command 启动方式。

    Args:
        coordinator: 已启动的 TentacleCoordinator（可选，None 时使用独立模式）
    """
    server = TentacleMcpServer(coordinator=coordinator)

    logger.info("MCP server starting in stdio mode")

    # 读取 stdin 的 JSON-RPC 消息，写入 stdout 的响应
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(
        writer_transport, writer_protocol, reader, asyncio.get_event_loop()
    )

    try:
        while True:
            # 读取一行 JSON-RPC 消息
            line = await reader.readline()
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            # 解析 JSON
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("invalid JSON-RPC message: %s", e)
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"},
                }
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                continue

            # 处理请求
            response = await server.handle_request(request)

            # 通知（空响应）不写回
            if not response:
                continue

            # 写回响应
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            await writer.drain()

    except Exception as e:
        logger.error("stdio transport error: %s", e)
    finally:
        logger.info("MCP stdio server shutting down")


# ── SSE Transport 辅助 ─────────────────────────────────────────


class SseSession:
    """SSE 会话 —— 管理 MCP 客户端的 SSE 连接.

    协议流程：
    1. 客户端连接 /api/tentacle/mcp/sse，获取 SSE 事件流
    2. 服务端发送 endpoint 事件，告知客户端消息发送 URL
    3. 客户端通过 /api/tentacle/mcp/message 发送 JSON-RPC 请求
    4. 服务端通过 SSE 推送 JSON-RPC 响应

    Args:
        actor_id: 创建该会话的账号（账号登录鉴权后由网关绑定）。
            ``None`` 表示匿名/未开启账号鉴权。绑定后 ``/mcp/message``
            只接受同一账号携带相同凭证的调用。
    """

    def __init__(
        self,
        server: TentacleMcpServer,
        session_id: str,
        *,
        actor_id: str | None = None,
    ) -> None:
        self.server = server
        self.session_id = session_id
        self.actor_id = actor_id
        self._event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def send_event(self, data: str) -> None:
        """向 SSE 客户端推送事件."""
        await self._event_queue.put(data)

    async def close(self) -> None:
        """关闭 SSE 会话."""
        await self._event_queue.put(None)

    async def event_stream(self):
        """生成 SSE 事件流（用于 FastAPI StreamingResponse）."""
        try:
            while True:
                data = await self._event_queue.get()
                if data is None:
                    break
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:  # expected · client disconnected, SSE stream torn down
            pass

    async def handle_message(self, request: dict[str, Any]) -> None:
        """处理客户端通过 /message 发送的 JSON-RPC 请求."""
        response = await self.server.handle_request(request)
        # 通知（空响应）不推送
        if not response:
            return
        await self.send_event(json.dumps(response, ensure_ascii=False))


class SseSessionManager:
    """SSE 会话管理器."""

    def __init__(self, server: TentacleMcpServer) -> None:
        self.server = server
        self._sessions: dict[str, SseSession] = {}

    def create_session(self, *, actor_id: str | None = None) -> SseSession:
        """创建新的 SSE 会话，可选绑定创建该会话的账号."""
        session_id = uuid.uuid4().hex[:16]
        session = SseSession(self.server, session_id, actor_id=actor_id)
        self._sessions[session_id] = session
        logger.info("MCP SSE session created: %s", session_id)
        return session

    def get_session(self, session_id: str) -> SseSession | None:
        """获取指定 SSE 会话."""
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """移除 SSE 会话."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            logger.info("MCP SSE session removed: %s", session_id)
