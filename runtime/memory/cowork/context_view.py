"""Context-grant enforcement: bound what history a member actually sees.

``group.visible_message_range`` decides the [lo, hi] slice a member's grant
permits; this module turns that decision into the concrete view the context
assembler hands an agent. It's the *enforcement* half of the privacy seam — so a
specialist pulled into an ongoing thread with a ``from_join`` grant literally
receives only the messages from their join point onward, and prior private
context never reaches their prompt.

Pure (operates on a ``GroupState`` + an in-memory message list), so the slicing
is fully unit-tested and free of the in-flight thread-store. The realtime
context builder calls ``slice_messages`` when assembling each member's turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.memory.cowork.group import GroupState, visible_message_range


@dataclass
class MemberView:
    member_id: str
    scope: str
    message_range: tuple[int, int] | None  # inclusive [lo, hi]; None for summary
    summary_only: bool

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "scope": self.scope,
            "message_range": list(self.message_range) if self.message_range else None,
            "summary_only": self.summary_only,
        }


def resolve_view(state: GroupState, member_id: str, max_message: int) -> MemberView | None:
    """The history slice ``member_id`` may see at the current message count, or
    ``None`` if they aren't a member."""
    member = state.member(member_id)
    if member is None:
        return None
    rng = visible_message_range(member, max_message)
    return MemberView(
        member_id=member_id,
        scope=member.grant.scope,
        message_range=rng,
        summary_only=rng is None,
    )


def slice_messages(view: MemberView, messages: list[Any]) -> list[Any]:
    """Return only the messages the view permits.

    ``messages`` is the full ordered history (index = message position). A
    ``summary_only`` view gets ``[]`` (the caller should substitute a summary).
    The range is inclusive and clamped to the list bounds — never raises, never
    leaks beyond the grant."""
    if view.summary_only or view.message_range is None:
        return []
    lo, hi = view.message_range
    lo = max(0, lo)
    hi = min(len(messages) - 1, hi)
    if hi < lo:
        return []
    return messages[lo : hi + 1]
