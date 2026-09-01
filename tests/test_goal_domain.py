"""dsh goal-domain port tests — CAS lifecycle, strict transitions, replay fold."""

from __future__ import annotations

from typing import Any

import pytest

from runtime.memory.goals.domain import (
    GOAL_ALREADY_EXISTS,
    GOAL_INVALID_BLOCK_REASON,
    GOAL_INVALID_EDIT,
    GOAL_INVALID_OBJECTIVE,
    GOAL_INVALID_TRANSITION,
    GOAL_NOT_FOUND,
    GOAL_STALE_REVISION,
    GoalBlockReason,
    GoalClearChange,
    GoalDomainError,
    GoalRef,
    GoalSnapshot,
    GoalSnapshotChange,
)
from runtime.memory.goals.fold import (
    apply_goal_change,
    apply_goal_event,
    decode_goal_change,
    empty_goal_fold_state,
    fold_goal,
)
from runtime.memory.goals.service import GoalService
from runtime.memory.journal import InMemoryJournal


def _change(
    op: str,
    *,
    goal_id: str = "g1",
    revision: int = 1,
    objective: str = "审计项目",
    phase: str = "active",
    max_rounds: int = 5,
    rounds: int = 0,
    created: int = 100,
    updated: int = 100,
    blocked: GoalBlockReason | None = None,
) -> GoalSnapshotChange:
    return GoalSnapshotChange(
        operation=op,  # type: ignore[arg-type]
        goal=GoalSnapshot(
            id=goal_id,
            revision=revision,
            objective=objective,
            phase=phase,  # type: ignore[arg-type]
            max_goal_rounds=max_rounds,
            blocked_reason=blocked,
        ),
        rounds_started=rounds,
        created_at=created,
        updated_at=updated,
    )


def _state_with(change: GoalSnapshotChange) -> Any:
    state = empty_goal_fold_state()
    apply_goal_change(state, change)
    return state


# ─── create ─────────────────────────────────────────────────────────────


def test_create_requires_fresh_active_revision_one() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    assert state.goal is not None
    assert state.goal.revision == 1
    assert state.goal.phase == "active"
    assert state.rounds_started == 0


def test_create_rejects_second_active_goal() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(state, _change("create", goal_id="g2"))
    assert exc.value.code == GOAL_ALREADY_EXISTS


def test_create_after_complete_is_allowed() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        _change("complete", goal_id="g1", revision=2, phase="complete"),
    )
    apply_goal_change(state, _change("create", goal_id="g2"))
    assert state.goal.id == "g2"
    assert state.goal.revision == 1


def test_create_rejects_reused_goal_id() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        _change("complete", goal_id="g1", revision=2, phase="complete"),
    )
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(state, _change("create", goal_id="g1"))
    assert exc.value.code == GOAL_ALREADY_EXISTS


def test_create_rejects_non_active_phase_or_nonzero_rounds() -> None:
    with pytest.raises(GoalDomainError):
        _state_with(_change("create", goal_id="g1", phase="paused"))
    with pytest.raises(GoalDomainError):
        _state_with(_change("create", goal_id="g1", rounds=1))


# ─── CAS revision guard ──────────────────────────────────────────────────


def test_stale_revision_fails_loudly() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError) as exc:
        # same revision again — no advance
        apply_goal_change(
            state,
            _change("pause", goal_id="g1", revision=1, phase="paused"),
        )
    assert exc.value.code == GOAL_STALE_REVISION


def test_revision_skipping_is_stale() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("pause", goal_id="g1", revision=3, phase="paused"),
        )
    assert exc.value.code == GOAL_STALE_REVISION


def test_wrong_goal_id_is_stale() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("pause", goal_id="other", revision=2, phase="paused"),
        )
    assert exc.value.code == GOAL_STALE_REVISION


def test_operation_without_current_goal_is_not_found() -> None:
    state = empty_goal_fold_state()
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(state, _change("pause", goal_id="g1", revision=2, phase="paused"))
    assert exc.value.code == GOAL_NOT_FOUND


# ─── transition table ────────────────────────────────────────────────────


def test_pause_only_from_active() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        _change("pause", goal_id="g1", revision=2, phase="paused"),
    )
    assert state.goal.phase == "paused"
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("pause", goal_id="g1", revision=3, phase="paused"),
        )
    assert exc.value.code == GOAL_INVALID_TRANSITION


