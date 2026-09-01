from __future__ import annotations

from pathlib import Path

from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalProvider,
    ApprovalRequest,
    ApprovalRule,
)
from runtime.safety.audit.trust_gateway import (
    TrustGatewayApprovalProvider,
    evaluate_tool_trust,
    trace_metadata_for_tool,
)


class _FallbackProvider(ApprovalProvider):
    def __init__(self) -> None:
        self.called = False

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        self.called = True
        return ApprovalDecision(approved=True, reason="fallback")


def test_evaluate_tool_trust_uses_static_policy_before_risk_policy() -> None:
    req = ApprovalRequest(
        thread_id="thread-1",
        tool_name="exec_shell",
        tool_call_id="call-1",
        args_preview="rm -rf tmp",
    )
    policy = ApprovalPolicy(
        rules=(
            ApprovalRule(
                effect="deny",
                tool="exec_shell",
                args_contains="rm -rf",
                reason="no destructive shell",
            ),
        )
    )

    decision = evaluate_tool_trust(req, static_policy=policy)

    assert decision.source == "static_policy"
    assert decision.action == "deny"
    assert decision.approved is False
    assert decision.reason == "no destructive shell"
    assert decision.risk["level"] == "critical"


def test_trace_metadata_has_stable_trust_gateway_shape() -> None:
    req = ApprovalRequest(
        thread_id="thread-1",
        tool_name="write_text_file",
        tool_call_id="call-1",
        args_preview="README.md",
        detail="write request",
    )

    metadata = trace_metadata_for_tool(req)

    assert metadata["detail"] == "write request"
    trust = metadata["trust_gateway"]
    assert isinstance(trust, dict)
    assert trust["schema"] == "echo.trust_decision.v1"
    assert trust["tool_name"] == "write_text_file"
    assert trust["source"] == "risk_policy"
    assert trust["risk"]["level"] == "high"
    assert trust["action"] == "ask"


def test_trust_gateway_provider_records_static_allow_to_trace(tmp_path: Path) -> None:
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    fallback = _FallbackProvider()
    policy = ApprovalPolicy(
        rules=(ApprovalRule(effect="allow", tool="read_*", reason="safe reads"),)
    )
    provider = TrustGatewayApprovalProvider(
        static_policy=policy,
        fallback=fallback,
        trace_store=trace,
        turn_id="turn-1",
        agent_id="agent-a",
    )

    try:
        decision = provider.request(
            ApprovalRequest(
                thread_id="thread-1",
                tool_name="read_file",
                tool_call_id="call-1",
                args_preview="notes.md",
            )
        )
        approvals = trace.approvals(thread_id="thread-1")
    finally:
        trace.close()

    assert decision.approved is True
    assert fallback.called is False
    assert approvals[0]["decision"] == "approved"
    assert approvals[0]["turn_id"] == "turn-1"
    assert approvals[0]["agent_id"] == "agent-a"
    trust = approvals[0]["metadata"]["trust_gateway"]
    assert trust["source"] == "static_policy"
    assert trust["static_decision"] == "allow"
    assert trust["reason"] == "safe reads"


def test_trust_gateway_provider_delegates_policy_miss() -> None:
    fallback = _FallbackProvider()
    provider = TrustGatewayApprovalProvider(
        static_policy=ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_*"),)),
        fallback=fallback,
    )

    decision = provider.request(
        ApprovalRequest(
            thread_id="thread-1",
            tool_name="exec_shell",
            tool_call_id="call-1",
            args_preview="git status",
        )
    )

    assert decision.approved is True
    assert decision.reason == "fallback"
    assert fallback.called is True
