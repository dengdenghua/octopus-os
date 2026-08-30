/**
 * Unit tests for ``LiveToolTimeline`` — the component that renders
 * the live sub-tool timeline while a turn streams.
 *
 * This is the automated counterpart to UI regression scene 6 in
 * ``docs/benchmarks.md`` where we manually verified that a
 * ``call_agent_parallel`` run renders its spawned sub-agent tool
 * calls indented under the parent row. The backend side already has
 * SSE-level coverage in ``benchmarks/_ui_sse_trace.py``; this covers
 * the rendering logic from the frontend side.
 *
 * We verify:
 *   1. A top-level event without children renders as a single row.
 *   2. A parent + N children produces an indented sub-tree (CSS
 *      ``ml-6 border-l pl-2`` on the container).
 *   3. Children are ordered by ``startedAt`` (timeline semantics).
 *   4. Children inherit ``showAgent=true`` so the sub-agent role is
 *      visible next to each child row.
 *   5. An event's ``parentToolUseId`` keeps it off the top-level
 *      list (it only appears as a child of its parent).
 *   6. ``runningOnly`` restricts the visible set to in-flight events.
 *   7. Empty input renders nothing (not even an empty wrapper).
 */
import { describe, expect, test } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { AllProviders } from "@/test/harness";
import { AGENT_WORKBENCH_FOCUS_EVENT } from "./agent-workbench-events";
import { LiveToolTimeline, type LiveToolEvent } from "./live-tool-timeline";

function wrap(
  events: LiveToolEvent[],
  extraProps: Record<string, unknown> = {},
  locale: "en-US" | "zh-CN" = "en-US",
) {
  return render(
    <AllProviders locale={locale}>
      <LiveToolTimeline events={events} {...extraProps} />
    </AllProviders>,
  );
}

// Factory helpers · shrink test noise · defaults map to typical
// backend-emitted shapes for ``tool_start``/``sub_tool_start`` events.
function parentEvent(partial: Partial<LiveToolEvent> = {}): LiveToolEvent {
  return {
    id: "parent-1",
    name: "call_agent_parallel",
    status: "running",
    startedAt: 1000,
    iteration: 0,
    ...partial,
  };
}

function childEvent(partial: Partial<LiveToolEvent> = {}): LiveToolEvent {
  return {
    id: "child-0",
    name: "bb_write",
    status: "done",
    startedAt: 1100,
    finishedAt: 1150,
    durationMs: 50,
    iteration: 1,
    parentToolUseId: "parent-1",
    subAgentRole: "architect",
    agentName: "architect",
    ...partial,
  };
}

