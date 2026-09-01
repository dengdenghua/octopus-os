import { describe, expect, test } from "vitest";

import type { MessageGroup } from "@/core/messages/utils";
import type { LiveToolEvent } from "../live-tool-timeline";
import { failureKind, isLatestMessageGroup } from "./message-list";
import {
  shouldOpenProcessTraceByDefault,
  shouldShowProcessTrace,
} from "./process-trace-visibility";

function toolEvent(
  name: string,
  overrides: Partial<LiveToolEvent> = {},
): LiveToolEvent {
  return {
    id: `${name}-${overrides.status ?? "done"}`,
    name,
    status: "done",
    startedAt: 1,
    iteration: 1,
    ...overrides,
  };
}

describe("message-list: assistant:subagent routing", () => {
  test("single task renders SubtaskCard (not grid)", () => {
    const toolCalls = [
      { id: "tc-1", name: "task", args: { description: "Analyze" } },
    ];
    const taskIds = toolCalls
      .filter((tc) => tc.name === "task")
      .map((tc) => tc.id);
    const validTaskIds = taskIds.filter((id): id is string => !!id);

    expect(validTaskIds.length).toBe(1);
    expect(validTaskIds.length > 1).toBe(false);
  });

  test("multiple tasks trigger ParallelSubtasksGrid", () => {
    const toolCalls = [
      { id: "tc-1", name: "task", args: { description: "Analyze" } },
      { id: "tc-2", name: "task", args: { description: "Research" } },
      { id: "tc-3", name: "task", args: { description: "Write" } },
    ];
    const taskIds = toolCalls
      .filter((tc) => tc.name === "task")
      .map((tc) => tc.id);
    const validTaskIds = taskIds.filter((id): id is string => !!id);

    expect(validTaskIds.length).toBe(3);
    expect(validTaskIds.length > 1).toBe(true);
  });

  test("non-task tool calls are excluded", () => {
    const toolCalls = [
      { id: "tc-1", name: "task", args: { description: "Analyze" } },
      { id: "tc-2", name: "read_file", args: { path: "/foo" } },
      { id: "tc-3", name: "bash", args: { command: "ls" } },
    ];
    const taskIds = toolCalls
      .filter((tc) => tc.name === "task")
      .map((tc) => tc.id);

    expect(taskIds.length).toBe(1);
  });

  test("tool calls without id are filtered out", () => {
    const toolCalls = [
      { id: "tc-1", name: "task", args: { description: "Analyze" } },
      { id: undefined, name: "task", args: { description: "No ID" } },
    ];
    const taskIds = toolCalls
      .filter((tc) => tc.name === "task")
      .map((tc) => tc.id);
    const validTaskIds = taskIds.filter((id): id is string => !!id);

    expect(validTaskIds.length).toBe(1);
    expect(validTaskIds[0]).toBe("tc-1");
  });

  test("empty tool calls results in no tasks", () => {
    const toolCalls: { id?: string; name: string }[] = [];
    const taskIds = toolCalls
      .filter((tc) => tc.name === "task")
      .map((tc) => tc.id);
    const validTaskIds = taskIds.filter((id): id is string => !!id);

    expect(validTaskIds.length).toBe(0);
  });
});

describe("message-list: process trace visibility", () => {
  test("hides model-only execution traces after simple answers", () => {
    const events = [
      toolEvent("model_gateway"),
      toolEvent("model_reasoning"),
      toolEvent("response_stream"),
    ];

    expect(shouldShowProcessTrace(events, true)).toBe(false);
    expect(shouldOpenProcessTraceByDefault(events, true)).toBe(false);
  });

  test("keeps completed meta-only trace visible but collapsed after an answer", () => {
    const events = [toolEvent("todo_write"), toolEvent("team_routing")];

    expect(shouldShowProcessTrace(events, true)).toBe(true);
    expect(shouldOpenProcessTraceByDefault(events, true)).toBe(false);
  });

  test("hides completed process traces after plain chat answers", () => {
    const events = [toolEvent("todo_write"), toolEvent("read_file")];

    expect(shouldShowProcessTrace(events, true, "chat")).toBe(false);
    expect(shouldOpenProcessTraceByDefault(events, true, "chat")).toBe(false);
  });

  test("keeps active or failed plain-chat traces visible before an answer", () => {
    expect(
      shouldShowProcessTrace(
        [toolEvent("read_file", { status: "running" })],
        false,
        "chat",
      ),
    ).toBe(true);
    expect(
      shouldOpenProcessTraceByDefault(
        [toolEvent("read_file", { status: "running" })],
        false,
        "chat",
      ),
    ).toBe(true);
    expect(
      shouldShowProcessTrace(
        [toolEvent("read_file", { status: "error" })],
        false,
        "chat",
      ),
    ).toBe(true);
  });

  test("shows real completed tool work but keeps it collapsed after an answer", () => {
    const events = [toolEvent("read_file")];

    expect(shouldShowProcessTrace(events, true)).toBe(true);
    expect(shouldOpenProcessTraceByDefault(events, true)).toBe(false);
  });

  test("shows active or failed meta events", () => {
    expect(
      shouldShowProcessTrace(
        [toolEvent("todo_write", { status: "running" })],
        true,
      ),
    ).toBe(true);
    expect(
      shouldOpenProcessTraceByDefault(
        [toolEvent("todo_write", { status: "running" })],
        true,
      ),
    ).toBe(true);
    expect(
      shouldShowProcessTrace(
        [toolEvent("todo_write", { status: "error" })],
        true,
      ),
    ).toBe(true);
    expect(
      shouldOpenProcessTraceByDefault(
        [toolEvent("todo_write", { status: "error" })],
        true,
      ),
    ).toBe(false);
    expect(
      shouldOpenProcessTraceByDefault(
        [toolEvent("todo_write", { status: "error" })],
        false,
      ),
    ).toBe(true);
  });
});

