"""
Organic NotificationEvent dispatches.

Contract pinned
---------------

1. ``Budget.check_warn_crossing`` fires 80 / 95 once each
2. Crossing 80 then 95 in sequence yields both events
3. Executor dispatches ``budget_warn`` on crossing
4. Executor dispatches ``budget_squirt`` on InsufficientBudget
5. AnthropicModelRouter dispatches ``provider_down`` on call exception
   (and still re-raises · doesn't swallow)
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def _clean_hooks():
    from runtime.safety.hooks.registry import get_global_registry

    get_global_registry().clear()
    yield
    get_global_registry().clear()


# ═══════════════════════════════════════════════════════════
# Budget level-crossing primitive
# ═══════════════════════════════════════════════════════════


class TestBudgetWarnCrossing:
    def test_fresh_budget_no_crossings(self):
        from runtime.platform.models import Budget, BudgetLimits, TaskId

        b = Budget(
            task_id=TaskId(uuid4()),
            limits=BudgetLimits(tokens=1000, usd=1.0),
        )
        assert b.check_warn_crossing() == []

    def test_crossings_fire_once_each(self):
        from runtime.platform.models import (
            Budget,
            BudgetLimits,
            CostEntry,
            TaskId,
        )

        b = Budget(
            task_id=TaskId(uuid4()),
            limits=BudgetLimits(tokens=1000, usd=1.0),
        )
        rid = b.reserve(CostEntry(tokens_in=400, tokens_out=400, usd=0.4))
        b.commit(rid, CostEntry(tokens_in=400, tokens_out=400, usd=0.4))
        # Now at 80% tokens · should fire 80 once
        first = b.check_warn_crossing()
        assert 80 in first
        # Second call · no new crossing
        assert b.check_warn_crossing() == []

    def test_both_thresholds_in_one_jump(self):
        from runtime.platform.models import (
            Budget,
            BudgetLimits,
            CostEntry,
            TaskId,
        )

        b = Budget(
            task_id=TaskId(uuid4()),
            limits=BudgetLimits(tokens=1000, usd=1.0),
        )
        rid = b.reserve(CostEntry(tokens_in=500, tokens_out=500, usd=0.98))
        b.commit(rid, CostEntry(tokens_in=500, tokens_out=500, usd=0.98))
        crossings = b.check_warn_crossing()
        assert sorted(crossings) == [80, 95]


# ═══════════════════════════════════════════════════════════
# Executor integration · budget_warn
# ═══════════════════════════════════════════════════════════


def _executor_plus_budget(usd_limit: float = 0.01):
    from runtime.execution.suckers import Skill, SkillRegistry
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import InMemoryJournal
    from runtime.platform.models import Budget, BudgetLimits, TaskId
    from runtime.safety.auth import TrustEngine

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="noop",
            description="no-op",
            affinity=["test"],
            cost_profile="low",
            trusted_source="skill://public/noop",
            handler=lambda: "ok",
        )
    )
    executor = ToolExecutor(
        registry=reg,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=InMemoryJournal(),
    )
    tid = TaskId(uuid4())
    budget = Budget(
        task_id=tid,
        limits=BudgetLimits(tokens=1000, usd=usd_limit),
    )
    return executor, budget, tid


class TestExecutorBudgetWarn:
    def test_budget_warn_dispatched_on_crossing(self):
        from runtime.platform.models import ArmId, CostEntry, SkillId
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "budget_warn":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        executor, budget, tid = _executor_plus_budget(usd_limit=0.01)

        # Consume ~85% via predicted_cost override (default is
        # tokens_in=100/out=100/usd=0.001 · way under limit).
        executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("noop"),
            args={},
            caller="t",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=budget,
            predicted_cost=CostEntry(
                tokens_in=400,
                tokens_out=400,
                usd=0.0085,
            ),
        )

        warn_levels = [c["level_pct"] for c in captured]
        assert 80 in warn_levels
        # details carry context
        assert captured[0]["task_id"] == str(tid)

    def test_budget_squirt_dispatched_on_insufficient(self):
        from runtime.platform.models import ArmId, CostEntry, SkillId
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "budget_squirt":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        executor, budget, tid = _executor_plus_budget(usd_limit=0.001)

        # Predict cost above limit · triggers InsufficientBudget
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("noop"),
            args={},
            caller="t",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=budget,
            predicted_cost=CostEntry(
                tokens_in=99999,
                tokens_out=99999,
                usd=99.0,
            ),
        )
        assert step.result.status == "circuit_broken"
        assert len(captured) == 1
        assert "reason" in captured[0]


# ═══════════════════════════════════════════════════════════
# Provider-down dispatch from anthropic_router
# ═══════════════════════════════════════════════════════════


class _BrokenAnthropicClient:
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("upstream 503")

    def __init__(self):
        self.messages = self._Messages()


class _RateLimitClient:
    class _Messages:
        def create(self, **kwargs):
            class RateLimitError(RuntimeError):
                pass

            raise RateLimitError("429 Too Many Requests")

    def __init__(self):
        self.messages = self._Messages()


class TestProviderDown:
    def test_anthropic_rate_limit_uses_rate_limit_kind(self):
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )
        from runtime.sensing.model_router import Message, ModelRequest
        from runtime.sensing.model_router.anthropic_router import (
            AnthropicModelRouter,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            captured.append({"kind": event.kind, **dict(event.details)})
            return HookDecision.pass_through()

        router = AnthropicModelRouter(client=_RateLimitClient())
        req = ModelRequest(
            model="m",
            messages=[Message(role="user", content="x")],
        )
        with pytest.raises(RuntimeError):
            router.call(req)
        assert any(c["kind"] == "rate_limit" for c in captured)

    def test_anthropic_call_dispatches_and_reraises(self):
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )
        from runtime.sensing.model_router import Message, ModelRequest
        from runtime.sensing.model_router.anthropic_router import (
            AnthropicModelRouter,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "provider_down":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        router = AnthropicModelRouter(client=_BrokenAnthropicClient())
        req = ModelRequest(
            model="claude-haiku-4-5",
            messages=[Message(role="user", content="hi")],
        )

        with pytest.raises(RuntimeError, match="upstream 503"):
            router.call(req)

        assert len(captured) == 1
        assert captured[0]["provider"] == "anthropic"
        assert captured[0]["error_type"] == "RuntimeError"


class TestImmuneReject:
    def test_dispatched_when_immune_blocks(self):
        from uuid import uuid4

        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.execution.tool_engine import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import (
            ArmId,
            Budget,
            BudgetLimits,
            SkillId,
            TaskId,
        )
        from runtime.safety.auth import TrustEngine
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "immune_reject":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        reg = SkillRegistry()
        reg.register(
            Skill(
                name="hostile",
                description="x",
                affinity=["test"],
                cost_profile="low",
                # NOT in trusted_sources allow-list · TrustEngine rejects
                trusted_source="skill://untrusted/hostile",
                handler=lambda: "no",
            )
        )
        # Allowlist excludes "untrusted" + reject policy → reject path
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(
                trusted_sources=["skill://public/*"],
                unknown_policy="reject",
            ),
            journal=InMemoryJournal(),
        )
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("hostile"),
            args={},
            caller="t",
            task_id=tid,
            arm_id=ArmId("a"),
            budget=Budget(
                task_id=tid,
                limits=BudgetLimits(tokens=1000, usd=0.01),
            ),
        )
        # Step's immune_verdict tracks the rejection (status field)
        assert step.immune_verdict == "immune_reject"
        assert len(captured) == 1
        assert captured[0]["sucker_id"] == "hostile"


class TestPlanModeExit:
    def test_dispatched_on_transition(self):
        from runtime.execution.suckers.plan_mode import _exit_plan_mode
        from runtime.platform.process.session import Session
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )

        class _StubAgent:
            agent_id = "coder"
            capabilities = {"code_mode_unlock": True}

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "plan_mode_exit":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        sess = Session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t-xyz",
            metadata={"mode": "plan"},
        )
        result = _exit_plan_mode(
            plan="execute the deploy",
            confirm=True,
            new_mode="code",
            session=sess,
        )
        assert result["mode_transitioned"] is True
        assert len(captured) == 1
        assert captured[0]["from"] == "plan"
        assert captured[0]["to"] == "code"
        assert captured[0]["thread_id"] == "t-xyz"
        assert captured[0]["plan_preview"].startswith("execute")

    def test_no_dispatch_when_confirm_false(self):
        from runtime.execution.suckers.plan_mode import _exit_plan_mode
        from runtime.safety.hooks import (
            HookDecision,
            NotificationEvent,
            register_hook,
        )

        captured: list[dict] = []

        @register_hook(NotificationEvent)
        def _h(event):
            if event.kind == "plan_mode_exit":
                captured.append(dict(event.details))
            return HookDecision.pass_through()

        result = _exit_plan_mode(plan="x", confirm=False)
        assert result["mode_transitioned"] is False
        assert captured == []
