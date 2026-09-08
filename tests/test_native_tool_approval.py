"""Last-mile native approval semantics.

These tests intentionally exercise the executor gate directly.  ReAct and
Codex have their own admission paths, so a regression here could otherwise
let a legacy/native worker interpret ``acceptEdits`` as unconditional access.
"""

from __future__ import annotations

from types import SimpleNamespace

from runtime.execution.tool_engine._native_tool_approval import (
    NativeToolResult,
    approve_native_call,
)
from runtime.platform.process.session import Session, session_scope
from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
)


class _RecordingProvider(ApprovalProvider):
    def __init__(self, *, approved: bool) -> None:
        self.approved = approved
        self.requests: list[ApprovalRequest] = []

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        self.requests.append(req)
        return ApprovalDecision(
            approved=self.approved,
            reason="test-approved" if self.approved else "test-denied",
        )


def _call(name: str = "write_text_file") -> SimpleNamespace:
    return SimpleNamespace(
        id="native-call-1",
        name=name,
        arguments={"path": "receipt.txt", "content": "approved content"},
    )


def _session(mode: str, provider: ApprovalProvider | None = None) -> Session:
    metadata: dict[str, object] = {"permission_mode": mode}
    if provider is not None:
        metadata["_approval_provider"] = provider
    return Session(actor="alice", thread_id="thread-native-approval", metadata=metadata)


def test_accept_edits_uses_the_auto_review_provider_for_writes() -> None:
    provider = _RecordingProvider(approved=True)
    with session_scope(_session("acceptEdits", provider)):
        result = approve_native_call(_call())

    assert result is True
    assert [item.tool_name for item in provider.requests] == ["write_text_file"]


def test_accept_edits_alias_denial_is_fail_closed() -> None:
    provider = _RecordingProvider(approved=False)
    with session_scope(_session("approve-for-me", provider)):
        result = approve_native_call(_call())

    assert isinstance(result, NativeToolResult)
    assert result.approval_blocked == "approval_not_granted"
    assert len(provider.requests) == 1


def test_accept_edits_without_a_provider_does_not_auto_approve() -> None:
    with session_scope(_session("accept-edits")):
        result = approve_native_call(_call())

    assert isinstance(result, NativeToolResult)
    assert result.approval_blocked == "approval_not_granted"


def test_bypass_permissions_remains_the_explicit_auto_path() -> None:
    provider = _RecordingProvider(approved=False)
    with session_scope(_session("full-access", provider)):
        result = approve_native_call(_call())

    assert result is True
    assert provider.requests == []
