"""Journal-based pause recovery tests.

Covers the terminal-trajectory filter in ``recover_from_journal``: a task
whose journal carries a terminal ``trajectory`` (completed / failed /
cancelled) must NOT be resurrected as paused/pending on restart - only
genuinely in-flight tasks (checkpoint, no terminal trajectory) recover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.core.cerebrum.pause_control import PauseController


@dataclass
class _FakeCheckpoint:
    task_id: str = ""
    has_final_answer: bool = False
    iteration_completed: int = 3
    conversation_id: str = ""
    agent_id: str = ""


@dataclass
class _FakeTrajectory:
    task_id: str = ""
    success: bool = True
    disposition: str = "completed"


@dataclass
class _FakeJournal:
    events: list[tuple[str, Any]] = field(default_factory=list)

    def read_by_type(self, event_type: str) -> list[Any]:
        return [payload for kind, payload in self.events if kind == event_type]


def _make_controller(tmp_path: Any) -> PauseController:
    return PauseController(store_path=tmp_path / "pause.json", autoload=False)


def test_mid_flight_task_recovers_as_pending(tmp_path: Any) -> None:
    """Checkpoint with no terminal trajectory and no pause events -> the
    restart surfaces a Continue entry for the interrupted task."""
    ctrl = _make_controller(tmp_path)
    journal = _FakeJournal(
        events=[("react_checkpoint", _FakeCheckpoint(task_id="t1", conversation_id="thr-1"))]
    )
    assert ctrl.recover_from_journal(journal) == 1
    pending = ctrl.list_pending()
    assert any(str(p.task_id) == "t1" for p in pending)


def test_completed_task_with_trajectory_is_not_resurrected(tmp_path: Any) -> None:
    """A terminal trajectory written after the last checkpoint means the
    task finished; the stale checkpoint must not become a phantom
    'waiting to continue' entry after restart."""
    ctrl = _make_controller(tmp_path)
    journal = _FakeJournal(
        events=[
            ("react_checkpoint", _FakeCheckpoint(task_id="t2")),
            ("trajectory", _FakeTrajectory(task_id="t2", success=True)),
        ]
    )
    assert ctrl.recover_from_journal(journal) == 0
    assert not any(str(p.task_id) == "t2" for p in ctrl.list_pending())


def test_cancelled_trajectory_blocks_recovery(tmp_path: Any) -> None:
    """A cancelled task's trajectory (written at Stop since the cancelled
    branch persists one) is terminal - it must not be resumable."""
    ctrl = _make_controller(tmp_path)
    journal = _FakeJournal(
        events=[
            ("react_checkpoint", _FakeCheckpoint(task_id="t3")),
            (
                "trajectory",
                _FakeTrajectory(task_id="t3", success=False, disposition="cancelled"),
            ),
        ]
    )
    assert ctrl.recover_from_journal(journal) == 0


def test_trajectory_for_other_task_does_not_block(tmp_path: Any) -> None:
    """The filter is per task_id - a trajectory for a different task must
    not suppress an unrelated in-flight checkpoint."""
    ctrl = _make_controller(tmp_path)
    journal = _FakeJournal(
        events=[
            ("react_checkpoint", _FakeCheckpoint(task_id="t4", conversation_id="thr-4")),
            ("trajectory", _FakeTrajectory(task_id="other")),
        ]
    )
    assert ctrl.recover_from_journal(journal) == 1
    assert any(str(p.task_id) == "t4" for p in ctrl.list_pending())

