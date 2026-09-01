from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution import cron_executor
from runtime.execution.cron_executor import run_due_cron_jobs
from runtime.platform.models import (
    ArmId,
    BudgetSpec,
    ParsedIntent,
    TaskGraph,
    TaskNode,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.gateway.team_tasks_router import create_team_tasks_router


def test_cron_timeout_kills_descendants(tmp_path: Path, monkeypatch: Any) -> None:
    marker = tmp_path / "descendant-survived"
    monkeypatch.setattr(cron_executor, "SHELL_JOB_TIMEOUT_S", 0.2)
    command = (
        f'{sys.executable} -c "import pathlib,time; time.sleep(.8); '
        f"pathlib.Path({str(marker)!r}).write_text('alive')\" & sleep 10"
    )
    status, _ = cron_executor.default_shell_runner(command, {})
    assert status == "timeout"
    time.sleep(1.0)
    assert not marker.exists()


def test_shared_capture_runner_kills_descendants(tmp_path: Path) -> None:
    from runtime.platform.process.tree import run_capture

    marker = tmp_path / "capture-descendant-survived"
    child = (
        f"import pathlib,time; time.sleep(.8); pathlib.Path({str(marker)!r}).write_text('alive')"
    )
    code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_capture(
            [sys.executable, "-c", code],
            cwd=str(tmp_path),
            timeout=0.2,
        )
    time.sleep(1.0)
    assert not marker.exists()


def test_background_exec_rotates_output_to_a_bounded_tail(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import runtime.execution.suckers._write_skills_background as background
    from runtime.execution.suckers._write_skills_background import (
        _background_paths,
    )
    from runtime.execution.suckers._write_skills_exec import (
        _background_exec,
        _read_background_output,
    )

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(background, "_BACKGROUND_OUTPUT_CAP", 1024)
    started = _background_exec(
        command=[
            sys.executable,
            "-u",
            "-c",
            "for i in range(400): print(f'line-{i:04d}-xxxxxxxx')",
        ],
    )
    assert "error" not in started
    task_id = started["task_id"]
    deadline = time.monotonic() + 3
    result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        result = _read_background_output(task_id=task_id)
        if result.get("status") == "completed":
            break
        time.sleep(0.02)

    paths = _background_paths(task_id)
    assert result["status"] == "completed"
    assert paths["stdout"].stat().st_size <= 1024
    assert "line-0399" in result["stdout"]
    assert result["stdout_truncated"] is True


def test_background_startup_recovery_converges_dead_metadata_and_adopts_live(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import runtime.execution.suckers._write_skills_background as background

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    live_paths = background._background_paths("bg_live")
    dead_paths = background._background_paths("bg_dead")
    common = {
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path),
        "pid": 1234,
        "exit_code": None,
        "stdout_path": str(live_paths["stdout"]),
        "stderr_path": str(live_paths["stderr"]),
    }
    background._write_background_metadata(
        live_paths["metadata"],
        {**common, "task_id": "bg_live"},
    )
    background._write_background_metadata(
        dead_paths["metadata"],
        {
            **common,
            "task_id": "bg_dead",
            "pid": 1235,
            "stdout_path": str(dead_paths["stdout"]),
            "stderr_path": str(dead_paths["stderr"]),
        },
    )

    monkeypatch.setattr(
        background,
        "_probe_process",
        lambda pid: (True, None) if pid == 1234 else (False, 17),
    )

    stats = background.recover_background_processes()
    assert stats == {"scanned": 2, "adopted": 1, "converged": 1, "unknown": 0}
    live = json.loads(live_paths["metadata"].read_text(encoding="utf-8"))
    dead = json.loads(dead_paths["metadata"].read_text(encoding="utf-8"))
    assert live["recovery_state"] == "adopted_external"
    assert dead["recovery_state"] == "orphaned_process_exited"
    assert dead["exit_code"] == 17


def test_recovered_background_kill_refuses_reused_pid(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import runtime.execution.suckers._write_skills_background as background
    import runtime.execution.suckers._write_skills_exec as exec_skills

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = background._background_paths("bg_reused")
    background._write_background_metadata(
        paths["metadata"],
        {
            "task_id": "bg_reused",
            "pid": 9999,
            "process_group_id": 9999,
            "exit_code": None,
            "argv": ["python", "-c", "pass"],
            "stdout_path": str(paths["stdout"]),
            "stderr_path": str(paths["stderr"]),
        },
    )
    monkeypatch.setattr(exec_skills, "background_process_identity_matches", lambda _metadata: False)

    result = exec_skills._kill_background_exec(task_id="bg_reused")
    assert result["status"] == "unknown"
    assert "identity_mismatch" in result["error"]


def test_cron_lock_skips_concurrent_tick(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    path.write_text(
        json.dumps([{"name": "once", "command": "echo ok", "cron_expression": "* * * * *"}]),
        encoding="utf-8",
    )
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow(_command: str, _job: dict[str, Any]) -> tuple[str, str]:
        calls.append("run")
        entered.set()
        release.wait(timeout=2)
        return "ok", "ok"

    first: dict[str, Any] = {}

    def invoke() -> None:
        first.update(
            run_due_cron_jobs(cron_path=path, now=datetime.now().astimezone(), shell_runner=slow)
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    assert entered.wait(timeout=2)
    second = run_due_cron_jobs(cron_path=path, now=datetime.now().astimezone(), shell_runner=slow)
    release.set()
    thread.join(timeout=2)

    # The lock is process-wide via flock; the second tick must not execute.
    assert second["skipped"] == "lock_held"
    assert calls == ["run"]
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first["fired"] == 1


def test_team_task_running_state_is_reconciled_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "team_tasks.json"
    path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-orphan",
                        "room_id": "room-1",
                        "title": "orphan",
                        "status": "running",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(create_team_tasks_router(state_path=path))
    with TestClient(app) as client:
        response = client.get("/api/team-tasks/task-orphan")
    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "failed"
    assert payload["metadata"]["failure_code"] == "worker_lost_on_restart"


def test_openai_sse_generator_close_cancels_runtime_worker() -> None:
    from runtime.safety.approval.cancellation import current_cancellation_token
    from runtime.sensing.gateway.openai_gateway import _stream_chat

    graph = TaskGraph(
        nodes=[TaskNode(node_id="n0", skill_ref="long_step")],
        budget=BudgetSpec(tokens=100, usd=0.01),
    )
    started = threading.Event()
    cancelled = threading.Event()

    class Planner:
        def plan(self, _intent: Any, **_kwargs: Any) -> TaskGraph:
            return graph

    class Journal:
        def read_by_task(self, _task_id: Any) -> list[Any]:
            return []

    class Runtime:
        def run(self, *_args: Any, **_kwargs: Any) -> Trajectory:
            started.set()
            token = current_cancellation_token()
            while not token.is_cancelled:
                time.sleep(0.01)
            cancelled.set()
            return Trajectory(
                task_id=graph.task_id,
                arm_id=ArmId("code_arm"),
                steps=[],
                outcome=TrajectoryOutcome(success=False),
            )

    class Stack:
        planner = Planner()
        runtime = Runtime()
        journal = Journal()

    generator = _stream_chat(
        Stack(),
        ParsedIntent(raw="run", intent_type="task", normalized_goal="run"),
        "echo-agent",
        "code_arm",
        keepalive_interval_s=0.01,
    )
    next(generator)  # role opener
    next(generator)  # planner + worker startup
    assert started.wait(timeout=2)
    generator.close()
    assert cancelled.wait(timeout=2)