def test_resume_from_paused_and_blocked() -> None:
    for from_phase in ("paused", "blocked"):
        state = empty_goal_fold_state()
        blocked = GoalBlockReason(code="waiting-user", message="need input")
        apply_goal_change(
            state,
            _change(
                "create",
                goal_id="g1",
                phase="active",
            ),
        )
        if from_phase == "paused":
            apply_goal_change(
                state,
                _change("pause", goal_id="g1", revision=2, phase="paused"),
            )
        else:
            apply_goal_change(
                state,
                _change(
                    "block",
                    goal_id="g1",
                    revision=2,
                    phase="blocked",
                    blocked=blocked,
                ),
            )
        apply_goal_change(
            state,
            _change("resume", goal_id="g1", revision=3, phase="active"),
        )
        assert state.goal.phase == "active"


def test_resume_rejects_exhausted_round_budget() -> None:
    state = _state_with(_change("create", goal_id="g1", max_rounds=1))
    state.rounds_started = 1
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("resume", goal_id="g1", revision=2, phase="active", rounds=1),
        )
    assert exc.value.code == GOAL_INVALID_TRANSITION


def test_resume_rejects_completed_goal() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        _change("complete", goal_id="g1", revision=2, phase="complete"),
    )
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("resume", goal_id="g1", revision=3, phase="active"),
        )
    assert exc.value.code == GOAL_INVALID_TRANSITION


def test_complete_rejects_already_complete() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        _change("complete", goal_id="g1", revision=2, phase="complete"),
    )
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("complete", goal_id="g1", revision=3, phase="complete"),
        )
    assert exc.value.code == GOAL_INVALID_TRANSITION


def test_block_only_from_active_with_reason() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError):
        apply_goal_change(
            state,
            _change("block", goal_id="g1", revision=2, phase="blocked"),
        )
    apply_goal_change(
        state,
        _change(
            "block",
            goal_id="g1",
            revision=2,
            phase="blocked",
            blocked=GoalBlockReason(code="waiting-user", message="need input"),
        ),
    )
    assert state.goal.phase == "blocked"


def test_edit_cannot_change_phase_or_blocked_reason() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            _change("edit", goal_id="g1", revision=2, phase="paused"),
        )
    assert exc.value.code == GOAL_INVALID_EDIT
    # edit may replace the objective
    apply_goal_change(
        state,
        _change("edit", goal_id="g1", revision=2, objective="新目标"),
    )
    assert state.goal.objective == "新目标"


def test_pause_resume_cannot_change_definition() -> None:
    state = _state_with(_change("create", goal_id="g1"))
    with pytest.raises(GoalDomainError):
        apply_goal_change(
            state,
            _change("pause", goal_id="g1", revision=2, phase="paused", objective="改掉"),
        )
    with pytest.raises(GoalDomainError):
        apply_goal_change(
            state,
            _change("pause", goal_id="g1", revision=2, phase="paused", max_rounds=9),
        )


# ─── clear tombstone ─────────────────────────────────────────────────────


def test_clear_requires_current_goal_and_advances_revision() -> None:
    state = empty_goal_fold_state()
    with pytest.raises(GoalDomainError) as exc:
        apply_goal_change(
            state,
            GoalClearChange(cleared=GoalRef(id="g1", revision=2), cleared_at=200),
        )
    assert exc.value.code == GOAL_NOT_FOUND

    state = _state_with(_change("create", goal_id="g1"))
    apply_goal_change(
        state,
        GoalClearChange(cleared=GoalRef(id="g1", revision=2), cleared_at=200),
    )
    assert state.goal is None
    assert state.last_ref == GoalRef(id="g1", revision=2)


def test_clear_timestamp_cannot_precede_update() -> None:
    state = _state_with(_change("create", goal_id="g1", updated=150))
    with pytest.raises(GoalDomainError):
        apply_goal_change(
            state,
            GoalClearChange(cleared=GoalRef(id="g1", revision=2), cleared_at=100),
        )


# ─── strict decoding ─────────────────────────────────────────────────────


def test_decode_rejects_malformed_changes() -> None:
    good = _change("create", goal_id="g1").to_dict()
    assert decode_goal_change(good) is not None

    bad_versions = [
        dict(good, version=2),
        dict(good, operation="clear", cleared={"id": "g1", "revision": 1}, clearedAt=1),
    ]
    with pytest.raises(GoalDomainError):
        decode_goal_change(bad_versions[0])
    with pytest.raises(GoalDomainError):
        decode_goal_change({**good, "extra": 1})
    with pytest.raises(GoalDomainError):
        decode_goal_change({**good, "updatedAt": 1, "createdAt": 2})
    with pytest.raises(GoalDomainError):
        decode_goal_change({**good, "goal": {**good["goal"], "objective": " padded "}})
    with pytest.raises(GoalDomainError):
        decode_goal_change(
            {
                **good,
                "goal": {
                    **good["goal"],
                    "phase": "blocked",
                    "blockedReason": {"code": "Bad Code", "message": "x"},
                },
            }
        )
    assert decode_goal_change({"kind": "other"}) is None


