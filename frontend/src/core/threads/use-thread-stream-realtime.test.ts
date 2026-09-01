import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ArtifactItem,
  CommandExecutionItem,
  Conversation,
  FileChangeItem,
  McpToolCallItem,
  SubagentItem,
  TodoListItem,
  Turn,
  VerificationItem,
} from "@/core/realtime/items";

import {
  useThreadStreamRealtime,
  liveToolEventsFromConversation,
  liveToolEventsFromLastTurn,
  uploadPromptInputFiles,
} from "./use-thread-stream-realtime";
import { RETRY_PENDING_MESSAGE_EVENT } from "./optimistic-messages";
import { useRealtimeThread } from "@/core/realtime";

vi.mock("@/core/realtime", () => ({
  useRealtimeThread: vi.fn(),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      agentWorkbenchPages: {
        inputsUploadedFiles: (count: number) => `${count} file(s) uploaded`,
      },
      chatInputBox: { uploadFailed: "Upload failed" },
      conversation: {
        previousMessagePending:
          "The previous message is still sending. Wait for confirmation, then retry.",
        steeringTurnUnavailable:
          "The original task is no longer running. Send this again as a new message.",
      },
    },
  }),
}));

const BASE_TS = "2026-05-09T00:00:00.000Z";
const DONE_TS = "2026-05-09T00:00:02.000Z";

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeConversation(turns: Turn[]): Conversation {
  return {
    threadId: "th-test",
    turns,
    pendingApprovals: [],
    tokenUsage: null,
    resumeState: "resumed",
  };
}

function makeConversationWithApproval(turns: Turn[]): Conversation {
  return {
    ...makeConversation(turns),
    pendingApprovals: [
      {
        requestId: 7,
        method: "item/commandExecution/requestApproval",
        params: {
          itemId: "cmd-approval",
          tool: "exec_shell",
          argsPreview: "rm -rf /tmp/demo",
          detail: "dangerous command",
        },
        createdAt: BASE_TS,
      },
    ],
  };
}

function makeTurn(items: Turn["items"], id = "turn-1"): Turn {
  return {
    id,
    threadId: "th-test",
    status: "completed",
    startedAt: BASE_TS,
    completedAt: DONE_TS,
    items,
    error: null,
  };
}

function commandItem(
  overrides: Partial<CommandExecutionItem> = {},
): CommandExecutionItem {
  return {
    id: "cmd-1",
    type: "commandExecution",
    status: "completed",
    createdAt: BASE_TS,
    command: "list_cwd",
    inputPreview: { path: "." },
    cwd: "/repo",
    aggregatedOutput: "listed files",
    exitCode: 0,
    processId: null,
    networkAccess: false,
    ...overrides,
  };
}

function mcpItem(overrides: Partial<McpToolCallItem> = {}): McpToolCallItem {
  return {
    id: "mcp-1",
    type: "mcpToolCall",
    status: "completed",
    createdAt: BASE_TS,
    server: "fs",
    tool: "read",
    arguments: { path: "README.md" },
    result: { ok: true },
    error: null,
    durationMs: 25,
    ...overrides,
  };
}

function fileChangeItem(
  overrides: Partial<FileChangeItem> = {},
): FileChangeItem {
  return {
    id: "file-1",
    type: "fileChange",
    status: "completed",
    createdAt: BASE_TS,
    changes: [{ path: "src/app.ts", op: "update" }],
    grantRoot: "/repo",
    ...overrides,
  };
}

function todoItem(overrides: Partial<TodoListItem> = {}): TodoListItem {
  return {
    id: "todo-1",
    type: "todo-list",
    status: "completed",
    createdAt: BASE_TS,
    explanation: "plan",
    plan: [{ title: "Inspect", status: "completed" }],
    ...overrides,
  };
}

function subagentItem(overrides: Partial<SubagentItem> = {}): SubagentItem {
  return {
    id: "subagent-1",
    type: "subagent",
    status: "completed",
    createdAt: BASE_TS,
    subagentId: "researcher-a",
    role: "researcher",
    name: "Researcher",
    codename: "Spark-abc",
    avatar: "R",
    parentItemId: null,
    summary: "checked options",
    error: null,
    iterationCount: 2,
    filesTouched: ["notes.md"],
    ...overrides,
  };
}

function verificationItem(
  overrides: Partial<VerificationItem> = {},
): VerificationItem {
  return {
    id: "verify-1",
    type: "verification",
    status: "completed",
    createdAt: BASE_TS,
    command: "pnpm test",
    kind: "test",
    exitCode: 0,
    summary: "passed",
    stdoutTail: "ok",
    stderrTail: null,
    relatedFiles: ["src/app.ts"],
    relatedChangeItemIds: ["file-1"],
    ...overrides,
  };
}

function artifactItem(overrides: Partial<ArtifactItem> = {}): ArtifactItem {
  return {
    id: "artifact-1",
    type: "artifact",
    status: "completed",
    createdAt: BASE_TS,
    artifactId: "artifact-report",
    kind: "pdf",
    path: "reports/out.pdf",
    mimeType: "application/pdf",
    title: "Report",
    version: 1,
    createdByItemId: null,
    previewUrl: "/preview/report",
    renderStatus: "rendered",
    validationStatus: "passed",
    ...overrides,
  };
}

