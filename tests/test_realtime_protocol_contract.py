from __future__ import annotations

from pathlib import Path

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    AgentPhaseSnapshot,
    ApprovalItem,
    ArtifactItem,
    ItemStatus,
    ItemType,
    ServerMethod,
    SubagentItem,
    Turn,
    TurnParams,
    VerificationItem,
    WorkbenchSnapshotV2,
    WorkspaceFocus,
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_REALTIME = ROOT / "frontend" / "src" / "core" / "realtime"


REQUEST_METHODS = {
    ServerMethod.REQ_COMMAND_APPROVAL.value,
    ServerMethod.REQ_FILE_APPROVAL.value,
    ServerMethod.REQ_PERMISSIONS_APPROVAL.value,
    ServerMethod.REQ_USER_INPUT.value,
    ServerMethod.REQ_MCP_ELICITATION.value,
    ServerMethod.PLAN_MODE_EXIT_REQUEST.value,
}

RESERVED_NOT_EMITTED = {
    ServerMethod.ITEM_PLAN_DELTA.value,
    ServerMethod.ITEM_FILE_CHANGE_OUTPUT_DELTA.value,
    ServerMethod.ITEM_MCP_TOOL_CALL_PROGRESS.value,
    ServerMethod.MODEL_REROUTED.value,
}

ACTIVE_REDUCED_NOTIFICATIONS = {
    ServerMethod.THREAD_STARTED.value,
    ServerMethod.THREAD_STATUS_CHANGED.value,
    ServerMethod.THREAD_TOKEN_USAGE_UPDATED.value,
    ServerMethod.TURN_STARTED.value,
    ServerMethod.TURN_COMPLETED.value,
    ServerMethod.TURN_INTERRUPTED.value,
    ServerMethod.TURN_DIFF_UPDATED.value,
    ServerMethod.TURN_PLAN_UPDATED.value,
    ServerMethod.WORKBENCH_SNAPSHOT.value,
    ServerMethod.TURN_HEARTBEAT.value,
    ServerMethod.TURN_META_SKILL_HINT.value,
    ServerMethod.TURN_GROUNDING.value,
    ServerMethod.TURN_COMPACTED.value,
    ServerMethod.WORKFLOW_COMPLETED.value,
    ServerMethod.ITEM_STARTED.value,
    ServerMethod.ITEM_COMPLETED.value,
    ServerMethod.ITEM_AGENT_MESSAGE_DELTA.value,
    ServerMethod.ITEM_REASONING_TEXT_DELTA.value,
    ServerMethod.ITEM_COMMAND_OUTPUT_DELTA.value,
    ServerMethod.ITEM_FILE_CHANGE_HUNK_DELTA.value,
    ServerMethod.ITEM_FILE_CHANGE_HUNK_DECISION.value,
    ServerMethod.ERROR.value,
}


def _read_frontend(path: str) -> str:
    return (FRONTEND_REALTIME / path).read_text(encoding="utf-8")


def test_server_methods_are_partitioned_by_frontend_contract() -> None:
    known = REQUEST_METHODS | RESERVED_NOT_EMITTED | ACTIVE_REDUCED_NOTIFICATIONS
    actual = {method.value for method in ServerMethod}
    assert actual == known


def test_active_server_notifications_are_reduced_by_frontend() -> None:
    reducer = _read_frontend("reducer.ts")
    missing = sorted(
        method for method in ACTIVE_REDUCED_NOTIFICATIONS if f'"{method}"' not in reducer
    )
    assert missing == []


def test_streaming_notifications_are_batched_by_frontend_client() -> None:
    client = _read_frontend("client.ts")
    streaming = {
        ServerMethod.ITEM_STARTED.value,
        ServerMethod.ITEM_COMPLETED.value,
        ServerMethod.ITEM_AGENT_MESSAGE_DELTA.value,
        ServerMethod.ITEM_REASONING_TEXT_DELTA.value,
        ServerMethod.ITEM_PLAN_DELTA.value,
        ServerMethod.ITEM_COMMAND_OUTPUT_DELTA.value,
    }
    missing = sorted(method for method in streaming if f'"{method}"' not in client)
    assert missing == []


def test_backend_item_types_exist_in_frontend_type_union() -> None:
    items_ts = _read_frontend("items.ts")
    missing = sorted(
        item_type.value for item_type in ItemType if f'"{item_type.value}"' not in items_ts
    )
    assert missing == []


def test_event_log_replays_full_realtime_item_union(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "threads" / "contract.jsonl")
    turn = Turn(
        threadId="contract",
        params=TurnParams(
            threadId="contract",
            input=[{"type": "text", "text": "audit"}],
        ),
    )
    items = [
        SubagentItem(subagentId="sub-1", name="worker"),
        ApprovalItem(requestId="req-1", method="req/commandApproval"),
        VerificationItem(command="pytest", kind="test", exitCode=0),
        ArtifactItem(artifactId="artifact-1", kind="log", path="output/audit.log"),
    ]
    for item in items:
        item.status = ItemStatus.COMPLETED

    log.thread_started("contract")
    log.turn_started("contract", turn)
    for item in items:
        log.item_started("contract", turn.id, item)
        log.item_completed("contract", turn.id, item)

    replayed = log.replay()

    assert [[item.type for item in replayed_turn.items] for replayed_turn in replayed] == [
        [
            ItemType.SUBAGENT,
            ItemType.APPROVAL,
            ItemType.VERIFICATION,
            ItemType.ARTIFACT,
        ]
    ]


def test_event_log_replays_workbench_snapshot(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "threads" / "snapshot.jsonl")
    turn = Turn(
        id="turn-1",
        threadId="snapshot",
        params=TurnParams(threadId="snapshot", input=[{"type": "text", "text": "build"}]),
    )
    phases = [
        AgentPhaseSnapshot(
            id="phase-1",
            index=1,
            total=1,
            title="Phase 1: Run task",
            status="running",
            activeItemId="cmd-1",
        )
    ]
    focus = WorkspaceFocus(itemId="cmd-1", view="terminal", title="Running command")
    snapshot = WorkbenchSnapshotV2(
        version=4,
        status="running",
        phases=phases,
        currentPhaseId="phase-1",
        currentItemId="cmd-1",
        workspaceFocus=focus,
    )

    log.thread_started("snapshot")
    log.turn_started("snapshot", turn)
    log.turn_updated(
        "snapshot",
        turn.id,
        phases=[phase.model_dump(by_alias=True, mode="json") for phase in phases],
        workspace_focus=focus.model_dump(by_alias=True, mode="json"),
        workbench_snapshot=snapshot.model_dump(by_alias=True, mode="json"),
    )

    replayed = log.replay()

    assert replayed[0].workbench_snapshot is not None
    assert replayed[0].workbench_snapshot.version == 4
    assert replayed[0].workbench_snapshot.current_item_id == "cmd-1"
    assert replayed[0].workbench_snapshot.workspace_focus is not None
    assert replayed[0].workbench_snapshot.workspace_focus.view == "terminal"
