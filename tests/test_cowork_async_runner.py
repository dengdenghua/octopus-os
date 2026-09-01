"""AsyncWorkRunner: drives pending tasks via an injected executor (no LLM)."""

from __future__ import annotations

import shutil
import sqlite3
import sys

import pytest

from runtime.memory.cowork import service
from runtime.memory.cowork.async_runner import AsyncWorkRunner
from runtime.memory.cowork.async_work import AsyncWorkStore
from runtime.memory.cowork.group import ContextGrant, MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.nominate import CompetenceStore
from runtime.memory.cowork.runtime import create_cowork_runtime


def _setup(tmp_path):
    gs = GroupStore(base_dir=tmp_path)
    aw = AsyncWorkStore(base_dir=tmp_path, group_store=gs)
    return gs, aw


def test_runner_executes_and_posts_to_board(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    seen = {}

    def execute(task, context):
        seen["prompt"] = task.prompt
        seen["context"] = context
        return f"done: {task.prompt}"

    runner = AsyncWorkRunner(aw, gs, execute)
    aw.assign("t", "worker", "find slow query", actor="user")
    assert runner.drain("t") == 1
    assert aw.pending("t") == []
    assert seen["prompt"] == "find slow query"
    # result posted to the shared blackboard
    assert any(v == "done: find slow query" for v in gs.blackboard_snapshot("t").values())


def test_runner_passes_grant_sliced_history(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    # worker joined at message 5 with from_join → should only see messages 5..
    service.invite_member(
        gs,
        "t",
        actor="u",
        target_id="worker",
        kind="agent",
        grant=ContextGrant(scope="from_join"),
        at_message=5,
    )
    captured = {}

    def execute(task, context):
        captured["history"] = context["history"]
        captured["scope"] = context["grant_scope"]
        return "ok"

    runner = AsyncWorkRunner(
        aw, gs, execute, history_provider=lambda _t: [f"m{i}" for i in range(10)]
    )
    aw.assign("t", "worker", "summarize", actor="u")
    runner.drain("t")
    assert captured["scope"] == "from_join"
    assert captured["history"] == [f"m{i}" for i in range(5, 10)]  # 0..4 not leaked


def test_runner_records_competence_on_success_and_failure(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    comp = CompetenceStore(base_dir=tmp_path)
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "ok", competence=comp)
    aw.assign("t", "worker", "database tuning", actor="u")
    runner.drain("t")
    assert comp.competence("worker", "database") == 1.0  # 1 success

    def boom(task, context):
        raise RuntimeError("model down")

    failing = AsyncWorkRunner(aw, gs, boom, competence=comp)
    tid = aw.assign("t", "worker", "database tuning", actor="u").task_id
    failing.drain("t")
    assert aw.get(tid).status == "failed"
    assert comp.competence("worker", "database") == 0.5  # 1 win / 2 total


def test_drain_all_across_threads(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "r")
    aw.assign("t1", "w", "a", actor="u")
    aw.assign("t2", "w", "b", actor="u")
    assert set(aw.threads_with_pending()) == {"t1", "t2"}
    assert runner.drain_all() == 2
    assert aw.threads_with_pending() == []


def test_tick_once_records_success_health(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "r")
    aw.assign("t", "w", "a", actor="u")

    assert runner.tick_once() == 1

    status = runner.status()
    assert status["running"] is False
    assert status["total_ticks"] == 1
    assert status["total_failures"] == 0
    assert status["consecutive_failures"] == 0
    assert status["last_error"] is None
    assert status["last_ran_count"] == 1
    assert status["last_recovered"] == {"requeued": 0, "failed": 0}
    assert status["last_success_at"]


def test_tick_once_records_recovered_stale_health(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    task = aw.assign("t", "worker", "recover during tick", actor="u")
    assert aw.claim(task.task_id) is True
    with aw._lock, sqlite3.connect(str(aw._db)) as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE async_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (task.task_id,),
        )
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "rerun", recover_stale_seconds=1)

    assert runner.tick_once() == 1

    status = runner.status()
    assert status["total_ticks"] == 1
    assert status["last_recovered"] == {"requeued": 1, "failed": 0}
    assert status["last_ran_count"] == 1


def test_tick_once_records_failure_health(tmp_path, monkeypatch) -> None:
    gs, aw = _setup(tmp_path)
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "r")

    monkeypatch.setattr(
        aw, "threads_with_pending", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    assert runner.tick_once() == 0

    status = runner.status()
    assert status["total_ticks"] == 1
    assert status["total_failures"] == 1
    assert status["consecutive_failures"] == 1
    assert status["last_error"] == "RuntimeError: db down"
    assert status["last_failure_at"]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows cannot delete an open sqlite file; removed-under-live-store is a POSIX-only scenario",
)
def test_tick_once_self_heals_missing_async_work_directory(tmp_path) -> None:
    base_dir = tmp_path / "cowork"
    gs = GroupStore(base_dir=base_dir)
    aw = AsyncWorkStore(base_dir=base_dir, group_store=gs)
    runner = AsyncWorkRunner(aw, gs, lambda t, c: "r")

    shutil.rmtree(base_dir)

    assert runner.tick_once() == 0
    assert base_dir.exists()
    assert aw._db.exists()  # noqa: SLF001 - verifies storage self-heal path
    status = runner.status()
    assert status["total_ticks"] == 1
    assert status["total_failures"] == 0
    assert status["last_error"] is None


