from __future__ import annotations

import pytest

from runtime.safety.approval.approval_gate import ApprovalRiskPolicy
from runtime.safety.governance import (
    ExecutionPolicyContext,
    GovernanceOutcome,
    build_execution_instruction,
    evaluate_execution_policy,
)
from runtime.safety.validation.prompt_injection import (
    mark_injection_taint,
    reset_injection_taint,
    set_injection_gate_handled,
)


@pytest.fixture(autouse=True)
def _clean_taint():
    reset_injection_taint()
    set_injection_gate_handled(False)
    yield
    reset_injection_taint()
    set_injection_gate_handled(False)


def _instruction(tool: str, args: dict | None = None, **kwargs):
    return build_execution_instruction(
        instruction_id=f"task:arm:1:{tool}",
        tool_name=tool,
        caller="react_loop",
        args=args or {},
        **kwargs,
    )


def test_low_risk_instruction_is_allowed() -> None:
    decision = evaluate_execution_policy(_instruction("read_text_file", {"path": "README.md"}))

    assert decision.outcome is GovernanceOutcome.ALLOW
    assert decision.may_execute is True
    assert decision.instruction.target == "README.md"
    assert decision.to_dict()["schema"] == "echo.governance_decision.v1"


def test_high_risk_instruction_holds_for_interactive_approval() -> None:
    decision = evaluate_execution_policy(
        _instruction("write_text_file", {"path": "report.md"}),
        context=ExecutionPolicyContext(enforce_approval=True),
    )

    assert decision.outcome is GovernanceOutcome.HOLD
    assert decision.requires_approval is True
    assert decision.approval_action == "ask"


def test_policy_deny_is_distinct_from_approval_hold() -> None:
    context = ExecutionPolicyContext.from_metadata(
        {
            "enforce_executor_approval": True,
            "approval_risk_policy": {"critical": "deny"},
        }
    )
    decision = evaluate_execution_policy(
        _instruction("exec_shell", {"command": "rm -rf dist"}),
        context=context,
    )

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.requires_approval is False
    assert decision.approval_action == "deny"


def test_context_preserves_prebuilt_risk_policy() -> None:
    policy = ApprovalRiskPolicy(high="deny")

    context = ExecutionPolicyContext.from_metadata({"approval_risk_policy": policy})

    assert context.approval_risk_policy is policy


def test_sanitized_privilege_fields_produce_rewrite_evidence() -> None:
    decision = evaluate_execution_policy(
        _instruction(
            "echo",
            {"message": "hello"},
            rewritten_fields=("allow_private", "allow_sensitive"),
        )
    )

    assert decision.outcome is GovernanceOutcome.REWRITE
    assert decision.may_execute is True
    assert decision.instruction.rewritten_fields == ("allow_private", "allow_sensitive")


def test_capability_denial_precedes_taint_gate() -> None:
    mark_injection_taint("high")
    decision = evaluate_execution_policy(
        _instruction("exec_shell", {"command": "echo ok"}),
        capability=(False, "shell capability disabled"),
    )

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.gate == "capability"
    assert decision.reason == "shell capability disabled"


def test_tainted_persistence_is_denied() -> None:
    mark_injection_taint("high")
    decision = evaluate_execution_policy(_instruction("remember", {"content": "keep this"}))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.gate == "injection_taint"
    assert decision.instruction.taint == "high"


def test_instruction_preview_redacts_secret_fields_and_url_query() -> None:
    instruction = _instruction(
        "http_post",
        {
            "url": "https://example.com/api?token=visible-in-query",
            "api_key": "not-a-real-key-but-still-private",
            "payload": {"password": "private-value"},
        },
    )

    assert "not-a-real-key" not in instruction.args_preview
    assert "private-value" not in instruction.args_preview
    assert instruction.target == "https://example.com/api"

