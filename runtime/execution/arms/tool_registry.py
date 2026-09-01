"""MCP-style tool registry — declarative tool registration pattern.

Tools are registered with a name, description, input schema, and
handler function. The registry handles validation, dispatch, and
lifecycle so individual tools can stay focused on their domain.

Key features
~~~~~~~~~~~~
- **Declarative schema**: Tools define their input schema as a dict.
- **Typed handlers**: Handlers receive typed input and return results.
- **Event hooks**: on_will_call_tool / on_did_call_tool for observability.
- **Provider abstraction**: Tools are grouped into providers.

Echo Native tool pipeline (implementation lineage: DeepSeek Harness,
absorbed 2026-08-14)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- **Canonical output contract**: a tool may declare ``output_schema``
  (JSON Schema enforced against every successful value) plus a pure
  ``render`` projection for what the model sees. ``schemas()`` is an
  explicit allowlist — host-only fields (output, timeout, concurrency,
  render) never leak into a model request.
- **Four-stage pipeline**: ``pre-execute`` (allow/deny/ask decision
  chain), ``execute`` (around-dispatch wrappers + cooperative timeout),
  ``post-execute`` (accept/replace/block), ``result`` (final
  observation). The legacy ``on_will_call_tool`` / ``on_did_call_tool``
  emit hooks remain for backward compatibility.
- **Explicit concurrency**: ``is_concurrency_safe`` lets a tool opt in
  to parallel dispatch; ``concurrency_safe_tools()`` lists the eligible
  names for the batch layer.
- **Last-mile finalization**: ``finalize_content`` runs exactly once
  per normalized outcome — including pipeline failures — immediately
  before materialization; returning ``None`` preserves the content.
- **Scoped registration** (dsh ``scope``): tools may register into a
  named scope layer; callers resolving through that scope see the
  scoped layer shadowing the global layer. No scope means global-only
  behavior, unchanged from the original contract.

Usage
~~~~~
    registry = ToolRegistry()

    # Register a tool
    registry.register_tool(
        name="execute_shell",
        description="Execute a shell command",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
        handler=execute_shell_handler,
    )

    # Call a tool
    result = await registry.call_tool("execute_shell", {
        "command": "ls -la",
    })
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from runtime.adapters.instrumentation import trace_stage

_logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Declarative definition of a tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    # dsh-style canonical output contract. All fields are host-only:
    # ``schemas()`` never exposes them to the model.
    output_schema: dict[str, Any] | None = None
    render: Callable[[dict[str, Any], Any], Any] | None = None
    timeout_ms: int | None = None
    is_concurrency_safe: Callable[[dict[str, Any]], bool] | None = None
    finalize_content: Callable[[Any], Any | None] | None = None


@dataclass
class ToolCallContext:
    """Context passed to tool call event handlers."""

    tool_name: str
    input: dict[str, Any]
    session_id: str | None = None
    chat_session_id: str | None = None


@dataclass
class ToolCallResult:
    """Result of a tool call."""

    tool_name: str
    success: bool
    output: Any
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class ToolProvider:
    """A group of tools registered under a single provider."""

    id: str
    display_name: str
    tools: list[ToolDefinition] = field(default_factory=list)
    feature_flags: list[str] = field(default_factory=list)
    is_ready: bool = False


class PreToolDecision(StrEnum):
    """Decision of a pre-execute gate (dsh ``tools/pre-execute``)."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PostToolDecision(StrEnum):
    """Decision of a post-execute gate (dsh ``tools/post-execute``)."""

    ACCEPT = "accept"
    REPLACE = "replace"
    BLOCK = "block"


# Event handler types
WillCallToolHandler = Callable[[ToolCallContext], Awaitable[None]]
DidCallToolHandler = Callable[[ToolCallResult], Awaitable[None]]
PreExecuteHandler = Callable[[ToolCallContext], Awaitable[PreToolDecision | None]]
ExecuteWrapperHandler = Callable[
    [ToolCallContext, Callable[[], Awaitable[Any]]],
    Awaitable[Any],
]
PostExecuteHandler = Callable[
    [ToolCallContext, ToolCallResult],
    Awaitable[PostToolDecision | None],
]
ResultHandler = Callable[[ToolCallResult], Awaitable[None]]


