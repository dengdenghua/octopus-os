"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.adapters.mcp_client import (
    STDIO_AVAILABLE,
    MCPClientError,
    MCPServerConfig,
    MCPTool,
    MockMCPClient,
    StdioMCPClient,
    register_mcp_tools_as_skills,
)
from runtime.execution.suckers import SkillRegistry

# ═══════════════════════════════════════════════════════════
# MCPServerConfig
# ═══════════════════════════════════════════════════════════


class TestServerConfig:
    def test_basic_construction(self):
        cfg = MCPServerConfig(
            name="fs",
            command="npx",
            args=["@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        assert cfg.name == "fs"
        assert cfg.trusted_source_uri == "mcp://fs/*"

    def test_requires_non_empty_name(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MCPServerConfig(name="", command="npx")


# ═══════════════════════════════════════════════════════════
# MockMCPClient
# ═══════════════════════════════════════════════════════════


class TestMockClient:
    def test_list_tools_adds_server_name(self):
        client = MockMCPClient(
            server_name="my-server",
            tools=[MCPTool(name="greet", description="greet x")],
        )
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "greet"
        assert tools[0].server_name == "my-server"

    def test_call_tool_success(self):
        client = MockMCPClient(
            tools=[MCPTool(name="echo")],
            tool_handlers={"echo": lambda args: f"you said: {args.get('msg')}"},
        )
        r = client.call_tool("echo", {"msg": "hi"})
        assert r.success
        assert r.output == "you said: hi"
        assert r.latency_ms >= 0

    def test_unknown_tool_returns_error(self):
        client = MockMCPClient(tools=[], tool_handlers={})
        r = client.call_tool("nope", {})
        assert not r.success
        assert "unknown_tool" in (r.error or "")

    def test_handler_exception_captured(self):
        def boom(args):
            raise RuntimeError("fail")

        client = MockMCPClient(
            tools=[MCPTool(name="boom")],
            tool_handlers={"boom": boom},
        )
        r = client.call_tool("boom", {})
        assert not r.success
        assert "RuntimeError" in (r.error or "")

    def test_call_log_tracks_invocations(self):
        client = MockMCPClient(
            tools=[MCPTool(name="a"), MCPTool(name="b")],
            tool_handlers={"a": "A", "b": "B"},
        )
        client.call_tool("a", {"x": 1})
        client.call_tool("b", {"y": 2})
        assert len(client.call_log) == 2
        assert client.call_log[0] == ("a", {"x": 1})

    def test_close_blocks_further_calls(self):
        client = MockMCPClient(tools=[MCPTool(name="x")], tool_handlers={"x": "y"})
        client.close()
        with pytest.raises(MCPClientError):
            client.list_tools()

    def test_context_manager(self):
        with MockMCPClient(tools=[MCPTool(name="x")]) as client:
            assert client.list_tools()
        # after exit client is closed
        with pytest.raises(MCPClientError):
            client.list_tools()


# ═══════════════════════════════════════════════════════════
# StdioMCPClient
# ═══════════════════════════════════════════════════════════


class TestStdioClient:
    def test_errors_if_sdk_missing(self, monkeypatch):
        """Implementation note."""
        from runtime.adapters.mcp_client import client as client_mod

        monkeypatch.setattr(client_mod, "STDIO_AVAILABLE", False)
        with pytest.raises(MCPClientError, match="mcp SDK not installed"):
            StdioMCPClient(MCPServerConfig(name="x", command="npx"))

    @pytest.mark.skipif(not STDIO_AVAILABLE, reason="mcp SDK required")
    def test_constructs_when_sdk_available(self):
        """Implementation note."""
        client = StdioMCPClient(MCPServerConfig(name="x", command="nonexistent-cmd"))
        assert not client._closed
        client.close()
        assert client._closed

    @pytest.mark.skipif(not STDIO_AVAILABLE, reason="mcp SDK required")
    def test_call_tool_catches_spawn_failure(self):
        """Implementation note."""
        client = StdioMCPClient(
            MCPServerConfig(
                name="x",
                command="cmd_definitely_does_not_exist_12345",
                timeout_ms=3000,
            )
        )
        result = client.call_tool("any_tool", {})
        assert not result.success
        assert result.error is not None
        assert result.latency_ms >= 0
        client.close()


# ═══════════════════════════════════════════════════════════
# Registry bridge
# ═══════════════════════════════════════════════════════════


class TestRegistryBridge:
    def test_registers_all_tools(self):
        client = MockMCPClient(
            server_name="fs",
            tools=[
                MCPTool(name="read"),
                MCPTool(name="list"),
            ],
            tool_handlers={"read": "read-output", "list": ["a", "b"]},
        )
        registry = SkillRegistry()
        registered = register_mcp_tools_as_skills(
            registry, client, require_trust=False, name_prefix=""
        )
        assert set(registered) == {"read", "list"}
        assert registry.has("read")
        assert registry.has("list")

    def test_with_name_prefix(self):
        client = MockMCPClient(
            server_name="fs",
            tools=[MCPTool(name="read")],
            tool_handlers={"read": "ok"},
        )
        registry = SkillRegistry()
        registered = register_mcp_tools_as_skills(
            registry, client, name_prefix="mcp_fs_", require_trust=False
        )
        assert "mcp_fs_read" in registered
        assert registry.has("mcp_fs_read")

    def test_skill_handler_routes_to_mcp(self):
        client = MockMCPClient(
            server_name="srv",
            tools=[MCPTool(name="greet")],
            tool_handlers={"greet": lambda args: f"hello {args.get('who', 'world')}"},
        )
        registry = SkillRegistry()
        register_mcp_tools_as_skills(registry, client, require_trust=False, name_prefix="")
        skill = registry.get("greet")
        result = skill.handler(who="alice")
        assert result["output"] == "hello alice"
        assert result["tool"] == "greet"
        assert "latency_ms" in result

    def test_skill_failure_wraps_as_error(self):
        client = MockMCPClient(
            tools=[MCPTool(name="boom")],
            tool_handlers={"boom": lambda args: (_ for _ in ()).throw(RuntimeError("x"))},
        )
        registry = SkillRegistry()
        register_mcp_tools_as_skills(registry, client, require_trust=False, name_prefix="")
        skill = registry.get("boom")
        result = skill.handler()
        assert "error" in result
        assert result["tool"] == "boom"

    def test_trusted_source_set_correctly(self):
        client = MockMCPClient(
            server_name="anthropic-fs",
            tools=[MCPTool(name="read")],
            tool_handlers={"read": ""},
        )
        registry = SkillRegistry()
        register_mcp_tools_as_skills(registry, client, require_trust=False, name_prefix="")
        skill = registry.get("read")
        assert skill.trusted_source == "mcp://anthropic-fs/read"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestThroughToolExecutor:
    def test_mcp_skill_runs_via_executor(self):
        from uuid import uuid4

        from runtime.execution.tool_engine import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
        from runtime.safety.auth import TrustEngine

        client = MockMCPClient(
            server_name="demo",
            tools=[MCPTool(name="mcp_echo", description="echo")],
            tool_handlers={"mcp_echo": lambda args: {"echoed": args.get("msg")}},
        )
        registry = SkillRegistry()
        register_mcp_tools_as_skills(registry, client, require_trust=False, name_prefix="")

        immunity = TrustEngine(trusted_sources=["mcp://demo/*"])
        journal = InMemoryJournal()
        executor = ToolExecutor(registry=registry, immunity=immunity, journal=journal)

        task_id = TaskId(uuid4())
        budget = Budget(task_id=task_id, limits=BudgetLimits(tokens=1000, usd=0.10))

        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("mcp_echo"),
            args={"msg": "hello"},
            caller="arms/code_arm",
            task_id=task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        assert step.success
        assert step.immune_verdict == "allow"  # trusted source matched
        # journal has the whole chain
        assert len(journal.read_by_type("step")) == 1
