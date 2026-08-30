"""Pure team-room governance helpers.

Speaker policy, the turn-based floor state machine, delegated speaking, and
the participant-role normalizers — split out of ``team_rooms_router`` to keep
that module under the god-file threshold.

Everything here is a pure function of its arguments: no router state, no
locking, no I/O, so each is directly unit-testable. The wire-model type
annotations are for documentation only — the bodies use ``getattr`` duck
typing, so there is no *runtime* import of the router and thus no import
cycle. ``team_rooms_router`` re-imports these names, so existing
``from team_rooms_router import _participant_can_speak`` call sites keep
working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .team_rooms_router import TeamParticipantWire, TeamRoomWire


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_participant_role(role: str | None) -> str:
    normalized = (role or "member").strip().lower()
    aliases = {
        "admin": "owner",
        "viewer": "viewer",
        "read_only": "viewer",
        "readonly": "viewer",
        "guest": "viewer",
        "collaborator": "member",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"owner", "member", "viewer"}:
        return "member"
    return normalized


def _normalize_participant_status(status: str | None) -> str:
    normalized = (status or "active").strip().lower()
    if normalized not in {"active", "offline", "removed"}:
        return "active"
    return normalized


# Speaker policies the room can enforce. ``free``/``admin_only`` are
# stateless; the turn-based trio drives the floor state machine below.
# Unknown values fall back to the open default rather than persisting a
# mode we can't enforce.
_TURN_POLICIES = {"round_robin", "roll_call", "moderated"}
_SPEAKER_POLICIES = {"free", "admin_only"} | _TURN_POLICIES


def _normalize_speaker_policy(value: str | None) -> str:
    normalized = (value or "free").strip().lower().replace("-", "_")
    aliases = {
        "open": "free",
        "all": "free",
        "everyone": "free",
        "admins_only": "admin_only",
        "admin": "admin_only",
        "owner_only": "admin_only",
        "muted": "admin_only",
        "locked": "admin_only",
        "rotation": "round_robin",
        "roundrobin": "round_robin",
        "take_turns": "round_robin",
        "rollcall": "roll_call",
        "call_on": "roll_call",
        "host": "moderated",
        "hosted": "moderated",
        "facilitated": "moderated",
        "queue": "moderated",
        "raise_hand": "moderated",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in _SPEAKER_POLICIES:
        return "free"
    return normalized


def _participant_is_admin(team: TeamRoomWire, participant: TeamParticipantWire) -> bool:
    """An admin is an owner-role participant, or the recorded room owner."""
    if _normalize_participant_role(participant.role) == "owner":
        return True
    actor_id = getattr(participant, "actor_id", None)
    return bool(actor_id) and actor_id == getattr(team, "owner_id", None)


def _resolve_moderator(team: TeamRoomWire) -> str | None:
    """Floor controller for roll_call/moderated — the explicit moderator
    if set, else the room owner."""
    return getattr(team, "moderator_id", None) or getattr(team, "owner_id", None)


def _is_moderator(team: TeamRoomWire, participant: TeamParticipantWire) -> bool:
    """The moderator (by participant id or actor) or any owner/admin may
    drive the floor in roll_call/moderated."""
    moderator = _resolve_moderator(team)
    if moderator is not None and moderator in {
        participant.id,
        getattr(participant, "actor_id", None),
    }:
        return True
    return _participant_is_admin(team, participant)


def _eligible_speakers(team: TeamRoomWire) -> list[TeamParticipantWire]:
    """Active, non-muted participants in a stable rotation order (by join
    time then id). These are the seats the floor rotates through."""
    eligible = [
        p
        for p in team.participants
        if getattr(p, "status", None) == "active" and not getattr(p, "muted", False)
    ]
    return sorted(eligible, key=lambda p: (p.joined_at or "", p.id))


def _next_speaker(team: TeamRoomWire, current_id: str | None) -> str | None:
    """Next floor holder after ``current_id`` in round-robin order, wrapping
    around. Returns the first eligible speaker when ``current_id`` is unset
    or no longer eligible, or None when nobody can speak."""
    order = [p.id for p in _eligible_speakers(team)]
    if not order:
        return None
    if current_id is None or current_id not in order:
        return order[0]
    idx = order.index(current_id)
    return order[(idx + 1) % len(order)]


def _initial_floor_state(team: TeamRoomWire, policy: str) -> dict[str, Any]:
    """Floor fields to apply when a room switches to ``policy``. Entering a
    turn mode seats the owner as moderator and clears the queue; round_robin
    additionally hands the floor to the first eligible speaker; leaving for a
    stateless policy tears the floor down."""
    if policy not in _TURN_POLICIES:
        return {"current_speaker_id": None, "moderator_id": None, "floor_requests": []}
    base: dict[str, Any] = {
        "moderator_id": getattr(team, "owner_id", None),
        "floor_requests": [],
        "current_speaker_id": None,
    }
    if policy == "round_robin":
        base["current_speaker_id"] = _next_speaker(team, None)
    return base


def _participant_can_speak(
    team: TeamRoomWire, participant: TeamParticipantWire
) -> tuple[bool, str | None]:
    """Resolve whether ``participant`` may broadcast a message right now.
    Returns ``(allowed, reason)`` — ``reason`` is a human-facing string
    when denied. This is the room's single speech chokepoint."""
    if getattr(participant, "muted", False):
        return False, "you have been muted by an admin"
    policy = _normalize_speaker_policy(getattr(team, "speaker_policy", "free"))
    if policy == "free":
        return True, None
    if policy == "admin_only":
        if _participant_is_admin(team, participant):
            return True, None
        return False, "only admins can speak while the room is in admin-only mode"
    # Turn-based: only the floor holder speaks. In roll_call/moderated the
    # moderator may always interject to facilitate; round_robin is strict
    # (everyone, owner included, waits their turn).
    if policy in {"roll_call", "moderated"} and _is_moderator(team, participant):
        return True, None
    if getattr(team, "current_speaker_id", None) == participant.id:
        return True, None
    reasons = {
        "round_robin": "wait for your turn — the room is taking turns",
        "roll_call": "the moderator hasn't called on you yet",
        "moderated": "raise your hand and wait for the floor",
    }
    return False, reasons.get(policy, "you don't hold the floor right now")


