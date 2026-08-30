from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import ItemStatus, Turn, TurnStatus
from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState


class _BridgeStateStub:
    agent_message = None

    async def flush(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    @staticmethod
    def prose_status_for_turn(_status: TurnStatus) -> ItemStatus:
        return ItemStatus.COMPLETED

    async def finalize_workbench(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _RuntimeStub:
    _max_iterations = 2
    _task_supervisor = None
    _workspaces = None
    _trace_store = None

    def __init__(self, *, real_bridge: bool = False) -> None:
        self._stack = SimpleNamespace(journal=None)
        self._orchestrator_bridge_tasks: set[asyncio.Task[Any]] = set()
        self._real_bridge = real_bridge

    def _make_bridge_state(self, *_args: Any, **_kwargs: Any) -> Any:
        if self._real_bridge:
            return _ReactBridgeState(enable_adaptive_batching=False)
        return _BridgeStateStub()

    def _drain_turn_steering(self, _turn_id: str) -> list[str]:
        return []

    def _record_react_trace_event(self, _turn: Turn, _event: dict[str, Any]) -> None:
        return None

    async def _publish_discovered_steering(self, *_args: Any) -> None:
        return None


class _EmitterStub:
    def __init__(self, *, interrupted: bool = False, fail_notify: bool = False) -> None:
        self.interrupted = interrupted
        self.fail_notify = fail_notify

    def is_turn_interrupted(self, _turn_id: str) -> bool:
        return self.interrupted

    def get_interrupt_reason(self, _turn_id: str) -> str | None:
        return "test interrupt" if self.interrupted else None

    async def notify(self, *_args: Any, **_kwargs: Any) -> None:
        if self.fail_notify:
            raise ConnectionError("notification transport failed")


def _intent() -> ParsedIntent:
    return ParsedIntent(
        raw="write the file",
        intent_type="task",
        normalized_goal="write the file",
        user_context={},
    )


def _tool_script(side_effect: threading.Event):
    def _stream(*_args: Any, **_kwargs: Any):
        yield {
            "type": "tool_start",
            "tool_name": "write_file",
            "tool_call_id": "call-audit",
            "input_preview": {"path": "result.txt"},
        }
        # This line represents the real handler reached by the next call to
        # the generator. It must remain unreachable until tool_start is
        # durably applied by the async consumer.
        side_effect.set()
        yield {
            "type": "tool_end",
            "tool_name": "write_file",
            "tool_call_id": "call-audit",
            "status": "success",
        }
        yield {"type": "react_completed"}

    return _stream


async def _run_drive(
    runtime: _RuntimeStub,
    log: Any,
    emitter: _EmitterStub,
) -> Turn:
    from runtime.sensing.gateway._realtime_react_stream_drive import _drive_react

    turn = Turn(threadId="thread-audit")
    await _drive_react(
        runtime,
        turn,
        log,
        emitter,
        _intent(),
        None,  # type: ignore[arg-type]
        None,
    )
    return turn


@pytest.mark.asyncio
async def test_tool_side_effect_waits_until_start_event_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    side_effect = threading.Event()
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()

    async def _blocked_apply(
        _runtime: Any,
        turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            apply_entered.set()
            await release_apply.wait()
        elif event.get("type") == "react_completed":
            turn.status = TurnStatus.COMPLETED

    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_script(side_effect))
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _blocked_apply)

    task = asyncio.create_task(_run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()))
    await asyncio.wait_for(apply_entered.wait(), timeout=1.0)
    assert not await asyncio.to_thread(side_effect.wait, 0.1)

    release_apply.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert side_effect.is_set()


@pytest.mark.asyncio
async def test_native_tool_loop_yield_is_a_real_audit_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.sensing.gateway._realtime_react_stream_drive as drive
    import runtime.sensing.gateway.tool_bridge as tool_bridge

    side_effect = threading.Event()
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()

    def _native_stream(*_args: Any, **_kwargs: Any):
        yield (
            "tool_start",
            {
                "id": "native-call",
                "name": "write_file",
                "input": {"path": "result.txt"},
                "iteration": 1,
            },
            None,
        )
        side_effect.set()
        yield (
            "tool_end",
            {
                "id": "native-call",
                "name": "write_file",
                "output": "ok",
                "is_error": False,
                "iteration": 1,
            },
            None,
        )
        yield ("done", None, None)

    async def _blocked_apply(
        _runtime: Any,
        turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            apply_entered.set()
            await release_apply.wait()
        elif event.get("type") == "react_completed":
            turn.status = TurnStatus.COMPLETED

    monkeypatch.setattr(tool_bridge, "stream_agentic_fallback", _native_stream)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: True)
    monkeypatch.setattr(drive, "_apply_react_event", _blocked_apply)

    task = asyncio.create_task(_run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()))
    await asyncio.wait_for(apply_entered.wait(), timeout=1.0)
    assert not await asyncio.to_thread(side_effect.wait, 0.1)

    release_apply.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert side_effect.is_set()