describe("liveToolEventsFromConversation", () => {
  it("maps realtime tool items into LiveToolEvents", () => {
    const conv = makeConversation([
      makeTurn([commandItem(), mcpItem(), fileChangeItem(), todoItem()]),
    ]);

    const events = liveToolEventsFromConversation(conv);

    expect(events.map((event) => event.name)).toEqual([
      "list_cwd",
      "mcp:read",
      "file_change",
      "todo_write",
    ]);
    expect(events[0]).toMatchObject({
      id: "cmd-1",
      turnId: "turn-1",
      turnIndex: 0,
      status: "done",
      input: {
        path: ".",
        command: "list_cwd",
        tool: "list_cwd",
        cwd: "/repo",
        networkAccess: false,
      },
      output: "listed files",
    });
    expect(events[1]).toMatchObject({
      durationMs: 25,
      output: { ok: true },
    });
    expect(events[2]?.input).toMatchObject({
      changes: [{ path: "src/app.ts", op: "update" }],
      grantRoot: "/repo",
    });
    expect(events[3]?.input).toMatchObject({
      items: [{ content: "Inspect", status: "completed" }],
      explanation: "plan",
    });
  });

  it("stamps events with their owning turn instead of overloading iteration", () => {
    const conv = makeConversation([
      makeTurn([commandItem({ id: "turn-one-tool" })], "turn-one"),
      makeTurn([commandItem({ id: "turn-two-tool" })], "turn-two"),
    ]);

    const events = liveToolEventsFromConversation(conv);

    expect(
      events.map(({ id, turnId, turnIndex }) => ({ id, turnId, turnIndex })),
    ).toEqual([
      { id: "turn-one-tool", turnId: "turn-one", turnIndex: 0 },
      { id: "turn-two-tool", turnId: "turn-two", turnIndex: 1 },
    ]);
  });

  it("renders a user-redirected tool as a neutral finished event", () => {
    const events = liveToolEventsFromConversation(
      makeConversation([
        makeTurn([
          commandItem({
            status: "interrupted",
            aggregatedOutput: "cancelled after live steering",
          }),
        ]),
      ]),
    );

    expect(events[0]).toMatchObject({
      id: "cmd-1",
      status: "done",
      output: "cancelled after live steering",
    });
  });

  it("surfaces MCP progress without changing completed result shape when absent", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          progress: {
            label: "Reading workbook",
            status: "running",
            percent: 42,
            current: 21,
            total: 50,
            preview: { sheet: "Revenue" },
            updatedAt: BASE_TS,
          },
        }),
      ]),
    ]);

    const [event] = liveToolEventsFromConversation(conv);

    expect(event.input).toMatchObject({
      progress: {
        label: "Reading workbook",
        percent: 42,
        current: 21,
        total: 50,
      },
    });
    expect(event.output).toMatchObject({
      result: { ok: true },
      progress: {
        label: "Reading workbook",
        preview: { sheet: "Revenue" },
      },
    });
  });

  it("appends server-authored turn phases as the preferred todo_write event", () => {
    const turn: Turn = {
      ...makeTurn([commandItem()]),
      phases: [
        {
          id: "phase-1",
          index: 1,
          total: 2,
          title: "Phase 1: Inspect context",
          status: "done",
        },
        {
          id: "phase-2",
          index: 2,
          total: 2,
          title: "Phase 2: Patch reducer",
          status: "running",
          activeItemId: "cmd-1",
        },
      ],
      workspaceFocus: {
        itemId: "cmd-1",
        view: "terminal",
        title: "Running tests",
      },
      workbenchSnapshot: {
        schemaVersion: 2,
        version: 3,
        status: "running",
        phases: [
          {
            id: "phase-1",
            index: 1,
            total: 2,
            title: "Phase 1: Inspect context",
            status: "done",
          },
          {
            id: "phase-2",
            index: 2,
            total: 2,
            title: "Phase 2: Patch reducer",
            status: "running",
            activeItemId: "cmd-1",
          },
        ],
        currentPhaseId: "phase-2",
        currentItemId: "cmd-1",
        workspaceFocus: {
          itemId: "cmd-1",
          view: "terminal",
          title: "Running tests",
        },
        updatedAt: "2026-01-01T00:00:00.000Z",
      },
    };

    const events = liveToolEventsFromLastTurn(makeConversation([turn]));
    const phaseEvent = events.at(-1);

    expect(phaseEvent?.id).toBe("server-phases:turn-1");
    expect(phaseEvent?.name).toBe("todo_write");
    expect(phaseEvent?.status).toBe("running");
    expect(phaseEvent?.input).toMatchObject({
      source: "turn.phases",
      workbenchSnapshot: { version: 3 },
      workspaceFocus: { itemId: "cmd-1", view: "terminal" },
      items: [
        { content: "Phase 1: Inspect context", status: "completed" },
        {
          content: "Phase 2: Patch reducer",
          status: "in_progress",
          activeItemId: "cmd-1",
        },
      ],
    });
  });

  it("maps first-class subagent, verification, and artifact items into LiveToolEvents", () => {
    const conv = makeConversation([
      makeTurn([
        subagentItem({ parentItemId: "parent-call-1" }),
        verificationItem(),
        artifactItem(),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);

    expect(events.map((event) => event.name)).toEqual([
      "subagent",
      "verification:test",
      "artifact",
    ]);
    expect(events[0]).toMatchObject({
      agentId: "researcher-a",
      subAgentRole: "researcher",
      subagentCodename: "Spark-abc",
      parentToolUseId: "parent-call-1",
      iterationCount: 2,
      filesTouched: ["notes.md"],
    });
    expect(events[1]).toMatchObject({
      output: { exitCode: 0, summary: "passed" },
    });
    expect(events[2]).toMatchObject({
      input: {
        path: "reports/out.pdf",
        kind: "pdf",
        workspaceFocus: {
          itemId: "artifact-1",
          view: "artifact",
          title: "Report",
          subtitle: "reports/out.pdf",
          previewUrl: "/preview/report",
        },
      },
      output: { renderStatus: "rendered", validationStatus: "passed" },
    });
  });

  it("maps team swarm MCP items to the shared swarm event name", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "team-1",
          server: "team",
          tool: "team_swarm",
          arguments: {
            schema: "echo.group_fanout_run.v1",
            specs: [{ agent_id: "db-agent", task: "check schema" }],
          },
          result: {
            schema: "echo.group_fanout_result.v1",
            replies: [{ agent_id: "db-agent", ok: true, reply: "done" }],
          },
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);

    expect(events[0]).toMatchObject({
      id: "team-1",
      name: "team_swarm",
      input: {
        server: "team",
        tool: "team_swarm",
      },
      output: {
        schema: "echo.group_fanout_result.v1",
      },
    });
  });

  it("precomputes isReportLike at mapping time", () => {
    const conv = makeConversation([
      makeTurn([
        commandItem({ id: "plain" }),
        commandItem({
          id: "report-output",
          aggregatedOutput: "wrote reports/out.docx",
        }),
        mcpItem({ id: "report-tool", tool: "report-writing" }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);

    expect(events.map((event) => [event.id, event.isReportLike])).toEqual([
      ["plain", false],
      ["report-output", true],
      ["report-tool", true],
    ]);
  });

  it("preserves running and failed statuses", () => {
    const conv = makeConversation([
      makeTurn([
        commandItem({ id: "running", status: "inProgress" }),
        commandItem({
          id: "failed",
          status: "failed",
          aggregatedOutput: "boom",
        }),
        commandItem({ id: "declined", status: "declined" }),
      ]),
    ]);

    expect(
      liveToolEventsFromConversation(conv).map((event) => event.status),
    ).toEqual(["running", "error", "error"]);
  });

  it("returns only the latest turn for lastTurnToolEvents", () => {
    const conv = makeConversation([
      makeTurn([commandItem({ id: "old", command: "read_file" })], "turn-old"),
      makeTurn([commandItem({ id: "new", command: "web_search" })], "turn-new"),
    ]);

    expect(
      liveToolEventsFromConversation(conv).map((event) => event.id),
    ).toEqual(["old", "new"]);
    expect(liveToolEventsFromLastTurn(conv).map((event) => event.id)).toEqual([
      "new",
    ]);
  });

  it("surfaces pending approvals as waiting tool events", () => {
    const conv = makeConversationWithApproval([
      makeTurn([commandItem({ id: "old", command: "read_file" })]),
    ]);

    const allEvents = liveToolEventsFromConversation(conv);
    const lastTurnEvents = liveToolEventsFromLastTurn(conv);

    expect(allEvents.at(-1)).toMatchObject({
      id: "cmd-approval",
      name: "exec_shell",
      status: "waiting_approval",
      input: {
        requestId: 7,
        argsPreview: "rm -rf /tmp/demo",
      },
    });
    expect(lastTurnEvents.at(-1)?.status).toBe("waiting_approval");
  });

  it("translates a __subagent_spawned__ marker into a lifecycle=spawned event", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "spawn-1",
          status: "inProgress",
          tool: "__subagent_spawned__",
          server: "subagent",
          arguments: {
            agent_id: "researcher_a",
            role: "researcher",
            codename: "Spark-abc",
            avatar: "🔍",
            prompt_preview: "explore vendor X",
            parent_tool_use_id: "parent-call-1",
          },
          result: null,
          durationMs: null,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      id: "spawn-1",
      lifecycle: "spawned",
      status: "running",
      agentId: "researcher_a",
      subAgentRole: "researcher",
      subagentCodename: "Spark-abc",
      parentToolUseId: "parent-call-1",
      subagentAvatar: "🔍",
    });
  });

  it("recovers the requested lane id when custom siblings share one builtin role", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "spawn-custom-reader",
          status: "inProgress",
          tool: "__subagent_spawned__",
          server: "runtime",
          arguments: {
            agent_id: "explorer",
            role: "explorer",
            codename: "Spark-4f6",
            prompt_preview:
              "# Role: reader_readme\n\nYou are acting as reader_readme",
          },
          result: null,
          durationMs: null,
        }),
        mcpItem({
          id: "finish-custom-reader",
          tool: "__subagent_finished__",
          server: "runtime",
          arguments: {},
          result: {
            agent_id: "explorer",
            role: "explorer",
            codename: "Spark-4f6",
            ok: true,
          },
          durationMs: null,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events.map((event) => event.agentId)).toEqual([
      "reader_readme",
      "reader_readme",
    ]);
  });

  it("uses codename for legacy same-role child tools instead of collapsing siblings", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "legacy-child-tool",
          server: "subagent",
          tool: "read_file",
          arguments: {
            agent_id: "explorer",
            sub_agent_role: "explorer",
            subagent_codename: "Spark-4f6",
            input: { path: "README.md" },
          },
          result: { output_preview: "ok" },
        }),
      ]),
    ]);

    expect(liveToolEventsFromConversation(conv)[0]?.agentId).toBe("Spark-4f6");
  });

  it("attributes durable child tool steps to the matching agent lane", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "subagent-tool-search",
          server: "subagent",
          tool: "web_search",
          arguments: {
            agent_id: "researcher-a",
            sub_agent_role: "researcher",
            subagent_codename: "Spark-a1",
            subagent_avatar: "🔎",
            parent_tool_use_id: "orchestration-1",
            input: { query: "Echo Agent" },
          },
          result: { output_preview: "3 results", status: "success" },
          durationMs: 320,
        }),
      ]),
    ]);

    expect(liveToolEventsFromConversation(conv)[0]).toMatchObject({
      id: "subagent-tool-search",
      name: "web_search",
      status: "done",
      agentId: "researcher-a",
      subAgentRole: "researcher",
      subagentCodename: "Spark-a1",
      subagentAvatar: "🔎",
      parentToolUseId: "orchestration-1",
      durationMs: 320,
    });
  });

  it("maps streamed child prose to one public progress event", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "subagent-progress-a",
          status: "inProgress",
          server: "subagent",
          tool: "__subagent_progress__",
          arguments: {
            agent_id: "researcher-a",
            sub_agent_role: "researcher",
            subagent_codename: "Spark-a1",
            subagent_avatar: "🔎",
            parent_tool_use_id: "orchestration-1",
            round: 2,
          },
          progress: {
            label: "子智能体输出",
            status: "running",
            preview: "已查看两个来源，正在核对第三个来源",
            updatedAt: BASE_TS,
          },
          result: null,
          durationMs: null,
        }),
      ]),
    ]);

    expect(liveToolEventsFromConversation(conv)[0]).toMatchObject({
      id: "subagent-progress-a",
      name: "subagent_progress",
      status: "running",
      agentId: "researcher-a",
      subAgentRole: "researcher",
      subagentCodename: "Spark-a1",
      observation: "已查看两个来源，正在核对第三个来源",
    });
  });

  it("carries a failed sub-agent's reason onto the event", () => {
    // Regression: this mapper whitelists fields off `result` by name, and
    // `error` was not on the list. The backend had always sent a concrete
    // cause, but a failed lane reached the UI as `status: "error"` and
    // nothing else -- in thread teD7hPf9dkGOExwO0dIiBE the user asked
    // "why did it fail" three times because the screen could not say.
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "finish-fail",
          status: "completed",
          tool: "__subagent_finished__",
          server: "subagent",
          arguments: {},
          result: {
            codename: "Kite-1ae",
            avatar: "\u{1F50D}",
            role: "researcher",
            agent_id: "researcher",
            ok: false,
            duration_s: 132.75,
            iteration_count: 7,
            files_touched: [],
            error:
              "ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)",
          },
          durationMs: null,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events).toHaveLength(1);
    expect(events[0].status).toBe("error");
    expect(events[0].error).toContain("UNEXPECTED_EOF_WHILE_READING");
  });

  it("leaves error unset when a sub-agent succeeded", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "finish-ok",
          status: "completed",
          tool: "__subagent_finished__",
          server: "subagent",
          arguments: {},
          result: {
            codename: "Halo-59c",
            role: "researcher",
            agent_id: "researcher",
            ok: true,
            duration_s: 143.15,
            error: null,
          },
          durationMs: null,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events[0].status).toBe("done");
    expect(events[0].error).toBeUndefined();
  });

  it("translates a __subagent_finished__ marker into a lifecycle=finished event", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "finish-1",
          status: "completed",
          tool: "__subagent_finished__",
          server: "subagent",
          arguments: {},
          result: {
            agent_id: "researcher_a",
            role: "researcher",
            codename: "Spark-abc",
            avatar: "🔍",
            parent_tool_use_id: "parent-call-1",
            ok: true,
            duration_s: 1.5,
            iteration_count: 3,
            files_touched: ["a.py", "b.py"],
            output: "找到了三篇相关专利",
          },
          durationMs: 1500,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      id: "finish-1",
      lifecycle: "finished",
      status: "done",
      durationMs: 1500,
      agentId: "researcher_a",
      subAgentRole: "researcher",
      subagentCodename: "Spark-abc",
      subagentAvatar: "🔍",
      parentToolUseId: "parent-call-1",
      iterationCount: 3,
      filesTouched: ["a.py", "b.py"],
    });
    // The bridge now carries the answer text on result.output; it must be
    // surfaced as observation so the workbench can render a readable final
    // message instead of falling back to the raw result envelope.
    expect(events[0]?.observation).toBe("找到了三篇相关专利");
  });

  it("marks a __subagent_finished__ event as error when ok is false", () => {
    const conv = makeConversation([
      makeTurn([
        mcpItem({
          id: "finish-err",
          status: "completed",
          tool: "__subagent_finished__",
          server: "subagent",
          arguments: {},
          result: {
            agent_id: "researcher_a",
            role: "researcher",
            codename: "Spark-abc",
            avatar: "🔍",
            ok: false,
            duration_s: 0.5,
            iteration_count: 1,
            files_touched: [],
            error: "boom",
          },
          durationMs: 500,
        }),
      ]),
    ]);

    const events = liveToolEventsFromConversation(conv);
    expect(events[0]?.lifecycle).toBe("finished");
    expect(events[0]?.status).toBe("error");
  });
});

