"""Durable goal service — append-only journal + CAS lifecycle verbs.

Each mutation writes one ``goal/change`` event (full next snapshot or clear
tombstone) to the journal and returns the fresh projection. The pure fold in
``fold.py`` is the only reader, so a stale or concurrent mutation fails the
next fold loudly instead of being silently applied — the compare-and-swap
guard lives in the data, not in the service instance.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .domain import (
    GOAL_ALREADY_EXISTS,
    GOAL_INVALID_MAX_ROUNDS,
    GOAL_INVALID_OBJECTIVE,
    GOAL_NOT_FOUND,
    FoldedGoal,
    GoalBlockReason,
    GoalClearChange,
    GoalDomainError,
    GoalOperation,
    GoalRef,
    GoalSnapshot,
    GoalSnapshotChange,
)
from .fold import decode_goal_change, fold_goal

if TYPE_CHECKING:
    from runtime.memory.goals.projection import GoalProjection

GOAL_EVENT_TYPE = "goal_change"

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoalChanged:
    """Live notification after one durable goal mutation commits (dsh ``goal/changed``).

    ``goal`` is absent for a clear tombstone; ``ref`` always carries the
    freshly committed revision identity. ``agent_id`` / ``conversation_id``
    carry the owning scope (dsh scopes ``goal/changed`` to the owning agent),
    so listeners can filter by scope instead of receiving every goal's events.
    """

    operation: str
    ref: GoalRef
    goal: GoalSnapshot | None = None
    agent_id: str | None = None
    conversation_id: str | None = None


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _validate_objective(objective: Any) -> str:
    if not isinstance(objective, str) or not objective.strip() or objective != objective.strip():
        raise GoalDomainError(
            GOAL_INVALID_OBJECTIVE, "goal.objective must be non-empty and normalized"
        )
    return objective


def _validate_max_rounds(max_goal_rounds: Any) -> int:
    if (
        not isinstance(max_goal_rounds, int)
        or isinstance(max_goal_rounds, bool)
        or max_goal_rounds < 1
    ):
        raise GoalDomainError(
            GOAL_INVALID_MAX_ROUNDS, "goal.maxGoalRounds must be a positive integer"
        )
    return max_goal_rounds


@dataclass(frozen=True, slots=True)
class _GoalFilter:
    """Scope filter for one ``GoalChanged`` subscriber (dsh agent scope)."""

    agent_id: str | None = None
    conversation_id: str | None = None

    def matches(self, change: GoalChanged) -> bool:
        if self.agent_id is not None and change.agent_id != self.agent_id:
            return False
        return not (
            self.conversation_id is not None and change.conversation_id != self.conversation_id
        )


def _decode_journal_change(event: Any) -> GoalSnapshotChange | GoalClearChange | None:
    """Decode a journal ``goal_change`` event's change dict, or ``None``.

    Malformed goal changes return ``None`` (skipped) instead of raising, so a
    single bad event never breaks the bridge for other events — mirroring the
    dsh projection fold that returns the same state reference for invalid
    goal changes. A value that isn't a goal change also returns ``None``.
    """
    try:
        return decode_goal_change(getattr(event, "change", None))
    except Exception:  # noqa: BLE001 — a malformed goal change is skipped
        _logger.warning(
            "goal journal bridge: malformed goal_change event skipped (event_id=%s)",
            getattr(event, "event_id", None),
            exc_info=True,
        )
        return None


class GoalService:
    """Journal-backed goal lifecycle with dsh CAS semantics.

    ``journal`` must expose ``write(event)`` and ``read_all()`` (the
    project's ``Journal`` base or any compatible substitute). Events of
    type ``goal_change`` carry the raw dsh change dict under ``change``.

    ``agent_id`` / ``conversation_id`` bind this service's goals to a scope,
    mirrored onto every ``goal_change`` event it writes and every
    ``GoalChanged`` it emits, so subscribers can filter by scope. When the
    journal supports live fan-out (``StreamingJournal`` — i.e. ``subscribe``
    is overridden), the service bridges ``goal/changed`` to the journal: goal
    mutations written by ANY writer on the same journal broadcast to this
    service's subscribers, not just this service's own writes.
    """

    def __init__(
        self,
        journal: Any,
        *,
        agent_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self._journal = journal
        self._agent_id = agent_id
        self._conversation_id = conversation_id
        self._lock = threading.RLock()
        self._listeners: list[tuple[Callable[[GoalChanged], None], _GoalFilter | None]] = []
        # Journal event bridge: when the journal actually broadcasts writes
        # (StreamingJournal overrides ``subscribe``; the base is a no-op),
        # the notification path is the journal subscription — it fires for
        # this service's own writes AND for goal changes written by any other
        # writer on the same journal. On a base journal (no live fan-out) we
        # fall back to direct in-process notify.
        from runtime.memory.journal.journal import Journal

        self._bridge_live = type(journal).subscribe is not Journal.subscribe
        self._journal_unsub: Callable[[], None] = lambda: None
        if self._bridge_live:
            self._journal_unsub = journal.subscribe(self._on_journal_event)
        # Read surface (dsh session-surface goal fold): a seed-once,
        # advance-incrementally projection cache so frequent readers do not
        # pay an O(n) journal fold per read. ``current()`` stays the
        # authoritative full fold for CAS; this is the cheap view.
        from runtime.memory.goals.projection import GoalProjectionCache

        self._projection = GoalProjectionCache(
            journal,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )

    def subscribe(
        self,
        callback: Callable[[GoalChanged], None],
        *,
        agent_id: str | None = None,
        conversation_id: str | None = None,
        replay: bool = False,
    ) -> Callable[[], None]:
        """Register a listener for committed goal mutations, optionally scoped.

        When any scope filter is given, only ``GoalChanged`` events whose
        owning scope matches are delivered (a listener with no filter receives
        every goal's events — dsh's wildcard). ``replay=True`` additionally
        delivers already-committed ``goal_change`` events from the journal to
        this listener (in journal order) so a late/subscriber-after-restart
        consumer can catch up.

        Returns an unsubscribe callable. Listener failures are contained —
        one throwing listener never blocks the write or other listeners
        (dsh: "listener failures are contained").
        """
        filt = _GoalFilter(agent_id=agent_id, conversation_id=conversation_id)
        with self._lock:
            self._listeners.append((callback, filt))

        if replay:
            self._replay_to(callback, filt)

        def _unsubscribe() -> None:
            with self._lock:
                self._listeners[:] = [
                    (fn, flt) for fn, flt in self._listeners if fn is not callback
                ]

        return _unsubscribe

    # ─── projection ──────────────────────────────────────────────────────

    def current(self) -> FoldedGoal:
        """Fold this service's goal from its journal's goal changes.

        Scoped to this service's own ``agent_id`` / ``conversation_id`` so a
        goal owned by another writer on a shared journal never collides with
        this service's CAS guard (dsh scopes goals to the owning agent). A
        service without a scope keeps the original global fold.
        """
        return fold_goal(self._scoped_goal_events())

    def _scoped_goal_events(self) -> list:
        """Goal-change events owned by this service's scope, or all when unscoped."""
        events = [
            e for e in self._journal.read_all() if getattr(e, "event_type", "") == GOAL_EVENT_TYPE
        ]
        if self._agent_id is None and self._conversation_id is None:
            return events
        return [
            e
            for e in events
            if (self._agent_id is None or getattr(e, "agent_id", None) == self._agent_id)
            and (
                self._conversation_id is None
                or getattr(e, "conversation_id", None) == self._conversation_id
            )
        ]

    def get(self) -> GoalSnapshot | None:
        """Current goal snapshot, or ``None`` when none is active/complete."""
        return self.current().goal

    def surface(self) -> GoalProjection:
        """O(1) projected view of this service's goal (dsh surface fold).

        The projection cache seeds from the journal once and advances on
        every committed goal change — live journals through the event
        bridge, base journals through the service's own writes. The
        ``as_of`` watermark lets consumers order frames; ``current()``
        remains the authoritative full fold for CAS writes.
        """
        return self._projection.current()

    # ─── lifecycle verbs ─────────────────────────────────────────────────

    def create(self, objective: str, *, max_goal_rounds: int = 5) -> FoldedGoal:
        """Create a fresh active revision-one goal with zero rounds."""
        objective = _validate_objective(objective)
        max_goal_rounds = _validate_max_rounds(max_goal_rounds)
        with self._lock:
            folded = self.current()
            if folded.goal is not None and folded.goal.phase != "complete":
                raise GoalDomainError(
                    GOAL_ALREADY_EXISTS,
                    "goal create requires no current goal or a completed one",
                )
            now = _now_ms()
            change = GoalSnapshotChange(
                operation="create",
                goal=GoalSnapshot(
                    id=uuid4().hex,
                    revision=1,
                    objective=objective,
                    phase="active",
                    max_goal_rounds=max_goal_rounds,
                ),
                rounds_started=0,
                created_at=now,
                updated_at=now,
            )
            self._write(change)
            return self.current()

    def edit(self, objective: str) -> FoldedGoal:
        """Replace the objective; phase, rounds and timestamps are preserved."""
        objective = _validate_objective(objective)
        with self._lock:
            folded = self.current()
            current = folded.goal
            if current is None:
                raise GoalDomainError(GOAL_NOT_FOUND, "goal edit requires a current goal")
            change = GoalSnapshotChange(
                operation="edit",
                goal=GoalSnapshot(
                    id=current.id,
                    revision=current.revision + 1,
                    objective=objective,
                    phase=current.phase,
                    max_goal_rounds=current.max_goal_rounds,
                    blocked_reason=current.blocked_reason,
                ),
                rounds_started=folded.rounds_started,
                created_at=folded.created_at if folded.created_at is not None else _now_ms(),
                updated_at=max(_now_ms(), folded.updated_at or 0),
            )
            self._write(change)
            return self.current()

    def pause(self) -> FoldedGoal:
        """active → paused (definition unchanged)."""
        return self._transition("pause")

    def resume(self) -> FoldedGoal:
        """active/paused/blocked → active, within the round budget."""
        return self._transition("resume")

    def complete(self) -> FoldedGoal:
        """any non-complete phase → complete."""
        return self._transition("complete")

    def block(self, *, code: str, message: str) -> FoldedGoal:
        """active → blocked with a canonical blocker explanation."""
        reason = GoalBlockReason(code=code, message=message)
        with self._lock:
            folded = self.current()
            current = folded.goal
            if current is None:
                raise GoalDomainError(GOAL_NOT_FOUND, "goal block requires a current goal")
            if current.phase != "active":
                raise GoalDomainError(GOAL_ALREADY_EXISTS, "goal block requires an active goal")
            change = GoalSnapshotChange(
                operation="block",
                goal=GoalSnapshot(
                    id=current.id,
                    revision=current.revision + 1,
                    objective=current.objective,
                    phase="blocked",
                    max_goal_rounds=current.max_goal_rounds,
                    blocked_reason=reason,
                ),
                rounds_started=folded.rounds_started,
                created_at=folded.created_at if folded.created_at is not None else _now_ms(),
                updated_at=max(_now_ms(), folded.updated_at or 0),
            )
            self._write(change)
            return self.current()

    def clear(self) -> FoldedGoal:
        """Tombstone the current goal; the next create starts fresh."""
        with self._lock:
            folded = self.current()
            current = folded.goal
            if current is None:
                raise GoalDomainError(GOAL_NOT_FOUND, "goal clear requires a current goal")
            change = GoalClearChange(
                cleared=GoalRef(id=current.id, revision=current.revision + 1),
                cleared_at=max(_now_ms(), folded.updated_at or 0),
            )
            self._write(change)
            return self.current()

    # ─── internals ───────────────────────────────────────────────────────

    def _transition(self, operation: GoalOperation) -> FoldedGoal:
        """Shared non-create snapshot transition (pause/resume/complete)."""
        with self._lock:
            folded = self.current()
            current = folded.goal
            if current is None:
                raise GoalDomainError(GOAL_NOT_FOUND, f"goal {operation} requires a current goal")
            target_phase = {
                "pause": "paused",
                "resume": "active",
                "complete": "complete",
            }[operation]
            change = GoalSnapshotChange(
                operation=operation,
                goal=GoalSnapshot(
                    id=current.id,
                    revision=current.revision + 1,
                    objective=current.objective,
                    phase=target_phase,  # type: ignore[arg-type]
                    max_goal_rounds=current.max_goal_rounds,
                    # dsh ``withPhase``: a phase-transition snapshot never
                    # carries blockedReason — only ``block`` attaches one,
                    # and the strict decoder requires non-blocked snapshots
                    # to omit it. Copying the current reason here made
                    # blocked→complete fail its own validation.
                    blocked_reason=None,
                ),
                rounds_started=folded.rounds_started,
                created_at=folded.created_at if folded.created_at is not None else _now_ms(),
                updated_at=max(_now_ms(), folded.updated_at or 0),
            )
            self._write(change)
            return self.current()

    def _write(self, change: GoalSnapshotChange | GoalClearChange) -> None:
        from runtime.memory.journal._journal_models import GoalChangeEvent

        self._journal.write(
            GoalChangeEvent(
                change=change.to_dict(),
                agent_id=self._agent_id,
                conversation_id=self._conversation_id,
            )
        )
        if self._bridge_live:
            # The journal broadcast already notified listeners synchronously
            # inside ``write`` via ``_on_journal_event`` (and advanced the
            # projection cache through its own subscription); avoid a
            # duplicate notification and a double change application.
            return
        self._projection.apply_change(change)
        self._notify(self._to_changed(change, None))

    def _on_journal_event(self, event: Any) -> None:
        """Journal bridge: decode a committed ``goal_change`` and fan it out.

        Runs for this service's own writes (via ``StreamingJournal.write``)
        and for goal changes written by any other writer sharing the same
        journal — the cross-instance/cross-writer event bridge. Malformed
        goal changes are skipped (mirror dsh's projection posture).
        """
        if getattr(event, "event_type", "") != GOAL_EVENT_TYPE:
            return
        change = _decode_journal_change(event)
        if change is None:
            return
        self._notify(self._to_changed(change, event))

    def _replay_to(
        self,
        callback: Callable[[GoalChanged], None],
        filt: _GoalFilter | None,
    ) -> None:
        """Deliver already-committed ``goal_change`` events to one subscriber.

        Catch-up for a consumer that subscribes after the goal was created
        (e.g. after a restart): the journal is the durable source of truth,
        so replay is just a scoped read of ``goal_change`` events in order.
        Only the newly subscribed listener receives the replay.
        """
        for event in self._journal.read_all():
            if getattr(event, "event_type", "") != GOAL_EVENT_TYPE:
                continue
            change = _decode_journal_change(event)
            if change is None:
                continue
            self._notify(self._to_changed(change, event), only=(callback, filt))

    def _to_changed(
        self,
        change: GoalSnapshotChange | GoalClearChange,
        event: Any | None,
    ) -> GoalChanged:
        is_snapshot = isinstance(change, GoalSnapshotChange)
        agent_id = getattr(event, "agent_id", None) if event is not None else None
        if agent_id is None:
            agent_id = self._agent_id
        conversation_id = getattr(event, "conversation_id", None) if event is not None else None
        if conversation_id is None:
            conversation_id = self._conversation_id
        return GoalChanged(
            operation=change.operation if is_snapshot else "clear",
            ref=change.goal.ref if is_snapshot else change.cleared,
            goal=change.goal if is_snapshot else None,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )

    def _notify(
        self,
        change: GoalChanged,
        *,
        only: tuple[Callable[[GoalChanged], None], _GoalFilter | None] | None = None,
    ) -> None:
        with self._lock:
            listeners = list(self._listeners)
        if only is not None:
            listeners = [only]
        for callback, filt in listeners:
            if filt is not None and not filt.matches(change):
                continue
            try:
                callback(change)
            except Exception:  # noqa: BLE001 — listener failures are contained
                _logger.warning("goal/changed listener failed", exc_info=True)


__all__ = ["GOAL_EVENT_TYPE", "GoalChanged", "GoalService"]
