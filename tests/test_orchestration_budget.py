"""Opt-in orchestration budget envelope.

The flat per-turn delegation cap (5) blocks any multi-stage / looping
orchestration. A deterministic recipe instead runs inside an
``orchestration_budget_scope(K)``: for the scope's duration the flat cap is
replaced by a single bounded total of K sub-agent spawns. With no scope the
old per-turn behaviour is unchanged.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from runtime.execution.suckers import delegation_budget as db
from runtime.execution.suckers.delegation_budget import (
    OrchestrationBudget,
    check_absolute_cap,
    current_orchestration_budget,
    orchestration_budget_scope,
    record_delegation,
)


@pytest.fixture(autouse=True)
def _reset_budget_state():
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )

    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)
    yield
    db._TURN_DELEGATIONS.clear()
    db._TURN_FAILED_FINGERPRINTS.clear()
    set_sub_agent_runner(None)
    set_subagent_registry(None)


# ── pure: scope + envelope accounting ────────────────────────────


def test_scope_sets_and_resets():
    assert current_orchestration_budget() is None
    with orchestration_budget_scope(4) as budget:
        assert current_orchestration_budget() is budget
        assert budget.max_spawns == 4
        assert budget.remaining() == 4
    assert current_orchestration_budget() is None


def test_check_cap_uses_envelope_when_active():
    with orchestration_budget_scope(3):
        assert check_absolute_cap("turn-1") == (0, True)
        record_delegation("turn-1", "fp1", succeeded=True)
        record_delegation("turn-1", "fp2", succeeded=True)
        assert check_absolute_cap("turn-1") == (2, True)
        record_delegation("turn-1", "fp3", succeeded=True)
        # envelope full now
        assert check_absolute_cap("turn-1") == (3, False)
    # the per-turn counter was NOT touched while the envelope was active
    assert db._TURN_DELEGATIONS.get("turn-1", 0) == 0


def test_envelope_charges_every_spawn_even_failures():
    with orchestration_budget_scope(5) as budget:
        record_delegation("t", "fpA", succeeded=False)
        record_delegation("t", "fpA", succeeded=False)  # first-time-free does NOT apply
        assert budget.used == 2


def test_default_per_turn_cap_unchanged_without_scope():
    for i in range(db._PER_TURN_ABSOLUTE_LIMIT):
        _, within = check_absolute_cap("turn-x")
        assert within is True
        record_delegation("turn-x", f"fp{i}", succeeded=True)
    _, within = check_absolute_cap("turn-x")
    assert within is False  # the flat 5-cap still bites when no envelope


def test_explicit_budget_param_overrides_ambient():
    explicit = OrchestrationBudget(max_spawns=1)
    # No ambient scope; pass the budget explicitly (the parallel pool path).
    assert check_absolute_cap("t", budget=explicit) == (0, True)
    record_delegation("t", "fp", succeeded=True, budget=explicit)
    assert check_absolute_cap("t", budget=explicit) == (1, False)


def test_charge_is_thread_safe():
    budget = OrchestrationBudget(max_spawns=1000)
    barrier = threading.Barrier(20)

    def _worker():
        barrier.wait()
        for _ in range(10):
            budget.charge()

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert budget.used == 200  # 20 threads * 10, no lost updates


def test_try_charge_is_thread_safe_and_cap_bound():
    budget = OrchestrationBudget(max_spawns=7)
    barrier = threading.Barrier(20)
    lock = threading.Lock()
    accepted = 0

    def _worker():
        nonlocal accepted
        barrier.wait()
        if budget.try_charge():
            with lock:
                accepted += 1

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert accepted == 7
    assert budget.used == 7
    assert budget.remaining() == 0


# ── integration: call_agent_parallel under an envelope ───────────


def _patch_bridge(monkeypatch):
    def _fake(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "output": f"echo:{prompt}",
            "success": True,
            "error": None,
            "codename": "W",
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr(
        "runtime.execution.subagents.bridge.call_subagent",
        _fake,
    )


def _specs(*prompts: str) -> list[dict[str, str]]:
    return [{"agent_id": "researcher", "prompt": p} for p in prompts]


def test_parallel_charges_envelope_across_stages(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(monkeypatch)
    with orchestration_budget_scope(10) as budget:
        r1 = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))
        r2 = _call_agent_parallel(specs=_specs("p4", "p5", "p6"))
    assert r1["success_count"] == 3
    assert r2["success_count"] == 3
    # two stages = 6 spawns charged to the one envelope (a 2nd+3rd stage like
    # this is exactly what the flat 5/turn cap would have refused)
    assert budget.used == 6


def test_envelope_blocks_call_when_exhausted(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    _patch_bridge(monkeypatch)
    with orchestration_budget_scope(2) as budget:
        r1 = _call_agent_parallel(specs=_specs("p1", "p2", "p3"))  # 2 run, 1 refused
        r2 = _call_agent_parallel(specs=_specs("p4", "p5"))  # no room → refused
    assert r1["success_count"] == 2
    assert r1["partial"] is True
    assert r1["failed"] == 1
    assert r1["failures"][0]["error_type"] == "budget_exhausted"
    assert budget.used == 2
    assert r2["ok"] is False
    assert "budget" in (r2.get("error") or "").lower()


def test_parallel_fanout_cannot_oversubscribe_envelope(monkeypatch):
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    calls: list[str] = []
    lock = threading.Lock()

    def _fake(agent_id: str = "", prompt: str = "", **_kw: Any) -> dict[str, Any]:
        with lock:
            calls.append(prompt)
        return {
            "agent_id": agent_id,
            "output": f"echo:{prompt}",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", _fake)
    monkeypatch.setattr(
        "runtime.execution.subagents.bridge.call_subagent",
        _fake,
    )

    with orchestration_budget_scope(3) as budget:
        result = _call_agent_parallel(
            specs=_specs("p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"),
        )

    assert budget.used == 3
    assert len(calls) == 3
    assert result["success_count"] == 3
    assert result["failed"] == 5
    assert result["partial"] is True
    assert {f["error_type"] for f in result["failures"]} == {"budget_exhausted"}

