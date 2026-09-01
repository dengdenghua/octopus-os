"""Small shared-blackboard helpers used by embedded agent engines."""

from __future__ import annotations

_BRIEF_VALUE_CAP = 300
_HARVEST_CAP = 4000


def blackboard_brief(turn_id: str | None, *, max_entries: int = 8) -> str:
    if not turn_id:
        return ""
    try:
        from runtime.memory.runtime_state.blackboard import get_blackboard

        board = get_blackboard(str(turn_id))
        snap = board.snapshot() if board is not None else {}
    except Exception:  # noqa: BLE001 - context enrichment is best-effort
        return ""
    if not isinstance(snap, dict) or not snap:
        return ""
    lines: list[str] = []
    for key, value in list(snap.items())[: max(1, max_entries)]:
        text = str(value)
        if len(text) > _BRIEF_VALUE_CAP:
            text = text[:_BRIEF_VALUE_CAP].rstrip() + "…"
        lines.append(f"- {key}: {text}")
    return "TEAM SHARED CONTEXT (from the shared blackboard):\n" + "\n".join(lines)


def harvest_to_blackboard(turn_id: str | None, writer: str | None, output: str) -> None:
    if not turn_id or not (output or "").strip():
        return
    try:
        from runtime.memory.runtime_state.blackboard import get_blackboard

        board = get_blackboard(str(turn_id))
        if board is not None:
            board.write(
                f"agent.{writer or 'agent'}.output",
                output[:_HARVEST_CAP],
                writer=str(writer or "agent"),
            )
    except Exception:  # noqa: BLE001 - harvesting is best-effort
        pass


__all__ = ["blackboard_brief", "harvest_to_blackboard"]
