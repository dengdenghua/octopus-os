from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.safety.approval.cancellation import (
    CancellationToken,
    OperationCancelled,
    current_cancellation_token,
)

from .types import MCPServerConfig

try:
    import mcp  # type: ignore[import-untyped]

    STDIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    STDIO_AVAILABLE = False
    mcp = None  # type: ignore[assignment]

# The same ``mcp`` SDK provides the remote (streamable-http / SSE) clients,
# so HTTP availability tracks stdio availability.
HTTP_AVAILABLE = STDIO_AVAILABLE


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class MCPTool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
    server_name: str = ""


class MCPInvocationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    raw_content: list[Any] = Field(default_factory=list)  # Implementation note.
    status: str = "completed"
    cancelled: bool = False
    cancellation_reason: str | None = None


class MCPClientError(RuntimeError):
    pass


# ═══════════════════════════════════════════════════════════
# ABC
# ═══════════════════════════════════════════════════════════


class MCPClient(ABC):
    @abstractmethod
    def list_tools(self) -> list[MCPTool]: ...

    @abstractmethod
    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _connector_injection_for_server(server_name: str) -> dict[str, dict[str, str]]:
    """Resolve connector auth injection (认证编排层) for an MCP server name.

    Only connectors that are installed + enabled + connected contribute
    headers/env. The connectors platform layer is imported lazily so the
    MCP adapters never hard-depend on it; any failure resolves to empty
    injection rather than blocking MCP traffic.
    """
    try:
        from runtime.platform.connectors.auth_orchestrator import (
            mcp_injection_for_server,
        )

        return mcp_injection_for_server(server_name)
    except Exception:  # noqa: BLE001 - orchestration layer must not block MCP
        return {"headers": {}, "env": {}}


def _connector_headers_for(server_name: str) -> dict[str, str]:
    return _connector_injection_for_server(server_name).get("headers", {})


