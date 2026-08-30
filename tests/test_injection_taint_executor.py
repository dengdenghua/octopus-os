"""Integration tests for injection_taint_block rejection path in ToolExecutor.

Spec source: prior deep-analysis identified this as a P0 test blind
spot. ``tests/test_prompt_injection.py`` covers the taint state machine
(mark_injection_taint / injection_taint_gates), but the executor's
end-to-end rejection path — where a tainted turn + risky tool without
approval returns ``immune_reject`` — had zero coverage. A regression
that removed the ``_inj_block = injection_taint_block(...)`` call would
not be caught by CI.

The guard sits at executor.py:755-761 and enforces:
  * tainted turn + risky tool + no approval-capable loop → block
  * tainted turn + durable-persistence write → block on every path
  * tainted turn + low-risk tool → allow
  * clean turn + any tool → allow
  * tainted turn + risky tool + gate_already_handled → allow (defer)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine
from runtime.safety.validation.prompt_injection import (
    injection_gate_already_handled,
    injection_taint_gates,
    mark_injection_taint,
    reset_injection_taint,
    set_injection_gate_handled,
)

# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def immunity() -> TrustEngine:
    return TrustEngine(trusted_sources=["skill://public/*"])


@pytest.fixture
def journal() -> InMemoryJournal:
    return InMemoryJournal()


@pytest.fixture
def budget() -> Budget:
    return Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=10_000, usd=1.0))


@pytest.fixture(autouse=True)
def clean_taint():
    """Reset taint state before AND after each test (it's a ContextVar)."""
    reset_injection_taint()
    set_injection_gate_handled(False)
    yield
    reset_injection_taint()
    set_injection_gate_handled(False)


def _make_executor(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    *,
    include_risky: bool = True,
    include_benign: bool = True,
    include_persistence: bool = True,
) -> ToolExecutor:
    """Build an executor with the skills needed for taint-block tests."""
    registry = SkillRegistry()

    if include_risky:
        # exec_shell is assessed as medium+ risk by assess_approval_risk
        registry.register(
            Skill(
                name="exec_shell",
                description="run a shell command",
                affinity=["shell", "exec"],
                trusted_source="skill://public/exec_shell",
                handler=lambda command="", **_kw: {"exit_code": 0, "stdout": ""},
            )
        )
    if include_benign:
        # echo is a no-op read-like demo tool, assessed low-risk
        registry.register(
            Skill(
                name="echo",
                description="returns its argument",
                affinity=["demo"],
                trusted_source="skill://public/echo",
                handler=lambda msg="", **_kw: msg,
            )
        )
    if include_persistence:
        # `remember` is one of the durable-persistence write tools
        # (_DURABLE_PERSISTENCE_WRITES = {"remember", "update_soul",
        # "note_user"}) — blocked on every path while tainted.
        registry.register(
            Skill(
                name="remember",
                description="persist to agent memory",
                affinity=["memory", "write", "persist"],
                trusted_source="skill://public/remember",
                handler=lambda **_kw: {"saved": True},
            )
        )

    return ToolExecutor(registry=registry, immunity=immunity, journal=journal)


# ── Tests: tainted turn + risky tool + no approval → blocked ───────


def test_tainted_turn_blocks_risky_tool_without_approval(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """High taint + risky tool (exec_shell) + no gate_already_handled → immune_reject."""
    mark_injection_taint("high")
    assert injection_taint_gates()

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("exec_shell"),
            args={"command": "echo hi"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert not step.success
    assert step.result.status == "immune_reject"
    assert step.result.error_type == "immune_reject"
    assert "injection_taint_block" in (
        step.result.stderr_tags[-1] if step.result.stderr_tags else ""
    )


# ── Tests: tainted turn + low-risk tool → allowed ─────────────────


def test_tainted_turn_allows_low_risk_tool(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """High taint + low-risk tool (echo) → allowed (gate only blocks medium+)."""
    mark_injection_taint("high")

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("echo"),
            args={"msg": "hello"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success
    assert step.result.output == "hello"


# ── Tests: clean turn + risky tool → allowed ──────────────────────


def test_clean_turn_allows_risky_tool(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """No taint + risky tool (exec_shell) → allowed (gate only blocks when tainted)."""
    assert not injection_taint_gates()

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("exec_shell"),
            args={"command": "echo hi"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success


# ── Tests: tainted turn + risky tool + gate_already_handled → allowed ─


def test_tainted_turn_allows_risky_tool_when_gate_handled(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """High taint + risky tool + gate_already_handled=True → allowed (defer).

    A loop that runs its own approval round-trip marks the call as
    "handled" so the executor's fail-closed taint block lets it
    through. This is the contract: approval-capable paths bypass the
    executor block; non-approval paths (parallel dispatch, subagents)
    do not.
    """
    mark_injection_taint("high")
    set_injection_gate_handled(True)
    assert injection_gate_already_handled()

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("exec_shell"),
            args={"command": "echo hi"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success


# ── Tests: durable persistence write blocked on EVERY path ────────


def test_tainted_turn_blocks_durable_persistence_even_when_gate_handled(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """Durable-persistence writes are blocked on EVERY path while tainted.

    Even when ``gate_already_handled=True``, a durable-persistence
    write (memory/soul/user profile) is blocked because the single-action
    approval gate won't have escalated a low-risk write, so deferring
    to it would let the poison through. This is the cross-turn
    laundering protection.
    """
    mark_injection_taint("high")
    set_injection_gate_handled(True)  # Even with gate handled!

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("remember"),
            args={"content": "remember this"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert not step.success
    assert step.result.status == "immune_reject"
    assert step.result.error_type == "immune_reject"
    assert "injection_taint_block" in (
        step.result.stderr_tags[-1] if step.result.stderr_tags else ""
    )


def test_clean_turn_allows_durable_persistence(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """No taint → durable-persistence write allowed (gate only blocks when tainted)."""
    assert not injection_taint_gates()

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("remember"),
            args={"content": "remember this"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success


# ── Tests: low taint (below threshold) → allowed ──────────────────


def test_low_taint_does_not_gate(
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """Low taint (below medium threshold) does not trigger the gate.

    ``injection_taint_gates`` defaults to threshold="medium"; a "low"
    taint is below threshold, so the executor block returns None
    immediately and the risky tool is allowed.
    """
    mark_injection_taint("low")
    assert not injection_taint_gates(), "low taint should be below the medium threshold"

    executor = _make_executor(immunity, journal)
    session = Session(metadata={"mode": "code"})

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("exec_shell"),
            args={"command": "echo hi"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success

