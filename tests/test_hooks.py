"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest

from runtime.core.nerves import (
    HookContext,
    HookError,
    HookManager,
    HookResult,
)
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    ExecutionResult,
    SkillId,
    TaskId,
)
from runtime.safety.auth import TrustEngine

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _stack(hooks=None):
    reg = SkillRegistry()
    reg.register(
        Skill(
            name="greet",
            trusted_source="skill://public/greet",
            handler=lambda name="world", **kw: {"msg": f"hello, {name}"},
        ),
        verify_tests=False,
    )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
        hooks=hooks,
    )
    return reg, journal, executor


def _run_once(executor, journal, sucker="greet", args=None):
    tid = TaskId(uuid4())
    budget = Budget(task_id=tid, limits=BudgetLimits(tokens=10_000, usd=0.10))
    return executor.execute_step(
        step_id=0,
        node_id="n0",
        sucker_id=SkillId(sucker),
        args=args or {"name": "Alice"},
        caller="arms/x",
        task_id=tid,
        arm_id=ArmId("x"),
        budget=budget,
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_no_hooks_no_change(self):
        reg, journal, executor = _stack(hooks=None)
        step = _run_once(executor, journal)
        assert step.success is True
        assert step.result.output == {"msg": "hello, Alice"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPreHookAudit:
    def test_pre_hook_sees_call(self):
        seen: list[HookContext] = []
        hm = HookManager()
        hm.add_pre("audit", lambda ctx: seen.append(ctx) or None)

        reg, journal, executor = _stack(hooks=hm)
        _run_once(executor, journal)

        assert len(seen) == 1
        ctx = seen[0]
        assert ctx.phase == "pre"
        assert ctx.sucker_id == "greet"
        assert ctx.call.args == {"name": "Alice"}
        assert ctx.result is None  # Implementation note.

    def test_pre_hook_not_replacing_lets_handler_run(self):
        hm = HookManager()
        hm.add_pre("noop", lambda ctx: None)
        reg, journal, executor = _stack(hooks=hm)
        step = _run_once(executor, journal)
        # Implementation note.
        assert step.result.output == {"msg": "hello, Alice"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPreHookReplace:
    def test_pre_hook_replaces_result(self):
        hm = HookManager()
        hm.add_pre(
            "mock_greet",
            lambda ctx: HookResult(
                replace_with=ExecutionResult(
                    call_id=ctx.call.call_id,
                    status="success",
                    output={"msg": "MOCKED"},
                )
            ),
        )

        handler_calls = 0

        def real_handler(**kw):
            nonlocal handler_calls
            handler_calls += 1
            return {"msg": "real"}

        reg = SkillRegistry()
        reg.register(
            Skill(
                name="greet",
                trusted_source="skill://public/greet",
                handler=real_handler,
            ),
            verify_tests=False,
        )

        journal = InMemoryJournal()
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=journal,
            hooks=hm,
        )
        step = _run_once(executor, journal)
        # Implementation note.
        assert handler_calls == 0
        assert step.result.output == {"msg": "MOCKED"}
        assert "pre_hook_replaced" in step.result.stderr_tags


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPreHookBlock:
    def test_hook_error_produces_rejected_step(self):
        handler_calls = 0

        def real(**kw):
            nonlocal handler_calls
            handler_calls += 1
            return {}

        reg = SkillRegistry()
        reg.register(
            Skill(
                name="greet",
                trusted_source="skill://public/greet",
                handler=real,
            ),
            verify_tests=False,
        )

        hm = HookManager()
        hm.add_pre("deny", lambda ctx: (_ for _ in ()).throw(HookError("permission denied")))

        journal = InMemoryJournal()
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=journal,
            hooks=hm,
        )
        step = _run_once(executor, journal)

        assert handler_calls == 0
        assert step.success is False
        assert step.result.status == "failed"
        assert "hook_block" in step.result.stderr_tags[-1]
        assert "permission denied" in step.result.stderr_tags[-1]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPostHookRewrite:
    def test_post_redacts_output(self):
        hm = HookManager()
        hm.add_post(
            "redact",
            lambda ctx: HookResult(replace_with=ctx.result.model_copy(update={"output": "***"})),
        )

        reg, journal, executor = _stack(hooks=hm)
        step = _run_once(executor, journal)
        # Implementation note.
        assert step.result.output == "***"

    def test_post_hook_sees_real_result(self):
        seen: list[HookContext] = []
        hm = HookManager()
        hm.add_post("observe", lambda ctx: seen.append(ctx) or None)

        reg, journal, executor = _stack(hooks=hm)
        _run_once(executor, journal)

        assert len(seen) == 1
        assert seen[0].phase == "post"
        assert seen[0].result.output == {"msg": "hello, Alice"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestHookChaining:
    def test_pre_first_replace_wins(self):
        hm = HookManager()
        hm.add_pre(
            "first",
            lambda ctx: HookResult(
                replace_with=ExecutionResult(
                    call_id=ctx.call.call_id,
                    status="success",
                    output={"msg": "FIRST"},
                )
            ),
        )
        hm.add_pre(
            "second",
            lambda ctx: HookResult(
                replace_with=ExecutionResult(
                    call_id=ctx.call.call_id,
                    status="success",
                    output={"msg": "SECOND"},
                )
            ),
        )

        reg, journal, executor = _stack(hooks=hm)
        step = _run_once(executor, journal)
        assert step.result.output == {"msg": "FIRST"}  # Implementation note.

    def test_post_chained_updates(self):
        """Implementation note."""
        hm = HookManager()
        hm.add_post(
            "add_a",
            lambda ctx: HookResult(
                replace_with=ctx.result.model_copy(
                    update={"output": {"stage": (ctx.result.output or {}).get("stage", "") + "A"}}
                )
            ),
        )
        hm.add_post(
            "add_b",
            lambda ctx: HookResult(
                replace_with=ctx.result.model_copy(
                    update={"output": {"stage": (ctx.result.output or {}).get("stage", "") + "B"}}
                )
            ),
        )

        reg, journal, executor = _stack(hooks=hm)
        step = _run_once(executor, journal)
        # Implementation note.
        assert step.result.output == {"stage": "AB"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestHookFaultTolerance:
    def test_pre_hook_exception_swallowed(self):
        hm = HookManager()

        def boom(ctx):
            raise ValueError("boom")

        hm.add_pre("bad", boom)

        reg, journal, executor = _stack(hooks=hm)
        # Implementation note.
        step = _run_once(executor, journal)
        assert step.result.output == {"msg": "hello, Alice"}

    def test_post_hook_exception_keeps_original_result(self):
        hm = HookManager()

        def bad(ctx):
            raise RuntimeError("broken")

        hm.add_post("crash", bad)

        reg, journal, executor = _stack(hooks=hm)
        step = _run_once(executor, journal)
        # Implementation note.
        assert step.result.output == {"msg": "hello, Alice"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestHookManagerLifecycle:
    def test_duplicate_name_within_phase_rejected(self):
        hm = HookManager()
        hm.add_pre("x", lambda ctx: None)
        with pytest.raises(ValueError, match="duplicate"):
            hm.add_pre("x", lambda ctx: None)

    def test_same_name_ok_across_phases(self):
        hm = HookManager()
        hm.add_pre("x", lambda ctx: None)
        hm.add_post("x", lambda ctx: None)  # Implementation note.
        assert hm.names("pre") == ["x"]
        assert hm.names("post") == ["x"]

    def test_remove_returns_true_when_found(self):
        hm = HookManager()
        hm.add_pre("x", lambda ctx: None)
        assert hm.remove("x", "pre") is True
        assert hm.remove("x", "pre") is False

    def test_remove_without_phase_removes_both(self):
        hm = HookManager()
        hm.add_pre("x", lambda ctx: None)
        hm.add_post("x", lambda ctx: None)
        hm.remove("x")  # Implementation note.
        assert hm.names() == []
