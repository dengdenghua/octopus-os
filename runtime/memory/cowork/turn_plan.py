"""Turn planning: the seam from group *state* to "who acts this turn".

This is the bridge that turns collaboration *mode* into *behaviour* — the
"automatic modes" the design called for. Given a thread's folded group state and
the incoming user message, it composes:

  1. @addressing  — parse ``@agent:<id>`` tokens (the existing input_mentions
     parser) plus explicit chat broadcasts such as ``@所有人`` / ``@all``, and
  2. the mode policy (``responders``)

into a small, side-effect-free ``TurnPlan`` the realtime driver can act on:
single-agent ReAct, a leader-orchestrated cluster, or a parallel swarm — without
the user manually flipping a mode.

Kept pure (operates on ``GroupState``, not the store) so the decision is fully
unit-tested; ``plan_turn_for_thread`` is the thin store-backed convenience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from runtime.core.cerebrum.input_mentions import parse_input_mentions
from runtime.memory.cowork.group import DEFAULT_MODE, GroupState, normalize_group_mode, responders

ResponseMode = Literal["chat", "cluster", "swarm"]

_CHAT_BROADCAST_MENTION_RE = re.compile(
    r"(?<![\w@])@(?:所有人|全员|all|everyone)(?!\w)",
    re.IGNORECASE,
)


def _chat_broadcast_addressed(state: GroupState, text: str) -> list[str] | None:
    """Expand an explicit chat-room broadcast mention to active agents.

    ``None`` means no broadcast token was present; an empty list means the user
    did broadcast, but the room currently has no eligible agent participant.
    The boundary checks keep email addresses and tokens such as ``@alliance``
    from accidentally waking the whole group.
    """

    if state.mode != "chat" or not _CHAT_BROADCAST_MENTION_RE.search(text):
        return None
    return [
        member.id
        for member in state.roster
        if member.kind == "agent" and member.role == "participant" and not member.muted
    ]


@dataclass
class TurnPlan:
    mode: ResponseMode
    responders: list[str]  # agent ids to run this turn (already mode-filtered)
    addressed: list[str]  # @-addressed agent ids parsed from the message
    is_multi: bool  # >1 responder → run them in parallel (swarm-style)
    reason: str  # human-readable rationale (debugging / UI hint)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "responders": self.responders,
            "addressed": self.addressed,
            "is_multi": self.is_multi,
            "reason": self.reason,
        }


def plan_turn(
    state: GroupState,
    text: str,
    *,
    persistent_group: bool = False,
    mode_override: ResponseMode | None = None,
) -> TurnPlan:
    """Decide who acts this turn from the group state + the message's @mentions.

    Pure. The realtime driver reads ``responders``/``is_multi`` to choose between
    single-agent ReAct (1 responder) and parallel execution (N), and ``mode`` for
    the orchestration style."""
    canonical_mode = normalize_group_mode(state.mode) or DEFAULT_MODE
    if canonical_mode != state.mode:
        # Old persisted ``project`` mode is a project-binding concern, not a
        # response strategy.  Folding normally migrates it already; this guard
        # also protects callers constructing a legacy GroupState directly.
        state = replace(state, mode=canonical_mode)
    if mode_override is not None:
        state = replace(state, mode=mode_override)
    text = text or ""
    addressed = list(parse_input_mentions(text).agents)
    broadcast_addressed = _chat_broadcast_addressed(state, text)
    if broadcast_addressed is not None:
        addressed = broadcast_addressed
    resp = responders(state, addressed)
    # A linked room/project home is a real group surface even when its roster
    # currently contains only one AI member.  Do not collapse that durable
    # room into the 1:1 convenience rule: ordinary chat stays human-only until
    # somebody explicitly @mentions an agent. Cluster/swarm modes retain their
    # existing dispatch policies.
    durable_chat = state.mode == "chat" and bool(state.room_id or persistent_group)
    if durable_chat and not addressed:
        resp = []
    is_multi = len(resp) > 1

    if broadcast_addressed is not None:
        reason = (
            f"@all — all {len(resp)} active participant agent(s)"
            if resp
            else "@all — no active participant agents"
        )
    elif not resp:
        reason = (
            "addressed agents are not active members"
            if addressed
            else "persistent group chat — waiting for an @mention"
            if durable_chat
            else "group chat with multiple members — waiting for an @mention"
        )
    elif addressed and set(resp) & set(addressed):
        reason = f"@addressed: {', '.join(resp)}"
    elif state.mode == "swarm":
        reason = f"swarm — all {len(resp)} participant agent(s) in parallel"
    elif state.mode == "cluster":
        reason = f"cluster — leader {resp[0]} orchestrates"
    else:
        reason = f"1:1 — {resp[0]} responds"

    return TurnPlan(
        mode=state.mode,
        responders=resp,
        addressed=addressed,
        is_multi=is_multi,
        reason=reason,
    )


def plan_turn_for_thread(
    store,
    thread_id: str,
    text: str,
    *,
    persistent_group: bool = False,
    mode_override: ResponseMode | None = None,
) -> TurnPlan:
    """Store-backed convenience: fold the thread's group state, then plan."""
    return plan_turn(
        store.state(thread_id),
        text,
        persistent_group=persistent_group,
        mode_override=mode_override,
    )
