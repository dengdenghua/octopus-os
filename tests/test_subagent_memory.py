"""Regression tests for thread-scoped subagent memory.

These tests replace the old manual smoke script with deterministic unit
coverage for the Codex-parity behavior we care about: a repeated subagent call
in the same thread can continue from its own prior output, while isolation
controls still keep unrelated roles or opt-out calls clean.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_subagent_memory_and_runner():
    from runtime.execution.subagents import bridge
    from runtime.execution.subagents.memory import clear_history
    from runtime.execution.suckers import ephemeral_agents

    orig_bridge_runner = bridge._RUNNER
    orig_ephemeral_runner = ephemeral_agents._EPHEMERAL_RUNNER
    clear_history()
    yield
    clear_history()
    bridge._RUNNER = orig_bridge_runner
    ephemeral_agents._EPHEMERAL_RUNNER = orig_ephemeral_runner


def test_recorded_turns_render_as_prior_context() -> None:
    from runtime.execution.subagents.memory import (
        recent_turns,
        recent_turns_prompt,
        record_turn,
    )

    record_turn(
        thread_id="thread-a",
        role_id="researcher",
        prompt="Find the candidate patents.",
        output="Candidate Alpha is the strongest prior-art match.",
        success=True,
        rounds=2,
    )

    turns = recent_turns("thread-a", "researcher")
    assert len(turns) == 1
    assert turns[0].prompt == "Find the candidate patents."

    rendered = recent_turns_prompt("thread-a", "researcher")
    assert "Prior turns in this thread" in rendered
    assert "Candidate Alpha is the strongest prior-art match." in rendered
    assert "Now: the user's NEW request" in rendered


def test_call_subagent_records_then_injects_same_role_history() -> None:
    from runtime.execution.subagents import bridge
    from runtime.execution.suckers import ephemeral_agents

    composed_prompts: list[str] = []

    def _runner(call):
        composed_prompts.append(call.composed_system_prompt)
        if len(composed_prompts) == 1:
            return "First result: Candidate Alpha was filed in 2024."
        return "Follow-up result: Candidate Alpha, 2024."

    ephemeral_agents.set_ephemeral_role_runner(_runner)

    first = bridge.call_subagent(
        "researcher",
        "Find the strongest candidate.",
        context={"thread_id": "thread-memory-a"},
    )
    second = bridge.call_subagent(
        "researcher",
        "Continue from that candidate and give the filing year.",
        context={"thread_id": "thread-memory-a"},
    )

    assert first["success"] is True
    assert second["success"] is True
    assert len(composed_prompts) == 2
    assert "Prior turns in this thread" not in composed_prompts[0]
    assert "Prior turns in this thread" in composed_prompts[1]
    assert "Find the strongest candidate." in composed_prompts[1]
    assert "First result: Candidate Alpha was filed in 2024." in composed_prompts[1]


def test_share_history_false_disables_prior_turn_injection() -> None:
    from runtime.execution.subagents import bridge
    from runtime.execution.suckers import ephemeral_agents

    composed_prompts: list[str] = []

    def _runner(call):
        composed_prompts.append(call.composed_system_prompt)
        return "ok"

    ephemeral_agents.set_ephemeral_role_runner(_runner)

    bridge.call_subagent(
        "researcher",
        "Remember Alpha.",
        context={"thread_id": "thread-memory-b"},
    )
    bridge.call_subagent(
        "researcher",
        "Ignore history for this call.",
        context={"thread_id": "thread-memory-b", "share_history": False},
    )

    assert len(composed_prompts) == 2
    assert "Prior turns in this thread" not in composed_prompts[1]
    assert "Remember Alpha." not in composed_prompts[1]


def test_subagent_memory_is_scoped_by_role() -> None:
    from runtime.execution.subagents import bridge
    from runtime.execution.suckers import ephemeral_agents

    prompts_by_role: dict[str, str] = {}

    def _runner(call):
        prompts_by_role[call.role.id] = call.composed_system_prompt
        return f"reply from {call.role.id}"

    ephemeral_agents.set_ephemeral_role_runner(_runner)

    bridge.call_subagent(
        "researcher",
        "Research Alpha.",
        context={"thread_id": "thread-memory-c"},
    )
    bridge.call_subagent(
        "reviewer",
        "Review without researcher history.",
        context={"thread_id": "thread-memory-c"},
    )

    assert "Prior turns in this thread" not in prompts_by_role["reviewer"]
    assert "Research Alpha." not in prompts_by_role["reviewer"]


def test_subagent_output_can_be_queued_as_trace_linked_review_candidate(tmp_path) -> None:
    from runtime.memory.learning.review_queue import ReviewQueue
    from runtime.memory.learning.subagent_review import queue_subagent_review_candidate

    path = tmp_path / "review_queue.json"
    result = queue_subagent_review_candidate(
        agent_id="researcher",
        role="researcher",
        prompt="Find reusable evidence.",
        result={
            "success": True,
            "output": "Reusable finding: prefer replay-backed promotion evidence.",
            "iteration_count": 4,
            "files_touched": ["docs/evidence.md"],
        },
        context={
            "task_id": "task-1",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
        },
        review_queue_path=path,
    )

    assert result["queued"] is True
    rows = ReviewQueue(path).items()["items"]
    assert len(rows) == 1
    item = rows[0]
    assert item["candidate_kind"] == "subagent_output"
    assert item["priority"] == "P1"
    assert item["source_task_ids"] == ["task-1"]
    assert item["thread_ids"] == ["thread-1"]
    assert item["turn_ids"] == ["turn-1"]
    assert item["agent_ids"] == ["researcher"]
    assert item["metadata"]["candidate"]["subagent"]["files_touched"] == [
        "docs/evidence.md",
    ]


def test_call_subagent_queues_review_candidate_when_trace_context_exists(tmp_path) -> None:
    from runtime.execution.subagents import bridge
    from runtime.execution.suckers import ephemeral_agents
    from runtime.memory.learning.review_queue import ReviewQueue
    from runtime.platform.process.session import Session

    path = tmp_path / "review_queue.json"

    def _runner(call):
        return "Use replay-gate evidence before promoting learned behavior."

    ephemeral_agents.set_ephemeral_role_runner(_runner)

    result = bridge.call_subagent(
        "researcher",
        "Derive a reusable promotion rule.",
        context={"review_queue_path": str(path)},
        session=Session(
            thread_id="thread-2",
            turn_id="turn-2",
            metadata={"task_id": "task-2"},
        ),
    )

    assert result["success"] is True
    assert result["review_candidate"]["queued"] is True

    rows = ReviewQueue(path).items(source_task_id="task-2")["items"]
    assert len(rows) == 1
    assert rows[0]["candidate_kind"] == "subagent_output"
    assert rows[0]["source_task_ids"] == ["task-2"]
    assert rows[0]["thread_ids"] == ["thread-2"]
    assert rows[0]["turn_ids"] == ["turn-2"]


def test_subagent_review_candidate_requires_trace_anchor(tmp_path) -> None:
    from runtime.memory.learning.subagent_review import queue_subagent_review_candidate

    result = queue_subagent_review_candidate(
        agent_id="researcher",
        role="researcher",
        prompt="No trace.",
        result={"success": True, "output": "Useful, but unauditable."},
        context={"thread_id": "thread-only"},
        review_queue_path=tmp_path / "review_queue.json",
    )

    assert result == {"queued": False, "reason": "missing_trace_anchor"}

