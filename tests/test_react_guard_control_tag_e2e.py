"""End-to-end integration test for control-tag leak guard in the evaluation chain.

Simulates the exact scenario from thread t293eeZgYDFq7uWVvfBhi2 where
agnes-2.5-flash echoed a `<system-reminder>` todo list as its final answer
instead of continuing work.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import GuardContext, evaluate_guards
from runtime.core.cerebrum.react_types import ReActStep


def _mk_step(iteration: int, action: str, observation: str = "") -> ReActStep:
    return ReActStep(
        iteration=iteration,
        action=action,
        observation=observation,
        action_results=[{"tool_name": action.split("(")[0], "observation": observation}]
        if observation
        else [],
    )


def test_control_tag_leak_guard_fires_in_evaluation_chain() -> None:
    """agnes回显 system-reminder 必须被 guard 拦截."""
    steps = [
        _mk_step(1, 'todo_write({"items":[...]})', "ok: 7 items created"),
        _mk_step(2, 'search_capabilities({"query":"research"})', "found: deep-research"),
        _mk_step(3, 'query_capability({"id":"deep-research"})', "details: ..."),
    ]
    # 第 4 轮 agnes 回显了 system-reminder (真实场景)
    leaked_answer = (
        "<system-reminder>\n"
        "This is a reminder that your todo list is currently:\n"
        "1. 探索可用调研能力包与研究工作流: in_progress\n"
        "2. 检索智能睡眠产品生态与代表产品: pending\n"
        "3. 检索底层传感技术与算法原理: pending\n"
        "4. 检索睡眠科学基础与睡眠分期标准: pending\n"
        "5. 检索市场规模、趋势与主要玩家: pending\n"
        "6. 检索风险、争议与循证有效性: pending\n"
        "7. 综合撰写深度调研报告: pending\n"
        "</system-reminder>"
    )

    ctx = GuardContext(
        steps=steps,
        final_answer=leaked_answer,
        is_code_mode=False,
        todo_protocol_required=True,
        todo_protocol_visible=True,
        file_inspection_tools_visible=False,
        tools_active=True,
        goal="做一个智能睡眠的深度调研",
        browser_operation_mode=False,
        grounded_source_paths=frozenset(),
        model="agnes-2.5-flash",
        execution_degraded=False,
    )

    # categories={"security","protocol","research"} 是 chat-style 短路分支的默认值
    result = evaluate_guards(ctx, categories=frozenset({"security", "protocol", "research"}))

    assert result is not None, "control-tag leak guard should have fired"
    label, message = result
    assert label == "control-tag leak guard"
    assert "internal control tag" in message
    assert "<system-reminder>" in message


def test_control_tag_fires_before_todo_protocol_guard() -> None:
    """控制标签泄漏优先级高于 todo-protocol，即使 todo 未完成也先拦回显."""
    steps = [_mk_step(1, 'todo_write({"items":[...]})', "ok: 5 items")]
    leaked = "<system-reminder>Your pending tasks: 1. A: pending, 2. B: pending</system-reminder>"

    ctx = GuardContext(
        steps=steps,
        final_answer=leaked,
        is_code_mode=False,
        todo_protocol_required=True,
        todo_protocol_visible=True,
        file_inspection_tools_visible=False,
        tools_active=True,
        goal="调研任务",
        execution_degraded=False,
    )

    result = evaluate_guards(ctx, categories=frozenset({"protocol"}))
    assert result is not None
    label, _ = result
    # control-tag leak guard 在注册表中排 todo-protocol guard 之前
    assert label == "control-tag leak guard"


def test_clean_answer_passes_all_guards() -> None:
    """正常答案不应被拦."""
    steps = [
        _mk_step(1, 'web_search({"q":"sleep tech"})', "Results: market size $2.3B"),
        _mk_step(2, 'web_search({"q":"oura ring"})', "Oura is a leader..."),
    ]
    clean_answer = (
        "根据调研，智能睡眠市场规模达到 $2.3B，主要玩家包括 Oura、Whoop 和 Eight Sleep。"
        "传感技术以 PPG 光电容积描记法和加速度计为主，睡眠分期基于心率变异性与运动模式。"
    )

    ctx = GuardContext(
        steps=steps,
        final_answer=clean_answer,
        is_code_mode=False,
        todo_protocol_required=False,
        todo_protocol_visible=False,
        file_inspection_tools_visible=False,
        tools_active=True,
        goal="智能睡眠调研",
        execution_degraded=False,
    )

    result = evaluate_guards(ctx, categories=frozenset({"security", "protocol", "research"}))
    assert result is None, "Clean research answer should pass all guards"

