"""
Tests for ephemeral sub-agent roles (chat/code mode Claude-Task
equivalent).

Contract pinned
---------------

1. ``BUILTIN_ROLES`` carries ≥ 6 roles · every one has a non-empty
   system_prompt and id matches dict key
2. ``is_ephemeral_role`` returns True only for catalog entries
3. ``call_agent("reviewer", ...)`` routes to ephemeral path · not the
   registered-agent path
4. Unknown agent_id falls through to registered-agent path
5. Null runner raises → returned as structured error · no crash
6. Custom runner receives the composed EphemeralCall with role +
   user_prompt + caller_thread_id
7. ``share_context=True`` · caller conversation pulled into
   composed_system_prompt
8. ``share_context=False`` · no caller messages in composed prompt
9. ``share_memory=True`` · agent's three-tier memory pulled in
10. Tool allowlist passes through to runner via ``context``
11. The ephemeral call does NOT go through the legacy ``_RUNNER``
   even if one is set
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_runners():
    from runtime.execution.suckers import ephemeral_agents, sub_agent

    ephemeral_agents.set_ephemeral_role_runner(None)
    sub_agent.set_sub_agent_runner(None)
    yield
    ephemeral_agents.set_ephemeral_role_runner(None)
    sub_agent.set_sub_agent_runner(None)


class TestCatalog:
    def test_builtin_roles_populated(self):
        from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

        assert len(BUILTIN_ROLES) >= 6
        for role_id, role in BUILTIN_ROLES.items():
            assert role.id == role_id
            assert role.display_name
            assert role.description
            assert role.system_prompt.strip()

    def test_synthesizer_prompt_does_not_force_summary_template(self):
        from runtime.execution.suckers.ephemeral_agents import BUILTIN_ROLES

        prompt = BUILTIN_ROLES["synthesizer"].system_prompt
        assert "topology-specific instructions" in prompt
        assert "Do not invent a generic summary format" in prompt
        assert "- Executive summary" not in prompt

    def test_is_ephemeral_role_lookup(self):
        from runtime.execution.suckers.ephemeral_agents import is_ephemeral_role

        assert is_ephemeral_role("reviewer") is True
        assert is_ephemeral_role("researcher") is True
        assert is_ephemeral_role("coder") is False  # registered agent dir
        assert is_ephemeral_role("__nope__") is False
        assert is_ephemeral_role("") is False


class TestDispatch:
    def test_routes_ephemeral_role_to_new_path(self):
        from runtime.execution.suckers.ephemeral_agents import (
            set_ephemeral_role_runner,
        )
        from runtime.execution.suckers.sub_agent import (
            _call_agent,
            set_sub_agent_runner,
        )

        # Install a LEGACY runner that would be wrong to hit for
        # ephemeral role · assertion proves we don't hit it.
        legacy_called = {"hit": False}

        def legacy(prompt, *, subagent_name, context):
            legacy_called["hit"] = True
            return "LEGACY"

        set_sub_agent_runner(legacy)

        eph_called = {"hit": False}

        def ephemeral_stub(call):
            eph_called["hit"] = True
            return f"ephemeral reply for {call.role.id}"

        set_ephemeral_role_runner(ephemeral_stub)

        result = _call_agent(agent_id="reviewer", prompt="look at X")
        assert result["success"] is True
        assert result["output"].startswith("ephemeral reply for reviewer")
        assert result.get("ephemeral") is True
        assert eph_called["hit"] is True
        assert legacy_called["hit"] is False, "ephemeral role must NOT trigger the legacy runner"

    def test_routes_registered_id_to_legacy_path(self):
        """Unknown / non-catalog agent_id falls through to legacy."""
        from runtime.execution.suckers.sub_agent import (
            _call_agent,
            set_sub_agent_runner,
        )

        def legacy(prompt, *, subagent_name, context):
            return f"LEGACY hit for {subagent_name}"

        set_sub_agent_runner(legacy)

        result = _call_agent(agent_id="coder", prompt="write code")
        assert result["success"] is True
        assert "LEGACY hit for coder" in result["output"]
        assert result.get("ephemeral") is not True


class TestRunnerStub:
    def test_unknown_role_returns_error(self):
        from runtime.execution.suckers.ephemeral_agents import run_ephemeral_role

        r = run_ephemeral_role("does-not-exist", "hi")
        assert r["success"] is False
        assert "unknown ephemeral role" in r["error"]

    def test_null_runner_returns_structured_error(self):
        """Default runner raises · wrapper turns it into structured
        ``success=False`` · no crash."""
        from runtime.execution.suckers.ephemeral_agents import run_ephemeral_role

        r = run_ephemeral_role("reviewer", "hi")
        assert r["success"] is False
        assert "not configured" in r["error"]

    def test_runner_exception_caught(self):
        from runtime.execution.suckers.ephemeral_agents import (
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        def boom(call):
            raise ValueError("upstream down")

        set_ephemeral_role_runner(boom)
        r = run_ephemeral_role("reviewer", "hi")
        assert r["success"] is False
        assert "ValueError" in r["error"]
        assert "upstream down" in r["error"]


class TestContextSharing:
    class _StubAgent:
        agent_id = "coder"
        capabilities = {"code_mode_unlock": True}

    def _make_session_with_messages(self, messages):
        from runtime.platform.process.session import Session

        # Session is a slotted dataclass · no arbitrary attr
        # assignment. ``metadata`` is the intended extension surface;
        # ``_collect_caller_context`` reads ``recent_messages`` from
        # it with fallback to ``thread_store.get(thread_id)``.
        return Session(
            actor="u",
            agent=self._StubAgent(),
            thread_id="t-test",
            metadata={"recent_messages": messages},
        )

    def test_share_context_true_injects_conversation(self):
        from runtime.execution.suckers.ephemeral_agents import (
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["prompt"] = call.composed_system_prompt
            return "ok"

        set_ephemeral_role_runner(capture)

        sess = self._make_session_with_messages(
            [
                {"type": "human", "content": "fix the login bug"},
                {"type": "ai", "content": "let me investigate auth.py"},
            ]
        )
        run_ephemeral_role("reviewer", "review recent work", session=sess)

        prompt = captured["prompt"]
        assert "Caller conversation" in prompt
        assert "fix the login bug" in prompt
        assert "let me investigate auth.py" in prompt

    def test_share_context_false_excludes_conversation(self):
        # researcher has share_context=True by default in current
        # catalog · use a temporary role def with share_context=False
        # to test the off path. Patch the catalog entry.
        from dataclasses import replace

        from runtime.execution.suckers.ephemeral_agents import (
            BUILTIN_ROLES,
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        original = BUILTIN_ROLES["researcher"]
        BUILTIN_ROLES["researcher"] = replace(original, share_context=False)
        try:
            captured = {}

            def capture(call):
                captured["prompt"] = call.composed_system_prompt
                return "ok"

            set_ephemeral_role_runner(capture)
            sess = self._make_session_with_messages(
                [
                    {"type": "human", "content": "secret phrase: banana-123"},
                ]
            )
            run_ephemeral_role("researcher", "what is X", session=sess)
            assert "banana-123" not in captured["prompt"]
        finally:
            BUILTIN_ROLES["researcher"] = original

    def test_call_carries_caller_thread_id(self):
        from runtime.execution.suckers.ephemeral_agents import (
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["thread_id"] = call.caller_thread_id
            captured["agent_id"] = call.caller_agent_id
            return "ok"

        set_ephemeral_role_runner(capture)
        sess = self._make_session_with_messages([])
        run_ephemeral_role("reviewer", "x", session=sess)
        assert captured["thread_id"] == "t-test"
        assert captured["agent_id"] == "coder"

    def test_system_addendum_is_injected_into_prompt(self):
        from runtime.execution.suckers.ephemeral_agents import (
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["prompt"] = call.composed_system_prompt
            return "ok"

        set_ephemeral_role_runner(capture)
        run_ephemeral_role(
            "synthesizer",
            "merge findings",
            context={
                "system_addendum": ("Produce the complete final report, not an executive summary."),
            },
        )

        prompt = captured["prompt"]
        assert "Topology-specific instructions" in prompt
        assert "complete final report" in prompt
        assert "override the generic role template" in prompt


class TestToolAllowlist:
    def test_role_allowlist_in_call_context(self):
        from runtime.execution.suckers.ephemeral_agents import (
            BUILTIN_ROLES,
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["tools"] = call.context.get("tool_allowlist")
            return "ok"

        set_ephemeral_role_runner(capture)
        run_ephemeral_role("researcher", "topic")
        # researcher has ("fetch_url", "web_search") · list form because
        # dataclass tuple → list() copy for context
        assert captured["tools"] == list(BUILTIN_ROLES["researcher"].tool_allowlist)

    def test_dynamic_tool_grants_merge_with_role_allowlist(self):
        from runtime.execution.suckers.ephemeral_agents import (
            BUILTIN_ROLES,
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["tools"] = call.context.get("tool_allowlist")
            captured["sources"] = call.context.get("skill_policy_sources")
            captured["system"] = call.composed_system_prompt
            return "ok"

        set_ephemeral_role_runner(capture)
        run_ephemeral_role(
            "researcher",
            "topic",
            context={
                "extra_tool_allowlist": ["read_file", "bb_write"],
                "skill_pack_names": ["research"],
            },
        )

        # Both grants are already part of the researcher's current role.
        # Dynamic provenance is retained, while the effective list dedupes.
        assert captured["tools"] == list(BUILTIN_ROLES["researcher"].tool_allowlist)
        assert captured["sources"]["role"] == list(BUILTIN_ROLES["researcher"].tool_allowlist)
        assert captured["sources"]["dynamic"] == ["read_file", "bb_write"]
        assert "Granted Skills" in captured["system"]
        assert "Skill packs: research" in captured["system"]
        assert "read_file" in captured["system"]

    def test_dynamic_full_tool_grant_clears_allowlist(self):
        from runtime.execution.suckers.ephemeral_agents import (
            run_ephemeral_role,
            set_ephemeral_role_runner,
        )

        captured = {}

        def capture(call):
            captured["tools"] = call.context.get("tool_allowlist")
            return "ok"

        set_ephemeral_role_runner(capture)
        run_ephemeral_role(
            "researcher",
            "topic",
            context={"tool_allowlist_mode": "all"},
        )

        assert captured["tools"] == []
