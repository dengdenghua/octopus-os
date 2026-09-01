"""Background-job registry tests (dsh ``packages/jobs`` port).

Exercises the local seam contract end to end: preflight, ownership
isolation, streaming reads, kill ordering, first-wins settlement, wait
timeout vs. settlement, completion notice suppression, teardown, and
listener containment.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from runtime.execution.jobs.registry import LocalJobRegistry
from runtime.execution.jobs.types import (
    JobHooks,
    JobOutcome,
    JobSnapshot,
    JobStart,
    is_terminal,
)


class _FakeProducer:
    """Producer with a controllable done future, optional stream, and an
    optional cancel hook (including a throwing one)."""

    def __init__(
        self,
        *,
        stream: list[str] | None = None,
        cancel_hook: Any = None,
    ) -> None:
        self.stream = stream or []
        self.cancel_hook = cancel_hook
        self.cancel_calls: list[str | None] = []
        self.done: asyncio.Future[JobOutcome] | None = None

    def start(
        self,
        *,
        kind: str = "test",
        label: str = "demo",
        owner: str | None = None,
        notify: Any = None,
        **extra: Any,
    ) -> JobStart:
        stream = list(self.stream)

        def run() -> JobHooks:
            self.done = asyncio.get_running_loop().create_future()
            cursor = 0

            def cancel(reason: str | None = None) -> None:
                self.cancel_calls.append(reason)
                if self.cancel_hook is not None:
                    self.cancel_hook(reason)

            def read_output() -> str:
                nonlocal cursor
                if cursor >= len(stream):
                    return ""
                text = stream[cursor]
                cursor += 1
                return text

            return JobHooks(
                cancel=cancel,
                done=self.done,
                read_output=read_output if stream else None,
            )

        return JobStart(
            kind=kind,
            label=label,
            owner=owner,
            notify=notify,
            run=run,
            **extra,
        )


async def _settle(
    registry: LocalJobRegistry,
    producer: _FakeProducer,
    outcome: JobOutcome,
    job_id: str = "test-1",
    caller: str | None = None,
) -> None:
    """Resolve the producer's done future from a worker thread so settle
    exercises the cross-thread path used by real background jobs. Polls the
    registry until the terminal state is recorded (the done callback runs on
    a later loop tick)."""
    done = producer.done
    assert done is not None

    def _resolve() -> None:
        done.get_loop().call_soon_threadsafe(
            lambda: done.set_result(outcome) if not done.done() else None
        )

    threading.Thread(target=_resolve, daemon=True).start()
    for _ in range(200):
        try:
            status = registry.get(job_id, caller).status
        except LookupError:
            return  # teardown removed the record after settlement
        if is_terminal(status):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not settle")


@pytest.mark.asyncio
async def test_start_registers_and_lists_by_owner() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer_a = _FakeProducer()
    producer_b = _FakeProducer()
    registry.start(producer_a.start(kind="subagent", owner="thr-a"))
    registry.start(producer_b.start(kind="bash", owner="thr-b"))
    registry.start(_FakeProducer().start(kind="open", owner=None))

    # Unowned jobs are open to every caller; owned jobs only to their owner.
    assert [s.id for s in registry.list("thr-a")] == ["subagent-1", "open-1"]
    assert [s.id for s in registry.list("thr-b")] == ["bash-1", "open-1"]
    assert [s.id for s in registry.list("other")] == ["open-1"]
    snapshot = registry.get("subagent-1", "thr-a")
    assert snapshot.kind == "subagent"
    assert snapshot.status == "running"
    assert snapshot.owner_session == "thr-a"
    assert snapshot.started_at > 0
    assert snapshot.finished_at is None
    assert snapshot.reported is False
    # Snapshots are fresh projections, never live state.
    assert registry.get("subagent-1", "thr-a") is not registry.get("subagent-1", "thr-a")


@pytest.mark.asyncio
async def test_ownership_fence() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    registry.start(_FakeProducer().start(owner="thr-a"))
    with pytest.raises(PermissionError):
        registry.get("test-1", "thr-b")
    with pytest.raises(PermissionError):
        registry.read("test-1", None)
    # Unowned jobs are open to any caller.
    registry.start(_FakeProducer().start(owner=None))
    assert registry.get("test-2", "thr-b").id == "test-2"


@pytest.mark.asyncio
async def test_preflight_rejects_bad_declarations() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    with pytest.raises(ValueError, match="kind"):
        registry.start(_FakeProducer().start(kind=""))
    with pytest.raises(ValueError, match="label"):
        registry.start(_FakeProducer().start(label=""))
    with pytest.raises(ValueError, match="output_limit_bytes"):
        registry.start(_FakeProducer().start(output_limit_bytes=0))
    with pytest.raises(ValueError, match="output_limit_bytes"):
        registry.start(_FakeProducer().start(output_limit_bytes=-3))

    # A throwing run() leaves nothing registered.
    def bad_run() -> JobHooks:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        registry.start(JobStart(kind="test", label="x", run=bad_run))
    assert registry.list(None) == []


@pytest.mark.asyncio
async def test_controller_gate() -> None:
    registry = LocalJobRegistry()
    with pytest.raises(RuntimeError, match="no job controller"):
        registry.start(_FakeProducer().start())
    detach = registry.attach_controller("test")
    registry.start(_FakeProducer().start())
    detach()
    with pytest.raises(RuntimeError, match="no job controller"):
        registry.start(_FakeProducer().start())


@pytest.mark.asyncio
async def test_concurrency_limit_per_owner() -> None:
    registry = LocalJobRegistry(max_concurrent_jobs_per_owner=2)
    registry.attach_controller("test")
    first = _FakeProducer()
    second = _FakeProducer()
    registry.start(first.start(owner="thr-a"))
    registry.start(second.start(owner="thr-a"))
    with pytest.raises(RuntimeError, match="job limit reached"):
        registry.start(_FakeProducer().start(owner="thr-a"))
    # A different owner bucket is unaffected.
    registry.start(_FakeProducer().start(owner="thr-b"))
    # Settlement frees the slot.
    await _settle(registry, first, JobOutcome(status="completed"), caller="thr-a")
    registry.start(_FakeProducer().start(owner="thr-a"))


@pytest.mark.asyncio
async def test_streaming_read_consumes_cursor() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer(stream=["a", "b", "c"])
    registry.start(producer.start())
    assert registry.read("test-1").text == "a"
    assert registry.read("test-1").text == "b"
    assert registry.read("test-1").text == "c"
    assert registry.read("test-1").text == ""
    # Streaming reads never mark reported.
    assert registry.get("test-1").reported is False


@pytest.mark.asyncio
async def test_final_output_read_is_terminal_and_idempotent() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer()
    registry.start(producer.start())
    # Live final-output job reads empty.
    read = registry.read("test-1")
    assert read.text == ""
    assert read.snapshot.reported is False
    await _settle(registry, producer, JobOutcome(status="completed", output="RESULT"))
    read = registry.read("test-1")
    assert read.text == "RESULT"
    assert read.snapshot.status == "completed"
    # Terminal reads mark reported; the output is idempotent, never consumed.
    assert registry.read("test-1").text == "RESULT"
    assert registry.get("test-1").reported is True


@pytest.mark.asyncio
async def test_kill_cancels_first_then_stops() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer()
    registry.start(producer.start(owner="thr-a"))
    outcome = registry.kill("test-1", "thr-a", "no longer needed")
    assert outcome == "requested"
    assert producer.cancel_calls == ["no longer needed"]
    assert registry.get("test-1", "thr-a").status == "stopping"
    assert registry.get("test-1", "thr-a").reported is True
    # A second kill on a stopping job requests again (stopping is not
    # terminal); once settled it reports and returns the sentinel.
    assert registry.kill("test-1", "thr-a") == "requested"
    assert producer.cancel_calls == ["no longer needed", None]
    await _settle(registry, producer, JobOutcome(status="killed"), caller="thr-a")
    assert registry.kill("test-1", "thr-a") == "already-finished"
    assert registry.get("test-1", "thr-a").reported is True


@pytest.mark.asyncio
async def test_kill_cancel_throw_leaves_state_unchanged() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer(cancel_hook=lambda _reason: (_ for _ in ()).throw(RuntimeError("no")))
    registry.start(producer.start())
    with pytest.raises(RuntimeError, match="no"):
        registry.kill("test-1")
    snapshot = registry.get("test-1")
    assert snapshot.status == "running"
    assert snapshot.reported is False


@pytest.mark.asyncio
async def test_settle_is_first_wins() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer()
    registry.start(producer.start())
    await _settle(registry, producer, JobOutcome(status="completed", output="ok", detail="done"))
    await _settle(registry, producer, JobOutcome(status="failed", detail="late"))
    snapshot = registry.get("test-1")
    assert snapshot.status == "completed"
    assert snapshot.detail == "done"
    assert snapshot.finished_at is not None
    # The terminal output lives behind read(), not on the snapshot.
    assert registry.read("test-1").text == "ok"
    assert registry.read("test-1").text == "ok"  # idempotent


@pytest.mark.asyncio
async def test_wait_timeout_returns_live_snapshot() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer()
    registry.start(producer.start())
    snapshot = await registry.wait("test-1", 50)
    assert snapshot.status == "running"
    assert snapshot.reported is False
    # Timeout is a success, not an error; the job stays alive.
    await registry.wait("test-1", 50)
    assert registry.get("test-1").status == "running"


@pytest.mark.asyncio
async def test_wait_settlement_marks_reported_and_suppresses_notice() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    notices: list[JobSnapshot] = []
    producer = _FakeProducer()
    registry.start(producer.start(notify=notices.append))
    waiter = asyncio.create_task(registry.wait("test-1", 5000))
    await asyncio.sleep(0)
    await _settle(registry, producer, JobOutcome(status="completed", output="ok"))
    snapshot = await waiter
    assert snapshot.status == "completed"
    assert snapshot.reported is True
    # A pending wait claimed the completion notice (dsh anti-suicide rule).
    assert notices == []


@pytest.mark.asyncio
async def test_notice_fires_only_for_unreported_settlement() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    notices: list[JobSnapshot] = []
    producer = _FakeProducer()
    registry.start(producer.start(owner="thr-a", notify=notices.append))
    await _settle(registry, producer, JobOutcome(status="failed", detail="crashed"), caller="thr-a")
    assert len(notices) == 1
    assert notices[0].id == "test-1"
    assert notices[0].status == "failed"
    assert notices[0].reported is False
    # A second settlement is first-wins suppressed.
    # A kill or read that claims the terminal state suppresses the notice.
    producer2 = _FakeProducer()
    registry.start(producer2.start(notify=notices.append))
    registry.kill("test-2")
    await _settle(registry, producer2, JobOutcome(status="killed"), job_id="test-2")
    assert len(notices) == 1


@pytest.mark.asyncio
async def test_read_before_settlement_suppresses_notice() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    notices: list[JobSnapshot] = []
    producer = _FakeProducer()
    registry.start(producer.start(notify=notices.append))
    registry.kill("test-1")
    await _settle(registry, producer, JobOutcome(status="killed"))
    assert notices == []


@pytest.mark.asyncio
async def test_done_rejection_fails_job() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer()
    registry.start(producer.start())

    def _reject() -> None:
        done = producer.done
        done.get_loop().call_soon_threadsafe(
            lambda: (
                done.set_exception(RuntimeError("contract violation")) if not done.done() else None
            )
        )

    threading.Thread(target=_reject, daemon=True).start()
    await asyncio.sleep(0.05)
    snapshot = registry.get("test-1")
    assert snapshot.status == "failed"
    assert "contract violation" in (snapshot.detail or "")


@pytest.mark.asyncio
async def test_done_listener_isolation() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    seen: list[str] = []
    registry.on_job_done(lambda snapshot, owner: (_ for _ in ()).throw(RuntimeError("boom")))
    registry.on_job_done(lambda snapshot, owner: seen.append(snapshot.id))
    producer = _FakeProducer()
    registry.start(producer.start())
    await _settle(registry, producer, JobOutcome(status="completed"))
    assert seen == ["test-1"]


@pytest.mark.asyncio
async def test_changed_listener_and_unregister() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    changed: list[str | None] = []
    unregister = registry.on_jobs_changed(changed.append)
    producer = _FakeProducer()
    registry.start(producer.start(owner="thr-a"))
    assert "thr-a" in changed
    unregister()
    registry.kill("test-1", "thr-a")
    assert changed.count("thr-a") == 1


@pytest.mark.asyncio
async def test_dispose_owned_cancels_awaits_and_removes() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer_a = _FakeProducer()
    producer_b = _FakeProducer()
    registry.start(producer_a.start(owner="thr-a"))
    registry.start(producer_b.start(owner="thr-b"))
    await _settle(registry, producer_a, JobOutcome(status="completed", output="x"), caller="thr-a")
    live = _FakeProducer()
    registry.start(live.start(owner="thr-a"))
    disposing = asyncio.create_task(registry.dispose_owned("thr-a"))
    await asyncio.sleep(0)
    # The live job was cancelled for teardown; settle it so disposal joins.
    assert live.cancel_calls == ["owner disposed"]
    await _settle(registry, live, JobOutcome(status="killed"), caller="thr-a")
    await disposing
    assert registry.list("thr-a") == []
    # The other owner is untouched.
    assert [s.id for s in registry.list("thr-b")] == ["test-2"]


@pytest.mark.asyncio
async def test_teardown_cancel_throw_force_fails_record() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _FakeProducer(
        cancel_hook=lambda _reason: (_ for _ in ()).throw(RuntimeError("stuck"))
    )
    registry.start(producer.start(owner="thr-a"))
    await registry.dispose_owned("thr-a")
    assert registry.list("thr-a") == []
    assert producer.cancel_calls == ["owner disposed"]


@pytest.mark.asyncio
async def test_dispose_all_closes_listeners_and_runs_cleanups() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    cleanups: list[str] = []
    producer = _FakeProducer()
    registry.start(
        producer.start(
            owner="thr-a",
            owner_cleanup=lambda: cleanups.append("cleanup"),
        )
    )
    disposing = asyncio.create_task(registry.dispose_all())
    await asyncio.sleep(0)
    assert producer.cancel_calls == ["jobs service disposed"]
    await _settle(registry, producer, JobOutcome(status="killed"), caller="thr-a")
    await disposing
    assert registry.list(None) == []
    assert cleanups == ["cleanup"]


@pytest.mark.asyncio
async def test_job_ids_are_kind_scoped() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    registry.start(_FakeProducer().start(kind="subagent"))
    registry.start(_FakeProducer().start(kind="subagent"))
    registry.start(_FakeProducer().start(kind="bash"))
    assert [s.id for s in registry.list(None)] == [
        "subagent-1",
        "subagent-2",
        "bash-1",
    ]


@pytest.mark.asyncio
async def test_unknown_job_fails_loud() -> None:
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    with pytest.raises(LookupError, match="unknown job"):
        registry.get("nope-1")
    with pytest.raises(LookupError, match="unknown job"):
        await registry.wait("nope-1", 100)

