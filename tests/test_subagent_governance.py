"""Durable cross-worker governance for delegated Agent turns."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from runtime.execution.subagents import bridge
from runtime.execution.subagents.governance import SubagentGovernanceStore
from runtime.platform.process.session import Session, session_scope


def test_root_limit_is_atomic_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "governance.sqlite3"
    first = SubagentGovernanceStore(path)
    second = SubagentGovernanceStore(path)

    lease = first.acquire("turn-a", depth=1, global_limit=8, root_limit=1)
    assert lease is not None
    assert second.acquire("turn-a", depth=1, global_limit=8, root_limit=1) is None
    assert first.snapshot("turn-a")["active_leases"] == 1

    assert second.release(lease["lease_id"]) is True
    assert first.acquire("turn-a", depth=1, global_limit=8, root_limit=1) is not None


def test_lease_renewal_extends_active_window_until_release(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_SUBAGENT_LEASE_SECONDS", "1")
    store = SubagentGovernanceStore(tmp_path / "governance.sqlite3")
    lease = store.acquire("turn-renew", depth=1, global_limit=8, root_limit=8)
    assert lease is not None

    assert store.renew(lease["lease_id"]) is True
    assert store.snapshot("turn-renew")["active_leases"] == 1
    assert store.release(lease["lease_id"]) is True
    assert store.renew(lease["lease_id"]) is False


def test_global_limit_is_visible_to_a_separate_process(tmp_path: Path) -> None:
    path = tmp_path / "governance.sqlite3"
    store = SubagentGovernanceStore(path)
    lease = store.acquire("turn-holder", depth=1, global_limit=1, root_limit=8)
    assert lease is not None

    script = """
from runtime.execution.subagents.governance import SubagentGovernanceStore
import sys
store = SubagentGovernanceStore(sys.argv[1])
print('accepted' if store.acquire('turn-child', depth=1, global_limit=1, root_limit=8) else 'rejected')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "rejected"
    assert store.release(lease["lease_id"]) is True


def test_usage_is_idempotent_and_trips_root_breaker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_MAX_SUBAGENT_TOKENS_PER_TURN", "100")
    path = tmp_path / "governance.sqlite3"
    store = SubagentGovernanceStore(path)

    first = store.record_usage(
        "turn-a",
        usage_id="usage-1",
        input_tokens=60,
        output_tokens=40,
        cost_usd=0.01,
        session_id="child-a",
        task_id="task-a",
        iteration=1,
        model="test-model",
    )
    duplicate = store.record_usage(
        "turn-a",
        usage_id="usage-1",
        input_tokens=60,
        output_tokens=40,
        cost_usd=0.01,
    )

    assert first["usage_recorded"] is True
    assert first["tokens_used"] == 100
    assert first["breaker"] == "tripped"
    assert first["trip_reason"] == "token_limit"
    assert duplicate["usage_recorded"] is False
    assert duplicate["tokens_used"] == 100
    assert store.acquire("turn-a", depth=1, global_limit=8, root_limit=8) is None


def test_lease_and_usage_tables_are_created(tmp_path: Path) -> None:
    path = tmp_path / "governance.sqlite3"
    SubagentGovernanceStore(path)
    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "subagent_governance_roots",
        "subagent_governance_usage",
        "subagent_governance_leases",
    } <= tables