describe("LiveToolTimeline · nested sub-tool rendering", () => {
  test("single top-level event renders one row, no nested container", () => {
    const { container } = wrap([parentEvent({ status: "running" })]);
    // No indented sub-tree when the parent has no children.
    expect(container.querySelector(".ml-6.border-l")).toBeNull();
  });

  test("parent with children renders indented sub-tree", () => {
    const events: LiveToolEvent[] = [
      parentEvent({ status: "done", finishedAt: 2000 }),
      childEvent({ id: "c1", name: "recall", startedAt: 1100 }),
      childEvent({ id: "c2", name: "bb_write", startedAt: 1200 }),
      childEvent({ id: "c3", name: "bb_read", startedAt: 1300 }),
    ];
    const { container } = wrap(events);

    // The ``ml-6 border-l pl-2`` stack is the indent hallmark — one
    // such container per parent that has kids.
    const nestedContainers = container.querySelectorAll(".ml-6.border-l.pl-2");
    expect(nestedContainers.length).toBe(1);

    // All 3 children + the parent = 4 rows in the timeline.
    // Rows are the flex containers with either ``running`` bg or
    // the muted done-style bg. Counting via children of root and
    // nested · 1 + 3 = 4.
    const nested = nestedContainers[0]!;
    expect(nested.children.length).toBe(3);
  });

  test("children are ordered by startedAt", () => {
    const events: LiveToolEvent[] = [
      parentEvent({ status: "done", finishedAt: 9999 }),
      // Insert out of order · render must still sort by startedAt.
      childEvent({ id: "c3", name: "bb_read", startedAt: 1300 }),
      childEvent({ id: "c1", name: "recall", startedAt: 1100 }),
      childEvent({ id: "c2", name: "bb_write", startedAt: 1200 }),
    ];
    const { container } = wrap(events);
    const nested = container.querySelector(".ml-6.border-l.pl-2")!;
    const rows = Array.from(nested.children);
    // Each child row shows its tool label in a <span class="font-medium">.
    // We read those in order · expect recall → write blackboard → read blackboard.
    const labels = rows
      .map((row) => row.querySelector(".font-medium")?.textContent ?? "")
      .map((s) => s.trim());
    expect(labels).toEqual([
      "Other action",
      "Write blackboard",
      "Read blackboard",
    ]);
  });

  test("children show the sub-agent role badge", () => {
    const events: LiveToolEvent[] = [
      parentEvent({ status: "done", finishedAt: 2000 }),
      childEvent({
        id: "c1",
        name: "recall",
        startedAt: 1100,
        agentName: "architect",
      }),
    ];
    wrap(events);
    // The sub-agent badge appears as " · architect" next to the
    // row label. Rendered via ``{showAgent && event.agentName && …}``.
    expect(screen.getAllByText(/architect/).length).toBeGreaterThan(0);
  });

  test("events with parentToolUseId never appear as top-level rows", () => {
    // Render ONLY a child event · with no parent in the list.
    // getVisibleEvents filters out anything with parentToolUseId, so
    // the orphan child shouldn't render as a top-level row.
    // (This guards against regression to "flat" rendering.)
    const { container } = wrap([
      childEvent({
        id: "orphan-1",
        name: "bb_write",
        startedAt: 1000,
        parentToolUseId: "nonexistent-parent",
      }),
    ]);
    // No rows rendered · the orphan child gets suppressed since its
    // parent is missing from the visible set.
    expect(container.querySelector(".ml-6.border-l.pl-2")).toBeNull();
    // Nothing else should be rendered either.
    expect(container.textContent).toBe("");
  });

  test("runningOnly={true} suppresses done events", () => {
    const events: LiveToolEvent[] = [
      parentEvent({
        id: "p-done",
        name: "call_agent_parallel",
        status: "done",
        startedAt: 900,
        finishedAt: 1000,
      }),
      parentEvent({
        id: "p-running",
        name: "call_agent",
        status: "running",
        startedAt: 1500,
      }),
    ];
    wrap(events, { runningOnly: true });
    // Only the running parent renders.
    // Done parent's tool name (``call_agent_parallel``) is NOT the
    // only label · we look for the label of the running one
    // (``call_agent`` maps to the raw name since there's no i18n key).
    // The key assert: the done row's " ms" timestamp span should
    // NOT be in the DOM.
    expect(screen.queryByText(/ms$/)).toBeNull();
  });

  test("stream recovery events render with a readable label", () => {
    wrap([
      parentEvent({
        id: "stream-recovery:r1",
        name: "stream_recovery",
        status: "running",
        input: { after: 2, next_after: 4 },
      }),
    ]);

    expect(screen.getByText("Stream recovery")).toBeInTheDocument();
    expect(screen.getAllByText(/next_after/).length).toBeGreaterThan(0);
  });

  test("clicking an agent group asks the workbench to show that agent process", () => {
    let focusedAgentId = "";
    const handler = (event: Event) => {
      focusedAgentId =
        (event as CustomEvent<{ agentId?: string }>).detail?.agentId ?? "";
    };
    window.addEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handler);
    try {
      wrap(
        [
          parentEvent({
            id: "agent-read",
            name: "read_file",
            agentId: "agent-42",
            agentName: "Agent 42",
            status: "running",
            input: { path: "plan.md" },
          }),
        ],
        { groupByAgent: true },
      );

      fireEvent.click(screen.getByRole("button", { name: /Agent 42/ }));

      expect(focusedAgentId).toBe("agent-42");
    } finally {
      window.removeEventListener(AGENT_WORKBENCH_FOCUS_EVENT, handler);
    }
  });

  test("write tools show a live content preview", () => {
    wrap([
      parentEvent({
        id: "write-1",
        name: "write_text_file",
        status: "running",
        input: {
          path: "website/index.html",
          content: "<!DOCTYPE html>\n<html>\n<body>hello</body>\n</html>",
        },
      }),
    ]);

    expect(screen.getByText("Live content preview")).toBeInTheDocument();
    expect(screen.getAllByText(/DOCTYPE html/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/hello/).length).toBeGreaterThan(0);
  });

  test("code tools render developer-readable activity labels", () => {
    wrap(
      [
        parentEvent({
          id: "read-1",
          name: "read_file",
          status: "done",
          startedAt: 1000,
          finishedAt: 1100,
          input: {
            path: "frontend/vite.config.ts",
            start_line: 121,
            end_line: 150,
          },
        }),
        parentEvent({
          id: "grep-1",
          name: "grep",
          status: "done",
          startedAt: 1200,
          finishedAt: 1300,
          input: { pattern: "useThreadStream", path: "frontend/src" },
        }),
        parentEvent({
          id: "bash-1",
          name: "exec_shell",
          status: "running",
          startedAt: 1400,
          input: {
            description: "Run frontend typecheck",
            command: "npm run typecheck",
          },
        }),
      ],
      { showAll: true },
    );

    expect(
      screen.getByText("Read frontend/vite.config.ts (lines 121-150)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Searched text useThreadStream"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Running command Run frontend typecheck"),
    ).toBeInTheDocument();
    expect(screen.getByText("npm run typecheck")).toBeInTheDocument();
  });

  test("file actions use specific running and completed labels", () => {
    wrap(
      [
        parentEvent({
          id: "list-1",
          name: "list_cwd",
          status: "running",
          startedAt: 1000,
          input: { path: "frontend/src" },
        }),
        parentEvent({
          id: "create-1",
          name: "create_file",
          status: "done",
          startedAt: 1200,
          finishedAt: 1300,
          input: { path: "frontend/src/new-file.ts" },
        }),
        parentEvent({
          id: "planning-1",
          name: "planning",
          status: "running",
          startedAt: 1400,
          input: { task: "decide next implementation step" },
        }),
      ],
      { showAll: true },
    );

    expect(
      screen.getByText("Browsing directory frontend/src"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Created file frontend/src/new-file.ts"),
    ).toBeInTheDocument();
    expect(screen.getByText("Planning next step")).toBeInTheDocument();
  });

  test("execution rows expose compact input and result summaries", () => {
    wrap(
      [
        parentEvent({
          id: "read-summary-1",
          name: "read_file",
          status: "done",
          startedAt: 1000,
          finishedAt: 1100,
          input: { path: "frontend/src/app.tsx" },
          output: { stdout: "export default App" },
        }),
      ],
      { showAll: true },
    );

    expect(screen.getByText("Read frontend/src/app.tsx")).toBeInTheDocument();
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("frontend/src/app.tsx")).toBeInTheDocument();
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(screen.getByText("stdout: export default App")).toBeInTheDocument();
  });

  test("Chinese locale uses localized status badges and detail titles", () => {
    const { container } = wrap(
      [
        parentEvent({
          id: "status-running",
          name: "read_file",
          status: "running",
          startedAt: 1000,
          input: { path: "src/running.ts" },
        }),
        parentEvent({
          id: "status-done",
          name: "read_file",
          status: "done",
          startedAt: 1100,
          finishedAt: 1200,
          input: { path: "src/done.ts" },
          output: { stdout: "ok" },
          thought: "\u5148\u770b\u6587\u4ef6",
          observation: "\u8bfb\u53d6\u6210\u529f",
        }),
        parentEvent({
          id: "status-error",
          name: "read_file",
          status: "error",
          startedAt: 1300,
          finishedAt: 1400,
          input: { path: "src/error.ts" },
          output: { error: "missing" },
        }),
        parentEvent({
          id: "status-approval",
          name: "read_file",
          status: "waiting_approval",
          startedAt: 1500,
          input: { path: "src/approval.ts" },
        }),
      ],
      { showAll: true },
      "zh-CN",
    );

    expect(screen.getByText("\u8fdb\u884c\u4e2d")).toBeInTheDocument();
    expect(screen.getByText("\u5df2\u5b8c\u6210")).toBeInTheDocument();
    expect(screen.getByText("\u5931\u8d25")).toBeInTheDocument();
    expect(screen.getByText("\u7b49\u5f85\u5ba1\u6279")).toBeInTheDocument();
    expect(screen.getAllByText("\u8f93\u5165").length).toBeGreaterThan(0);
    expect(screen.getAllByText("\u7ed3\u679c").length).toBeGreaterThan(0);
    expect(container).not.toHaveTextContent("模型");
    expect(container).not.toHaveTextContent("思考细节");
    expect(container).not.toHaveTextContent("Action:");
    expect(container).not.toHaveTextContent("Observation:");
  });

  test("Chinese model-side timeline events read like product progress, not raw model logs", () => {
    const { container } = wrap(
      [
        parentEvent({
          id: "thought-1",
          name: "agent_thought",
          status: "running",
          startedAt: 1000,
        }),
        parentEvent({
          id: "planning-1",
          name: "planning",
          status: "running",
          startedAt: 1100,
        }),
      ],
      { showAll: true },
      "zh-CN",
    );

    expect(
      screen.getByText("这一轮公开返回了可展示的整理片段。"),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/正在组织下一步/).length).toBeGreaterThan(0);
    expect(container).not.toHaveTextContent("模型");
    expect(container).not.toHaveTextContent("思考细节");
    expect(container).not.toHaveTextContent("reasoning");
  });

  test("sanitizes unknown tool labels and expanded payload details", () => {
    const { container } = wrap(
      [
        parentEvent({
          id: "unknown-1",
          name: "internal_private_runner",
          status: "done",
          startedAt: 1000,
          finishedAt: 1100,
          input: {
            token: "super-secret-token",
            nested: { authorization: "Bearer abc123" },
            path: "src/app.tsx",
          },
          output: { result: "ok", api_key: "sk-live-123456" },
        }),
      ],
      { showAll: true },
      "zh-CN",
    );

    expect(screen.getByText("其他操作")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("internal_private_runner");
    fireEvent.click(screen.getByRole("button", { name: "展开工具详情" }));
    expect(container).toHaveTextContent("[redacted]");
    expect(container).not.toHaveTextContent("super-secret-token");
    expect(container).not.toHaveTextContent("abc123");
    expect(container).not.toHaveTextContent("sk-live-123456");
  });

  test("consecutive research searches stay separate and show their own results", () => {
    wrap(
      [
        parentEvent({
          id: "search-1",
          name: "web_search",
          status: "done",
          startedAt: 1000,
          finishedAt: 1200,
          durationMs: 200,
          input: { query: "NAS 2026", max_results: 5 },
          output: {
            results: [
              { title: "Synology guide", url: "https://www.synology.com/a" },
              { title: "QNAP guide", url: "https://www.qnap.com/b" },
            ],
          },
        }),
        parentEvent({
          id: "search-2",
          name: "web_search",
          status: "done",
          startedAt: 1300,
          finishedAt: 1500,
          durationMs: 200,
          input: { query: "NAS market share", max_results: 5 },
          output: {
            results: [{ title: "Market", url: "https://example.com/market" }],
          },
        }),
      ],
      { showAll: true },
    );

    expect(screen.getByText("Found 2 web pages")).toBeInTheDocument();
    expect(screen.getByText("Found 1 web pages")).toBeInTheDocument();
    expect(screen.queryByText("Found 3 web pages")).toBeNull();
    expect(screen.getByText("Synology guide")).toBeInTheDocument();
    expect(screen.getByText("QNAP guide")).toBeInTheDocument();
    expect(screen.getByText("Market")).toBeInTheDocument();
  });

  test("long search result lists stay compact until expanded", () => {
    wrap(
      [
        parentEvent({
          id: "search-long",
          name: "web_search",
          status: "done",
          startedAt: 1000,
          finishedAt: 1200,
          input: { query: "OpenClaw", max_results: 8 },
          output: {
            results: Array.from({ length: 8 }, (_, index) => ({
              title: `Search result ${index + 1}`,
              url: `https://example.com/${index + 1}`,
            })),
          },
        }),
      ],
      { showAll: true },
    );

    expect(screen.getByText("Search result 1")).toBeInTheDocument();
    expect(screen.getByText("Search result 5")).toBeInTheDocument();
    expect(screen.queryByText("Search result 6")).toBeNull();

    fireEvent.click(screen.getByText("Show 3 more"));

    expect(screen.getByText("Search result 8")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Collapse results"));

    expect(screen.queryByText("Search result 6")).toBeNull();
  });

  test("empty events renders nothing (null, not an empty wrapper)", () => {
    const { container } = wrap([]);
    expect(container.firstChild).toBeNull();
  });
});

// ── Getter smoke tests for the internal helpers ──────────────
// Not exported · we validate the logic transitively through the
// component above. This is here as a reminder that the logic in
// ``getVisibleEvents``/``getChildren``/``getRunningEvents`` is
// covered by the rendering assertions rather than called directly.
describe("LiveToolTimeline · visibility rules (transitive via component)", () => {
  test("when both running and done events exist, running ones come first", () => {
    const events: LiveToolEvent[] = [
      parentEvent({
        id: "p-done-1",
        name: "call_agent",
        status: "done",
        startedAt: 100,
        finishedAt: 200,
      }),
      parentEvent({
        id: "p-running",
        name: "call_agent_parallel",
        status: "running",
        startedAt: 500,
      }),
    ];
    const { container } = wrap(events);
    // Top-level rows are the direct children of the root
    // <div class="space-y-1 py-1.5">.
    const root = container.querySelector(".space-y-1");
    expect(root).not.toBeNull();
    const rows = Array.from(root!.children);
    expect(rows.length).toBe(2);
    // First row should be the RUNNING one · read the tool name from
    // the ``font-medium`` label span. Testing order rather than
    // CSS class names because jsdom doesn't compute Tailwind styles.
    const firstLabel =
      rows[0]!.querySelector(".font-medium")?.textContent ?? "";
    expect(firstLabel).toContain("Dispatching parallel subtasks");
  });
});
