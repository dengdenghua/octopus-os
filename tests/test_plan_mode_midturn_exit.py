"""Mid-turn ``exit_plan_mode`` tests.

Pinned contract
---------------

1. Headless / no provider → handler returns ``error_type="unsupported"``,
   ``approved=False`` (caller falls back to legacy behaviour).
2. Provider approves → handler returns
   ``{"ok": True, "approved": True, "continue_in_same_turn": True}`` and
   sets ``session.metadata["_plan_mode_exit_approved"] = True``.
3. Provider denies → handler returns ``{"ok": False, "approved": False}``
   and does NOT set the metadata flag.
4. react_loop iteration check: when planning_mode is True and the
   ``_plan_mode_exit_approved`` flag is present in session metadata,
   the next iteration entry flips planning_mode to False and re-enables
   tools.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers.plan_mode import _exit_plan_mode
from runtime.platform.process.session import Session
from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
)


class _StaticProvider(ApprovalProvider):
    """Test provider that returns a pre-canned decision."""

    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def request(
        self,
        req: ApprovalRequest,
        *,
        timeout: float = 120.0,
    ) -> ApprovalDecision:
        self.requests.append(req)
        return self.decision


class _RaisingProvider(ApprovalProvider):
    def request(
        self,
        req: ApprovalRequest,
        *,
        timeout: float = 120.0,
    ) -> ApprovalDecision:
        raise RuntimeError("transport down")


# ═══════════════════════════════════════════════════════════
# 1) Headless behaviour — no live channel
# ═══════════════════════════════════════════════════════════


def test_headless_no_session_returns_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``current_session()`` returns None and no session is passed
    explicitly, the handler must surface ``error_type="unsupported"`` so
    the caller can fall back to the legacy "end turn" behaviour."""
    monkeypatch.setattr(
        "runtime.platform.process.session.current_session",
        lambda: None,
    )
    result = _exit_plan_mode(plan="demo", confirm=True, new_mode="chat")
    assert result["approved"] is False
    assert result["error_type"] == "unsupported"
    assert result["error"] == "no_user_channel"


def test_session_without_provider_is_unsupported() -> None:
    """A session is present but nobody plumbed an approval provider —
    same fallback shape as the truly headless path."""
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "plan"},
    )
    result = _exit_plan_mode(
        plan="demo",
        confirm=True,
        new_mode="chat",
        session=sess,
    )
    assert result["approved"] is False
    assert result["error_type"] == "unsupported"


# ═══════════════════════════════════════════════════════════
# 2) Provider approves — flag set, payload promises continuation
# ═══════════════════════════════════════════════════════════


def test_approval_granted_sets_metadata_flag() -> None:
    provider = _StaticProvider(ApprovalDecision(approved=True, reason="accept"))
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "plan", "_approval_provider": provider},
    )
    result = _exit_plan_mode(
        plan="write hello.txt",
        confirm=True,
        new_mode="chat",
        session=sess,
    )
    assert result == {
        "ok": True,
        "approved": True,
        "continue_in_same_turn": True,
        "mode_transitioned": True,
        "from": "plan",
        "to": "chat",
        "plan": "write hello.txt",
        "persist_hint": {"thread_metadata": {"mode": "chat"}},
    }
    assert sess.metadata["_plan_mode_exit_approved"] is True
    assert sess.metadata["mode"] == "chat"
    assert len(provider.requests) == 1
    assert provider.requests[0].tool_name == "exit_plan_mode"
    assert "write hello.txt" in provider.requests[0].args_preview


def test_approval_request_carries_method_detail() -> None:
    """The approval request's ``detail`` must reference the new
    JSON-RPC method so the gateway routes it correctly."""
    provider = _StaticProvider(ApprovalDecision(approved=True))
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"_approval_provider": provider},
    )
    _exit_plan_mode(
        plan="x",
        confirm=True,
        new_mode="code",
        session=sess,
    )
    assert len(provider.requests) == 1
    assert provider.requests[0].detail == "item/planMode/exitRequest"


# ═══════════════════════════════════════════════════════════
# 3) Provider denies — no flag, ok=False
# ═══════════════════════════════════════════════════════════


def test_approval_denied_returns_not_ok() -> None:
    provider = _StaticProvider(
        ApprovalDecision(approved=False, reason="user_clicked_decline"),
    )
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "plan", "_approval_provider": provider},
    )
    result = _exit_plan_mode(
        plan="risky",
        confirm=True,
        new_mode="code",
        session=sess,
    )
    assert result["ok"] is False
    assert result["approved"] is False
    assert result["reason"] == "user_clicked_decline"
    # Critical: the flag must NOT be set on denial — react_loop must
    # stay in planning mode.
    assert "_plan_mode_exit_approved" not in sess.metadata
    # And the mode must NOT have been mutated.
    assert sess.metadata["mode"] == "plan"


def test_provider_exception_treated_as_transport_error() -> None:
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "plan", "_approval_provider": _RaisingProvider()},
    )
    result = _exit_plan_mode(
        plan="x",
        confirm=True,
        new_mode="chat",
        session=sess,
    )
    assert result["ok"] is False
    assert result["approved"] is False
    assert result["error_type"] == "transport"
    assert "_plan_mode_exit_approved" not in sess.metadata


def test_confirm_false_short_circuits_before_provider() -> None:
    """``confirm=False`` is the agent's "are-you-sure" guard — it must
    short-circuit BEFORE we ever touch the approval channel."""
    provider = _StaticProvider(ApprovalDecision(approved=True))
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"_approval_provider": provider},
    )
    result = _exit_plan_mode(
        plan="x",
        confirm=False,
        new_mode="chat",
        session=sess,
    )
    assert result["mode_transitioned"] is False
    assert provider.requests == []  # provider must not be called


def test_invalid_new_mode_short_circuits_before_provider() -> None:
    provider = _StaticProvider(ApprovalDecision(approved=True))
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"_approval_provider": provider},
    )
    result = _exit_plan_mode(
        plan="x",
        confirm=True,
        new_mode="galaxy_brain",
        session=sess,
    )
    assert result["mode_transitioned"] is False
    assert provider.requests == []


# ═══════════════════════════════════════════════════════════
# 4) react_loop integration sanity — flag detection
# ═══════════════════════════════════════════════════════════


def _emulate_react_loop_post_step(
    session: Any,
    planning_mode: bool,
    enable_tools: bool,
) -> tuple[bool, bool]:
    """Subset of the per-iteration post-step check that lives in
    ``react_loop.stream_react_loop``. Mirrors the production code so we
    can unit-test the state-machine without booting the full loop."""
    if planning_mode and (
        session is not None
        and session.metadata is not None
        and session.metadata.pop("_plan_mode_exit_approved", False)
    ):
        planning_mode = False
        enable_tools = True
    return planning_mode, enable_tools


def test_react_loop_flips_when_flag_set() -> None:
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "chat", "_plan_mode_exit_approved": True},
    )
    planning_mode, enable_tools = _emulate_react_loop_post_step(
        sess,
        planning_mode=True,
        enable_tools=False,
    )
    assert planning_mode is False
    assert enable_tools is True
    # Flag is consumed exactly once.
    assert "_plan_mode_exit_approved" not in sess.metadata


def test_react_loop_stays_in_plan_when_flag_absent() -> None:
    sess = Session(
        actor="u",
        thread_id="t",
        metadata={"mode": "plan"},
    )
    planning_mode, enable_tools = _emulate_react_loop_post_step(
        sess,
        planning_mode=True,
        enable_tools=False,
    )
    assert planning_mode is True
    assert enable_tools is False
