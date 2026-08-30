"""
Tests for ``runtime.safety.hooks`` · the Claude-Code-aligned
lifecycle hooks system (PreToolUse / PostToolUse / UserPromptSubmit
/ Stop / SessionStart / Notification).

Distinct from the legacy ``runtime.core.nerves.hooks`` HookManager
(see ``test_hooks.py``) · this is the community-facing dispatch
system used by ``@register_hook`` decorated handlers in
``~/.echo/hooks/*.py``.

Contract pinned
---------------

1. ``register_hook`` decorator registers on the global registry
2. PreToolUse cancel short-circuits the chain
3. PreToolUse modify_args propagates through the chain
4. PostToolUse modify_output propagates through the chain
5. Handler exception is caught · treated as pass_through
6. Executor honors PreToolUse cancel (step fails)
7. Executor honors PreToolUse modify_args (handler sees new args)
8. Executor honors PostToolUse modify_output (result.output rewritten)
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test starts with an empty global registry."""
    from runtime.safety.hooks.registry import get_global_registry

    reg = get_global_registry()
    reg.clear()
    yield
    reg.clear()


# ═══════════════════════════════════════════════════════════
# Registry / decorator primitives
# ═══════════════════════════════════════════════════════════


class TestRegistryBasics:
    def test_register_hook_decorator(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            get_global_registry,
            register_hook,
        )

        @register_hook(PreToolUseEvent)
        def _h(event):
            return HookDecision.pass_through()

        reg = get_global_registry()
        assert len(reg.handlers_for(PreToolUseEvent)) == 1

    def test_handlers_for_different_events_isolated(self):
        from runtime.safety.hooks import (
            HookDecision,
            PostToolUseEvent,
            PreToolUseEvent,
            get_global_registry,
            register_hook,
        )

        @register_hook(PreToolUseEvent)
        def _pre(event):
            return HookDecision.pass_through()

        reg = get_global_registry()
        assert len(reg.handlers_for(PreToolUseEvent)) == 1
        assert len(reg.handlers_for(PostToolUseEvent)) == 0


# ═══════════════════════════════════════════════════════════
# Dispatch chain semantics
# ═══════════════════════════════════════════════════════════


