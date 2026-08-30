"""Pure replay fold and strict decoder for durable goal changes (dsh port).

Every invariant from dsh's ``fold.ts`` is mirrored:

- a mutation must advance the current goal by exactly one revision
  (compare-and-swap guard) — anything else is ``GOAL_STALE_REVISION``;
- the strict phase transition table (create / edit / pause / resume /
  complete / block / clear) is enforced during replay, and malformed
  changes fail the fold loudly instead of being skipped;
- admitted continuation rounds must be the exact next round of the
  active goal at its current revision, capped by ``maxGoalRounds``.
"""

from __future__ import annotations

import json
from typing import Any

from .domain import (
    GOAL_ALREADY_EXISTS,
    GOAL_CHANGE_KIND,
    GOAL_CHANGE_VERSION,
    GOAL_INVALID_BLOCK_REASON,
    GOAL_INVALID_CHANGE,
    GOAL_INVALID_EDIT,
    GOAL_INVALID_TRANSITION,
    GOAL_NOT_FOUND,
    GOAL_STALE_REVISION,
    FoldedGoal,
    GoalBlockReason,
    GoalChange,
    GoalClearChange,
    GoalDomainError,
    GoalFoldState,
    GoalOperation,
    GoalRef,
    GoalSnapshot,
    GoalSnapshotChange,
)

_SNAPSHOT_OPERATIONS: frozenset[str] = frozenset(
    {"create", "edit", "pause", "resume", "complete", "block"}
)
_PHASES: frozenset[str] = frozenset({"active", "paused", "blocked", "complete"})

_SNAPSHOT_CHANGE_KEYS: frozenset[str] = frozenset(
    {"createdAt", "goal", "kind", "operation", "roundsStarted", "updatedAt", "version"}
)
_CLEAR_CHANGE_KEYS: frozenset[str] = frozenset(
    {"cleared", "clearedAt", "kind", "operation", "version"}
)
_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {"blockedReason", "id", "maxGoalRounds", "objective", "phase", "revision"}
)
_SNAPSHOT_KEYS_BLOCKED: frozenset[str] = frozenset(
    {"blockedReason", "id", "maxGoalRounds", "objective", "phase", "revision"}
)
_REF_KEYS: frozenset[str] = frozenset({"id", "revision"})


def empty_goal_fold_state() -> GoalFoldState:
    """Build an empty replay accumulator."""
    return GoalFoldState()


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE, f"goal change {field_name} must be a positive safe integer"
        )
    return value


def _non_negative_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE, f"goal change {field_name} must be a non-negative safe integer"
        )
    return value


def _decode_block_reason(value: Any) -> GoalBlockReason:
    if not _is_record(value) or set(value) != {"code", "message"}:
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON,
            "goal change goal.blockedReason must have exactly code and message fields",
        )
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, str):
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON, "goal change goal.blockedReason.code must be a string"
        )
    if not isinstance(message, str):
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON, "goal change goal.blockedReason.message must be a string"
        )
    return GoalBlockReason(code=code, message=message)


def _decode_snapshot(value: Any) -> GoalSnapshot:
    if not _is_record(value):
        raise GoalDomainError(GOAL_INVALID_CHANGE, "goal change goal must be a record")
    phase = value.get("phase")
    if not isinstance(phase, str) or phase not in _PHASES:
        raise GoalDomainError(GOAL_INVALID_CHANGE, "goal change goal.phase is invalid")
    has_reason = "blockedReason" in value
    if phase == "blocked" and not has_reason:
        raise GoalDomainError(GOAL_INVALID_BLOCK_REASON, "blocked goal must carry a blocked reason")
    if phase != "blocked" and has_reason:
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON, "non-blocked goal must not carry a blocked reason"
        )
    expected = _SNAPSHOT_KEYS_BLOCKED if phase == "blocked" else _SNAPSHOT_KEYS - {"blockedReason"}
    if set(value) != expected:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE,
            f"goal change goal for phase {phase} must have exactly {sorted(expected)} fields",
        )
    return GoalSnapshot(
        id=value["id"],
        revision=_positive_integer(value.get("revision"), "goal.revision"),
        objective=value["objective"],
        phase=phase,  # type: ignore[arg-type]
        max_goal_rounds=_positive_integer(value.get("maxGoalRounds"), "goal.maxGoalRounds"),
        blocked_reason=(
            _decode_block_reason(value.get("blockedReason")) if phase == "blocked" else None
        ),
    )


def _decode_ref(value: Any) -> GoalRef:
    if not _is_record(value) or set(value) != _REF_KEYS:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE, "goal clear tombstone must have exactly id and revision fields"
        )
    return GoalRef(
        id=value["id"], revision=_positive_integer(value.get("revision"), "cleared.revision")
    )


