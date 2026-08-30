"""Authoritative cross-worker interrupt control for realtime turns.

The WebSocket and in-memory interrupt registry are process-local.  This
module bridges workers through the same two primitives that already protect
the thread journal:

* the held :class:`ThreadTurnClaim` proves that one exact turn incarnation is
  resident (no TTL or heartbeat is used as authority);
* a durable ``turn_interrupt_requested`` event addresses that claim's opaque
  epoch and is consumed by the lock owner through ``EventLog.tail_events``.

The opaque epoch closes the validation/append ABA race: if the old owner
finishes after validation and a new turn acquires the thread before the event
is appended, the new owner has a different epoch and ignores the stale signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.memory.threads.event_log import EventLog, LoggedEvent
from runtime.platform.process.thread_turn_claim import (
    ThreadTurnClaim,
    ThreadTurnClaimConflict,
    ThreadTurnClaimUnavailable,
    acquire_thread_turn_claim,
)
from runtime.protocol import Turn, TurnStatus


class InterruptControlError(RuntimeError):
    """Base class for fail-closed interrupt-control errors."""


class InterruptTargetNotFound(InterruptControlError):
    """The caller may not observe the requested thread/turn."""


class InterruptTargetInactive(InterruptControlError):
    """The requested turn is not the resident owner of its thread claim."""


class InterruptAuthorityUnavailable(InterruptControlError):
    """The shared filesystem could not prove or persist the control request."""


@dataclass(frozen=True, slots=True)
class PersistedInterrupt:
    """Accepted durable request (claim epoch intentionally stays server-side)."""

    thread_id: str
    turn_id: str
    event_id: str
    claim_epoch: str


def _metadata_value(turn: Turn, *keys: str) -> str | None:
    params = turn.params
    if params is None:
        return None
    for block in params.input:
        if not isinstance(block, dict):
            continue
        metadata = block.get("metadata")
        if not isinstance(metadata, dict):
            continue
        context = metadata.get("context")
        candidates: list[dict[str, Any]] = [metadata]
        if isinstance(context, dict):
            candidates.append(context)
        for candidate in candidates:
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _thread_principal(turns: list[Turn]) -> tuple[str | None, str | None]:
    for turn in turns:
        owner = _metadata_value(turn, "actor_id", "actorId", "owner_actor_id")
        tenant = _metadata_value(turn, "tenant_id", "tenantId")
        if owner is not None or tenant is not None:
            return owner, tenant
    return None, None


def _require_principal(
    turns: list[Turn],
    *,
    actor_id: str | None,
    tenant_id: str | None,
    auth_required: bool,
    authoritative_principal: tuple[str | None, str | None] | None = None,
    collaboration_access_granted: bool = False,
) -> None:
    stored_actor, stored_tenant = (
        authoritative_principal if authoritative_principal is not None else _thread_principal(turns)
    )
    authenticated = auth_required or actor_id is not None
    if authenticated:
        if collaboration_access_granted:
            # The gateway has just resolved an active same-tenant TeamRoom
            # member for this canonical thread. Keep the persisted tenant as
            # a second, independent boundary before accepting control writes.
            if not actor_id or not tenant_id or stored_tenant != tenant_id:
                raise InterruptTargetNotFound("unknown thread")
            return
        if not actor_id or not tenant_id or stored_actor != actor_id or stored_tenant != tenant_id:
            raise InterruptTargetNotFound("unknown thread")
        return
    # Local/no-auth compatibility: an ownerless local thread remains
    # controllable, but anonymous callers never gain access to an owned one.
    if stored_actor is not None or stored_tenant is not None:
        raise InterruptTargetNotFound("unknown thread")


def thread_store_principal(
    thread_store: Any,
    thread_id: str,
) -> tuple[str | None, str | None] | None:
    """Read the server-owned thread principal when the runtime has one.

    ``ThreadStateStore`` is the authoritative owner/tenant allocation for
    managed authenticated threads.  The event-log turn metadata remains a
    compatibility fallback for older/local runtimes, but must not override a
    principal already persisted by the server-side thread boundary.

    ``None`` means the store has no authoritative principal for this thread.
    A store read failure is different: production must fail closed rather
    than silently trusting client-era journal metadata.
    """

    if thread_store is None:
        return None
    getter = getattr(thread_store, "get", None)
    if not callable(getter):
        return None
    try:
        record = getter(thread_id)
    except Exception as exc:  # noqa: BLE001 - storage implementations vary
        raise InterruptAuthorityUnavailable(
            "persistent thread owner lookup is unavailable"
        ) from exc
    if not isinstance(record, dict):
        return None
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    # Only the explicit server-owned keys qualify.  Legacy ``actor_id`` in
    # this derived store is no stronger than the sanitized event-log fallback.
    if "owner_actor_id" not in metadata and "tenant_id" not in metadata:
        return None

    def _clean(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    return _clean(metadata.get("owner_actor_id")), _clean(metadata.get("tenant_id"))


def _prove_claim_owner(
    logs_root: Path,
    thread_id: str,
    turn_id: str,
) -> str:
    """Return the resident claim epoch, or fail without TTL inference."""

    try:
        probe = acquire_thread_turn_claim(logs_root, thread_id)
    except ThreadTurnClaimConflict as conflict:
        if conflict.active_turn_id != turn_id:
            raise InterruptTargetInactive("target turn is not active") from None
        if not conflict.claim_epoch:
            raise InterruptAuthorityUnavailable("active claim metadata is unavailable") from None
        return conflict.claim_epoch
    except ThreadTurnClaimUnavailable as exc:
        raise InterruptAuthorityUnavailable(
            "authoritative thread turn lock is unavailable"
        ) from exc
    else:
        # Acquiring the descriptor is proof that no resident owns it.  Hold it
        # until this decision is made, then release; never infer liveness from
        # a heartbeat or a stale metadata file.
        probe.release()
        raise InterruptTargetInactive("target turn is not active")


def persist_interrupt_request(
    *,
    logs_root: str | Path,
    log: EventLog,
    thread_id: str,
    turn_id: str,
    actor_id: str | None,
    tenant_id: str | None,
    auth_required: bool,
    authoritative_principal: tuple[str | None, str | None] | None = None,
    collaboration_access_granted: bool = False,
) -> PersistedInterrupt:
    """Authorize, prove and fsync one cross-worker interrupt request."""

    snapshot = log.snapshot()
    turns = log.replay(snapshot)
    if not turns:
        raise InterruptTargetNotFound("unknown thread")
    _require_principal(
        turns,
        actor_id=actor_id,
        tenant_id=tenant_id,
        auth_required=auth_required,
        authoritative_principal=authoritative_principal,
        collaboration_access_granted=collaboration_access_granted,
    )
    target = next((candidate for candidate in turns if candidate.id == turn_id), None)
    if target is None or target.thread_id != thread_id:
        # Keep cross-thread guesses indistinguishable from unknown ids.
        raise InterruptTargetNotFound("unknown thread")
    if target.status != TurnStatus.IN_PROGRESS:
        raise InterruptTargetInactive("target turn is not active")

    claim_epoch = _prove_claim_owner(Path(logs_root), thread_id, turn_id)
    try:
        event = log.turn_interrupt_requested(
            thread_id,
            turn_id,
            claim_epoch=claim_epoch,
            requested_by_actor=actor_id,
            tenant_id=tenant_id,
        )
    except OSError as exc:
        raise InterruptAuthorityUnavailable("durable interrupt journal is unavailable") from exc
    if not event.event_id:
        raise InterruptAuthorityUnavailable("durable interrupt event has no identity")
    return PersistedInterrupt(
        thread_id=thread_id,
        turn_id=turn_id,
        event_id=event.event_id,
        claim_epoch=claim_epoch,
    )


def tail_contains_interrupt(
    log: EventLog,
    after_offset: int,
    *,
    thread_id: str,
    turn_id: str,
    claim_epoch: str,
) -> tuple[bool, int]:
    """Consume appended controls and match only this exact claim epoch."""

    events, next_offset = log.tail_events(after_offset)
    return (
        any(
            _is_targeted_interrupt(
                event,
                thread_id=thread_id,
                turn_id=turn_id,
                claim_epoch=claim_epoch,
            )
            for event in events
        ),
        next_offset,
    )


def _is_targeted_interrupt(
    event: LoggedEvent,
    *,
    thread_id: str,
    turn_id: str,
    claim_epoch: str,
) -> bool:
    return (
        event.event == "turn_interrupt_requested"
        and event.thread_id == thread_id
        and event.turn_id == turn_id
        and event.payload.get("claimEpoch") == claim_epoch
    )


def claim_is_held_for_turn(
    logs_root: str | Path,
    thread_id: str,
    turn_id: str,
) -> bool:
    """No-TTL liveness check used by stale-turn recovery.

    A metadata mismatch is not proof that the requested turn is alive.  It is
    still proof that *some* owner holds the thread, so recovery must not write
    a competing terminal event while that descriptor exists.
    """

    try:
        probe = acquire_thread_turn_claim(logs_root, thread_id)
    except ThreadTurnClaimConflict:
        return True
    except ThreadTurnClaimUnavailable as exc:
        raise InterruptAuthorityUnavailable(
            "authoritative thread turn lock is unavailable"
        ) from exc
    else:
        probe.release()
        return False


def acquire_stale_recovery_claim(
    logs_root: str | Path,
    thread_id: str,
) -> ThreadTurnClaim | None:
    """Acquire recovery authority; conflict means a live owner, never stale."""

    try:
        return acquire_thread_turn_claim(logs_root, thread_id)
    except ThreadTurnClaimConflict:
        return None
    except ThreadTurnClaimUnavailable as exc:
        raise InterruptAuthorityUnavailable(
            "authoritative thread turn lock is unavailable"
        ) from exc


__all__ = [
    "InterruptAuthorityUnavailable",
    "InterruptControlError",
    "InterruptTargetInactive",
    "InterruptTargetNotFound",
    "PersistedInterrupt",
    "acquire_stale_recovery_claim",
    "claim_is_held_for_turn",
    "persist_interrupt_request",
    "tail_contains_interrupt",
    "thread_store_principal",
]
