"""Tests for the sub-agent threading foundation (lineage + decoupled roots)."""

from __future__ import annotations

from runtime.execution.subagents.threading import (
    bind_subagent_session,
    forge_subagent_thread,
)
from runtime.platform.process.session import Session


def test_forge_roots_parent_is_lineage_root():
    sess = Session(thread_id="thread-1", turn_id="turn-1")
    b = forge_subagent_thread(sess, agent_id="r", role="researcher", persist=False)
    assert b.parent_thread_id == "thread-1"
    assert b.root_thread_id == "thread-1"
    assert b.parent_turn_id == "turn-1"
    assert b.child_thread_id
    assert b.persisted is False


def test_forge_inherits_root_from_metadata_for_grandchild():
    sess = Session(
        thread_id="child",
        turn_id="turn-child",
        metadata={"root_thread_id": "thread-root"},
    )
    b = forge_subagent_thread(sess, role="explorer", persist=False)
    assert b.parent_thread_id == "child"
    assert b.root_thread_id == "thread-root"
    assert b.parent_turn_id == "turn-child"


def test_forge_no_session_yields_empty_parent():
    b = forge_subagent_thread(None, persist=False)
    assert b.parent_thread_id == ""
    assert b.root_thread_id == ""
    assert b.parent_turn_id == ""
    assert b.to_metadata() == {}


def test_bind_stamps_roots_and_keeps_parent_thread_by_default():
    sess = Session(thread_id="thread-1", turn_id="turn-1")
    b = forge_subagent_thread(sess, role="researcher", persist=False)
    bound = bind_subagent_session(sess, b)
    assert bound.thread_id == "thread-1"  # trace attribution preserved
    assert bound.conversation_id == sess.conversation_id
    assert bound.turn_id == "turn-1"
    assert bound.metadata["root_thread_id"] == "thread-1"
    assert bound.metadata["parent_thread_id"] == "thread-1"
    assert bound.metadata["blackboard_root_turn_id"] == "turn-1"


def test_bind_flip_thread_id_gives_independent_identity():
    sess = Session(thread_id="thread-1", turn_id="turn-1")
    b = forge_subagent_thread(sess, role="researcher", persist=False)
    bound = bind_subagent_session(sess, b, flip_thread_id=True)
    assert bound.thread_id == b.child_thread_id
    assert bound.conversation_id == b.child_thread_id
    # blackboard + bus roots still point at the lineage root / parent turn
    assert bound.metadata["root_thread_id"] == "thread-1"
    assert bound.metadata["blackboard_root_turn_id"] == "turn-1"


def test_bind_merges_extra_metadata():
    sess = Session(thread_id="thread-1", turn_id="turn-1")
    b = forge_subagent_thread(sess, role="r", persist=False)
    bound = bind_subagent_session(sess, b, extra_metadata={"_locked_write_root": "/ws"})
    assert bound.metadata["_locked_write_root"] == "/ws"
    assert bound.metadata["root_thread_id"] == "thread-1"

