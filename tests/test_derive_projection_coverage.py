"""Dense coverage for journal derive projections (audit Q-05)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from runtime.memory.journal._journal_models import (
    AssistantChunkEvent,
    TokenUsageEvent,
)
from runtime.memory.journal.derive import (
    derive_assistant_stream,
    derive_session_usage,
)
from runtime.memory.journal.journal import InMemoryJournal


def _chunk(iter_no: int, text: str, kind: str = "text-delta") -> AssistantChunkEvent:
    return AssistantChunkEvent(
        ts=datetime(2026, 1, 1),
        thread_id="t",
        task_id=str(uuid4()),
        iteration=iter_no,
        kind=kind,
        delta=text,
    )


def test_derive_assistant_stream_groups_by_iteration() -> None:
    j = InMemoryJournal()
    j.write(_chunk(1, "Hello"))
    j.write(_chunk(1, " world"))
    j.write(_chunk(2, "Second"))
    j.write(_chunk(2, " answer", kind="reasoning-delta"))
    stream = derive_assistant_stream(j)
    assert [s.iteration for s in stream] == [1, 2]
    assert stream[0].text == "Hello world"
    assert stream[0].chunk_count == 2
    # kind filter + iteration filter
    only_text = derive_assistant_stream(j, kind="text-delta")
    assert only_text[1].text == "Second"
    iter2 = derive_assistant_stream(j, iteration=2)
    assert [s.iteration for s in iter2] == [2]
    empty = derive_assistant_stream(InMemoryJournal())
    assert empty == []


def test_derive_session_usage_filters_by_session() -> None:
    j = InMemoryJournal()
    j.write(
        TokenUsageEvent(
            ts=datetime(2026, 1, 1),
            thread_id="t",
            task_id=str(uuid4()),
            session_id="A",
            iteration=1,
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.01,
            model="m1",
        )
    )
    j.write(
        TokenUsageEvent(
            ts=datetime(2026, 1, 1),
            thread_id="t",
            task_id=str(uuid4()),
            session_id="B",
            iteration=2,
            input_tokens=1,
            output_tokens=1,
        )
    )
    all_records = derive_session_usage(j)
    assert len(all_records) == 2
    only_a = derive_session_usage(j, session_id="A")
    assert len(only_a) == 1
    assert only_a[0].input_tokens == 10 and only_a[0].cost_usd == 0.01
    assert only_a[0].model == "m1"
    assert derive_session_usage(j, session_id="missing") == []

