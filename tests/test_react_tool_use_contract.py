"""Default tool-use contract in the system prompt.

Root cause (thread tj1qarRWyf8H5zzT6dR_-u): the model delivered an
announce-only "我继续核对…" final answer with zero tool calls, and the
final-answer guard — which rejects that class of placeholder — was the only
line of defence. This module pins the *first* line of defence: the system
prompt must mandate actual tool use on every normal turn, and only explicit
audit/chat-like modes may answer directly. The guard stays as a last-resort
backstop, not the primary enforcement.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from runtime.core.cerebrum._react_prompt_assembly_sections import (
    _TOOL_USE_CONTRACT,
    _assemble_early_sections,
)
from runtime.core.cerebrum._react_prompt_assembly_state import _AssemblyState


def _make_state(
    goal: str = "帮我查一下 Echo 的最新版本",
    *,
    user_context: dict | None = None,
    tools_active: bool = True,
    no_tool_turn: bool = False,
) -> _AssemblyState:
    uc = dict(user_context or {})
    intent = SimpleNamespace(
        raw=goal,
        normalized_goal=goal,
        user_context=uc,
        flags={},
    )
    state = _AssemblyState(
        intent=intent,
        agent=None,
        stack=SimpleNamespace(config=SimpleNamespace()),
        executor=object() if tools_active else None,
        approval_provider=None,
        resume_task_id=None,
        planning_mode=False,
        tools_active=tools_active,
        native_mode=False,
        no_tool_turn=no_tool_turn,
        strict_explicit_reads=False,
        camouflage_suffix="",
        max_iterations=20,
        max_tokens_budget=20_000,
        max_usd_budget=0.5,
        user_context=uc,
    )
    state.metadata = dict(uc.get("metadata") or {})
    state.effective_goal = ""
    return state


def _system_text(state: _AssemblyState) -> str:
    _assemble_early_sections(state)
    return "\n".join(str(part) for part in state.system_parts)


def test_default_turn_injects_tool_use_contract() -> None:
    text = _system_text(_make_state())
    assert "<tool-use-contract>" in text
    assert "默认必须使用工具" in text


def test_contract_forbids_announce_only_prefixes_that_trip_the_guard() -> None:
    # The guard rejects "我将…/我接下来会…/我先核对…/我继续…" as placeholder
    # final answers. The prompt contract must forbid the same phrasing so the
    # model never produces it in the first place.
    for phrase in ("我将", "我接下来会", "我先核对", "我继续", "接下来", "下一步"):
        assert phrase in _TOOL_USE_CONTRACT, f"contract must forbid {phrase}"


def test_chat_mode_skips_tool_use_contract() -> None:
    text = _system_text(_make_state(user_context={"mode": "chat"}))
    assert "<tool-use-contract>" not in text


def test_flash_and_inspiration_modes_skip_tool_use_contract() -> None:
    for mode in ("flash", "inspiration", "conversation"):
        text = _system_text(_make_state(user_context={"mode": mode}))
        assert "<tool-use-contract>" not in text, f"mode={mode} must skip"


def test_audit_mode_skips_tool_use_contract() -> None:
    text = _system_text(_make_state(user_context={"mode": "audit"}))
    assert "<tool-use-contract>" not in text
    text_flag = _system_text(_make_state(user_context={"audit_mode": True}))
    assert "<tool-use-contract>" not in text_flag


def test_no_tool_turn_skips_tool_use_contract() -> None:
    text = _system_text(_make_state(goal="直接回答，不要使用任何工具", no_tool_turn=True))
    assert "<tool-use-contract>" not in text
    assert "<direct-answer-contract>" in text


def test_tools_inactive_skips_tool_use_contract() -> None:
    text = _system_text(_make_state(tools_active=False))
    assert "<tool-use-contract>" not in text


def test_contract_wording_aligns_with_guard_regex() -> None:
    """The prompt's forbidden phrases must be caught by the guard regex, so a
    model that ignores the contract still cannot slip an announce-only answer."""
    from runtime.core.cerebrum.react_final_answer_content_guards import (
        _incomplete_final_answer_guard,
    )

    assert (
        _incomplete_final_answer_guard("我继续核对广义健康板块，确认是否跟随医药大涨") is not None
    )
    assert _incomplete_final_answer_guard("我先核对三个文件再给结论") is not None
    assert _incomplete_final_answer_guard("我将检查这些数据") is not None
    assert re.search(r"禁止用预告式措辞代替执行", _TOOL_USE_CONTRACT)

