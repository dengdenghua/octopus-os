"""Tests for ReAct protocol-block leak handling (Solution A + B).

Regression coverage for thread t0sNCfPNLzn2FuMn-FmJmH, where deepseek wrote
``Action: name({...})`` blocks into the Final Answer channel. Solution B
scrubs the markup at parse time (``_strip_react_protocol_blocks`` /
``_parse_step``); Solution A downgrades a guard rejection that is purely a
leaked-protocol answer into a one-shot cleaned delivery instead of a retry
loop (``_try_clean_downgrade``).
"""

from runtime.core.cerebrum.react_final_answer_guards import (
    _clean_protocol_leak,
    _guard_repair_feedback,
    _guard_soft_landing_answer,
    _trajectory_has_successful_tool_evidence,
    _try_clean_downgrade,
)
from runtime.core.cerebrum.react_parsing import (
    _looks_like_protocol_leak,
    _parse_step,
    _strip_react_protocol_blocks,
)
from runtime.core.cerebrum.react_types import ReActStep

_LEAK_ACTION_BLOCK = (
    "实际文件名是 execution_policy.py...继续读它...\n\n"
    'Action:\n    read_file({"path": "runtime/safety/governance/execution_policy.py"})\n'
    '    glob_files({"pattern": "runtime/core/cerebrum/*.py"})'
)
_LEAK_THOUGHT_ACTION = (
    "明白,正式开跑前端代码审计。先列计划。\n\n"
    "Thought: 阶段=理解。按审计模式只读\n\n"
    'Action:\n    list_cwd({"path": "frontend"})'
)
_INLINE_ACTION = (
    '好的,继续。\n(real tool execution succeeded)\nAction:\n    read_file({"path": "x.py"})'
)


def test_strip_react_protocol_blocks_removes_action_block_keeps_narration():
    cleaned = _strip_react_protocol_blocks(_LEAK_ACTION_BLOCK)
    assert "Action:" not in cleaned
    assert "read_file" not in cleaned
    assert "实际文件名是 execution_policy.py" in cleaned


def test_strip_react_protocol_blocks_removes_thought_and_action():
    cleaned = _strip_react_protocol_blocks(_LEAK_THOUGHT_ACTION)
    assert "Thought:" not in cleaned
    assert "Action:" not in cleaned
    assert "list_cwd" not in cleaned
    assert "正式开跑前端代码审计" in cleaned


def test_strip_react_protocol_blocks_drops_receipt_marker():
    cleaned = _strip_react_protocol_blocks(_INLINE_ACTION)
    assert "(real tool execution succeeded)" not in cleaned
    assert "Action:" not in cleaned
    assert "好的,继续。" in cleaned


def test_strip_react_protocol_blocks_keeps_legit_prose():
    prose = "ReAct 协议包含 Thought/Action/Observation 三个块。"
    assert _strip_react_protocol_blocks(prose) == prose


def test_looks_like_protocol_leak_detects_and_rejects():
    assert _looks_like_protocol_leak(_LEAK_ACTION_BLOCK) is True
    assert _looks_like_protocol_leak(_LEAK_THOUGHT_ACTION) is True
    assert _looks_like_protocol_leak("这是一条正常的审计结论,没有协议块。") is False


def test_parse_step_recovers_bare_inline_json_tool_call():
    step, final = _parse_step(
        'web_search({"query":"site:example.com complaint","max_results":10})',
        iteration=1,
    )
    assert step.action == 'web_search({"query": "site:example.com complaint", "max_results": 10})'
    assert step.actions == [step.action]
    assert final is None


def test_bare_inline_tool_call_is_protocol_leak_not_final_prose():
    text = 'web_search({"query":"x"})'
    assert _looks_like_protocol_leak(text) is True


def test_inline_tool_example_inside_prose_is_not_executable():
    step, final = _parse_step(
        '说明一下 web_search({"query":"x"}) 的用途。',
        iteration=1,
    )
    assert not step.action
    assert _looks_like_protocol_leak('说明一下 web_search({"query":"x"}) 的用途。') is False
    assert final is None


def test_parse_step_cleans_final_when_no_pending_action():
    # A Final Answer that carries only protocol noise (no executable Action)
    # must be scrubbed, not delivered verbatim.
    text = f"Final Answer: {_LEAK_ACTION_BLOCK}"
    step, final = _parse_step(text, iteration=1)
    assert final is None or "Action:" not in final