def decode_goal_change(value: Any) -> GoalChange | None:
    """Decode a value that declares itself as a goal change.

    Unrelated values return ``None``; malformed goal changes fail loudly.
    """
    if not _is_record(value) or value.get("kind") != GOAL_CHANGE_KIND:
        return None
    if value.get("version") != GOAL_CHANGE_VERSION:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE, f"unsupported goal change version {value.get('version')!r}"
        )
    if value.get("operation") == "clear":
        if set(value) != _CLEAR_CHANGE_KEYS:
            raise GoalDomainError(
                GOAL_INVALID_CHANGE,
                f"goal clear change must have exactly {sorted(_CLEAR_CHANGE_KEYS)} fields",
            )
        cleared_at = _non_negative_integer(value.get("clearedAt"), "clearedAt")
        return GoalClearChange(
            cleared=_decode_ref(value.get("cleared")),
            cleared_at=cleared_at,
            version=GOAL_CHANGE_VERSION,
        )
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in _SNAPSHOT_OPERATIONS:
        raise GoalDomainError(GOAL_INVALID_CHANGE, "goal change operation is invalid")
    if set(value) != _SNAPSHOT_CHANGE_KEYS:
        raise GoalDomainError(
            GOAL_INVALID_CHANGE,
            f"goal snapshot change must have exactly {sorted(_SNAPSHOT_CHANGE_KEYS)} fields",
        )
    created_at = _non_negative_integer(value.get("createdAt"), "createdAt")
    updated_at = _non_negative_integer(value.get("updatedAt"), "updatedAt")
    if updated_at < created_at:
        raise GoalDomainError(GOAL_INVALID_CHANGE, "goal change updatedAt cannot precede createdAt")
    return GoalSnapshotChange(
        operation=operation,  # type: ignore[arg-type]
        goal=_decode_snapshot(value.get("goal")),
        rounds_started=_non_negative_integer(value.get("roundsStarted"), "roundsStarted"),
        created_at=created_at,
        updated_at=updated_at,
        version=GOAL_CHANGE_VERSION,
    )


def goal_change_ref(change: GoalChange) -> GoalRef:
    """Return the revision identity carried by a snapshot or tombstone."""
    if isinstance(change, GoalClearChange):
        return change.cleared
    return change.goal.ref


def _require_same_definition(
    current: GoalSnapshot,
    next_: GoalSnapshot,
    operation: GoalOperation,
) -> None:
    if next_.objective != current.objective or next_.max_goal_rounds != current.max_goal_rounds:
        raise GoalDomainError(
            GOAL_INVALID_TRANSITION,
            f"goal {operation} cannot change objective or maxGoalRounds",
        )


def _require_next_revision(current: GoalSnapshot, next_: GoalRef, operation: GoalOperation) -> None:
    if next_.id != current.id or next_.revision != current.revision + 1:
        raise GoalDomainError(
            GOAL_STALE_REVISION,
            f"goal {operation} must advance the current goal by one revision",
        )


def _validate_snapshot_transition(
    state: GoalFoldState,
    change: GoalSnapshotChange,
    current: GoalSnapshot,
) -> None:
    next_ = change.goal
    _require_next_revision(current, next_, change.operation)
    if state.updated_at is None:
        raise GoalDomainError(GOAL_INVALID_CHANGE, "current goal fold lacks updatedAt")
    if (
        change.created_at != state.created_at
        or change.updated_at < state.updated_at
        or change.rounds_started != state.rounds_started
    ):
        raise GoalDomainError(
            GOAL_INVALID_CHANGE,
            f"goal {change.operation} does not preserve the current counters and timestamps",
        )
    op = change.operation
    if op == "edit":
        if next_.phase != current.phase or next_.blocked_reason != current.blocked_reason:
            raise GoalDomainError(
                GOAL_INVALID_EDIT, "goal edit cannot change phase or blocked reason"
            )
    elif op == "pause":
        _require_same_definition(current, next_, op)
        if current.phase != "active" or next_.phase != "paused":
            raise GoalDomainError(
                GOAL_INVALID_TRANSITION, "goal pause has an invalid phase transition"
            )
    elif op == "resume":
        _require_same_definition(current, next_, op)
        resumable = {"active", "paused", "blocked"}
        if (
            current.phase not in resumable
            or next_.phase != "active"
            or state.rounds_started >= next_.max_goal_rounds
        ):
            raise GoalDomainError(
                GOAL_INVALID_TRANSITION,
                "goal resume has an invalid phase transition or exhausted round budget",
            )
    elif op == "complete":
        _require_same_definition(current, next_, op)
        if current.phase == "complete" or next_.phase != "complete":
            raise GoalDomainError(
                GOAL_INVALID_TRANSITION, "goal complete has an invalid phase transition"
            )
    elif op == "block":
        _require_same_definition(current, next_, op)
        if current.phase != "active" or next_.phase != "blocked":
            raise GoalDomainError(
                GOAL_INVALID_TRANSITION, "goal block has an invalid phase transition"
            )
    elif op == "create":
        raise GoalDomainError(
            GOAL_INVALID_TRANSITION, "goal create cannot be validated as a current-goal transition"
        )


