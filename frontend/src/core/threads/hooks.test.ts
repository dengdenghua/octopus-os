import { describe, expect, test } from "vitest";

import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";

import { isSubtaskActive, isSubtaskTerminal } from "../tasks/types";
import {
  finalizeLiveToolEvents,
  finalizeTurnHistory,
  normalizeCustomToolEvent,
  upsertLiveToolEvent,
} from "./hooks";

const _SUBTASK_UPDATED_IDS: string[] = [];

describe("LiveToolEvent normalization", () => {
  test("preserves tool input when a tool_end event omits it", () => {
    const started: LiveToolEvent = {
      id: "todo-1",
      name: "todo_write",
      status: "running",
      startedAt: 100,
      iteration: 1,
      input: {
        todos: [{ text: "Confirm task", status: "in_progress" }],
      },
    };

    const events = upsertLiveToolEvent([started], {
      id: "todo-1",
      name: "todo_write",
      status: "done",
      startedAt: 100,
      finishedAt: 140,
      durationMs: 40,
      iteration: 1,
    });

    expect(events[0]?.input).toEqual(started.input);
    expect(events[0]?.status).toBe("done");
    expect(events[0]?.durationMs).toBe(40);
  });

  test("accepts agentic fallback tool event aliases", () => {
    const started = normalizeCustomToolEvent({
      type: "tool_start",
      id: "call-1",
      name: "web_search",
      input: { query: "AI research updates" },
      iteration: "2",
      agentName: "Researcher",
    });

    expect(started).toMatchObject({
      id: "call-1",
      name: "web_search",
      status: "running",
      iteration: 2,
      agentName: "Researcher",
      input: { query: "AI research updates" },
    });
  });

  test("maps rejected or failed terminal events to error", () => {
    const ended = normalizeCustomToolEvent({
      type: "tool_end",
      tool_call_id: "call-2",
      tool_name: "exec_shell",
      status: "rejected",
      output_preview: "User denied tool execution",
    });

    expect(ended?.status).toBe("error");
    expect(ended?.output).toBe("User denied tool execution");
  });

  test("finalizes running events so stopped runs can keep collapsed history", () => {
    const events: LiveToolEvent[] = [
      {
        id: "reasoning-1",
        name: "model_reasoning",
        status: "running",
        startedAt: Date.now() - 1000,
        iteration: 0,
        output: { content: "Inspecting the request" },
      },
      {
        id: "todo-1",
        name: "todo_write",
        status: "done",
        startedAt: Date.now() - 900,
        finishedAt: Date.now() - 800,
        iteration: 1,
      },
    ];

    const finalized = finalizeLiveToolEvents(events, "error", {
      reason: "stopped_by_user",
    });

    expect(finalized[0]?.status).toBe("error");
    expect(finalized[0]?.finishedAt).toBeTypeOf("number");
    expect(finalized[0]?.durationMs).toBeGreaterThanOrEqual(0);
    expect(finalized[0]?.output).toEqual({ content: "Inspecting the request" });
    expect(finalized[1]?.status).toBe("done");
  });

  test("adds lifecycle events to saved stopped history", () => {
    const finalized = finalizeTurnHistory([], Date.now() - 2000, "error", {
      reason: "stopped_by_user",
    });

    expect(finalized.map((event) => event.name)).toEqual([
      "turn_request",
      "stream_connection",
      "model_gateway",
    ]);
    expect(finalized[2]?.status).toBe("error");
    expect(finalized[2]?.output).toEqual({ reason: "stopped_by_user" });
  });

  test("does not duplicate lifecycle rows when real events share the same names", () => {
    const finalized = finalizeTurnHistory(
      [
        {
          id: "provider-gateway-1",
          name: "model_gateway",
          status: "running",
          startedAt: Date.now() - 1000,
          iteration: 0,
        },
      ],
      Date.now() - 2000,
      "done",
      { reason: "stream_finished" },
    );

    expect(
      finalized.filter((event) => event.name === "model_gateway"),
    ).toHaveLength(1);
  });

  test("preserves explicit todo states when the turn finishes", () => {
    const finalized = finalizeTurnHistory(
      [
        {
          id: "todo-old",
          name: "todo_write",
          status: "done",
          startedAt: Date.now() - 1200,
          iteration: 1,
          input: {
            todos: [{ text: "Old step", status: "in_progress" }],
          },
        },
        {
          id: "todo-latest",
          name: "todo_write",
          status: "done",
          startedAt: Date.now() - 800,
          iteration: 1,
          input: {
            todos: [
              { text: "Done step", status: "completed" },
              { text: "Final step", status: "in_progress" },
              { text: "Future step", status: "pending" },
            ],
          },
        },
      ],
      Date.now() - 2000,
      "done",
      { reason: "stream_finished" },
    );

    const oldTodo = finalized.find((event) => event.id === "todo-old");
    const latestTodo = finalized.find((event) => event.id === "todo-latest");

    expect(oldTodo?.input?.todos).toEqual([
      { text: "Old step", status: "in_progress" },
    ]);
    expect(latestTodo?.input?.todos).toEqual([
      { text: "Done step", status: "completed" },
      { text: "Final step", status: "in_progress" },
      { text: "Future step", status: "pending" },
    ]);
  });

  test("keeps an active todo visible when the turn fails", () => {
    const finalized = finalizeTurnHistory(
      [
        {
          id: "todo-latest",
          name: "todo_write",
          status: "done",
          startedAt: Date.now() - 800,
          iteration: 1,
          input: {
            todos: [{ text: "Retry step", status: "in_progress" }],
          },
        },
      ],
      Date.now() - 2000,
      "error",
      { reason: "network_error" },
    );

    const latestTodo = finalized.find((event) => event.id === "todo-latest");

    expect(latestTodo?.input?.todos).toEqual([
      { text: "Retry step", status: "in_progress" },
    ]);
  });
});

