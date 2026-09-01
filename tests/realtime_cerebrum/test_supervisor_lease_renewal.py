"""Realtime turns renew their TaskSupervisor lease during execution.

The execution/loops controller path heartbeats the supervisor lease so a
long run is not cut off by the default 300s TTL. The realtime react loop
had no such renewal: a turn that outlived the TTL failed at finish with
"lease is no longer current" and stayed a zombie "running" task. The
consumer loop now renews the lease (throttled to lease_ttl/3) whenever
the turn has a registered supervisor task.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.realtime_cerebrum import _helpers


@pytest.fixture()
def gateway_with_supervisor(tmp_path: Any) -> Any:
    """Realtime gateway wired with a real TaskSupervisor (tiny TTL)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.process._task_supervisor_store import TaskSupervisorStore
    from runtime.platform.process.task_supervisor import TaskSupervisor
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    supervisor = TaskSupervisor(
        TaskSupervisorStore(tmp_path / "task_runs.json"),
        lease_ttl_seconds=600.0,
    )
    runtime = CerebrumRuntime(
        stack=object(),  # unused by the fake loop
        agent=object(),
        logs_root=str(tmp_path / "threads"),
        task_supervisor=supervisor,
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client, tmp_path / "threads", supervisor


def test_realtime_turn_renews_supervisor_lease(
    gateway_with_supervisor: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, supervisor = gateway_with_supervisor

    import runtime.core.cerebrum.react_loop as rl
    import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

    # Speed up both the queue keepalive and the lease renewal cadence so
    # the assertion runs sub-second instead of waiting on real TTLs.
    monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", 0.02)
    monkeypatch.setattr(drive_rs, "_lease_renewal_interval_s", lambda ttl: 0.02)

    def slow_stream(*_args: Any, **_kwargs: Any) -> Any:
        # Emit react_started with a supervisor task id, then trickle
        # deltas slowly so the consumer loop has idle stretches in which
        # the lease renewal is due.
        yield {
            "type": "react_started",
            "task_id": "renew-task-1",
            "thread_id": _kwargs.get("thread_id"),
        }
        for _ in range(15):
            time.sleep(0.02)
            yield {"type": "text_delta", "delta": "."}
        yield {"type": "react_completed"}

    monkeypatch.setattr(rl, "stream_react_loop", slow_stream)

    with client.websocket_connect("/api/realtime") as ws:
        result = _helpers.drive(
            ws,
            params={
                "threadId": "th_lease_renew",
                "input": [{"type": "text", "text": "run a long task"}],
                "approvalPolicy": "never",
            },
        )

    record = supervisor.store.get("renew-task-1")
    assert record is not None, "react_started must register the task with the supervisor"
    # heartbeat() stamps ``heartbeat_at`` (and extends ``expires_at`` while
    # the task is live). The terminal transition clears the lease object
    # itself, so the renewal is observable via the heartbeat timestamp.
    assert record.heartbeat_at, "lease must be renewed via supervisor heartbeat"
    assert record.heartbeat_at >= record.started_at, "renewal must happen after the task started"

    # The turn still completes normally.
    assert any(n.method == "turn/completed" for n in result["notifications"])


def test_no_supervisor_turn_completes_normally(
    gateway_with_supervisor: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression guard: when no supervisor is wired (tests, minimal
    # deployments) the renewal helper must be a no-op, not a crash.
    import runtime.core.cerebrum.react_loop as rl
    import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

    client, _, _ = gateway_with_supervisor

    monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", 0.02)

    def quick_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield {"type": "react_started", "task_id": "no-sup-1"}
        yield {"type": "text_delta", "delta": "done"}
        yield {"type": "react_completed"}

    monkeypatch.setattr(rl, "stream_react_loop", quick_stream)

    with client.websocket_connect("/api/realtime") as ws:
        result = _helpers.drive(
            ws,
            params={
                "threadId": "th_no_sup",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    assert any(n.method == "turn/completed" for n in result["notifications"])