def apply_goal_change(state: GoalFoldState, change: GoalChange) -> None:
    """Validate and apply one decoded change to a mutable accumulator."""
    ref = goal_change_ref(change)
    if isinstance(change, GoalClearChange):
        current = state.goal
        if current is None:
            raise GoalDomainError(GOAL_NOT_FOUND, "goal clear requires a current goal")
        _require_next_revision(current, change.cleared, "clear")
        if state.updated_at is None:
            raise GoalDomainError(GOAL_INVALID_CHANGE, "current goal fold lacks updatedAt")
        if change.cleared_at < state.updated_at:
            raise GoalDomainError(
                GOAL_INVALID_CHANGE, "goal clear timestamp cannot precede the current goal update"
            )
        state.goal = None
        state.rounds_started = 0
        state.created_at = None
        state.updated_at = None
        state.last_ref = ref
        return

    if change.operation == "create":
        if (
            change.goal.revision != 1
            or change.goal.phase != "active"
            or change.rounds_started != 0
            or (state.goal is not None and state.goal.phase != "complete")
            or change.goal.id in state.seen_goal_ids
        ):
            raise GoalDomainError(
                GOAL_ALREADY_EXISTS,
                "goal create requires a fresh active revision-one goal with zero rounds",
            )
        state.seen_goal_ids.add(change.goal.id)
    else:
        current = state.goal
        if current is None:
            raise GoalDomainError(
                GOAL_NOT_FOUND, f"goal {change.operation} requires a current goal"
            )
        _validate_snapshot_transition(state, change, current)

    state.goal = change.goal
    state.rounds_started = change.rounds_started
    state.created_at = change.created_at
    state.updated_at = change.updated_at
    state.last_ref = ref


def _goal_source_round(source: Any) -> tuple[str, int, int] | None:
    """Validate a goal-attributed message source (dsh ``goalSource``)."""
    if not _is_record(source) or source.get("kind") != "goal":
        return None
    goal_id = source.get("goalId")
    revision = source.get("revision")
    round_ = source.get("round")
    if (
        not isinstance(goal_id, str)
        or not goal_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not isinstance(round_, int)
        or isinstance(round_, bool)
        or round_ < 1
    ):
        raise GoalDomainError(GOAL_INVALID_CHANGE, "goal message source is invalid")
    return goal_id, revision, round_


def apply_goal_event(state: GoalFoldState, event: Any) -> None:
    """Apply one session event to the strict durable goal fold.

    ``event`` is a dict-like with ``type`` and ``data`` (or a dataclass
    exposing the same shape); ``goal/change`` events are decoded strictly,
    and user messages carrying a goal source must be the next admitted
    continuation round of the active goal.
    """
    if isinstance(event, dict):
        event_type = event.get("type") or event.get("event_type")
        data = event.get("data")
        if data is None:
            data = event.get("change")
    else:
        event_type = getattr(event, "type", None) or getattr(event, "event_type", None)
        data = getattr(event, "data", None)
        if data is None:
            data = getattr(event, "change", None)
    if event_type in ("goal/change", "goal_change"):
        change = decode_goal_change(data)
        if change is None:
            raise GoalDomainError(
                GOAL_INVALID_CHANGE,
                "goal change event has an invalid kind",
            )
        apply_goal_change(state, change)
        return
    if event_type == "user/message":
        source = None
        if data is not None:
            source = data.get("source") if isinstance(data, dict) else getattr(data, "source", None)
        if source is None:
            # Typed journal event (``UserMessageEvent.goal_source``).
            source = getattr(event, "goal_source", None)
        parsed = _goal_source_round(source)
        if parsed is None:
            return
        goal_id, revision, round_ = parsed
        current = state.goal
        if (
            current is None
            or current.phase != "active"
            or goal_id != current.id
            or revision != current.revision
            or round_ != state.rounds_started + 1
            or round_ > current.max_goal_rounds
        ):
            raise GoalDomainError(
                GOAL_INVALID_TRANSITION,
                "goal round is not the next admitted round of the active goal",
            )
        state.rounds_started = round_


def fold_goal(events: list[Any]) -> FoldedGoal:
    """Fold current goal state from a contiguous session event log."""
    state = empty_goal_fold_state()
    for event in events:
        apply_goal_event(state, event)
    return FoldedGoal(
        goal=state.goal,
        rounds_started=state.rounds_started,
        created_at=state.created_at,
        updated_at=state.updated_at,
        last_ref=state.last_ref,
    )


def goal_change_to_json(change: GoalChange) -> str:
    """Serialize a decoded change for the journal (lossless round-trip)."""
    return json.dumps(change.to_dict(), ensure_ascii=False, sort_keys=True)


__all__ = [
    "apply_goal_change",
    "apply_goal_event",
    "decode_goal_change",
    "empty_goal_fold_state",
    "fold_goal",
    "goal_change_ref",
    "goal_change_to_json",
]
