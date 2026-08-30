// Tests for client-side event-log replay.
//
// The golden conformance test pins the TS replay against the Python
// server replay: ``__fixtures__/replay-golden.events.jsonl`` is a real
// EventLog-written log, ``__fixtures__/replay-golden.expected.json`` is
// the projection of ``EventLog.replay()`` over it. Regenerate both with
// ``__fixtures__/generate_replay_golden.py`` (repo venv). If this test
// fails after a protocol change, one of the two replays drifted — find
// out which before touching the fixture.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  emptyConversation,
  type Conversation,
  type Item,
  type Turn,
} from "./items";
import { itemStreamText, reduce } from "./reducer";
import {
  normalizeEvent,
  replayEvents,
  type LoggedEvent,
  type SequencedLoggedEvent,
} from "./replay";

// ── Fixture loading ───────────────────────────────────────────

// Vitest runs from the frontend root; jsdom rewrites import.meta.url to an
// http URL, so resolve fixtures from the working directory instead.
const FIXTURE_DIR = join(
  process.cwd(),
  "src/core/realtime/__fixtures__",
);

function loadGoldenEvents(): SequencedLoggedEvent[] {
  return readFileSync(join(FIXTURE_DIR, "replay-golden.events.jsonl"), "utf8")
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line, index) => ({
      ...(JSON.parse(line) as LoggedEvent),
      sequence: index + 1, // one-based physical line number, like the server
    }));
}

function loadGoldenExpected(): {
  turns: {
    id: string;
    status: string;
    items: { id: string; type: string; text: string; hunks?: string[] }[];
  }[];
} {
  return JSON.parse(
    readFileSync(join(FIXTURE_DIR, "replay-golden.expected.json"), "utf8"),
  );
}

/** Same projection the Python generator emits — the conformance surface. */
function project(conversation: Conversation) {
  return {
    turns: conversation.turns.map((turn) => ({
      id: turn.id,
      status: turn.status as string,
      items: turn.items.map((item) => {
        const entry: {
          id: string;
          type: string;
          text: string;
          hunks?: string[];
        } = {
          id: item.id,
          type: item.type,
          text:
            item.type === "userMessage"
              ? item.text
              : itemStreamText(item) || streamTextFallback(item),
        };
        if (item.type === "fileChange") {
          entry.hunks = item.changes.flatMap((c) =>
            (c.hunks ?? []).map((h) => h.body),
          );
        }
        return entry;
      }),
    })),
  };
}

function streamTextFallback(item: Item): string {
  switch (item.type) {
    case "agentMessage":
    case "plan":
      return item.text;
    case "reasoning":
      return item.content;
    case "commandExecution":
      return item.aggregatedOutput;
    default:
      return "";
  }
}

// ── Helpers ───────────────────────────────────────────────────

function makeTurn(id: string, status: Turn["status"] = "completed"): Turn {
  return {
    id,
    threadId: "thr",
    status,
    startedAt: "2026-07-28T00:00:00Z",
    completedAt: status === "inProgress" ? null : "2026-07-28T00:01:00Z",
    items: [],
    error: null,
  };
}

// ── normalizeEvent ────────────────────────────────────────────

