"""Settlement bridge — workflow/job lifecycle rows reach the timeline.

Covers ``runtime/memory/journal/activity.py`` (best-effort journal mirrors
for orchestration activity that outlives a streaming connection) and the
``/api/journal/timeline`` passthrough of the structured event types
(``workflow/start`` / ``workflow/progress`` / ``workflow/end`` /
``job/change``).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers.registry import SkillRegistry
from runtime.memory.journal.journal import InMemoryJournal
from runtime.platform.process.session import Session, session_scope
from runtime.sensing.gateway.observability_router import create_observability_router


def _session(
    journal: InMemoryJournal,
    *,
    thread_id: str = "thr-1",
    task_id: str | None = None,
) -> Session:
    task_id = task_id or str(uuid4())
    return Session(
        thread_id=thread_id,
        metadata={"journal": journal, "task_id": task_id},
    )


def _write_all_activity() -> None:
    from runtime.memory.journal.activity import (
        write_job_change,
        write_workflow_end,
        write_workflow_progress,
        write_workflow_start,
    )

    assert write_workflow_start(run_id="r1", name="demo", description="demo run")
    assert write_workflow_progress(run_id="r1", kind="phase", text="collect")
    assert write_workflow_progress(
        run_id="r1",
        kind="agent_start",
        agent_seq=1,
        agent_label="coder",
    )
    assert write_workflow_end(
        run_id="r1",
        stop_reason="completed",
        agents_started=1,
    )
    assert write_job_change(
        job_id="j1",
        kind="subagent",
        label="fix the bug",
        status="completed",
        detail="ok",
    )


def test_activity_writers_land_in_journal_with_attribution() -> None:
    """Best-effort writers attach the ambient task attribution when a
    session is present."""
    journal = InMemoryJournal()
    with session_scope(_session(journal)):
        _write_all_activity()

    by_type = {e.event_type: e for e in journal.read_all()}
    assert set(by_type) == {
        "workflow/start",
        "workflow/progress",
        "workflow/end",
        "job/change",
    }
    job = by_type["job/change"]
    assert job.job_id == "j1"
    assert job.label == "fix the bug"
    assert job.status == "completed"
    assert job.detail == "ok"
    assert job.task_id is not None

    wf = by_type["workflow/start"]
    assert wf.run_id == "r1"
    assert wf.name == "demo"
    assert wf.task_id is not None

    progress = by_type["workflow/progress"]
    assert progress.kind == "agent_start"
    assert progress.agent_label == "coder"

    end = by_type["workflow/end"]
    assert end.stop_reason == "completed"
    assert end.agents_started == 1


def test_activity_writers_without_session_do_not_raise() -> None:
    """No session → writers return False, never raise (best-effort)."""
    from runtime.memory.journal.activity import (
        write_job_change,
        write_workflow_start,
    )

    assert (
        write_job_change(
            job_id="j9",
            kind="subagent",
            label="x",
            status="failed",
        )
        is False
    )
    assert write_workflow_start(run_id="r9", name="x") is False


def test_timeline_endpoint_carries_settlement_fields() -> None:
    """/api/journal/timeline exposes the settlement fields (whitelisted,
    envelope never leaked)."""
    journal = InMemoryJournal()
    with session_scope(_session(journal)):
        _write_all_activity()

    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=journal,
            registry=SkillRegistry(),
        )
    )
    client = TestClient(app)
    resp = client.get("/api/journal/timeline")
    assert resp.status_code == 200
    body = resp.json()
    task_id = str(journal.read_all()[0].task_id)
    assert task_id in body["task_ids"]
    events = body["timelines"][task_id]
    by_type = {e["event_type"]: e for e in events}

    job = by_type["job/change"]
    assert job["job_id"] == "j1"
    assert job["label"] == "fix the bug"
    assert job["status"] == "completed"
    assert job["detail"] == "ok"

    wf = by_type["workflow/start"]
    assert wf["run_id"] == "r1"
    assert wf["name"] == "demo"
    assert wf["description"] == "demo run"

    progress = by_type["workflow/progress"]
    assert progress["kind"] == "agent_start"
    assert progress["agent_label"] == "coder"

    end = by_type["workflow/end"]
    assert end["stop_reason"] == "completed"
    assert end["agents_started"] == 1

    # envelope fields never leak into the timeline entry
    for entry in events:
        assert "event_id" not in entry
        assert "actor" not in entry
        assert "tenant_id" not in entry


@pytest.mark.asyncio
async def test_registry_on_settle_observer_invoked() -> None:
    """LocalJobRegistry.settle() calls the per-job ``on_settle`` observer
    once, after the terminal state is set."""
    import asyncio
    import threading

    from runtime.execution.jobs.registry import LocalJobRegistry
    from runtime.execution.jobs.types import (
        JobHooks,
        JobOutcome,
        JobStart,
        is_terminal,
    )

    seen: list[dict[str, Any]] = []
    holder: dict[str, asyncio.Future[JobOutcome]] = {}

    def run() -> JobHooks:
        done: asyncio.Future[JobOutcome] = asyncio.get_running_loop().create_future()
        holder["done"] = done
        return JobHooks(cancel=lambda reason=None: None, done=done, read_output=None)

    registry = LocalJobRegistry()
    registry.attach_controller("test")
    job_id = registry.start(
        JobStart(
            kind="subagent",
            label="settle observer",
            run=run,
            on_settle=lambda snap: seen.append(
                {
                    "id": snap.id,
                    "status": snap.status,
                    "kind": snap.kind,
                    "label": snap.label,
                }
            ),
        )
    )

    done = holder["done"]

    def _resolve() -> None:
        done.get_loop().call_soon_threadsafe(
            lambda: (
                done.set_result(JobOutcome(status="completed", detail="ok"))
                if not done.done()
                else None
            )
        )

    threading.Thread(target=_resolve, daemon=True).start()
    for _ in range(200):
        if is_terminal(registry.get(job_id).status):
            break
        await asyncio.sleep(0.01)

    assert len(seen) == 1
    assert seen[0]["id"] == job_id
    assert seen[0]["status"] == "completed"
    assert seen[0]["label"] == "settle observer"


# ═══════════════════════════════════════════════════════════
# Audit T-12: parallel-batch lifecycle rows + startup sweep
# ═══════════════════════════════════════════════════════════


def test_parallel_batch_lifecycle_rows_and_sweep() -> None:
    """A parallel batch journals running -> terminal, and a batch left
    running by a crash is folded to interrupted by the startup sweep."""
    from runtime.execution.parallel_agents.helpers import journal_batch_lifecycle
    from runtime.memory.journal.activity import sweep_interrupted_jobs

    journal = InMemoryJournal()
    with session_scope(_session(journal)):
        journal_batch_lifecycle("batch_abc", status="running", detail="parallel batch started")
        journal_batch_lifecycle(
            "batch_abc",
            status="completed",
            detail="parallel batch finished (completed=3 failed=0 cancelled=0)",
        )
        # A second batch "crashed" mid-flight: only its running row exists.
        journal_batch_lifecycle("batch_crashed", status="running", detail="parallel batch started")

    rows = [e for e in journal.read_all() if e.event_type == "job/change"]
    by_job: dict[str, list[Any]] = {}
    for r in rows:
        by_job.setdefault(str(r.job_id), []).append(r.status)
    assert by_job["batch_abc"] == ["running", "completed"]
    assert by_job["batch_crashed"] == ["running"]

    # Startup sweep closes the crashed batch as interrupted.
    closed = sweep_interrupted_jobs(journal)
    closed_ids = {c["job_id"] for c in closed}
    assert "batch_crashed" in closed_ids
    assert "batch_abc" not in closed_ids  # already terminal, untouched
    terminal = [e for e in journal.read_all() if e.event_type == "job/change"]
    crashed_last = [e for e in terminal if str(e.job_id) == "batch_crashed"][-1]
    assert crashed_last.status == "failed"
    assert "interrupted" in crashed_last.detail