def test_decode_rejects_phase_without_blocked_reason() -> None:
    good = _change("create", goal_id="g1").to_dict()
    with pytest.raises(GoalDomainError) as exc:
        decode_goal_change({**good, "goal": {**good["goal"], "phase": "blocked"}})
    assert exc.value.code == GOAL_INVALID_BLOCK_REASON


def test_blocked_reason_validation() -> None:
    with pytest.raises(GoalDomainError) as exc:
        GoalBlockReason(code="Bad Code", message="x")
    assert exc.value.code == GOAL_INVALID_BLOCK_REASON
    with pytest.raises(GoalDomainError):
        GoalBlockReason(code="waiting-user", message=" padded ")
    with pytest.raises(GoalDomainError):
        GoalBlockReason(code="waiting-user", message="")
    GoalBlockReason(code="waiting-user", message="need input")  # ok


# ─── round accounting ────────────────────────────────────────────────────


def _round_event(goal_id: str, revision: int, round_: int) -> dict[str, Any]:
    return {
        "type": "user/message",
        "data": {
            "source": {"kind": "goal", "goalId": goal_id, "revision": revision, "round": round_}
        },
    }


def test_round_requires_next_round_of_active_goal() -> None:
    state = _state_with(_change("create", goal_id="g1", max_rounds=3))
    apply_goal_event(state, _round_event("g1", 1, 1))
    assert state.rounds_started == 1
    apply_goal_event(state, _round_event("g1", 1, 2))
    assert state.rounds_started == 2

    with pytest.raises(GoalDomainError) as exc:
        apply_goal_event(state, _round_event("g1", 1, 2))  # repeat is stale
    assert exc.value.code == GOAL_INVALID_TRANSITION
    with pytest.raises(GoalDomainError):
        apply_goal_event(state, _round_event("g1", 1, 4))  # over budget


def test_round_rejected_when_paused_or_wrong_revision() -> None:
    state = _state_with(_change("create", goal_id="g1", max_rounds=3))
    apply_goal_change(
        state,
        _change("pause", goal_id="g1", revision=2, phase="paused", max_rounds=3),
    )
    with pytest.raises(GoalDomainError):
        apply_goal_event(state, _round_event("g1", 2, 1))
    state2 = _state_with(_change("create", goal_id="g1", max_rounds=3))
    with pytest.raises(GoalDomainError):
        apply_goal_event(state2, _round_event("g1", 99, 1))


# ─── full replay fold ────────────────────────────────────────────────────


def test_fold_reconstructs_projection_from_event_sequence() -> None:
    events: list[dict[str, Any]] = [
        {"type": "goal/change", "data": _change("create", goal_id="g1").to_dict()},
        _round_event("g1", 1, 1),
        _round_event("g1", 1, 2),
        {
            "type": "goal/change",
            "data": _change("pause", goal_id="g1", revision=2, phase="paused", rounds=2).to_dict(),
        },
        {
            "type": "goal/change",
            "data": _change("resume", goal_id="g1", revision=3, phase="active", rounds=2).to_dict(),
        },
        {
            "type": "goal/change",
            "data": _change(
                "complete", goal_id="g1", revision=4, phase="complete", rounds=2
            ).to_dict(),
        },
        {
            "type": "goal/change",
            "data": GoalClearChange(cleared=GoalRef(id="g1", revision=5), cleared_at=999).to_dict(),
        },
    ]
    folded = fold_goal(events)
    assert folded.goal is None
    assert folded.rounds_started == 0
    assert folded.last_ref == GoalRef(id="g1", revision=5)


# ─── service (journal adapter) ───────────────────────────────────────────


def test_service_lifecycle_end_to_end() -> None:
    svc = GoalService(InMemoryJournal())
    created = svc.create("审计 echo 项目", max_goal_rounds=3)
    assert created.goal is not None and created.goal.phase == "active"
    goal_id = created.goal.id

    assert svc.pause().goal.phase == "paused"
    assert svc.resume().goal.phase == "active"
    assert svc.complete().goal.phase == "complete"

    second = svc.create("新目标")
    assert second.goal is not None
    assert second.goal.id != goal_id
    assert second.goal.revision == 1

    cleared = svc.clear()
    assert cleared.goal is None
    assert svc.get() is None


