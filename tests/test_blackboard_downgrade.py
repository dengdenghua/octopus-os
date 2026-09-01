"""Tests for the blackboard downgrade: coordination-scope resolution,
write guards (claim/pin), and the large-value gate."""

from __future__ import annotations

import pytest

from runtime.execution.suckers.blackboard_skills import (
    _bb_claim,
    _bb_keys,
    _bb_pin,
    _bb_save,
    _bb_write,
)
from runtime.memory.runtime_state.blackboard import Blackboard, get_blackboard, reset_for_tests
from runtime.platform.process.session import Session, session_scope


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


# ── scope resolution ────────────────────────────────────────


def test_scope_prefers_root_thread_id_from_metadata():
    with session_scope(
        Session(
            actor="alice", thread_id="child", turn_id="turn-x", metadata={"root_thread_id": "ROOT"}
        )
    ):
        r = _bb_write("decision", "go")
        assert r["ok"] is True
        assert get_blackboard("ROOT").read("decision") == "go"
        # legacy turn-scoped board is untouched
        assert get_blackboard("turn-x").read("decision") is None


def test_scope_falls_back_to_turn_id():
    with session_scope(Session(actor="alice", thread_id="t", turn_id="turn-y")):
        _bb_write("k", "v")
        assert get_blackboard("turn-y").read("k") == "v"


def test_scope_prefers_blackboard_root_turn_id_over_root_thread_id():
    # A threaded child carries BOTH roots: ``root_thread_id`` (event bus) and
    # ``blackboard_root_turn_id`` (shared parent board). The board must use
    # the turn id so it stays continuous with the parent, not the thread id.
    with session_scope(
        Session(
            actor="alice",
            thread_id="child",
            turn_id="child-turn",
            metadata={
                "root_thread_id": "thread-root",
                "blackboard_root_turn_id": "parent-turn",
            },
        )
    ):
        r = _bb_write("decision", "go")
        assert r["ok"] is True
        assert get_blackboard("parent-turn").read("decision") == "go"
        # neither the child turn nor the thread root boards receive it
        assert get_blackboard("child-turn").read("decision") is None
        assert get_blackboard("thread-root").read("decision") is None


# ── large-value gate ────────────────────────────────────────


def test_large_value_rejected():
    with session_scope(Session(actor="alice", turn_id="turn-z")):
        r = _bb_write("big", {"blob": "x" * 9000})
        assert r["ok"] is False
        assert "file artifact" in r["error"]


def test_small_value_accepted():
    with session_scope(Session(actor="alice", turn_id="turn-z")):
        r = _bb_write("small", {"decision": "go", "owner": "alice"})
        assert r["ok"] is True
        assert get_blackboard("turn-z").read("small")["decision"] == "go"


# ── claim / pin via skills (single writer) ──────────────────


def test_claim_then_write_ok_and_readable():
    with session_scope(
        Session(actor="alice", turn_id="turn-c", metadata={"root_thread_id": "ROOT"})
    ):
        c = _bb_claim("owner")
        assert c["ok"] is True
        w = _bb_write("owner", "alice")
        assert w["ok"] is True
        assert get_blackboard("ROOT").read("owner") == "alice"
        assert get_blackboard("ROOT").audit()["claimed_keys"]["owner"] == "alice"


def test_pin_seals_key():
    with session_scope(
        Session(actor="alice", turn_id="turn-p", metadata={"root_thread_id": "ROOT"})
    ):
        _bb_write("decision", "v1")
        p = _bb_pin("decision")
        assert p["ok"] is True
        w = _bb_write("decision", "v2")
        assert w["ok"] is False
        assert "pinned" in w["error"]
        assert get_blackboard("ROOT").read("decision") == "v1"
        assert "decision" in get_blackboard("ROOT").audit()["pinned_keys"]


def test_pin_missing_key_fails():
    with session_scope(Session(actor="alice", turn_id="turn-p")):
        p = _bb_pin("nope")
        assert p["ok"] is False


# ── two-writer enforcement at the board level ───────────────


def test_claimed_slot_blocks_other_writer():
    bb = Blackboard()
    assert bb.claim("owner", "alice") == (True, "")
    # bob can't take an already-claimed slot
    assert bb.claim("owner", "bob") == (False, "claimed_by:alice")
    # alice writes fine
    assert bb.can_write("owner", "alice") == (True, "")
    bb.write("owner", "alice", writer="alice")
    # bob's write is guarded
    assert bb.can_write("owner", "bob") == (False, "claimed_by:alice")


def test_claim_by_same_writer_is_idempotent():
    bb = Blackboard()
    assert bb.claim("slot", "alice") == (True, "")
    assert bb.claim("slot", "alice") == (True, "")


def test_pinned_blocks_all_writers():
    bb = Blackboard()
    bb.write("k", "v")
    assert bb.pin("k") == (True, "")
    assert bb.can_write("k", "alice") == (False, "pinned")
    assert bb.can_write("k", None) == (False, "pinned")


# ── skills surface ──────────────────────────────────────────


def test_bb_keys_reports_claims_and_pins():
    with session_scope(
        Session(actor="alice", turn_id="turn-keys", metadata={"root_thread_id": "ROOT"})
    ):
        _bb_write("decision", "go")
        _bb_claim("owner")
        _bb_pin("decision")
        r = _bb_keys()
        assert r["ok"] is True
        assert set(r["keys"]) == {"decision", "owner"}
        assert r["audit"]["claimed_keys"]["owner"] == "alice"
        assert "decision" in r["audit"]["pinned_keys"]


# ── bb_save: large payloads go to file artifacts ────────────


def test_bb_save_writes_artifact_and_returns_reference(tmp_path):
    from runtime.execution.subagents.artifacts import read_artifact

    with session_scope(
        Session(
            actor="alice",
            thread_id="child",
            turn_id="turn-s",
            metadata={"root_thread_id": "ROOT", "workspace_path": str(tmp_path)},
        )
    ):
        # large value that bb_write would reject
        r = _bb_save("big_report", {"blob": "x" * 9000})
        assert r["ok"] is True
        ref = r["artifact"]
        assert ref["path"].startswith(str(tmp_path / ".echo" / "artifacts" / "ROOT"))
        assert ref["hash"]
        assert r["size_bytes"] > 9000

        back = read_artifact(ref["path"])
        assert back["ok"] is True
        assert "x" * 9000 in back["content"]


def test_bb_write_large_value_mentions_bb_save():
    with session_scope(Session(actor="alice", turn_id="turn-z")):
        r = _bb_write("big", {"blob": "x" * 9000})
        assert r["ok"] is False
        assert "bb_save" in r["error"]


def test_bb_save_empty_key_fails():
    with session_scope(Session(actor="alice", turn_id="turn-s")):
        r = _bb_save("", {"x": 1})
        assert r["ok"] is False


