"""Public facade for embedding the Echo Agent kernel.

The implementation remains in its existing, well-tested modules.  This file
defines the stable host-facing seam so a desktop shell or another application
can package the kernel as a subsystem without importing gateway/UI internals.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.config import AgentConfig, BuiltStack, build_from_config
from runtime.platform.process.paths import app_paths


class AgentKernel:
    """Own one fully assembled Agent execution kernel.

    ``AgentKernel`` deliberately owns the execution stack, not a web server or
    a workbench UI.  A host can embed it in-process, expose its own transport,
    or pass it to :func:`runtime.platform.ui.create_app` as the application
    adapter is migrated.

    Use :meth:`from_config` for a normal deployment.  ``from_stack`` keeps the
    facade compatible with callers that already construct ``BuiltStack`` (for
    example tests and specialized hosts).
    """

    def __init__(
        self,
        stack: BuiltStack,
        *,
        config: AgentConfig | None = None,
    ) -> None:
        self._stack = stack
        self._config = config or getattr(stack, "config", None)
        self._realtime_runtime: Any | None = None
        self._closed = False

    @classmethod
    def from_config(cls, config: AgentConfig) -> AgentKernel:
        """Build a kernel from the same typed config used by ``serve``."""

        if not isinstance(config, AgentConfig):
            raise TypeError("config must be an AgentConfig")
        return cls(build_from_config(config), config=config)

    @classmethod
    def build(cls, config: AgentConfig) -> AgentKernel:
        """Readable alias for :meth:`from_config` used by host applications."""

        return cls.from_config(config)

    @classmethod
    def from_stack(
        cls,
        stack: BuiltStack,
        *,
        config: AgentConfig | None = None,
    ) -> AgentKernel:
        """Wrap an already-built stack without rebuilding it."""

        if stack is None:
            raise TypeError("stack is required")
        return cls(stack, config=config)

    @property
    def config(self) -> AgentConfig | None:
        """Typed configuration used to assemble this kernel."""

        return self._config

    @property
    def stack(self) -> BuiltStack:
        """Compatibility escape hatch for host adapters during migration."""

        return self._stack

    @property
    def registry(self) -> Any:
        return self._stack.registry

    @property
    def journal(self) -> Any:
        return self._stack.journal

    @property
    def planner(self) -> Any:
        return self._stack.planner

    @property
    def executor(self) -> Any:
        return self._stack.executor

    @property
    def graph_runtime(self) -> Any:
        return self._stack.runtime

    @property
    def realtime_runtime(self) -> Any | None:
        """The lazily-created Realtime/Cerebrum adapter, if requested."""

        return self._realtime_runtime

    @property
    def closed(self) -> bool:
        """Whether this kernel has released its owned resources."""

        return self._closed

    def create_realtime_runtime(self, **kwargs: Any) -> Any:
        """Create the standard Realtime/Cerebrum adapter for this kernel.

        The realtime adapter is a transport-facing wrapper around the kernel;
        it is intentionally lazy so CLI, batch, and desktop hosts can use the
        same kernel without paying for websocket/thread state they do not need.
        Host-specific stores and registries can be supplied as keyword
        arguments.  Sensible local paths are provided when omitted.
        """

        if self._closed:
            raise RuntimeError("agent kernel is closed")
        if self._realtime_runtime is not None:
            if kwargs:
                raise RuntimeError(
                    "realtime runtime is already configured; create a new kernel "
                    "to change host-specific runtime options"
                )
            return self._realtime_runtime
        from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

        requested_stack = kwargs.get("stack")
        if requested_stack is not None and requested_stack is not self._stack:
            raise ValueError("realtime runtime must use this kernel's stack")
        kwargs["stack"] = self._stack
        paths = app_paths()
        kwargs.setdefault("logs_root", str(paths.data_dir / "threads"))
        kwargs.setdefault("policy_path", paths.permissions_path)
        kwargs.setdefault("workspace_root", str(paths.data_dir / "workspaces"))
        self._realtime_runtime = CerebrumRuntime(**kwargs)
        return self._realtime_runtime

    async def handle_request(
        self,
        method: str,
        params: dict[str, Any],
        emitter: Any,
    ) -> Any:
        """Forward one realtime protocol request through the kernel adapter."""

        runtime = self._realtime_runtime or self.create_realtime_runtime()
        return await runtime.handle_request(method, params, emitter)

    def close(self) -> None:
        """Release kernel-owned clients and make future use fail fast."""

        if self._closed:
            return
        self._closed = True
        close_clients = getattr(self._stack, "close_mcp_clients", None)
        if callable(close_clients):
            close_clients()

    async def aclose(self, *, timeout_seconds: float = 3.0) -> None:
        """Drain active realtime work, then release kernel-owned resources."""

        if self._closed:
            return
        try:
            runtime = self._realtime_runtime
            drain = getattr(runtime, "drain_active_turns_for_shutdown", None)
            if callable(drain):
                await drain(timeout_seconds=timeout_seconds)
        finally:
            # A failed drain must not leak MCP clients or leave the kernel
            # looking usable to a host that is already shutting down.
            self.close()

    shutdown = close


__all__ = ["AgentKernel"]
