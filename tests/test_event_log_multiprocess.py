from __future__ import annotations

import multiprocessing
from pathlib import Path

from runtime.memory.threads.event_log import EventLog, LoggedEvent
from runtime.protocol import SteeringUserMessageItem, Turn


def _append_large_events(path: str, worker: int, count: int) -> None:
    log = EventLog(path)
    payload = "x" * 16_384
    for index in range(count):
        timeline_sequence = log.reserve_timeline_sequence("turn-live")
        log.append(
            LoggedEvent(
                event="turn_updated",
                eventId=f"worker-{worker}-{index}",
                threadId="thread-multiprocess",
                turnId="turn-live",
                payload={
                    "worker": worker,
                    "index": index,
                    "blob": payload,
                    "timelineSequence": timeline_sequence,
                },
            )
        )


def test_event_log_large_appends_are_process_safe(tmp_path: Path) -> None:
    path = tmp_path / "shared.jsonl"
    worker_count = 4
    events_per_worker = 30
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_append_large_events,
            args=(str(path), worker, events_per_worker),
        )
        for worker in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    snapshot = EventLog(path).snapshot()
    expected = worker_count * events_per_worker
    assert snapshot.cursor == expected
    assert len(snapshot.events) == expected
    assert {event.event_id for _, event in snapshot.events} == {
        f"worker-{worker}-{index}"
        for worker in range(worker_count)
        for index in range(events_per_worker)
    }
    assert sorted(event.payload["timelineSequence"] for _, event in snapshot.events) == list(
        range(1, expected + 1)
    )


def test_timeline_allocator_recovers_from_the_append_only_log(tmp_path: Path) -> None:
    path = tmp_path / "restart.jsonl"
    log = EventLog(path)
    turn = Turn(thread_id="thread-restart")
    log.turn_started(turn.thread_id, turn)
    item = SteeringUserMessageItem(
        text="persist before restart",
        targetTurnId=turn.id,
        timelineSequence=1,
    )
    log.item_started(turn.thread_id, turn.id, item)
    log.item_completed(turn.thread_id, turn.id, item)

    # The sidecar is deliberately disposable. A new runtime can rebuild its
    # next sequence solely from the durable conversation log.
    path.with_suffix(path.suffix + ".timeline").unlink(missing_ok=True)
    restarted = EventLog(path)
    assert restarted.reserve_timeline_sequence(turn.id) == 2
    assert EventLog(path).reserve_timeline_sequence(turn.id) == 3


def test_event_log_tail_reads_only_new_complete_events(tmp_path: Path) -> None:
    path = tmp_path / "tail.jsonl"
    log = EventLog(path)
    log.append(
        LoggedEvent(
            event="thread_started",
            eventId="first",
            threadId="thread-tail",
        )
    )

    first, offset = log.tail_events(0)
    assert [event.event_id for event in first] == ["first"]
    assert offset == path.stat().st_size

    log.append(
        LoggedEvent(
            event="turn_updated",
            eventId="second",
            threadId="thread-tail",
            turnId="turn-tail",
        )
    )
    second, next_offset = log.tail_events(offset)
    assert [event.event_id for event in second] == ["second"]
    assert next_offset > offset
    assert log.tail_events(next_offset) == ([], next_offset)