class ToolRegistry:
    """Registry for MCP-style tools.

    Manages tool registration, validation, and execution with
    event hooks for observability and pre/post processing.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._scoped_tools: dict[str, dict[str, ToolDefinition]] = {}
        self._providers: dict[str, ToolProvider] = {}
        self._on_will_call: list[WillCallToolHandler] = []
        self._on_did_call: list[DidCallToolHandler] = []
        self._on_pre_execute: list[PreExecuteHandler] = []
        self._on_execute: list[ExecuteWrapperHandler] = []
        self._on_post_execute: list[PostExecuteHandler] = []
        self._on_result: list[ResultHandler] = []
        self._call_count = 0

    @property
    def tool_names(self) -> list[str]:
        """List of all globally registered tool names."""
        return list(self._tools.keys())

    def tool_names_for(self, scope: str | None = None) -> list[str]:
        """Names visible in a scope: global tools (insertion order)
        followed by scoped shadows and scoped-only tools. With no scope
        this matches ``tool_names`` exactly."""
        if scope is None:
            return list(self._tools.keys())
        merged = dict(self._tools)
        merged.update(self._scoped_tools.get(scope, {}))
        return list(merged.keys())

    def _resolve_tool(
        self,
        tool_name: str,
        scope: str | None = None,
    ) -> ToolDefinition | None:
        """Resolve a tool for a scope: the scoped layer shadows the
        global layer (dsh ``ScopedLayers`` merge)."""
        if scope is not None:
            layer = self._scoped_tools.get(scope)
            if layer and tool_name in layer:
                return layer[tool_name]
        return self._tools.get(tool_name)

    def dispose_scope(self, scope: str) -> None:
        """Remove every tool registered in a scope layer. Global tools
        are never touched; the layer is reclaimed wholesale."""
        layer = self._scoped_tools.pop(scope, None)
        if layer:
            _logger.info("disposed %d scoped tool(s) for scope %s", len(layer), scope)

    @property
    def providers(self) -> dict[str, ToolProvider]:
        """All registered providers."""
        return dict(self._providers)

    def register_provider(
        self,
        provider_id: str,
        display_name: str,
        *,
        feature_flags: list[str] | None = None,
    ) -> ToolProvider:
        """Register a tool provider.

        Args:
            provider_id: Unique provider identifier.
            display_name: Human-readable name.
            feature_flags: Optional feature flags for this provider.

        Returns:
            The created ToolProvider.
        """
        provider = ToolProvider(
            id=provider_id,
            display_name=display_name,
            feature_flags=feature_flags or [],
        )
        self._providers[provider_id] = provider
        return provider

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any]],
        *,
        provider_id: str | None = None,
        scope: str | None = None,
        output_schema: dict[str, Any] | None = None,
        render: Callable[[dict[str, Any], Any], Any] | None = None,
        timeout_ms: int | None = None,
        is_concurrency_safe: Callable[[dict[str, Any]], bool] | None = None,
        finalize_content: Callable[[Any], Any | None] | None = None,
    ) -> Callable[[], None]:
        """Register a tool.

        Args:
            name: Unique tool name.
            description: Human-readable description.
            input_schema: JSON Schema for tool input.
            handler: Async function that executes the tool.
            provider_id: Optional provider to associate with.
            scope: Optional scope key (dsh ``ScopeKey``). Tools in a
                scope layer shadow same-named global tools for callers
                that resolve through that scope; no scope means global.
            output_schema: Optional canonical output contract (JSON
                Schema) enforced against every successful value. Never
                sent to the model.
            render: Optional pure projection ``(args, value) -> model
                content``. The host always receives the canonical value;
                callers materialize the projection for the model.
            timeout_ms: Optional cooperative timeout budget. Enforced
                by the registry; never sent to the model.
            is_concurrency_safe: Optional pure classifier for overlap
                with sibling calls. Only ``True`` opts in.
            finalize_content: Optional last-mile transform run exactly
                once per normalized outcome (including failures) before
                materialization; ``None`` preserves the content.
        """
        tool = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            output_schema=output_schema,
            render=render,
            timeout_ms=timeout_ms,
            is_concurrency_safe=is_concurrency_safe,
            finalize_content=finalize_content,
        )
        if scope is not None:
            layer = self._scoped_tools.setdefault(scope, {})
            if name in layer:
                raise ValueError(f"Tool '{name}' is already registered in scope '{scope}'")
            layer[name] = tool
        else:
            if name in self._tools:
                raise ValueError(f"Tool '{name}' is already registered")
            self._tools[name] = tool

        if provider_id and provider_id in self._providers:
            self._providers[provider_id].tools.append(tool)

        _logger.info("registered tool: %s", name)

        # A disposer is the ownership primitive used by dynamically loaded
        # plugins.  It is identity-aware so a stale disposer from an older
        # plugin generation can never remove a newer registration that reused
        # the same public name after a reload.
        def dispose() -> None:
            self._unregister_tool_if_same(name, tool, scope=scope)

        return dispose

    def unregister_tool(self, name: str, *, scope: str | None = None) -> bool:
        """Remove one exact tool registration.

        ``scope`` mirrors :meth:`register_tool`; omitting it only touches the
        global layer.  Provider membership is cleaned at the same time so
        introspection cannot retain a ghost tool after a plugin unload.
        """

        layer = self._tools if scope is None else self._scoped_tools.get(scope)
        if layer is None:
            return False
        tool = layer.pop(name, None)
        if tool is None:
            return False
        if scope is not None and not layer:
            self._scoped_tools.pop(scope, None)
        self._remove_tool_from_providers(tool)
        _logger.info("unregistered tool: %s", name)
        return True

    def _unregister_tool_if_same(
        self,
        name: str,
        expected: ToolDefinition,
        *,
        scope: str | None,
    ) -> bool:
        layer = self._tools if scope is None else self._scoped_tools.get(scope)
        if layer is None or layer.get(name) is not expected:
            return False
        layer.pop(name, None)
        if scope is not None and not layer:
            self._scoped_tools.pop(scope, None)
        self._remove_tool_from_providers(expected)
        _logger.info("unregistered tool: %s", name)
        return True

    def _remove_tool_from_providers(self, tool: ToolDefinition) -> None:
        for provider in self._providers.values():
            provider.tools[:] = [
                registered for registered in provider.tools if registered is not tool
            ]

    def unregister_provider(self, provider_id: str) -> bool:
        """Remove provider metadata without deleting its tool definitions."""

        return self._providers.pop(provider_id, None) is not None

    @staticmethod
    def _register_handler(
        handlers: list[Any],
        handler: Any,
    ) -> Callable[[], None]:
        handlers.append(handler)

        def dispose() -> None:
            for index, registered in enumerate(handlers):
                if registered is handler:
                    handlers.pop(index)
                    break

        return dispose

    def on_will_call_tool(self, handler: WillCallToolHandler) -> Callable[[], None]:
        """Register a pre-call event handler."""
        return self._register_handler(self._on_will_call, handler)

    def on_did_call_tool(self, handler: DidCallToolHandler) -> Callable[[], None]:
        """Register a post-call event handler."""
        return self._register_handler(self._on_did_call, handler)

    def on_pre_execute(self, handler: PreExecuteHandler) -> Callable[[], None]:
        """Register a pre-execute gate (dsh ``tools/pre-execute``).

        Gates run in registration order. The first ``deny`` rejects the
        call; ``ask`` requires an ``approve`` callback on ``call_tool``
        (missing approval support turns ``ask`` into denial); an empty
        result or ``allow`` defers to the next gate.
        """
        return self._register_handler(self._on_pre_execute, handler)

    def on_execute(self, wrapper: ExecuteWrapperHandler) -> Callable[[], None]:
        """Register an around-dispatch wrapper (dsh ``tools/execute``).

        Wrappers run in registration order and must call ``next()`` to
        delegate to the underlying handler (or the next wrapper). They
        may add timeout, retry, metrics, or argument rewriting.
        """
        return self._register_handler(self._on_execute, wrapper)

    def on_post_execute(self, handler: PostExecuteHandler) -> Callable[[], None]:
        """Register a post-execute gate (dsh ``tools/post-execute``).

        Gates run in registration order over the normalized result; a
        ``replace`` gate sets ``result.output`` to its replacement, a
        ``block`` gate turns the outcome into a failure (it may set
        ``result.error`` first). The first ``block`` short-circuits.
        """
        return self._register_handler(self._on_post_execute, handler)

    def on_result(self, handler: ResultHandler) -> Callable[[], None]:
        """Register a final result observer (dsh ``tools/result``)."""
        return self._register_handler(self._on_result, handler)

    async def call_tool(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        context: ToolCallContext | None = None,
        approve: Callable[[ToolCallContext], Awaitable[bool]] | None = None,
        scope: str | None = None,
    ) -> ToolCallResult:
        """Call a registered tool by name.

        Args:
            tool_name: The tool to call.
            input: Input parameters for the tool.
            context: Optional call context for event handlers.
            approve: Optional interactive approver for ``ask`` gates.
                Absent approval support turns ``ask`` into denial.
            scope: Optional scope key. The scoped layer shadows the
                global layer for this call.

        Returns:
            ToolCallResult with output and timing info.

        Pipeline (dsh four-stage):
            pre-execute (allow/deny/ask) → will_call (legacy emit) →
            execute (wrappers + cooperative timeout) → canonical output
            validation → post-execute (accept/replace/block) →
            finalize_content (always once) → did_call (legacy emit) →
            result (emit).
        """
        tool = self._resolve_tool(tool_name, scope)
        if tool is None:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=f"Tool '{tool_name}' is not registered",
            )

        # Build call context
        if context is None:
            context = ToolCallContext(
                tool_name=tool_name,
                input=input,
            )

        # Stage 1 · pre-execute decision chain (dsh tools/pre-execute).
        decision = await self._run_pre_execute(context)
        if decision == PreToolDecision.DENY:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error="Tool call denied by pre-execute policy",
            )
        if decision == PreToolDecision.ASK:
            if approve is None:
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error="Tool call requires approval but no approver is configured",
                )
            granted = False
            try:
                granted = bool(await approve(context))
            except Exception as e:
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=f"Approval gate failed: {e}",
                )
            if not granted:
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error="Tool call rejected by approval",
                )

        self._call_count += 1

        # Stage 2 · legacy emit hook (backward compatibility).
        for handler in self._on_will_call:
            try:
                await handler(context)
            except Exception as e:
                return ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=f"Pre-call hook failed: {e}",
                )

        # Stage 3 · execute (wrappers + cooperative timeout).
        start_time = time.perf_counter()
        try:
            with trace_stage(
                "tool.call",
                tool_name=tool_name,
            ) as span:
                span.set_attribute("echo.tool.input_keys", list(input.keys()))
                output = await self._run_execute(context, tool, input)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                span.set_attribute("echo.tool.elapsed_ms", elapsed_ms)

            result = ToolCallResult(
                tool_name=tool_name,
                success=True,
                output=output,
                elapsed_ms=elapsed_ms,
            )
        except TimeoutError:
            result = ToolCallResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=f"Tool '{tool_name}' timed out",
                elapsed_ms=(time.perf_counter() - start_time) * 1000,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            _logger.exception("tool call failed: %s", tool_name)
            result = ToolCallResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=str(e),
                elapsed_ms=elapsed_ms,
            )

        # Stage 4 · canonical output validation (dsh output contract).
        if result.success and tool.output_schema is not None:
            try:
                _validate_output_value(result.output, tool.output_schema)
            except ValueError as e:
                result = ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=f"Output contract violation: {e}",
                    elapsed_ms=result.elapsed_ms,
                )

        # Stage 5 · post-execute decision chain (dsh tools/post-execute).
        for handler in self._on_post_execute:
            try:
                decision = await _maybe_await(handler(context, result))
            except Exception as e:
                result = ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=f"Post-execute gate failed: {e}",
                    elapsed_ms=result.elapsed_ms,
                )
                break
            if decision == PostToolDecision.BLOCK:
                result = ToolCallResult(
                    tool_name=tool_name,
                    success=False,
                    output=result.output,
                    error=result.error or "Tool result blocked by post-execute policy",
                    elapsed_ms=result.elapsed_ms,
                )
                break

        # Stage 6 · last-mile finalization, exactly once per outcome
        # (including failures), before materialization (dsh
        # finalizeContent).
        if tool.finalize_content is not None:
            try:
                finalized = tool.finalize_content(result)
                if finalized is not None:
                    result = ToolCallResult(
                        tool_name=tool_name,
                        success=result.success,
                        output=finalized,
                        error=result.error,
                        elapsed_ms=result.elapsed_ms,
                    )
            except Exception:
                _logger.exception("finalize_content failed for tool: %s", tool_name)

        # Stage 7 · legacy emit hook (backward compatibility).
        for handler in self._on_did_call:
            try:
                await handler(result)
            except (TypeError, ValueError, RuntimeError):
                _logger.warning("Post-call hook failed for tool: %s", tool_name)

        # Stage 8 · final result observation (dsh tools/result).
        for handler in self._on_result:
            try:
                await handler(result)
            except Exception:
                _logger.exception("Result hook failed for tool: %s", tool_name)

        return result

    async def _run_pre_execute(
        self,
        context: ToolCallContext,
    ) -> PreToolDecision | None:
        """Run the pre-execute decision chain in registration order."""
        outcome: PreToolDecision | None = None
        for handler in self._on_pre_execute:
            try:
                decision = await _maybe_await(handler(context))
            except Exception as e:
                _logger.warning("Pre-execute gate failed for %s: %s", context.tool_name, e)
                decision = PreToolDecision.DENY
            if decision == PreToolDecision.DENY:
                return PreToolDecision.DENY
            if decision == PreToolDecision.ASK and outcome is None:
                outcome = PreToolDecision.ASK
        return outcome

    async def _run_execute(
        self,
        context: ToolCallContext,
        tool: ToolDefinition,
        input: dict[str, Any],
    ) -> Any:
        """Run the around-dispatch wrapper chain, then the handler with
        its cooperative timeout budget."""

        async def base() -> Any:
            if tool.timeout_ms is not None and tool.timeout_ms > 0:
                return await asyncio.wait_for(
                    tool.handler(input),
                    timeout=tool.timeout_ms / 1000,
                )
            return await tool.handler(input)

        async def chain(index: int) -> Any:
            if index >= len(self._on_execute):
                return await base()
            wrapper = self._on_execute[index]
            return await wrapper(context, lambda: chain(index + 1))

        return await chain(0)

    def materialize(
        self,
        result: ToolCallResult,
        args: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
    ) -> Any:
        """Project the canonical output into model-facing content
        (dsh ``render``). The host always receives the canonical value
        from ``call_tool``; call this right before the content is sent
        to the model. Returns the raw output when the tool declares no
        render projection or the call failed."""
        if not result.success:
            return result.output
        tool = self._resolve_tool(result.tool_name, scope)
        if tool is None or tool.render is None:
            return result.output
        try:
            return tool.render(args or {}, result.output)
        except Exception:
            _logger.exception("render failed for tool: %s", result.tool_name)
            return result.output

    def is_concurrency_safe(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
    ) -> bool:
        """Whether this tool opted in to parallel dispatch for these
        arguments (dsh ``isConcurrencySafe``). Only ``True`` opts in."""
        tool = self._resolve_tool(tool_name, scope)
        if tool is None or tool.is_concurrency_safe is None:
            return False
        try:
            return bool(tool.is_concurrency_safe(args or {}))
        except Exception:
            return False

    def concurrency_safe_tools(
        self,
        args: dict[str, Any] | None = None,
        *,
        scope: str | None = None,
    ) -> list[str]:
        """Names of tools that opted in to parallel dispatch for these
        arguments, in registration order."""
        return [
            name
            for name in self.tool_names_for(scope)
            if self.is_concurrency_safe(name, args, scope=scope)
        ]

    def get_tool_metadata(
        self,
        tool_name: str,
        *,
        scope: str | None = None,
    ) -> dict[str, Any] | None:
        """Host-only metadata for a tool. Never exposed through
        ``get_tool_schema``/``get_all_tool_schemas`` (the model-facing
        allowlist), so output/timeout/concurrency declarations cannot
        leak into a model request."""
        tool = self._resolve_tool(tool_name, scope)
        if not tool:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "timeout_ms": tool.timeout_ms,
            "declares_concurrency_safety": tool.is_concurrency_safe is not None,
            "declares_render": tool.render is not None,
            "declares_finalize_content": tool.finalize_content is not None,
        }

    def get_tool_schema(
        self,
        tool_name: str,
        *,
        scope: str | None = None,
    ) -> dict[str, Any] | None:
        """Get the model-facing schema for a tool.

        This is an explicit allowlist (dsh ``schemas()``): host-only
        fields — output contract, timeout, concurrency declaration,
        render/finalize callbacks — must never leak into a model
        request.
        """
        tool = self._resolve_tool(tool_name, scope)
        if not tool:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }

    def get_all_tool_schemas(
        self,
        *,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get schemas for all registered tools."""
        return [
            self.get_tool_schema(name, scope=scope)
            for name in self.tool_names_for(scope)
            if self.get_tool_schema(name, scope=scope) is not None
        ]

    def mark_provider_ready(self, provider_id: str) -> None:
        """Mark a provider as ready for tool calls."""
        if provider_id in self._providers:
            self._providers[provider_id].is_ready = True

    @property
    def call_count(self) -> int:
        """Total number of tool calls made."""
        return self._call_count


