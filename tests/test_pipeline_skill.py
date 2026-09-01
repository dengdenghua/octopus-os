"""Tests for the run_pipeline delegation skill.

Covers:
  - Guard conditions (empty items, empty stages)
  - Stage chaining: {item}/{prev}/{stageN_output} substitution
  - Concurrent item chains: all items processed, results in original order
  - Stage failure halts remaining stages for that item, others continue
  - Budget gate: turn cap exhausted → fail-closed
  - Clamping: items > 16 trimmed, stages > 4 trimmed
  - Registration: register_delegation_skills returns 5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def mock_subagent():
    """Intercept call_subagent at the import site used inside delegation_skills."""
    with patch("runtime.execution.subagents.call_subagent") as m:
        yield m


@pytest.fixture
def mock_builtins():
    """Stub _allowed_agent_ids so spec validation always passes."""
    allowed = {"researcher", "reviewer", "explorer", "critic", "implementer"}
    with patch(
        "runtime.execution.suckers.delegation_skills._allowed_agent_ids",
        return_value=allowed,
    ):
        yield allowed


@pytest.fixture
def unlimited_budget():
    """Disable the per-turn delegation cap so tests never hit it."""
    with (
        patch(
            "runtime.execution.suckers.delegation_skills._check_absolute_cap",
            return_value=(0, True),
        ),
        patch(
            "runtime.execution.suckers.delegation_skills._record_delegation",
        ),
    ):
        yield


# ── Guard conditions ───────────────────────────────────────────────────────


def test_empty_items_returns_error(unlimited_budget):
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    r = _run_pipeline(items=[], stages=[{"prompt_template": "review {item}"}])
    assert r["ok"] is False
    assert "required" in r["error"]
    assert r["results"] == []


def test_missing_items_returns_error(unlimited_budget):
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    r = _run_pipeline(stages=[{"prompt_template": "review {item}"}])
    assert r["ok"] is False


def test_empty_stages_returns_error(unlimited_budget):
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    r = _run_pipeline(items=["a.py"], stages=[])
    assert r["ok"] is False
    assert "required" in r["error"]


def test_none_stages_returns_error(unlimited_budget):
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    r = _run_pipeline(items=["a.py"], stages=None)
    assert r["ok"] is False


# ── Stage chaining ─────────────────────────────────────────────────────────


def test_item_placeholder_substituted(mock_subagent, unlimited_budget, mock_builtins):
    """Stage 0 receives {item} = the original item string."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    captured: list[str] = []

    def _capture(**kwargs):
        captured.append(kwargs.get("prompt", ""))
        return {"success": True, "output": "stage0-out"}

    mock_subagent.side_effect = _capture

    r = _run_pipeline(
        items=["hello.py"],
        stages=[{"prompt_template": "review {item}", "agent_id": "reviewer"}],
    )

    assert r["ok"] is True
    assert any("hello.py" in p for p in captured)
    assert r["results"][0]["final_output"] == "stage0-out"


def test_prev_substituted_in_stage1(mock_subagent, unlimited_budget, mock_builtins):
    """{prev} in stage 1 receives stage 0's output."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    captured_prompts: list[str] = []
    call_idx = [0]

    def _respond(**kwargs):
        captured_prompts.append(kwargs.get("prompt", ""))
        out = "stage0-result" if call_idx[0] == 0 else "stage1-result"
        call_idx[0] += 1
        return {"success": True, "output": out}

    mock_subagent.side_effect = _respond

    r = _run_pipeline(
        items=["item_x"],
        stages=[
            {"prompt_template": "find issues in {item}"},
            {"prompt_template": "fix this: {prev} for {item}"},
        ],
    )

    assert r["ok"] is True
    assert len(captured_prompts) == 2
    assert "stage0-result" in captured_prompts[1]
    assert "item_x" in captured_prompts[1]


def test_stage0_output_placeholder(mock_subagent, unlimited_budget, mock_builtins):
    """{stage0_output} is available in stage 1 and beyond."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    call_count = [0]

    def _respond(**kwargs):
        call_count[0] += 1
        prompt = kwargs.get("prompt", "")
        if call_count[0] == 1:
            return {"success": True, "output": "first-output"}
        # stage 1 prompt should contain stage0_output value
        assert "first-output" in prompt, f"Expected stage0_output in: {prompt}"
        return {"success": True, "output": "second-output"}

    mock_subagent.side_effect = _respond

    r = _run_pipeline(
        items=["x"],
        stages=[
            {"prompt_template": "stage0: {item}"},
            {"prompt_template": "stage1 sees {stage0_output}"},
        ],
    )
    assert r["ok"] is True
    assert r["results"][0]["stages"][1]["output"] == "second-output"


# ── Concurrency and ordering ───────────────────────────────────────────────


