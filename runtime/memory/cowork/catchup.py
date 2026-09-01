"""Catch-up brief for a member who just joined an ongoing thread.

The ``summary`` / ``from_join`` grant scopes already decide *what* a newcomer may
see; this turns that into a "here's where we are" brief — like a teammate getting
caught up when pulled into a meeting. Deterministic + pure (no LLM), so it's
testable and free; an LLM polish step can wrap ``render`` later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.memory.cowork.context_view import resolve_view, slice_messages
from runtime.memory.cowork.group import GroupState


@dataclass
class CatchUp:
    member_id: str
    roster: list[str]
    visible_count: int
    summary_only: bool
    recent: list[str]
    blackboard_keys: list[str]

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "roster": self.roster,
            "visible_count": self.visible_count,
            "summary_only": self.summary_only,
            "recent": self.recent,
            "blackboard_keys": self.blackboard_keys,
        }

    def render(self) -> str:
        who = "、".join(self.roster) or "(无)"
        lines = [f"你加入了协作。当前成员:{who}。"]
        if self.summary_only:
            lines.append("(按授权,你看到的是摘要,而非完整聊天记录。)")
        else:
            lines.append(f"你可见 {self.visible_count} 条消息。")
        if self.recent:
            lines.append("最近进展:")
            lines.extend(f"  - {r}" for r in self.recent)
        if self.blackboard_keys:
            lines.append("共享黑板已有:" + "、".join(self.blackboard_keys))
        return "\n".join(lines)


def _as_text(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return str(msg.get("text") or msg.get("content") or msg.get("body") or "")
    return str(msg)


def build_catchup(
    state: GroupState,
    member_id: str,
    messages: list[Any],
    blackboard: dict[str, Any] | None = None,
    *,
    recent: int = 3,
) -> CatchUp | None:
    """Build the brief for ``member_id`` over the history their grant permits.
    Returns ``None`` if they aren't a member."""
    view = resolve_view(state, member_id, max_message=max(0, len(messages) - 1))
    if view is None:
        return None
    visible = slice_messages(view, messages)
    recent_texts = [t for t in (_as_text(m) for m in visible[-recent:]) if t]
    return CatchUp(
        member_id=member_id,
        roster=[m.id for m in state.roster],
        visible_count=len(visible),
        summary_only=view.summary_only,
        recent=recent_texts,
        blackboard_keys=sorted((blackboard or {}).keys()),
    )
