"""SSE Transport 辅助（从 mcp_server 拆分）.

包含：
- ``SseSession`` —— 单个 MCP 客户端的 SSE 会话
- ``SseSessionManager`` —— SSE 会话管理器
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .mcp_server import TentacleMcpServer

logger = logging.getLogger(__name__)


class SseSession:
    """SSE 会话 —— 管理 MCP 客户端的 SSE 连接.

    协议流程：
    1. 客户端连接 /api/tentacle/mcp/sse，获取 SSE 事件流
    2. 服务端发送 endpoint 事件，告知客户端消息发送 URL
    3. 客户端通过 /api/tentacle/mcp/message 发送 JSON-RPC 请求
    4. 服务端通过 SSE 推送 JSON-RPC 响应
    """

    def __init__(self, server: TentacleMcpServer, session_id: str) -> None:
        self.server = server
        self.session_id = session_id
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

    def create_session(self) -> SseSession:
        """创建新的 SSE 会话."""
        session_id = uuid.uuid4().hex[:16]
        session = SseSession(self.server, session_id)
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


__all__ = [
    "SseSession",
    "SseSessionManager",
]
