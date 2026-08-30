"""WeChat-style thread group: membership / mode / context-grant as folded events.

The design conclusion from the conversation made concrete:

- **A thread *is* the group.** A 1:1 chat is just the N=2 degenerate case — there
  is no separate "team" entity. ``fold_state`` reconstructs the current roster +
  mode by folding an append-only event log, so "add / remove anyone at any time"
  is just appending an event.
- **Membership is event-sourced**, not a snapshot: invite / leave / mute / mode
  events anchored at a message index, so the timeline can show "Alice pulled Bob
  in at message 42" and a re-opened thread restores its exact state.
- **Context-grant on join** is the privacy seam: when you pull someone into an
  ongoing thread you choose *what slice of history* they get (all / from-here /
  a range / summary only) — so prior private context never silently leaks and a
  specialist pulled in for one question isn't handed 500 messages.
- **Collaboration mode** (chat / cluster / swarm) is a non-destructive overlay:
  switching it changes *who responds*, never the membership or history.

This module is pure (no I/O) so the folding + grant + speaker logic is fully
unit-tested; persistence lives in ``group_store`` and the shared blackboard is
the existing ``SqliteBlackboard`` namespaced by thread id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

MemberKind = Literal["agent", "human"]
MemberRole = Literal["participant", "observer"]
GrantScope = Literal["all", "from_join", "range", "summary"]
GroupMode = Literal["chat", "cluster", "swarm"]

EventAction = Literal["invite", "leave", "mute", "unmute", "mode", "room_link", "workspace_link"]

DEFAULT_MODE: GroupMode = "chat"
VALID_MODES: frozenset[str] = frozenset({"chat", "cluster", "swarm"})
LEGACY_PROJECT_MODE = "project"


def normalize_group_mode(value: object) -> GroupMode | None:
    """Project the old four-mode wire/storage contract onto response modes.

    ``project`` used to mean both "this thread has project state" and "route
    the next chat message into Project OS".  A project binding is now an
    independent capability, so legacy events and old clients safely fall back
    to ordinary chat.  Unknown values remain invalid instead of silently
    widening the response contract.
    """

    if not isinstance(value, str):
        return None
    if value == LEGACY_PROJECT_MODE:
        return DEFAULT_MODE
    if value in VALID_MODES:
        return cast("GroupMode", value)
    return None


@dataclass(frozen=True)
class ContextGrant:
    """What slice of thread history a newly-invited member may see."""

    scope: GrantScope = "all"
    from_msg: int | None = None  # for scope="range"
    to_msg: int | None = None  # for scope="range"

    def to_dict(self) -> dict:
        return {"scope": self.scope, "from_msg": self.from_msg, "to_msg": self.to_msg}

    @classmethod
    def from_dict(cls, raw: dict | None) -> ContextGrant:
        if not isinstance(raw, dict):
            return cls()
        scope = raw.get("scope")
        if scope not in ("all", "from_join", "range", "summary"):
            scope = "all"
        return cls(
            scope=scope,
            from_msg=_as_int(raw.get("from_msg")),
            to_msg=_as_int(raw.get("to_msg")),
        )


@dataclass
class MemberEvent:
    """One append-only membership/mode event on a thread's timeline."""

    action: EventAction
    actor: str  # who performed it (member id; "" for system)
    target_id: str = ""  # member affected (invite/leave/mute); "" for mode
    target_kind: MemberKind = "agent"
    role: MemberRole = "participant"
    grant: ContextGrant = field(default_factory=ContextGrant)
    mode: GroupMode | None = None  # for action="mode"
    at_message: int | None = None  # message index this event is anchored to
    ts: str = ""  # ISO timestamp (stamped by the store)
    seq: int = 0  # monotonic order within the thread (stamped by the store)
    # For action="workspace_link": {"id", "name", "mount_type"} describing the
    # bound workspace. ``None`` for all other actions.
    workspace: dict | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "actor": self.actor,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "role": self.role,
            "grant": self.grant.to_dict(),
            "mode": self.mode,
            "at_message": self.at_message,
            "ts": self.ts,
            "seq": self.seq,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> MemberEvent:
        action = raw.get("action")
        if action not in (
            "invite",
            "leave",
            "mute",
            "unmute",
            "mode",
            "room_link",
            "workspace_link",
        ):
            raise ValueError(f"unknown member event action: {action!r}")
        mode = normalize_group_mode(raw.get("mode"))
        ws_raw = raw.get("workspace")
        workspace = dict(ws_raw) if isinstance(ws_raw, dict) else None
        return cls(
            action=action,
            actor=str(raw.get("actor") or ""),
            target_id=str(raw.get("target_id") or ""),
            target_kind="human" if raw.get("target_kind") == "human" else "agent",
            role="observer" if raw.get("role") == "observer" else "participant",
            grant=ContextGrant.from_dict(raw.get("grant")),
            mode=mode,
            at_message=_as_int(raw.get("at_message")),
            ts=str(raw.get("ts") or ""),
            seq=int(raw.get("seq") or 0),
            workspace=workspace,
        )


@dataclass
class Member:
    """A folded, currently-present member of the group."""

    id: str
    kind: MemberKind
    role: MemberRole
    joined_at_message: int | None
    grant: ContextGrant
    muted: bool = False
    invited_by: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "role": self.role,
            "joined_at_message": self.joined_at_message,
            "grant": self.grant.to_dict(),
            "muted": self.muted,
            "invited_by": self.invited_by,
        }


