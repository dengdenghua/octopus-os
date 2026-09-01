// Reducer is the heart of the realtime client. Tests target it directly:
// no React, no WebSocket, no async — just pure transitions.
//
// Coverage:
//   * Turn upsert (started + completed by id, idempotent)
//   * Item lifecycle (started → delta accumulation → completed replace)
//   * Out-of-order delta (item not yet known → no crash, no-op)
//   * Out-of-order item/started after item/completed → no regression
//   * Token usage update preserves turns
//   * Error event surfaces an error item on the active turn
import { describe, expect, it } from "vitest";

import { emptyConversation, type Conversation, type Turn } from "./items";
import {
  itemStreamText,
  reduce,
  type ConversationEvent,
  type ReducerDiagnostic,
} from "./reducer";

const T0_ISO = "2026-01-01T00:00:00.000Z";

function blankTurn(id: string, threadId: string): Turn {
  return {
    id,
    threadId,
    status: "inProgress",
    startedAt: T0_ISO,
    completedAt: null,
    items: [],
    error: null,
  };
}

function apply(
  state: Conversation,
  ...events: ConversationEvent[]
): Conversation {
  return events.reduce((s, e) => reduce(s, e).next, state);
}

describe("reducer", () => {
  it("turn/started inserts and turn/completed replaces", () => {
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "turn/completed",
        params: {
          threadId: "th",
          turn: {
            ...blankTurn("trn-1", "th"),
            status: "completed",
            completedAt: T0_ISO,
          },
        },
      },
    );
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].status).toBe("completed");
  });

  it("turn/completed closes in-progress items missing from final payload", () => {
    const withItem = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-c",
            type: "commandExecution",
            status: "inProgress",
            createdAt: T0_ISO,
            command: "npm test",
            cwd: null,
            aggregatedOutput: "",
            exitCode: null,
            processId: null,
            networkAccess: false,
          },
        },
      },
    );
    const result = reduce(withItem, {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "completed",
          completedAt: T0_ISO,
        },
      },
    });

    expect(result.changedItemIds).toEqual(["itm-c"]);
    expect(result.next.turns[0].items[0].status).toBe("completed");
  });

  it("turn/completed marks dangling items interrupted when turn is interrupted", () => {
    const withItem = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "partial",
          },
        },
      },
    );
    const state = reduce(withItem, {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "interrupted",
          completedAt: T0_ISO,
        },
      },
    }).next;

    expect(state.turns[0].items[0].status).toBe("interrupted");
  });

  it("repairs a legacy completed public draft in an interrupted snapshot", () => {
    const withTurn = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("trn-1", "th") },
    });
    const state = reduce(withTurn, {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "interrupted",
          completedAt: T0_ISO,
          items: [
            {
              id: "draft-answer",
              type: "agentMessage",
              status: "completed",
              createdAt: T0_ISO,
              text: 'str = ""',
              messageKind: "commentary",
            },
          ],
        },
      },
    }).next;

    expect(state.turns[0].items[0].status).toBe("interrupted");
  });

  it("turn/interrupted optimistically closes the active turn", () => {
    const withItem = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "reasoning",
            status: "inProgress",
            createdAt: T0_ISO,
            content: "thinking",
          },
        },
      },
    );
    const state = reduce(withItem, {
      method: "turn/interrupted",
      params: { threadId: "th", turnId: "trn-1", completedAt: T0_ISO },
    }).next;

    expect(state.turns[0].status).toBe("interrupted");
    expect(state.turns[0].completedAt).toBe(T0_ISO);
    expect(state.turns[0].items[0].status).toBe("interrupted");
  });

  it("turn/interrupted invalidates only the last prose item, not completed evidence", () => {
    const withItems = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "tool-1",
            type: "commandExecution",
            status: "completed",
            createdAt: T0_ISO,
            command: "read_file",
            cwd: null,
            aggregatedOutput: "source",
            exitCode: 0,
            processId: null,
            networkAccess: false,
          },
        },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "draft-answer",
            type: "agentMessage",
            status: "completed",
            createdAt: T0_ISO,
            text: "unfinished",
          },
        },
      },
    );
    const state = reduce(withItems, {
      method: "turn/interrupted",
      params: { threadId: "th", turnId: "trn-1", completedAt: T0_ISO },
    }).next;

    expect(
      state.turns[0].items.find((item) => item.id === "tool-1")?.status,
    ).toBe("completed");
    expect(
      state.turns[0].items.find((item) => item.id === "draft-answer")?.status,
    ).toBe("interrupted");
  });

  it("item/started inserts, item delta accumulates, item/completed replaces", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "",
          },
        },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-a",
          delta: "hello ",
        },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-a",
          delta: "world",
        },
      },
      {
        method: "item/completed",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "agentMessage",
            status: "completed",
            createdAt: T0_ISO,
            text: "hello world",
          },
        },
      },
    );
    const item = state.turns[0].items.find((i) => i.id === "itm-a");
    expect(item?.type).toBe("agentMessage");
    if (item?.type === "agentMessage") {
      expect(item.text).toBe("hello world");
      expect(item.status).toBe("completed");
    }
  });

  it("replaces an in-progress subagent summary on a second item/started", () => {
    // The orchestrator bridge re-broadcasts item/started with a growing
    // summary so the workbench sub-agent view streams live instead of
    // waiting for the terminal item/completed snapshot. The reducer must
    // treat a second started-for-the-same-inProgress-item as a replace.
    const turn = blankTurn("trn-1", "th");
    const base = {
      id: "sub-1",
      type: "subagent",
      status: "inProgress",
      createdAt: T0_ISO,
      subagentId: "task-1",
      role: "researcher",
      summary: "",
    } as const;
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: base },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: { ...base, summary: "已找到 1 篇" },
        },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            ...base,
            summary: "已找到 1 篇，正在阅读",
            status: "inProgress",
          },
        },
      },
    );
    const item = state.turns[0].items.find((i) => i.id === "sub-1");
    expect(item?.type).toBe("subagent");
    if (item?.type === "subagent") {
      expect(item.summary).toBe("已找到 1 篇，正在阅读");
      expect(item.status).toBe("inProgress");
    }
  });

  it("buffers streamed deltas and materializes them into the completed snapshot", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "",
          },
        },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-a",
          delta: "chunk-one ",
        },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-a",
          delta: "chunk-two",
        },
      },
    );
    // While streaming, deltas live in the append buffer, not the wire
    // field — reads through the resolver see the accumulated text.
    const streaming = state.turns[0].items.find((i) => i.id === "itm-a");
    expect(streaming?.type).toBe("agentMessage");
    if (streaming?.type === "agentMessage") {
      expect(streaming.text).toBe("");
      expect(itemStreamText(streaming)).toBe("chunk-one chunk-two");
    }

    // A completed snapshot replaces the item; buffered chunks are
    // materialized INTO the wire field so the settled object is
    // self-contained (``itemStreamText`` keeps working too).
    const completed = reduce(state, {
      method: "item/completed",
      params: {
        threadId: "th",
        turnId: "trn-1",
        item: {
          id: "itm-a",
          type: "agentMessage",
          status: "completed",
          createdAt: T0_ISO,
          text: "",
        },
      },
    }).next;
    const settled = completed.turns[0].items.find((i) => i.id === "itm-a");
    if (settled?.type === "agentMessage") {
      expect(settled.text).toBe("chunk-one chunk-two");
      expect(itemStreamText(settled)).toBe("chunk-one chunk-two");
    }
  });

  it("materializes buffered deltas when the turn closes without an item snapshot", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-r",
            type: "reasoning",
            status: "inProgress",
            createdAt: T0_ISO,
            summary: [],
            content: "",
          },
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "thinking…",
          contentIndex: 0,
        },
      },
    );
    const closed = reduce(state, {
      method: "turn/interrupted",
      params: { threadId: "th", turnId: "trn-1", completedAt: T0_ISO },
    }).next;
    const reasoning = closed.turns[0].items.find((i) => i.id === "itm-r");
    if (reasoning?.type === "reasoning") {
      expect(reasoning.status).toBe("interrupted");
      expect(reasoning.content).toBe("thinking…");
    }
  });

  it("reports a diagnostic when a delta lands on a settled item", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            id: "itm-a",
            type: "agentMessage",
            status: "completed",
            createdAt: T0_ISO,
            text: "final",
          },
        },
      },
    );
    const diagnostics: ReducerDiagnostic[] = [];
    const next = reduce(
      state,
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-a",
          delta: "late",
        },
      },
      (d) => diagnostics.push(d),
    ).next;
    const item = next.turns[0].items.find((i) => i.id === "itm-a");
    if (item?.type === "agentMessage") {
      expect(item.text).toBe("final");
    }
    expect(diagnostics).toEqual([
      {
        type: "lateDeltaDropped",
        turnId: "trn-1",
        itemId: "itm-a",
        kind: "agentMessage",
        itemStatus: "completed",
        deltaLength: 4,
      },
    ]);
  });

  it("passes first-class control/artifact items through lifecycle updates", () => {
    const artifact = {
      id: "itm-art",
      type: "artifact" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      artifactId: "art-1",
      kind: "pdf" as const,
      path: "reports/out.pdf",
      mimeType: "application/pdf",
      title: "Report",
      version: 1,
      createdByItemId: null,
      previewUrl: null,
      renderStatus: "rendering" as const,
      validationStatus: "pending" as const,
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: artifact },
      },
      {
        method: "item/completed",
        params: {
          threadId: "th",
          turnId: "trn-1",
          item: {
            ...artifact,
            status: "completed",
            renderStatus: "rendered",
            validationStatus: "passed",
          },
        },
      },
    );
    const item = state.turns[0].items[0];
    expect(item.type).toBe("artifact");
    if (item.type === "artifact") {
      expect(item.renderStatus).toBe("rendered");
      expect(item.validationStatus).toBe("passed");
    }
  });

  it("delta for unknown item is a no-op (idempotent under loss)", () => {
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/agentMessage/delta",
        params: { threadId: "th", turnId: "trn-1", itemId: "lost", delta: "x" },
      },
    );
    expect(state.turns[0].items).toHaveLength(0);
  });

  it("item/started arriving after item/completed does not regress", () => {
    const completed = {
      id: "itm-x",
      type: "agentMessage" as const,
      status: "completed" as const,
      createdAt: T0_ISO,
      text: "final",
    };
    const inflight = { ...completed, status: "inProgress" as const, text: "" };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/completed",
        params: { threadId: "th", turnId: "trn-1", item: completed },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: inflight },
      },
    );
    const item = state.turns[0].items[0];
    expect(item.status).toBe("completed");
    if (item.type === "agentMessage") expect(item.text).toBe("final");
  });

  it("keeps a completed turn authoritative when its start arrives late", () => {
    const completedTurn = {
      ...blankTurn("trn-1", "th"),
      status: "completed" as const,
      completedAt: T0_ISO,
      items: [
        {
          id: "user",
          type: "userMessage" as const,
          status: "completed" as const,
          createdAt: T0_ISO,
          text: "inspect",
        },
        {
          id: "answer",
          type: "agentMessage" as const,
          status: "completed" as const,
          createdAt: T0_ISO,
          text: "done",
          timelineSequence: 3,
          parentItemId: "tool",
        },
        {
          id: "commentary",
          type: "agentMessage" as const,
          status: "completed" as const,
          createdAt: T0_ISO,
          text: "checking",
          messageKind: "commentary" as const,
          timelineSequence: 1,
        },
        {
          id: "tool",
          type: "commandExecution" as const,
          status: "completed" as const,
          createdAt: T0_ISO,
          command: "read_file",
          cwd: null,
          aggregatedOutput: "ok",
          exitCode: 0,
          processId: null,
          networkAccess: false,
          timelineSequence: 2,
          parentItemId: "commentary",
        },
      ],
    };
    const staleStart = {
      ...blankTurn("trn-1", "th"),
      items: [completedTurn.items[0]!],
    };

    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/completed",
        params: { threadId: "th", turn: completedTurn },
      },
      {
        method: "turn/started",
        params: { threadId: "th", turn: staleStart },
      },
    );

    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]?.status).toBe("completed");
    expect(state.turns[0]?.items.map((item) => item.id)).toEqual([
      "user",
      "commentary",
      "tool",
      "answer",
    ]);
  });

  it("does not let a duplicate turn start erase streamed item content", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: turn.id,
          item: {
            id: "answer",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "",
          },
        },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: turn.id,
          itemId: "answer",
          delta: "streamed text",
        },
      },
      {
        method: "turn/started",
        params: {
          threadId: "th",
          turn: {
            ...turn,
            items: [
              {
                id: "answer",
                type: "agentMessage",
                status: "inProgress",
                createdAt: T0_ISO,
                text: "",
                timelineSequence: 2,
                parentItemId: "commentary",
              },
              {
                id: "commentary",
                type: "agentMessage",
                status: "completed",
                createdAt: T0_ISO,
                text: "working",
                messageKind: "commentary",
                timelineSequence: 1,
              },
            ],
          },
        },
      },
    );

    expect(state.turns[0]?.items.map((item) => item.id)).toEqual([
      "commentary",
      "answer",
    ]);
    const answer = state.turns[0]?.items.find((item) => item.id === "answer");
    expect(answer?.timelineSequence).toBe(2);
    if (answer?.type === "agentMessage") {
      expect(itemStreamText(answer)).toBe("streamed text");
    }
  });

  it("treats an identical duplicate turn start as a true no-op", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn },
    });

    const result = reduce(state, {
      method: "turn/started",
      params: { threadId: "th", turn: { ...turn, items: [] } },
    });

    expect(result.next).toBe(state);
    expect(result.changedTurnIds).toEqual([]);
    expect(result.changedItemIds).toEqual([]);
  });

  it("orders late lifecycle snapshots by server timeline sequence", () => {
    const turn = blankTurn("trn-1", "th");
    const state = apply(
      emptyConversation("th"),
      { method: "turn/started", params: { threadId: "th", turn } },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: turn.id,
          item: {
            id: "answer",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "",
            timelineSequence: 3,
            parentItemId: "tool",
          },
        },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: turn.id,
          item: {
            id: "commentary",
            type: "agentMessage",
            status: "inProgress",
            createdAt: T0_ISO,
            text: "Checking the implementation.",
            messageKind: "commentary",
            timelineSequence: 1,
          },
        },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: turn.id,
          item: {
            id: "tool",
            type: "commandExecution",
            status: "inProgress",
            createdAt: T0_ISO,
            command: "read_file",
            cwd: null,
            aggregatedOutput: "",
            exitCode: null,
            processId: null,
            networkAccess: false,
            timelineSequence: 2,
            parentItemId: "commentary",
          },
        },
      },
    );

    expect(state.turns[0].items.map((item) => item.id)).toEqual([
      "commentary",
      "tool",
      "answer",
    ]);
  });

  it("keeps legacy user slots fixed while ordering coordinated replay items", () => {
    const user = {
      id: "user",
      type: "userMessage" as const,
      status: "completed" as const,
      createdAt: T0_ISO,
      text: "go",
    };
    const answer = {
      id: "answer",
      type: "agentMessage" as const,
      status: "completed" as const,
      createdAt: T0_ISO,
      text: "done",
      timelineSequence: 2,
    };
    const commentary = {
      id: "commentary",
      type: "agentMessage" as const,
      status: "completed" as const,
      createdAt: T0_ISO,
      text: "working",
      messageKind: "commentary" as const,
      timelineSequence: 1,
    };
    const state = apply(emptyConversation("th"), {
      method: "turn/started",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          items: [user, answer, commentary],
        },
      },
    });

    expect(state.turns[0].items.map((item) => item.id)).toEqual([
      "user",
      "commentary",
      "answer",
    ]);
  });

  it("reasoning delta accumulates onto reasoning content", () => {
    const reasoning = {
      id: "itm-r",
      type: "reasoning" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      summary: [],
      content: "",
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: reasoning },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "step one. ",
          contentIndex: 0,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "step two.",
          contentIndex: 0,
        },
      },
    );
    const item = state.turns[0].items[0];
    if (item.type === "reasoning") {
      expect(itemStreamText(item)).toBe("step one. step two.");
    } else {
      expect.fail("expected a reasoning item");
    }
  });

  it("reasoning deltas bucket by contentIndex and join in index order", () => {
    // Interleaved deltas across two contentIndex buckets — the server
    // may stream chain-of-thought (index 0) and encrypted content
    // (index 1) concurrently. The reducer must bucket them separately
    // and concatenate in ascending index order on read.
    const reasoning = {
      id: "itm-r",
      type: "reasoning" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      summary: [],
      content: "",
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: reasoning },
      },
      // Interleave: idx0 chunk, idx1 chunk, idx0 chunk, idx1 chunk
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "A",
          contentIndex: 0,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "X",
          contentIndex: 1,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "B",
          contentIndex: 0,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "Y",
          contentIndex: 1,
        },
      },
    );
    const item = state.turns[0].items[0];
    if (item.type === "reasoning") {
      // Bucket 0: "AB", Bucket 1: "XY" → "ABXY"
      expect(itemStreamText(item)).toBe("ABXY");
      // Wire field stays clean while streaming — chunks are buffered.
      expect(item.content).toBe("");
    } else {
      expect.fail("expected a reasoning item");
    }
  });

  it("reasoning contentIndex defaults to 0 (backward compatible)", () => {
    // Servers that omit contentIndex (or send 0) must behave exactly
    // like the pre-bucketing single-stream path.
    const reasoning = {
      id: "itm-r",
      type: "reasoning" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      summary: [],
      content: "",
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: reasoning },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "only. ",
          contentIndex: 0,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "bucket.",
          contentIndex: 0,
        },
      },
    );
    const item = state.turns[0].items[0];
    if (item.type === "reasoning") {
      expect(itemStreamText(item)).toBe("only. bucket.");
    }
  });

  it("materializes reasoning buckets into content on turn close", () => {
    // When the turn closes, buffered reasoning buckets must land in the
    // wire ``content`` field so the settled item is self-contained.
    const reasoning = {
      id: "itm-r",
      type: "reasoning" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      summary: [],
      content: "",
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: reasoning },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "hello ",
          contentIndex: 0,
        },
      },
      {
        method: "item/reasoning/textDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-r",
          delta: "world",
          contentIndex: 1,
        },
      },
    );
    const closed = reduce(state, {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "completed",
          completedAt: T0_ISO,
        },
      },
    }).next;
    const item = closed.turns[0].items[0];
    if (item.type === "reasoning") {
      // Buckets materialized: index 0 ("hello ") + index 1 ("world")
      expect(item.content).toBe("hello world");
      expect(itemStreamText(item)).toBe("hello world");
      expect(item.status).toBe("completed");
    }
  });

  it("commandExecution outputDelta accumulates aggregatedOutput", () => {
    const cmd = {
      id: "itm-c",
      type: "commandExecution" as const,
      status: "inProgress" as const,
      createdAt: T0_ISO,
      command: "ls",
      cwd: null,
      aggregatedOutput: "",
      exitCode: null,
      processId: null,
      networkAccess: false,
    };
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: cmd },
      },
      {
        method: "item/commandExecution/outputDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-c",
          delta: "line1\n",
        },
      },
      {
        method: "item/commandExecution/outputDelta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: "itm-c",
          delta: "line2\n",
        },
      },
    );
    const item = state.turns[0].items[0];
    if (item.type === "commandExecution") {
      expect(itemStreamText(item)).toBe("line1\nline2\n");
    } else {
      expect.fail("expected commandExecution");
    }
  });

  it("token usage update preserves turns and items", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("trn-1", "th") },
    });
    const after = reduce(before, {
      method: "thread/tokenUsage/updated",
      params: { threadId: "th", tokenUsage: { totalTokens: 42 } },
    }).next;
    expect(after.turns).toBe(before.turns);
    expect(after.tokenUsage).toEqual({ totalTokens: 42 });
  });

  it("error event surfaces an error item on the active turn", () => {
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "error",
        params: {
          threadId: "th",
          turnId: "trn-1",
          error: { message: "boom" },
          willRetry: false,
        },
      },
    );
    const errors = state.turns[0].items.filter((i) => i.type === "error");
    expect(errors).toHaveLength(1);
    if (errors[0].type === "error") {
      expect(errors[0].message).toBe("boom");
    }
  });

  it("unwraps JSON error envelopes and keeps raw diagnostics out of the title", () => {
    const raw = JSON.stringify({
      additionalDetails: null,
      codexErrorInfo: "other",
      message:
        "unexpected status 409 Conflict: Responses request replay was rejected, url: http://127.0.0.1:58238/v1/responses",
    });
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "error",
        params: {
          threadId: "th",
          turnId: "trn-1",
          error: { message: raw },
          willRetry: false,
        },
      },
    );
    const error = state.turns[0].items.find((item) => item.type === "error");
    expect(error).toMatchObject({
      type: "error",
      message:
        "unexpected status 409 Conflict: Responses request replay was rejected",
      errorInfo: {
        codexErrorInfo: "other",
        rawMessage: raw,
      },
    });
  });

  it("error item ids stay unique within the same millisecond", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("trn-1", "th") },
    });
    const originalNow = Date.now;
    Date.now = () => 123;
    try {
      const state = apply(
        before,
        {
          method: "error",
          params: {
            threadId: "th",
            turnId: "trn-1",
            error: { message: "one" },
            willRetry: false,
          },
        },
        {
          method: "error",
          params: {
            threadId: "th",
            turnId: "trn-1",
            error: { message: "two" },
            willRetry: false,
          },
        },
      );
      const errors = state.turns[0].items.filter((i) => i.type === "error");
      expect(errors).toHaveLength(2);
      expect(new Set(errors.map((i) => i.id)).size).toBe(2);
    } finally {
      Date.now = originalNow;
    }
  });

  it("unknown method is a no-op", () => {
    const before = emptyConversation("th");
    // Cast through ``unknown`` — the goal is to prove the closed-set
    // switch silently ignores anything outside the union.
    const after = reduce(before, {
      method: "future/event",
      params: {},
    } as unknown as ConversationEvent).next;
    expect(after).toBe(before);
  });

  it("hunk decision marks only the targeted hunk on the target fileChange item", () => {
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("tn", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "tn",
          item: {
            id: "it-fc",
            type: "fileChange",
            status: "inProgress",
            createdAt: T0_ISO,
            changes: [
              {
                path: "foo.py",
                op: "update",
                hunks: [
                  {
                    id: "h1",
                    oldStart: 1,
                    oldLines: 1,
                    newStart: 1,
                    newLines: 1,
                    body: "-a\n+b\n",
                    decision: "pending",
                  },
                  {
                    id: "h2",
                    oldStart: 5,
                    oldLines: 1,
                    newStart: 5,
                    newLines: 1,
                    body: "-c\n+d\n",
                    decision: "pending",
                  },
                ],
              },
            ],
            grantRoot: null,
          },
        },
      },
      {
        method: "item/fileChange/hunkDecision",
        params: {
          threadId: "th",
          turnId: "tn",
          itemId: "it-fc",
          hunkId: "h1",
          decision: "accepted",
          path: "foo.py",
        },
      },
    );
    const fc = state.turns[0].items[0];
    if (fc.type !== "fileChange") throw new Error("expected fileChange");
    const hunks = fc.changes[0].hunks!;
    expect(hunks[0].decision).toBe("accepted");
    expect(hunks[1].decision).toBe("pending");
  });

  it("hunk decision on unknown item is a no-op", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("tn", "th") },
    });
    const after = reduce(before, {
      method: "item/fileChange/hunkDecision",
      params: {
        threadId: "th",
        turnId: "tn",
        itemId: "missing",
        hunkId: "h1",
        decision: "accepted",
        path: "foo.py",
      },
    } as ConversationEvent).next;
    expect(after).toBe(before);
  });

  it("turn/plan/updated stores server-authored phases and workspace focus", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("tn", "th") },
    });
    const result = reduce(before, {
      method: "turn/plan/updated",
      params: {
        threadId: "th",
        turnId: "tn",
        phases: [
          {
            id: "phase-1",
            index: 1,
            total: 2,
            title: "Inspect context",
            status: "done",
          },
          {
            id: "phase-2",
            index: 2,
            total: 2,
            title: "Patch reducer",
            status: "running",
            activeItemId: "tool-1",
          },
        ],
        workspaceFocus: {
          itemId: "tool-1",
          view: "terminal",
          title: "Running tests",
          subtitle: "pnpm test",
        },
        workbenchSnapshot: {
          schemaVersion: 2,
          version: 1,
          status: "running",
          phases: [
            {
              id: "phase-1",
              index: 1,
              total: 2,
              title: "Inspect context",
              status: "done",
            },
            {
              id: "phase-2",
              index: 2,
              total: 2,
              title: "Patch reducer",
              status: "running",
              activeItemId: "tool-1",
            },
          ],
          currentPhaseId: "phase-2",
          currentItemId: "tool-1",
          workspaceFocus: {
            itemId: "tool-1",
            view: "terminal",
            title: "Running tests",
          },
          updatedAt: T0_ISO,
        },
      },
    });

    expect(result.changedTurnIds).toEqual(["tn"]);
    expect(result.changedItemIds).toEqual([]);
    expect(result.next.turns[0].phases?.map((phase) => phase.title)).toEqual([
      "Inspect context",
      "Patch reducer",
    ]);
    expect(result.next.turns[0].workspaceFocus).toMatchObject({
      itemId: "tool-1",
      view: "terminal",
    });
    expect(result.next.turns[0].workbenchSnapshot).toMatchObject({
      version: 1,
      currentPhaseId: "phase-2",
      currentItemId: "tool-1",
    });
  });

  it("workbench/snapshot stores the current frame and mirrors phases/focus", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("tn", "th") },
    });
    const result = reduce(before, {
      method: "workbench/snapshot",
      params: {
        threadId: "th",
        turnId: "tn",
        snapshot: {
          schemaVersion: 2,
          version: 2,
          status: "running",
          phases: [
            {
              id: "phase-a",
              index: 1,
              total: 1,
              title: "Browse docs",
              status: "running",
              activeItemId: "browser-1",
            },
          ],
          currentPhaseId: "phase-a",
          currentItemId: "browser-1",
          workspaceFocus: {
            itemId: "browser-1",
            view: "browser",
            title: "Browser",
          },
          updatedAt: T0_ISO,
        },
      },
    });

    expect(result.changedTurnIds).toEqual(["tn"]);
    expect(result.changedItemIds).toEqual([]);
    expect(result.next.turns[0].workbenchSnapshot?.version).toBe(2);
    expect(result.next.turns[0].phases?.[0]?.title).toBe("Browse docs");
    expect(result.next.turns[0].workspaceFocus?.view).toBe("browser");
  });

  it("item/mcpToolCall/progress updates the matching MCP item", () => {
    const before = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("tn", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "tn",
          item: {
            id: "mcp-1",
            type: "mcpToolCall",
            status: "inProgress",
            createdAt: T0_ISO,
            server: "browser",
            tool: "screenshot",
            arguments: {},
            result: null,
            error: null,
            durationMs: null,
          },
        },
      },
    );
    const result = reduce(before, {
      method: "item/mcpToolCall/progress",
      params: {
        threadId: "th",
        turnId: "tn",
        itemId: "mcp-1",
        progress: {
          label: "Capturing screenshot",
          status: "running",
          percent: 40,
          updatedAt: T0_ISO,
        },
        workspaceFocus: {
          itemId: "mcp-1",
          view: "browser",
          title: "Browser screenshot",
        },
      },
    });

    const item = result.next.turns[0].items[0];
    if (item.type !== "mcpToolCall") throw new Error("expected mcpToolCall");
    expect(result.changedItemIds).toEqual(["mcp-1"]);
    expect(item.progress).toMatchObject({
      label: "Capturing screenshot",
      percent: 40,
    });
    expect(result.next.turns[0].workspaceFocus?.view).toBe("browser");
  });

  it("item/fileChange/hunkDelta appends and replaces hunks idempotently", () => {
    const before = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("tn", "th") },
      },
      {
        method: "item/started",
        params: {
          threadId: "th",
          turnId: "tn",
          item: {
            id: "fc-1",
            type: "fileChange",
            status: "inProgress",
            createdAt: T0_ISO,
            changes: [],
            grantRoot: null,
          },
        },
      },
      {
        method: "item/fileChange/hunkDelta",
        params: {
          threadId: "th",
          turnId: "tn",
          itemId: "fc-1",
          path: "src/app.ts",
          op: "update",
          hunk: {
            id: "h1",
            oldStart: 1,
            oldLines: 1,
            newStart: 1,
            newLines: 1,
            body: "-old\n+new\n",
            decision: "pending",
          },
        },
      },
    );
    const after = reduce(before, {
      method: "item/fileChange/hunkDelta",
      params: {
        threadId: "th",
        turnId: "tn",
        itemId: "fc-1",
        path: "src/app.ts",
        op: "update",
        hunk: {
          id: "h1",
          oldStart: 1,
          oldLines: 1,
          newStart: 1,
          newLines: 2,
          body: "-old\n+new\n+again\n",
          decision: "pending",
        },
        workspaceFocus: {
          itemId: "fc-1",
          view: "diff",
          title: "Editing src/app.ts",
        },
      },
    }).next;

    const item = after.turns[0].items[0];
    if (item.type !== "fileChange") throw new Error("expected fileChange");
    expect(item.changes).toHaveLength(1);
    expect(item.changes[0].hunks).toHaveLength(1);
    expect(item.changes[0].hunks?.[0]?.newLines).toBe(2);
    expect(after.turns[0].workspaceFocus?.view).toBe("diff");
  });

  it("turn/metaSkill/hint attaches the hint to the matching turn", () => {
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("tn", "th") },
      },
      {
        method: "turn/metaSkill/hint",
        params: {
          threadId: "th",
          turnId: "tn",
          name: "bug-hunt",
          description: "安全漏洞猎手",
          kind: "skill_cluster",
          affinity: ["security", "code", "audit"],
          stepCount: 5,
        },
      },
    );
    expect(state.turns[0].metaSkillHint).toEqual({
      name: "bug-hunt",
      description: "安全漏洞猎手",
      kind: "skill_cluster",
      affinity: ["security", "code", "audit"],
      stepCount: 5,
    });
  });

  it("turn/metaSkill/hint for an unknown turn is a no-op", () => {
    // Race: the hint can arrive before turn/started in pathological
    // network ordering. Reducer must drop it silently rather than
    // creating a phantom turn.
    const before = apply(emptyConversation("th"));
    const result = reduce(before, {
      method: "turn/metaSkill/hint",
      params: {
        threadId: "th",
        turnId: "nope",
        name: "bug-hunt",
        description: "x",
        kind: "skill_cluster",
        affinity: [],
        stepCount: 1,
      },
    });
    expect(result.next).toBe(before);
    expect(result.changedTurnIds).toEqual([]);
  });

  it("turn/grounding attaches consulted sources to the turn", () => {
    const sources = [
      {
        kind: "doc" as const,
        title: "Hemolymph",
        path: "23-memory/hemolymph.md",
      },
      {
        kind: "source" as const,
        title: "react_loop.py",
        path: "runtime/react_loop.py:501",
      },
    ];
    const state = apply(
      emptyConversation("th"),
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("tn", "th") },
      },
      {
        method: "turn/grounding",
        params: { threadId: "th", turnId: "tn", sources },
      },
    );
    expect(state.turns[0].grounding).toEqual(sources);
  });

  it("turn/grounding with empty sources or unknown turn is a no-op", () => {
    const before = apply(emptyConversation("th"), {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("tn", "th") },
    });
    // empty list → dropped
    const empty = reduce(before, {
      method: "turn/grounding",
      params: { threadId: "th", turnId: "tn", sources: [] },
    });
    expect(empty.next).toBe(before);
    // unknown turn (race before turn/started) → dropped silently
    const unknown = reduce(before, {
      method: "turn/grounding",
      params: {
        threadId: "th",
        turnId: "nope",
        sources: [{ kind: "doc", title: "X", path: "x.md" }],
      },
    });
    expect(unknown.next).toBe(before);
    expect(unknown.changedTurnIds).toEqual([]);
  });
});