def test_bridge_holds_and_releases_durable_turn_lease(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("ECHO_SUBAGENT_GOVERNANCE_DB", str(path))
    session = Session(thread_id="thread-governed")
    called: list[str] = []

    def runner(prompt: str, *, subagent_name: str, context: dict) -> str:
        called.append(subagent_name)
        return f"done:{prompt}"

    with session_scope(session):
        result = bridge.call_subagent(
            agent_id="custom-governed",
            prompt="check",
            runner=runner,
        )

    assert result["success"] is True
    assert called == ["custom-governed"]
    store = SubagentGovernanceStore(path)
    assert store.snapshot(session.turn_id)["active_leases"] == 0


def test_bridge_exposes_sanitized_governance_snapshot(tmp_path: Path, monkeypatch) -> None:
    from runtime.execution.subagents.event_bus import get_bus, reset_for_tests

    path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("ECHO_SUBAGENT_GOVERNANCE_DB", str(path))
    session = Session(thread_id="thread-governance-ui")
    events: list[dict] = []

    def runner(prompt: str, *, subagent_name: str, context: dict) -> str:
        return f"done:{prompt}:{subagent_name}:{bool(context)}"

    reset_for_tests()
    try:
        with session_scope(session):
            result = bridge.call_subagent(
                agent_id="custom-governance-ui",
                prompt="check",
                runner=runner,
                event_emitter=events.append,
            )
        bus_finish = next(
            event
            for event in get_bus(session.thread_id).replay(0)
            if event.get("type") == "sub_concluded"
        )
    finally:
        reset_for_tests()

    snapshot = result["governance"]
    assert snapshot["root_id"] == session.turn_id
    assert snapshot["tokens_used"] == 0
    assert snapshot["breaker"] == "open"
    assert "lease_id" not in snapshot
    finish = next(event for event in events if event.get("type") == "subagent_finished")
    assert finish["governance"]["root_id"] == session.turn_id
    assert bus_finish["payload"]["governance"]["root_id"] == session.turn_id
    assert "lease_id" not in bus_finish["payload"]["governance"]


def test_bridge_refuses_when_parent_turn_has_no_durable_slot(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("ECHO_SUBAGENT_GOVERNANCE_DB", str(path))
    monkeypatch.setenv("ECHO_MAX_ACTIVE_SUBAGENTS_PER_TURN", "1")
    session = Session(thread_id="thread-held")
    holder = SubagentGovernanceStore(path).acquire(
        session.turn_id,
        depth=1,
        global_limit=8,
        root_limit=1,
    )
    assert holder is not None
    called = False

    def runner(prompt: str, *, subagent_name: str, context: dict) -> str:
        nonlocal called
        called = True
        return "unexpected"

    try:
        with session_scope(session):
            result = bridge.call_subagent(
                agent_id="custom-held",
                prompt="check",
                runner=runner,
            )
    finally:
        SubagentGovernanceStore(path).release(holder["lease_id"])

    assert result["success"] is False
    assert result["status"] == "rejected"
    assert "durable governance" in result["error"]
    assert called is False


def test_bridge_cancels_child_when_lease_renewal_is_lost(tmp_path: Path, monkeypatch) -> None:
    from runtime.safety.approval.cancellation import current_cancellation_token

    path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("ECHO_SUBAGENT_GOVERNANCE_DB", str(path))
    monkeypatch.setenv("ECHO_SUBAGENT_LEASE_SECONDS", "1")
    renew_seen = threading.Event()
    monkeypatch.setattr(
        SubagentGovernanceStore,
        "renew",
        lambda _self, _lease_id: (renew_seen.set() or False),
    )
    session = Session(thread_id="thread-lost-lease")
    cancelled = threading.Event()

    def runner(prompt: str, *, subagent_name: str, context: dict) -> str:
        del prompt, subagent_name, context
        token = current_cancellation_token()
        deadline = time.monotonic() + 4.0
        while not token.is_cancelled and time.monotonic() < deadline:
            time.sleep(0.02)
        if token.is_cancelled:
            cancelled.set()
        return "stopped"

    with session_scope(session):
        result = bridge.call_subagent(
            agent_id="custom-lost-lease",
            prompt="wait",
            runner=runner,
        )

    assert result["success"] is True
    assert renew_seen.wait(0.1)
    assert cancelled.is_set()
    assert SubagentGovernanceStore(path).snapshot(session.turn_id)["active_leases"] == 0


def test_bridge_keeps_lease_until_timed_out_worker_exits(tmp_path: Path, monkeypatch) -> None:
    from runtime.safety.approval.cancellation import current_cancellation_token

    path = tmp_path / "governance.sqlite3"
    monkeypatch.setenv("ECHO_SUBAGENT_GOVERNANCE_DB", str(path))
    monkeypatch.setenv("ECHO_SUBAGENT_LEASE_SECONDS", "60")
    started = threading.Event()
    allow_exit = threading.Event()
    session = Session(thread_id="thread-timeout-lease")

    def runner(prompt: str, *, subagent_name: str, context: dict) -> str:
        del prompt, subagent_name, context
        started.set()
        token = current_cancellation_token()
        while not token.is_cancelled:
            time.sleep(0.01)
        allow_exit.wait(2.0)
        return "late"

    with session_scope(session):
        result = bridge.call_subagent(
            agent_id="custom-timeout-lease",
            prompt="wait",
            runner=runner,
            timeout_seconds=0.05,
        )

    assert started.is_set()
    assert result["status"] == "timeout"
    assert SubagentGovernanceStore(path).snapshot(session.turn_id)["active_leases"] == 1

    allow_exit.set()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if SubagentGovernanceStore(path).snapshot(session.turn_id)["active_leases"] == 0:
            break
        time.sleep(0.02)
    assert SubagentGovernanceStore(path).snapshot(session.turn_id)["active_leases"] == 0
