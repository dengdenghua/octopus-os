"""dsh-style tool contract + four-stage pipeline tests.

Absorbed from DeepSeek Harness (2026-08-14): canonical output
contracts, pre-execute decision gates, around-dispatch wrappers with
cooperative timeouts, post-execute accept/replace/block, last-mile
finalization, explicit concurrency declarations, and the model-facing
schema allowlist. The legacy ``on_will_call_tool`` /
``on_did_call_tool`` emit hooks must keep working untouched.
"""

from __future__ import annotations

import asyncio

import pytest

from runtime.execution.arms.tool_registry import (
    PostToolDecision,
    PreToolDecision,
    ToolCallContext,
    ToolRegistry,
)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


async def _register_echo(registry: ToolRegistry, **kwargs: object) -> None:
    async def handler(input: dict) -> dict:
        return input

    registry.register_tool(
        "echo",
        "Echo the input",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler,
        **kwargs,
    )


def test_dynamic_tool_and_pipeline_registrations_are_disposable() -> None:
    registry = ToolRegistry()

    async def handler(args: dict[str, object]) -> dict[str, object]:
        return args

    async def result_handler(_result) -> None:
        return None

    dispose_tool = registry.register_tool(
        "plugin.echo",
        "Echo plugin input",
        {"type": "object"},
        handler,
    )
    dispose_result = registry.on_result(result_handler)

    assert "plugin.echo" in registry.tool_names
    assert result_handler in registry._on_result

    dispose_result()
    dispose_tool()
    dispose_result()
    dispose_tool()

    assert "plugin.echo" not in registry.tool_names
    assert result_handler not in registry._on_result


@pytest.mark.asyncio
class TestSchemaAllowlist:
    """Host-only fields must never leak into a model request."""

    async def test_schema_never_exposes_host_fields(self, registry: ToolRegistry) -> None:
        await _register_echo(
            registry,
            output_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            timeout_ms=500,
            is_concurrency_safe=lambda _args: True,
            render=lambda _args, value: f"rendered:{value}",
        )

        schema = registry.get_tool_schema("echo")
        assert schema is not None
        assert set(schema.keys()) == {"name", "description", "inputSchema"}

        schemas = registry.get_all_tool_schemas()
        assert len(schemas) == 1
        assert "output_schema" not in schemas[0]
        assert "timeout_ms" not in schemas[0]

    async def test_metadata_holds_host_fields(self, registry: ToolRegistry) -> None:
        await _register_echo(
            registry,
            output_schema={"type": "object"},
            timeout_ms=250,
            is_concurrency_safe=lambda _args: True,
            render=lambda _args, value: value,
            finalize_content=lambda _result: None,
        )
        meta = registry.get_tool_metadata("echo")
        assert meta is not None
        assert meta["timeout_ms"] == 250
        assert meta["declares_concurrency_safety"] is True
        assert meta["declares_render"] is True
        assert meta["declares_finalize_content"] is True
        assert registry.get_tool_metadata("missing") is None


