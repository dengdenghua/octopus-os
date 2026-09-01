import { renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { LiveToolEvent } from "./live-tool-timeline";
import {
  buildAgentWorkbenchSnapshot,
  currentScreenFrame,
  screenBlocksForAgent,
  useAgentWorkbenchSnapshot,
} from "./agent-workbench-snapshot";
import { hasFinalOutputArtifact } from "./agent-workbench-utils";

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

const deriveAgentTiles = () => [];

describe("agent workbench snapshot", () => {
  test("uses the latest todo statuses when the workbench snapshot is stale", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "todo-live",
          name: "todo_write",
          status: "running",
          input: {
            items: [
              { content: "定位入口", status: "completed" },
              { content: "实现看门狗", status: "completed" },
              { content: "接入路由", status: "in_progress" },
            ],
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 1,
              status: "running",
              phases: [
                {
                  id: "server-locate",
                  index: 1,
                  total: 3,
                  title: "定位入口",
                  status: "running",
                },
                {
                  id: "server-watchdog",
                  index: 2,
                  total: 3,
                  title: "实现看门狗",
                  status: "pending",
                },
                {
                  id: "server-route",
                  index: 3,
                  total: 3,
                  title: "接入路由",
                  status: "pending",
                },
              ],
              currentPhaseId: "server-locate",
              currentItemId: null,
              updatedAt: "2026-08-11T00:00:00.000Z",
            },
          },
        }),
      ],
      { deriveAgentTiles, isLoading: true },
    );

    expect(snapshot.phases.map((phase) => phase.status)).toEqual([
      "done",
      "done",
      "running",
    ]);
    expect(snapshot.currentPhase?.title).toBe("接入路由");
  });

  test("keeps the same version for duplicate visible state", () => {
    const events = [
      event({
        id: "read-1",
        input: { path: "src/app.tsx" },
      }),
    ];
    const { result, rerender } = renderHook(
      ({ items }: { items: LiveToolEvent[] }) =>
        useAgentWorkbenchSnapshot(items, { deriveAgentTiles }),
      { initialProps: { items: events } },
    );

    const first = result.current;
    expect(first.version).toBe(1);

    rerender({ items: [...events] });
    expect(result.current).toBe(first);
    expect(result.current.version).toBe(1);

    rerender({
      items: [
        event({
          id: "read-1",
          input: { path: "src/app.tsx" },
        }),
        event({
          id: "shell-1",
          name: "shell_command",
          status: "running",
          startedAt: 2000,
          input: { command: "pnpm test" },
        }),
      ],
    });
    expect(result.current).not.toBe(first);
    expect(result.current.version).toBe(2);
  });

  test("caches object payload fingerprints by identity without changing output", () => {
    const input = { path: "src/app.tsx", nested: { b: 2, a: 1 } };
    const output = { ok: true, detail: "x".repeat(600) };
    const build = (
      payloadInput: Record<string, unknown>,
      payloadOutput: unknown,
    ) =>
      buildAgentWorkbenchSnapshot(
        [event({ id: "read-1", input: payloadInput, output: payloadOutput })],
        { deriveAgentTiles },
      ).fingerprint;

    const first = build(input, output);
    // Same identity (WeakMap cache hit) reproduces the fingerprint.
    expect(build(input, output)).toBe(first);
    // Equal-by-value clones (cache miss) must produce the same fingerprint:
    // the cache is an optimization, not part of the output.
    expect(build(structuredClone(input), structuredClone(output))).toBe(first);
    // Different payload content still changes the fingerprint.
    expect(build({ path: "src/other.tsx" }, output)).not.toBe(first);
  });

  test("selects one current screen frame instead of replaying historical blocks", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "spawn-1",
          name: "subagent",
          lifecycle: "spawned",
          status: "running",
          agentId: "writer-a",
          subAgentRole: "writer",
          subagentCodename: "Spark-01",
        }),
        event({
          id: "read-main",
          name: "read_file",
          input: { path: "src/old.ts" },
        }),
        event({
          id: "read-agent",
          name: "read_file",
          agentId: "writer-a",
          subAgentRole: "writer",
          startedAt: 1500,
          input: { path: "agent/old.md" },
        }),
        event({
          id: "shell-main",
          name: "shell_command",
          status: "running",
          startedAt: 2000,
          input: { command: "pnpm test" },
        }),
      ],
      { deriveAgentTiles },
    );

    const mainBlocks = screenBlocksForAgent(snapshot.blocks, null);
    expect(mainBlocks.map((block) => block.id)).toEqual([
      "read-main",
      "shell-main",
    ]);
    expect(currentScreenFrame(mainBlocks).block?.id).toBe("shell-main");
    expect(currentScreenFrame(mainBlocks, "read-main").block?.id).toBe(
      "read-main",
    );

    const agentBlocks = screenBlocksForAgent(snapshot.blocks, "writer-a");
    expect(agentBlocks.map((block) => block.id)).toEqual([
      "spawn-1",
      "read-agent",
    ]);
    expect(currentScreenFrame(agentBlocks).block?.id).toBe("read-agent");
  });

  test("uses server workbench snapshot as the current-frame source", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "todo-server",
          name: "todo_write",
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 7,
              status: "running",
              phases: [
                {
                  id: "server-phase-1",
                  index: 1,
                  total: 2,
                  title: "Phase 1: Read docs",
                  status: "done",
                },
                {
                  id: "server-phase-2",
                  index: 2,
                  total: 2,
                  title: "Phase 2: Run tests",
                  status: "running",
                  activeItemId: "shell-server",
                },
              ],
              currentPhaseId: "server-phase-2",
              currentItemId: "shell-server",
              workspaceFocus: {
                itemId: "shell-server",
                view: "terminal",
                title: "Running tests",
              },
              updatedAt: "2026-01-01T00:00:00.000Z",
            },
          },
        }),
        event({
          id: "read-old",
          name: "read_file",
          startedAt: 1500,
          input: { path: "src/old.ts" },
        }),
        event({
          id: "shell-server",
          name: "shell_command",
          status: "running",
          startedAt: 2000,
          input: { command: "pnpm test" },
        }),
      ],
      { deriveAgentTiles },
    );

    expect(snapshot.currentPhase?.id).toBe("server-phase-2");
    expect(snapshot.currentPhase?.title).toBe("Run tests");
    expect(snapshot.phases.map((phase) => phase.title)).toEqual([
      "Read docs",
      "Run tests",
    ]);
    expect(snapshot.currentBlock?.id).toBe("shell-server");
    expect(snapshot.focusedTab).toBe("terminal");
    expect(snapshot.phases.map((phase) => phase.id)).toEqual([
      "server-phase-1",
      "server-phase-2",
    ]);
  });

  test("preserves server waiting approval phase status", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "approval-snapshot",
          name: "todo_write",
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 8,
              status: "waiting_approval",
              phases: [
                {
                  id: "phase-approval",
                  index: 1,
                  total: 1,
                  title: "Phase 1: Confirm file write",
                  status: "waiting_approval",
                  activeItemId: "write-approval",
                },
              ],
              currentPhaseId: "phase-approval",
              currentItemId: "write-approval",
              workspaceFocus: {
                itemId: "write-approval",
                view: "approval",
                title: "Confirm write",
              },
              updatedAt: "2026-01-01T00:00:00.000Z",
            },
          },
        }),
        event({
          id: "write-approval",
          name: "write_text_file",
          status: "waiting_approval",
          startedAt: 2000,
          input: { path: "plan.md" },
        }),
      ],
      { deriveAgentTiles },
    );

    expect(snapshot.currentPhase?.status).toBe("waiting_approval");
    expect(snapshot.phases[0]?.status).toBe("waiting_approval");
    expect(snapshot.currentBlock?.id).toBe("write-approval");
  });

  test("keeps manual verification-required audit out of completed read phase", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "snapshot-1",
          name: "todo_write",
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 10,
              status: "error",
              phases: [
                {
                  id: "phase-read",
                  index: 1,
                  total: 2,
                  title: "Phase 1: 补齐上下文",
                  status: "done",
                  activeItemId: "read-package",
                },
                {
                  id: "phase-deliver",
                  index: 2,
                  total: 2,
                  title: "Phase 2: 收拢答案",
                  status: "waiting_approval",
                  activeItemId: "verify-required",
                },
              ],
              currentPhaseId: "phase-deliver",
              currentItemId: "verify-required",
              updatedAt: "2026-01-01T00:00:00.000Z",
            },
          },
        }),
        event({
          id: "read-package",
          name: "read_file",
          status: "done",
          startedAt: 1500,
          input: { path: "package.json" },
        }),
        event({
          id: "verify-required",
          name: "verification:manual",
          status: "error",
          startedAt: 2000,
          input: { command: "verification required" },
          output: {
            summary:
              "Code changes were produced but no verification step was recorded before final answer.",
          },
        }),
      ],
      { deriveAgentTiles },
    );

    expect(snapshot.blocks.map((block) => [block.id, block.status])).toEqual([
      ["snapshot-1", "done"],
      ["read-package", "done"],
      ["verify-required", "waiting_approval"],
    ]);
    expect(snapshot.phases.map((phase) => [phase.id, phase.status])).toEqual([
      ["phase-read", "done"],
      ["phase-deliver", "waiting_approval"],
    ]);
    expect(snapshot.phases[0]?.blockIds).toEqual(["read-package"]);
    expect(snapshot.phases[1]?.blockIds).toEqual(["verify-required"]);
    expect(snapshot.currentPhase?.id).toBe("phase-deliver");
    expect(snapshot.currentBlock?.id).toBe("verify-required");
  });

  test("downgrades recovered tool failures after a successful final answer", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "read-package",
          name: "read_file",
          status: "done",
          startedAt: 1000,
          input: { path: "frontend/package.json" },
        }),
        event({
          id: "search-failed",
          name: "code_search",
          status: "error",
          startedAt: 1200,
          input: { query: "useState" },
          output:
            "(工具失败) status=failed error=TypeError\n请在下一轮 Thought 中分析失败原因，然后换一种方式重试",
        }),
        event({
          id: "read-failed",
          name: "read_file",
          status: "error",
          startedAt: 1300,
          input: { path: "frontend/src/app/workspace/page.tsx" },
          output:
            "(工具失败) status=failed error=TypeError\n请在下一轮 Thought 中分析失败原因，然后换一种方式重试",
        }),
        event({
          id: "fallback-read",
          name: "ipython",
          status: "done",
          startedAt: 1500,
          input: { command: "Path('frontend/src').rglob('*.tsx')" },
          output: "frontend/src/app/workspace/page.tsx",
        }),
      ],
      { deriveAgentTiles, hasAnswer: true, runSettled: true },
    );

    expect(snapshot.blocks.map((block) => [block.id, block.status])).toEqual([
      ["read-package", "done"],
      ["search-failed", "warning"],
      ["read-failed", "warning"],
      ["fallback-read", "done"],
    ]);
    expect(snapshot.phases.every((phase) => phase.status !== "error")).toBe(
      true,
    );
    expect(snapshot.phases.map((phase) => phase.status)).toContain("done");
  });

  test("settled final answers preserve explicit server phase truth", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "snapshot-stale",
          name: "todo_write",
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 9,
              status: "running",
              phases: [
                {
                  id: "phase-1",
                  index: 1,
                  total: 3,
                  title: "Phase 1: Pick market",
                  status: "done",
                },
                {
                  id: "phase-2",
                  index: 2,
                  total: 3,
                  title: "Phase 2: Deep research",
                  status: "running",
                  activeItemId: "search-1",
                },
                {
                  id: "phase-3",
                  index: 3,
                  total: 3,
                  title: "Phase 3: Write report",
                  status: "pending",
                },
              ],
              currentPhaseId: "phase-2",
              currentItemId: "search-1",
              updatedAt: "2026-01-01T00:00:00.000Z",
            },
          },
        }),
        event({
          id: "search-1",
          name: "web_search",
          status: "done",
          startedAt: 1500,
        }),
      ],
      { deriveAgentTiles, hasAnswer: true, runSettled: true },
    );

    expect(snapshot.phases.map((phase) => phase.status)).toEqual([
      "done",
      "running",
      "pending",
    ]);
    expect(snapshot.currentPhase?.id).toBe("phase-2");
  });

  test("ignores a stale current item from a terminal pending snapshot", () => {
    const eventsWithCurrentItem = (currentItemId: string | null) => [
      event({
        id: "server-snapshot",
        name: "todo_write",
        input: {
          workbenchSnapshot: {
            schemaVersion: 2,
            version: 3,
            status: "pending",
            phases: [
              {
                id: "phase-1",
                index: 1,
                total: 2,
                title: "Inspect",
                status: "done",
              },
              {
                id: "phase-2",
                index: 2,
                total: 2,
                title: "Optional follow-up",
                status: "pending",
              },
            ],
            currentPhaseId: "phase-2",
            // Older backends persisted the last tool here after the turn
            // had settled. It is history, not an active selection.
            currentItemId,
            updatedAt: "2026-01-01T00:00:00.000Z",
          },
        },
      }),
      event({ id: "read-old", name: "read_file", startedAt: 1500 }),
      event({ id: "read-latest", name: "read_file", startedAt: 2000 }),
    ];
    const stale = buildAgentWorkbenchSnapshot(
      eventsWithCurrentItem("read-old"),
      { deriveAgentTiles, hasAnswer: true, runSettled: true },
    );
    const clean = buildAgentWorkbenchSnapshot(eventsWithCurrentItem(null), {
      deriveAgentTiles,
      hasAnswer: true,
      runSettled: true,
    });

    expect(stale.currentBlock?.id).toBe(clean.currentBlock?.id);
    expect(stale.currentPhase?.id).toBe(clean.currentPhase?.id);
    expect(stale.currentPhase?.id).toBe("phase-2");
  });

  test("treats generated final artifacts as completed output", () => {
    const events = [
      event({
        id: "snapshot-stale",
        name: "todo_write",
        input: {
          workbenchSnapshot: {
            schemaVersion: 2,
            version: 9,
            status: "running",
            phases: [
              {
                id: "phase-1",
                index: 1,
                total: 2,
                title: "Phase 1: Research",
                status: "done",
              },
              {
                id: "phase-2",
                index: 2,
                total: 2,
                title: "Phase 2: Write final report",
                status: "running",
                activeItemId: "search-failed",
              },
            ],
            currentPhaseId: "phase-2",
            currentItemId: "search-failed",
            updatedAt: "2026-01-01T00:00:00.000Z",
          },
        },
      }),
      event({
        id: "search-failed",
        name: "web_search",
        status: "error",
        startedAt: 1500,
        input: { query: "source that timed out" },
      }),
      event({
        id: "write-final",
        name: "write_file",
        status: "done",
        startedAt: 2000,
        input: {
          path: "/tmp/workspace/output/final/market_report.md",
        },
      }),
    ];

    expect(hasFinalOutputArtifact(events)).toBe(true);

    const snapshot = buildAgentWorkbenchSnapshot(events, {
      deriveAgentTiles,
      hasAnswer: true,
      runSettled: true,
    });

    expect(snapshot.visibleDiffEntries).toEqual([
      expect.objectContaining({
        created: true,
        path: "/tmp/workspace/output/final/market_report.md",
      }),
    ]);
    expect(snapshot.phases.map((phase) => phase.status)).toEqual([
      "done",
      "running",
    ]);
    expect(snapshot.currentPhase?.id).toBe("phase-2");
  });

  test("treats persisted artifact items as completed final output", () => {
    const events = [
      event({
        id: "search-failed",
        name: "web_search",
        status: "error",
        input: { query: "source timeout" },
      }),
      event({
        id: "artifact-final",
        name: "artifact",
        status: "done",
        startedAt: 2000,
        input: {
          path: "/tmp/workspace/output/final/market_report.pdf",
          kind: "pdf",
          title: "Market report",
        },
      }),
    ];

    expect(hasFinalOutputArtifact(events)).toBe(true);
  });

  test("treats updated report deliverables as completed output", () => {
    const events = [
      event({
        id: "file-update",
        name: "file_change",
        status: "done",
        startedAt: 2000,
        input: {
          changes: [
            {
              path: "/tmp/workspace/reports/ai_glasses_report.md",
              op: "update",
            },
          ],
        },
      }),
    ];

    expect(hasFinalOutputArtifact(events)).toBe(true);
  });

  test("treats MCP write_text_file report outputs as completed output", () => {
    const events = [
      event({
        id: "write-text-final",
        name: "mcp:filesystem.write_text_file",
        status: "done",
        startedAt: 2000,
        input: {
          filePath: "/tmp/workspace/个人知识库自动化周报方案.md",
          content: "# 个人知识库 + 自动化周报方案\n",
        },
      }),
    ];

    expect(hasFinalOutputArtifact(events)).toBe(true);
  });

  test("does not treat read-only report sources as final output", () => {
    const events = [
      event({
        id: "read-source",
        name: "read_file",
        status: "done",
        startedAt: 2000,
        input: {
          path: "/tmp/source/industry_report.pdf",
        },
      }),
    ];

    expect(hasFinalOutputArtifact(events)).toBe(false);
  });

  test("keeps observed frame ids attached to their server phases", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "read-server",
          name: "read_file",
          input: { path: "src/context.ts" },
        }),
        event({
          id: "snapshot-1",
          name: "todo_write",
          startedAt: 1200,
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 1,
              status: "running",
              phases: [
                {
                  id: "phase-read",
                  index: 1,
                  total: 2,
                  title: "Phase 1: Read context",
                  status: "running",
                  activeItemId: "read-server",
                },
                {
                  id: "phase-test",
                  index: 2,
                  total: 2,
                  title: "Phase 2: Test",
                  status: "pending",
                },
              ],
              currentPhaseId: "phase-read",
              currentItemId: "read-server",
              updatedAt: "2026-01-01T00:00:00.000Z",
            },
          },
        }),
        event({
          id: "shell-server",
          name: "shell_command",
          status: "running",
          startedAt: 2000,
          input: { command: "pnpm test" },
        }),
        event({
          id: "snapshot-2",
          name: "todo_write",
          startedAt: 2200,
          status: "running",
          input: {
            workbenchSnapshot: {
              schemaVersion: 2,
              version: 2,
              status: "running",
              phases: [
                {
                  id: "phase-read",
                  index: 1,
                  total: 2,
                  title: "Phase 1: Read context",
                  status: "done",
                },
                {
                  id: "phase-test",
                  index: 2,
                  total: 2,
                  title: "Phase 2: Test",
                  status: "running",
                  activeItemId: "shell-server",
                },
              ],
              currentPhaseId: "phase-test",
              currentItemId: "shell-server",
              updatedAt: "2026-01-01T00:00:01.000Z",
            },
          },
        }),
      ],
      { deriveAgentTiles },
    );

    expect(snapshot.phases.map((phase) => [phase.id, phase.blockIds])).toEqual([
      ["phase-read", ["read-server"]],
      ["phase-test", ["shell-server"]],
    ]);
  });

  test("shows an optimistic planning phase while a live run has no events yet", () => {
    const snapshot = buildAgentWorkbenchSnapshot([], {
      deriveAgentTiles,
      isLoading: true,
    });

    expect(snapshot.phases.map((phase) => [phase.id, phase.status])).toEqual([
      ["optimistic:planning", "running"],
    ]);
    expect(snapshot.currentPhase?.id).toBe("optimistic:planning");
  });

  test("drops the optimistic phase once real phases exist", () => {
    const snapshot = buildAgentWorkbenchSnapshot(
      [
        event({
          id: "todo-1",
          name: "todo_write",
          input: {
            items: [
              { content: "\u8bfb\u53d6\u4ee3\u7801", status: "in_progress" },
              { content: "\u4fee\u6539\u5b9e\u73b0", status: "pending" },
            ],
          },
        }),
      ],
      { deriveAgentTiles },
    );

    expect(
      snapshot.phases.some((phase) => phase.id === "optimistic:planning"),
    ).toBe(false);
    expect(snapshot.phases.length).toBeGreaterThan(0);
  });

  test("hides the optimistic phase when the run is not explicitly loading", () => {
    const snapshot = buildAgentWorkbenchSnapshot([], { deriveAgentTiles });
    expect(snapshot.phases).toEqual([]);
    expect(snapshot.currentPhase).toBeNull();
  });

  test("hides the optimistic phase for settled, failed, paused, or answered runs", () => {
    for (const options of [
      { runSettled: true },
      { runFailed: true },
      { paused: true },
      { hasAnswer: true },
    ]) {
      const snapshot = buildAgentWorkbenchSnapshot([], {
        deriveAgentTiles,
        isLoading: true,
        ...options,
      });
      expect(snapshot.phases).toEqual([]);
      expect(snapshot.currentPhase).toBeNull();
    }
  });
});
