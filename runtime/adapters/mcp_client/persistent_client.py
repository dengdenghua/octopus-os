from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
import time
import weakref
from contextlib import AsyncExitStack
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.safety.approval.cancellation import (
    CancellationToken,
    OperationCancelled,
    current_cancellation_token,
)

from .client import (
    STDIO_AVAILABLE,
    MCPClient,
    MCPClientError,
    MCPInvocationResult,
    MCPTool,
    _cancelled_result,
)
from .types import MCPServerConfig

_LOG = logging.getLogger(__name__)


_LIVE_CLIENTS: weakref.WeakSet[PersistentStdioMCPClient] = weakref.WeakSet()
_LIVE_LOCK = threading.Lock()


def close_all_persistent_clients() -> None:
    with _LIVE_LOCK:
        clients = list(_LIVE_CLIENTS)
    for c in clients:
        with contextlib.suppress(Exception):
            c.close()


_RECONNECT_ERR_MARKERS = (
    "closedresourceerror",
    "brokenresourceerror",
    "brokenpipe",
    "endofstream",
    "streamclose",
    "connectionclosed",
    "connectionreset",
)


def _should_reconnect(exc: BaseException) -> bool:
    cls = type(exc).__name__.lower()
    msg = f"{cls}: {exc}".lower()
    if isinstance(exc, (BrokenPipeError, ConnectionError, EOFError)):
        return True
    return any(m in msg for m in _RECONNECT_ERR_MARKERS)


