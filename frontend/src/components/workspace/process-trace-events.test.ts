import { describe, expect, test } from "vitest";

import type { LiveToolEvent } from "./live-tool-timeline";
import { publicTraceEventLabel } from "./messages/process-trace";
import {
  getProcessTraceEvents,
  isCollapsibleAutoVerificationEvent,
} from "./process-trace-events";

function event(partial: Partial<LiveToolEvent>): LiveToolEvent {
  return {
    id: "event-1",
    name: "read_file",
    status: "done",
    startedAt: 1000,
    iteration: 0,
    ...partial,
  };
}

describe("process trace events", () => {
  test("filters transport and model plumbing from user-visible process", () => {
    const visible = getProcessTraceEvents([
      event({ id: "request", name: "turn_request", startedAt: 100 }),
      event({ id: "stream", name: "stream_connection", startedAt: 200 }),
      event({ id: "gateway", name: "model_gateway", startedAt: 300 }),
      event({ id: "reasoning", name: "model_reasoning", startedAt: 400 }),
      event({ id: "response", name: "response_stream", startedAt: 500 }),
      event({ id: "read", name: "read_file", startedAt: 600 }),
    ]);

    expect(visible.map((item) => item.id)).toEqual(["read"]);
  });

  test("keeps meaningful work events and preserves chronological order", () => {
    const visible = getProcessTraceEvents([
      event({ id: "write", name: "write_file", startedAt: 400 }),
      event({ id: "search", name: "web_search", startedAt: 100 }),
      event({ id: "todo", name: "todo_write", startedAt: 200 }),
      event({ id: "swarm", name: "call_agent_parallel", startedAt: 300 }),
    ]);

    expect(visible.map((item) => item.id)).toEqual([
      "search",
      "todo",
      "swarm",
      "write",
    ]);
  });

  test("omits child tool events because their parent carries the process step", () => {
    const visible = getProcessTraceEvents([
      event({ id: "shell", name: "shell_command", startedAt: 100 }),
      event({
        id: "child",
        name: "grep",
        parentToolUseId: "shell",
        startedAt: 200,
      }),
    ]);

    expect(visible.map((item) => item.id)).toEqual(["shell"]);
  });

  test("keeps completed auto verification events and marks them collapsible instead of filtering", () => {
    const visible = getProcessTraceEvents([
      event({ id: "verification-done", name: "verification", status: "done" }),
      event({
        id: "verification-running",
        name: "verification",
        status: "running",
      }),
      event({
        id: "verification-error",
        name: "verification",
        status: "error",
      }),
      event({ id: "read", name: "read_file", status: "done" }),
    ]);

    expect(visible.map((item) => item.id)).toEqual([
      "verification-done",
      "verification-running",
      "verification-error",
      "read",
    ]);
    expect(
      visible.filter(isCollapsibleAutoVerificationEvent).map((item) => item.id),
    ).toEqual(["verification-done"]);
  });

  test("public trace labels hide raw tool names, commands, and sensitive targets", () => {
    expect(
      publicTraceEventLabel(
        event({
          name: "read_file",
          input: { path: "/repo/src/message-group.tsx" },
        }),
      ),
    ).toEqual({ label: "Read file", detail: "message-group.tsx" });

    expect(
      publicTraceEventLabel(
        event({
          name: "exec_shell",
          input: { command: "cat ~/.ssh/id_rsa && pnpm test" },
        }),
      ),
    ).toEqual({ label: "Run command", detail: "" });

    expect(
      publicTraceEventLabel(
        event({
          name: "mcp_secret_probe",
          thought: 'Action: read_file({"path":"secret"})',
          observation: "token sk-test-should-not-render",
        }),
      ),
    ).toEqual({ label: "Run operation", detail: "" });
  });
});
