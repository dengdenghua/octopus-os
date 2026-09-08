from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from runtime.memory.journal import JournalRecoveryReadError, JSONLJournal
from runtime.memory.journal.journal import ReactCheckpointEvent
from runtime.platform.models import TaskId
from runtime.safety.recovery.tenant_scope import read_recovery_events
from runtime.sensing.gateway.streaming_journal import StreamingJournal


def _checkpoint(task_id: TaskId) -> ReactCheckpointEvent:
    return ReactCheckpointEvent(
        task_id=task_id,
        iteration_completed=1,
        max_iterations=4,
        messages_snapshot=[{"role": "user", "content": "continue"}],
        steps_snapshot=[{"iteration": 1, "action": "read_file", "observation": "ok"}],
    )


def test_recovery_read_rejects_a_new_bad_row_instead_of_falling_back(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    task_id = TaskId(uuid4())
    journal = JSONLJournal(path)
    journal.write(_checkpoint(task_id))
    # A normal projection remains backward-compatible and exposes the last
    # parseable event; recovery must not mistake that prefix for a complete log.
    with path.open("ab") as stream:
        stream.write(b"{this is not a journal row}\n")

    assert len(journal.read_by_type("react_checkpoint")) == 1
    with pytest.raises(JournalRecoveryReadError):
        journal.read_by_type_for_recovery("react_checkpoint")
    with pytest.raises(JournalRecoveryReadError):
        read_recovery_events(journal, "react_checkpoint")


def test_streaming_recovery_read_preserves_inner_strictness(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    task_id = TaskId(uuid4())
    inner = JSONLJournal(path)
    inner.write(_checkpoint(task_id))
    path.write_bytes(path.read_bytes() + b"not-json\n")

    wrapped = StreamingJournal(inner)
    with pytest.raises(JournalRecoveryReadError):
        wrapped.read_by_type_for_recovery("react_checkpoint")