// ===========================================================================
// Out-of-order event matrix
//
// The reducer's race safety currently lives in comments (mergeStartedTurn
// guards terminal turns; mergeDelta drops late deltas; mergeCompletedTurn
// keeps a completed snapshot authoritative). This block pins those
// invariants down with exhaustive orderings so a future refactor can't
// silently reopen a terminal turn, regress settled text, or crash on a
// reordered stream.
// ===========================================================================

function permute<T>(arr: T[]): T[][] {
  if (arr.length <= 1) return [arr];
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += 1) {
    const rest = [...arr.slice(0, i), ...arr.slice(i + 1)];
    for (const tail of permute(rest)) {
      out.push([arr[i]!, ...tail]);
    }
  }
  return out;
}

describe("reducer · out-of-order event matrix", () => {
  const FINAL_ITEM = {
    id: "itm-a",
    type: "agentMessage" as const,
    status: "completed" as const,
    createdAt: T0_ISO,
    text: "final answer",
    timelineSequence: 1,
  };
  const INFLIGHT_ITEM = {
    ...FINAL_ITEM,
    status: "inProgress" as const,
    text: "",
  };

  const events = () => [
    {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("trn-1", "th") },
    },
    {
      method: "item/started",
      params: { threadId: "th", turnId: "trn-1", item: INFLIGHT_ITEM },
    },
    {
      method: "item/agentMessage/delta",
      params: {
        threadId: "th",
        turnId: "trn-1",
        itemId: FINAL_ITEM.id,
        // Prefix of the settled snapshot so an authoritative snapshot
        // always supersedes the accumulated live text (see
        // preserveCompletedStreamText).
        delta: "final ",
      },
    },
    {
      method: "item/completed",
      params: { threadId: "th", turnId: "trn-1", item: FINAL_ITEM },
    },
    {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "completed",
          completedAt: T0_ISO,
          items: [FINAL_ITEM],
        },
      },
    },
  ];

  it("converges to a completed turn with full text under every lifecycle ordering", () => {
    for (const order of permute(events())) {
      const label = order.map((e) => e.method).join("→");
      const state = apply(emptyConversation("th"), ...order);
      const turn = state.turns[0];
      expect(turn?.status, label).toBe("completed");
      expect(turn?.id, label).toBe("trn-1");
      const item = turn?.items.find((i) => i.id === FINAL_ITEM.id);
      expect(item?.status, label).toBe("completed");
      if (item?.type === "agentMessage") {
        expect(item.text, label).toBe("final answer");
      }
    }
  });

  it("never reopens a terminal turn or regresses settled text with a late started/delta", () => {
    // Start from the converged terminal state, then replay the earliest
    // events (stale turn/started, stale inflight item, stale delta) on top.
    const converged = apply(emptyConversation("th"), ...events());
    const stale = [
      {
        method: "turn/started",
        params: { threadId: "th", turn: blankTurn("trn-1", "th") },
      },
      {
        method: "item/started",
        params: { threadId: "th", turnId: "trn-1", item: INFLIGHT_ITEM },
      },
      {
        method: "item/agentMessage/delta",
        params: {
          threadId: "th",
          turnId: "trn-1",
          itemId: FINAL_ITEM.id,
          delta: " late",
        },
      },
    ];
    const state = apply(converged, ...stale);
    expect(state.turns[0]?.status).toBe("completed");
    const item = state.turns[0]?.items.find((i) => i.id === FINAL_ITEM.id);
    expect(item?.status).toBe("completed");
    if (item?.type === "agentMessage") expect(item.text).toBe("final answer");
  });

  it("keeps the turn terminal when interrupt and completed arrive in any order", () => {
    const start = {
      method: "turn/started",
      params: { threadId: "th", turn: blankTurn("trn-1", "th") },
    };
    const interrupted = {
      method: "turn/interrupted",
      params: { threadId: "th", turnId: "trn-1" },
    };
    const completed = {
      method: "turn/completed",
      params: {
        threadId: "th",
        turn: {
          ...blankTurn("trn-1", "th"),
          status: "completed",
          completedAt: T0_ISO,
          items: [FINAL_ITEM],
        },
      },
    };
    for (const order of permute([start, interrupted, completed])) {
      const label = order.map((e) => e.method).join("→");
      const state = apply(emptyConversation("th"), ...order);
      const status = state.turns[0]?.status;
      // Last terminal lifecycle event is authoritative; a terminal turn is
      // never left in-progress regardless of arrival order.
      expect(["completed", "interrupted"], label).toContain(status);
      expect(status, label).not.toBe("inProgress");
    }
  });

  it("replaying the same completed sequence twice is idempotent", () => {
    const once = apply(emptyConversation("th"), ...events());
    const twice = apply(once, ...events());
    expect(twice.turns).toEqual(once.turns);
    expect(twice.turns[0]?.status).toBe("completed");
  });

  it("workflow/completed records a bounded notification list", () => {
    const evt: ConversationEvent = {
      method: "workflow/completed",
      params: {
        threadId: "th",
        workflowName: "deploy",
        workflowDescription: "ship it",
        runId: "run-1",
        stopReason: "completed",
        success: true,
        agentsStarted: 3,
        error: null,
      },
    };
    const state = apply(emptyConversation("th"), evt);
    expect(state.workflowNotifications).toHaveLength(1);
    const note = state.workflowNotifications[0]!;
    expect(note.workflowName).toBe("deploy");
    expect(note.runId).toBe("run-1");
    expect(note.success).toBe(true);
    expect(note.agentsStarted).toBe(3);
    expect(note.receivedAt).toBeTruthy();

    // Bounded: 21 notifications keep only the latest 20.
    const many = apply(
      emptyConversation("th"),
      ...Array.from({ length: 21 }, (_, i) => ({
        method: "workflow/completed" as const,
        params: {
          threadId: "th",
          workflowName: `wf-${i}`,
          workflowDescription: "",
          runId: `run-${i}`,
          stopReason: "done",
          success: true,
          agentsStarted: 1,
          error: null,
        },
      })),
    );
    expect(many.workflowNotifications).toHaveLength(20);
    expect(many.workflowNotifications[19]!.workflowName).toBe("wf-20");
  });
});