describe("normalizeEvent", () => {
  const base = { threadId: "thr", ts: "2026-07-28T00:00:00Z" };

  it("maps thread_started", () => {
    expect(normalizeEvent({ ...base, event: "thread_started" })).toEqual([
      { method: "thread/started", params: { thread: { id: "thr" } } },
    ]);
  });

  it("synthesizes an inProgress turn shell for turn_started", () => {
    const [evt] = normalizeEvent({
      ...base,
      event: "turn_started",
      turnId: "t1",
    });
    expect(evt?.method).toBe("turn/started");
    if (evt?.method === "turn/started") {
      expect(evt.params.turn).toMatchObject({
        id: "t1",
        status: "inProgress",
        startedAt: "2026-07-28T00:00:00Z",
        items: [],
      });
    }
  });

  it("maps turn_completed to turn/finalized with tolerant status decode", () => {
    const cases: Array<[unknown, string]> = [
      ["completed", "completed"],
      ["paused", "paused"],
      ["cancelled", "cancelled"],
      ["interrupted", "interrupted"],
      ["failed", "failed"],
      ["garbage", "failed"],
      [undefined, "failed"],
    ];
    for (const [raw, expected] of cases) {
      const [evt] = normalizeEvent({
        ...base,
        event: "turn_completed",
        turnId: "t1",
        payload: { status: raw, error: null },
      });
      if (evt?.method === "turn/finalized") {
        expect(evt.params.status).toBe(expected);
      } else {
        throw new Error(`unexpected method for status ${String(raw)}`);
      }
    }
  });

  it("drops turn_completed without a turnId", () => {
    expect(
      normalizeEvent({ ...base, event: "turn_completed", payload: {} }),
    ).toEqual([]);
  });

  it("splits turn_updated into grounding and plan events per field", () => {
    const events = normalizeEvent({
      ...base,
      event: "turn_updated",
      turnId: "t1",
      payload: {
        grounding: [{ kind: "source", title: "a.ts", path: "src/a.ts:1" }],
        phases: [{ id: "ph1", index: 1, total: 1, title: "x", status: "running" }],
        workbenchSnapshot: { schemaVersion: 2, version: 1, status: "running", phases: [], updatedAt: "t" },
      },
    });
    expect(events.map((e) => e.method)).toEqual([
      "turn/grounding",
      "turn/plan/updated",
    ]);
    const plan = events[1];
    if (plan?.method === "turn/plan/updated") {
      expect(plan.params.phases).toHaveLength(1);
      expect(plan.params.workbenchSnapshot).toMatchObject({ version: 1 });
    }
  });

  it("emits turn/plan/updated without phases when only focus changes", () => {
    const [evt] = normalizeEvent({
      ...base,
      event: "turn_updated",
      turnId: "t1",
      payload: { workspaceFocus: null },
    });
    expect(evt?.method).toBe("turn/plan/updated");
    if (evt?.method === "turn/plan/updated") {
      expect("phases" in evt.params).toBe(false);
      expect(evt.params.workspaceFocus).toBeNull();
    }
  });

  it("maps every known item_delta kind", () => {
    const expectations: Record<string, string> = {
      agentMessage: "item/agentMessage/delta",
      reasoning: "item/reasoning/textDelta",
      plan: "item/plan/delta",
      commandOutput: "item/commandExecution/outputDelta",
      fileChangeHunk: "item/fileChange/hunkDelta",
      mcpToolProgress: "item/mcpToolCall/progress",
    };
    const deltas: Record<string, unknown> = {
      agentMessage: "x",
      reasoning: "x",
      plan: "x",
      commandOutput: "x",
      fileChangeHunk: {
        path: "a.ts",
        op: "create",
        hunk: { id: "h", oldStart: 0, oldLines: 0, newStart: 1, newLines: 1, body: "+x", decision: "pending" },
      },
      mcpToolProgress: { status: "running", updatedAt: "t" },
    };
    for (const [kind, method] of Object.entries(expectations)) {
      const [evt] = normalizeEvent({
        ...base,
        event: "item_delta",
        turnId: "t1",
        payload: { itemId: "i1", kind, delta: deltas[kind] },
      });
      expect(evt?.method).toBe(method);
    }
  });

  it("drops unknown delta kinds and malformed hunk deltas", () => {
    expect(
      normalizeEvent({
        ...base,
        event: "item_delta",
        turnId: "t1",
        payload: { itemId: "i1", kind: "futureKind", delta: {} },
      }),
    ).toEqual([]);
    expect(
      normalizeEvent({
        ...base,
        event: "item_delta",
        turnId: "t1",
        payload: { itemId: "i1", kind: "fileChangeHunk", delta: { path: 1 } },
      }),
    ).toEqual([]);
  });

  it("ignores unknown event kinds (forward compatibility)", () => {
    expect(normalizeEvent({ ...base, event: "brand_new_kind" })).toEqual([]);
  });
});

// ── reducer: turn/compacted + turn/finalized ─────────────────

describe("reducer turn/compacted", () => {
  function conversationWith(...turnIds: string[]): Conversation {
    return {
      ...emptyConversation("thr"),
      turns: turnIds.map((id) => makeTurn(id)),
    };
  }

  const compact = (
    state: Conversation,
    supersededTurnIds: string[],
    summaryId: string,
  ) =>
    reduce(state, {
      method: "turn/compacted",
      params: {
        threadId: "thr",
        supersededTurnIds,
        summaryTurn: makeTurn(summaryId),
      },
    }).next;

  it("slots the summary where the oldest superseded turn sat", () => {
    const state = conversationWith("a", "b", "c", "d");
    const next = compact(state, ["b", "c"], "sum");
    expect(next.turns.map((t) => t.id)).toEqual(["a", "sum", "d"]);
  });

  it("inserts at the head when the first turn is superseded", () => {
    const state = conversationWith("a", "b");
    const next = compact(state, ["a"], "sum");
    expect(next.turns.map((t) => t.id)).toEqual(["sum", "b"]);
  });

  it("appends when no superseded turn is found", () => {
    const state = conversationWith("a", "b");
    const next = compact(state, ["zzz"], "sum");
    expect(next.turns.map((t) => t.id)).toEqual(["a", "b", "sum"]);
  });

  it("replaces rather than duplicates a repeated summary", () => {
    const state = conversationWith("a", "b", "sum");
    const next = compact(state, ["a"], "sum");
    expect(next.turns.map((t) => t.id)).toEqual(["sum", "b"]);
  });
});

