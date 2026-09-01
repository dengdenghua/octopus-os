"""Tests for the two-layer permission model in approval_gate.

Covers the new ``ApprovalRule`` / ``ApprovalPolicy`` / ``RuleBasedProvider``
additions. The model is a small declarative rule set that absorbs
the common case so the UI-backed fallback provider only sees the
genuinely ambiguous calls.
"""

from __future__ import annotations

from runtime.safety.approval.approval_gate import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalProvider,
    ApprovalRequest,
    ApprovalRiskPolicy,
    ApprovalRule,
    AutoApproveProvider,
    AutoDenyProvider,
    RuleBasedProvider,
    approval_action_for_tool,
    is_dangerous_tool,
)


def _req(tool: str, preview: str = "") -> ApprovalRequest:
    return ApprovalRequest(
        thread_id="t",
        tool_name=tool,
        tool_call_id="call-1",
        args_preview=preview,
    )


def test_data_egress_tools_default_to_dangerous() -> None:
    for tool_name in (
        "send_message",
        "http_post",
        "fetch_url",
        "email_send",
        "slack_post_message",
    ):
        assert is_dangerous_tool(tool_name) is True


def test_desktop_gui_tools_default_to_dangerous() -> None:
    """Mouse, keyboard, and screen capture are GUI control + screen
    egress · they must default to needing approval. The ``DANGEROUS_PREFIXES``
    fix-up that added these prefixes pairs with the ``Capabilities``
    group gate: capabilities is the user's coarse on/off, the approval
    gate is the per-call check when the group IS enabled but the
    individual call still warrants confirmation."""
    for tool_name in (
        "mouse_click",
        "mouse_move",
        "keyboard_type",
        "keyboard_press",
        "screen_capture",
        "screen_info",
    ):
        assert is_dangerous_tool(tool_name) is True, tool_name


def test_browser_skills_default_to_dangerous() -> None:
    """Both the headless Playwright pool (``browser_*``) and the live
    bridge into the desktop Electron webview (``live_browser_*``) are
    flagged. The live bridge in particular drives the user's real,
    logged-in browser, so leaving it un-gated is the worst case the
    fix-up targets."""
    for tool_name in (
        "browser_get",
        "browser_navigate",
        "browser_click",
        "browser_screenshot",
        "live_browser_click",
        "live_browser_type",
        "live_browser_navigate",
        "live_browser_execute_js",
    ):
        assert is_dangerous_tool(tool_name) is True, tool_name


def test_computer_api_executors_are_dangerous_but_observers_are_not() -> None:
    """The computer/* API splits into observe / preview / execute.
    Approval should fire on the executor side (``computer_plan_next``
    sends a screenshot off-host to a vision model, ``computer_execute_token``
    actually moves the mouse) but stay quiet on the read/queue side
    (``computer_observe``, ``computer_preview_action`` just queue a
    token for later confirmation)."""
    assert is_dangerous_tool("computer_plan_next") is True
    assert is_dangerous_tool("computer_execute_token") is True
    assert is_dangerous_tool("computer_observe") is False
    assert is_dangerous_tool("computer_preview_action") is False


def test_unrelated_tools_remain_safe() -> None:
    """The widened catalog must not bleed onto unrelated read skills
    that share no prefix with the automation surface."""
    for tool_name in ("read_file", "list_cwd", "file_stats", "count_words"):
        assert is_dangerous_tool(tool_name) is False, tool_name


def test_risk_policy_maps_levels_to_actions() -> None:
    risk, action, policy = approval_action_for_tool(
        "exec_shell",
        "git reset --hard",
        policy={"critical": "deny", "high": "ask"},
    )

    assert risk.level == "critical"
    assert action == "deny"
    assert policy.to_dict()["critical"] == "deny"


def test_risk_policy_defaults_critical_to_confirm() -> None:
    _risk, action, policy = approval_action_for_tool(
        "exec_shell",
        "rm -rf dist",
        policy=ApprovalRiskPolicy(),
    )

    assert action == "confirm"
    assert policy.critical == "confirm"


class _CountingFallback(ApprovalProvider):
    def __init__(self, inner: ApprovalProvider) -> None:
        self.inner = inner
        self.calls = 0

    def request(self, req: ApprovalRequest, *, timeout: float = 120.0) -> ApprovalDecision:
        self.calls += 1
        return self.inner.request(req, timeout=timeout)


class TestApprovalPolicy:
    def test_first_match_wins(self) -> None:
        policy = ApprovalPolicy(
            rules=(
                ApprovalRule(effect="allow", tool="read_*"),
                ApprovalRule(effect="deny", tool="read_secrets"),
            )
        )
        decision = policy.decide(_req("read_secrets"))
        assert decision is not None
        assert decision.approved is True

    def test_miss_returns_none(self) -> None:
        policy = ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_*"),))
        assert policy.decide(_req("exec_shell")) is None

    def test_glob_matching(self) -> None:
        policy = ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="git_*"),))
        assert policy.decide(_req("git_status")).approved is True
        assert policy.decide(_req("git_push")).approved is True
        assert policy.decide(_req("exec_shell")) is None

    def test_args_contains_filter(self) -> None:
        policy = ApprovalPolicy(
            rules=(
                ApprovalRule(
                    effect="deny",
                    tool="exec_shell",
                    args_contains="rm -rf",
                ),
                ApprovalRule(effect="allow", tool="exec_shell"),
            )
        )
        assert policy.decide(_req("exec_shell", "rm -rf /tmp/x")).approved is False
        assert policy.decide(_req("exec_shell", "ls -la")).approved is True

    def test_wildcard_tool_matches_anything(self) -> None:
        policy = ApprovalPolicy(rules=(ApprovalRule(effect="deny", tool="*"),))
        assert policy.decide(_req("anything")).approved is False


class TestRuleBasedProvider:
    def test_hit_short_circuits_fallback(self) -> None:
        fallback = _CountingFallback(AutoDenyProvider())
        provider = RuleBasedProvider(
            ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_*"),)),
            fallback,
        )

        decision = provider.request(_req("read_file"))

        assert decision.approved is True
        assert fallback.calls == 0

    def test_miss_delegates_to_fallback(self) -> None:
        fallback = _CountingFallback(AutoApproveProvider())
        provider = RuleBasedProvider(
            ApprovalPolicy(rules=(ApprovalRule(effect="allow", tool="read_*"),)),
            fallback,
        )

        decision = provider.request(_req("exec_shell"))

        assert decision.approved is True
        assert fallback.calls == 1

    def test_deny_rule_short_circuits(self) -> None:
        fallback = _CountingFallback(AutoApproveProvider())
        provider = RuleBasedProvider(
            ApprovalPolicy(
                rules=(
                    ApprovalRule(
                        effect="deny",
                        tool="exec_shell",
                        args_contains="rm -rf",
                        reason="destructive",
                    ),
                )
            ),
            fallback,
        )

        decision = provider.request(_req("exec_shell", "rm -rf /"))

        assert decision.approved is False
        assert decision.reason == "destructive"
        assert fallback.calls == 0

    def test_empty_policy_always_delegates(self) -> None:
        fallback = _CountingFallback(AutoDenyProvider())
        provider = RuleBasedProvider(ApprovalPolicy(), fallback)

        provider.request(_req("read_file"))
        provider.request(_req("exec_shell"))

        assert fallback.calls == 2