# Global registry instance
_global_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def _validate_output_value(output: Any, schema: dict[str, Any]) -> None:
    """Enforce a canonical output contract against a successful value.

    Mirrors the executor's skill ``output_schema`` semantics (required
    keys + scalar type checks) with array item support. Raises
    ``ValueError`` on the first violation.
    """
    if not isinstance(schema, dict):
        return
    if not isinstance(output, dict):
        raise ValueError(f"expected object output, got {type(output).__name__}")
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    for key in required:
        if key not in output:
            raise ValueError(f"missing required field: {key}")
    for key, prop in properties.items():
        if key not in output or not isinstance(prop, dict):
            continue
        expected_type = prop.get("type")
        val = output[key]
        if expected_type == "string" and not isinstance(val, str):
            raise ValueError(f"field {key}: expected string, got {type(val).__name__}")
        if expected_type == "number" and not isinstance(val, (int, float)):
            raise ValueError(f"field {key}: expected number, got {type(val).__name__}")
        if expected_type == "boolean" and not isinstance(val, bool):
            raise ValueError(f"field {key}: expected boolean, got {type(val).__name__}")
        if expected_type == "array":
            if not isinstance(val, list):
                raise ValueError(f"field {key}: expected array, got {type(val).__name__}")
            items = prop.get("items")
            if isinstance(items, dict) and items.get("type") == "string":
                for item in val:
                    if not isinstance(item, str):
                        raise ValueError(
                            f"field {key}: expected array of strings, got {type(item).__name__}"
                        )
        if expected_type == "object" and not isinstance(val, dict):
            raise ValueError(f"field {key}: expected object, got {type(val).__name__}")


async def _maybe_await(value: Any) -> Any:
    """Await a coroutine when the callback returned one, otherwise
    return the value untouched (sync callbacks stay supported)."""
    if inspect.iscoroutine(value):
        return await value
    return value