@pytest.mark.asyncio
class TestPreExecuteGates:
    async def test_deny_rejects_without_running_handler(
        self,
        registry: ToolRegistry,
    ) -> None:
        ran = False

        async def handler(_input: dict) -> dict:
            nonlocal ran
            ran = True
            return {}

        registry.register_tool("t", "t", {"type": "object"}, handler)
        registry.on_pre_execute(lambda _ctx: _async(PreToolDecision.DENY))

        result = await registry.call_tool("t", {})
        assert result.success is False
        assert "denied" in (result.error or "")
        assert ran is False

    async def test_ask_without_approver_becomes_denial(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)
        registry.on_pre_execute(lambda _ctx: _async(PreToolDecision.ASK))

        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is False
        assert "approval" in (result.error or "").lower()

    async def test_ask_with_approver(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)
        registry.on_pre_execute(lambda _ctx: _async(PreToolDecision.ASK))
        approvals: list[ToolCallContext] = []

        async def approve(ctx: ToolCallContext) -> bool:
            approvals.append(ctx)
            return True

        result = await registry.call_tool(
            "echo",
            {"value": "x"},
            approve=approve,
        )
        assert result.success is True
        assert len(approvals) == 1

    async def test_ask_with_approver_rejection(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)
        registry.on_pre_execute(lambda _ctx: _async(PreToolDecision.ASK))

        async def approve(_ctx: ToolCallContext) -> bool:
            return False

        result = await registry.call_tool("echo", {"value": "x"}, approve=approve)
        assert result.success is False
        assert "rejected" in (result.error or "").lower()

    async def test_first_deny_short_circuits_chain(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)
        calls: list[str] = []
        registry.on_pre_execute(lambda _ctx: _chain_append(calls, "g1", PreToolDecision.ALLOW))
        registry.on_pre_execute(lambda _ctx: _chain_append(calls, "g2", PreToolDecision.DENY))
        registry.on_pre_execute(lambda _ctx: _chain_append(calls, "g3", PreToolDecision.ALLOW))

        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is False
        assert calls == ["g1", "g2"]


@pytest.mark.asyncio
class TestExecuteWrappersAndTimeout:
    async def test_cooperative_timeout_marks_failure(self, registry: ToolRegistry) -> None:
        async def slow(_input: dict) -> dict:
            await asyncio.sleep(0.5)
            return {}

        registry.register_tool(
            "slow",
            "slow",
            {"type": "object"},
            slow,
            timeout_ms=50,
        )

        result = await registry.call_tool("slow", {})
        assert result.success is False
        assert "timed out" in (result.error or "")

    async def test_wrapper_chain_order(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)
        order: list[str] = []

        async def wrap_a(
            _ctx: ToolCallContext,
            next_call,
        ):
            order.append("a-in")
            value = await next_call()
            order.append("a-out")
            return value

        async def wrap_b(
            _ctx: ToolCallContext,
            next_call,
        ):
            order.append("b-in")
            value = await next_call()
            order.append("b-out")
            return value

        registry.on_execute(wrap_a)
        registry.on_execute(wrap_b)
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True
        assert order == ["a-in", "b-in", "b-out", "a-out"]

    async def test_wrapper_can_rewrite_output(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)

        async def wrap(
            _ctx: ToolCallContext,
            next_call,
        ):
            value = await next_call()
            return {**value, "wrapped": True}

        registry.on_execute(wrap)
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True
        assert result.output == {"value": "x", "wrapped": True}


@pytest.mark.asyncio
class TestPostExecuteGates:
    async def test_replace_swaps_output(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)

        def replace(_ctx: ToolCallContext, result):
            result.output = {"replaced": True}
            return PostToolDecision.REPLACE

        registry.on_post_execute(replace)
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True
        assert result.output == {"replaced": True}

    async def test_block_turns_outcome_into_failure(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)

        def block(_ctx: ToolCallContext, result):
            result.error = "blocked by policy"
            return PostToolDecision.BLOCK

        registry.on_post_execute(block)
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is False
        assert result.error == "blocked by policy"