def _connector_env_for(server_name: str) -> dict[str, str]:
    return _connector_injection_for_server(server_name).get("env", {})


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class MockMCPClient(MCPClient):
    def __init__(
        self,
        *,
        server_name: str = "mock-server",
        tools: list[MCPTool] | None = None,
        tool_handlers: dict[str, Any] | None = None,
    ) -> None:
        self.server_name = server_name
        self._tools = list(tools or [])
        self._handlers: dict[str, Any] = dict(tool_handlers or {})
        self._closed = False
        self.call_log: list[tuple[str, dict]] = []

    def list_tools(self) -> list[MCPTool]:
        self._check_open()
        return [t.model_copy(update={"server_name": self.server_name}) for t in self._tools]

    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult:
        self._check_open()
        t0 = time.monotonic()
        token = cancellation or current_cancellation_token()
        if token.is_cancelled:
            return _cancelled_result(name, token, t0)
        self.call_log.append((name, dict(args)))
        with trace_stage("mcp.call_tool") as span:
            span.set_attribute("echo.mcp.server", self.server_name)
            span.set_attribute("echo.mcp.tool", name)

            handler = self._handlers.get(name)
            if handler is None:
                if token.is_cancelled:
                    return _cancelled_result(name, token, t0)
                return MCPInvocationResult(
                    tool_name=name,
                    success=False,
                    error=f"unknown_tool: {name!r}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            try:
                output = handler(args) if callable(handler) else handler
                if token.is_cancelled:
                    return _cancelled_result(name, token, t0)
                return MCPInvocationResult(
                    tool_name=name,
                    success=True,
                    output=output,
                    latency_ms=(time.monotonic() - t0) * 1000,
                    raw_content=[{"type": "text", "text": str(output)[:400]}],
                )
            except Exception as e:  # noqa: BLE001
                return MCPInvocationResult(
                    tool_name=name,
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )

    def close(self) -> None:
        self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise MCPClientError("client closed")


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class StdioMCPClient(MCPClient):
    def __init__(self, config: MCPServerConfig) -> None:
        if not STDIO_AVAILABLE:
            raise MCPClientError("mcp SDK not installed · `pip install mcp` or use MockMCPClient")
        self.config = config
        self._closed = False
        self._tools_cache: list[MCPTool] | None = None

    def list_tools(self) -> list[MCPTool]:
        if self._closed:
            raise MCPClientError("client closed")
        if self._tools_cache is None:
            self._tools_cache = asyncio.run(self._list_tools_async())
        return list(self._tools_cache)

    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult:
        if self._closed:
            raise MCPClientError("client closed")
        t0 = time.monotonic()
        token = cancellation or current_cancellation_token()
        if token.is_cancelled:
            return _cancelled_result(name, token, t0)
        with trace_stage("mcp.stdio.call_tool") as span:
            span.set_attribute("echo.mcp.server", self.config.name)
            span.set_attribute("echo.mcp.tool", name)
            try:
                result = asyncio.run(
                    _await_with_cancellation(self._call_tool_async(name, args), token)
                )
            except OperationCancelled:
                return _cancelled_result(name, token, t0)
            except Exception as e:  # noqa: BLE001
                return MCPInvocationResult(
                    tool_name=name,
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            return result.model_copy(update={"latency_ms": (time.monotonic() - t0) * 1000})

    def _stdio_env(self) -> dict[str, str] | None:
        """config env + connector env(认证编排层,仅已安装+启用+连接)。"""
        env = dict(self.config.env) if self.config.env else {}
        for key, value in _connector_env_for(self.config.name).items():
            env.setdefault(key, value)
        return env or None

    def close(self) -> None:
        self._closed = True

    # ─── async implementations ─────────────────────

    async def _list_tools_async(self) -> list[MCPTool]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=self._stdio_env(),
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return [
                MCPTool(
                    name=t.name,
                    description=(t.description or ""),
                    input_schema=(t.inputSchema or {}),
                    server_name=self.config.name,
                )
                for t in result.tools
            ]

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> MCPInvocationResult:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=self._stdio_env(),
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, args)
            text = "\n".join(
                getattr(b, "text", "")
                for b in result.content
                if hasattr(b, "text") and getattr(b, "text", None)
            )
            raw = [b.model_dump() if hasattr(b, "model_dump") else str(b) for b in result.content]
            is_err = bool(getattr(result, "isError", False))
            return MCPInvocationResult(
                tool_name=name,
                success=not is_err,
                output=text if text else None,
                error=text if is_err else None,
                raw_content=raw,
            )


# ═══════════════════════════════════════════════════════════
# Remote (streamable-http / SSE) transport
# ═══════════════════════════════════════════════════════════


class HttpMCPClient(MCPClient):
    """MCP client over a remote HTTP transport (streamable-http or SSE).

    Mirrors ``StdioMCPClient`` but connects to a ``url`` instead of spawning
    a subprocess — this is how most of the hosted MCP ecosystem (GitHub,
    Slack, Linear, Notion, ...) is reached. The transport is picked from
    ``config.transport``: ``"http"`` (default) → streamable-http,
    ``"sse"`` → the legacy SSE transport.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        if not HTTP_AVAILABLE:
            raise MCPClientError("mcp SDK not installed · `pip install mcp` or use MockMCPClient")
        if not config.url:
            raise MCPClientError("HttpMCPClient requires config.url")
        self.config = config
        self._closed = False
        self._tools_cache: list[MCPTool] | None = None

    def list_tools(self) -> list[MCPTool]:
        if self._closed:
            raise MCPClientError("client closed")
        if self._tools_cache is None:
            self._tools_cache = asyncio.run(self._list_tools_async())
        return list(self._tools_cache)

    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult:
        if self._closed:
            raise MCPClientError("client closed")
        t0 = time.monotonic()
        token = cancellation or current_cancellation_token()
        if token.is_cancelled:
            return _cancelled_result(name, token, t0)
        with trace_stage("mcp.http.call_tool") as span:
            span.set_attribute("echo.mcp.server", self.config.name)
            span.set_attribute("echo.mcp.tool", name)
            try:
                result = asyncio.run(
                    _await_with_cancellation(self._call_tool_async(name, args), token)
                )
            except OperationCancelled:
                return _cancelled_result(name, token, t0)
            except Exception as e:  # noqa: BLE001
                return MCPInvocationResult(
                    tool_name=name,
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            return result.model_copy(update={"latency_ms": (time.monotonic() - t0) * 1000})

    def close(self) -> None:
        self._closed = True

    # ─── async implementations ─────────────────────

    def _transport(self) -> Any:
        """Return the transport async-context-manager for this config.

        ``streamable-http`` yields a 3-tuple ``(read, write, get_id)`` and
        ``sse`` a 2-tuple ``(read, write)``; callers index ``[0]``/``[1]``
        so both shapes work uniformly.
        """
        header_values = dict(self.config.headers) if self.config.headers else {}
        # Connector 认证编排层(WorkBuddy / echo): only connectors that
        # are installed + enabled + connected contribute headers. A header
        # the user pasted into the server config always wins (manual beats
        # connector auth, which beats the generic OAuth fallback below).
        for key, value in _connector_headers_for(self.config.name).items():
            header_values.setdefault(key, value)
        # OAuth-authorized servers (see mcp_router.py's /api/mcp/oauth/*
        # endpoints) get their bearer token injected here rather than
        # baked into ``config.headers`` — tokens refresh/expire and are
        # looked up fresh on every connect. An explicit Authorization
        # header the user pasted into the server config always wins
        # (manual token beats auto-OAuth).
        if "Authorization" not in header_values and "authorization" not in header_values:
            from .oauth import bearer_for_server

            token = bearer_for_server(self.config.name, self.config.tenant_id)
            if token:
                header_values["Authorization"] = f"Bearer {token}"
        headers: dict[str, str] | None = header_values or None
        timeout = max(1.0, self.config.timeout_ms / 1000.0)
        if self.config.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(self.config.url, headers=headers, timeout=timeout)
        return self._streamable_http_client(headers=headers, timeout=timeout)

    def _streamable_http_client(self, *, headers: dict[str, str] | None, timeout: float):
        """Streamable HTTP transport, compatible with both MCP SDK 1.x and 2.x.

        MCP 2.0 renamed ``streamablehttp_client`` → ``streamable_http_client``
        and moved header/timeout configuration onto an injected
        ``httpx2.AsyncClient`` (``httpx2`` replaces ``httpx`` in the 2.x
        client stack). 1.x kept ``headers=``/``timeout=`` kwargs. Both yield
        ``(read_stream, write_stream)`` so callers unpack identically.
        """
        try:
            from mcp.client.streamable_http import (  # MCP >= 2.0
                streamable_http_client as _new_client,
            )
        except ImportError:  # pragma: no cover — 1.x fallback
            from mcp.client.streamable_http import (  # MCP < 2.0
                streamablehttp_client as _new_client,
            )

            return _new_client(self.config.url, headers=headers, timeout=timeout)

        # 2.x: inject headers/timeout via an httpx2.AsyncClient.
        try:
            import httpx2

            client = httpx2.AsyncClient(headers=headers, timeout=timeout)
        except ImportError:  # pragma: no cover — httpx2 missing, degrade to default
            client = None
        if client is None:
            return _new_client(self.config.url)
        return _new_client(self.config.url, http_client=client)

    async def _list_tools_async(self) -> list[MCPTool]:
        from mcp import ClientSession

        async with self._transport() as conn:
            read, write = conn[0], conn[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    MCPTool(
                        name=t.name,
                        description=(t.description or ""),
                        input_schema=(t.inputSchema or {}),
                        server_name=self.config.name,
                    )
                    for t in result.tools
                ]

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> MCPInvocationResult:
        from mcp import ClientSession

        async with self._transport() as conn:
            read, write = conn[0], conn[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
                text = "\n".join(
                    getattr(b, "text", "")
                    for b in result.content
                    if hasattr(b, "text") and getattr(b, "text", None)
                )
                raw = [
                    b.model_dump() if hasattr(b, "model_dump") else str(b) for b in result.content
                ]
                is_err = bool(getattr(result, "isError", False))
                return MCPInvocationResult(
                    tool_name=name,
                    success=not is_err,
                    output=text if text else None,
                    error=text if is_err else None,
                    raw_content=raw,
                )


async def _await_with_cancellation(
    coro: Any,
    token: CancellationToken,
    *,
    cancel_notification: Callable[[str], Awaitable[None]] | None = None,
) -> Any:
    """Await an MCP request while forwarding the ambient turn cancellation.

    Persistent sessions may provide ``cancel_notification`` to send MCP's
    protocol-level ``notifications/cancelled`` message before the local await
    is torn down. Regardless of peer behaviour, the task is fenced so a late
    response cannot become the tool result of a redirected turn.
    """

    token.throw_if_cancelled()
    loop = asyncio.get_running_loop()
    task = loop.create_task(coro)

    notification_tasks: set[asyncio.Future[None]] = set()

    def _cancel_task(reason: str) -> None:
        def _cancel_on_loop() -> None:
            if task.done():
                return
            if cancel_notification is not None:
                notice: asyncio.Future[None] = asyncio.ensure_future(
                    cancel_notification(reason),
                    loop=loop,
                )
                notification_tasks.add(notice)
                notice.add_done_callback(notification_tasks.discard)
            task.cancel()

        loop.call_soon_threadsafe(_cancel_on_loop)

    unsubscribe = token.on_cancelled(_cancel_task)
    try:
        return await task
    except asyncio.CancelledError as exc:
        if token.is_cancelled:
            raise OperationCancelled(token.reason or "MCP call cancelled") from exc
        raise
    finally:
        unsubscribe()


def _cancelled_result(
    name: str,
    token: CancellationToken,
    started_at: float,
) -> MCPInvocationResult:
    reason = token.reason or "operation cancelled"
    return MCPInvocationResult(
        tool_name=name,
        success=False,
        error=f"cancelled: {reason}",
        latency_ms=(time.monotonic() - started_at) * 1000,
        status="cancelled",
        cancelled=True,
        cancellation_reason=reason,
    )
