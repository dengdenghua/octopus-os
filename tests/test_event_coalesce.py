"""coalesce_events — replay-equivalent shrinking of raw log slices.

The load-bearing invariant: replaying a coalesced slice yields the same
turns as replaying the raw slice. Tested by feeding BOTH through the real
``EventLogSnapshot.replay()`` (the production server replay) rather than
hand-asserting individual fields.
"""

from __future__ import annotations

from pathlib import Path

from runtime.memory.threads.event_log import (
    EventLog,
    EventLogSnapshot,
    LoggedEvent,
    coalesce_events,
)
from runtime.protocol.items import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    FileHunk,
    McpToolCallItem,
    Turn,
    TurnStatus,
)


def _replay(events: list[tuple[int, LoggedEvent]]) -> list[dict]:
    snapshot = EventLogSnapshot(events=tuple(events), cursor=len(events))
    return [turn.model_dump(by_alias=True, mode="json") for turn in snapshot.replay()]


def _build_log(path: Path) -> EventLog:
    log = EventLog(path)
    log.thread_started("th")

    log.turn_started("th", Turn(id="t1", threadId="th"))
    # Completed item with many deltas: all redundant in coalesced mode.
    log.item_started("th", "t1", AgentMessageItem(id="a1", status="inProgress", text=""))
    for chunk in ["alpha ", "beta ", "gamma ", "delta"]:
        log.item_delta("th", "t1", "a1", "agentMessage", chunk)
    log.item_completed(
        "th", "t1", AgentMessageItem(id="a1", status="completed", text="alpha beta gamma delta")
    )
    # Late delta AFTER completion: must survive coalescing.
    log.item_delta("th", "t1", "a1", "agentMessage", " tail")

    # Repeated absolute patches: only the merged/last one matters.
    log.turn_updated(
        "th",
        "t1",
        phases=[{"id": "p1", "index": 1, "total": 2, "title": "first", "status": "running"}],
    )
    log.turn_updated(
        "th",
        "t1",
        phases=[{"id": "p2", "index": 2, "total": 2, "title": "second", "status": "done"}],
        grounding=[{"kind": "source", "title": "f.py", "path": "src/f.py:1"}],
    )
    log.turn_completed("th", "t1", TurnStatus.COMPLETED)

    log.turn_started("th", Turn(id="t2", threadId="th"))
    # In-progress item: deltas must be merged, never dropped.
    log.item_started(
        "th",
        "t2",
        CommandExecutionItem(
            id="c2",
            status="inProgress",
            command="tail -f",
            cwd=None,
            aggregatedOutput="",
            exitCode=None,
            processId=None,
            networkAccess=False,
        ),
    )
    for chunk in ["line1\n", "line2\n", "line3\n"]:
        log.item_delta("th", "t2", "c2", "commandOutput", chunk)
    # MCP progress: absolute patches, only the latest survives.
    log.item_started(
        "th",
        "t2",
        McpToolCallItem(
            id="m2",
            status="inProgress",
            server="fs",
            tool="read",
            arguments={},
            result=None,
            error=None,
            durationMs=None,
        ),
    )
    for pct in (10, 40, 90):
        log.item_delta("th", "t2", "m2", "mcpToolProgress", {"percent": pct, "updatedAt": "t"})
    # Hunks pass through untouched.
    log.item_started(
        "th", "t2", FileChangeItem(id="f2", status="inProgress", changes=[], grantRoot=None)
    )
    log.item_delta(
        "th",
        "t2",
        "f2",
        "fileChangeHunk",
        {
            "path": "a.ts",
            "op": "create",
            "hunk": FileHunk(
                id="h1",
                oldStart=0,
                oldLines=0,
                newStart=1,
                newLines=1,
                body="+x",
                decision="pending",
            ).model_dump(by_alias=True),
        },
    )
    # t2 never completes — trailing in-progress turn.
    return log


def test_coalesced_replay_matches_raw_replay(tmp_path: Path) -> None:
    log = _build_log(tmp_path / "th.jsonl")
    raw = list(log.snapshot().events)
    coalesced = coalesce_events(raw)
    assert _replay(coalesced) == _replay(raw)


def test_coalesce_shrinks_and_preserves_semantics(tmp_path: Path) -> None:
    log = _build_log(tmp_path / "th.jsonl")
    raw = list(log.snapshot().events)
    coalesced = coalesce_events(raw)

    assert len(coalesced) < len(raw)

    kinds = [event.event for _, event in coalesced]
    # The completed item's start + 4 pre-completion deltas are gone...
    message_deltas = [
        e for _, e in coalesced if e.event == "item_delta" and e.payload.get("itemId") == "a1"
    ]
    # ...but the late post-completion delta survives.
    assert len(message_deltas) == 1
    assert message_deltas[0].payload["delta"] == " tail"

    # In-progress command output merges into ONE concatenated delta.
    command_deltas = [
        e for _, e in coalesced if e.event == "item_delta" and e.payload.get("itemId") == "c2"
    ]
    assert len(command_deltas) == 1
    assert command_deltas[0].payload["delta"] == "line1\nline2\nline3\n"
    assert command_deltas[0].payload.get("coalesced") is True

    # MCP progress collapses to the latest absolute patch.
    progress = [
        e for _, e in coalesced if e.event == "item_delta" and e.payload.get("itemId") == "m2"
    ]
    assert len(progress) == 1
    assert progress[0].payload["delta"]["percent"] == 90

    # turn_updated merges per turn, later fields win.
    updates = [e for _, e in coalesced if e.event == "turn_updated"]
    assert len(updates) == 1
    assert updates[0].payload["phases"][0]["title"] == "second"
    assert updates[0].payload["grounding"][0]["path"] == "src/f.py:1"

    # Lifecycle events are never dropped.
    assert kinds[0] == "thread_started"
    assert kinds.count("turn_started") == 2
    assert "turn_completed" in kinds
    assert "item_completed" in kinds

    # Output sequences stay ordered (merged events keep the first
    # contributor's sequence; gaps are legal).
    sequences = [seq for seq, _ in coalesced]
    assert sequences == sorted(sequences)


def test_coalesce_empty_and_passthrough(tmp_path: Path) -> None:
    assert coalesce_events([]) == []

    # A slice with no coalescible content passes through unchanged.
    log = EventLog(tmp_path / "th2.jsonl")
    log.thread_started("th2")
    log.turn_started("th2", Turn(id="t1", threadId="th2"))
    log.turn_completed("th2", "t1", TurnStatus.COMPLETED)
    raw = list(log.snapshot().events)
    assert coalesce_events(raw) == raw