def test_drain_all_recovers_stale_working_before_polling(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    task = aw.assign("t", "worker", "recover during drain", actor="u")
    assert aw.claim(task.task_id) is True
    with aw._lock, sqlite3.connect(str(aw._db)) as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE async_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (task.task_id,),
        )

    runner = AsyncWorkRunner(
        aw,
        gs,
        lambda t, c: f"rerun: {t.prompt}",
        recover_stale_seconds=1,
    )

    assert runner.drain_all() == 1
    finished = aw.get(task.task_id)
    assert finished.status == "done"
    assert finished.attempts == 2
    assert finished.result == "rerun: recover during drain"


def test_stale_working_tasks_are_recovered_or_failed(tmp_path) -> None:
    gs, aw = _setup(tmp_path)
    retry = aw.assign("t", "worker", "retry me", actor="u")
    fail = aw.assign("t", "worker", "give up", actor="u")
    assert aw.claim(retry.task_id) is True
    assert aw.claim(fail.task_id) is True

    with aw._lock, sqlite3.connect(str(aw._db)) as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE async_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (retry.task_id,),
        )
        conn.execute(
            "UPDATE async_tasks SET updated_at='2000-01-01T00:00:00+00:00', attempts=3 "
            "WHERE task_id=?",
            (fail.task_id,),
        )

    recovered = aw.recover_stale_working(max_age_seconds=1, max_attempts=3)

    assert recovered == {"requeued": 1, "failed": 1}
    assert aw.get(retry.task_id).status == "pending"
    assert aw.get(fail.task_id).status == "failed"


def test_runtime_dispatches_through_subagent_bridge(tmp_path, monkeypatch) -> None:
    from runtime.execution.subagents import get_sub_agent_runner, set_sub_agent_runner

    seen = {}

    def fake_call_subagent(agent_id, prompt, **kwargs):
        seen["agent_id"] = agent_id
        seen["prompt"] = prompt
        seen["context"] = kwargs["context"]
        return {"success": True, "output": "worker result"}

    previous_runner = get_sub_agent_runner()
    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call_subagent)
    set_sub_agent_runner(lambda **_kwargs: "available")
    try:
        runtime = create_cowork_runtime(base_dir=tmp_path, enable_runner=True)
        runtime.group_store.append(
            "t",
            MemberEvent(action="invite", actor="u", target_id="worker"),
        )
        task = runtime.async_store.assign("t", "worker", "do background work", actor="u")

        assert runtime.runner.drain("t") == 1
        assert runtime.async_store.get(task.task_id).status == "done"
        assert seen["agent_id"] == "worker"
        assert seen["prompt"] == "do background work"
        assert seen["context"]["source"] == "cowork_async_task"
        assert runtime.group_store.blackboard_snapshot("t")
        status = runtime.status("t")
        assert status["runner_status"]["total_ticks"] == 0
        assert status["runner_status"]["last_error"] is None
    finally:
        set_sub_agent_runner(previous_runner)


def test_runtime_does_not_enable_runner_without_subagent_executor(tmp_path) -> None:
    runtime = create_cowork_runtime(base_dir=tmp_path, enable_runner=True)

    assert runtime.runner is None
    assert runtime.runner_enabled is False
    assert "not configured" in runtime.runner_reason
    assert runtime.status("t")["task_counts"] == {
        "pending": 0,
        "working": 0,
        "done": 0,
        "failed": 0,
    }