def test_multiple_items_all_processed(mock_subagent, unlimited_budget, mock_builtins):
    """All items run and results are returned in original order."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    mock_subagent.return_value = {"success": True, "output": "ok"}

    items = ["a.py", "b.py", "c.py"]
    r = _run_pipeline(
        items=items,
        stages=[{"prompt_template": "review {item}"}],
    )

    assert r["ok"] is True
    assert r["total"] == 3
    assert r["success_count"] == 3
    assert [res["item"] for res in r["results"]] == items


def test_results_in_original_order(mock_subagent, unlimited_budget, mock_builtins):
    """Results preserve original item order even with concurrent execution."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    # Stagger so items finish in reverse order (c finishes first)
    delays = {"a.py": 0.05, "b.py": 0.02, "c.py": 0.0}

    def _stagger(**kwargs):
        import time

        prompt = kwargs.get("prompt", "")
        for name, delay in delays.items():
            if name in prompt:
                time.sleep(delay)
                return {"success": True, "output": f"done-{name}"}
        return {"success": True, "output": "done"}

    mock_subagent.side_effect = _stagger

    items = ["a.py", "b.py", "c.py"]
    r = _run_pipeline(
        items=items,
        stages=[{"prompt_template": "review {item}"}],
    )

    assert [res["item"] for res in r["results"]] == items


# ── Stage failure isolation ────────────────────────────────────────────────


def test_stage_failure_halts_item_chain(mock_subagent, unlimited_budget, mock_builtins):
    """A failing stage marks item as failed and skips subsequent stages."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    mock_subagent.return_value = {"success": False, "output": "", "error": "boom"}

    r = _run_pipeline(
        items=["x.py"],
        stages=[
            {"prompt_template": "stage0: {item}"},
            {"prompt_template": "stage1: {prev}"},
            {"prompt_template": "stage2: {prev}"},
        ],
    )

    assert r["ok"] is False
    assert r["success_count"] == 0
    item_result = r["results"][0]
    assert item_result["ok"] is False
    # Stage 0 failed; stages 1 and 2 should be skipped
    assert item_result["stages"][0]["ok"] is False
    assert item_result["stages"][1].get("skipped") is True
    assert item_result["stages"][2].get("skipped") is True


def test_one_item_failure_does_not_stop_others(mock_subagent, unlimited_budget, mock_builtins):
    """Item A failing doesn't affect item B."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    def _respond(**kwargs):
        prompt = kwargs.get("prompt", "")
        if "bad.py" in prompt:
            return {"success": False, "output": "", "error": "broken"}
        return {"success": True, "output": "good"}

    mock_subagent.side_effect = _respond

    r = _run_pipeline(
        items=["good.py", "bad.py"],
        stages=[{"prompt_template": "review {item}"}],
    )

    assert r["success_count"] == 1
    assert r["failure_count"] == 1
    assert r["ok"] is True  # at least one succeeded


# ── Budget gate ────────────────────────────────────────────────────────────


def test_budget_exhausted_returns_error():
    """When the per-turn delegation cap is exhausted, run_pipeline fails closed."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    with patch(
        "runtime.execution.suckers.delegation_skills._check_absolute_cap",
        return_value=(5, False),
    ):
        r = _run_pipeline(
            items=["a.py"],
            stages=[{"prompt_template": "review {item}"}],
        )

    assert r["ok"] is False
    assert "budget exhausted" in r["error"]


# ── Clamping ───────────────────────────────────────────────────────────────


def test_items_clamped_to_16(mock_subagent, unlimited_budget, mock_builtins):
    """Items list is silently clamped to _PIPELINE_MAX_ITEMS (16)."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    mock_subagent.return_value = {"success": True, "output": "ok"}

    r = _run_pipeline(
        items=[f"f{i}.py" for i in range(20)],
        stages=[{"prompt_template": "review {item}"}],
    )

    assert r["total"] == 16


def test_stages_clamped_to_4(mock_subagent, unlimited_budget, mock_builtins):
    """Stages list is silently clamped to _PIPELINE_MAX_STAGES (4)."""
    from runtime.execution.suckers.delegation_skills import _run_pipeline

    mock_subagent.return_value = {"success": True, "output": "ok"}

    r = _run_pipeline(
        items=["x.py"],
        stages=[{"prompt_template": f"stage{i}: {{item}}"} for i in range(6)],
    )

    assert r["stages_run"] == 4


# ── Registration count ─────────────────────────────────────────────────────


def test_register_delegation_skills_returns_8():
    """register_delegation_skills exposes only the eight native tools.

    call_agent, call_agent_parallel, call_agent_vote, run_orchestration,
    verdict_repair, tournament, run_pipeline, call_agent_graph. External CLI
    auto-detection is intentionally not a registered capability.
    """

    from runtime.execution.suckers.delegation_skills import register_delegation_skills

    registry = MagicMock()
    count = register_delegation_skills(registry)
    assert count == 8
    assert registry.register.call_count == 8

