from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from runtime.core.cerebrum.react_types import ReActResult
from runtime.execution.loops.controller import LoopController
from runtime.execution.loops.models import LoopMode, LoopPolicy, LoopRun, LoopRunStatus
from runtime.execution.loops.store import LoopRunStore
from runtime.platform.process.task_supervisor import TaskLeaseConflict, TaskSupervisor
from runtime.platform.runtime_policy.workspaces import WorkspaceManager


def test_long_loop_attempt_renews_supervisor_lease(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    task_runs = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(
        task_runs,
        holder_id="loop-worker-a",
        lease_ttl_seconds=1.0,
    )
    contender = TaskSupervisor.from_path(
        task_runs,
        holder_id="loop-worker-b",
        lease_ttl_seconds=1.0,
    )
    run = LoopRun(
        owner_id="alice",
        goal="hold the lease during a long attempt",
        mode=LoopMode.PLAN,
        policy=LoopPolicy(max_attempts=1, max_iterations=1),
    )
    store.create(run)
    started = threading.Event()

    def runner(*, stack, intent, agent, model=None, max_iterations=0, thread_id=None):
        started.set()
        time.sleep(1.35)
        return ReActResult(final_answer="done", success=True)

    controller = LoopController(
        store=store,
        stack=SimpleNamespace(name="stack"),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        task_supervisor=supervisor,
        react_runner=runner,
    )
    result: dict[str, object] = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("run", controller.execute(run.run_id))
    )
    worker.start()
    assert started.wait(timeout=2)
    time.sleep(1.1)

    with pytest.raises(TaskLeaseConflict):
        contender.start_task(task_id=run.run_id, kind="loop")

    worker.join(timeout=4)
    assert not worker.is_alive()
    assert result["run"].status == LoopRunStatus.COMPLETED  # type: ignore[union-attr]