@pytest.mark.asyncio
async def test_full_queue_drains_after_producer_done_when_apply_was_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion must not depend on a capacity-bound queue sentinel.

    Once the consumer removes event 1 and blocks in its reducer, events
    2..64 plus the terminal event fill every remaining queue slot. Holding
    the reducer beyond the former five-second sentinel timeout reproduces
    the exact lost-sentinel window; after release, all events must still be
    applied in order and the driver must terminate promptly.
    """
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()
    producer_body_done = threading.Event()
    applied: list[int | str] = []

    def _full_queue_stream(*_args: Any, **_kwargs: Any):
        for sequence in range(1, 65):
            yield {"type": "text_delta", "delta": str(sequence), "sequence": sequence}
        yield {"type": "react_completed"}
        producer_body_done.set()

    async def _blocked_first_apply(
        _runtime: Any,
        turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("sequence") == 1:
            apply_entered.set()
            await release_apply.wait()
        if isinstance(event.get("sequence"), int):
            applied.append(event["sequence"])
        else:
            applied.append(str(event.get("type")))
        if event.get("type") == "react_completed":
            turn.status = TurnStatus.COMPLETED

    monkeypatch.setattr(react_loop, "stream_react_loop", _full_queue_stream)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _blocked_first_apply)

    task = asyncio.create_task(_run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()))
    await asyncio.wait_for(apply_entered.wait(), timeout=1.0)
    assert await asyncio.to_thread(producer_body_done.wait, 1.0)
    await asyncio.sleep(5.2)
    assert not task.done()

    release_apply.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert applied == [*range(1, 65), "react_completed"]


@pytest.mark.asyncio
async def test_critical_structural_enqueue_timeout_fails_turn_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()
    critical_attempted = threading.Event()
    advanced_past_critical = threading.Event()

    def _blocked_queue_stream(*_args: Any, **_kwargs: Any):
        # The consumer removes sequence 1 before blocking; 2..65 then fill
        # every queue slot, so the critical receipt below hits the bounded
        # enqueue timeout deterministically.
        for sequence in range(1, 66):
            yield {"type": "text_delta", "delta": str(sequence), "sequence": sequence}
        critical_attempted.set()
        yield {
            "type": "tool_end",
            "tool_name": "write_file",
            "tool_call_id": "call-full",
            "status": "success",
        }
        advanced_past_critical.set()

    async def _blocked_first_apply(
        _runtime: Any,
        _turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("sequence") == 1:
            apply_entered.set()
            await release_apply.wait()

    monkeypatch.setattr(react_loop, "stream_react_loop", _blocked_queue_stream)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _blocked_first_apply)
    monkeypatch.setattr(drive, "_REACT_QUEUE_PUT_TIMEOUT_S", 0.05)

    task = asyncio.create_task(_run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()))
    await asyncio.wait_for(apply_entered.wait(), timeout=1.0)
    assert await asyncio.to_thread(critical_attempted.wait, 1.0)
    await asyncio.sleep(0.2)
    assert not advanced_past_critical.is_set()

    release_apply.set()
    turn = await asyncio.wait_for(task, timeout=2.0)
    assert turn.status is TurnStatus.FAILED
    assert turn.outcome_reason == "react_structural_event_delivery_failed"
    assert turn.error is not None
    assert turn.error["code"] == "react_structural_event_delivery_failed"


@pytest.mark.asyncio
async def test_terminal_apply_failure_is_not_counted_as_terminal_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    def _terminal_stream(*_args: Any, **_kwargs: Any):
        yield {"type": "react_completed"}

    async def _failing_terminal_apply(
        _runtime: Any,
        _turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "react_completed":
            raise OSError("terminal journal failed")

    monkeypatch.setattr(react_loop, "stream_react_loop", _terminal_stream)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _failing_terminal_apply)

    turn = await asyncio.wait_for(
        _run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()),
        timeout=2.0,
    )

    assert turn.status is TurnStatus.FAILED
    assert turn.outcome_reason == "react_structural_event_apply_failed"
    assert turn.error == {
        "code": "react_structural_event_apply_failed",
        "message": "terminal journal failed",
        "event_type": "react_completed",
        "exception_type": "OSError",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trailing_terminal", "overwriting_status", "trailing_failure"),
    [
        ("react_cancelled", TurnStatus.INTERRUPTED, False),
        ("react_paused", TurnStatus.PAUSED, False),
        ("react_completed", TurnStatus.COMPLETED, False),
        ("react_error", TurnStatus.FAILED, True),
    ],
)
async def test_later_terminal_cannot_mask_structural_apply_failure(
    monkeypatch: pytest.MonkeyPatch,
    trailing_terminal: str,
    overwriting_status: TurnStatus,
    trailing_failure: bool,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    def _receipt_then_terminal(*_args: Any, **_kwargs: Any):
        yield {
            "type": "tool_end",
            "tool_name": "write_file",
            "tool_call_id": "call-receipt",
            "status": "success",
        }
        yield {"type": trailing_terminal}

    async def _apply_with_failed_receipt(
        _runtime: Any,
        turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_end":
            raise OSError("tool receipt journal failed")
        if event.get("type") == trailing_terminal:
            if trailing_failure:
                raise RuntimeError("later terminal failure")
            # Model the real paused/cancelled reducers, which legitimately
            # mutate status while the driver is draining queued events.
            turn.status = overwriting_status

    monkeypatch.setattr(react_loop, "stream_react_loop", _receipt_then_terminal)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _apply_with_failed_receipt)

    turn = await asyncio.wait_for(
        _run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()),
        timeout=2.0,
    )

    assert turn.status is TurnStatus.FAILED
    assert turn.outcome_reason == "react_structural_event_apply_failed"
    assert turn.error == {
        "code": "react_structural_event_apply_failed",
        "message": "tool receipt journal failed",
        "event_type": "tool_end",
        "exception_type": "OSError",
    }


@pytest.mark.asyncio
async def test_tool_apply_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    side_effect = threading.Event()

    async def _failing_apply(
        _runtime: Any,
        turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            raise OSError("journal unavailable")
        if event.get("type") == "react_error":
            turn.status = TurnStatus.FAILED

    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_script(side_effect))
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _failing_apply)

    turn = await asyncio.wait_for(
        _run_drive(_RuntimeStub(), SimpleNamespace(), _EmitterStub()),
        timeout=2.0,
    )

    assert not side_effect.is_set()
    assert turn.status is TurnStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["fsync", "notify"])
async def test_tool_does_not_execute_when_durable_start_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.memory.threads.event_log as event_log_module
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    side_effect = threading.Event()
    real_apply = drive._apply_react_event

    async def _tool_start_only_apply(
        runtime: Any,
        turn: Turn,
        log: EventLog,
        emitter: Any,
        state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            await real_apply(runtime, turn, log, emitter, state, event)
        elif event.get("type") == "react_error":
            turn.status = TurnStatus.FAILED

    if failure_stage == "fsync":

        def _fail_fsync(_fd: int) -> None:
            raise OSError("fsync failed")

        monkeypatch.setattr(event_log_module.os, "fsync", _fail_fsync)

    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_script(side_effect))
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _tool_start_only_apply)

    turn = await asyncio.wait_for(
        _run_drive(
            _RuntimeStub(real_bridge=True),
            EventLog(tmp_path / f"{failure_stage}.jsonl"),
            _EmitterStub(fail_notify=failure_stage == "notify"),
        ),
        timeout=2.0,
    )

    assert not side_effect.is_set()
    assert turn.status is TurnStatus.FAILED


@pytest.mark.asyncio
async def test_interrupt_rejects_pending_tool_start_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    side_effect = threading.Event()
    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_script(side_effect))
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)

    turn = await asyncio.wait_for(
        _run_drive(
            _RuntimeStub(),
            SimpleNamespace(),
            _EmitterStub(interrupted=True),
        ),
        timeout=2.0,
    )

    assert not side_effect.is_set()
    assert turn.status is TurnStatus.CANCELLED


@pytest.mark.asyncio
async def test_interrupt_arriving_during_tool_start_apply_prevents_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    side_effect = threading.Event()
    apply_entered = asyncio.Event()
    release_apply = asyncio.Event()
    emitter = _EmitterStub()

    async def _blocked_apply(
        _runtime: Any,
        _turn: Turn,
        _log: Any,
        _emitter: Any,
        _state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            apply_entered.set()
            await release_apply.wait()

    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_script(side_effect))
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _blocked_apply)

    task = asyncio.create_task(_run_drive(_RuntimeStub(), SimpleNamespace(), emitter))
    await asyncio.wait_for(apply_entered.wait(), timeout=1.0)
    emitter.interrupted = True
    release_apply.set()

    turn = await asyncio.wait_for(task, timeout=2.0)
    assert not side_effect.is_set()
    assert turn.status is TurnStatus.CANCELLED