class PersistentStdioMCPClient(MCPClient):
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        connect_timeout_ms: int = 10_000,
    ) -> None:
        if not STDIO_AVAILABLE:
            raise MCPClientError("mcp SDK not installed · `pip install mcp` or use MockMCPClient")
        self.config = config
        self.connect_timeout_ms = connect_timeout_ms
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._ready_event = threading.Event()
        self._connect_error: Exception | None = None

        self._start_background_loop()
        self._connect_sync()
        with _LIVE_LOCK:
            _LIVE_CLIENTS.add(self)

    def _start_background_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"mcp-{self.config.name}"
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready_event.set)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def _connect_sync(self) -> None:
        fut = asyncio.run_coroutine_threadsafe(self._connect_async(), self._loop)  # type: ignore[arg-type]
        try:
            fut.result(timeout=self.connect_timeout_ms / 1000)
        except (OSError, ConnectionError, TimeoutError) as e:
            self._connect_error = e
            self._shutdown_loop()
            raise MCPClientError(f"connect failed: {type(e).__name__}: {e}") from e

    async def _connect_async(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._stack = AsyncExitStack()
        params = self._stdio_parameters(StdioServerParameters)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    def _stdio_parameters(self, parameter_type: Any) -> Any:
        """Build SDK parameters, wrapping stdio in the hard process backend.

        The MCP SDK does not expose a launcher callback, but its parameter
        object accepts the final command/args/env/cwd. Transforming those
        values before handing them to ``stdio_client`` keeps the SDK protocol
        handling while putting the server process inside bwrap/Seatbelt.
        """

        from runtime.safety.sandboxing.sandbox import (
            SandboxPolicy,
            SandboxViolation,
            effective_process_sandbox_mode,
            inference_domains,
            process_sandbox_required,
            resolved_process_backend,
        )

        from .client import _connector_env_for

        # config env + connector env(认证编排层,仅已安装+启用+连接)。
        merged_env = dict(self.config.env) if self.config.env else {}
        for key, value in _connector_env_for(self.config.name).items():
            merged_env.setdefault(key, value)

        if not process_sandbox_required():
            return parameter_type(
                command=self.config.command,
                args=list(self.config.args),
                env=merged_env or None,
            )

        if not self.config.sandbox_dir:
            raise MCPClientError(
                "sandbox_violation: shared/commercial MCP stdio requires "
                "an operator-selected sandbox_dir"
            )
        from pathlib import Path

        workspace = Path(self.config.sandbox_dir).expanduser().resolve()
        if not workspace.is_dir():
            raise MCPClientError(
                f"sandbox_violation: MCP workspace is not a directory: {workspace}"
            )
        from runtime.platform.process.streaming import _sandbox_extra_env

        policy = SandboxPolicy(
            workspace=workspace,
            allow_network=False,
            timeout_s=self.config.timeout_ms / 1000,
            extra_env=_sandbox_extra_env(merged_env),
            # Model inference endpoints stay reachable in a network-denied
            # sandbox (Claude Desktop parity).
            inference_domains=inference_domains(),
        )
        try:
            choice = resolved_process_backend(effective_process_sandbox_mode())
            argv, env, cwd = choice.backend.transform(
                [self.config.command, *self.config.args],
                policy.env_for(),
                workspace,
                policy,
            )
        except SandboxViolation as exc:
            raise MCPClientError(f"sandbox_violation: {exc}") from exc
        return parameter_type(
            command=argv[0],
            args=argv[1:],
            env=env,
            cwd=str(cwd),
        )

    async def _reconnect_async(self) -> None:
        old_stack = self._stack
        self._stack = None
        self._session = None
        if old_stack is not None:
            try:
                await old_stack.aclose()
            except Exception as e:  # noqa: BLE001
                _LOG.debug("old stack aclose during reconnect: %s", e)
        await self._connect_async()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _LIVE_LOCK:
            _LIVE_CLIENTS.discard(self)
        if self._loop is not None and self._loop.is_running() and self._stack is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop)
                fut.result(timeout=5.0)
            except (OSError, ConnectionError, TimeoutError):  # noqa: BLE001 — MCP client teardown best-effort
                pass
        self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def list_tools(self) -> list[MCPTool]:
        self._check_alive()
        return self._call_with_reconnect(
            self._list_tools_async,
            (),
            self.config.timeout_ms / 1000,
        )

    def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult:
        self._check_alive()
        t0 = time.monotonic()
        token = cancellation or current_cancellation_token()
        if token.is_cancelled:
            return _cancelled_result(name, token, t0)
        with trace_stage("mcp.persistent.call_tool") as span:
            span.set_attribute("echo.mcp.server", self.config.name)
            span.set_attribute("echo.mcp.tool", name)
            try:
                result: MCPInvocationResult = self._call_with_reconnect(
                    self._call_tool_async,
                    (name, args, token),
                    self.config.timeout_ms / 1000,
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

    def _call_with_reconnect(
        self,
        factory: Any,
        args: tuple[Any, ...],
        timeout: float,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Any:
        try:
            return self._run_in_loop(factory(*args), timeout, cancellation=cancellation)
        except OperationCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if self._closed or not _should_reconnect(e):
                raise
            _LOG.warning(
                "MCP %s · connection lost (%s: %s) · reconnecting once",
                self.config.name,
                type(e).__name__,
                e,
            )
            try:
                self._run_in_loop(self._reconnect_async(), self.connect_timeout_ms / 1000)
            except Exception as rc:  # noqa: BLE001
                raise MCPClientError(f"reconnect failed: {type(rc).__name__}: {rc}") from rc
            return self._run_in_loop(factory(*args), timeout, cancellation=cancellation)

    async def _list_tools_async(self) -> list[MCPTool]:
        result = await self._session.list_tools()
        return [
            MCPTool(
                name=t.name,
                description=(t.description or ""),
                input_schema=(t.inputSchema or {}),
                server_name=self.config.name,
            )
            for t in result.tools
        ]

    async def _call_tool_async(
        self,
        name: str,
        args: dict[str, Any],
        cancellation: CancellationToken | None = None,
    ) -> MCPInvocationResult:
        from mcp import types as mcp_types

        token = cancellation or CancellationToken.none()
        request_state: dict[str, Any] = {}

        async def _invoke() -> Any:
            # Capture on the session loop immediately before ``call_tool``
            # enters ``send_request``. There is no await between these two
            # operations in the SDK, so concurrent MCP calls cannot make us
            # cancel a neighbour's request id.
            request_state["id"] = getattr(self._session, "_request_id", None)
            return await self._session.call_tool(name, args)

        async def _notify_cancel(reason: str) -> None:
            request_id = request_state.get("id")
            if request_id is None:
                return
            try:
                cancelled = mcp_types.CancelledNotification(
                    params=mcp_types.CancelledNotificationParams(
                        requestId=request_id,
                        reason=reason or "turn redirected",
                    )
                )
                # MCP 1.x wraps in ``ClientNotification``; 2.x made it a
                # Union type and accepts the notification directly.
                if isinstance(mcp_types.ClientNotification, type):
                    await self._session.send_notification(
                        mcp_types.ClientNotification(cancelled),
                    )
                else:
                    await self._session.send_notification(cancelled)
            except Exception as exc:  # noqa: BLE001 — remote cancellation is best-effort
                _LOG.debug("MCP cancel notification failed: %s", exc)

        from .client import _await_with_cancellation

        result = await _await_with_cancellation(
            _invoke(),
            token,
            cancel_notification=_notify_cancel,
        )
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

    # ─── helpers ────────────────────────────────

    def _run_in_loop(
        self,
        coro: Any,
        timeout: float,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Any:
        if self._loop is None or not self._loop.is_running():
            raise MCPClientError("event loop not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        token = cancellation or CancellationToken.none()

        def _cancel_future(_reason: str) -> None:
            fut.cancel()

        unsubscribe = token.on_cancelled(_cancel_future)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.CancelledError as exc:
            if token.is_cancelled:
                raise OperationCancelled(token.reason or "MCP call cancelled") from exc
            raise
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise
        finally:
            unsubscribe()

    def _check_alive(self) -> None:
        if self._closed:
            raise MCPClientError("client closed")
        if self._session is None:
            raise MCPClientError("not connected")
