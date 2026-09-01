import { describe, expect, test } from "vitest";

import type { LiveToolEvent } from "./live-tool-timeline";
import {
  pickCurrentWorkBlock,
  progressForWorkBlocks,
  statusText,
  toWorkBlocks,
  workBlockActionLabel,
  workBlockLabelsFromShape,
  workBlockTarget,
  workBlockTitle,
} from "./work-blocks";

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

describe("work blocks", () => {
  test("accepts localized status labels without changing the default fallback", () => {
    expect(statusText("running")).toBe("正在执行");
    expect(
      statusText("running", {
        running: "Running",
        waiting_approval: "Waiting",
        warning: "Recovered",
        error: "Failed",
        done: "Done",
      }),
    ).toBe("Running");
  });

  test("filters transport and child tool events", () => {
    const blocks = toWorkBlocks([
      event({ id: "transport", name: "response_stream" }),
      event({ id: "gateway", name: "model_gateway", status: "running" }),
      event({ id: "reasoning", name: "model_reasoning" }),
      event({
        id: "child",
        name: "grep",
        parentToolUseId: "shell-1",
        input: { pattern: "needle" },
      }),
      event({ id: "read", name: "read_file", input: { path: "src/app.tsx" } }),
    ]);

    expect(blocks.map((block) => block.id)).toEqual(["read"]);
    expect(blocks[0]).toMatchObject({
      kind: "read",
      actionKey: "read",
      target: "app.tsx",
      title: { key: "actionTarget", target: "app.tsx" },
      subtitle: "app.tsx",
    });
    expect(workBlockActionLabel(blocks[0])).toBe("Read");
    expect(workBlockTitle(blocks[0])).toBe("Read app.tsx");
  });

  test("renders localized titles from a locale label shape", () => {
    const labels = workBlockLabelsFromShape({
      actions: { read: "阅读", parallelDispatch: "并行分派" },
      actionTarget: "{action} {target}",
      parallelDispatch: "并行分派子任务",
      parallelDispatchWithCount: "并行分派 {count} 个子任务",
      parallelTarget: "子任务",
      parallelTargetWithCount: "{count} 个子任务",
    });
    const blocks = toWorkBlocks([
      event({ id: "read", name: "read_file", input: { path: "src/app.tsx" } }),
      event({
        id: "swarm",
        name: "call_agent_parallel",
        input: { specs: [{ agent_id: "a" }, { agent_id: "b" }] },
      }),
    ]);

    expect(workBlockTitle(blocks[0], labels)).toBe("阅读 app.tsx");
    expect(workBlockActionLabel(blocks[0], labels)).toBe("阅读");
    expect(workBlockTitle(blocks[1], labels)).toBe("并行分派 2 个子任务");
    expect(workBlockTarget(blocks[1], labels)).toBe("2 个子任务");
  });

  test("coalesces restored start and result records for one tool call", () => {
    const blocks = toWorkBlocks([
      event({
        id: "same-call",
        status: "running",
        startedAt: 1000,
        input: { path: "src/app.tsx" },
      }),
      event({
        id: "same-call",
        status: "done",
        startedAt: 1010,
        finishedAt: 1020,
        output: { content: "source" },
      }),
    ]);

    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      id: "same-call",
      status: "done",
      startedAt: 1000,
      target: "app.tsx",
    });
    expect(blocks[0].outputText).toContain("source");
  });

  test("uses active todo text and running block for progress", () => {
    const blocks = toWorkBlocks([
      event({
        id: "todo",
        name: "todo_write",
        input: {
          items: [
            { content: "one", status: "completed" },
            { content: "implement renderer", status: "in_progress" },
          ],
        },
      }),
      event({
        id: "shell",
        name: "shell_command",
        status: "running",
        startedAt: 2000,
        input: { command: "npm run typecheck" },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      actionKey: "writeTodoList",
      title: { key: "action" },
      subtitle: "implement renderer",
    });
    expect(workBlockTitle(blocks[0])).toBe("Write to-do list");
    expect(blocks[1]).toMatchObject({
      actionKey: "runTerminal",
      target: "",
      title: { key: "action" },
      subtitle: "正在执行",
    });
    expect(workBlockTitle(blocks[1])).toBe("Run terminal");
    expect(workBlockTitle(blocks[1])).not.toContain("npm run typecheck");
    expect(blocks[1].subtitle).not.toContain("npm run typecheck");
    const current = pickCurrentWorkBlock(blocks);
    expect(current?.id).toBe("shell");
    expect(progressForWorkBlocks(blocks, current!)).toEqual({
      current: 2,
      total: 2,
    });
  });

  test("promotes MCP progress into visible block text", () => {
    const blocks = toWorkBlocks([
      event({
        id: "mcp-progress",
        name: "mcp:read_workbook",
        status: "running",
        input: {
          server: "sheets",
          tool: "read_workbook",
          progress: {
            label: "Reading workbook",
            percent: 0.42,
            current: 21,
            total: 50,
          },
        },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      id: "mcp-progress",
      title: { key: "raw", text: "Reading workbook" },
      subtitle: "42%",
    });
    expect(workBlockTitle(blocks[0])).toBe("Reading workbook");
  });

  test("treats manual verification-required audit as waiting, not a hard failure", () => {
    const blocks = toWorkBlocks([
      event({
        id: "verify-required",
        name: "verification:manual",
        status: "error",
        input: { command: "verification required" },
        output: {
          summary:
            "Code changes were produced but no verification step was recorded before final answer.",
        },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      id: "verify-required",
      title: { key: "awaitVerification" },
      status: "waiting_approval",
      subtitle: "等待确认",
    });
    expect(workBlockTitle(blocks[0])).toBe("Awaiting verification");
  });

  test("keeps real read failures red", () => {
    const blocks = toWorkBlocks([
      event({
        id: "read-error",
        name: "read_file",
        status: "error",
        input: { path: "missing.ts" },
        output: { error: "ENOENT" },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      id: "read-error",
      kind: "read",
      status: "error",
      title: { key: "actionTarget", target: "missing.ts" },
    });
    expect(workBlockTitle(blocks[0])).toBe("Read missing.ts");
  });

  test("renders nested child-agent read and report events as public actions", () => {
    const blocks = toWorkBlocks([
      event({
        id: "child-read",
        name: "read_file",
        agentId: "schema_reader",
        input: {
          server: "subagent",
          arguments: {
            agent_id: "schema_reader",
            input: { path: "output/final/agent-regression.json" },
          },
        },
      }),
      event({
        id: "child-report",
        name: "report",
        agentId: "schema_reader",
        input: {
          server: "subagent",
          arguments: {
            agent_id: "schema_reader",
            input: { output: "echo.regression.v1" },
          },
        },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      actionKey: "read",
      target: "agent-regression.json",
      subtitle: "agent-regression.json",
    });
    expect(workBlockTitle(blocks[0])).toBe("Read agent-regression.json");
    expect(blocks[1].actionKey).toBe("submitResult");
    expect(workBlockActionLabel(blocks[1])).toBe("Submit result");
  });

  test("uses explicit terminal failure wording for command errors", () => {
    const blocks = toWorkBlocks([
      event({
        id: "shell-error",
        name: "shell_command",
        status: "error",
        input: { command: "npm run build" },
        output: { error: "exit 1" },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      id: "shell-error",
      actionKey: "terminalFailed",
      target: "",
      title: { key: "action" },
      subtitle: "执行失败",
    });
    expect(workBlockActionLabel(blocks[0])).toBe("Terminal run failed");
    expect(workBlockTitle(blocks[0])).toBe("Terminal run failed");
    expect(workBlockTitle(blocks[0])).not.toContain("npm run build");
    expect(blocks[0].subtitle).not.toContain("npm run build");
  });

  test("uses public terminal summaries without leaking local cwd", () => {
    const blocks = toWorkBlocks([
      event({
        id: "shell-cwd",
        name: "shell_command",
        status: "running",
        input: {
          command: "cat ~/.ssh/id_rsa && pnpm test",
          cwd: "/Users/example/Public/echo/echo-agent",
        },
      }),
    ]);

    expect(blocks[0]).toMatchObject({
      id: "shell-cwd",
      title: { key: "action" },
      subtitle: "echo-agent",
    });
    expect(workBlockTitle(blocks[0])).toBe("Run terminal");
    expect(workBlockTitle(blocks[0])).not.toContain("cat ~/.ssh");
    expect(blocks[0].subtitle).not.toContain("/Users/");
  });

  test("shows the failure cause as the subtitle of a failed agent block", () => {
    // Regression: the real session teD7hPf9dkGOExwO0dIiBE lost three lanes to
    // an SSL disconnect and a round cap. Both causes were on the wire; the
    // subtitle fell through to the agent name, so the screen said nothing.
    const blocks = toWorkBlocks([
      event({
        id: "finished-ssl",
        name: "subagent",
        lifecycle: "finished",
        status: "error",
        agentName: "Researcher",
        error:
          "ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1010)",
      }),
    ]);

    expect(blocks[0].subtitle).toContain("UNEXPECTED_EOF_WHILE_READING");
  });

  test("keeps the normal subtitle when a finished agent block succeeded", () => {
    const blocks = toWorkBlocks([
      event({
        id: "finished-ok",
        name: "subagent",
        lifecycle: "finished",
        status: "done",
        agentName: "Researcher",
      }),
    ]);

    expect(blocks[0].subtitle).toBe("Researcher");
  });

  test("classifies swarm dispatch and document skills as workflow blocks", () => {
    const blocks = toWorkBlocks([
      event({
        id: "swarm",
        name: "call_agent_parallel",
        input: {
          specs: [
            { agent_id: "researcher", prompt: "A" },
            { agent_id: "reviewer", prompt: "B" },
            { agent_id: "writer", prompt: "C" },
          ],
        },
      }),
      event({
        id: "docx",
        name: "docx",
        input: { name: "docx" },
      }),
    ]);

    expect(blocks.map((block) => block.kind)).toEqual(["swarm", "skill"]);
    expect(blocks[0].title).toEqual({ key: "parallelDispatch", count: 3 });
    expect(workBlockTitle(blocks[0])).toContain("3");
    expect(blocks[1].title).toEqual({ key: "skillDocx" });
    expect(workBlockTitle(blocks[1])).toContain("DOCX");
  });
});
