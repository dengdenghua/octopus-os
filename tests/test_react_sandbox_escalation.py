"""Tests for the sandbox-blocked escalation path.

When a tool that ran inside the sandbox comes back as a ``sandbox_violation``
execution error, the react loop offers the human an approval to re-run the same
command with a relaxed sandbox (network allowed). These tests cover the two
pure pieces of that path: the violation detector + eligibility helper, and the
``sandbox_override`` hook on ``_execute_action_via_beak`` (relaxed policy is
applied during the rerun and restored afterwards).
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

from runtime.core.cerebrum._react_execution_dispatch import _execute_action_via_beak
from runtime.core.cerebrum._react_execution_phase6d import (
    _approval_could_not_reach_user,
    _can_escalate_sandbox,
    _looks_like_sandbox_violation,
)
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.platform.models.pipeline import ParsedIntent
from runtime.platform.process.session import Session, session_scope
from runtime.safety.approval.approval_gate import ApprovalDecision
from runtime.safety.auth import TrustEngine


class TestSandboxViolationDetection:
    def test_detects_sandbox_violation_observation(self) -> None:
        assert (
            _looks_like_sandbox_violation("exec_failed: sandbox_violation: network denied") is True
        )
        assert _looks_like_sandbox_violation("(工具失败) ... sandbox_violation ...") is True
        assert _looks_like_sandbox_violation("command ran fine") is False
        assert _looks_like_sandbox_violation(None) is False

    def test_escalation_eligible_tools(self) -> None:
        assert _can_escalate_sandbox("exec_shell") is True
        assert _can_escalate_sandbox("git") is True
        assert _can_escalate_sandbox("npm") is True
        # Pure write tools must NOT be auto re-offered.
        assert _can_escalate_sandbox("write_file") is False
        assert _can_escalate_sandbox("str_replace") is False
        assert _can_escalate_sandbox("delete_file") is False

    def test_distinguishes_unavailable_approval_from_human_decline(self) -> None:
        assert _approval_could_not_reach_user(ApprovalDecision(approved=False, reason="timeout"))
        assert _approval_could_not_reach_user(
            ApprovalDecision(approved=False, reason="connection_lost")
        )
        assert _approval_could_not_reach_user(
            ApprovalDecision(
                approved=False,
                reason="no interactive approval UI is wired in this runtime",
            )
        )
        assert not _approval_could_not_reach_user(
            ApprovalDecision(approved=False, reason="decline")
        )
        assert not _approval_could_not_reach_user(ApprovalDecision(approved=True, reason="accept"))


def _make_stack() -> SimpleNamespace:
    captured: dict[str, Any] = {}

    def handler(**_kwargs: Any) -> str:
        from runtime.platform.process.session import current_session

        policy = (current_session().metadata or {}).get("sandbox_policy")
        captured["policy"] = policy
        return json.dumps({"policy": policy}, ensure_ascii=False)

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="exec_shell",
            description="run a command",
            trusted_source="skill://public/exec_shell",
            handler=handler,
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace()
    stack.executor = ToolExecutor(reg, TrustEngine())
    stack.captured = captured
    return stack


class TestSandboxOverride:
    def test_override_applied_during_rerun_and_restored(self) -> None:
        original = {"type": "workspaceWrite", "networkAccess": False}
        relaxed = {"type": "dangerFullAccess", "networkAccess": True}
        stack = _make_stack()
        intent = ParsedIntent(
            raw="run it",
            intent_type="task",
            normalized_goal="run it",
            user_context={},
        )
        with session_scope(Session(thread_id="t-esc", metadata={"sandbox_policy": dict(original)})):
            observation, _step = _execute_action_via_beak(
                stack,
                'exec_shell({"command": "echo hi"})',
                react_task_id=str(uuid.uuid4()),
                react_step_counter=1,
                intent=intent,
                sandbox_override=dict(relaxed),
            )
            # The override was visible to the handler during execution.
            assert stack.captured["policy"] == relaxed
            # The observation carries the successful rerun output.
            assert "dangerFullAccess" in (observation or "")
            # The session policy is restored to the original after the rerun.
            from runtime.platform.process.session import current_session

            assert current_session().metadata["sandbox_policy"] == original

    def test_no_override_leaves_policy_untouched(self) -> None:
        original = {"type": "workspaceWrite", "networkAccess": False}
        stack = _make_stack()
        intent = ParsedIntent(
            raw="run it",
            intent_type="task",
            normalized_goal="run it",
            user_context={},
        )
        with session_scope(
            Session(thread_id="t-plain", metadata={"sandbox_policy": dict(original)})
        ):
            _execute_action_via_beak(
                stack,
                'exec_shell({"command": "echo hi"})',
                react_task_id=str(uuid.uuid4()),
                react_step_counter=1,
                intent=intent,
            )
            from runtime.platform.process.session import current_session

            assert stack.captured["policy"] == original
            assert current_session().metadata["sandbox_policy"] == original