@dataclass
class GroupState:
    """The current group: roster (in join order) + active collaboration mode."""

    roster: list[Member] = field(default_factory=list)
    mode: GroupMode = DEFAULT_MODE
    event_count: int = 0
    room_id: str | None = None  # linked Team Room (the session's other surface)
    # Linked Workspace info (``{"id", "name", "mount_type"}``) when a
    # ``workspace_link`` event has been folded in; ``None`` otherwise. The
    # *latest* ``workspace_link`` event wins, mirroring the ``room_link`` rule.
    workspace: dict | None = None

    @property
    def is_one_to_one(self) -> bool:
        """A 1:1 is the degenerate group: at most one agent + at most one human.
        The UI uses this to stay lightweight, not to branch the data model."""
        agents = sum(1 for m in self.roster if m.kind == "agent")
        humans = sum(1 for m in self.roster if m.kind == "human")
        return agents <= 1 and humans <= 1

    def member(self, member_id: str) -> Member | None:
        return next((m for m in self.roster if m.id == member_id), None)

    def to_dict(self) -> dict:
        return {
            "roster": [m.to_dict() for m in self.roster],
            "mode": self.mode,
            "event_count": self.event_count,
            "is_one_to_one": self.is_one_to_one,
            "room_id": self.room_id,
            "workspace": self.workspace,
        }


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def fold_state(events: list[MemberEvent], until_seq: int | None = None) -> GroupState:
    """Reconstruct the current group by folding the membership event log.

    Order is by ``seq``. invite adds (or re-adds — joined_at_message refreshes);
    leave removes; mute/unmute toggle; mode sets the active overlay. Removed
    members simply drop from the roster — their past blackboard writes stay
    (attributed) because the blackboard is a separate, append-only store.

    ``until_seq`` folds only events up to and including that seq — that's all
    "replay to a point" / "fork at message N" need, for free, because the whole
    state is event-sourced."""
    members: dict[str, Member] = {}
    mode: GroupMode = DEFAULT_MODE
    room_id: str | None = None
    workspace: dict | None = None
    scoped = events if until_seq is None else [e for e in events if e.seq <= until_seq]
    for ev in sorted(scoped, key=lambda e: e.seq):
        if ev.action == "invite":
            if not ev.target_id:
                continue
            members[ev.target_id] = Member(
                id=ev.target_id,
                kind=ev.target_kind,
                role=ev.role,
                joined_at_message=ev.at_message,
                grant=ev.grant,
                muted=False,
                invited_by=ev.actor,
            )
        elif ev.action == "leave":
            members.pop(ev.target_id, None)
        elif ev.action in ("mute", "unmute"):
            m = members.get(ev.target_id)
            if m is not None:
                m.muted = ev.action == "mute"
        elif ev.action == "mode":
            normalized_mode = normalize_group_mode(ev.mode)
            if normalized_mode is not None:
                mode = normalized_mode
        elif ev.action == "room_link":
            room_id = ev.target_id or None
        elif ev.action == "workspace_link":
            # The latest workspace_link event wins; fall back to target_id for
            # the workspace id when the ``workspace`` dict isn't populated.
            ws = dict(ev.workspace) if isinstance(ev.workspace, dict) else {}
            if ev.target_id and not ws.get("id"):
                ws["id"] = ev.target_id
            workspace = ws or None
    return GroupState(
        roster=list(members.values()),
        mode=mode,
        event_count=len(scoped),
        room_id=room_id,
        workspace=workspace,
    )


def visible_message_range(member: Member, current_max_message: int) -> tuple[int, int] | None:
    """The [lo, hi] message indices ``member`` is allowed to see, from its grant.

    Returns ``None`` for scope="summary" (the member gets a summary, not raw
    history). This is the privacy seam that makes pulling someone into an ongoing
    thread safe: they see exactly the granted slice, not the whole transcript."""
    grant = member.grant
    if grant.scope == "all":
        return (0, current_max_message)
    if grant.scope == "from_join":
        return (member.joined_at_message or 0, current_max_message)
    if grant.scope == "range":
        lo = grant.from_msg if grant.from_msg is not None else 0
        hi = grant.to_msg if grant.to_msg is not None else current_max_message
        return (max(0, lo), max(lo, hi))
    return None  # summary → no raw range


def responders(state: GroupState, addressed: list[str] | None = None) -> list[str]:
    """Who should act this turn — the bridge from *mode* to *behaviour*.

    This is how "modes" stop being a manual switch and become automatic:
      - chat:    @addressed members, else the sole agent (a true 1:1), else
                 nobody (wait for an @mention) — like a real group chat.
      - cluster: the leader (first agent participant) orchestrates.
      - swarm:   every unmuted agent participant works in parallel.
    Observers and muted members never respond; humans aren't auto-driven."""
    agents = [
        m for m in state.roster if m.kind == "agent" and m.role == "participant" and not m.muted
    ]
    if addressed:
        targeted = [m.id for m in agents if m.id in set(addressed)]
        if targeted:
            return targeted
    if state.mode == "swarm":
        return [m.id for m in agents]
    if state.mode == "cluster":
        return [agents[0].id] if agents else []
    # chat: the sole agent answers a 1:1; otherwise wait to be addressed.
    return [agents[0].id] if len(agents) == 1 else []
