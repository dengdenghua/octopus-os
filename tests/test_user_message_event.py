"""Durable ``user/message`` journal events (dsh goal round accounting)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.goals.domain import (
    GOAL_INVALID_TRANSITION,
    GoalDomainError,
    GoalSnapshot,
    GoalSnapshotChange,
)
from runtime.memory.goals.fold import fold_goal
from runtime.memory.journal import InMemoryJournal, JSONLJournal
from runtime.memory.journal._journal_models import UserMessageEvent
from runtime.memory.journal._journal_parse import _EVENT_CLASSES


def _create_change(goal_id: str = "g1") -> dict:
    return GoalSnapshotChange(
        operation="create",
        goal=GoalSnapshot(
            id=goal_id,
            revision=1,
            objective="目标",
            phase="active",
            max_goal_rounds=5,
        ),
        rounds_started=0,
        created_at=100,
        updated_at=100,
    ).to_dict()


def _round_source(round_: int, *, goal_id: str = "g1", revision: int = 1) -> dict:
    return {"kind": "goal", "goalId": goal_id, "revision": revision, "round": round_}


def test_event_class_is_registered_for_parse() -> None:
    assert _EVENT_CLASSES["user/message"] is UserMessageEvent


def test_write_user_message_roundtrips_through_jsonl(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    journal.write_goal_change(_create_change())
    journal.write_user_message("继续", goal_source=_round_source(1))

    events = journal.read_all()
    assert len(events) == 2
    msg = events[1]
    assert isinstance(msg, UserMessageEvent)
    assert msg.text == "继续"
    assert msg.goal_source == _round_source(1)


def test_fold_counts_attributed_rounds_through_journal() -> None:
    journal = InMemoryJournal()
    journal.write_goal_change(_create_change())
    journal.write_user_message("第 1 轮", goal_source=_round_source(1))
    journal.write_user_message("第 2 轮", goal_source=_round_source(2))
    journal.write_user_message("第 3 轮", goal_source=_round_source(3))

    folded = fold_goal(journal.read_all())
    assert folded.rounds_started == 3
    assert folded.goal is not None
    assert folded.goal.phase == "active"


def test_fold_rejects_out_of_order_round() -> None:
    journal = InMemoryJournal()
    journal.write_goal_change(_create_change())
    journal.write_user_message("第 1 轮", goal_source=_round_source(1))
    journal.write_user_message("第 3 轮", goal_source=_round_source(3))

    with pytest.raises(GoalDomainError) as exc:
        fold_goal(journal.read_all())
    assert exc.value.code == GOAL_INVALID_TRANSITION


def test_fold_ignores_unattributed_message() -> None:
    journal = InMemoryJournal()
    journal.write_goal_change(_create_change())
    journal.write_user_message("普通消息")

    folded = fold_goal(journal.read_all())
    assert folded.rounds_started == 0


def test_fold_ignores_non_goal_source() -> None:
    journal = InMemoryJournal()
    journal.write_goal_change(_create_change())
    journal.write_user_message("消息", goal_source={"kind": "user"})

    folded = fold_goal(journal.read_all())
    assert folded.rounds_started == 0


def test_fold_stops_at_round_budget() -> None:
    journal = InMemoryJournal()
    change = GoalSnapshotChange(
        operation="create",
        goal=GoalSnapshot(
            id="g1",
            revision=1,
            objective="目标",
            phase="active",
            max_goal_rounds=2,
        ),
        rounds_started=0,
        created_at=100,
        updated_at=100,
    ).to_dict()
    journal.write_goal_change(change)
    journal.write_user_message("第 1 轮", goal_source=_round_source(1))
    journal.write_user_message("第 2 轮", goal_source=_round_source(2))
    journal.write_user_message("第 3 轮", goal_source=_round_source(3))

    with pytest.raises(GoalDomainError) as exc:
        fold_goal(journal.read_all())
    assert exc.value.code == GOAL_INVALID_TRANSITION