def test_service_rejects_invalid_inputs() -> None:
    svc = GoalService(InMemoryJournal())
    with pytest.raises(GoalDomainError) as exc:
        svc.create("  padded ")
    assert exc.value.code == GOAL_INVALID_OBJECTIVE
    with pytest.raises(GoalDomainError):
        svc.create("ok", max_goal_rounds=0)
    with pytest.raises(GoalDomainError):
        svc.create("ok", max_goal_rounds=-1)


def test_service_rejects_second_active_goal() -> None:
    svc = GoalService(InMemoryJournal())
    svc.create("第一个")
    with pytest.raises(GoalDomainError) as exc:
        svc.create("第二个")
    assert exc.value.code == GOAL_ALREADY_EXISTS


def test_service_operations_without_goal_are_not_found() -> None:
    svc = GoalService(InMemoryJournal())
    for op in (
        lambda: svc.pause(),
        lambda: svc.resume(),
        lambda: svc.complete(),
        lambda: svc.clear(),
    ):
        with pytest.raises(GoalDomainError) as exc:
            op()
        assert exc.value.code == GOAL_NOT_FOUND
    with pytest.raises(GoalDomainError) as exc:
        svc.edit("改目标")
    assert exc.value.code == GOAL_NOT_FOUND


def test_service_edit_and_block() -> None:
    svc = GoalService(InMemoryJournal())
    svc.create("原目标")
    edited = svc.edit("新目标")
    assert edited.goal is not None and edited.goal.objective == "新目标"
    blocked = svc.block(code="waiting-user", message="等待用户确认")
    assert blocked.goal is not None and blocked.goal.phase == "blocked"
    assert blocked.goal.blocked_reason == GoalBlockReason(
        code="waiting-user", message="等待用户确认"
    )
    resumed = svc.resume()
    assert resumed.goal is not None and resumed.goal.phase == "active"


def test_service_survives_jsonl_replay(tmp_path: Any) -> None:
    from pathlib import Path

    from runtime.memory.journal import JSONLJournal

    path = Path(tmp_path) / "journal.jsonl"
    svc = GoalService(JSONLJournal(path))
    svc.create("持久化目标", max_goal_rounds=4)
    svc.pause()

    replayed = GoalService(JSONLJournal(path)).current()
    assert replayed.goal is not None
    assert replayed.goal.phase == "paused"
    assert replayed.goal.revision == 2
    assert replayed.goal.objective == "持久化目标"


# ─── goal/changed live broadcast ──────────────────────────────────────────


def test_subscribe_receives_committed_changes() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal())
    seen: list[GoalChanged] = []
    unsubscribe = svc.subscribe(seen.append)

    created = svc.create("目标A", max_goal_rounds=3)
    svc.pause()
    cleared = svc.clear()

    assert [c.operation for c in seen] == ["create", "pause", "clear"]
    assert seen[0].ref == created.goal.ref
    assert seen[0].goal is not None and seen[0].goal.phase == "active"
    assert seen[2].goal is None  # clear tombstone carries no snapshot
    assert seen[2].ref == cleared.last_ref
    unsubscribe()
    svc.create("目标B")
    assert len(seen) == 3


def test_subscribe_isolates_failing_listener() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal())
    good: list[GoalChanged] = []

    def bad(_change: GoalChanged) -> None:
        raise RuntimeError("listener boom")

    svc.subscribe(bad)
    svc.subscribe(good.append)
    svc.create("仍要落地")
    assert len(good) == 1
    assert good[0].operation == "create"


# ─── scope-filtered dispatch + journal event bridge ───────────────────────


def test_subscribe_filters_by_agent_scope() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal(), agent_id="agent-a", conversation_id="conv-1")
    all_: list[GoalChanged] = []
    agent_a: list[GoalChanged] = []
    agent_b: list[GoalChanged] = []
    conv_1: list[GoalChanged] = []

    svc.subscribe(all_.append)  # wildcard
    svc.subscribe(agent_a.append, agent_id="agent-a")
    svc.subscribe(agent_b.append, agent_id="agent-b")
    svc.subscribe(conv_1.append, conversation_id="conv-1")

    svc.create("目标A")

    assert len(all_) == 1
    assert all_[0].agent_id == "agent-a"
    assert all_[0].conversation_id == "conv-1"
    assert len(agent_a) == 1
    assert len(agent_b) == 0  # different agent scope filtered out
    assert len(conv_1) == 1