describe("onCustomEvent: task_started", () => {
  test("sets status to reasoning, progress to 0, and visual attributes", () => {
    const e = {
      type: "task_started",
      task_id: "task-1",
      subagent_type: "coder",
      description: "Analyze code",
      agent_name: "Code Expert",
      agent_id: "task-1",
      subgraph: "task-1",
    };

    const hue = (e.task_id.charCodeAt(0) * 47 + 28) % 360;
    const emojiMap: Record<string, string> = {
      coder: "💻",
      writer: "✍️",
      researcher: "🔍",
      reviewer: "📋",
      general: "🤖",
    };

    const update = {
      id: e.task_id,
      status: "reasoning",
      progress: 0,
      description: e.description ?? "",
      name: e.agent_name ?? e.subagent_type ?? e.task_id,
      role: e.subagent_type ?? e.agent_name,
      avatarEmoji: emojiMap[e.subagent_type ?? ""] ?? "🤖",
      hue,
    };

    expect(update.status).toBe("reasoning");
    expect(update.progress).toBe(0);
    expect(update.name).toBe("Code Expert");
    expect(update.role).toBe("coder");
    expect(update.avatarEmoji).toBe("💻");
    expect(typeof update.hue).toBe("number");
    expect(isSubtaskActive(update.status)).toBeTruthy();
  });
});

describe("onCustomEvent: task_running with tool_calls", () => {
  test("generates LiveToolEvents from tool_calls", () => {
    const e = {
      type: "task_running",
      task_id: "task-2",
      subagent_type: "researcher",
      message: {
        id: "msg-1",
        type: "ai",
        content: "thinking...",
        tool_calls: [
          { id: "tc-1", name: "read_file", args: {} },
          { id: "tc-2", name: "bash", args: {} },
        ],
      },
      message_index: 2,
      total_messages: 5,
    };

    const newEvents = e.message.tool_calls
      .filter((tc: { id?: string }) => tc.id)
      .map((tc: { id: string; name: string }) => ({
        id: tc.id,
        name: tc.name,
        status: "done" as const,
        startedAt: Date.now(),
        iteration: e.message_index ?? 0,
        agentId: e.task_id,
        agentName: e.subagent_type ?? e.task_id,
      }));

    expect(newEvents.length).toBe(2);
    expect(newEvents[0].name).toBe("read_file");
    expect(newEvents[0].agentId).toBe("task-2");
    expect(newEvents[1].name).toBe("bash");
    expect(newEvents[1].agentName).toBe("researcher");
  });

  test("estimates progress from message_index/total_messages", () => {
    const e = {
      type: "task_running",
      task_id: "task-3",
      message_index: 3,
      total_messages: 10,
    };

    const progress = Math.min(
      (e.message_index ?? e.total_messages) / e.total_messages,
      0.95,
    );

    expect(progress).toBe(0.3);
  });

  test("progress caps at 0.95", () => {
    const e = {
      type: "task_running",
      task_id: "task-4",
      message_index: 99,
      total_messages: 100,
    };

    const progress = Math.min(
      (e.message_index ?? e.total_messages) / e.total_messages,
      0.95,
    );

    expect(progress).toBe(0.95);
  });
});

describe("onCustomEvent: task_completed", () => {
  test("sets status to completed and progress to 1", () => {
    const e = {
      type: "task_completed",
      task_id: "task-5",
      result: "Analysis complete",
    };

    const update = {
      id: e.task_id,
      status: "completed",
      progress: 1,
      result: e.result ?? "",
    };

    expect(update.status).toBe("completed");
    expect(update.progress).toBe(1);
    expect(isSubtaskTerminal(update.status)).toBeTruthy();
  });
});

describe("onCustomEvent: task_failed/cancelled/timed_out", () => {
  test("sets status to failed and progress to 1", () => {
    for (const eventType of [
      "task_failed",
      "task_cancelled",
      "task_timed_out",
    ]) {
      const e = {
        type: eventType,
        task_id: "task-6",
        error: "Something went wrong",
      };

      const update = {
        id: e.task_id,
        status: "failed",
        progress: 1,
        error: e.error ?? e.type.replace("task_", ""),
      };

      expect(update.status).toBe("failed");
      expect(update.progress).toBe(1);
      expect(isSubtaskTerminal(update.status)).toBeTruthy();
    }
  });
});
