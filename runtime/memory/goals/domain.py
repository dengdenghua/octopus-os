"""Goal domain vocabulary — dsh ``@deepseek-ai/dsh-goal`` port.

Semantics mirrored from dsh:

- a goal is a durable snapshot with a strictly increasing ``revision``;
- the phase machine is ``active → paused|blocked|complete`` with strict
  transition rules (see ``fold.py``);
- every mutation carries the full next snapshot; replaying the change log
  folds the current projection (``FoldedGoal``);
- a clear leaves a tombstone ``GoalRef`` so the cleared goal identity stays
  reconcilable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

GoalPhase = Literal["active", "paused", "blocked", "complete"]
GoalOperation = Literal["create", "edit", "pause", "resume", "complete", "block", "clear"]

GOAL_CHANGE_KIND = "goal/change"
GOAL_CHANGE_VERSION = 1

# dsh stable error codes for rejected goal reads and mutations.
GOAL_NOT_FOUND = "GOAL_NOT_FOUND"
GOAL_ALREADY_EXISTS = "GOAL_ALREADY_EXISTS"
GOAL_STALE_REVISION = "GOAL_STALE_REVISION"
GOAL_INVALID_OBJECTIVE = "GOAL_INVALID_OBJECTIVE"
GOAL_INVALID_MAX_ROUNDS = "GOAL_INVALID_MAX_ROUNDS"
GOAL_INVALID_BLOCK_REASON = "GOAL_INVALID_BLOCK_REASON"
GOAL_INVALID_EDIT = "GOAL_INVALID_EDIT"
GOAL_INVALID_TRANSITION = "GOAL_INVALID_TRANSITION"
GOAL_INVALID_CHANGE = "GOAL_INVALID_CHANGE"

_BLOCK_CODE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class GoalDomainError(ValueError):
    """A goal read or mutation was rejected with a stable dsh error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoalBlockReason:
    """Canonical blocker explanation (lower-kebab code + normalized message)."""

    code: str
    message: str

    def __post_init__(self) -> None:
        validate_block_reason(self.code, self.message)


def validate_block_reason(code: str, message: str) -> None:
    if not isinstance(code, str) or _BLOCK_CODE_RE.fullmatch(code) is None:
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON,
            f"goal change goal.blockedReason.code must be lower-kebab-case, got {code!r}",
        )
    if not isinstance(message, str) or not message.strip() or message != message.strip():
        raise GoalDomainError(
            GOAL_INVALID_BLOCK_REASON,
            "goal change goal.blockedReason.message must be non-empty and normalized",
        )


@dataclass(frozen=True, slots=True)
class GoalSnapshot:
    """Immutable full snapshot of the current goal (revision is CAS identity)."""

    id: str
    revision: int
    objective: str
    phase: GoalPhase
    max_goal_rounds: int
    blocked_reason: GoalBlockReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise GoalDomainError(GOAL_INVALID_CHANGE, "goal.id must be a non-empty string")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise GoalDomainError(GOAL_INVALID_CHANGE, "goal.revision must be a positive integer")
        if (
            not isinstance(self.objective, str)
            or not self.objective.strip()
            or self.objective != self.objective.strip()
        ):
            raise GoalDomainError(
                GOAL_INVALID_OBJECTIVE, "goal.objective must be non-empty and normalized"
            )
        if self.phase not in ("active", "paused", "blocked", "complete"):
            raise GoalDomainError(GOAL_INVALID_CHANGE, f"goal.phase is invalid: {self.phase!r}")
        if (
            not isinstance(self.max_goal_rounds, int)
            or isinstance(self.max_goal_rounds, bool)
            or self.max_goal_rounds < 1
        ):
            raise GoalDomainError(
                GOAL_INVALID_MAX_ROUNDS, "goal.maxGoalRounds must be a positive integer"
            )
        if self.phase == "blocked" and self.blocked_reason is None:
            raise GoalDomainError(
                GOAL_INVALID_BLOCK_REASON, "blocked goal must carry a blocked reason"
            )
        if self.phase != "blocked" and self.blocked_reason is not None:
            raise GoalDomainError(
                GOAL_INVALID_BLOCK_REASON, "non-blocked goal must not carry a blocked reason"
            )

    @property
    def ref(self) -> GoalRef:
        return GoalRef(id=self.id, revision=self.revision)

    def to_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": self.id,
            "revision": self.revision,
            "objective": self.objective,
            "phase": self.phase,
            "maxGoalRounds": self.max_goal_rounds,
        }
        if self.blocked_reason is not None:
            base["blockedReason"] = {
                "code": self.blocked_reason.code,
                "message": self.blocked_reason.message,
            }
        return base


@dataclass(frozen=True, slots=True)
class GoalRef:
    """Revision identity used to reconcile a change with its log event."""

    id: str
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise GoalDomainError(GOAL_INVALID_CHANGE, "goal ref id must be a non-empty string")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise GoalDomainError(
                GOAL_INVALID_CHANGE, "goal ref revision must be a positive integer"
            )


@dataclass(frozen=True, slots=True)
class GoalSnapshotChange:
    """Durable full-snapshot goal mutation (kind ``goal/change``)."""

    operation: GoalOperation
    goal: GoalSnapshot
    rounds_started: int
    created_at: int
    updated_at: int
    version: int = GOAL_CHANGE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": GOAL_CHANGE_KIND,
            "version": self.version,
            "operation": self.operation,
            "goal": self.goal.to_dict(),
            "roundsStarted": self.rounds_started,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GoalClearChange:
    """Durable tombstone retained when the current goal is cleared."""

    cleared: GoalRef
    cleared_at: int
    version: int = GOAL_CHANGE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": GOAL_CHANGE_KIND,
            "version": self.version,
            "operation": "clear",
            "cleared": {"id": self.cleared.id, "revision": self.cleared.revision},
            "clearedAt": self.cleared_at,
        }


GoalChange = GoalSnapshotChange | GoalClearChange


@dataclass(frozen=True, slots=True)
class FoldedGoal:
    """Pure replay projection of durable goal facts."""

    goal: GoalSnapshot | None = None
    rounds_started: int = 0
    created_at: int | None = None
    updated_at: int | None = None
    last_ref: GoalRef | None = None


@dataclass(slots=True)
class GoalFoldState:
    """Mutable accumulator kept private to the pure fold."""

    goal: GoalSnapshot | None = None
    rounds_started: int = 0
    created_at: int | None = None
    updated_at: int | None = None
    last_ref: GoalRef | None = None
    seen_goal_ids: set[str] = field(default_factory=set)


__all__ = [
    "GOAL_ALREADY_EXISTS",
    "GOAL_CHANGE_KIND",
    "GOAL_CHANGE_VERSION",
    "GOAL_INVALID_BLOCK_REASON",
    "GOAL_INVALID_CHANGE",
    "GOAL_INVALID_EDIT",
    "GOAL_INVALID_MAX_ROUNDS",
    "GOAL_INVALID_OBJECTIVE",
    "GOAL_INVALID_TRANSITION",
    "GOAL_NOT_FOUND",
    "GOAL_STALE_REVISION",
    "GoalBlockReason",
    "GoalChange",
    "GoalClearChange",
    "GoalDomainError",
    "GoalFoldState",
    "GoalOperation",
    "GoalPhase",
    "GoalRef",
    "GoalSnapshot",
    "GoalSnapshotChange",
    "FoldedGoal",
    "validate_block_reason",
]
