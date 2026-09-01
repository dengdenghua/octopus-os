"""Stdio and SSE transports for the Tentacle MCP server."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from runtime.tentacle.coordinator import TentacleCoordinator

logger = logging.getLogger(__name__)


async def serve_stdio(coordinator: TentacleCoordinator | None = None) -> None:
    """Serve newline-delimited MCP JSON-RPC over stdin/stdout."""

    from runtime.tentacle.mobile.mcp_server import TentacleMcpServer

    server = TentacleMcpServer(coordinator=coordinator)
    logger.info("MCP server starting in stdio mode")
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
            line = (await reader.readline()).strip()
            if not line:
                if reader.at_eof():
                    break
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("invalid JSON-RPC message: %s", exc)
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                continue

            response = await server.handle_request(request)
            if response:
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
                await writer.drain()
    except Exception as exc:  # noqa: BLE001
        logger.error("stdio transport error: %s", exc)
    finally:
        logger.info("MCP stdio server shutting down")


class SseSession:
    """One MCP client's server-sent-event response channel."""

    def __init__(self, server: Any, session_id: str) -> None:
        self.server = server
        self.session_id = session_id
        self._event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def send_event(self, data: str) -> None:
        await self._event_queue.put(data)

    async def close(self) -> None:
        await self._event_queue.put(None)

    async def event_stream(self):
        try:
            while True:
                data = await self._event_queue.get()
                if data is None:
                    break
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            return

    async def handle_message(self, request: dict[str, Any]) -> None:
        response = await self.server.handle_request(request)
        if response:
            await self.send_event(json.dumps(response, ensure_ascii=False))


class SseSessionManager:
    """Own active MCP SSE sessions."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self._sessions: dict[str, SseSession] = {}

    def create_session(self) -> SseSession:
        session_id = uuid.uuid4().hex[:16]
        session = SseSession(self.server, session_id)
        self._sessions[session_id] = session
        logger.info("MCP SSE session created: %s", session_id)
        return session

    def get_session(self, session_id: str) -> SseSession | None:
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            logger.info("MCP SSE session removed: %s", session_id)


__all__ = ["SseSession", "SseSessionManager", "serve_stdio"]
