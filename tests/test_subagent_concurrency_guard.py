"""Global concurrency guard on subagent spawns.

Each ``call_subagent`` holds a slot for the whole time its child runs, so a
parent→child→grandchild chain holds one slot PER LEVEL. A single global cap
therefore bounds both the depth and the width of the live subagent tree — the
runaway-tree resource/cost vector (a buggy or malicious agent recursively
spawning subagents, each with its own per-task budget).
"""

from __future__ import annotations

from typing import Any

import pytest
from runtime.execution.subagents import bridge


@pytest.fixture(autouse=True)
def _reset_slots():
    # Defensive: ensure a clean counter even if a prior test leaked.
    while bridge.active_subagent_count() > 0:
        bridge._release_subagent_slot()
    yield
    while bridge.active_subagent_count() > 0:
        bridge._release_subagent_slot()


def test_slot_acquire_release_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "MAX_ACTIVE_SUBAGENTS", 2)
    assert bridge._acquire_subagent_slot() is True
    assert bridge._acquire_subagent_slot() is True
    assert bridge.active_subagent_count() == 2
    assert bridge._acquire_subagent_slot() is False  # cap reached
    bridge._release_subagent_slot()
    assert bridge._acquire_subagent_slot() is True  # slot freed
    bridge._release_subagent_slot()
    bridge._release_subagent_slot()
    assert bridge.active_subagent_count() == 0
    bridge._release_subagent_slot()  # never goes negative
    assert bridge.active_subagent_count() == 0


def test_nested_spawn_refused_when_cap_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """With cap=1, the outer subagent holds the only slot while it runs, so a
    nested spawn it attempts is refused fail-closed (not silently allowed)."""
    monkeypatch.setattr(bridge, "MAX_ACTIVE_SUBAGENTS", 1)
    captured: dict[str, Any] = {}

    def _runner(prompt: str, *, subagent_name: str, context: Any) -> str:  # noqa: ARG001
        # While this (the only) slot is held, a nested spawn must be refused.
        captured["nested"] = bridge.call_subagent(
            agent_id="custom_child",
            role="researcher",
            prompt="nested",
        )
        captured["count_inside"] = bridge.active_subagent_count()
        return "outer-done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        outer = bridge.call_subagent(
            agent_id="custom_parent",
            role="researcher",
            prompt="outer",
        )
    finally:
        bridge._RUNNER = orig

    # Outer ran (held the slot); nested was refused at the cap.
    assert outer.get("output") == "outer-done" or outer.get("success")
    assert captured["count_inside"] == 1
    nested = captured["nested"]
    assert nested["status"] == "rejected"
    assert "concurrency cap" in nested["error"]
    assert nested["success"] is False
    # Both slots released after return.
    assert bridge.active_subagent_count() == 0


def test_default_cap_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_MAX_ACTIVE_SUBAGENTS", "7")
    assert bridge._default_max_active_subagents() == 7
    monkeypatch.setenv("ECHO_MAX_ACTIVE_SUBAGENTS", "0")  # invalid → default
    assert bridge._default_max_active_subagents() == 64
    monkeypatch.setenv("ECHO_MAX_ACTIVE_SUBAGENTS", "nope")  # non-int → default
    assert bridge._default_max_active_subagents() == 64
    monkeypatch.delenv("ECHO_MAX_ACTIVE_SUBAGENTS", raising=False)
    assert bridge._default_max_active_subagents() == 64