class TestDispatchChain:
    def test_pass_through_default(self):
        from runtime.safety.hooks.runner import dispatch_pre_tool

        decision = dispatch_pre_tool(sucker_id="x", args={"a": 1})
        assert decision.cancelled is False
        assert decision.modified_args is None

    def test_cancel_short_circuits(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        calls: list[str] = []

        @register_hook(PreToolUseEvent)
        def _first(event):
            calls.append("first")
            return HookDecision.cancel("nope")

        @register_hook(PreToolUseEvent)
        def _second(event):
            calls.append("second")
            return HookDecision.pass_through()

        d = dispatch_pre_tool(sucker_id="x", args={})
        assert d.cancelled is True
        assert d.reason == "nope"
        assert calls == ["first"]

    def test_modify_args_accumulates(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        def _h1(event):
            return HookDecision.modify_args({"a": 2, "b": 1})

        @register_hook(PreToolUseEvent)
        def _h2(event):
            return HookDecision.modify_args({"a": 3})

        d = dispatch_pre_tool(sucker_id="x", args={"a": 1})
        assert d.modified_args == {"a": 3}

    def test_handler_exception_tolerated(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        def _bad(event):
            raise ValueError("kaboom")

        @register_hook(PreToolUseEvent)
        def _good(event):
            return HookDecision.modify_args({"a": 99})

        d = dispatch_pre_tool(sucker_id="x", args={"a": 1})
        assert d.modified_args == {"a": 99}

    def test_none_return_is_pass_through(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        def _none(event):
            return None

        @register_hook(PreToolUseEvent)
        def _mod(event):
            return HookDecision.modify_args({"a": 7})

        d = dispatch_pre_tool(sucker_id="x", args={})
        assert d.modified_args == {"a": 7}


class TestOtherDispatchers:
    def test_post_tool_modify_output(self):
        from runtime.safety.hooks import (
            HookDecision,
            PostToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_post_tool

        @register_hook(PostToolUseEvent)
        def _scrub(event):
            return HookDecision.modify_output("[REDACTED]")

        d = dispatch_post_tool(sucker_id="x", args={}, output="secret")
        assert d.modified_output == "[REDACTED]"

    def test_user_prompt_cancel(self):
        from runtime.safety.hooks import (
            HookDecision,
            UserPromptSubmitEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_user_prompt

        @register_hook(UserPromptSubmitEvent)
        def _policy(event):
            if "forbidden" in event.prompt_text:
                return HookDecision.cancel("policy violation")
            return HookDecision.pass_through()

        d = dispatch_user_prompt(prompt_text="forbidden thing")
        assert d.cancelled is True

    def test_stop_dispatcher_tolerates_bad_handler(self):
        from runtime.safety.hooks import StopEvent, register_hook

        @register_hook(StopEvent)
        def _bad(event):
            raise RuntimeError("x")

        from runtime.safety.hooks.runner import dispatch_stop

        d = dispatch_stop(thread_id="t1", success=True, step_count=3)
        assert d.cancelled is False


# ═══════════════════════════════════════════════════════════
# Executor integration
# ═══════════════════════════════════════════════════════════


def _make_executor():
    from runtime.execution.suckers import Skill, SkillRegistry
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import InMemoryJournal
    from runtime.safety.auth import TrustEngine

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="echo",
            description="echo back the input",
            affinity=["test"],
            cost_profile="low",
            trusted_source="skill://public/echo",
            handler=lambda x="default": f"echoed:{x}",
        )
    )
    return ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=InMemoryJournal(),
    )


def _make_budget(task_id):
    from runtime.platform.models import Budget, BudgetLimits

    return Budget(task_id=task_id, limits=BudgetLimits(tokens=1000, usd=0.01))


class TestExecutorIntegration:
    def test_pre_tool_cancel_fails_step(self):
        from runtime.platform.models import ArmId, SkillId, TaskId
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )

        @register_hook(PreToolUseEvent)
        def _deny(event):
            if event.sucker_id == "echo":
                return HookDecision.cancel("denied by policy")
            return HookDecision.pass_through()

        executor = _make_executor()
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("echo"),
            args={"x": "hi"},
            caller="test",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=_make_budget(tid),
        )
        assert step.result.status == "failed"
        tags = step.result.stderr_tags or []
        assert any("hook_cancel" in t for t in tags)

    def test_pre_tool_modify_args_propagates(self):
        from runtime.platform.models import ArmId, SkillId, TaskId
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )

        @register_hook(PreToolUseEvent)
        def _rewrite(event):
            return HookDecision.modify_args({"x": "rewritten"})

        executor = _make_executor()
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("echo"),
            args={"x": "original"},
            caller="test",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=_make_budget(tid),
        )
        assert step.result.status == "success"
        assert step.result.output == "echoed:rewritten"

    def test_post_tool_modify_output_rewrites_result(self):
        from runtime.platform.models import ArmId, SkillId, TaskId
        from runtime.safety.hooks import (
            HookDecision,
            PostToolUseEvent,
            register_hook,
        )

        @register_hook(PostToolUseEvent)
        def _scrub(event):
            return HookDecision.modify_output("[SCRUBBED]")

        executor = _make_executor()
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("echo"),
            args={"x": "sensitive"},
            caller="test",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=_make_budget(tid),
        )
        assert step.result.status == "success"
        assert step.result.output == "[SCRUBBED]"
        tags = step.result.stderr_tags or []
        assert "post_hook_rewrote" in tags

    def test_buggy_hook_does_not_break_executor(self):
        from runtime.platform.models import ArmId, SkillId, TaskId
        from runtime.safety.hooks import PreToolUseEvent, register_hook

        @register_hook(PreToolUseEvent)
        def _bad(event):
            raise RuntimeError("hook exploded")

        executor = _make_executor()
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("echo"),
            args={"x": "ok"},
            caller="test",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=_make_budget(tid),
        )
        assert step.result.status == "success"
        assert step.result.output == "echoed:ok"
