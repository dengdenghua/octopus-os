#!/usr/bin/env python3
"""Regenerate the client-replay golden conformance fixture.

Builds a synthetic per-thread event log through the real ``EventLog`` API
(so the on-disk JSONL shape is exactly what production writes), then
replays it server-side and dumps a projection the TypeScript replay must
reproduce. See ``frontend/src/core/realtime/replay.ts`` and
``docs/client-replay-design.md``.

Run from the repo root:

    .venv/bin/python frontend/src/core/realtime/__fixtures__/generate_replay_golden.py

Outputs (next to this script):

    replay-golden.events.jsonl    raw log lines, input for both replays
    replay-golden.expected.json   server-side replay projection

The projection compares turn id/status/order and per-item id/type/order
plus streamed text fields. Item *status* is intentionally excluded: the
client repairs stale ``inProgress`` items on terminal turns
(``closeItemsForTurn``) while the Python replay leaves them as logged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[4]
sys.path.insert(0, str(REPO_ROOT))

from runtime.memory.threads.event_log import EventLog  # noqa: E402
from runtime.protocol.items import (  # noqa: E402
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    FileHunk,
    GroundingSource,
    McpToolCallItem,
    PlanItem,
    ReasoningItem,
    Turn,
    TurnStatus,
    UserMessageItem,
)

THREAD_ID = "thr_golden"
LOG_PATH = FIXTURE_DIR / "replay-golden.events.jsonl"
EXPECTED_PATH = FIXTURE_DIR / "replay-golden.expected.json"


def build_log() -> EventLog:
    LOG_PATH.unlink(missing_ok=True)
    log = EventLog(LOG_PATH)
    log.thread_started(THREAD_ID)

    # ── Turn t1: full lifecycle, all text-bearing item kinds ──
    t1 = Turn(id="t1", threadId=THREAD_ID)
    log.turn_started(THREAD_ID, t1)

    log.item_started(
        THREAD_ID, "t1", UserMessageItem(id="u1", status="completed", text="hi")
    )
    log.item_started(
        THREAD_ID,
        "t1",
        AgentMessageItem(id="a1", status="inProgress", text=""),
    )
    log.item_delta(THREAD_ID, "t1", "a1", "agentMessage", "Hello, ")
    log.item_delta(THREAD_ID, "t1", "a1", "agentMessage", "world")

    # Mid-turn metadata: phases + workspaceFocus + workbenchSnapshot + grounding.
    log.turn_updated(
        THREAD_ID,
        "t1",
        phases=[
            {
                "id": "ph1",
                "index": 1,
                "total": 1,
                "title": "Answering",
                "status": "running",
            }
        ],
        workbench_snapshot={
            "schemaVersion": 2,
            "version": 1,
            "status": "running",
            "phases": [
                {
                    "id": "ph1",
                    "index": 1,
                    "total": 1,
                    "title": "Answering",
                    "status": "running",
                }
            ],
            "updatedAt": "2026-07-28T00:00:00Z",
        },
        grounding=[
            GroundingSource(
                kind="source", title="event_log.py", path="runtime/memory/threads/event_log.py:1"
            ).model_dump(by_alias=True)
        ],
    )

    log.item_completed(
        THREAD_ID,
        "t1",
        AgentMessageItem(id="a1", status="completed", text="Hello, world"),
    )
    log.item_started(
        THREAD_ID,
        "t1",
        ReasoningItem(id="r1", status="inProgress", summary=[], content="", durationMs=None),
    )
    log.item_delta(THREAD_ID, "t1", "r1", "reasoning", "thinking ")
    log.item_delta(THREAD_ID, "t1", "r1", "reasoning", "hard")
    log.item_completed(
        THREAD_ID,
        "t1",
        ReasoningItem(id="r1", status="completed", summary=[], content="thinking hard", durationMs=1200),
    )
    log.item_started(
        THREAD_ID,
        "t1",
        CommandExecutionItem(
            id="c1",
            status="inProgress",
            command="echo hi",
            cwd=None,
            aggregatedOutput="",
            exitCode=None,
            processId=None,
            networkAccess=False,
        ),
    )
    log.item_delta(THREAD_ID, "t1", "c1", "commandOutput", "hi\n")
    log.item_completed(
        THREAD_ID,
        "t1",
        CommandExecutionItem(
            id="c1",
            status="completed",
            command="echo hi",
            cwd=None,
            aggregatedOutput="hi\n",
            exitCode=0,
            processId=None,
            networkAccess=False,
        ),
    )
    log.turn_completed(THREAD_ID, "t1", TurnStatus.COMPLETED)

    # Late delta AFTER turn completion: both replays must apply it
    # unconditionally (Python ``_merge_delta`` has no status gate; the TS
    # replay bypasses its live-only gate to match).
    log.item_delta(THREAD_ID, "t1", "a1", "agentMessage", " (late)")

    # ── Turn t2: interrupted with an unfinished message ──
    t2 = Turn(id="t2", threadId=THREAD_ID)
    log.turn_started(THREAD_ID, t2)
    log.item_started(
        THREAD_ID,
        "t2",
        AgentMessageItem(id="a2", status="inProgress", text=""),
    )
    log.item_delta(THREAD_ID, "t2", "a2", "agentMessage", "partial answer")
    log.turn_completed(THREAD_ID, "t2", TurnStatus.INTERRUPTED)
    # Late delta after an INTERRUPTED turn close: the live reducer would
    # gate this behind the interrupt grace window; both replays apply it
    # unconditionally. Kept on a visible turn (unlike t1's late delta,
    # which compaction hides) so the projection asserts the parity.
    log.item_delta(THREAD_ID, "t2", "a2", "agentMessage", " tail")

    # ── Compaction: t1 folds into a summary turn ──
    summary_turn = Turn(
        id="t_summary",
        threadId=THREAD_ID,
        status=TurnStatus.COMPLETED,
    )
    summary_turn.items = [
        AgentMessageItem(id="a_sum", status="completed", text="Summary of turn 1.")
    ]
    log.turn_compacted(THREAD_ID, summary_turn, ["t1"])

    # ── Turn t3: left in-progress at log end (materialization target) ──
    t3 = Turn(id="t3", threadId=THREAD_ID)
    log.turn_started(THREAD_ID, t3)
    log.item_started(
        THREAD_ID, "t3", PlanItem(id="p3", status="inProgress", text="")
    )
    log.item_delta(THREAD_ID, "t3", "p3", "plan", "step one; ")
    log.item_delta(THREAD_ID, "t3", "p3", "plan", "step two")

    # Unknown delta kind: both replays must ignore it.
    log.item_delta(THREAD_ID, "t3", "p3", "futureKind", {"anything": True})

    # Non-text items: hunk + MCP progress deltas.
    log.item_started(
        THREAD_ID,
        "t3",
        FileChangeItem(id="f3", status="inProgress", changes=[], grantRoot=None),
    )
    log.item_delta(
        THREAD_ID,
        "t3",
        "f3",
        "fileChangeHunk",
        {
            "path": "src/new.ts",
            "op": "create",
            "hunk": FileHunk(
                id="h1",
                oldStart=0,
                oldLines=0,
                newStart=1,
                newLines=2,
                body="+++ b/src/new.ts\n+line",
                decision="pending",
            ).model_dump(by_alias=True),
        },
    )
    log.item_started(
        THREAD_ID,
        "t3",
        McpToolCallItem(
            id="m3",
            status="inProgress",
            server="fs",
            tool="read",
            arguments={},
            result=None,
            error=None,
            durationMs=None,
        ),
    )
    log.item_delta(
        THREAD_ID,
        "t3",
        "m3",
        "mcpToolProgress",
        {"status": "running", "percent": 50, "updatedAt": "2026-07-28T00:00:00Z"},
    )
    return log


def projection(turns) -> dict:
    """The comparison surface shared with the TS conformance test."""
    projected_turns = []
    for turn in turns:
        items = []
        for item in turn.items:
            text = ""
            if item.type == "agentMessage" or item.type == "plan":
                text = item.text
            elif item.type == "reasoning":
                text = item.content
            elif item.type == "commandExecution":
                text = item.aggregated_output
            elif item.type == "userMessage":
                text = item.text
            entry = {"id": item.id, "type": item.type, "text": text}
            if item.type == "fileChange":
                entry["hunks"] = [
                    h.body for c in item.changes for h in (c.hunks or [])
                ]
            items.append(entry)
        projected_turns.append(
            {"id": turn.id, "status": turn.status.value, "items": items}
        )
    return {"turns": projected_turns}


def main() -> None:
    log = build_log()
    expected = projection(log.replay())
    EXPECTED_PATH.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {LOG_PATH}")
    print(f"wrote {EXPECTED_PATH}")


if __name__ == "__main__":
    main()
