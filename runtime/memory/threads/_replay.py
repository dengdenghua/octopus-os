"""Replay engine for the append-only event log.

Extracted from ``event_log.py`` to keep the ``EventLog`` class focused on
file I/O. These pure functions walk a slice of ``LoggedEvent`` records
and rebuild the corresponding ``Turn`` / ``Item`` graph in memory.

The replay is idempotent — duplicate ``itemId`` values are silently
merged, and compaction events replace superseded turns in-place.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from runtime.protocol.items import (
    AgentMessageItem,
    AgentPhaseSnapshot,
    ApprovalItem,
    ArtifactItem,
    CommandExecutionItem,
    ErrorItem,
    FileChange,
    FileChangeItem,
    FileHunk,
    GroundingSource,
    Item,
    ItemStatus,
    ItemType,
    McpToolCallItem,
    McpToolProgress,
    PlanItem,
    ReasoningItem,
    SteeringUserMessageItem,
    SubagentItem,
    TodoListItem,
    Turn,
    TurnParams,
    TurnStatus,
    UserMessageItem,
    VerificationItem,
    VisibilityItem,
    WorkbenchSnapshotV2,
    WorkspaceFocus,
)
from runtime.protocol.text_limits import (
    MAX_AGGREGATED_OUTPUT,
    MAX_STREAM_ITEM_CONTENT,
    OUTPUT_TRUNCATION_MARK,
    STREAM_CONTENT_TRUNCATION_MARK,
    append_capped_text,
)

if TYPE_CHECKING:
    from .event_log import LoggedEvent


# ── Item type → model class registry ─────────────────────────

_ITEM_BY_TYPE: dict[ItemType, type] = {
    ItemType.USER_MESSAGE: UserMessageItem,
    ItemType.STEERING_USER_MESSAGE: SteeringUserMessageItem,
    ItemType.AGENT_MESSAGE: AgentMessageItem,
    ItemType.REASONING: ReasoningItem,
    ItemType.PLAN: PlanItem,
    ItemType.COMMAND_EXECUTION: CommandExecutionItem,
    ItemType.FILE_CHANGE: FileChangeItem,
    ItemType.MCP_TOOL_CALL: McpToolCallItem,
    ItemType.TODO_LIST: TodoListItem,
    ItemType.SUBAGENT: SubagentItem,
    ItemType.APPROVAL: ApprovalItem,
    ItemType.VERIFICATION: VerificationItem,
    ItemType.ARTIFACT: ArtifactItem,
    ItemType.ERROR: ErrorItem,
    ItemType.VISIBILITY: VisibilityItem,
}


# ── Event application ────────────────────────────────────────


def _apply_event(
    evt: LoggedEvent,
    turns: list[Turn],
    by_id: dict[str, Turn],
) -> None:
    if evt.event == "thread_started":
        return
    if evt.event == "turn_started":
        params_raw = evt.payload.get("params")
        params = TurnParams.model_validate(params_raw) if params_raw else None
        turn = Turn(
            id=evt.turn_id or "",
            threadId=evt.thread_id,
            status=TurnStatus.IN_PROGRESS,
            startedAt=evt.ts,
            params=params,
            objectiveId=(
                evt.payload.get("objectiveId")
                if isinstance(evt.payload.get("objectiveId"), str)
                else evt.turn_id
            ),
            taskId=(
                evt.payload.get("taskId") if isinstance(evt.payload.get("taskId"), str) else None
            ),
        )
        turns.append(turn)
        by_id[turn.id] = turn
        return
    if evt.event == "turn_completed":
        turn = by_id.get(evt.turn_id or "")
        if turn:
            # evt.payload["status"] is a string from JSON ("completed", "failed").
            # A missing or future status is not evidence of success.  Keep the
            # replay fail-closed so schema drift cannot manufacture completed
            # tasks after refresh.
            status_str = str(evt.payload.get("status", "failed")).upper()
            try:
                turn.status = TurnStatus[status_str]
            except KeyError:
                turn.status = TurnStatus.FAILED
                turn.error = {
                    "message": f"unknown persisted turn status: {status_str.lower()}",
                    "code": "unknown_turn_status",
                }
            turn.completed_at = evt.ts
            turn.error = evt.payload.get("error")
        return
    if evt.event == "turn_updated":
        turn = by_id.get(evt.turn_id or "")
        if turn:
            _apply_turn_update(turn, evt.payload)
        return
    if evt.event == "item_started":
        turn = by_id.get(evt.turn_id or "")
        if not turn:
            return
        item = _decode_item(evt.payload.get("item", {}))
        if item is not None:
            _upsert_replayed_item(turn, item, completed=False)
        return
    if evt.event == "item_delta":
        turn = by_id.get(evt.turn_id or "")
        if not turn:
            return
        item_id = evt.payload.get("itemId")
        kind = evt.payload.get("kind")
        delta = evt.payload.get("delta")
        for itm in turn.items:
            if itm.id != item_id:
                continue
            _merge_delta(itm, kind, delta)
            break
        return
    if evt.event == "item_completed":
        turn = by_id.get(evt.turn_id or "")
        if not turn:
            return
        new_item = _decode_item(evt.payload.get("item", {}))
        if new_item is None:
            return
        _upsert_replayed_item(turn, new_item, completed=True)
        return
    if evt.event == "turn_compacted":
        # Compaction is a compare-and-replace checkpoint over the current
        # visible history, not an arbitrary splice. ``compact()`` always
        # folds one ordered prefix. Requiring that exact prefix here makes
        # replay deterministic when two processes compact the same (or
        # overlapping) stale snapshot: the first persisted replacement wins
        # and later stale events become no-ops instead of appending duplicate
        # summaries out of chronological order.
        superseded_ids = evt.payload.get("supersededTurnIds") or []
        if (
            not isinstance(superseded_ids, list)
            or not superseded_ids
            or any(not isinstance(turn_id, str) or not turn_id for turn_id in superseded_ids)
            or len(set(superseded_ids)) != len(superseded_ids)
            or [turn.id for turn in turns[: len(superseded_ids)]] != superseded_ids
        ):
            return
        summary_raw = evt.payload.get("summaryTurn")
        if not isinstance(summary_raw, dict):
            return
        try:
            summary_turn = Turn.model_validate(summary_raw)
        except (TypeError, ValueError):  # noqa: BLE001
            return
        turns[:] = [summary_turn, *turns[len(superseded_ids) :]]
        for sid in superseded_ids:
            by_id.pop(sid, None)
        by_id[summary_turn.id] = summary_turn


def _decode_item(raw: dict[str, Any]) -> Item | None:
    type_str = raw.get("type")
    if not type_str:
        return None
    try:
        item_type = ItemType(type_str)
    except ValueError:
        return None
    cls = _ITEM_BY_TYPE.get(item_type)
    if cls is None:
        return None
    try:
        return cls.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _upsert_replayed_item(turn: Turn, incoming: Item, *, completed: bool) -> None:
    """Idempotently fold a lifecycle snapshot into replay state.

    A completed snapshot may be observed before its delayed started snapshot
    after journal repair or event import. Never regress terminal state, and
    use protocol-authored timeline coordinates rather than append order.
    """
    existing_idx = next(
        (idx for idx, item in enumerate(turn.items) if item.id == incoming.id),
        None,
    )
    if existing_idx is None:
        turn.items.append(incoming)
    else:
        existing = turn.items[existing_idx]
        if not completed and existing.status != ItemStatus.IN_PROGRESS:
            return
        if incoming.timeline_sequence is None:
            incoming.timeline_sequence = existing.timeline_sequence
        if incoming.parent_item_id is None:
            incoming.parent_item_id = existing.parent_item_id
        if incoming.phase_id is None:
            incoming.phase_id = existing.phase_id
        turn.items[existing_idx] = incoming
    _order_replayed_timeline(turn)


def _order_replayed_timeline(turn: Turn) -> None:
    """Sort coordinated item slots while leaving legacy slots untouched."""
    sequenced = sorted(
        (item for item in turn.items if item.timeline_sequence is not None),
        key=lambda item: item.timeline_sequence or 0,
    )
    if len(sequenced) < 2:
        return
    cursor = iter(sequenced)
    turn.items = [
        next(cursor) if item.timeline_sequence is not None else item for item in turn.items
    ]


def _apply_turn_update(turn: Turn, payload: dict[str, Any]) -> None:
    if isinstance(payload.get("objectiveId"), str):
        turn.objective_id = payload["objectiveId"]
    if isinstance(payload.get("taskId"), str):
        turn.task_id = payload["taskId"]
    if isinstance(payload.get("checkpointId"), int):
        turn.checkpoint_id = payload["checkpointId"]
    if isinstance(payload.get("outcomeReason"), str):
        turn.outcome_reason = payload["outcomeReason"]
    grounding_raw = payload.get("grounding")
    if isinstance(grounding_raw, list):
        grounding: list[GroundingSource] = []
        for raw in grounding_raw:
            if not isinstance(raw, dict):
                continue
            with contextlib.suppress(TypeError, ValueError):
                grounding.append(GroundingSource.model_validate(raw))
        turn.grounding = grounding
    phases_raw = payload.get("phases")
    if isinstance(phases_raw, list):
        phases: list[AgentPhaseSnapshot] = []
        for raw in phases_raw:
            if not isinstance(raw, dict):
                continue
            try:
                phases.append(AgentPhaseSnapshot.model_validate(raw))
            except (TypeError, ValueError):  # noqa: BLE001
                continue
        turn.phases = phases
    if "workspaceFocus" in payload:
        focus_raw = payload.get("workspaceFocus")
        if focus_raw is None:
            turn.workspace_focus = None
        elif isinstance(focus_raw, dict):
            with contextlib.suppress(TypeError, ValueError):
                turn.workspace_focus = WorkspaceFocus.model_validate(focus_raw)
    if "workbenchSnapshot" in payload:
        snapshot_raw = payload.get("workbenchSnapshot")
        if snapshot_raw is None:
            turn.workbench_snapshot = None
        elif isinstance(snapshot_raw, dict):
            with contextlib.suppress(TypeError, ValueError):
                turn.workbench_snapshot = WorkbenchSnapshotV2.model_validate(snapshot_raw)


def _merge_delta(item: Item, kind: str | None, delta: Any) -> None:
    """Apply a delta event in-place. Unknown kinds are ignored.

    The set of (kind, target field) pairs lives here so the rest of the
    codebase doesn't have to know how a given subtype accumulates.
    """
    if kind == "agentMessage" and isinstance(item, AgentMessageItem) and isinstance(delta, str):
        item.text = append_capped_text(
            item.text,
            delta,
            cap=MAX_STREAM_ITEM_CONTENT,
            marker=STREAM_CONTENT_TRUNCATION_MARK,
        )
    elif kind == "reasoning" and isinstance(item, ReasoningItem) and isinstance(delta, str):
        item.content = append_capped_text(
            item.content,
            delta,
            cap=MAX_STREAM_ITEM_CONTENT,
            marker=STREAM_CONTENT_TRUNCATION_MARK,
        )
    elif kind == "plan" and isinstance(item, PlanItem) and isinstance(delta, str):
        item.text = append_capped_text(
            item.text,
            delta,
            cap=MAX_STREAM_ITEM_CONTENT,
            marker=STREAM_CONTENT_TRUNCATION_MARK,
        )
    elif (
        kind == "commandOutput"
        and isinstance(item, CommandExecutionItem)
        and isinstance(delta, str)
    ):
        item.aggregated_output = append_capped_text(
            item.aggregated_output,
            delta,
            cap=MAX_AGGREGATED_OUTPUT,
            marker=OUTPUT_TRUNCATION_MARK,
        )
    elif (
        kind == "mcpToolProgress" and isinstance(item, McpToolCallItem) and isinstance(delta, dict)
    ):
        with contextlib.suppress(TypeError, ValueError):
            item.progress = McpToolProgress.model_validate(delta)
    elif kind == "fileChangeHunk" and isinstance(item, FileChangeItem) and isinstance(delta, dict):
        _merge_file_change_hunk(item, delta)


def _merge_file_change_hunk(item: FileChangeItem, delta: dict[str, Any]) -> None:
    path = delta.get("path")
    op = delta.get("op")
    raw_hunk = delta.get("hunk")
    if (
        not isinstance(path, str)
        or op not in ("create", "update", "delete")
        or not isinstance(raw_hunk, dict)
    ):
        return
    try:
        hunk = FileHunk.model_validate(raw_hunk)
    except (TypeError, ValueError):  # noqa: BLE001
        return
    for change in item.changes:
        if change.path != path:
            continue
        hunks = list(change.hunks)
        for idx, existing in enumerate(hunks):
            if existing.id == hunk.id:
                hunks[idx] = hunk
                change.hunks = hunks
                return
        change.hunks = [*hunks, hunk]
        return
    item.changes.append(FileChange(path=path, op=op, hunks=[hunk]))
    item.status = ItemStatus.IN_PROGRESS