def test_unscoped_service_does_not_feed_scoped_listener() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal())  # no agent/conversation scope
    scoped: list[GoalChanged] = []
    svc.subscribe(scoped.append, agent_id="agent-a")
    svc.create("无主目标")
    assert scoped == []


def test_journal_bridge_cross_writer_dispatch() -> None:
    from runtime.memory.goals import GoalChanged
    from runtime.sensing.gateway.streaming_journal import StreamingJournal

    journal = StreamingJournal(InMemoryJournal())
    svc_a = GoalService(journal, agent_id="agent-a")
    svc_b = GoalService(journal, agent_id="agent-b")
    heard_a: list[GoalChanged] = []
    heard_b: list[GoalChanged] = []

    svc_a.subscribe(heard_a.append)
    svc_b.subscribe(heard_b.append)

    # writer A's mutation broadcasts to BOTH services through the journal.
    created = svc_a.create("共享目标", max_goal_rounds=3)

    assert len(heard_a) == 1  # own write, no duplicate
    assert heard_a[0].agent_id == "agent-a"
    assert heard_a[0].ref == created.goal.ref
    # B hears A's mutation via the journal bridge, carrying A's scope.
    assert len(heard_b) == 1
    assert heard_b[0].agent_id == "agent-a"


def test_journal_bridge_scope_filter_across_writers() -> None:
    from runtime.memory.goals import GoalChanged
    from runtime.sensing.gateway.streaming_journal import StreamingJournal

    journal = StreamingJournal(InMemoryJournal())
    svc_a = GoalService(journal, agent_id="agent-a")
    svc_b = GoalService(journal, agent_id="agent-b")
    svc_a.subscribe(lambda c: None, agent_id="agent-b")  # only cares about agent-b

    svc_a.create("A 的目标")
    # A's own create is agent-a, so A's agent-b-filtered listener is skipped.
    # B writing does not disturb A either (no agent-b write here).
    heard: list[GoalChanged] = []
    svc_b.subscribe(heard.append, agent_id="agent-b")
    svc_b.create("B 的目标")
    assert len(heard) == 1
    assert heard[0].agent_id == "agent-b"


def test_journal_bridge_does_not_double_notify_own_write() -> None:
    from runtime.memory.goals import GoalChanged
    from runtime.sensing.gateway.streaming_journal import StreamingJournal

    journal = StreamingJournal(InMemoryJournal())
    svc = GoalService(journal, agent_id="agent-a")
    seen: list[GoalChanged] = []
    svc.subscribe(seen.append)
    svc.create("目标")
    assert [c.operation for c in seen] == ["create"]


def test_subscribe_replay_delivers_committed_events_in_order() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal(), agent_id="agent-a")
    svc.create("已有目标")
    svc.pause()

    heard: list[GoalChanged] = []
    svc.subscribe(heard.append, replay=True)  # late / after-restart consumer
    assert [c.operation for c in heard] == ["create", "pause"]
    assert all(c.agent_id == "agent-a" for c in heard)

    # Without replay, a fresh subscriber sees nothing already committed.
    fresh: list[GoalChanged] = []
    svc.subscribe(fresh.append)
    assert fresh == []


def test_subscribe_replay_respects_scope_filter() -> None:
    from runtime.memory.goals import GoalChanged

    svc = GoalService(InMemoryJournal(), agent_id="agent-a")
    svc.create("目标A")

    mine: list[GoalChanged] = []
    other: list[GoalChanged] = []
    svc.subscribe(mine.append, agent_id="agent-a", replay=True)
    svc.subscribe(other.append, agent_id="agent-b", replay=True)
    assert len(mine) == 1
    assert other == []


def test_journal_bridge_skips_malformed_goal_event() -> None:
    from runtime.memory.goals import GoalChanged
    from runtime.memory.journal._journal_models import GoalChangeEvent
    from runtime.sensing.gateway.streaming_journal import StreamingJournal

    journal = StreamingJournal(InMemoryJournal())
    svc = GoalService(journal, agent_id="agent-a")
    heard: list[GoalChanged] = []
    svc.subscribe(heard.append)

    # A malformed goal_change event must not break the bridge for later events.
    journal.write(GoalChangeEvent(change={"kind": "goal/change", "operation": "bogus"}))
    svc.create("好目标")
    assert [c.operation for c in heard] == ["create"]