describe("useThreadStreamRealtime permissions", () => {
  function mockRealtime(startTurn = vi.fn().mockResolvedValue(undefined)) {
    vi.mocked(useRealtimeThread).mockReturnValue({
      state: makeConversation([]),
      connected: true,
      startTurn,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    });
    return startTurn;
  }

  it("keeps an empty resumed thread in its loading state until history arrives", () => {
    vi.mocked(useRealtimeThread).mockReturnValue({
      state: {
        ...makeConversation([]),
        resumeState: "resuming",
      },
      connected: true,
      startTurn: vi.fn().mockResolvedValue(undefined),
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    });

    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    expect(result.current[0].isThreadLoading).toBe(true);
  });

  it("stops showing the history skeleton after an empty resume settles", () => {
    mockRealtime();

    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    expect(result.current[0].isThreadLoading).toBe(false);
  });

  it("shows a new-turn human message immediately and reconciles it by stable item id", async () => {
    const startTurn = vi.fn(() => new Promise<void>(() => undefined));
    const steer = vi.fn().mockResolvedValue(undefined);
    let state: Conversation = makeConversation([]);
    vi.mocked(useRealtimeThread).mockImplementation(() => ({
      state,
      connected: true,
      startTurn,
      steer,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    }));
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "先显示，再发送",
        files: [],
      });
    });

    expect(result.current[0].messages).toEqual([
      expect.objectContaining({
        id: expect.stringMatching(/^itm_user_/),
        type: "human",
        content: "先显示，再发送",
        additional_kwargs: expect.objectContaining({
          delivery_state: "sending",
        }),
      }),
    ]);
    expect(result.current[0].isLoading).toBe(true);
    await waitFor(() => expect(startTurn).toHaveBeenCalledTimes(1));
    const clientItemId = startTurn.mock.calls[0]?.[0].clientItemId;
    expect(clientItemId).toBe(result.current[0].messages[0]?.id);
    expect(startTurn.mock.calls[0]?.[0].metadata).not.toHaveProperty(
      "client_message_id",
    );

    state = makeConversation([
      {
        ...makeTurn(
          [
            {
              id: clientItemId!,
              type: "userMessage",
              status: "completed",
              createdAt: BASE_TS,
              text: "先显示，再发送",
            },
          ],
          "turn-live",
        ),
        status: "inProgress",
        completedAt: null,
      },
    ]);
    rerender();

    await waitFor(() => {
      const humanMessages = result.current[0].messages.filter(
        (message) => message.type === "human",
      );
      expect(humanMessages).toHaveLength(1);
      expect(humanMessages[0]).toMatchObject({
        id: clientItemId,
        content: "先显示，再发送",
      });
      expect(humanMessages[0]?.additional_kwargs).not.toHaveProperty(
        "delivery_state",
      );
    });
  });

  it("marks optimistic delivery queued until websocket resume is authoritative", async () => {
    const startTurn = vi.fn(() => new Promise<void>(() => undefined));
    let connected = false;
    let state: Conversation = {
      ...makeConversation([]),
      resumeState: "resuming",
    };
    vi.mocked(useRealtimeThread).mockImplementation(() => ({
      state,
      connected,
      startTurn,
      steer: vi.fn().mockResolvedValue(undefined),
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    }));
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", { text: "离线也要看得见", files: [] });
    });
    expect(
      result.current[0].messages[0]?.additional_kwargs?.delivery_state,
    ).toBe("queued");
    expect(startTurn).not.toHaveBeenCalled();

    connected = true;
    state = makeConversation([]);
    rerender();
    await waitFor(() =>
      expect(
        result.current[0].messages[0]?.additional_kwargs?.delivery_state,
      ).toBe("sending"),
    );
    expect(startTurn).toHaveBeenCalledTimes(1);
  });

  it("keeps failed text retryable and reuses the same client item id", async () => {
    const startTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("socket dropped"))
      .mockImplementation(() => new Promise<void>(() => undefined));
    mockRealtime(startTurn);
    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", { text: "不要吞掉这句话", files: [] });
    });
    await waitFor(() =>
      expect(
        result.current[0].messages[0]?.additional_kwargs?.delivery_state,
      ).toBe("failed"),
    );
    const clientMessageId = result.current[0].messages[0]?.id;
    expect(result.current[0].messages[0]?.content).toBe("不要吞掉这句话");

    act(() => {
      window.dispatchEvent(
        new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
          detail: { threadId: "th-test", clientMessageId },
        }),
      );
    });
    await waitFor(() => expect(startTurn).toHaveBeenCalledTimes(2));
    expect(startTurn.mock.calls.map((call) => call[0].clientItemId)).toEqual([
      clientMessageId,
      clientMessageId,
    ]);
    expect(
      result.current[0].messages[0]?.additional_kwargs?.delivery_state,
    ).toBe("sending");
  });

  it("shows running-turn steering immediately and reconciles its server receipt", async () => {
    const originalUser = {
      id: "itm_user_original",
      type: "userMessage" as const,
      status: "completed" as const,
      createdAt: BASE_TS,
      text: "先检查核心流程",
    };
    let state: Conversation = makeConversation([
      {
        ...makeTurn([originalUser], "turn-live"),
        status: "inProgress",
        completedAt: null,
      },
    ]);
    const steer = vi.fn(() => new Promise<void>(() => undefined));
    vi.mocked(useRealtimeThread).mockImplementation(() => ({
      state,
      connected: true,
      startTurn: vi.fn().mockResolvedValue(undefined),
      steer,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    }));
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "顺便检查许可证",
        files: [],
      });
    });
    expect(
      result.current[0].messages.map((message) => message.content),
    ).toEqual(["先检查核心流程", "顺便检查许可证"]);
    await waitFor(() => expect(steer).toHaveBeenCalledTimes(1));
    const clientItemId = steer.mock.calls[0]?.[0].itemId;
    expect(steer).toHaveBeenCalledWith({
      input: "顺便检查许可证",
      itemId: clientItemId,
    });

    state = makeConversation([
      {
        ...makeTurn(
          [
            originalUser,
            {
              id: clientItemId!,
              type: "steeringUserMessage",
              status: "completed",
              createdAt: DONE_TS,
              text: "顺便检查许可证",
              targetTurnId: "turn-live",
              source: "user",
            },
          ],
          "turn-live",
        ),
        status: "inProgress",
        completedAt: null,
      },
    ]);
    rerender();

    await waitFor(() => {
      const corrections = result.current[0].messages.filter(
        (message) => message.content === "顺便检查许可证",
      );
      expect(corrections).toHaveLength(1);
      expect(corrections[0]?.id).toBe(clientItemId);
      expect(corrections[0]?.additional_kwargs).not.toHaveProperty(
        "delivery_state",
      );
    });
  });

  it("retries failed steering only against its original active turn", async () => {
    const state = makeConversation([
      {
        ...makeTurn(
          [
            {
              id: "itm_user_original",
              type: "userMessage",
              status: "completed",
              createdAt: BASE_TS,
              text: "继续原任务",
            },
          ],
          "turn-original",
        ),
        status: "inProgress",
        completedAt: null,
      },
    ]);
    const steer = vi
      .fn()
      .mockRejectedValueOnce(new Error("socket dropped"))
      .mockImplementation(() => new Promise<void>(() => undefined));
    vi.mocked(useRealtimeThread).mockReturnValue({
      state,
      connected: true,
      startTurn: vi.fn().mockResolvedValue(undefined),
      steer,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    });
    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", { text: "失败的纠偏", files: [] });
    });
    await waitFor(() =>
      expect(
        result.current[0].messages.find(
          (message) => message.content === "失败的纠偏",
        )?.additional_kwargs?.delivery_state,
      ).toBe("failed"),
    );
    const clientMessageId = steer.mock.calls[0]?.[0].itemId;

    act(() => {
      window.dispatchEvent(
        new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
          detail: { threadId: "th-test", clientMessageId },
        }),
      );
    });
    await waitFor(() => expect(steer).toHaveBeenCalledTimes(2));

    expect(steer.mock.calls.map((call) => call[0])).toEqual([
      { input: "失败的纠偏", itemId: clientMessageId },
      { input: "失败的纠偏", itemId: clientMessageId },
    ]);
    expect(
      result.current[0].messages.find(
        (message) => message.id === clientMessageId,
      )?.additional_kwargs?.delivery_state,
    ).toBe("sending");
  });

  it("does not open a second turn on rapid double-submit or early retry", async () => {
    const startTurn = vi.fn(() => new Promise<void>(() => undefined));
    mockRealtime(startTurn);
    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", { text: "第一条", files: [] });
      result.current[1]("th-test", { text: "第二条", files: [] });
    });
    await waitFor(() => expect(startTurn).toHaveBeenCalledTimes(1));
    expect(
      result.current[0].messages.map((message) => ({
        content: message.content,
        state: message.additional_kwargs?.delivery_state,
      })),
    ).toEqual([
      { content: "第一条", state: "sending" },
      { content: "第二条", state: "failed" },
    ]);
    expect(result.current[0].error?.message).toBe(
      "The previous message is still sending. Wait for confirmation, then retry.",
    );

    const secondClientMessageId = result.current[0].messages[1]?.id;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
          detail: {
            threadId: "th-test",
            clientMessageId: secondClientMessageId,
          },
        }),
      );
    });
    await Promise.resolve();
    expect(startTurn).toHaveBeenCalledTimes(1);
  });

  it("does not turn a failed new-turn retry into steering for a newer active turn", async () => {
    const startTurn = vi
      .fn()
      .mockRejectedValueOnce(new Error("socket dropped"))
      .mockImplementation(() => new Promise<void>(() => undefined));
    const steer = vi.fn().mockResolvedValue(undefined);
    let state: Conversation = makeConversation([]);
    vi.mocked(useRealtimeThread).mockImplementation(() => ({
      state,
      connected: true,
      startTurn,
      steer,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    }));
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", { text: "失败的 A", files: [] });
    });
    await waitFor(() =>
      expect(
        result.current[0].messages.find(
          (message) => message.content === "失败的 A",
        )?.additional_kwargs?.delivery_state,
      ).toBe("failed"),
    );
    const failedAId = result.current[0].messages.find(
      (message) => message.content === "失败的 A",
    )?.id;

    act(() => {
      result.current[1]("th-test", { text: "已发送的 B", files: [] });
    });
    await waitFor(() => expect(startTurn).toHaveBeenCalledTimes(2));
    const sentBId = startTurn.mock.calls[1]?.[0].clientItemId;
    state = makeConversation([
      {
        ...makeTurn(
          [
            {
              id: sentBId!,
              type: "userMessage",
              status: "completed",
              createdAt: DONE_TS,
              text: "已发送的 B",
            },
          ],
          "turn-b",
        ),
        status: "inProgress",
        completedAt: null,
      },
    ]);
    rerender();
    await waitFor(() =>
      expect(
        result.current[0].messages.filter(
          (message) => message.content === "已发送的 B",
        ),
      ).toHaveLength(1),
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent(RETRY_PENDING_MESSAGE_EVENT, {
          detail: {
            threadId: "th-test",
            clientMessageId: failedAId,
          },
        }),
      );
    });
    await Promise.resolve();

    expect(startTurn).toHaveBeenCalledTimes(2);
    expect(steer).not.toHaveBeenCalled();
    expect(
      result.current[0].messages.find((message) => message.id === failedAId)
        ?.additional_kwargs?.delivery_state,
    ).toBe("failed");
    expect(result.current[0].error?.message).toBe(
      "The previous message is still sending. Wait for confirmation, then retry.",
    );
  });

  it("reports the server-created thread id when starting from the new-thread route", async () => {
    const onStart = vi.fn();
    const liveTurn: Turn = {
      ...makeTurn([], "turn-live"),
      threadId: "server-thread-1",
      status: "inProgress",
      completedAt: null,
    };
    vi.mocked(useRealtimeThread).mockReturnValue({
      state: makeConversation([liveTurn]),
      connected: true,
      startTurn: vi.fn().mockResolvedValue(undefined),
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    });

    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "new",
        onStart,
      }),
    );

    expect(result.current[0].threadId).toBe("server-thread-1");
    await waitFor(() =>
      expect(onStart).toHaveBeenCalledWith("server-thread-1"),
    );
    expect(onStart).not.toHaveBeenCalledWith("new");
  });

  it("sends default sandbox permissions by default", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "hello", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    expect(payload).toEqual(
      expect.objectContaining({
        approvalPolicy: "on-request",
        sandboxPolicy: {
          type: "workspaceWrite",
          // Default permission mode keeps network denied unless the user
          // opts in from the sandbox settings page.
          networkAccess: false,
        },
        metadata: {
          context: expect.objectContaining({
            permission_mode: "default",
            sandbox_mode: "sandbox",
            execution_environment: "sandbox",
          }),
        },
      }),
    );
    expect(payload).not.toHaveProperty("planningMode");
  });

  it("allows attachment-only turns so pasted screenshots still reach the model", async () => {
    const startTurn = mockRealtime();
    const file = new File(["img"], "screen.png", { type: "image/png" });
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "new",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("new", {
        text: "",
        files: [
          {
            type: "file",
            mediaType: "image/png",
            filename: "screen.png",
            url: "data:image/png;base64,AAAA",
            file,
          },
        ],
      });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        input: "",
        attachments: [
          expect.objectContaining({
            filename: "screen.png",
            mediaType: "image/png",
            data_url: expect.stringMatching(/^data:image\/png;base64,/),
          }),
        ],
      }),
    );
  });

  it("passes uploaded document path and extracted preview to the turn", async () => {
    const startTurn = mockRealtime();
    const file = new File(["deck body"], "deck.pptx", {
      type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          success: true,
          files: [
            {
              filename: "deck.pptx",
              size: file.size,
              path: "/managed/th-test/upload/deck.pptx",
              virtual_path: "upload/deck.pptx",
              artifact_url: "/api/threads/th-test/artifacts/deck.pptx",
              extension: "pptx",
              modified: 1,
              extracted_text: "[Slide 1]\nQuarterly review",
            },
          ],
        }),
      }),
    );
    const { result } = renderHook(() =>
      useThreadStreamRealtime({ threadId: "th-test" }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "summarize",
        files: [
          {
            type: "file",
            mediaType: file.type,
            filename: file.name,
            url: "blob:deck",
            file,
          },
        ],
      });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn.mock.calls[0]?.[0].attachments).toEqual([
      expect.objectContaining({
        filename: "deck.pptx",
        path: "/managed/th-test/upload/deck.pptx",
        extracted_text: "[Slide 1]\nQuarterly review",
      }),
    ]);
  });

  it("dispatches failed image attachments back to the composer on send error", async () => {
    const startTurn = vi.fn().mockRejectedValue(new Error("socket dropped"));
    mockRealtime(startTurn);
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    const file = new File(["img"], "screen.png", { type: "image/png" });
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "",
        files: [
          {
            type: "file",
            mediaType: "image/png",
            filename: "screen.png",
            url: "data:image/png;base64,AAAA",
            file,
          },
        ],
      });
    });

    await waitFor(() =>
      expect(dispatchSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "echo:send-failed",
          detail: expect.objectContaining({
            threadId: "th-test",
            text: "",
            images: [expect.objectContaining({ name: "screen.png" })],
          }),
        }),
      ),
    );
    dispatchSpy.mockRestore();
  });

  it("restores the original Codex marker text when a send fails", async () => {
    const startTurn = vi.fn().mockRejectedValue(new Error("socket dropped"));
    mockRealtime(startTurn);
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "/codex goal\nFinish the hardening pass",
        files: [],
      });
    });

    await waitFor(() =>
      expect(dispatchSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "echo:send-failed",
          detail: expect.objectContaining({
            threadId: "th-test",
            text: "/codex goal\nFinish the hardening pass",
          }),
        }),
      ),
    );
    dispatchSpy.mockRestore();
  });

  it("marks first-screen realtime turns as coding-agent turns", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "fix the tests", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        metadata: {
          context: expect.objectContaining({
            mode: "code",
            capability_mode: "code",
            code_mode: "solo",
            permission_mode: "default",
            sandbox_mode: "sandbox",
            execution_environment: "sandbox",
          }),
        },
      }),
    );
  });

  it("does not add code capability defaults to explicit chat/react turns", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: {
          mode: "react",
          permission_mode: "default",
          personal_mode: "research",
        },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "summarize this topic", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    const context = (payload?.metadata as { context?: Record<string, unknown> })
      ?.context;
    expect(context).toMatchObject({
      mode: "react",
      personal_mode: "research",
      permission_mode: "default",
      sandbox_mode: "sandbox",
      execution_environment: "sandbox",
    });
    expect(context).not.toHaveProperty("capability_mode");
    expect(context).not.toHaveProperty("code_mode");
  });

  it("keeps team turns in team mode without stealth code capability defaults", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "team-thread",
        context: {
          mode: "team",
          permission_mode: "default",
          team_mode: "chat",
          workspace_path: "/repo",
        },
      }),
    );

    act(() => {
      result.current[1]("team-thread", { text: "ask the group", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    const context = (payload?.metadata as { context?: Record<string, unknown> })
      ?.context;
    expect(context).toMatchObject({
      mode: "team",
      team_mode: "chat",
      workspace_path: "/repo",
      permission_mode: "default",
    });
    expect(context).not.toHaveProperty("capability_mode");
    expect(context).not.toHaveProperty("code_mode");
  });

  it("passes collaborator cluster topology while staying on the current thread", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "chat-thread",
        context: {
          mode: "team",
          permission_mode: "default",
          team_mode: "cowork",
          serve_mesh: "0",
          topology_id: "cowork",
          agent_roster: [
            { agent_id: "general", display_name: "General", role: "tl" },
            { agent_id: "coder", display_name: "Coder", role: "member" },
          ],
        },
      }),
    );

    act(() => {
      result.current[1]("chat-thread", {
        text: "继续这个任务",
        files: [],
      });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    const context = (payload?.metadata as { context?: Record<string, unknown> })
      ?.context;
    expect(payload).toEqual(
      expect.objectContaining({
        topologyId: "cowork",
      }),
    );
    expect(context).toMatchObject({
      mode: "team",
      team_mode: "cowork",
      serve_mesh: "0",
      topology_id: "cowork",
      permission_mode: "default",
    });
    expect(context?.agent_roster).toHaveLength(2);
    expect(context).not.toHaveProperty("capability_mode");
    expect(context).not.toHaveProperty("code_mode");
  });

  it("adds code capability defaults to explicit project code turns", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "code-thread",
        context: {
          mode: "code",
          permission_mode: "default",
          workspace_path: "/repo",
        },
      }),
    );

    act(() => {
      result.current[1]("code-thread", { text: "fix the bug", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        cwd: "/repo",
        metadata: {
          context: expect.objectContaining({
            mode: "code",
            workspace_path: "/repo",
            capability_mode: "code",
            code_mode: "solo",
          }),
        },
      }),
    );
  });

  it("turns composer Codex Plan marker into runtime metadata", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "/codex plan\nRefactor the router",
        files: [],
      });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        input: "Refactor the router",
        planningMode: true,
        metadata: {
          context: expect.objectContaining({
            workflow_mode: "plan",
            completion_policy: "plan",
            mode_preset: "plan.mode",
            workflow_preset: "plan.mode",
          }),
        },
      }),
    );
  });

  it("turns composer Codex Goal marker into bounded goal metadata", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", {
        text: "/codex goal\nFinish the hardening pass",
        files: [],
      });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    const context = (payload?.metadata as { context?: Record<string, unknown> })
      ?.context;
    expect(payload).toEqual(
      expect.objectContaining({
        input: "Finish the hardening pass",
      }),
    );
    expect(payload).not.toHaveProperty("planningMode");
    expect(context).toMatchObject({
      workflow_mode: "goal",
      completion_policy: "goal",
      goal_mode: true,
      mode_preset: "goal.mode",
      workflow_preset: "goal.mode",
    });
  });

  it("sends selected reasoning effort as top-level turn effort", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: {
          permission_mode: "default",
          reasoning_effort: "xhigh",
        },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "solve the hard bit", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        effort: "xhigh",
        metadata: {
          context: expect.objectContaining({
            reasoning_effort: "xhigh",
          }),
        },
      }),
    );
  });

  it("keeps reasoning off in provider metadata but omits invalid turn effort", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: {
          permission_mode: "default",
          reasoning_effort: "off",
        },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "answer directly", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    expect(payload).not.toHaveProperty("effort");
    expect(payload?.metadata).toEqual({
      context: expect.objectContaining({ reasoning_effort: "off" }),
    });
  });

  it("maps legacy full access to bypassPermissions", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "full" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "hello", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    const payload = startTurn.mock.calls[0]?.[0];
    expect(payload).toEqual(
      expect.objectContaining({
        approvalPolicy: "never",
        sandboxPolicy: {
          type: "dangerFullAccess",
          networkAccess: true,
        },
        metadata: {
          context: expect.objectContaining({
            permission_mode: "bypassPermissions",
            sandbox_mode: "full",
            execution_environment: "local",
          }),
        },
      }),
    );
    expect(payload).not.toHaveProperty("planningMode");
  });

  it("wraps send failures in an Error so BaseStream.error keeps its type", async () => {
    const startTurn = vi
      .fn()
      .mockRejectedValue(new Error("websocket closed (1006)"));
    mockRealtime(startTurn);
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "hello", files: [] });
    });

    await waitFor(() => expect(result.current[0].error).toBeInstanceOf(Error));
    // message-list keys its network styling off `.message`; wrapping
    // must keep the original text intact.
    expect(result.current[0].error?.message).toBe("websocket closed (1006)");
  });

  it("clears a local send failure after reconnect and successful resume", async () => {
    const startTurn = vi
      .fn()
      .mockRejectedValue(new Error("websocket closed (1006)"));
    const realtime = {
      state: makeConversation([]),
      connected: true,
      startTurn,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    };
    vi.mocked(useRealtimeThread).mockImplementation(() => realtime);
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "hello", files: [] });
    });
    await waitFor(() =>
      expect(result.current[0].error?.message).toBe("websocket closed (1006)"),
    );

    realtime.connected = false;
    realtime.state = {
      ...makeConversation([]),
      resumeState: "needsResume",
    };
    rerender();
    expect(result.current[0].error?.message).toBe("websocket closed (1006)");

    realtime.connected = true;
    realtime.state = {
      ...makeConversation([]),
      resumeState: "resuming",
    };
    rerender();
    expect(result.current[0].error?.message).toBe("websocket closed (1006)");

    realtime.state = makeConversation([]);
    rerender();
    await waitFor(() => expect(result.current[0].error).toBeUndefined());
  });

  it("keeps a local send failure when reconnect cannot resume the thread", async () => {
    const startTurn = vi
      .fn()
      .mockRejectedValue(new Error("unknown thread th-test"));
    const realtime = {
      state: makeConversation([]),
      connected: true,
      startTurn,
      resolveApproval: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
      interrupt: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue({ compacted: false }),
      decideHunk: vi.fn().mockResolvedValue(undefined),
    };
    vi.mocked(useRealtimeThread).mockImplementation(() => realtime);
    const { result, rerender } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "default" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "hello", files: [] });
    });
    await waitFor(() =>
      expect(result.current[0].error?.message).toBe("unknown thread th-test"),
    );

    realtime.connected = false;
    realtime.state = {
      ...makeConversation([]),
      resumeState: "needsResume",
    };
    rerender();
    realtime.connected = true;
    rerender();

    expect(result.current[0].error?.message).toBe("unknown thread th-test");
  });

  it("sends plan permission mode as planningMode without auto approval", async () => {
    const startTurn = mockRealtime();
    const { result } = renderHook(() =>
      useThreadStreamRealtime({
        threadId: "th-test",
        context: { permission_mode: "plan" },
      }),
    );

    act(() => {
      result.current[1]("th-test", { text: "make a plan first", files: [] });
    });

    await waitFor(() => expect(startTurn).toHaveBeenCalled());
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        approvalPolicy: "on-request",
        sandboxPolicy: {
          type: "workspaceWrite",
          // Plan mode defaults to network denied too.
          networkAccess: false,
        },
        planningMode: true,
        metadata: {
          context: expect.objectContaining({
            permission_mode: "plan",
            sandbox_mode: "sandbox",
            execution_environment: "sandbox",
          }),
        },
      }),
    );
  });
});

// ── attach-time uploads are not re-posted at send ─
//
// The composer now uploads the moment a file lands in it. Sending used to
// upload unconditionally, which would push the same bytes twice and mint a
// second artifact for one picture.
describe("uploadPromptInputFiles", () => {
  const file = new File(["img"], "shot.png", { type: "image/png" });
  const uploaded = {
    filename: "shot.png",
    size: file.size,
    path: "/artifacts/shot.png",
    virtual_path: "uploads/shot.png",
    artifact_url: "https://example.test/shot.png",
    content_type: "image/png",
  };

  it("skips the network when every part carries server-side info", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const attachments = await uploadPromptInputFiles("thread-1", [
      {
        type: "file",
        mediaType: "image/png",
        filename: "shot.png",
        url: "data:image/png;base64,aW1n",
        file,
        uploaded,
      },
    ]);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(attachments[0]).toMatchObject({
      filename: "shot.png",
      artifact_url: "https://example.test/shot.png",
    });
  });

  it("still uploads when a part has no attach-time info", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, files: [uploaded] }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    await uploadPromptInputFiles("thread-1", [
      {
        type: "file",
        mediaType: "image/png",
        filename: "shot.png",
        url: "data:image/png;base64,aW1n",
        file,
      },
    ]);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
