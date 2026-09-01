"""An authorised audit turn must not carry the read-only clause at all.

Regression cover for the audit-mode livelock: the contract stated the read-only
default *and* an exception for authorised repairs, so the model re-adjudicated
"does the exception apply?" every round. In trn_c2fbddce247b4164 and
trn_3348dff0b9e54a99 the reasoning traces spend most of their length on that
question and conclude with another plan instead of an edit. Once authorised,
the read-only clause is removed rather than qualified.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from runtime.core.cerebrum._react_prompt_assembly_guidance import (
    _assemble_delegation_guidance,
    _fix_authorization_present,
)
from runtime.core.cerebrum._react_prompt_assembly_state import _AssemblyState


def _state(goal: str = "", **ctx: object) -> Any:
    """A real assembly state so the audit branch runs against production code."""
    state = _AssemblyState(
        intent=SimpleNamespace(normalized_goal=goal, user_context=dict(ctx)),
        agent=None,
        stack=None,
        executor=None,
        approval_provider=None,
        resume_task_id=None,
        planning_mode=False,
        tools_active=True,
        native_mode=True,
        no_tool_turn=False,
        strict_explicit_reads=False,
        camouflage_suffix="",
        max_iterations=10,
        max_tokens_budget=None,
        max_usd_budget=None,
        user_context=dict(ctx),
    )
    state.agent_mode_value = "audit"
    state.mode_value = "code"
    state.is_code_mode = True
    state.work_mode = SimpleNamespace(scope="project")
    return state


def _audit_section(state: SimpleNamespace) -> str:
    _assemble_delegation_guidance(state)  # type: ignore[arg-type]
    for part in state.system_parts:
        if "<audit-mode>" in part:
            return part
    return ""


def test_authorized_section_drops_the_read_only_clause() -> None:
    section = _audit_section(_state("继续修复上面发现的问题"))
    assert "只读约束不再适用" in section
    assert "征得确认后再执行写操作" not in section


def test_unauthorized_section_keeps_the_read_only_default() -> None:
    section = _audit_section(_state("审计项目"))
    assert "征得确认后再执行写操作" in section
    assert "只读约束不再适用" not in section


def test_authorized_section_forbids_plan_as_conclusion() -> None:
    """The observed failure was ending the turn on "I'll check X next"."""
    section = _audit_section(_state("干活啊"))
    assert "不要复述计划代替执行" in section


def test_imperative_repair_instructions_authorize() -> None:
    for goal in ("继续修复", "干活啊", "动手", "改吧", "全修", "我让你修复问题", "开始优化"):
        assert _fix_authorization_present(_state(goal)) is True, goal


def test_questions_about_problems_do_not_authorize() -> None:
    """Asking *about* repairs is not a mandate to perform them."""
    for goal in ("审计项目", "有哪些问题需要修复？", "这些问题严重吗", "帮我看看代码质量"):
        assert _fix_authorization_present(_state(goal)) is False, goal


def test_english_imperatives_authorize() -> None:
    for goal in ("go ahead and fix it", "Just fix the failing test", "apply the fix"):
        assert _fix_authorization_present(_state(goal)) is True, goal


def test_explicit_host_flag_overrides_goal_text() -> None:
    assert _fix_authorization_present(_state("审计项目", fix_authorized=True)) is True
    assert _fix_authorization_present(_state("继续修复", fix_authorized=False)) is False


def test_empty_goal_defaults_to_read_only() -> None:
    assert _fix_authorization_present(_state("")) is False