def test_parse_step_keeps_action_and_drops_leaky_final():
    # When an Action is pending, the leaked block is a status update — the
    # answer is dropped so the loop keeps working instead of finalizing on a
    # partial narration.
    text = _LEAK_ACTION_BLOCK
    step, final = _parse_step(text, iteration=1)
    assert step.action  # the work still routes to the tool channel
    assert final is None


def test_try_clean_downgrade_delivers_cleaned_leak():
    assert (
        _try_clean_downgrade(_LEAK_ACTION_BLOCK) == "实际文件名是 execution_policy.py...继续读它..."
    )


def test_try_clean_downgrade_rejects_legit_answer():
    assert _try_clean_downgrade("前端代码审计完成。发现 3 个问题:1. XSS 2. 密钥泄露。") is None


def test_guard_soft_landing_answer_strips_protocol():
    landed = _guard_soft_landing_answer(_LEAK_ACTION_BLOCK, "protocol guard")
    assert "Action:" not in landed
    assert "实际文件名是 execution_policy.py" in landed


def test_clean_protocol_leak_combines_inline_and_block():
    combined = _clean_protocol_leak(
        '总结:\nAction:\n    read_file({"path": "a.py"})\n另外 todo_write({"items": []})'
    )
    assert "Action:" not in combined
    assert "read_file" not in combined
    assert "todo_write" not in combined
    assert "总结:" in combined


def test_completeness_repair_synthesizes_when_tool_evidence_already_exists():
    steps = [
        ReActStep(
            iteration=1,
            action='read_file({"path": "README.md"})',
            observation="project facts",
            action_results=[{"ok": True, "observation": "project facts"}],
        )
    ]

    feedback = _guard_repair_feedback(
        "final-answer completeness guard",
        "Execute the stated read/search action.",
        steps,
    )

    assert _trajectory_has_successful_tool_evidence(steps) is True
    assert "Do not call another tool" in feedback
    assert "recorded Observations" in feedback
    assert "complete Final Answer now" in feedback


def test_completeness_repair_keeps_evidence_request_when_tool_failed():
    steps = [
        ReActStep(
            iteration=1,
            action='read_file({"path": "missing.md"})',
            observation="tool failed: file not found",
            action_results=[{"ok": False, "observation": "file not found"}],
        )
    ]
    original = "Execute the stated read/search action."

    assert _trajectory_has_successful_tool_evidence(steps) is False
    feedback = _guard_repair_feedback("final-answer completeness guard", original, steps)
    # The guard's own repair instruction is preserved; an anti-echo directive
    # is appended so the model never quotes loop machinery in the answer.
    assert original in feedback
    assert "internal loop machinery" in feedback


def test_non_completeness_guard_feedback_is_unchanged_with_evidence():
    steps = [
        ReActStep(
            iteration=1,
            action='read_file({"path": "README.md"})',
            observation="project facts",
            action_results=[{"ok": True}],
        )
    ]
    original = "Run the requested verification."

    feedback = _guard_repair_feedback("verification guard", original, steps)
    assert original in feedback
    assert "internal loop machinery" in feedback
    assert "user-facing Final Answer" in feedback


_FENCED_ACTION_EXAMPLE = (
    "以下是一个 Action 调用示例：\n\n"
    "```\n"
    "Action:\n"
    '    read_file({"path": "a.py"})\n'
    "```\n\n"
    "请按这个格式继续调用。"
)


def test_looks_like_protocol_leak_ignores_fenced_examples() -> None:
    # A quoted ``Action:`` example inside a markdown fence is legitimate
    # prose, not leaked protocol.
    assert _looks_like_protocol_leak(_FENCED_ACTION_EXAMPLE) is False


def test_strip_react_protocol_blocks_preserves_fenced_examples() -> None:
    cleaned = _strip_react_protocol_blocks(_FENCED_ACTION_EXAMPLE)
    assert "Action:" in cleaned
    assert "read_file" in cleaned
    assert "以下是一个 Action 调用示例" in cleaned


def test_parse_step_keeps_fenced_example_in_final() -> None:
    # Fenced protocol examples must survive the parse so the user sees the
    # model's quoted illustration instead of a silently emptied answer.
    text = f"Final Answer: {_FENCED_ACTION_EXAMPLE}"
    step, final = _parse_step(text, iteration=1)
    assert final is not None
    assert "Action:" in final
    assert "read_file" in final
    assert step.raw_llm_output == text

