"""Durable, cross-process SQLite-backed blackboard."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.runtime_state.blackboard import Blackboard, get_blackboard
from runtime.memory.runtime_state.blackboard_store import (
    SqliteBlackboard,
    reset_sqlite_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    from runtime.execution.suckers import delegation_budget as dbud

    dbud._TURN_DELEGATIONS.clear()
    reset_sqlite_cache_for_tests()
    yield
    dbud._TURN_DELEGATIONS.clear()
    reset_sqlite_cache_for_tests()


def test_write_read_keys_snapshot(tmp_path: Path) -> None:
    bb = SqliteBlackboard(tmp_path / "bb.db", "turn-1")
    bb.write("k", {"a": 1}, writer="lead")
    assert bb.read("k") == {"a": 1}
    assert bb.read("missing", "def") == "def"
    assert bb.keys() == ["k"]
    assert bb.snapshot() == {"k": {"a": 1}}


def test_cross_connection_sharing(tmp_path: Path) -> None:
    # two SEPARATE connections to the same file + turn = two processes
    db = tmp_path / "shared.db"
    writer = SqliteBlackboard(db, "turn-x")
    reader = SqliteBlackboard(db, "turn-x")
    writer.write("finding", "SQL injection", writer="proc-a")
    assert reader.read("finding") == "SQL injection"  # seen across "processes"
    writer.write("finding2", "TOCTOU", writer="proc-a")
    assert set(reader.keys()) == {"finding", "finding2"}


def test_turn_namespacing_in_one_file(tmp_path: Path) -> None:
    db = tmp_path / "ns.db"
    a = SqliteBlackboard(db, "turn-a")
    b = SqliteBlackboard(db, "turn-b")
    a.write("k", "va")
    b.write("k", "vb")
    assert a.read("k") == "va"
    assert b.read("k") == "vb"  # isolated namespaces in one file


def test_overwrite_and_writers_audit(tmp_path: Path) -> None:
    bb = SqliteBlackboard(tmp_path / "a.db", "t")
    bb.write("k", "v1", writer="a")
    bb.write("k", "v2", writer="b")  # overwrite, second writer
    assert bb.read("k") == "v2"
    audit = bb.audit()
    assert audit["key_count"] == 1
    assert audit["overwrite_count"] >= 1
    assert "k" in audit["contested_keys"]  # two distinct writers
    assert set(audit["writers_by_key"]["k"]) == {"a", "b"}
    assert audit["durable"] is True


def test_get_blackboard_durable_when_env_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_BLACKBOARD_DB", str(tmp_path / "g.db"))
    bb = get_blackboard("turn-z")
    assert isinstance(bb, SqliteBlackboard)
    bb.write("k", "v", writer="x")
    assert get_blackboard("turn-z").read("k") == "v"


def test_get_blackboard_in_memory_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_BLACKBOARD_DB", raising=False)
    assert isinstance(get_blackboard("turn-mem"), Blackboard)


def test_cross_process_orchestration_coordination(tmp_path: Path, monkeypatch) -> None:
    # The exceed: two orchestrations in two DIFFERENT processes (simulated by
    # resetting the connection cache = a restart) coordinate via the DB file.
    monkeypatch.setenv("ECHO_BLACKBOARD_DB", str(tmp_path / "orch.db"))
    import runtime.execution.suckers.delegation_skills as ds
    from tests.test_run_orchestration import _fake_parallel_seq

    turn = "shared-turn"
    monkeypatch.setattr(ds, "_resolve_session_and_turn", lambda: (None, turn))

    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["finding-from-proc-A"]]),
    )
    r1 = ds._run_orchestration(goal="audit", n=1, rounds=1, patience=2)
    assert r1["shared"] is True

    # second "process": drop the connection cache (restart), same DB file
    reset_sqlite_cache_for_tests()
    monkeypatch.setattr(
        ds,
        "_call_agent_parallel",
        _fake_parallel_seq([["finding-from-proc-B"]]),
    )
    r2 = ds._run_orchestration(goal="audit", n=1, rounds=1, patience=2)
    assert r2["inherited"] == 1  # inherited proc-A's finding via the DB file
    assert "finding-from-proc-A" in r2["collected"]
    assert "finding-from-proc-B" in r2["collected"]

