"""Unit tests for ``runtime.core.cerebrum.rewind``.

Uses lightweight fakes instead of a real Journal — the module is
duck-typed against ``read_by_type(event_type)``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from runtime.core.cerebrum.rewind import (
    list_rewind_points,
    rewind_to_checkpoint,
)


@dataclass
class _FakeEvent:
    event_id: str
    event_type: str
    task_id: str
    ts: str
    # react_checkpoint fields
    iteration_completed: int = 0
    working_set_snapshot: list[dict] = field(default_factory=list)
    current_phase: str = ""
    has_final_answer: bool = False
    # file_op fields (action = "write" / "edit" / "delete")
    path: str = ""
    action: str = "write"
    rollback: dict | None = None
    # step fields (sucker_id + action = command string)
    sucker_id: str = ""


class _FakeJournal:
    def __init__(self, events: list[_FakeEvent]) -> None:
        self._events = events

    def read_by_type(self, event_type: str) -> list[_FakeEvent]:
        return [e for e in self._events if e.event_type == event_type]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_checkpoint(
    task_id: str,
    ts: str,
    iteration: int,
    *,
    working_set: list[dict] | None = None,
    phase: str = "executing",
    final: bool = False,
) -> _FakeEvent:
    return _FakeEvent(
        event_id=f"ckpt-{iteration}",
        event_type="react_checkpoint",
        task_id=task_id,
        ts=ts,
        iteration_completed=iteration,
        working_set_snapshot=working_set or [],
        current_phase=phase,
        has_final_answer=final,
    )


def _make_file_op(
    task_id: str,
    ts: str,
    path: str,
    *,
    action: str = "write",
    content_before: str = "",
    content_after: str = "",
) -> _FakeEvent:
    return _FakeEvent(
        event_id=f"fop-{ts}-{path}",
        event_type="file_op",
        task_id=task_id,
        ts=ts,
        path=path,
        action=action,
        rollback={
            "reversible": True,
            "action": action,
            "path": path,
            "expected_current_sha256": _hash(content_after),
            "content": content_before,
        },
    )


# ── list_rewind_points ───────────────────────────────────────


def test_list_rewind_points_filters_by_task() -> None:
    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "2024-01-01T00:00:00Z", 1),
            _make_checkpoint("task-b", "2024-01-01T00:01:00Z", 1),
            _make_checkpoint("task-a", "2024-01-01T00:02:00Z", 2),
        ]
    )
    points = list_rewind_points(journal, "task-a")
    assert [p.iteration for p in points] == [1, 2]
    assert all(p.task_id == "task-a" for p in points)


def test_list_rewind_points_empty_for_unknown_task() -> None:
    journal = _FakeJournal([_make_checkpoint("task-a", "t", 1)])
    assert list_rewind_points(journal, "missing") == []


def test_list_rewind_points_extracts_working_set_paths() -> None:
    journal = _FakeJournal(
        [
            _make_checkpoint(
                "task-a",
                "t",
                1,
                working_set=[{"path": "a.py"}, {"path": "b.py"}],
            ),
        ]
    )
    points = list_rewind_points(journal, "task-a")
    assert points[0].working_set_paths == ("a.py", "b.py")


# ── rewind_to_checkpoint ─────────────────────────────────────


def test_rewind_raises_when_iteration_not_found(tmp_path: Path) -> None:
    journal = _FakeJournal([_make_checkpoint("task-a", "t", 1)])
    with pytest.raises(ValueError, match="iteration 99"):
        rewind_to_checkpoint(journal, "task-a", 99, project_root=str(tmp_path))


def test_rewind_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    # Two checkpoints: iter 1 (before file write), iter 2 (after).
    # Current on-disk state = "v2" (matches expected_current_sha256).
    # Rewinding to iter 1 dry-runs to restore v1 without touching disk.
    target_file = tmp_path / "out.txt"
    target_file.write_text("v2")

    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "t1", 1, working_set=[{"path": "out.txt"}]),
            _make_file_op(
                "task-a",
                "t2",
                "out.txt",
                content_before="v1",
                content_after="v2",
            ),
            _make_checkpoint("task-a", "t3", 2),
        ]
    )

    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=True,
    )
    assert result.dry_run is True
    # dry_run returns a preview — applied counts the entries that WOULD run.
    assert result.file_rollback.applied == 1
    # File on disk unchanged (still v2, the post-write state).
    assert target_file.read_text() == "v2"


def test_rewind_restores_previous_file_state(tmp_path: Path) -> None:
    target_file = tmp_path / "out.txt"
    v1, v2 = "v1", "v2"
    target_file.write_text(v2)  # current state matches the post-write hash

    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "t1", 1, working_set=[{"path": "out.txt"}]),
            _make_file_op(
                "task-a",
                "t2",
                "out.txt",
                content_before=v1,
                content_after=v2,
            ),
            _make_checkpoint("task-a", "t3", 2),
        ]
    )

    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=False,
    )
    assert result.file_rollback.applied == 1
    assert result.file_rollback.failed == 0
    assert target_file.read_text() == v1


def test_rewind_skips_file_with_hash_mismatch(tmp_path: Path) -> None:
    """If the on-disk content was changed externally, rewind refuses
    to overwrite it — the optimistic hash check protects against
    silently clobbering concurrent edits.
    """
    target_file = tmp_path / "out.txt"
    target_file.write_text("independent-edit")  # neither v1 nor v2

    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "t1", 1),
            _make_file_op(
                "task-a",
                "t2",
                "out.txt",
                content_before="v1",
                content_after="v2",
            ),
            _make_checkpoint("task-a", "t3", 2),
        ]
    )

    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=False,
    )
    assert result.file_rollback.applied == 0
    assert result.file_rollback.skipped == 1
    assert any("hash_mismatch" in err for err in result.file_rollback.errors)


def test_rewind_only_applies_events_after_target_checkpoint(tmp_path: Path) -> None:
    """Events at or before the target checkpoint ts must NOT be rolled back."""
    a_path = tmp_path / "a.txt"
    b_path = tmp_path / "b.txt"
    a_path.write_text("a-after")
    b_path.write_text("b-after")

    journal = _FakeJournal(
        [
            # iter 1 checkpoint at t1
            _make_checkpoint("task-a", "t1", 1),
            # file_op AT t1 (should be considered "before or equal" → skipped)
            _make_file_op(
                "task-a",
                "t1",
                "a.txt",
                content_before="a-before",
                content_after="a-after",
            ),
            # file_op AFTER t1 — this should be rolled back
            _make_file_op(
                "task-a",
                "t2",
                "b.txt",
                content_before="b-before",
                content_after="b-after",
            ),
            _make_checkpoint("task-a", "t3", 2),
        ]
    )

    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=False,
    )
    # Only b.txt should be rolled back; a.txt untouched.
    assert result.file_rollback.applied == 1
    assert a_path.read_text() == "a-after"
    assert b_path.read_text() == "b-before"


def test_rewind_collects_non_reversible_warnings(tmp_path: Path) -> None:
    """Shell / deploy / push actions between checkpoints surface as warnings."""
    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "t1", 1),
            # A non-reversible shell step after t1 (sucker_id match).
            _FakeEvent(
                event_id="step-1",
                event_type="step",
                task_id="task-a",
                ts="t2",
                sucker_id="exec_shell",
                action="rm -rf /tmp/scratch",
            ),
            # A potentially destructive action by command text alone.
            _FakeEvent(
                event_id="step-2",
                event_type="step",
                task_id="task-a",
                ts="t3",
                sucker_id="",
                action="git push --force origin main",
            ),
            _make_checkpoint("task-a", "t4", 2),
        ]
    )

    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=True,
    )
    assert any("exec_shell" in w for w in result.non_reversible_warnings)
    assert any("git push" in w for w in result.non_reversible_warnings)


def test_rewind_result_to_dict_round_trip(tmp_path: Path) -> None:
    journal = _FakeJournal(
        [
            _make_checkpoint("task-a", "t1", 1, working_set=[{"path": "x.py"}]),
        ]
    )
    result = rewind_to_checkpoint(
        journal,
        "task-a",
        1,
        project_root=str(tmp_path),
        dry_run=True,
    )
    d = result.to_dict()
    assert d["target"]["iteration"] == 1
    assert d["dry_run"] is True
    assert "file_rollback" in d
    assert "non_reversible_warnings" in d

