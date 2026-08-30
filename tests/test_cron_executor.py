"""Tests for runtime/execution/cron_executor.py — the cron igniter."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.cron_executor import run_due_cron_jobs

NOW = datetime(2026, 7, 28, 12, 30, 0).astimezone()


def _write_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(jobs), encoding="utf-8")


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ok_shell(command: str, job: dict[str, Any]) -> tuple[str, str]:
    return "ok", f"ran: {command}"


def _ok_prompt(prompt: str, job: dict[str, Any]) -> tuple[str, str]:
    return "ok", f"answered: {prompt}"


# ─── basic firing ────────────────────────────────────────────


def test_missing_file_is_noop(tmp_path: Path) -> None:
    result = run_due_cron_jobs(cron_path=tmp_path / "nope.json", now=NOW)
    assert result == {"ok": True, "fired": 0, "results": []}


def test_recurring_job_fires_and_records_last_run(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "every_min", "command": "echo hi", "cron_expression": "* * * * *"}],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        shell_runner=_ok_shell,
    )
    assert result["fired"] == 1
    job = _read_jobs(path)[0]
    assert job["last_status"] == "ok"
    assert job["last_run"] is not None
    assert "ran: echo hi" in job["last_output"]


def test_future_cron_does_not_fire(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    # Only matches Jan 1 at midnight — NOW is Jul 28.
    _write_jobs(
        path,
        [{"name": "new_year", "command": "echo hi", "cron_expression": "0 0 1 1 *"}],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        shell_runner=_ok_shell,
    )
    assert result["fired"] == 0
    assert _read_jobs(path)[0].get("last_run") is None


def test_same_minute_does_not_double_fire(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "every_min", "command": "echo hi", "cron_expression": "* * * * *"}],
    )
    run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell)
    # A second tick within the same scheduled minute must not refire.
    later_same_minute = NOW + timedelta(seconds=30)
    result = run_due_cron_jobs(
        cron_path=path,
        now=later_same_minute,
        shell_runner=_ok_shell,
    )
    assert result["fired"] == 0


# ─── one-shot fire_at ────────────────────────────────────────


def test_one_shot_fires_once_then_never_again(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    fire_at = (NOW - timedelta(minutes=5)).isoformat()
    _write_jobs(
        path,
        [
            {
                "name": "remind_once",
                "command": "check the deploy",
                "cron_expression": "25 12 28 7 *",
                "fire_at": fire_at,
                "recurring": False,
                "prompt": "check the deploy",
                "creator_actor": "agent_self",
            }
        ],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        prompt_runner=_ok_prompt,
    )
    assert result["fired"] == 1
    assert result["results"][0]["status"] == "ok"

    # Any later tick — the one-shot is spent.
    result2 = run_due_cron_jobs(
        cron_path=path,
        now=NOW + timedelta(hours=1),
        prompt_runner=_ok_prompt,
    )
    assert result2["fired"] == 0


def test_one_shot_future_fire_at_waits(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    fire_at = (NOW + timedelta(minutes=10)).isoformat()
    _write_jobs(
        path,
        [
            {
                "name": "future_once",
                "command": "later",
                "cron_expression": "40 12 28 7 *",
                "fire_at": fire_at,
                "recurring": False,
                "prompt": "later",
            }
        ],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        prompt_runner=_ok_prompt,
    )
    assert result["fired"] == 0


# ─── dispatch routing ────────────────────────────────────────


def test_prompt_job_uses_prompt_runner_not_shell(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    calls: list[str] = []

    def recording_prompt(prompt: str, job: dict[str, Any]) -> tuple[str, str]:
        calls.append(prompt)
        return "ok", "done"

    def forbidden_shell(command: str, job: dict[str, Any]) -> tuple[str, str]:
        raise AssertionError("shell runner must not run agent jobs")

    _write_jobs(
        path,
        [
            {
                "name": "agent_job",
                "command": "summarize today's journal",
                "cron_expression": "* * * * *",
                "prompt": "summarize today's journal",
                "creator_actor": "agent_self",
            }
        ],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        prompt_runner=recording_prompt,
        shell_runner=forbidden_shell,
    )
    assert result["fired"] == 1
    assert calls == ["summarize today's journal"]


def test_shell_job_uses_shell_runner(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    calls: list[str] = []

    def recording_shell(command: str, job: dict[str, Any]) -> tuple[str, str]:
        calls.append(command)
        return "ok", "done"

    def forbidden_prompt(prompt: str, job: dict[str, Any]) -> tuple[str, str]:
        raise AssertionError("prompt runner must not run shell jobs")

    _write_jobs(
        path,
        [
            {
                "name": "ui_job",
                "command": "df -h",
                "cron_expression": "* * * * *",
                "creator_actor": "admin",
            }
        ],
    )
    result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        shell_runner=recording_shell,
        prompt_runner=forbidden_prompt,
        allow_cross_tenant=True,
    )
    assert result["fired"] == 1
    assert calls == ["df -h"]


# ─── failure isolation ───────────────────────────────────────


def test_failing_job_marks_error_and_tick_continues(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"

    def flaky(command: str, job: dict[str, Any]) -> tuple[str, str]:
        if job["name"] == "bad":
            return "error", "boom"
        return "ok", "fine"

    _write_jobs(
        path,
        [
            {"name": "bad", "command": "false", "cron_expression": "* * * * *"},
            {"name": "good", "command": "true", "cron_expression": "* * * * *"},
        ],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=flaky)
    assert result["fired"] == 2
    jobs = {j["name"]: j for j in _read_jobs(path)}
    assert jobs["bad"]["last_status"] == "error"
    assert jobs["good"]["last_status"] == "ok"


def test_runner_exception_is_captured_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"

    def exploding(command: str, job: dict[str, Any]) -> tuple[str, str]:
        raise RuntimeError("runner bug")

    _write_jobs(
        path,
        [{"name": "x", "command": "whatever", "cron_expression": "* * * * *"}],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=exploding)
    assert result["fired"] == 1
    job = _read_jobs(path)[0]
    assert job["last_status"] == "error"
    assert "RuntimeError" in job["last_output"]


def test_unparseable_cron_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {"name": "broken", "command": "echo", "cron_expression": "not a cron"},
            {"name": "fine", "command": "echo", "cron_expression": "* * * * *"},
        ],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell)
    assert result["fired"] == 1
    assert result["results"][0]["name"] == "fine"


# ─── catch-up after downtime ─────────────────────────────────


def test_missed_schedule_fires_single_catch_up(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    last_run = (NOW - timedelta(hours=3)).isoformat()
    _write_jobs(
        path,
        [
            {
                "name": "hourly",
                "command": "echo tick",
                "cron_expression": "0 * * * *",
                "last_run": last_run,
                "last_status": "ok",
            }
        ],
    )
    # Server was down for 3 hourly slots — exactly one catch-up run.
    result = run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell)
    assert result["fired"] == 1

    # And it does not keep catching up on the next tick.
    result2 = run_due_cron_jobs(
        cron_path=path,
        now=NOW + timedelta(seconds=45),
        shell_runner=_ok_shell,
    )
    assert result2["fired"] == 0


# ─── field preservation ──────────────────────────────────────


def test_extra_fields_survive_the_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    fire_at = (NOW - timedelta(minutes=1)).isoformat()
    _write_jobs(
        path,
        [
            {
                "name": "extras",
                "command": "ping",
                "cron_expression": "29 12 28 7 *",
                "fire_at": fire_at,
                "recurring": False,
                "prompt": "ping",
                "creator_actor": "agent_self",
            }
        ],
    )
    run_due_cron_jobs(cron_path=path, now=NOW, prompt_runner=_ok_prompt)
    job = _read_jobs(path)[0]
    assert job["fire_at"] == fire_at
    assert job["recurring"] is False
    assert job["prompt"] == "ping"
    assert job["creator_actor"] == "agent_self"


# ─── default shell runner (real subprocess) ──────────────────


def test_default_shell_runner_executes_real_command(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "real", "command": "echo echo-cron-ok", "cron_expression": "* * * * *"}],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW)
    assert result["fired"] == 1
    job = _read_jobs(path)[0]
    assert job["last_status"] == "ok"
    assert "echo-cron-ok" in job["last_output"]


def test_default_shell_runner_records_nonzero_exit(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "real_fail", "command": "exit 3", "cron_expression": "* * * * *"}],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW)
    assert result["fired"] == 1
    job = _read_jobs(path)[0]
    assert job["last_status"] == "error"
    assert "exit=3" in job["last_output"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ─── run ledger + delivery ───────────────────────────────────


def test_fired_job_appends_run_ledger(tmp_path: Path) -> None:
    from runtime.execution.cron_executor import read_run_ledger

    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "ledgered", "command": "echo hi", "cron_expression": "* * * * *"}],
    )
    run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell)

    runs = read_run_ledger(tmp_path / "cron_runs.jsonl")
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "ledgered"
    assert run["kind"] == "shell"
    assert run["status"] == "ok"
    assert "ran: echo hi" in run["output_excerpt"]
    assert run["duration_ms"] >= 0


def test_deliver_hook_receives_each_run_record(tmp_path: Path) -> None:
    delivered: list[dict] = []
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {"name": "a", "command": "echo a", "cron_expression": "* * * * *"},
            {
                "name": "b",
                "command": "goal",
                "cron_expression": "* * * * *",
                "prompt": "goal",
            },
        ],
    )
    run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        shell_runner=_ok_shell,
        prompt_runner=_ok_prompt,
        deliver=delivered.append,
    )
    assert [r["name"] for r in delivered] == ["a", "b"]
    assert delivered[1]["kind"] == "prompt"


def test_deliver_hook_failure_does_not_break_tick(tmp_path: Path) -> None:
    def bad_deliver(record: dict) -> None:
        raise RuntimeError("channel down")

    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "x", "command": "echo", "cron_expression": "* * * * *"}],
    )
    result = run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell, deliver=bad_deliver)
    assert result["fired"] == 1
    assert _read_jobs(path)[0]["last_status"] == "ok"


def test_no_fire_means_no_ledger(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "idle", "command": "echo", "cron_expression": "0 0 1 1 *"}],
    )
    run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=_ok_shell)
    assert not (tmp_path / "cron_runs.jsonl").exists()


def test_store_projection_includes_last_output(tmp_path: Path) -> None:
    from runtime.execution.cron_store import _read_cron_jobs

    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "proj",
                "command": "echo",
                "cron_expression": "* * * * *",
                "last_output": "some output",
            }
        ],
    )
    jobs = _read_cron_jobs(path)
    assert jobs[0]["last_output"] == "some output"


# ═══════════════════════════════════════════════════════════
# Audit T-02: in-flight markers + startup recovery
# ═══════════════════════════════════════════════════════════


def test_tick_skips_job_with_inflight_marker(tmp_path: Path) -> None:
    """A job with a persisted started_at marker is NOT fired again."""
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "in_flight",
                "command": "echo hi",
                "cron_expression": "* * * * *",
                "started_at": NOW.isoformat(),
                "pid": 999999,
            }
        ],
    )
    result = run_due_cron_jobs(
        cron_path=path, now=NOW + timedelta(minutes=1), shell_runner=_ok_shell
    )
    assert result["fired"] == 0
    job = _read_jobs(path)[0]
    assert job["started_at"]  # marker preserved, untouched


def test_marker_persisted_before_dispatch_and_cleared_after(tmp_path: Path) -> None:
    """The in-flight marker is on disk BEFORE the runner starts (crash
    window) and is cleared + last_run stamped when the run finishes."""
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [{"name": "m", "command": "echo hi", "cron_expression": "* * * * *"}],
    )
    seen_on_disk: list[bool] = []

    def observing_runner(command: str, job: dict[str, Any]) -> tuple[str, str]:
        # Simulate the crash window: read the persisted file mid-run.
        job["pid"] = 424242  # as the subprocess runner would record
        seen_on_disk.append(bool(_read_jobs(path)[0].get("started_at")))
        return "ok", "done"

    run_due_cron_jobs(cron_path=path, now=NOW, shell_runner=observing_runner)
    assert seen_on_disk == [True]  # marker was persisted before dispatch
    job = _read_jobs(path)[0]
    assert "started_at" not in job
    assert "pid" not in job
    assert job["last_status"] == "ok"
    assert job["last_run"] is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group recovery contract")
def test_real_subprocess_pid_is_persisted_and_shutdown_is_cooperative(tmp_path: Path) -> None:
    """The real pgid reaches disk while the child is alive, and a service
    stop terminates that group instead of leaving the executor thread stuck."""
    import os
    import shlex
    import sys
    import threading
    import time

    from runtime.platform.process.tree import terminate_pid_tree

    path = tmp_path / "cron_jobs.json"
    command = shlex.join([sys.executable, "-c", "import time; time.sleep(30)"])
    _write_jobs(path, [{"name": "long", "command": command, "cron_expression": "* * * * *"}])
    stop_event = threading.Event()
    result: dict[str, Any] = {}

    def _run() -> None:
        result.update(run_due_cron_jobs(cron_path=path, now=NOW, stop_event=stop_event))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = _read_jobs(path)[0]
            if isinstance(job.get("pid"), int):
                child_pid = job["pid"]
                break
            time.sleep(0.02)
        assert child_pid is not None, "child process-group id was never persisted"
        assert _read_jobs(path)[0]["started_at"]
        os.killpg(child_pid, 0)
    finally:
        stop_event.set()
        worker.join(timeout=7)
        if child_pid is not None and worker.is_alive():
            terminate_pid_tree(child_pid, grace_s=0.1, kill_wait_s=0.5)
            worker.join(timeout=2)

    assert not worker.is_alive()
    assert result["fired"] == 1
    job = _read_jobs(path)[0]
    assert job["last_status"] == "interrupted"
    assert "started_at" not in job
    assert "pid" not in job
    with pytest.raises(ProcessLookupError):
        os.killpg(child_pid, 0)


def test_recover_clears_stale_marker_and_prevents_refire(tmp_path: Path) -> None:
    """Startup recovery: a marker with a dead pid is cleared, recorded as
    interrupted, and last_run is stamped so the job does not re-fire."""
    from runtime.execution.cron_executor import recover_interrupted_cron_jobs

    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "crashed",
                "command": "echo hi",
                "cron_expression": "* * * * *",
                "started_at": NOW.isoformat(),
                "pid": 99999999,  # almost certainly dead
            }
        ],
    )
    result = recover_interrupted_cron_jobs(cron_path=path)
    assert result["ok"] is True
    assert result["interrupted"] == 1
    job = _read_jobs(path)[0]
    assert "started_at" not in job
    assert "pid" not in job
    assert job["last_status"] == "interrupted"
    assert job["last_run"] is not None
    # After recovery the job must NOT fire on the catch-up tick (no double run).
    fired = run_due_cron_jobs(
        cron_path=path, now=NOW + timedelta(minutes=1), shell_runner=_ok_shell
    )
    assert fired["fired"] == 0


def test_recover_kills_live_orphan_process_group(tmp_path: Path) -> None:
    """Startup recovery kills a surviving process group left by a crash."""
    import sys as _sys

    from runtime.execution.cron_executor import recover_interrupted_cron_jobs

    orphan = subprocess.Popen(  # noqa: S603
        [_sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        path = tmp_path / "cron_jobs.json"
        _write_jobs(
            path,
            [
                {
                    "name": "orphan",
                    "command": "sleep 30",
                    "cron_expression": "* * * * *",
                    "started_at": NOW.isoformat(),
                    "pid": orphan.pid,
                }
            ],
        )
        result = recover_interrupted_cron_jobs(cron_path=path)
        assert result["interrupted"] == 1
        assert orphan.poll() is not None  # process group was killed
    finally:
        if orphan.poll() is None:
            orphan.kill()


# ═══════════════════════════════════════════════════════════
# Audit P-08: ledger tail read + midpoint trim (O(tail), not O(file))
# ═══════════════════════════════════════════════════════════


def test_read_run_ledger_returns_only_newest_tail(tmp_path: Path) -> None:
    from runtime.execution.cron_executor import read_run_ledger

    ledger = tmp_path / "cron_runs.jsonl"
    lines = []
    for i in range(200):
        lines.append(json.dumps({"name": f"job{i}", "status": "ok"}))
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    runs = read_run_ledger(ledger, limit=5)
    assert len(runs) == 5
    assert [r["name"] for r in runs] == ["job199", "job198", "job197", "job196", "job195"]


def test_read_run_ledger_mid_file_cut_skips_partial_line(tmp_path: Path) -> None:
    """A tail read that starts mid-line must not return a corrupt record."""
    from runtime.execution.cron_executor import read_run_ledger

    ledger = tmp_path / "cron_runs.jsonl"
    ledger.write_text(
        "".join(json.dumps({"name": f"job{i}", "status": "ok"}) + "\n" for i in range(30)),
        encoding="utf-8",
    )
    runs = read_run_ledger(ledger, limit=3)
    assert len(runs) == 3
    assert all(r["name"].startswith("job") for r in runs)


def test_trim_ledger_oldest_half_keeps_valid_records(tmp_path: Path) -> None:
    from runtime.execution.cron_executor import (
        _append_run_ledger,
        _trim_ledger_oldest_half,
        read_run_ledger,
    )

    ledger = tmp_path / "cron_runs.jsonl"
    records = [{"name": f"job{i}", "status": "ok"} for i in range(20)]
    _append_run_ledger(ledger, records)

    before = len(read_run_ledger(ledger, limit=1000))
    assert before == 20

    _trim_ledger_oldest_half(ledger)

    after = read_run_ledger(ledger, limit=1000)
    assert len(after) < before, "oldest records were not dropped"
    assert len(after) >= 9  # roughly half; the cut line itself is dropped
    # Every surviving record is a valid, complete JSON record.
    for line in ledger.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        assert "name" in rec

