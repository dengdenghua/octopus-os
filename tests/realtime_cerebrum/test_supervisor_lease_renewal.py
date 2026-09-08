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
    """Realtime gateway wired with a real TaskSupervisor and temporary store."""
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
    admitted: list[Any] = []
    renewed: list[tuple[str, int | None, Any, Any]] = []
    real_heartbeat = supervisor.heartbeat

    def heartbeat(
        task_id: str,
        *,
        expected_lease_token: int | None = None,
        expected_lease_incarnation: str | None = None,
    ) -> Any:
        before = supervisor.store.get(task_id)
        record = real_heartbeat(
            task_id,
            expected_lease_token=expected_lease_token,
            expected_lease_incarnation=expected_lease_incarnation,
        )
        renewed.append((task_id, expected_lease_token, expected_lease_incarnation, before, record))
        return record

    monkeypatch.setattr(supervisor, "heartbeat", heartbeat)

    def slow_stream(*_args: Any, **_kwargs: Any) -> Any:
        # Echo the already admitted server ID, then trickle
        # deltas slowly so the consumer loop has idle stretches in which
        # the lease renewal is due.
        task_id = str(_kwargs["execution_task_id"])
        record = supervisor.store.get(task_id)
        assert record is not None and record.lease is not None
        admitted.append(record)
        yield {
            "type": "react_started",
            "task_id": task_id,
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

    assert len(admitted) == 1
    initial = admitted[0]
    assert renewed, "Observe an actual live heartbeat, not the terminal transition's timestamp"
    for task_id, expected_token, expected_incarnation, before, after in renewed:
        assert task_id == initial.task_id
        assert expected_token == initial.lease.token
        assert before.status.value == after.status.value == "running"
        assert before.lease.token == after.lease.token == expected_token
        assert before.lease.incarnation == after.lease.incarnation == expected_incarnation
        assert expected_incarnation == initial.lease.incarnation
        assert after.lease.expires_at > before.lease.expires_at
    record = supervisor.store.get(initial.task_id)
    assert record is not None and record.status.value == "completed"

    # The turn still completes normally.
    assert result["response"].result["turn"]["status"] == "completed"
    assert any(n.method == "turn/completed" for n in result["notifications"])


def test_no_supervisor_turn_completes_normally(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression guard: when no supervisor is wired (tests, minimal
    # deployments) the renewal helper must be a no-op, not a crash.
    import runtime.core.cerebrum.react_loop as rl
    import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

    # conftest.gateway constructs CerebrumRuntime without a Supervisor.
    client, logs_root = gateway

    monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", 0.02)

    def quick_stream(*_args: Any, **_kwargs: Any) -> Any:
        from runtime.platform.process.session import current_session

        assert _kwargs.get("execution_task_id") is None
        assert current_session().execution_lease is None
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
    assert result["response"].result["turn"]["status"] == "completed"
    completions = [n for n in result["notifications"] if n.method == "turn/completed"]
    assert completions and completions[-1].params["turn"]["status"] == "completed"
    assert not (logs_root.parent / "task_runs.json").exists()
