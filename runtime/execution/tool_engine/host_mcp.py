"""Ephemeral, authenticated MCP transport for one Echo turn's host tools.

This server owns no registry, credentials, permissions or execution loop. It
only projects HostToolBroker through the MCP protocol used by OpenCode.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import socket
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any

import uvicorn
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from runtime.execution.host_mcp_connection import HostMCPConnection as HostMCPConnection
from runtime.execution.request import (
    ExecutionDeadlineExceeded,
    ExecutionRequest,
    execution_request_scope,
)
from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth.scope import TenantScope

from .host_tool_broker import HostToolBroker


class _EmbeddedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The parent gateway owns process signals; this is only a turn task.
        yield


class HostMCPBridge:
    def __init__(self, broker: HostToolBroker, request: ExecutionRequest, session: Session) -> None:
        task = request.task
        if (
            task.thread_id != session.thread_id
            or task.task_id != session.turn_id
            or task.actor_id != session.actor
            or task.tenant_id != session.metadata.get("tenant_id")
        ):
            raise ValueError("MCP host session does not match the authenticated task")
        bound = broker._execution_request
        if (
            bound is None
            or session.execution_request is not request
            or bound.task != replace(task, execution_engine=bound.task.execution_engine)
            or bound.task.permissions is not task.permissions
            or bound.task.resources is not task.resources
            or broker._outer_thread_id != task.thread_id
            or broker._outer_turn_id != task.task_id
            or broker._principal_id != (task.actor_id or "local")
            or broker._tenant_id != (task.tenant_id or "local")
            or broker._inner_thread_id is not None
        ):
            raise ValueError("MCP broker does not match the authenticated task authority")
        self.broker = broker
        self.request = bound
        self.session = replace(session, execution_request=bound, metadata=dict(session.metadata))
        self.scope = (
            TenantScope(tenant_id=task.tenant_id, actor_id=task.actor_id)
            if task.tenant_id and task.actor_id
            else None
        )
        broker.bind_inner_scope(thread_id=task.thread_id, turn_id=task.task_id)
        self.token = secrets.token_urlsafe(32)
        self.closed = False
        self._served = False
        self.pending: set[asyncio.Task[dict[str, Any]]] = set()
        self.server = Server(
            "Echo host tools",
            version="1.0.0",
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )
        self.manager = StreamableHTTPSessionManager(
            self.server,
            stateless=True,
            json_response=True,
            max_request_body_size=262_144,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
            ),
        )

    async def list_tools(
        self, _ctx: ServerRequestContext, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        try:
            self.request.task.resources.remaining_seconds()
        except ExecutionDeadlineExceeded:
            return types.ListToolsResult(tools=[])
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=spec["name"],
                    description=spec["description"],
                    inputSchema=spec["inputSchema"],
                )
                for spec in self.broker.catalog.specs
            ]
            if not self.closed and not self.broker._interrupted()
            else []
        )

    async def call_tool(
        self, ctx: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        if self.closed or self.broker._interrupted():
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text="Echo turn stopped")]
            )
        # HTTP handlers must not rely on ambient ContextVars inherited from
        # uvicorn. All coordinates and grants are captured from the host turn.
        with (
            execution_request_scope(self.request),
            session_scope(self.session),
            use_capability_scope(self.scope),
        ):
            try:
                self.request.task.resources.remaining_seconds()
            except ExecutionDeadlineExceeded:
                return types.CallToolResult(
                    isError=True,
                    content=[types.TextContent(type="text", text="Echo task deadline exceeded")],
                )
            coordinate = json.dumps(
                [type(ctx.request_id).__name__, ctx.request_id], ensure_ascii=True
            )
            task = asyncio.create_task(
                self.broker.invoke(
                    params.name,
                    params.arguments or {},
                    call_id="mcp:" + hashlib.sha256(coordinate.encode()).hexdigest(),
                )
            )
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        # Dropping an HTTP connection cannot abandon an already executing
        # native write. The turn's close path drains these executor calls.
        result = await asyncio.shield(task)
        return types.CallToolResult(
            isError=not result["success"],
            content=[
                types.TextContent(type="text", text=item["text"])
                for item in result["contentItems"]
                if item["type"] == "inputText"
            ],
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"")
        expected = f"Bearer {self.token}".encode("ascii")
        if self.closed or not secrets.compare_digest(auth, expected):
            status = 401
        elif scope.get("path") not in ("/mcp", "/mcp/"):
            status = 404
        else:
            await self.manager.handle_request(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    @contextlib.asynccontextmanager
    async def serve(self) -> AsyncIterator[HostMCPConnection]:
        if self._served or self.closed or self.broker._interrupted():
            raise RuntimeError("Echo tool bridge is closed or already served")
        self._served = True
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.setblocking(False)
            port = listener.getsockname()[1]
            config = uvicorn.Config(
                self,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_config=None,
                proxy_headers=False,
                server_header=False,
                ws="none",
                timeout_graceful_shutdown=5,
            )
            server = _EmbeddedServer(config)

            async def run() -> None:
                # Keep MCP's AnyIO task group outside the caller's context so
                # model errors retain their type instead of ExceptionGroup.
                async with self.manager.run():
                    await server.serve(sockets=[listener])

            running = asyncio.create_task(run())
            try:
                for _ in range(100):
                    if running.done():
                        await running
                        raise RuntimeError("Echo tool bridge failed to start")
                    if server.started:
                        break
                    await asyncio.sleep(0.02)
                else:
                    raise RuntimeError("Echo tool bridge startup timed out")
                yield HostMCPConnection(f"http://127.0.0.1:{port}/mcp", self.token)
            finally:
                self.closed = True
                self.broker.close()
                # Stop accepting new calls before draining operations that
                # already crossed the native executor's approval boundary.
                server.should_exit = True
                try:
                    if self.pending:
                        await asyncio.shield(
                            asyncio.gather(*tuple(self.pending), return_exceptions=True)
                        )
                    await self.broker.aclose()
                finally:
                    await running


__all__ = ["HostMCPBridge", "HostMCPConnection"]