@pytest.mark.asyncio
class TestOutputContract:
    async def test_missing_required_field_fails(self, registry: ToolRegistry) -> None:
        async def handler(_input: dict) -> dict:
            return {"other": "oops"}

        registry.register_tool(
            "bad",
            "bad",
            {"type": "object"},
            handler,
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )
        result = await registry.call_tool("bad", {})
        assert result.success is False
        assert "missing required field" in (result.error or "")

    async def test_type_violation_fails(self, registry: ToolRegistry) -> None:
        async def handler(_input: dict) -> dict:
            return {"count": "not-a-number"}

        registry.register_tool(
            "bad",
            "bad",
            {"type": "object"},
            handler,
            output_schema={
                "type": "object",
                "properties": {"count": {"type": "number"}},
                "required": ["count"],
            },
        )
        result = await registry.call_tool("bad", {})
        assert result.success is False
        assert "expected number" in (result.error or "")

    async def test_valid_output_passes(self, registry: ToolRegistry) -> None:
        await _register_echo(
            registry,
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True

    async def test_array_of_strings_enforced(self, registry: ToolRegistry) -> None:
        async def handler(_input: dict) -> dict:
            return {"items": [1, 2]}

        registry.register_tool(
            "bad-array",
            "bad-array",
            {"type": "object"},
            handler,
            output_schema={
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"],
            },
        )
        result = await registry.call_tool("bad-array", {})
        assert result.success is False
        assert "array of strings" in (result.error or "")


@pytest.mark.asyncio
class TestRenderMaterialize:
    async def test_host_gets_canonical_model_gets_projection(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(
            registry,
            render=lambda args, value: f"rendered:{value.get('value')}",
        )
        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True
        # Host sees the canonical value...
        assert result.output == {"value": "x"}
        # ...the model sees the projection.
        assert registry.materialize(result, {"value": "x"}) == "rendered:x"

    async def test_materialize_passthrough_without_render(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)
        result = await registry.call_tool("echo", {"value": "x"})
        assert registry.materialize(result, {"value": "x"}) == {"value": "x"}

    async def test_materialize_passthrough_on_failure(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry, render=lambda _a, v: f"r:{v}")

        async def handler(_input: dict) -> dict:
            raise RuntimeError("boom")

        registry.register_tool("boom", "boom", {"type": "object"}, handler)
        result = await registry.call_tool("boom", {})
        assert result.success is False
        assert registry.materialize(result, {}) is None


@pytest.mark.asyncio
class TestFinalizeContent:
    async def test_runs_once_on_success(self, registry: ToolRegistry) -> None:
        runs: list[object] = []

        async def handler(_input: dict) -> dict:
            return {"value": "x"}

        def finalize(result) -> object | None:
            runs.append(result.output)
            return {"finalized": True}

        registry.register_tool(
            "f",
            "f",
            {"type": "object"},
            handler,
            finalize_content=finalize,
        )
        result = await registry.call_tool("f", {})
        assert result.success is True
        assert result.output == {"finalized": True}
        assert len(runs) == 1

    async def test_runs_on_failure_too(self, registry: ToolRegistry) -> None:
        runs: list[object] = []

        async def handler(_input: dict) -> dict:
            raise RuntimeError("boom")

        def finalize(result) -> object | None:
            runs.append(result.success)
            return None  # preserve the failed outcome

        registry.register_tool(
            "f",
            "f",
            {"type": "object"},
            handler,
            finalize_content=finalize,
        )
        result = await registry.call_tool("f", {})
        assert result.success is False
        assert runs == [False]


@pytest.mark.asyncio
class TestConcurrencyDeclaration:
    async def test_opt_in_only(self, registry: ToolRegistry) -> None:
        await _register_echo(
            registry,
            is_concurrency_safe=lambda args: args.get("value") == "safe",
        )
        assert registry.is_concurrency_safe("echo", {"value": "safe"}) is True
        assert registry.is_concurrency_safe("echo", {"value": "no"}) is False
        assert registry.is_concurrency_safe("missing", {}) is False

    async def test_concurrency_safe_tools_lists_eligible(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(
            registry,
            is_concurrency_safe=lambda _args: True,
        )

        async def handler(_input: dict) -> dict:
            return {}

        registry.register_tool("plain", "plain", {"type": "object"}, handler)
        assert registry.concurrency_safe_tools({}) == ["echo"]


@pytest.mark.asyncio
class TestBackwardCompatibility:
    async def test_legacy_will_did_hooks_still_fire(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)
        events: list[str] = []
        registry.on_will_call_tool(lambda _ctx: _async_append(events, "will"))
        registry.on_did_call_tool(lambda _result: _async_append(events, "did"))

        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is True
        assert events == ["will", "did"]

    async def test_legacy_hooks_do_not_run_when_pre_execute_denies(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)
        events: list[str] = []
        registry.on_pre_execute(lambda _ctx: _async(PreToolDecision.DENY))
        registry.on_will_call_tool(lambda _ctx: _async_append(events, "will"))
        registry.on_did_call_tool(lambda _result: _async_append(events, "did"))

        result = await registry.call_tool("echo", {"value": "x"})
        assert result.success is False
        assert events == []


@pytest.mark.asyncio
class TestScopedRegistration:
    """dsh scope semantics: scoped layers shadow the global layer."""

    async def test_global_tools_visible_in_scope(self, registry: ToolRegistry) -> None:
        await _register_echo(registry)
        assert registry.get_tool_schema("echo", scope="agent-a") is not None
        assert "echo" in registry.tool_names_for("agent-a")

    async def test_scoped_tool_only_visible_in_its_scope(
        self,
        registry: ToolRegistry,
    ) -> None:
        async def handler(_input: dict) -> dict:
            return {"scoped": True}

        registry.register_tool(
            "secret-tool",
            "scoped tool",
            {"type": "object"},
            handler,
            scope="agent-a",
        )
        assert registry.get_tool_schema("secret-tool", scope="agent-a") is not None
        assert registry.get_tool_schema("secret-tool", scope="agent-b") is None
        assert registry.get_tool_schema("secret-tool") is None
        assert "secret-tool" not in registry.tool_names_for("agent-b")
        assert registry.get_all_tool_schemas(scope="agent-a")[0]["name"] == "secret-tool"

    async def test_scoped_tool_shadows_global_same_name(
        self,
        registry: ToolRegistry,
    ) -> None:
        async def global_handler(_input: dict) -> dict:
            return {"source": "global"}

        async def scoped_handler(_input: dict) -> dict:
            return {"source": "scoped"}

        registry.register_tool(
            "resolve-me",
            "global",
            {"type": "object"},
            global_handler,
        )
        registry.register_tool(
            "resolve-me",
            "scoped",
            {"type": "object"},
            scoped_handler,
            scope="agent-a",
        )

        global_result = await registry.call_tool("resolve-me", {})
        scoped_result = await registry.call_tool("resolve-me", {}, scope="agent-a")
        assert global_result.output == {"source": "global"}
        assert scoped_result.output == {"source": "scoped"}
        # The schema allowlist resolves to the scoped description.
        assert registry.get_tool_schema("resolve-me", scope="agent-a")["description"] == "scoped"
        assert registry.get_tool_schema("resolve-me")["description"] == "global"

    async def test_merged_visibility_global_then_scoped(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)

        async def handler(_input: dict) -> dict:
            return {}

        registry.register_tool("extra", "extra", {"type": "object"}, handler, scope="agent-a")
        names = registry.tool_names_for("agent-a")
        assert names == ["echo", "extra"]

    async def test_dispose_scope_removes_layer_keeps_global(
        self,
        registry: ToolRegistry,
    ) -> None:
        await _register_echo(registry)

        async def handler(_input: dict) -> dict:
            return {}

        registry.register_tool("temp", "temp", {"type": "object"}, handler, scope="agent-a")
        assert registry.get_tool_schema("temp", scope="agent-a") is not None

        registry.dispose_scope("agent-a")
        assert registry.get_tool_schema("temp", scope="agent-a") is None
        assert registry.get_tool_schema("echo", scope="agent-a") is not None

    async def test_duplicate_name_in_same_scope_rejected(
        self,
        registry: ToolRegistry,
    ) -> None:
        async def handler(_input: dict) -> dict:
            return {}

        registry.register_tool("dup", "dup", {"type": "object"}, handler, scope="agent-a")
        with pytest.raises(ValueError, match="already registered in scope"):
            registry.register_tool(
                "dup",
                "dup",
                {"type": "object"},
                handler,
                scope="agent-a",
            )
        # Same name globally is fine — shadowing is the point.
        registry.register_tool("dup", "global-dup", {"type": "object"}, handler)


async def _async(value: object) -> object:
    return value


async def _async_append(target: list[str], value: str) -> None:
    target.append(value)


def _chain_append(
    target: list[str],
    label: str,
    decision: PreToolDecision,
) -> PreToolDecision:
    target.append(label)
    return decision

