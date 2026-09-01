"""
Tests for the subagent bridge. Subagents are not skills; the old
``runtime.execution.suckers.sub_agent`` module is only a compatibility shim.

Contract pinned
---------------

1. No runner configured → success=False with clear error
2. Missing agent_id → success=False
3. Missing prompt → success=False
4. Happy path · runner installed · returns its output verbatim
5. Runner exception caught · bridge still returns structured result
6. Context merging · caller passes extra kv + timeout + session thread_id
7. Subagent dispatch is not registered by the skill catalog.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_runner():
    from runtime.execution.subagents import set_sub_agent_runner

    set_sub_agent_runner(None)
    yield
    set_sub_agent_runner(None)


class TestGuards:
    def test_no_runner_configured(self):
        from runtime.execution.subagents import call_subagent

        r = call_subagent(agent_id="coder", prompt="do x")
        assert r["success"] is False
        assert "runner" in r["error"].lower()

    def test_missing_agent_id(self):
        from runtime.execution.subagents import call_subagent

        r = call_subagent(agent_id="", prompt="x")
        assert r["success"] is False
        assert "agent_id" in r["error"]

    def test_missing_prompt(self):
        from runtime.execution.subagents import call_subagent

        r = call_subagent(agent_id="coder", prompt="")
        assert r["success"] is False


class TestHappyPath:
    def test_runner_output_returned(self):
        from runtime.execution.subagents import (
            call_subagent,
            set_sub_agent_runner,
        )

        def _stub(description, *, subagent_name, context):
            return f"[{subagent_name}] {description}"

        set_sub_agent_runner(_stub)
        # Use a non-ephemeral agent_id — names like "planner" / "researcher"
        # are now built-in ephemeral roles (see
        # runtime.execution.suckers.ephemeral_agents.BUILTIN_ROLES) and
        # would dispatch through the ephemeral runner before reaching
        # the user-installed _RUNNER. ``custom_agent`` isn't in the
        # ephemeral set so it falls through to ``_RUNNER`` as intended.
        r = call_subagent(agent_id="custom_agent", prompt="draft plan")
        assert r["success"] is True
        assert r["agent_id"] == "custom_agent"
        assert r["output"] == "[custom_agent] draft plan"
        assert r["error"] is None

    def test_context_merging(self):
        from runtime.execution.subagents import (
            call_subagent,
            set_sub_agent_runner,
        )

        captured: dict = {}

        def _stub(description, *, subagent_name, context):
            captured.update(context or {})
            return "ok"

        set_sub_agent_runner(_stub)
        call_subagent(
            agent_id="a",
            prompt="p",
            context={"key": "v"},
            timeout_s=42,
        )
        assert captured["key"] == "v"
        assert captured["timeout_s"] == 42

    def test_session_thread_id_forwarded(self):
        from runtime.execution.subagents import (
            call_subagent,
            set_sub_agent_runner,
        )

        class _FakeSess:
            thread_id = "t-parent"

        captured: dict = {}

        def _stub(description, *, subagent_name, context):
            captured.update(context or {})
            return "ok"

        set_sub_agent_runner(_stub)
        call_subagent(agent_id="a", prompt="p", session=_FakeSess())
        assert captured["caller_thread_id"] == "t-parent"


class TestErrorHandling:
    def test_runner_exception_caught(self):
        from runtime.execution.subagents import (
            call_subagent,
            set_sub_agent_runner,
        )

        def _boom(description, *, subagent_name, context):
            raise RuntimeError("sub-agent exploded")

        set_sub_agent_runner(_boom)
        r = call_subagent(agent_id="a", prompt="p")
        assert r["success"] is False
        assert "RuntimeError" in r["error"]
        assert "exploded" in r["error"]


class TestRegistration:
    def test_registered_via_register_all(self):
        from runtime.execution.all_skills import register_all
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_all(reg)
        assert reg.has("call_agent")

    def test_not_registered_via_register_base(self):
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        assert not reg.has("call_agent")

    def test_is_not_in_atomic_base(self):
        """``call_agent`` is not an atomic skill because it is not a skill."""
        from runtime.execution.suckers.layers import ATOMIC_SKILL_NAMES

        assert "call_agent" not in ATOMIC_SKILL_NAMES

    def test_catalog_metadata(self):
        from runtime.execution.all_skills import (
            ALL_SKILL_IDS,
            skill_group,
        )

        assert "call_agent" in ALL_SKILL_IDS
        assert skill_group("call_agent") == "delegation"