describe("message-list: Subtask creation from tool_call", () => {
  test("creates Subtask with correct defaults", () => {
    const toolCall = {
      id: "tc-100",
      name: "task",
      args: {
        subagent_type: "coder",
        description: "Fix the bug",
        prompt: "Fix the login bug",
      },
    };

    const task = {
      id: toolCall.id,
      subagent_type: toolCall.args.subagent_type as string,
      description: toolCall.args.description as string,
      prompt: toolCall.args.prompt as string,
      status: "in_progress" as const,
      progress: 0,
    };

    expect(task.id).toBe("tc-100");
    expect(task.status).toBe("in_progress");
    expect(task.progress).toBe(0);
    expect(task.subagent_type).toBe("coder");
  });

  test("completed result sets progress to 1", () => {
    const result = "Task Succeeded. Result: All bugs fixed";
    const update = {
      status: "completed" as const,
      progress: 1,
      result: result.split("Task Succeeded. Result:")[1]?.trim(),
    };

    expect(update.status).toBe("completed");
    expect(update.progress).toBe(1);
    expect(update.result).toBe("All bugs fixed");
  });

  test("failed result sets progress to 1", () => {
    const result = "Task failed. Error: timeout exceeded";
    const update = {
      status: "failed" as const,
      progress: 1,
      error: result.split("Task failed.")[1]?.trim(),
    };

    expect(update.status).toBe("failed");
    expect(update.progress).toBe(1);
    expect(update.error).toBe("Error: timeout exceeded");
  });
});

describe("message-list: failureKind classification", () => {
  test("blocked_on_user disposition is always blocked, never a network loss", () => {
    expect(
      failureKind("network is unreachable", "network_unavailable", "blocked_on_user"),
    ).toBe("blocked");
    expect(
      failureKind(
        "net::ERR_CONNECTION_REFUSED",
        undefined,
        "blocked_on_user",
        "environment",
      ),
    ).toBe("blocked");
  });

  test("structured environment kind maps to environment even for network-like text", () => {
    expect(
      failureKind("network is unreachable", "network_unavailable", "failed", "environment"),
    ).toBe("environment");
  });

  test("legacy environment markers are still recognised without structure", () => {
    expect(
      failureKind("Aborted removal of modules directory due to no TTY"),
    ).toBe("environment");
    expect(
      failureKind("zsh: command not found: pnpm"),
    ).toBe("environment");
    expect(
      failureKind("Permission denied: /tmp/x"),
    ).toBe("environment");
  });

  test("genuine network failures stay network", () => {
    expect(
      failureKind("fetch failed: network error"),
    ).toBe("network");
    expect(
      failureKind("econnrefused", "ECONNREFUSED"),
    ).toBe("network");
  });

  test("ordinary guard / verification codes keep their kinds", () => {
    expect(
      failureKind("todo-protocol guard: incomplete", "guard_impasse"),
    ).toBe("guard");
    expect(
      failureKind("no verification step was recorded", "verification_required"),
    ).toBe("verification");
    expect(
      failureKind("boom", "agent_response_failed"),
    ).toBe("error");
  });
});

describe("message-list: clarification card activity", () => {
  function makeGroup(type: MessageGroup["type"], id: string): MessageGroup {
    return { type, id, messages: [] } as MessageGroup;
  }

  test("only the newest group is active", () => {
    const clarification = makeGroup("assistant:clarification", "g1");
    const human = makeGroup("human", "g2");
    const groups = [clarification, human];

    expect(isLatestMessageGroup(groups, clarification)).toBe(false);
    expect(isLatestMessageGroup(groups, human)).toBe(true);
  });

  test("a stale clarification card goes inert once the user moves on", () => {
    const clarification = makeGroup("assistant:clarification", "g1");
    const laterAssistant = makeGroup("assistant", "g2");

    expect(
      isLatestMessageGroup([clarification, laterAssistant], clarification),
    ).toBe(false);
  });

  test("the newest clarification card stays active", () => {
    const clarification = makeGroup("assistant:clarification", "g1");

    expect(isLatestMessageGroup([clarification], clarification)).toBe(true);
  });
});