describe("reducer turn/finalized", () => {
  it("patches status/completedAt/error and closes open items", () => {
    const open: Item = {
      id: "a1",
      type: "agentMessage",
      status: "inProgress",
      createdAt: "t",
      text: "draft",
    };
    const state: Conversation = {
      ...emptyConversation("thr"),
      turns: [{ ...makeTurn("t1", "inProgress"), items: [open] }],
    };
    const next = reduce(state, {
      method: "turn/finalized",
      params: {
        threadId: "thr",
        turnId: "t1",
        status: "failed",
        completedAt: "2026-07-28T00:02:00Z",
        error: { message: "boom" },
      },
    }).next;
    const turn = next.turns[0]!;
    expect(turn.status).toBe("failed");
    expect(turn.completedAt).toBe("2026-07-28T00:02:00Z");
    expect(turn.error).toEqual({ message: "boom" });
    expect(turn.items[0]!.status).toBe("failed");
  });

  it("ignores unknown turns", () => {
    const state: Conversation = {
      ...emptyConversation("thr"),
      turns: [makeTurn("t1")],
    };
    const next = reduce(state, {
      method: "turn/finalized",
      params: {
        threadId: "thr",
        turnId: "nope",
        status: "completed",
        completedAt: null,
      },
    });
    expect(next.next).toBe(state);
  });
});

// ── replayEvents ──────────────────────────────────────────────

describe("replayEvents", () => {
  it("matches the Python server replay on the golden log", () => {
    const events = loadGoldenEvents();
    const result = replayEvents(events);
    expect(project(result.conversation)).toEqual(loadGoldenExpected());
    expect(result.cursor).toBe(events.length);
    expect(result.skipped).toBeGreaterThan(0); // the unknown futureKind delta
  });

  it("is deterministic — replaying twice yields identical state", () => {
    const events = loadGoldenEvents();
    const first = replayEvents(events).conversation;
    const second = replayEvents(events).conversation;
    expect(JSON.stringify(project(first))).toBe(JSON.stringify(project(second)));
  });

  it("materializes trailing in-flight deltas into item wire fields", () => {
    const events: LoggedEvent[] = [
      { event: "thread_started", threadId: "thr" },
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "p1", type: "plan", status: "inProgress", createdAt: "t", text: "" },
        },
      },
      {
        event: "item_delta",
        threadId: "thr",
        turnId: "t1",
        payload: { itemId: "p1", kind: "plan", delta: "a" },
      },
      {
        event: "item_delta",
        threadId: "thr",
        turnId: "t1",
        payload: { itemId: "p1", kind: "plan", delta: "b" },
      },
    ];
    const { conversation } = replayEvents(events);
    const item = conversation.turns[0]!.items[0]!;
    if (item.type !== "plan") throw new Error("wrong item");
    // Self-contained: the wire field carries the text, no buffer needed.
    expect(item.text).toBe("ab");
    expect(itemStreamText(item)).toBe("ab");
  });

  it("applies post-completion deltas in replay mode (Python parity)", () => {
    const events: LoggedEvent[] = [
      { event: "thread_started", threadId: "thr" },
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "a1", type: "agentMessage", status: "inProgress", createdAt: "t", text: "" },
        },
      },
      {
        event: "item_completed",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "a1", type: "agentMessage", status: "completed", createdAt: "t", text: "final" },
        },
      },
      {
        event: "item_delta",
        threadId: "thr",
        turnId: "t1",
        payload: { itemId: "a1", kind: "agentMessage", delta: " +late" },
      },
    ];
    const { conversation } = replayEvents(events);
    const item = conversation.turns[0]!.items[0]!;
    if (item.type !== "agentMessage") throw new Error("wrong item");
    expect(item.text).toBe("final +late");
  });

  it("marks the conversation resumed even without thread_started", () => {
    const { conversation } = replayEvents([
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
    ]);
    expect(conversation.resumeState).toBe("resumed");
    expect(conversation.turns).toHaveLength(1);
  });

  it("replays incrementally on top of a base conversation", () => {
    const base = replayEvents([
      { event: "thread_started", threadId: "thr" },
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
    ]).conversation;
    const { conversation } = replayEvents(
      [
        {
          event: "item_started",
          threadId: "thr",
          turnId: "t1",
          payload: {
            item: { id: "a1", type: "agentMessage", status: "completed", createdAt: "t", text: "hi" },
          },
        },
      ],
      { base },
    );
    expect(conversation.turns[0]!.items).toHaveLength(1);
  });
});

