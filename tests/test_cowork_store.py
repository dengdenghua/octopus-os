"""Tests for runtime.memory.cowork.store."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from runtime.memory.cowork import CoworkStore, Task
from runtime.memory.cowork.store import (
    PHASE_COMPLETE,
    PHASE_FAILED,
    PHASE_PLAN,
    PHASE_SYNTHESIZE,
    PHASE_WORK,
)

# ─── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> CoworkStore:
    return CoworkStore(base_dir=tmp_path / "cowork")


def _sample_tasks() -> list[Task]:
    return [
        Task(id="t1", title="Research", description="Find papers"),
        Task(id="t2", title="Draft", description="Write outline"),
    ]


# ─── 1. create_plan writes plan.json atomically ─────────────


def test_create_plan_writes_plan_json_atomically(store: CoworkStore, tmp_path: Path) -> None:
    plan = store.create_plan(
        session_id="sess-1",
        created_by="agent-A",
        tasks=_sample_tasks(),
    )

    assert plan.session_id == "sess-1"
    assert plan.created_by == "agent-A"
    assert plan.phase == PHASE_PLAN
    assert len(plan.tasks) == 2

    # Look up the on-disk file via the store's own session-hash logic
    plan_path = store._plan_path("sess-1")
    assert plan_path.exists()
    # No leftover temp file from atomic_write_json next to it.
    siblings = list(plan_path.parent.iterdir())
    tmp_siblings = [p for p in siblings if p.name.startswith(f".{plan_path.name}.tmp-")]
    assert tmp_siblings == [], f"orphan temp files: {tmp_siblings}"

    # Round-trip via read_plan recovers the same payload.
    re_read = store.read_plan("sess-1")
    assert re_read is not None
    assert re_read.session_id == "sess-1"
    assert {t.id for t in re_read.tasks} == {"t1", "t2"}


# ─── 2. read_plan returns None for unknown session ──────────


def test_read_plan_returns_none_for_unknown_session(
    store: CoworkStore,
) -> None:
    assert store.read_plan("never-created") is None


# ─── 3. Invalid phase transition raises ValueError ──────────


def test_invalid_phase_transition_raises(store: CoworkStore) -> None:
    store.create_plan(
        session_id="sess-3",
        created_by="agent-A",
        tasks=_sample_tasks(),
    )
    # plan → work is fine
    store.advance_phase("sess-3", PHASE_WORK)
    # work → plan must fail (going backwards)
    with pytest.raises(ValueError):
        store.advance_phase("sess-3", PHASE_PLAN)

    # Shortcut to a terminal state then verify nothing escapes it.
    store.advance_phase("sess-3", PHASE_FAILED)
    with pytest.raises(ValueError):
        store.advance_phase("sess-3", PHASE_PLAN)
    with pytest.raises(ValueError):
        store.advance_phase("sess-3", PHASE_WORK)

    # Fresh session, walk to complete and check complete → plan fails.
    store.create_plan(
        session_id="sess-3b",
        created_by="agent-A",
        tasks=_sample_tasks(),
    )
    store.advance_phase("sess-3b", PHASE_WORK)
    store.advance_phase("sess-3b", PHASE_SYNTHESIZE)
    store.write_artifact("sess-3b", "__final__", "agent-A", {"summary": "done"})
    store.advance_phase("sess-3b", PHASE_COMPLETE)
    with pytest.raises(ValueError):
        store.advance_phase("sess-3b", PHASE_PLAN)


# ─── 4. claim_task: same task only succeeds once ────────────


def test_claim_task_returns_true_only_once(store: CoworkStore) -> None:
    store.create_plan(
        session_id="sess-4",
        created_by="agent-A",
        tasks=_sample_tasks(),
    )
    # First claim wins.
    assert store.claim_task("sess-4", "t1", "agent-A") is True
    # Second claim, even from a different agent, is rejected.
    assert store.claim_task("sess-4", "t1", "agent-B") is False
    # Same agent re-claiming also returns False (idempotent at bool).
    assert store.claim_task("sess-4", "t1", "agent-A") is False


# ─── 5. Concurrent claim: exactly one winner ────────────────


def test_concurrent_claim_exactly_one_winner(store: CoworkStore) -> None:
    store.create_plan(
        session_id="sess-5",
        created_by="coord",
        tasks=_sample_tasks(),
    )

    # Use a barrier so all 4 threads attempt to claim at very nearly
    # the same instant. Without the barrier the GIL may serialize
    # them so trivially that the test no longer exercises the
    # race condition we care about.
    barrier = threading.Barrier(4)
    results: list[bool] = []
    results_lock = threading.Lock()

    def claimant(agent_id: str) -> None:
        barrier.wait()
        won = store.claim_task("sess-5", "t1", agent_id)
        with results_lock:
            results.append(won)

    threads = [threading.Thread(target=claimant, args=(f"agent-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sum(1 for r in results if r is True) == 1, results
    assert sum(1 for r in results if r is False) == 3, results

    # Assignments file is well-formed and shows exactly one entry.
    assigns = store.read_assignments("sess-5")
    assert "t1" in assigns
    assert assigns["t1"].agent_id.startswith("agent-")


# ─── 6. write_artifact creates file + updates assignment ────


def test_write_artifact_creates_file_and_updates_assignment(
    store: CoworkStore,
) -> None:
    store.create_plan(
        session_id="sess-6",
        created_by="coord",
        tasks=_sample_tasks(),
    )
    assert store.claim_task("sess-6", "t1", "agent-A") is True

    artifact_path = store.write_artifact(
        session_id="sess-6",
        task_id="t1",
        agent_id="agent-A",
        output={"finding": "42"},
    )

    assert artifact_path.exists()
    assert artifact_path.name == "t1.json"

    # Assignment now reflects done + artifact_ref pointing at the
    # relative path under the session dir.
    assigns = store.read_assignments("sess-6")
    a = assigns["t1"]
    assert a.status == "done"
    assert a.artifact_ref == "artifacts/t1.json"
    assert a.completed_at is not None


# ─── 7. read_artifacts returns all artifacts for a session ──


def test_read_artifacts_returns_all_for_session(
    store: CoworkStore,
) -> None:
    store.create_plan(
        session_id="sess-7",
        created_by="coord",
        tasks=_sample_tasks(),
    )
    store.claim_task("sess-7", "t1", "agent-A")
    store.claim_task("sess-7", "t2", "agent-B")

    store.write_artifact("sess-7", "t1", "agent-A", {"k": "v1"})
    store.write_artifact("sess-7", "t2", "agent-B", {"k": "v2"})

    arts = store.read_artifacts("sess-7")
    assert set(arts.keys()) == {"t1", "t2"}
    assert arts["t1"]["output"] == {"k": "v1"}
    assert arts["t2"]["output"] == {"k": "v2"}
    assert arts["t1"]["agent_id"] == "agent-A"
    assert arts["t2"]["agent_id"] == "agent-B"


# ─── 8. Sessions are isolated ───────────────────────────────


def test_sessions_are_isolated(store: CoworkStore) -> None:
    store.create_plan(
        session_id="sess-A",
        created_by="coord",
        tasks=[Task(id="ta", title="only-A")],
    )
    store.create_plan(
        session_id="sess-B",
        created_by="coord",
        tasks=[Task(id="tb", title="only-B")],
    )

    plan_a = store.read_plan("sess-A")
    plan_b = store.read_plan("sess-B")
    assert plan_a is not None and plan_b is not None
    assert {t.id for t in plan_a.tasks} == {"ta"}
    assert {t.id for t in plan_b.tasks} == {"tb"}

    # Claim in A, B's slot stays empty.
    store.claim_task("sess-A", "ta", "agent-A")
    assert "ta" in store.read_assignments("sess-A")
    assert store.read_assignments("sess-B") == {}

    # Artifact in A, B's artifact dir stays empty.
    store.write_artifact("sess-A", "ta", "agent-A", {"v": 1})
    assert "ta" in store.read_artifacts("sess-A")
    assert store.read_artifacts("sess-B") == {}


# ─── 9. advance_phase(work) fails when plan has 0 tasks ─────


def test_advance_to_work_fails_with_zero_tasks(store: CoworkStore) -> None:
    store.create_plan(
        session_id="sess-9",
        created_by="coord",
        tasks=[],  # empty
    )
    with pytest.raises(ValueError, match="0 tasks"):
        store.advance_phase("sess-9", PHASE_WORK)


# ─── 10. list_sessions returns all stored session IDs ───────


def test_list_sessions_returns_all_stored(store: CoworkStore) -> None:
    assert store.list_sessions() == []

    store.create_plan(
        session_id="alpha",
        created_by="coord",
        tasks=_sample_tasks(),
    )
    store.create_plan(
        session_id="beta",
        created_by="coord",
        tasks=_sample_tasks(),
    )
    store.create_plan(
        session_id="gamma",
        created_by="coord",
        tasks=_sample_tasks(),
    )

    assert store.list_sessions() == ["alpha", "beta", "gamma"]
