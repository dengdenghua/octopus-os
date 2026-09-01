"""Tests for the extended hook event set (subagent / failure / permission)."""

from __future__ import annotations

from typing import Any

import pytest

from runtime.safety.approval.approval_gate import (
    ApprovalPolicy,
    ApprovalRequest,
    AutoDenyProvider,
    RuleBasedProvider,
)
from runtime.safety.hooks import (
    HookDecision,
    PermissionDeniedEvent,
    PermissionRequestEvent,
    PostToolUseFailureEvent,
    SubagentStartEvent,
    SubagentStopEvent,
    get_global_registry,
    register_hook,
)


def _clear_event_handlers() -> None:
    reg = get_global_registry()
    reg._handlers.clear()


@pytest.fixture(autouse=True)
def _clear_hooks():
    _clear_event_handlers()
    yield
    _clear_event_handlers()


def test_subagent_start_and_stop_fire(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.safety.hooks import dispatch_subagent_start, dispatch_subagent_stop

    seen: list[str] = []

    @register_hook(SubagentStartEvent)
    def on_start(event: SubagentStartEvent) -> HookDecision:
        seen.append(f"start:{event.subagent_type}")
        return HookDecision.pass_through()

    @register_hook(SubagentStopEvent)
    def on_stop(event: SubagentStopEvent) -> HookDecision:
        seen.append(f"stop:{event.subagent_type}:{event.ok}")
        return HookDecision.pass_through()

    dispatch_subagent_start(thread_id="t1", agent_id="r", subagent_type="researcher")
    dispatch_subagent_stop(thread_id="t1", agent_id="r", subagent_type="researcher", ok=True)
    assert seen == ["start:researcher", "stop:researcher:True"]


def test_subagent_hooks_fire_through_bridge(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.subagents import bridge

    seen: list[str] = []

    @register_hook(SubagentStartEvent)
    def on_start(event: SubagentStartEvent) -> HookDecision:
        seen.append(f"start:{event.subagent_type}")
        return HookDecision.pass_through()

    @register_hook(SubagentStopEvent)
    def on_stop(event: SubagentStopEvent) -> HookDecision:
        seen.append(f"stop:{event.subagent_type}:{event.ok}")
        return HookDecision.pass_through()

    previous_runner = bridge.get_sub_agent_runner()
    try:

        def runner(prompt: str, **kw: object) -> str:
            return "the answer"

        bridge.set_sub_agent_runner(runner)  # type: ignore[arg-type]
        result = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="do the thing",
        )
    finally:
        bridge.set_sub_agent_runner(previous_runner)
    assert result.get("success") is True
    assert any(s.startswith("start:") for s in seen)
    assert any(s.startswith("stop:") and s.endswith(":True") for s in seen)


def test_post_tool_failure_fires() -> None:
    from runtime.safety.hooks import dispatch_post_tool_failure

    seen: list[str] = []

    @register_hook(PostToolUseFailureEvent)
    def on_failure(event: PostToolUseFailureEvent) -> HookDecision:
        seen.append(f"{event.sucker_id}:{event.error}")
        return HookDecision.pass_through()

    dispatch_post_tool_failure(sucker_id="exec_shell", args={"cmd": "x"}, error="boom")
    assert seen == ["exec_shell:boom"]


def test_permission_request_hook_can_deny() -> None:

    policy = ApprovalPolicy(rules=[])
    provider = RuleBasedProvider(policy=policy, fallback=AutoDenyProvider())

    @register_hook(PermissionRequestEvent)
    def deny_shell(event: PermissionRequestEvent) -> HookDecision:
        if event.sucker_id == "exec_shell":
            return HookDecision.cancel("hook refuses shell")
        return HookDecision.pass_through()

    req = ApprovalRequest(thread_id="t1", tool_name="exec_shell", tool_call_id="c1")
    decision = provider.request(req)
    assert decision.approved is False
    assert "hook refuses shell" in (decision.reason or "")
    # Non-matching tool falls through to the fallback (AutoDeny).
    req2 = ApprovalRequest(thread_id="t1", tool_name="read_file", tool_call_id="c2")
    decision2 = provider.request(req2)
    assert decision2.approved is False


def test_permission_denied_fires_on_deny() -> None:
    from runtime.safety.hooks import dispatch_permission_denied

    seen: list[str] = []

    @register_hook(PermissionDeniedEvent)
    def on_denied(event: PermissionDeniedEvent) -> HookDecision:
        seen.append(f"{event.sucker_id}:{event.reason}")
        return HookDecision.pass_through()

    dispatch_permission_denied(sucker_id="exec_shell", reason="policy deny")
    assert seen == ["exec_shell:policy deny"]