describe("replayEvents batch fold (§2.6)", () => {
  it("batched and event-by-event paths are deep-equal on the golden log", () => {
    const events = loadGoldenEvents();
    const batched = replayEvents(events);
    const literal = replayEvents(events, { batch: false });
    // Full-state equality, not just the projection: batching must be
    // semantically transparent everywhere.
    expect(batched.conversation).toEqual(literal.conversation);
    expect(batched.cursor).toBe(literal.cursor);
    expect(batched.replayed).toBe(literal.replayed);
    expect(batched.skipped).toBe(literal.skipped);
    // The batch path must actually engage on this delta-heavy fixture.
    expect(batched.reduceCalls).toBeLessThan(literal.reduceCalls);
    // And both paths still conform to the Python replay.
    expect(project(batched.conversation)).toEqual(loadGoldenExpected());
  });

  it("collapses consecutive deltas into one reduce call per run", () => {
    const events: LoggedEvent[] = [
      { event: "thread_started", threadId: "thr" },
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "a1", type: "agentMessage", status: "inProgress", createdAt: "t", text: "" },
        },
      },
      // 50 consecutive deltas to the same item → 1 reduce call.
      ...Array.from({ length: 50 }, (_, i) => ({
        event: "item_delta",
        threadId: "thr",
        turnId: "t1",
        payload: { itemId: "a1", kind: "agentMessage", delta: `c${i}:` },
      })),
      {
        event: "item_completed",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: {
            id: "a1",
            type: "agentMessage",
            status: "completed",
            createdAt: "t",
            text: Array.from({ length: 50 }, (_, i) => `c${i}:`).join(""),
          },
        },
      },
    ];
    const batched = replayEvents(events);
    const literal = replayEvents(events, { batch: false });
    expect(batched.conversation).toEqual(literal.conversation);
    // thread/started + turn/started + item/started + 1 merged delta run
    // + item/completed = 5 vs 54 literal.
    expect(batched.reduceCalls).toBe(5);
    expect(literal.reduceCalls).toBe(54);
  });

  it("does not merge deltas across items or across interrupting events", () => {
    const events: LoggedEvent[] = [
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "a1", type: "agentMessage", status: "inProgress", createdAt: "t", text: "" },
        },
      },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "r1", type: "reasoning", status: "inProgress", createdAt: "t", content: "" },
        },
      },
      // Interleaved items: no merge possible even with batching on.
      { event: "item_delta", threadId: "thr", turnId: "t1",
        payload: { itemId: "a1", kind: "agentMessage", delta: "A" } },
      { event: "item_delta", threadId: "thr", turnId: "t1",
        payload: { itemId: "r1", kind: "reasoning", delta: "R" } },
      { event: "item_delta", threadId: "thr", turnId: "t1",
        payload: { itemId: "a1", kind: "agentMessage", delta: "B" } },
    ];
    const batched = replayEvents(events);
    const literal = replayEvents(events, { batch: false });
    expect(batched.conversation).toEqual(literal.conversation);
    expect(batched.reduceCalls).toBe(literal.reduceCalls);
    const turn = batched.conversation.turns[0]!;
    const msg = turn.items.find((i) => i.id === "a1")!;
    const reasoning = turn.items.find((i) => i.id === "r1")!;
    expect(itemStreamText(msg)).toBe("AB");
    expect(itemStreamText(reasoning)).toBe("R");
  });

  it("collapses consecutive MCP progress events to the latest", () => {
    const events: LoggedEvent[] = [
      { event: "turn_started", threadId: "thr", turnId: "t1", ts: "t" },
      {
        event: "item_started",
        threadId: "thr",
        turnId: "t1",
        payload: {
          item: { id: "m1", type: "mcpToolCall", status: "inProgress", createdAt: "t" },
        },
      },
      ...[1, 2, 3].map((step) => ({
        event: "item_delta",
        threadId: "thr",
        turnId: "t1",
        payload: {
          itemId: "m1",
          kind: "mcpToolProgress",
          delta: { message: `step ${step}`, progress: step / 3 },
        },
      })),
    ];
    const batched = replayEvents(events);
    const literal = replayEvents(events, { batch: false });
    expect(batched.conversation).toEqual(literal.conversation);
    // turn/started + item/started + 1 collapsed progress = 3 vs 5.
    expect(batched.reduceCalls).toBe(3);
    const item = batched.conversation.turns[0]!.items[0]!;
    if (item.type !== "mcpToolCall") throw new Error("wrong item");
    expect(item.progress).toEqual({ message: "step 3", progress: 1 });
  });
});
