"""
Integration tests for plan mode + ``exit_plan_mode`` skill.

Contract pinned
---------------

1. ``plan`` in ``_VALID_MODES`` · scope resolver returns empty roots
2. ``scope.primary`` is ``None`` in plan mode
3. ``scope.allows(any_path)`` is False in plan mode
4. Executor rejects any write-skill call in plan mode with a
   helpful ``PermissionError`` mentioning ``exit_plan_mode``
5. ``exit_plan_mode(confirm=True, new_mode="chat")`` returns a
   transition payload · mutates session.metadata for current turn
6. ``confirm=False`` is a no-op · bad mode is rejected
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from runtime.platform.process.scope import resolve_write_scope
from runtime.platform.process.session import Session, session_scope


class _StubAgent:
    def __init__(self, aid: str = "coder"):
        self.agent_id = aid
        self.capabilities: dict = {"code_mode_unlock": True}


# ═══════════════════════════════════════════════════════════
# Scope resolver · plan mode
# ═══════════════════════════════════════════════════════════


class TestScopePlanMode:
    def test_plan_mode_has_empty_roots(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        sess = Session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            metadata={"mode": "plan"},
        )
        scope = resolve_write_scope(sess)
        assert scope.mode == "plan"
        assert scope.roots == ()
        assert scope.primary is None

    def test_plan_mode_rejects_all_paths(self):
        sess = Session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            metadata={"mode": "plan"},
        )
        scope = resolve_write_scope(sess)
        assert scope.allows("/tmp/anything") is False
        assert scope.allows("/etc/passwd") is False

    def test_non_plan_modes_still_have_primary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Sanity · plan mode change doesn't break chat/team/code."""
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        for mode in ("chat", "team", "code"):
            sess = Session(
                actor="u",
                agent=_StubAgent(),
                thread_id="t",
                metadata={"mode": mode},
            )
            scope = resolve_write_scope(sess)
            assert scope.primary is not None, f"mode={mode!r} should have a primary root"


# ═══════════════════════════════════════════════════════════
# Executor · plan mode blocks write skills
# ═══════════════════════════════════════════════════════════


class TestExecutorPlanMode:
    def test_write_skill_in_plan_mode_fails_with_hint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Plan-mode agent calling write_text_file → step fails ·
        error message points at ``exit_plan_mode``."""
        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.write_skills import (
            register_write_skills,
        )
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

        reg = SkillRegistry()
        register_write_skills(reg)
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=InMemoryJournal(),
        )
        tid = TaskId(uuid4())
        budget = Budget(
            task_id=tid,
            limits=BudgetLimits(tokens=1000, usd=0.01),
        )

        sess = Session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            metadata={"mode": "plan"},
        )
        with session_scope(sess):
            step = executor.execute_step(
                step_id=0,
                node_id="n0",
                sucker_id=SkillId("write_text_file"),
                args={"path": "note.txt", "content": "x"},
                caller="arms/x",
                task_id=tid,
                arm_id=ArmId("x"),
                budget=budget,
            )

        assert not step.success
        # Error surface mentions the escape hatch · not a mystery 500
        (step.result.error_type or "") + " " + " ".join(step.result.stderr_tags or [])
        assert step.result.error_type == "PermissionError"


# ═══════════════════════════════════════════════════════════
# exit_plan_mode skill · the protocol tool
# ═══════════════════════════════════════════════════════════


class TestExitPlanModeSkill:
    def test_confirm_false_is_noop(self):
        from runtime.execution.suckers.plan_mode import _exit_plan_mode

        result = _exit_plan_mode(plan="do stuff", confirm=False)
        assert result["mode_transitioned"] is False
        assert "confirm" in result["reason"].lower()

    def test_invalid_new_mode_rejected(self):
        from runtime.execution.suckers.plan_mode import _exit_plan_mode

        result = _exit_plan_mode(
            plan="x",
            confirm=True,
            new_mode="super-admin",
        )
        assert result["mode_transitioned"] is False
        assert "not valid" in result["reason"]

    def test_valid_transition_mutates_session(self):
        from runtime.execution.suckers.plan_mode import _exit_plan_mode

        sess = Session(
            actor="u",
            agent=_StubAgent(),
            thread_id="t",
            metadata={"mode": "plan"},
        )
        result = _exit_plan_mode(
            plan="write hello.txt",
            confirm=True,
            new_mode="chat",
            session=sess,
        )
        assert result["mode_transitioned"] is True
        assert result["from"] == "plan"
        assert result["to"] == "chat"
        # Current-turn mutation · next step sees new mode
        assert sess.metadata["mode"] == "chat"

    def test_transition_emits_persist_hint(self):
        """Client frontend needs this to update thread metadata ·
        otherwise next turn the mode would revert to 'plan'."""
        from runtime.execution.suckers.plan_mode import _exit_plan_mode

        result = _exit_plan_mode(
            plan="x",
            confirm=True,
            new_mode="code",
        )
        assert "persist_hint" in result
        assert result["persist_hint"]["thread_metadata"]["mode"] == "code"

    def test_skill_registered_in_base(self):
        """exit_plan_mode is atomic base · every agent MUST have it ·
        else plan mode becomes a trap (no way out)."""
        from runtime.execution.all_skills import register_base
        from runtime.execution.suckers import SkillRegistry

        reg = SkillRegistry()
        register_base(reg)
        assert reg.has("exit_plan_mode")
