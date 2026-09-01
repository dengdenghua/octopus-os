"""Tests for the memory block (MemoryProvider) of the composition layer."""

from __future__ import annotations

import uuid
from pathlib import Path

from runtime.memory.journal._journal_models import TaskStartedEvent
from runtime.memory.journal.journal import JSONLJournal
from runtime.memory.provider import JournalMemoryProvider, MemoryProvider


class _FakeJournal:
    """Minimal journal surface for adapter tests."""

    def __init__(self) -> None:
        self.events = []
        self.writes = []

    def write(self, event) -> None:
        self.writes.append(event)
        self.events.append(event)

    def read_all(self, *, scope=None):
        return list(self.events)

    def read_by_session(self, session_id: str):
        return [e for e in self.events if getattr(e, "conversation_id", "") == session_id]


def _task(uid: str, conversation_id: str | None = None) -> TaskStartedEvent:
    return TaskStartedEvent(
        task_id=uuid.UUID(uid) if _looks_like_uuid(uid) else uuid.uuid4(),
        conversation_id=conversation_id,
        total_nodes=2,
        strategy="linear",
        task_type="demo",
    )


def _looks_like_uuid(uid: str) -> bool:
    try:
        uuid.UUID(uid)
        return True
    except (ValueError, AttributeError):
        return False


def test_protocol_conformance():
    provider = JournalMemoryProvider(_FakeJournal())
    # runtime_checkable Protocol: isinstance() proves structural conformance.
    assert isinstance(provider, MemoryProvider)


def test_store_writes_and_reports_success():
    journal = _FakeJournal()
    provider = JournalMemoryProvider(journal)
    event = _task("t1")
    assert provider.store(event) is True
    assert journal.writes == [event]


def test_store_failure_returns_false_without_raising():
    class Boom:
        def write(self, _event):
            raise OSError("disk full")

    provider = JournalMemoryProvider(Boom())
    assert provider.store(_task("t1")) is False


def test_recall_all_capped_by_limit():
    journal = _FakeJournal()
    for index in range(10):
        journal.write(_task(f"t{index}"))
    provider = JournalMemoryProvider(journal)
    assert len(provider.recall(limit=3)) == 3
    assert len(provider.recall()) == 10


def test_recall_filters_by_event_type_and_session():
    journal = _FakeJournal()
    journal.write(_task("t1", conversation_id="sess-a"))
    journal.write(_task("t2"))

    provider = JournalMemoryProvider(journal)
    filtered = provider.recall(event_type="task_started")
    assert len(filtered) == 2
    by_session = provider.recall(session_id="sess-a")
    assert len(by_session) == 1
    assert by_session[0].conversation_id == "sess-a"


def test_forget_is_honest_for_append_only():
    provider = JournalMemoryProvider(_FakeJournal())
    assert provider.forget(["id-1", "id-2"]) == 0


def test_reflect_and_health():
    provider = JournalMemoryProvider(_FakeJournal())
    assert provider.reflect("low citation coverage") == []
    health = provider.health()
    assert health["provider"] == "journal"
    assert health["ok"] is True
    assert health["append_only"] is True


def test_real_journal_roundtrip(tmp_path: Path):
    journal = JSONLJournal(tmp_path / "events.jsonl")
    provider = JournalMemoryProvider(journal)
    event = _task("real-1")

    assert provider.store(event) is True
    recalled = provider.recall()
    assert len(recalled) == 1
    assert recalled[0].event_type == "task_started"
    assert recalled[0].task_type == "demo"

    # A second provider instance over the same file sees the same history
    # (durable, not in-memory).
    provider2 = JournalMemoryProvider(JSONLJournal(tmp_path / "events.jsonl"))
    assert [e.event_type for e in provider2.recall()] == ["task_started"]

