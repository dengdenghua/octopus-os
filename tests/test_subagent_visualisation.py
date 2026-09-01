"""Tests for sub-agent visualisation: codenames, role avatars, and
``subagent_spawned`` / ``subagent_finished`` lifecycle events.
"""

from __future__ import annotations

from typing import Any

from runtime.execution.subagents import bridge


def _restore_runner(orig):
    bridge._RUNNER = orig


def test_codename_format() -> None:
    name = bridge._codename_for_role("researcher")
    # "<Word>-<3-hex>"
    assert "-" in name
    word, suffix = name.split("-", 1)
    assert word in bridge._CODENAME_POOL
    assert len(suffix) == 3
    assert all(c in "0123456789abcdef" for c in suffix)


def test_codename_uniqueness_per_call() -> None:
    """Suffix random → vanishingly small collision rate even for
    a few-hundred-call burst (3 hex = 4096 codes)."""
    seen = set()
    for _ in range(200):
        seen.add(bridge._codename_for_role("x"))
    # Allow collisions but not catastrophic (suffix entropy is small).
    assert len(seen) >= 150


def test_avatar_picks_role_emoji() -> None:
    assert bridge._avatar_for_role("researcher") == "🔍"
    assert bridge._avatar_for_role("Researcher") == "🔍"  # case insensitive
    assert bridge._avatar_for_role("CRITIC") == "🛡️"
    assert bridge._avatar_for_role("synthesizer") == "✍️"
    assert bridge._avatar_for_role("architect") == "🏗️"


def test_avatar_falls_back_to_echo_for_unknown() -> None:
    assert bridge._avatar_for_role("unknown_role") == "🐙"
    assert bridge._avatar_for_role("") == "🐙"
    assert bridge._avatar_for_role(None) == "🐙"  # type: ignore[arg-type]


def test_spawn_event_emitted_before_runner_runs() -> None:
    """``subagent_spawned`` must be emitted BEFORE the runner is
    invoked so the frontend can render a card from the very start."""
    received: list[dict[str, Any]] = []
    runner_call_idx = {"value": -1}

    def _emitter(event: dict) -> None:
        received.append(event)

    def _runner(prompt, *, subagent_name, context):
        runner_call_idx["value"] = len(received)
        return "ok"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_explorer",  # not an ephemeral built-in
            role="researcher",  # role drives avatar
            prompt="explore vendor X",
            event_emitter=_emitter,
        )
        # spawn event must precede runner invocation
        assert runner_call_idx["value"] >= 1
        assert received[0]["type"] == "subagent_spawned"
        assert received[0]["role"] == "researcher"
        assert received[0]["avatar"] == "🔍"
        assert received[0]["codename"]
        assert received[0]["prompt_preview"].startswith("explore")
    finally:
        _restore_runner(orig)


def test_finish_event_emitted_with_stats() -> None:
    received: list[dict[str, Any]] = []

    def _runner(prompt, *, subagent_name, context):
        emitter = context.get("event_emitter")
        emitter(
            {
                "type": "sub_tool_end",
                "skill": "edit_file",
                "args": {"path": "a.py"},
                "status": "success",
                "round": 1,
            }
        )
        emitter(
            {
                "type": "sub_tool_end",
                "skill": "edit_file",
                "args": {"path": "b.py"},
                "status": "success",
                "round": 2,
            }
        )
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_implementer",
            role="implementer",
            prompt="apply patches",
            event_emitter=lambda e: received.append(e),
        )
        finish = next(e for e in received if e["type"] == "subagent_finished")
        assert finish["role"] == "implementer"
        assert finish["avatar"] == "🔧"
        assert finish["ok"] is True
        assert finish["iteration_count"] >= 2
        assert sorted(finish["files_touched"]) == ["a.py", "b.py"]
        assert finish["duration_s"] >= 0
    finally:
        _restore_runner(orig)


def test_finish_event_marks_failure() -> None:
    received: list[dict[str, Any]] = []

    def _runner(prompt, *, subagent_name, context):
        raise RuntimeError("boom")

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_researcher",
            role="researcher",
            prompt="x",
            event_emitter=lambda e: received.append(e),
        )
        finish = next(e for e in received if e["type"] == "subagent_finished")
        assert finish["ok"] is False
        assert "boom" in (finish.get("error") or "")
    finally:
        _restore_runner(orig)


def test_tool_events_get_subagent_annotation() -> None:
    """Per-tool events emitted by the runner are annotated with the
    parent sub-agent's codename + avatar so the frontend timeline
    can group them under the same tile."""
    received: list[dict[str, Any]] = []

    def _runner(prompt, *, subagent_name, context):
        emitter = context.get("event_emitter")
        emitter({"type": "sub_tool_start", "skill": "read_file", "args": {"path": "x"}, "round": 1})
        emitter(
            {
                "type": "sub_tool_end",
                "skill": "read_file",
                "args": {"path": "x"},
                "status": "success",
                "round": 1,
            }
        )
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        bridge.call_subagent(
            agent_id="custom_researcher",
            role="researcher",
            prompt="x",
            event_emitter=lambda e: received.append(dict(e)),
        )
        # spawn + 2 tool events + finish = 4
        tool_starts = [e for e in received if e.get("type") == "sub_tool_start"]
        assert len(tool_starts) == 1
        assert tool_starts[0].get("subagent_codename")
        assert tool_starts[0].get("subagent_avatar") == "🔍"
        assert tool_starts[0].get("agent_id") == "custom_researcher"
    finally:
        _restore_runner(orig)


def test_result_envelope_carries_codename() -> None:
    """``call_subagent`` return dict includes codename / avatar / role
    so synchronous tests + transcripts can correlate without re-
    parsing the event stream."""

    def _runner(prompt, *, subagent_name, context):
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        result = bridge.call_subagent(
            agent_id="custom_critic",
            role="critic",
            prompt="review",
        )
        assert result["role"] == "critic"
        assert result["avatar"] == "🛡️"
        assert result["codename"]
    finally:
        _restore_runner(orig)
