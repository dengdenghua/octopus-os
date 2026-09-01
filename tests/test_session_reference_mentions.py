"""Realtime host wiring for Echo session-reference mentions.

Covers the PHASE 3.5 mention-resolution helper in ``realtime_turn_lifecycle``
and the prompt-assembly injection of the referenced-sessions frame.
"""

from __future__ import annotations

from pathlib import Path

from runtime.execution.subagents.sessions import SubagentSessionStore
from runtime.execution.tool_engine.session_reference_uri import (
    format_session_reference_mention,
)
from runtime.sensing.gateway.realtime_turn_lifecycle import (
    _resolve_session_reference_mentions,
)


def _store_with_session(tmp_path: Path) -> tuple[SubagentSessionStore, str]:
    store = SubagentSessionStore(base_dir=tmp_path / "sessions")
    session = store.create(agent_id="researcher", thread_id="t1")
    store.append_turn(
        session.session_id,
        prompt="find the patents",
        output="patents summarized",
        success=True,
    )
    return store, session.session_id


def test_no_mention_passthrough(tmp_path: Path) -> None:
    store, _sid = _store_with_session(tmp_path)
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        text, frame = _resolve_session_reference_mentions("hello world", "t1")
        assert text == "hello world"
        assert frame is None
        text, frame = _resolve_session_reference_mentions("", "t1")
        assert text == ""
        assert frame is None
    finally:
        sessions_module.get_subagent_session_store = original


def test_legacy_mention_resolves(tmp_path: Path) -> None:
    store, sid = _store_with_session(tmp_path)
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        text, frame = _resolve_session_reference_mentions(
            f"research @session:{sid} deeper",
            "t1",
        )
        assert "@session:" not in text
        assert "research" in text and "deeper" in text
        assert frame is not None
        assert "## Referenced sessions" in frame
        assert "find the patents" in frame
        assert "patents summarized" in frame
    finally:
        sessions_module.get_subagent_session_store = original


def test_canonical_mention_resolves(tmp_path: Path) -> None:
    store, sid = _store_with_session(tmp_path)
    mention = format_session_reference_mention(sid, "Researcher")
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        text, frame = _resolve_session_reference_mentions(
            f"see {mention} please",
            "t1",
        )
        assert text == "see @Researcher please"
        assert frame is not None
        assert "## Referenced sessions" in frame
        assert "echo-session:" not in text
        assert "dsh-session:" not in text
    finally:
        sessions_module.get_subagent_session_store = original


def test_historical_dsh_mention_still_resolves(tmp_path: Path) -> None:
    store, sid = _store_with_session(tmp_path)
    mention = format_session_reference_mention(sid, "Researcher").replace(
        "echo-session:",
        "dsh-session:",
    )
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        text, frame = _resolve_session_reference_mentions(f"see {mention} please", "t1")
        assert text == "see @Researcher please"
        assert frame is not None
        assert "find the patents" in frame
    finally:
        sessions_module.get_subagent_session_store = original


def test_stale_mention_unchanged(tmp_path: Path) -> None:
    store, _sid = _store_with_session(tmp_path)
    stale = "deadbeefdeadbeefdeadbeefdeadbeef"
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        prompt = f"research @session:{stale} deeper"
        text, frame = _resolve_session_reference_mentions(prompt, "t1")
        # Stale mentions are skipped (no frame) but the host token is still
        # stripped so the model never sees unresolved plumbing.
        assert text == "research deeper"
        assert frame is None
    finally:
        sessions_module.get_subagent_session_store = original


def test_malformed_mention_unchanged(tmp_path: Path) -> None:
    store, _sid = _store_with_session(tmp_path)
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: store
    try:
        prompt = "research @session:nothex deeper"
        text, frame = _resolve_session_reference_mentions(prompt, "t1")
        assert text == prompt
        assert frame is None
    finally:
        sessions_module.get_subagent_session_store = original


def test_store_missing_unchanged(tmp_path: Path) -> None:
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    sessions_module.get_subagent_session_store = lambda: None
    try:
        prompt = "research @session:deadbeefdeadbeefdeadbeefdeadbeef deeper"
        text, frame = _resolve_session_reference_mentions(prompt, "t1")
        assert text == prompt
        assert frame is None
    finally:
        sessions_module.get_subagent_session_store = original


def test_resolution_failure_degrades(tmp_path: Path) -> None:
    store, sid = _store_with_session(tmp_path)
    import runtime.execution.subagents.sessions as sessions_module

    original = sessions_module.get_subagent_session_store
    original_resolve = store.resolve_session_mentions
    sessions_module.get_subagent_session_store = lambda: store
    store.resolve_session_mentions = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("store down")
    )
    try:
        prompt = f"research @session:{sid} deeper"
        text, frame = _resolve_session_reference_mentions(prompt, "t1")
        assert text == prompt
        assert frame is None
    finally:
        store.resolve_session_mentions = original_resolve
        sessions_module.get_subagent_session_store = original


def test_assembly_injects_frame_before_goal() -> None:
    from runtime.core.cerebrum._react_prompt_assembly_state import (
        _assemble_messages,
        _AssemblyState,
    )
    from runtime.platform.models.pipeline import ParsedIntent

    goal = "please do X"
    frame = (
        '## Referenced sessions\n\n<referenced-sessions>{"sessionId": "s1"}</referenced-sessions>'
    )
    intent = ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={"session_reference_context": frame},
    )
    state = _AssemblyState(
        intent=intent,
        agent=None,
        stack=None,
        executor=None,
        approval_provider=None,
        resume_task_id=None,
        planning_mode=False,
        tools_active=False,
        native_mode=False,
        no_tool_turn=False,
        strict_explicit_reads=False,
        camouflage_suffix="",
        max_iterations=1,
        max_tokens_budget=None,
        max_usd_budget=None,
        user_context={"session_reference_context": frame},
    )
    _assemble_messages(state)
    contents = [message.content for message in state.messages]
    assert frame in contents
    assert contents[-1] == goal
    assert contents.index(frame) < contents.index(goal)


def test_assembly_skips_empty_or_missing_frame() -> None:
    from runtime.core.cerebrum._react_prompt_assembly_state import (
        _assemble_messages,
        _AssemblyState,
    )
    from runtime.platform.models.pipeline import ParsedIntent

    goal = "please do X"
    intent = ParsedIntent(raw=goal, intent_type="task", normalized_goal=goal)
    state = _AssemblyState(
        intent=intent,
        agent=None,
        stack=None,
        executor=None,
        approval_provider=None,
        resume_task_id=None,
        planning_mode=False,
        tools_active=False,
        native_mode=False,
        no_tool_turn=False,
        strict_explicit_reads=False,
        camouflage_suffix="",
        max_iterations=1,
        max_tokens_budget=None,
        max_usd_budget=None,
        user_context={"session_reference_context": "   "},
    )
    _assemble_messages(state)
    contents = [message.content for message in state.messages]
    assert contents[-1] == goal
    assert "## Referenced sessions" not in "".join(str(c) for c in contents[:-1])