_SPEAK_MODES = {"manual", "twin", "hosted"}


def _normalize_speak_mode(value: str | None) -> str:
    normalized = (value or "manual").strip().lower()
    aliases = {"self": "manual", "human": "manual", "agent": "twin", "delegate": "hosted"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _SPEAK_MODES else "manual"


def _authorized_to_speak_for(sender: TeamParticipantWire, target: TeamParticipantWire) -> bool:
    """Whether ``sender`` may speak on ``target``'s behalf. Honors the
    target's OWN opt-in: hosted → the bound human host, twin → the bound
    agent. Identity matches either the participant id or the actor id, so
    both a connected human host and a backend twin agent resolve."""
    mode = _normalize_speak_mode(getattr(target, "speak_mode", "manual"))
    sender_ids = {sender.id, getattr(sender, "actor_id", None)}
    if mode == "hosted":
        return getattr(target, "host_id", None) in sender_ids
    if mode == "twin":
        return getattr(target, "twin_agent_id", None) in sender_ids
    return False


def _caller_is_team_admin(team: Any, actor: str | None) -> bool:
    """True if ``actor`` is the team owner — either the recorded
    ``owner_id`` or an active participant whose role is ``owner``.
    Used to gate participant role/status mutations and the removal
    of *other* members. Returns False for unauthenticated callers."""
    if not actor:
        return False
    if actor == getattr(team, "owner_id", None):
        return True
    for p in getattr(team, "participants", []):
        if (
            getattr(p, "actor_id", None) == actor
            and getattr(p, "status", None) != "removed"
            and _normalize_participant_role(p.role) == "owner"
        ):
            return True
    return False


# ─── Floor state transitions ─────────────────────────────────────────
# Each returns the room with the floor advanced, or None when there is
# nothing to do, so the WS handler reduces to a ``with lock`` that swaps
# the result in and broadcasts. They never touch the room store or
# persist — that stays with the caller, under its lock.


def apply_floor_request(team: TeamRoomWire | None, participant_id: str) -> TeamRoomWire | None:
    """Queue a raised hand for the moderator (``moderated`` policy only).
    None when there's nothing to do — no room, wrong policy, or the hand is
    already up."""
    if team is None:
        return None
    if _normalize_speaker_policy(team.speaker_policy) != "moderated":
        return None
    if participant_id in (team.floor_requests or []):
        return None
    return team.model_copy(
        update={
            "floor_requests": [*(team.floor_requests or []), participant_id],
            "updated_at": _now(),
        }
    )


def apply_floor_yield(team: TeamRoomWire | None, participant_id: str) -> TeamRoomWire | None:
    """Release the floor ``participant_id`` holds: round_robin advances to
    the next seat, the other turn modes re-open the floor. None when the
    caller doesn't currently hold it."""
    if team is None or team.current_speaker_id != participant_id:
        return None
    policy = _normalize_speaker_policy(team.speaker_policy)
    nxt = _next_speaker(team, participant_id) if policy == "round_robin" else None
    return team.model_copy(
        update={
            "current_speaker_id": nxt,
            "updated_at": _now(),
        }
    )


def apply_floor_grant(
    team: TeamRoomWire | None,
    actor_participant: TeamParticipantWire,
    target: str | None,
) -> TeamRoomWire | None:
    """Moderator hands the floor to ``target`` (None re-opens it).
    roll_call + moderated only. None when the policy doesn't apply OR the
    caller isn't the moderator — the WS handler re-checks the latter to
    surface a ``not_moderator`` error."""
    if team is None:
        return None
    if _normalize_speaker_policy(team.speaker_policy) not in {"roll_call", "moderated"}:
        return None
    if not _is_moderator(team, actor_participant):
        return None
    return team.model_copy(
        update={
            "current_speaker_id": target,
            "floor_requests": [q for q in (team.floor_requests or []) if q != target],
            "updated_at": _now(),
        }
    )


def advance_round_robin(team: TeamRoomWire, participant_id: str) -> TeamRoomWire:
    """Hand the round_robin floor to the next eligible speaker after
    ``participant_id`` — called once a message lands."""
    return team.model_copy(
        update={
            "current_speaker_id": _next_speaker(team, participant_id),
            "updated_at": _now(),
        }
    )
