"""Invariant tests for the dsh-style tool-result pruner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.execution.tool_engine.tool_output_pruner import (
    DEFAULT_PRUNE_HEAD_CHARS,
    DEFAULT_PRUNE_TAIL_CHARS,
    DEFAULT_PRUNE_THRESHOLD_CHARS,
    PRUNE_MARKER,
    ToolResultPrunePolicy,
    prune_tool_result_text,
    validate_prune_budgets,
)
from runtime.execution.tool_engine.tool_protocol import (
    normalize_step_tool_result,
    normalize_tool_result,
    render_tool_output,
)


def test_within_budget_is_noop():
    text = "x" * (DEFAULT_PRUNE_THRESHOLD_CHARS - 1)
    assert prune_tool_result_text(text) is None


def test_exact_budget_is_noop():
    text = "x" * DEFAULT_PRUNE_THRESHOLD_CHARS
    assert prune_tool_result_text(text) is None


def test_over_budget_prunes_to_head_marker_tail():
    text = "A" * 9000 + "Z" * 2000
    pruned = prune_tool_result_text(text)
    assert pruned is not None
    assert pruned == (
        "A" * DEFAULT_PRUNE_HEAD_CHARS + PRUNE_MARKER + "Z" * DEFAULT_PRUNE_TAIL_CHARS
    )


def test_pruned_result_is_strictly_smaller():
    text = "x" * (DEFAULT_PRUNE_THRESHOLD_CHARS + 5000)
    pruned = prune_tool_result_text(text)
    assert pruned is not None
    assert len(pruned) < len(text)
    assert len(pruned) == DEFAULT_PRUNE_HEAD_CHARS + len(PRUNE_MARKER) + DEFAULT_PRUNE_TAIL_CHARS


def test_second_pass_is_noop():
    text = "y" * (DEFAULT_PRUNE_THRESHOLD_CHARS * 2)
    once = prune_tool_result_text(text)
    assert once is not None
    assert prune_tool_result_text(once) is None


def test_unicode_code_point_boundaries_never_split():
    # Each emoji is a single code point; Python str slicing is code-point safe.
    head_emoji = "😀" * DEFAULT_PRUNE_HEAD_CHARS
    tail_emoji = "🎉" * DEFAULT_PRUNE_TAIL_CHARS
    middle = "A" * (DEFAULT_PRUNE_THRESHOLD_CHARS + 100)
    text = head_emoji + middle + tail_emoji
    pruned = prune_tool_result_text(text)
    assert pruned is not None
    assert pruned == head_emoji + PRUNE_MARKER + tail_emoji


def test_custom_policy_budgets():
    policy = ToolResultPrunePolicy(threshold_chars=100, head_chars=40, tail_chars=10)
    text = "A" * 60 + "B" * 60
    pruned = prune_tool_result_text(text, policy=policy)
    assert pruned is not None
    assert pruned == "A" * 40 + PRUNE_MARKER + "B" * 10


def test_tail_chars_zero_drops_tail():
    policy = ToolResultPrunePolicy(threshold_chars=100, head_chars=50, tail_chars=0)
    pruned = prune_tool_result_text("A" * 120, policy=policy)
    assert pruned == "A" * 50 + PRUNE_MARKER


def test_validate_rejects_bad_budgets():
    with pytest.raises(ValueError):
        ToolResultPrunePolicy(threshold_chars=0)
    with pytest.raises(ValueError):
        ToolResultPrunePolicy(head_chars=-1)
    with pytest.raises(ValueError):
        ToolResultPrunePolicy(tail_chars=-1)
    with pytest.raises(ValueError):
        # head + marker + tail exceeds the threshold
        ToolResultPrunePolicy(threshold_chars=100, head_chars=100, tail_chars=1)
    with pytest.raises(ValueError):
        validate_prune_budgets(threshold_chars=10, head_chars=5, tail_chars=5, marker="\n\n[m]\n\n")


def test_render_tool_output_prune_opt_in():
    text = "A" * (DEFAULT_PRUNE_THRESHOLD_CHARS + 100) + "END"
    # Default: legacy head truncation when max_chars is set, no middle pruning.
    default_rendered = render_tool_output(text)
    assert default_rendered == text
    pruned = render_tool_output(text, prune_middle=True)
    assert PRUNE_MARKER in pruned
    assert pruned.endswith("END")
    assert len(pruned) < len(text)


def test_render_tool_output_prune_then_max_chars():
    text = "A" * (DEFAULT_PRUNE_THRESHOLD_CHARS * 2)
    rendered = render_tool_output(text, prune_middle=True, max_chars=5000)
    assert "(truncated," in rendered
    assert PRUNE_MARKER in rendered


def test_normalize_tool_result_prune_middle_flag():
    call = {"id": "t1", "name": "read_file", "arguments": {"path": "/tmp/x"}}
    output = "A" * (DEFAULT_PRUNE_THRESHOLD_CHARS + 500) + "TAIL"
    result = normalize_tool_result(call, output, prune_middle=True)
    assert PRUNE_MARKER in result.rendered
    assert result.rendered.endswith("TAIL")
    assert result.output is output  # raw output untouched


def test_normalize_step_tool_result_prune_middle_flag():
    step = SimpleNamespace(
        action=SimpleNamespace(id="t2", name="bash", input={"cmd": "ls"}),
        result=SimpleNamespace(
            status="success",
            output="B" * (DEFAULT_PRUNE_THRESHOLD_CHARS + 500) + "TAIL2",
            error_type=None,
        ),
    )
    result = normalize_step_tool_result(step, prune_middle=True)
    assert PRUNE_MARKER in result.rendered
    assert result.rendered.endswith("TAIL2")

