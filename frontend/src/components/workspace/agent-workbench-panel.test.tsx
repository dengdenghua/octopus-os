import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import {
  ThreadStreamingContext,
  ThreadValuesContext,
} from "@/components/workspace/messages/context";

import {
  AgentWorkbenchPanel,
  hasAgentWorkbenchContent,
} from "./agent-workbench-panel";
import { AGENT_WORKBENCH_FOCUS_EVENT } from "./agent-workbench-events";
import type { LiveToolEvent } from "./live-tool-timeline";
import { deriveAgentTilesFromEvents } from "./use-agent-workbench-i18n";

vi.mock("@/components/workspace/terminal-panel", () => ({
  TerminalPanel: ({ cwd, sessionId }: { cwd?: string; sessionId: string }) => (
    <div data-testid="mock-terminal-panel">
      Terminal {sessionId} {cwd}
    </div>
  ),
}));

vi.mock("./live-preview-panel", () => ({
  LivePreviewPanel: ({
    previewUrl,
    htmlContent,
  }: {
    previewUrl?: string | null;
    htmlContent?: string;
  }) => (
    <div
      data-testid="mock-live-preview"
      data-preview-url={previewUrl ?? ""}
      data-has-srcdoc={htmlContent ? "true" : "false"}
    />
  ),
}));

vi.mock("./browser-preview-panel", () => ({
  BrowserPreviewPanel: () => <div data-testid="mock-browser-preview" />,
}));

// Streamdown ships a package-level CSS side-effect. Rendering behavior is
// covered elsewhere; this workbench suite only needs deterministic text so a
// React.lazy import cannot escape Vitest's CSS transform after the test ends.
vi.mock("@/components/ai-elements/streamdown-host", () => ({
  default: ({ children }: { children?: ReactNode }) => <>{children}</>,
  LocalizedStreamdown: ({ children }: { children?: ReactNode }) => (
    <>{children}</>
  ),
}));

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

function renderWorkbench(ui: ReactElement) {
  return renderWithProviders(ui, { locale: "zh-CN" });
}

function expandSummarySection(name: RegExp) {
  const trigger = screen.getByRole("button", { name });
  if (trigger.getAttribute("aria-expanded") !== "true") {
    fireEvent.click(trigger);
  }
}

function listAfterSummaryLabel(label: string): HTMLElement {
  const labelElement = screen.getAllByText(label).find((element) => {
    const next = element.closest("div")?.nextElementSibling;
    return next instanceof HTMLElement && next.tagName.toLowerCase() === "ul";
  });
  expect(labelElement).toBeTruthy();
  return labelElement?.closest("div")?.nextElementSibling as HTMLElement;
}

describe("<AgentWorkbenchPanel />", () => {
  test("reports no workbench content for low-level transport events only", () => {
    expect(
      hasAgentWorkbenchContent([
        event({ id: "transport-1", name: "turn_request" }),
        event({ id: "transport-2", name: "response_stream" }),
      ]),
    ).toBe(false);
  });

  test("reports workbench content for visible work events", () => {
    expect(
      hasAgentWorkbenchContent([
        event({
          id: "search-1",
          name: "web_search",
          input: { query: "AI market" },
        }),
      ]),
    ).toBe(true);
  });

  test("renders an empty shell for low-level transport events only", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="subagents"
        events={[
          event({ id: "transport-1", name: "turn_request" }),
          event({ id: "transport-2", name: "response_stream" }),
        ]}
      />,
    );

    expect(
      screen.getByRole("button", { name: "主电脑 · 等待中" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Diff/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /终端/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /浏览器/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("当前还没有活跃的协作过程。")).toBeInTheDocument();
    expect(screen.queryByText("等待开机")).not.toBeInTheDocument();
  });

  test("keeps a visible close control in the workbench header", () => {
    const onClose = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel activeTab="agent" events={[]} onClose={onClose} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭工作台" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("lets temporary workbench tabs close themselves", () => {
    const onSelectTab = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="diff"
        onSelectTab={onSelectTab}
        events={[
          event({
            id: "diff-tab-file",
            name: "write_file",
            input: {
              changes: [{ path: "src/app.tsx", op: "update", diff: "+change" }],
            },
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭标签页：Diff" }));
    expect(onSelectTab).toHaveBeenCalledWith("agent");
    expect(
      screen.queryByRole("button", { name: "关闭标签页：协作工作台" }),
    ).not.toBeInTheDocument();
  });

  test("hides capability decisions when the turn has no decision trace", () => {
    renderWorkbench(<AgentWorkbenchPanel activeTab="agent" events={[]} />);

    expect(screen.queryByText("能力决策")).not.toBeInTheDocument();
    expect(screen.queryByText("本轮暂无能力决策")).not.toBeInTheDocument();
  });

  test("keeps explanatory capability decisions out of the persistent workbench UI", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        isLoading
        events={[
          event({
            id: "legacy-noise",
            name: "grep_text",
            output: "oklch(0.55 0.13 215) globals.cs runtime/wrong.py",
          }),
          event({
            id: "visibility-1",
            name: "visibility",
            input: {
              summary: "本轮能力路由 / 委派 / 技能目录决策",
              steps: [
                {
                  decision_point: "context.delegation_cap",
                  conclusion: "委派工具隐藏",
                  basis: "未命中委派条件",
                },
                {
                  decision_point: "context.skill_catalog",
                  conclusion: "技能目录 42 条（未截断）",
                  basis: "总数未超过上限",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /能力决策/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("委派工具隐藏")).not.toBeInTheDocument();
    expect(
      screen.queryByText("技能目录 42 条（未截断）"),
    ).not.toBeInTheDocument();
  });

  test("surfaces only capability decisions that need user attention", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "visibility-action-required",
            name: "visibility",
            input: {
              summary: "本轮能力路由 / 权限决策",
              steps: [
                {
                  decision_point: "context.skill_catalog",
                  conclusion: "技能目录已加载",
                  basis: "能力检查已完成",
                },
                {
                  decision_point: "permission.browser",
                  conclusion: "需要授权浏览器控制",
                  basis: "当前浏览器控制权限未开启",
                  details: {
                    status: "permission_required",
                    requires_user_action: true,
                  },
                },
              ],
            },
          }),
        ]}
      />,
    );

    const trigger = screen.getByRole("button", { name: /能力决策/ });
    expect(trigger).toHaveTextContent("需要处理");
    expect(trigger).toHaveTextContent("1");
    fireEvent.click(trigger);

    expect(screen.getByText("决策 1")).toBeInTheDocument();
    expect(screen.getByText("需要授权浏览器控制")).toBeInTheDocument();
    expect(screen.getByText("当前浏览器控制权限未开启")).toBeInTheDocument();
    expect(screen.queryByText("技能目录已加载")).not.toBeInTheDocument();
    expect(screen.queryByText("permission.browser")).not.toBeInTheDocument();
    expect(
      screen.queryByText("本轮能力路由 / 权限决策"),
    ).not.toBeInTheDocument();
  });

  test("keeps free-form todo phase titles visible", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            input: {
              items: [
                { content: "阅读鉴权模块源码", status: "completed" },
                { content: "修改登录页实现", status: "in_progress" },
              ],
            },
          }),
        ]}
      />,
    );

    // The visible plan remains specific to this task rather than replacing
    // every row with a generic business bucket label.
    const taskPlan = screen.getByTestId("workbench-task-plan");
    expect(taskPlan).toHaveTextContent("阅读鉴权模块源码");
    expect(taskPlan).toHaveTextContent("修改登录页实现");
    expect(screen.getByTitle("阅读鉴权模块源码")).toBeInTheDocument();
    expect(screen.getByTitle("修改登录页实现")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /待办事项.*1\/2.*进行中/ }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("workbench-todo-list")).toHaveClass(
      "max-h-72",
      "overflow-y-auto",
    );
    expect(taskPlan).toHaveClass("border-b");
    expect(taskPlan.querySelector(".h-1")).toBeNull();
    expect(screen.queryByText("P1")).not.toBeInTheDocument();
    const todoRows = within(
      screen.getByTestId("workbench-todo-list"),
    ).getAllByRole("listitem");
    expect(todoRows[0]).toHaveAttribute("data-task-status", "done");
    expect(todoRows[1]).toHaveAttribute("data-task-status", "running");
  });

  test("keeps a planned active run focused on the task list", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "todo-focused-active",
            name: "todo_write",
            input: {
              items: [
                { content: "整理创作需求", status: "completed" },
                { content: "生成主视觉", status: "in_progress" },
                { content: "整理交付物", status: "pending" },
              ],
            },
          }),
          event({
            id: "active-output",
            name: "write_file",
            input: {
              changes: [
                {
                  path: "output/brief.md",
                  op: "create",
                  diff: "+# Brief",
                },
              ],
            },
          }),
        ]}
      />,
    );

    const progress = screen.getByRole("button", { name: /待办事项.*1\/3/ });
    expect(progress).not.toHaveTextContent("产物");
    expect(screen.queryByTestId("workbench-current-objective")).toBeNull();
    expect(screen.queryByTestId("workbench-result-receipt")).toBeNull();
    expect(screen.queryByRole("heading", { name: "产物" })).toBeNull();
    expect(screen.getByRole("button", { name: /^上下文/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  test("keeps the todo list visible after a transcript process event is focused", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        focusedEventId="search-2"
        focusedEventKind="execution"
        focusedEventView="summary"
        focusedEventNonce={1}
        focusedProcessEvent={{
          kind: "execution",
          summary: "已完成市场搜索",
          detail: "搜索结果已返回，继续整理厂商清单。",
          status: "done",
          count: 1,
        }}
        events={[
          event({
            id: "todo-focused",
            name: "todo_write",
            input: {
              items: [
                { content: "规划调研框架", status: "completed" },
                { content: "收集市场数据", status: "in_progress" },
                { content: "整理完整报告", status: "pending" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByTestId("workbench-todo-list")).toBeInTheDocument();
    const taskPlan = screen.getByTestId("workbench-task-plan");
    expect(taskPlan).toHaveTextContent("规划调研框架");
    expect(taskPlan).toHaveTextContent("收集市场数据");
    expect(taskPlan).toHaveTextContent("整理完整报告");
  });

  test("renders the main agent workstation dock placeholder", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
            output: "const value = 1;",
          }),
        ]}
      />,
    );

    expect(
      screen.getByRole("button", { name: "主电脑 · 已完成" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("工位")).not.toBeInTheDocument();
  });

  test("does not expose an internal thread id as the workspace label", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "report-1",
            name: "read_file",
            input: {
              path: "/tmp/data/workspaces/thread-internal-1/output/final/report.md",
            },
          }),
        ]}
        threadId="thread-internal-1"
        workDir="/tmp/data/workspaces/thread-internal-1"
        runSettled
      />,
    );

    expect(screen.queryByText("thread-internal-1")).not.toBeInTheDocument();
    const workbenchHeader = screen.getByRole("banner");
    expect(
      within(workbenchHeader).getByTitle("主电脑 · 已完成"),
    ).toBeInTheDocument();
  });

  test("empty shell says the controller is idle when no turn is running", () => {
    renderWorkbench(<AgentWorkbenchPanel activeTab="agent" events={[]} />);
    expect(screen.getByText("当前还没有活跃的协作过程。")).toBeInTheDocument();
  });

  test("empty shell marks a settled answer as completed", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        hasAnswer
        runSettled
      />,
    );
    expect(
      screen.getByRole("button", { name: "主电脑 · 已完成" }),
    ).toBeInTheDocument();
  });

  test("shows an interrupted turn honestly instead of claiming completion", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "todo-interrupted",
            name: "todo_write",
            input: {
              items: [
                { content: "读取相关文件", status: "completed" },
                { content: "核对中断状态", status: "completed" },
              ],
            },
          }),
        ]}
        runSettled
        runInterrupted
      />,
    );

    expect(
      screen.getByRole("button", { name: "主电脑 · 已中断" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /(?:待办事项|进展).*1\/2.*已完成.*已中断/,
      }),
    ).toBeInTheDocument();
    const interruptedTodos = within(
      screen.getByTestId("workbench-todo-list"),
    ).getAllByRole("listitem");
    expect(interruptedTodos[0]).toHaveAttribute("data-task-status", "done");
    expect(interruptedTodos[1]).toHaveAttribute("data-task-status", "warning");
    expect(interruptedTodos[1]).toHaveTextContent("已中断");
    expect(
      screen.queryByRole("button", { name: "主电脑 · 已完成" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("最新一轮")).not.toBeInTheDocument();
  });

  test("empty shell admits the turn is live before the first tool event", () => {
    // The panel is event-driven, so a turn that has started but not yet run
    // a tool leaves it with zero blocks. It must not claim nothing is
    // running — that window is exactly when the user is watching for signs
    // of life.
    renderWorkbench(
      <AgentWorkbenchPanel activeTab="agent" events={[]} isLoading />,
    );
    expect(
      screen.getByText("已进入协作现场，正在等待第一个可见动作…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("当前还没有活跃的协作过程。"),
    ).not.toBeInTheDocument();
  });

  test("keeps a compact task marker when a turn has no tool events", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        hasAnswer
        progressOutline={[
          {
            iteration: 1,
            intentText: "核对本轮上下文",
            executionCount: 0,
            facts: ["本轮已返回一条具体结论"],
          },
        ]}
        runSettled
      />,
    );

    expect(
      screen.getByRole("button", { name: "主电脑 · 已完成" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /进展/ })).toBeInTheDocument();
    expect(screen.getByText("T1")).toBeInTheDocument();
    expect(screen.queryByText("核对本轮上下文")).not.toBeInTheDocument();
    expect(
      screen.queryByText("本轮已返回一条具体结论"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("1 条确认")).toHaveLength(2);
    expect(
      screen.queryByText("当前还没有活跃的协作过程。"),
    ).not.toBeInTheDocument();
  });

  test("renders invited collaborators as workstation seats before they run", () => {
    const { container } = renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        rosterSeats={[
          {
            id: "general",
            name: "Eve",
            role: "tl",
            avatarUrl: "/api/agents/general/avatar",
          },
          { id: "codex-cli", name: "Codex CLI", role: "member" },
          { id: "claude-code", name: "Claude Code", role: "member" },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Eve · 群主" })).toHaveAttribute(
      "title",
      "Eve · 群主",
    );
    expect(
      container.querySelector('button[aria-label="Eve · 群主"] img'),
    ).toHaveAttribute("src", "/api/agents/general/avatar");
    expect(screen.getByText("群主")).toBeInTheDocument();
    expect(screen.queryByText("工位")).not.toBeInTheDocument();
    const workbenchHeader = screen.getByRole("banner");
    expect(
      within(workbenchHeader).queryByRole("button", {
        name: "Codex CLI · 协作 · 在场",
      }),
    ).not.toBeInTheDocument();
    const bottomRail = screen.getByTestId("workstation-bottom-rail");
    const codexSeat = within(bottomRail).getByRole("button", {
      name: "Codex CLI · 协作 · 在场",
    });
    expect(codexSeat).toHaveAttribute("title", "Codex CLI · 协作 · 在场");
    expect(
      screen.getByRole("button", { name: "Claude Code · 协作 · 在场" }),
    ).toHaveAttribute("title", "Claude Code · 协作 · 在场");
    expect(screen.queryByText("Codex CLI")).not.toBeInTheDocument();
    expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
    expect(screen.queryByText("协作")).not.toBeInTheDocument();

    fireEvent.click(codexSeat);
    expect(screen.getByRole("button", { name: "概要" })).toHaveClass(
      "border-foreground/70",
    );
    expect(screen.queryByText("暂无独立进程活动")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "执行画面" }),
    ).not.toBeInTheDocument();

    const mainComputerButton = screen.getByRole("button", {
      name: "主电脑 · 等待中",
    });
    expect(mainComputerButton).toHaveClass("border-warning/40");

    fireEvent.click(mainComputerButton);

    expect(screen.queryByText("暂无独立进程活动")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Eve · 群主" }),
    ).toBeInTheDocument();
  });

  test("keeps human room participants out of the Agent machine rail", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        rosterSeats={[
          { id: "general", name: "Eve", role: "tl", kind: "agent" },
          { id: "coder", name: "Coder", role: "member", kind: "agent" },
          { id: "local", name: "Local user", role: "群主", kind: "human" },
        ]}
      />,
    );

    const bottomRail = screen.getByTestId("workstation-bottom-rail");
    expect(
      within(bottomRail).getByRole("button", {
        name: "Coder · 协作 · 在场",
      }),
    ).toBeInTheDocument();
    expect(within(bottomRail).queryByText("Local user")).toBeNull();
  });

  test("can move team roster avatars out while retaining runtime machine seats", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        showMachineRosterRail={false}
        rosterSeats={[
          { id: "general", name: "Eve", role: "tl", kind: "agent" },
          { id: "coder", name: "Coder", role: "member", kind: "agent" },
        ]}
      />,
    );

    expect(screen.queryByTestId("workstation-bottom-rail")).toBeNull();
    expect(screen.queryByRole("button", { name: /Eve · 群主/ })).toBeNull();
  });

  test("uses the leader avatar for the main workstation in solo mode", () => {
    const { container } = renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[]}
        rosterSeats={[
          {
            id: "general",
            name: "Eve",
            role: "tl",
            avatarUrl: "/api/agents/general/avatar",
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Eve" })).toHaveAttribute(
      "title",
      "Eve",
    );
    expect(
      container.querySelector('button[aria-label="Eve"] img'),
    ).toHaveAttribute("src", "/api/agents/general/avatar");
    expect(screen.queryByText("群主")).not.toBeInTheDocument();
    expect(screen.queryByText("工位")).not.toBeInTheDocument();
  });

  test("renders dispatched subagent seats before lifecycle events arrive", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "parent-call-1",
            name: "call_agent_parallel",
            status: "running",
            input: {
              specs: [
                { agent_id: "researcher", prompt: "pricing lane" },
                { agent_id: "reviewer", prompt: "risk lane" },
                { agent_id: "writer", prompt: "summary lane" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByTitle("researcher: pricing lane")).toBeInTheDocument();
    expect(screen.getByTitle("reviewer: risk lane")).toBeInTheDocument();
    const writerSeat = screen.getByTitle("writer: summary lane");
    expect(writerSeat).toBeInTheDocument();

    fireEvent.click(writerSeat);

    // Clicking a sub-role lands on its nameplate (工牌): the role's identity,
    // not the turn's delegated brief.
    expect(screen.getAllByText("writer").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Drafts clear, well-structured prose and deliverables."),
    ).toBeInTheDocument();
    expect(screen.queryByText("summary lane")).not.toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: "执行画面" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("summary lane")).not.toBeInTheDocument();
  });

  test("merges dispatch specs with same-role runtime lifecycle by requested id", () => {
    const tiles = deriveAgentTilesFromEvents([
      event({
        id: "parallel-call-1",
        name: "call_agent_parallel",
        status: "running",
        input: {
          specs: [
            { agent_id: "reader_readme", prompt: "read README" },
            { agent_id: "reader_pyproject", prompt: "read pyproject" },
          ],
        },
      }),
      event({
        id: "spawn-readme",
        name: "subagent",
        lifecycle: "spawned",
        status: "running",
        agentId: "reader_readme",
        subAgentRole: "explorer",
        subagentCodename: "Spark-4f6",
        parentToolUseId: "parallel-call-1",
      }),
      event({
        id: "finish-readme",
        name: "subagent",
        lifecycle: "finished",
        status: "done",
        agentId: "reader_readme",
        subAgentRole: "explorer",
        subagentCodename: "Spark-4f6",
        parentToolUseId: "parallel-call-1",
      }),
      event({
        id: "spawn-pyproject",
        name: "subagent",
        lifecycle: "spawned",
        status: "running",
        agentId: "reader_pyproject",
        subAgentRole: "explorer",
        subagentCodename: "Aurora-108",
        parentToolUseId: "parallel-call-1",
      }),
    ]);

    expect(tiles).toHaveLength(2);
    expect(tiles.map((tile) => tile.id)).toEqual([
      "reader_readme",
      "reader_pyproject",
    ]);
    expect(tiles.map((tile) => tile.codename)).toEqual([
      "Spark-4f6",
      "Aurora-108",
    ]);
  });

  test("keeps an agent running when one of its child tools settles", () => {
    const active = deriveAgentTilesFromEvents([
      event({
        id: "spawn-reader",
        name: "subagent",
        lifecycle: "spawned",
        status: "running",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
      }),
      event({
        id: "report-reader",
        name: "report",
        status: "done",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
      }),
      event({
        id: "read-reader",
        name: "read_file",
        status: "error",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
        output: "missing file",
      }),
    ]);

    expect(active).toHaveLength(1);
    expect(active[0]?.status).toBe("running");
    expect(active[0]?.error).toBeUndefined();

    const finished = deriveAgentTilesFromEvents([
      ...[
        event({
          id: "spawn-reader",
          name: "subagent",
          lifecycle: "spawned",
          status: "running",
          agentId: "reader",
          subagentCodename: "Quark-d9c",
        }),
        event({
          id: "report-reader",
          name: "report",
          status: "done",
          agentId: "reader",
          subagentCodename: "Quark-d9c",
        }),
      ],
      event({
        id: "finish-reader",
        name: "subagent",
        lifecycle: "finished",
        status: "done",
        agentId: "reader",
        subagentCodename: "Quark-d9c",
      }),
    ]);
    expect(finished[0]?.status).toBe("done");
  });

  test("does not create workbench seats for a rejected zero-spawn batch", () => {
    expect(
      deriveAgentTilesFromEvents([
        event({
          id: "parallel-invalid",
          name: "call_agent_parallel",
          status: "error",
          input: {
            specs: [
              { role: "reader", goal: "read README" },
              { role: "reader", goal: "read pyproject" },
            ],
          },
          output:
            '(工具失败) status=success error=structured_error\n{"ok":false,"count":0}',
        }),
      ]),
    ).toEqual([]);
  });

  test("keeps failed auto-parallel bootstrap attempts out of Agent seats", () => {
    expect(
      deriveAgentTilesFromEvents([
        event({
          id: "auto-probe-failure",
          name: "subagent",
          status: "error",
          agentId: "task-auto-failed",
          subAgentRole: "general-purpose",
          output: { error: "empty_result_contract_violation" },
        }),
      ]),
    ).toEqual([]);
  });

  test("does not mark a successful settled run anomalous for internal bootstrap failures", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "auto-probe-failure",
            name: "subagent",
            status: "error",
            iteration: 1,
            agentId: "task-auto-failed",
            subAgentRole: "general-purpose",
            output: { error: "empty_result_contract_violation" },
          }),
          event({
            id: "server-phases:turn-1",
            name: "todo_write",
            status: "error",
            iteration: 1,
            input: {
              source: "turn.phases",
              items: [
                {
                  content: "Gather context",
                  status: "error",
                  phaseId: "phase-1",
                  index: 1,
                  total: 2,
                },
              ],
            },
          }),
          event({
            id: "child-read-retry",
            name: "read_file",
            status: "error",
            iteration: 1,
            agentId: "reader-a",
            subAgentRole: "explorer",
            input: {
              server: "subagent",
              tool: "read_file",
              arguments: { path: "README.md", limit: "1" },
            },
            output: { error: "invalid limit" },
          }),
          event({
            id: "explicit-agent-finished",
            name: "subagent",
            status: "done",
            iteration: 2,
            lifecycle: "finished",
            agentId: "reader-a",
            subAgentRole: "explorer",
            subagentCodename: "Volt-01",
          }),
        ]}
      />,
    );

    const progressButton = screen.getByRole("button", {
      name: /(?:进展|待办事项) 2\/2 已完成/,
    });
    expect(progressButton).not.toHaveTextContent("异常");
  });

  test("keeps the main workstation status independent from subagent failures", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "main-approval",
            name: "write_text_file",
            status: "waiting_approval",
            input: { path: "docs/notes.md" },
          }),
          event({
            id: "agent-error",
            name: "read_file",
            status: "error",
            parentToolUseId: "dispatch-1",
            subAgentRole: "reviewer",
            subagentCodename: "Review-03",
            input: { path: "missing/replay.json" },
            output: { error: "Replay artifact was not found" },
          }),
        ]}
      />,
    );

    expect(screen.getByTitle("主电脑 · 待确认")).toBeInTheDocument();
    expect(screen.queryByTitle("主电脑 · 遇到问题")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "查看 Review-03 独立进程" }),
    );

    // Clicking a sub-role lands on its nameplate (工牌) first.
    expect(screen.getByText("Agent 集群 - 创建助手")).toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: "执行画面" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Agent 集群 - 独立进程")).not.toBeInTheDocument();
  });

  test("surfaces call_agent_parallel result outputs and failures", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "parent-call-1",
            name: "call_agent_parallel",
            status: "done",
            input: {
              specs: [
                { agent_id: "researcher", prompt: "pricing lane" },
                { agent_id: "reviewer", prompt: "risk lane" },
              ],
            },
            output: {
              ok: true,
              partial: true,
              successes: [
                {
                  agent_id: "researcher",
                  spec_index: 0,
                  task_label: "pricing lane",
                  output: "Pricing lane result is ready.",
                  iteration_count: 4,
                },
              ],
              failures: [
                {
                  agent_id: "reviewer",
                  spec_index: 1,
                  task_label: "risk lane",
                  error: "ROUND_CAP_EXCEEDED",
                  error_type: "round_cap_exceeded",
                  partial_output: "Risk lane partial notes.",
                  rounds_completed: 25,
                  round_cap_exceeded: true,
                },
              ],
            },
          }),
        ]}
      />,
    );

    expandSummarySection(/子智能体/);

    expect(
      screen.getByText("Pricing lane result is ready."),
    ).toBeInTheDocument();
    expect(screen.getByText("ROUND_CAP_EXCEEDED")).toBeInTheDocument();
    expect(screen.getAllByText("1/2 已完成").length).toBeGreaterThan(0);
    expect(screen.getByText("1 异常")).toBeInTheDocument();
    expect(screen.getByText(/失败 lane: risk lane/)).toBeInTheDocument();
  });

  test("hides the workstation dock on tool tabs", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="terminal"
        events={[
          event({
            id: "shell-1",
            name: "shell_command",
            input: { command: "pnpm typecheck", cwd: "F:\\repo" },
            output: "Done in 10s",
          }),
        ]}
      />,
    );

    expect(screen.getByTestId("mock-terminal-panel")).toBeInTheDocument();
    expect(screen.queryByText("工位")).not.toBeInTheDocument();
  });

  test("renders readable work steps and selected tool details", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({ id: "transport-1", name: "stream_connection" }),
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
            output: "const value = 1;",
          }),
          event({
            id: "shell-1",
            name: "shell_command",
            status: "running",
            startedAt: 2000,
            input: { command: "npm run typecheck" },
          }),
          event({
            id: "child-1",
            name: "grep",
            parentToolUseId: "shell-1",
            input: { pattern: "Agent Workspace" },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("tablist", { name: /看板/ })).toBeInTheDocument();
    expandSummarySection(/(?:待办事项|进展)/);
    expect(screen.getAllByText("处理线索").length).toBeGreaterThan(0);
    expect(screen.queryByText("电脑视图")).not.toBeInTheDocument();
    expect(screen.getByTitle("主电脑 · 执行任务中...")).toBeInTheDocument();

    expect(screen.queryByText("活动轨迹")).not.toBeInTheDocument();
  });

  test("groups screen frames by phase while keeping phase titles visible", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
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
                    title: "Phase 2: Run tests",
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
                    title: "Phase 2: Run tests",
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
        ]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    // Backend phase titles stay visible after their machine prefix is removed.
    const taskPlan = screen.getByTestId("workbench-task-plan");
    expect(taskPlan).toHaveTextContent(/Read context/);
    expect(taskPlan).toHaveTextContent(/Run tests/);
    expect(screen.getByTitle(/Read context/)).toBeInTheDocument();
    expect(screen.getByTitle(/Run tests/)).toBeInTheDocument();

    expect(screen.queryByText("电脑视图")).not.toBeInTheDocument();
  });

  test("shows verification-required audit as waiting instead of many failed reads", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-package",
            name: "read_file",
            status: "done",
            input: { path: "package.json" },
          }),
          event({
            id: "read-context",
            name: "read_file",
            status: "done",
            startedAt: 1200,
            input: { path: "src/context.tsx" },
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
        ]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getByTestId("workbench-task-plan")).toHaveTextContent(
      /补齐上下文/,
    );
    expect(screen.getByTestId("workbench-task-plan")).toHaveTextContent(
      /收拢答案/,
    );
    expect(screen.getByTitle("主电脑 · 待确认")).toBeInTheDocument();
    expect(screen.queryByTitle("主电脑 · 遇到问题")).not.toBeInTheDocument();

    expect(screen.queryByText("电脑视图")).not.toBeInTheDocument();
  });

  test("prefers compact phase tasks over a duplicated narrative outline", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
            output: "const value = 1;",
          }),
        ]}
        progressOutline={[
          {
            iteration: 1,
            intentText: "先查看项目结构",
            executionCount: 2,
            facts: ["已确认入口文件位置"],
          },
          {
            iteration: 2,
            intentText: "修复构建错误",
            executionCount: 1,
            facts: ["已确认配置存在"],
          },
          {
            iteration: 3,
            intentText: "运行测试验证",
            executionCount: 3,
            facts: ["测试全部通过"],
          },
        ]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-task-plan")).toHaveTextContent(
      /补齐上下文|了解代码结构/,
    );
    expect(screen.queryByText("先查看项目结构")).not.toBeInTheDocument();
    expect(screen.queryByText("修复构建错误")).not.toBeInTheDocument();
    expect(screen.queryByText("运行测试验证")).not.toBeInTheDocument();
    expect(screen.queryByText("已确认入口文件位置")).not.toBeInTheDocument();
    expect(screen.queryByText("已确认配置存在")).not.toBeInTheDocument();
    expect(screen.queryByText("测试全部通过")).not.toBeInTheDocument();
  });

  test("falls back to the phase list when the progress outline is empty", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
            output: "const value = 1;",
          }),
        ]}
        progressOutline={[]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getByTestId("workbench-task-plan")).toHaveTextContent(
      /补齐上下文|收拢答案|Read context/,
    );
  });

  test("shows recovered tool failures as warnings instead of failing the phase", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-package",
            name: "read_file",
            status: "done",
            input: { path: "frontend/package.json" },
          }),
          event({
            id: "read-page-failed",
            name: "read_file",
            status: "error",
            startedAt: 1200,
            input: { path: "frontend/src/app/workspace/page.tsx" },
            output:
              "(工具失败) status=failed error=TypeError\n请在下一轮 Thought 中分析失败原因，然后换一种方式重试",
          }),
          event({
            id: "fallback-read",
            name: "ipython",
            status: "done",
            startedAt: 1400,
            input: { command: "read via pathlib" },
            output: "frontend/src/app/workspace/page.tsx",
          }),
        ]}
        hasAnswer
        runSettled
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getByText(/补齐上下文/)).toBeInTheDocument();
    expect(screen.getByTitle("主电脑 · 已完成")).toBeInTheDocument();
    expect(screen.queryByTitle("主电脑 · 遇到问题")).not.toBeInTheDocument();

    expect(screen.queryByText("电脑视图")).not.toBeInTheDocument();
  });

  test("shows only observed context categories in the summary", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "read-context-1",
            name: "read_file",
            input: { path: "src/context.ts" },
            output: "export const context = true;",
          }),
          event({
            id: "write-context-1",
            name: "write_file",
            startedAt: 1500,
            input: {
              changes: [
                { path: "reports/market_report.md", op: "create" },
                { path: "scripts/analyze.py", op: "create" },
              ],
            },
          }),
          event({
            id: "todo-context-1",
            name: "todo_write",
            startedAt: 1750,
            input: {
              todos: [
                {
                  content: "Draft a context plan",
                  status: "pending",
                },
              ],
            },
          }),
          event({
            id: "search-context-1",
            name: "web_search",
            startedAt: 2000,
            input: { query: "AI market" },
            output: {
              results: [
                {
                  title: "AI Market Size Report",
                  url: "https://example.com/ai-market-size",
                  snippet: "Market sizing overview",
                },
                {
                  title: "Industry Forecast",
                  url: "https://research.example.org/forecast",
                },
              ],
            },
          }),
          event({
            id: "search-context-2",
            name: "web_search",
            startedAt: 2250,
            input: { query: "sleep tech" },
            output:
              '(real tool execution succeeded) web_search\n{"query":"sleep tech","backend":"ddg","results":[{"title":"Eight Sleep raises $50M","url":"https://techcrunch.com/eight-sleep-funding","snippet":"Funding news"},{"title":"Oura Ring 5 review","url":"https://www.tomsguide.com/reviews/oura-ring-5"}]}',
          }),
          event({
            id: "search-context-3",
            name: "web_search",
            startedAt: 2350,
            input: {
              query: "企业级AI Agent工作流自动化市场规模 2025 2026",
            },
            output:
              '(real tool execution succeeded) web_search\n{"query": "企业级AI Agent工作流自动化市场规模 2025 2026", "backend": "ddg", "results": [{"title": "企业 AI Agent 落地现状深度调研：从技术 Demo 到&quot;数字员工&quot;规模化实战【2026】 | QubitTool", "url": "https://qubittool.com/zh/blog/enterprise-ai-agent-status-2026", "snipp …(已截断)',
          }),
          event({
            id: "shell-context-1",
            name: "shell_command",
            startedAt: 2500,
            input: { command: "pnpm typecheck" },
            output: "Done in 10s",
          }),
        ]}
      />,
    );

    expandSummarySection(/上下文/);

    expect(screen.getByText("\u4e0a\u4e0b\u6587")).toBeInTheDocument();
    expect(screen.queryByText("Repo Wiki")).not.toBeInTheDocument();
    expect(screen.queryByText("\u77e5\u8bc6\u5361")).not.toBeInTheDocument();
    expect(screen.queryByText("\u8bb0\u5fc6")).not.toBeInTheDocument();
    expect(screen.queryByText("todo_write")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /\u5f85\u529e plan/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "\u7ec8\u7aef 1 \u6761\u6765\u6e90",
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "\u5176\u4ed6 1 \u6761\u6765\u6e90",
      }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("context.ts")).toBeInTheDocument();
    expect(screen.getByText("market_report.md")).toBeInTheDocument();
    expect(screen.getByText("analyze.py")).toBeInTheDocument();
    expect(screen.getByText("MD")).toBeInTheDocument();
    expect(screen.getByText("PY")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: "\u641c\u7d22/\u7f51\u9875 5 \u6761\u6765\u6e90",
      }),
    );
    expect(screen.getByText("AI Market Size Report")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /AI Market Size Report/ }),
    ).toHaveAttribute("href", "https://example.com/ai-market-size");
    expect(
      screen.queryByText("https://example.com/ai-market-size"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Industry Forecast")).toBeInTheDocument();
    expect(screen.getByText("Eight Sleep raises $50M")).toBeInTheDocument();
    expect(
      screen.queryByText("https://techcrunch.com/eight-sleep-funding"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Oura Ring 5 review")).toBeInTheDocument();
    expect(
      screen.getByText(
        '企业 AI Agent 落地现状深度调研：从技术 Demo 到"数字员工"规模化实战【2026】 | QubitTool',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "https://qubittool.com/zh/blog/enterprise-ai-agent-status-2026",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("AI market")).not.toBeInTheDocument();
    expect(screen.queryByText("sleep tech")).not.toBeInTheDocument();
  });

  test("shows confirmed local file-search matches as file context", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "glob-context-1",
            name: "glob_files",
            input: { pattern: "**/approval_gate.py", path: "." },
            output: {
              files: ["runtime/safety/approval/approval_gate.py"],
            },
          }),
          event({
            id: "grep-context-1",
            name: "grep_text",
            startedAt: 1250,
            input: { pattern: "waiting_user" },
            output: [
              {
                path: "runtime/sensing/gateway/computer_control_session.py",
                line: 131,
              },
            ],
          }),
          event({
            id: "empty-search-context-1",
            name: "search_files",
            startedAt: 1500,
            input: { pattern: "not-a-source" },
            output: [],
          }),
        ]}
      />,
    );

    expandSummarySection(/上下文/);

    expect(screen.getByText("approval_gate.py")).toBeInTheDocument();
    expect(screen.getByText("computer_control_session.py")).toBeInTheDocument();
    expect(screen.queryByText("not-a-source")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "文件 2 条来源" }),
    ).toBeInTheDocument();
  });

  test("shows sources injected before tool execution in context", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "todo-only",
            name: "todo_write",
            input: { todos: [{ content: "Inspect", status: "in_progress" }] },
          }),
        ]}
        groundingSources={[
          {
            kind: "source",
            title: "approval_gate.py",
            path: "runtime/safety/approval/approval_gate.py:44",
          },
          {
            kind: "doc",
            title: "Runtime architecture",
            path: "docs/runtime-architecture.md",
          },
        ]}
      />,
    );

    expandSummarySection(/上下文/);

    expect(screen.getByText("approval_gate.py")).toBeInTheDocument();
    expect(screen.getByText("Runtime architecture")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "文件 2 条来源" }),
    ).toBeInTheDocument();
  });

  test("shows typed workbench evidence without parsing tool output", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "snapshot-evidence",
            name: "todo_write",
            input: {
              workbenchSnapshot: {
                schemaVersion: 2,
                version: 3,
                status: "running",
                phases: [],
                evidence: [
                  {
                    id: "tool:read-1:runtime/protocol/items.py",
                    kind: "file",
                    title: "items.py",
                    uri: "runtime/protocol/items.py",
                    status: "observed",
                    origin: "tool",
                    sourceItemId: "read-1",
                  },
                ],
                updatedAt: "2026-08-12T00:00:00.000Z",
              },
            },
          }),
        ]}
      />,
    );

    expandSummarySection(/上下文/);

    expect(screen.getByText("items.py")).toBeInTheDocument();
    expect(screen.queryByText("wrong.py")).not.toBeInTheDocument();
    expect(screen.queryByText("globals.cs")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "文件 1 条来源" }),
    ).toBeInTheDocument();
  });

  test("counts user-fed context files (uploaded + attachments) in context stats", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "read-context-1",
            name: "read_file",
            input: { path: "src/app.ts" },
            output: "const app = 1;",
          }),
        ]}
        userInput={{
          text: "分析这些文件",
          uploadedFiles: [
            { filename: "report.pdf", path: "uploads/report.pdf" },
            { filename: "data.csv", path: "uploads/data.csv" },
          ],
          attachments: [{ filename: "spec.md" }],
        }}
      />,
    );

    expandSummarySection(/上下文/);

    // 喂入的上下文文件应出现在 files 分类中
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("data.csv")).toBeInTheDocument();
    expect(screen.getByText("spec.md")).toBeInTheDocument();

    // 上传文件/附件 + 过程件 read_file 应合计为 4 条文件来源
    expect(
      screen.getByRole("button", {
        name: /\u6587\u4ef6 4 \u6761\u6765\u6e90/,
      }),
    ).toBeInTheDocument();
  });

  test("uses the real context window and exposes compression in the summary", () => {
    const onCompressContext = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        events={[
          event({
            id: "read-context-window",
            name: "read_file",
            input: { path: "src/context-window.ts" },
            output: "export const contextWindow = true;",
          }),
        ]}
        contextTokens={118_000}
        maxContextTokens={200_000}
        onCompressContext={onCompressContext}
      />,
    );

    expandSummarySection(/上下文/);

    expect(
      screen.getByLabelText("当前对话中 AI 获取的上下文"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "已占用 59%（上限 200K）",
      }),
    ).toHaveTextContent("59%");
    expect(
      screen.getByTestId("workbench-context-usage-bar").firstElementChild,
    ).toHaveStyle({ width: "59%" });

    fireEvent.click(screen.getByRole("button", { name: "压缩" }));
    expect(onCompressContext).toHaveBeenCalledTimes(1);
  });

  test("surfaces real sub-agents as task cards", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="subagents"
        events={[
          event({
            id: "agent-1",
            name: "subagent_spawned",
            agentId: "agent-1",
            agentName: "Researcher",
            subAgentRole: "researcher",
            subagentCodename: "Spark-01",
            thought: "Collect background sources",
            status: "running",
          }),
          event({
            id: "agent-2",
            name: "subagent_finished",
            agentId: "agent-2",
            agentName: "Writer",
            subAgentRole: "writer",
            subagentCodename: "Spark-02",
            observation: "Draft completed",
            status: "done",
            startedAt: 2000,
          }),
        ]}
      />,
    );

    // Summary page shows agent labels (codenames) inside the roster section
    expect(
      screen.queryByRole("tab", { name: "\u5b50\u667a\u80fd\u4f53" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /子智能体/ }));
    expect(screen.getAllByText("Spark-01").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Spark-02").length).toBeGreaterThan(0);
  });

  test("shows the role card only when the user focuses a sub-agent", async () => {
    const spawn = event({
      id: "spawn-1",
      name: "subagent",
      lifecycle: "spawned",
      status: "running",
      parentToolUseId: "parent-call-1",
      agentId: "designer-a",
      subAgentRole: "designer",
      subagentCodename: "Spark-Design",
      thought: "Create the interaction design direction",
    });
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel events={[spawn]} />,
    );

    // The spawn event alone must not replace the summary with the role card.
    expect(screen.queryByText("Agent 集群 - 创建助手")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "角色卡" }),
    ).not.toBeInTheDocument();

    // A later tool event still keeps the summary page normal.
    rerender(
      <AgentWorkbenchPanel
        events={[
          spawn,
          event({
            id: "read-1",
            name: "read_file",
            status: "running",
            parentToolUseId: "parent-call-1",
            subAgentRole: "designer",
            startedAt: 2000,
            input: { path: "design.md" },
          }),
        ]}
      />,
    );

    await waitFor(() => {
      expect(
        screen.queryByText("Agent 集群 - 创建助手"),
      ).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("tab", { name: /Diff/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /终端/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /浏览器/ }),
    ).not.toBeInTheDocument();

    // An explicit role focus intent lands on the role card (工牌).
    rerender(
      <AgentWorkbenchPanel
        focusedAgentId="designer-a"
        focusedAgentView="role"
        focusedAgentNonce={1}
        events={[
          spawn,
          event({
            id: "read-1",
            name: "read_file",
            status: "running",
            parentToolUseId: "parent-call-1",
            subAgentRole: "designer",
            startedAt: 2000,
            input: { path: "design.md" },
          }),
        ]}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("Agent 集群 - 创建助手")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Spark-Design").length).toBeGreaterThan(0);
  });

  test("resolves a main-chat codename focus to the runtime agent tile", async () => {
    const onSelectTab = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="agent"
        onSelectTab={onSelectTab}
        events={[
          event({
            id: "spawn-reviewer",
            name: "subagent",
            lifecycle: "spawned",
            status: "running",
            parentToolUseId: "parent-review",
            agentId: "reviewer-runtime-id",
            subAgentRole: "reviewer",
            subagentCodename: "Prism-fcc",
            thought: "Review the current frontend implementation",
          }),
        ]}
      />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent(AGENT_WORKBENCH_FOCUS_EVENT, {
          detail: {
            agentId: "Prism-fcc",
            tab: "agent",
            view: "screen",
          },
        }),
      );
    });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "查看 Prism-fcc 独立进程" }),
      ).toHaveClass("border-foreground/25");
    });
    expect(onSelectTab).toHaveBeenCalledWith("agent");
  });

  test("keeps a historical main-chat agent inspectable after the workbench advances", async () => {
    renderWorkbench(
      <ThreadStreamingContext.Provider
        value={{ streamingMessage: null, subgraphStreams: {} }}
      >
        <ThreadValuesContext.Provider value={{ values: {} as never }}>
          <AgentWorkbenchPanel
            activeTab="agent"
            focusedAgentId="historic-reviewer"
            focusedAgentView="screen"
            focusedAgentNonce={1}
            focusedAgentSnapshot={{
              id: "historic-reviewer",
              name: "Prism-history",
              role: "reviewer",
              status: "done",
              task: "复核上一轮的前端视觉回归",
              summary: "已完成上一轮视觉回归并记录关键差异。",
              iterationCount: 3,
              filesTouchedCount: 2,
              index: 2,
            }}
            events={[]}
          />
        </ThreadValuesContext.Provider>
      </ThreadStreamingContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByText("复核上一轮的前端视觉回归")).toBeInTheDocument();
    });
    expect(
      screen.getByText("已完成上一轮视觉回归并记录关键差异。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "执行画面" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "角色卡" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "角色卡" }));
    await waitFor(() => {
      expect(screen.getByText("Agent 集群 - 创建助手")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Prism-history").length).toBeGreaterThan(0);
  });

  test("shows the backend built-in role identity on the nameplate", async () => {
    const spawn = event({
      id: "spawn-1",
      name: "subagent",
      lifecycle: "spawned",
      status: "running",
      parentToolUseId: "parent-call-1",
      agentId: "reviewer-a",
      subAgentRole: "reviewer",
      subagentCodename: "Spark-Review",
      subagentRoleDisplayName: "Code Reviewer",
      subagentRoleDescription:
        "Scans a code change for bugs, security holes, performance issues, and maintainability smells.",
    });
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedAgentId="reviewer-a"
        focusedAgentView="role"
        focusedAgentNonce={1}
        events={[spawn]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Agent 集群 - 创建助手")).toBeInTheDocument();
    });
    // The backend catalog's display name wins over the frontend fallback map
    // (which would render "Reviewer" for role="reviewer").
    expect(screen.getByText("Code Reviewer")).toBeInTheDocument();
    expect(
      screen.getByText(/Scans a code change for bugs, security holes/),
    ).toBeInTheDocument();
  });

  test("shows only the focused sub-agent complete stream in the right workbench", async () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedAgentId="agent-2"
        events={[
          event({
            id: "agent-1-spawn",
            name: "subagent_spawned",
            agentId: "agent-1",
            agentName: "Researcher",
            subAgentRole: "researcher",
            subagentCodename: "Spark-01",
            status: "running",
          }),
          event({
            id: "agent-1-read",
            name: "read_file",
            agentId: "agent-1",
            subAgentRole: "researcher",
            input: { path: "research.md" },
          }),
          event({
            id: "agent-2-spawn",
            name: "subagent_spawned",
            agentId: "agent-2",
            agentName: "Writer",
            subAgentRole: "writer",
            subagentCodename: "Spark-02",
            status: "running",
            startedAt: 2000,
          }),
          event({
            id: "agent-2-read",
            name: "read_file",
            agentId: "agent-2",
            subAgentRole: "writer",
            input: { path: "writer.md" },
            observation: "正在读取 writer.md",
            status: "running",
            startedAt: 2100,
          }),
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("live-exec-stream")).toHaveTextContent(
      "正在读取 writer.md",
    );
    expect(screen.queryByText("research.md")).not.toBeInTheDocument();
    expect(screen.queryByText("子智能体对话")).not.toBeInTheDocument();
  });

  test("renders a readable answer for a finished sub-agent without dumping metadata JSON", async () => {
    // Regression: the realtime bridge ships the sub-agent's answer text on
    // result.output (not observation), and the result envelope only carries
    // metadata (codename/role/duration_s/...). The process view must render the
    // answer text, not a JSON.stringify of the envelope.
    renderWorkbench(
      <ThreadStreamingContext.Provider
        value={{ streamingMessage: null, subgraphStreams: {} }}
      >
        <ThreadValuesContext.Provider value={{ values: {} as never }}>
          <AgentWorkbenchPanel
            focusedAgentId="researcher-a"
            focusedAgentView="screen"
            events={[
              event({
                id: "spawn-1",
                name: "subagent",
                lifecycle: "spawned",
                status: "running",
                agentId: "researcher-a",
                subAgentRole: "researcher",
                subagentCodename: "Spark-01",
                startedAt: 1000,
              }),
              event({
                id: "finish-1",
                name: "subagent",
                lifecycle: "finished",
                status: "done",
                agentId: "researcher-a",
                subAgentRole: "researcher",
                subagentCodename: "Spark-01",
                startedAt: 2000,
                output: {
                  agent_id: "researcher-a",
                  role: "researcher",
                  codename: "Spark-01",
                  avatar: "🔍",
                  ok: true,
                  duration_s: 2.5,
                  iteration_count: 4,
                  files_touched: ["reports/pricing.md"],
                  output: "调研完成：整理出三份定价策略报告",
                },
              }),
            ]}
          ></AgentWorkbenchPanel>
        </ThreadValuesContext.Provider>
      </ThreadStreamingContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();

    // The real answer text is shown...
    expect(
      screen.getByText("调研完成：整理出三份定价策略报告"),
    ).toBeInTheDocument();
    // ...and the metadata envelope is NOT rendered as the answer.
    expect(screen.queryByText(/"duration_s"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"iteration_count"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"agent_id"/)).not.toBeInTheDocument();
  });

  test("extracts a readable reason from a legacy stringified verdict", async () => {
    renderWorkbench(
      <ThreadStreamingContext.Provider
        value={{ streamingMessage: null, subgraphStreams: {} }}
      >
        <ThreadValuesContext.Provider value={{ values: {} as never }}>
          <AgentWorkbenchPanel
            activeTab="agent"
            focusedAgentId="historic-reviewer"
            focusedAgentView="screen"
            focusedAgentNonce={1}
            focusedAgentSnapshot={{
              id: "historic-reviewer",
              name: "Prism-history",
              role: "reviewer",
              status: "done",
              task: "复核审计发现",
              summary:
                '{"verdict":"drop","reason":"这只是运行状态，不是可执行的代码发现。"}',
              index: 2,
            }}
            events={[]}
          />
        </ThreadValuesContext.Provider>
      </ThreadStreamingContext.Provider>,
    );

    expect(
      await screen.findByText("这只是运行状态，不是可执行的代码发现。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/"verdict"/)).not.toBeInTheDocument();
  });

  test("folds a trailing running step into a result once the sub-agent settles", async () => {
    // The last tool block never received a done event, but the agent reached
    // a terminal finished marker. The process view must stop showing a live
    // running window and surface the step's output as a completed result.
    renderWorkbench(
      <ThreadStreamingContext.Provider
        value={{ streamingMessage: null, subgraphStreams: {} }}
      >
        <ThreadValuesContext.Provider value={{ values: {} as never }}>
          <AgentWorkbenchPanel
            focusedAgentId="researcher-a"
            focusedAgentView="screen"
            events={[
              event({
                id: "spawn-1",
                name: "subagent",
                lifecycle: "spawned",
                status: "running",
                agentId: "researcher-a",
                subAgentRole: "researcher",
                subagentCodename: "Spark-01",
                startedAt: 1000,
              }),
              event({
                id: "read-1",
                name: "read_file",
                status: "running",
                agentId: "researcher-a",
                subAgentRole: "researcher",
                input: { path: "report.md" },
                output: "正在读取 report.md",
                startedAt: 1500,
              }),
              event({
                id: "finish-1",
                name: "subagent",
                lifecycle: "finished",
                status: "done",
                agentId: "researcher-a",
                subAgentRole: "researcher",
                subagentCodename: "Spark-01",
                startedAt: 2000,
                output: {
                  agent_id: "researcher-a",
                  role: "researcher",
                  codename: "Spark-01",
                  ok: true,
                  duration_s: 1.2,
                  iteration_count: 2,
                  output: "调研完成",
                },
              }),
            ]}
          ></AgentWorkbenchPanel>
        </ThreadValuesContext.Provider>
      </ThreadStreamingContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();
    // Settled: no live typewriter window, but the step output is reachable.
    expect(screen.queryByTestId("live-exec-stream")).not.toBeInTheDocument();
    const conversation = screen.getByTestId("subagent-main-conversation");
    expect(conversation.textContent).toContain("正在读取 report.md");
  });

  test("keeps the summary view when the focus intent asks for it", async () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedAgentId="agent-2"
        focusedAgentView="summary"
        events={[
          event({
            id: "agent-2-spawn",
            name: "subagent_spawned",
            agentId: "agent-2",
            agentName: "Writer",
            subAgentRole: "writer",
            subagentCodename: "Spark-02",
            status: "running",
          }),
          event({
            id: "agent-2-read",
            name: "read_file",
            agentId: "agent-2",
            subAgentRole: "writer",
            input: { path: "writer.md" },
            startedAt: 2000,
          }),
        ]}
      />,
    );

    // The intent must not force the computer screen open.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "概要" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(screen.queryByText("Agent 集群 - 独立进程")).not.toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: "执行画面" }),
    ).toBeInTheDocument();
    expect(screen.getByText("writer.md")).toBeInTheDocument();
  });

  test("keeps the focused sub-agent stream stable during snapshot churn", async () => {
    const focusEvents = [
      event({
        id: "agent-2-spawn",
        name: "subagent_spawned",
        agentId: "agent-2",
        agentName: "Writer",
        subAgentRole: "writer",
        subagentCodename: "Spark-02",
        status: "running",
      }),
      event({
        id: "agent-2-read",
        name: "read_file",
        agentId: "agent-2",
        subAgentRole: "writer",
        input: { path: "writer.md" },
        startedAt: 2000,
      }),
    ];
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel focusedAgentId="agent-2" events={focusEvents} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });

    // Streaming churn rebuilds agentTiles with a fresh identity; the stale
    // focus intent must not yank the user back to the sub-agent view.
    rerender(
      <AgentWorkbenchPanel
        focusedAgentId="agent-2"
        events={[
          ...focusEvents,
          event({
            id: "agent-2-read-2",
            name: "read_file",
            agentId: "agent-2",
            subAgentRole: "writer",
            status: "running",
            input: { path: "writer-2.md" },
            startedAt: 2200,
          }),
        ]}
      />,
    );
    expect(screen.queryByText("Agent 集群 - 独立进程")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
  });

  test("switches a repeated focus intent from summary to the sub-agent stream", async () => {
    const focusEvents = [
      event({
        id: "agent-2-spawn",
        name: "subagent_spawned",
        agentId: "agent-2",
        agentName: "Writer",
        subAgentRole: "writer",
        subagentCodename: "Spark-02",
        status: "running",
      }),
      event({
        id: "agent-2-read",
        name: "read_file",
        agentId: "agent-2",
        subAgentRole: "writer",
        input: { path: "writer.md" },
        startedAt: 2000,
      }),
    ];
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel
        focusedAgentId="agent-2"
        focusedAgentView="summary"
        focusedAgentNonce={1}
        events={focusEvents}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "概要" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(screen.queryByText("Agent 集群 - 独立进程")).not.toBeInTheDocument();

    // A fresh explicit screen intent opens the selected agent's conversation.
    rerender(
      <AgentWorkbenchPanel
        focusedAgentId="agent-2"
        focusedAgentView="screen"
        focusedAgentNonce={2}
        events={focusEvents}
      />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
  });

  test("keeps main-agent transcript selections out of a duplicate workbench trace", async () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedEventId="write-2"
        focusedEventKind="execution"
        focusedEventView="trace"
        focusedEventNonce={1}
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/old.ts" },
          }),
          event({
            id: "write-2",
            name: "write_file",
            input: { path: "src/selected.ts" },
            startedAt: 2000,
          }),
        ]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "概要" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(screen.queryByText("活动轨迹")).not.toBeInTheDocument();
  });

  test("opens the matching evidence surface for a selected transcript action", async () => {
    const onSelectTab = vi.fn();
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel
        focusedEventId="write-2"
        focusedEventKind="execution"
        focusedEventView="trace"
        focusedEventNonce={1}
        onSelectTab={onSelectTab}
        events={[
          event({
            id: "write-2",
            name: "write_file",
            input: { path: "src/selected.ts" },
          }),
        ]}
      />,
    );

    await waitFor(() => expect(onSelectTab).toHaveBeenCalledWith("diff"));

    rerender(
      <AgentWorkbenchPanel
        focusedEventId="shell-3"
        focusedEventKind="execution"
        focusedEventView="trace"
        focusedEventNonce={2}
        onSelectTab={onSelectTab}
        events={[
          event({
            id: "shell-3",
            name: "shell_command",
            input: { command: "pnpm test" },
          }),
        ]}
      />,
    );

    await waitFor(() => expect(onSelectTab).toHaveBeenCalledWith("terminal"));
  });

  // SKIPPED: focusedProcessEvent detail rendering was removed in recent refactor.
  // Design decision: "思考/执行详情均在对话框内完整展示，右侧不再重复渲染"
  // (see agent-workbench-pages.tsx line 1085-1086)
  test.skip("renders selected public thinking detail in the summary panel", async () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedEventId="thinking-7"
        focusedEventKind="thinking"
        focusedEventView="summary"
        focusedEventNonce={1}
        focusedProcessEvent={{
          kind: "thinking",
          summary: "已确认时间线顺序",
          detail:
            "已确认公开进展位于工具结果之后，最终回答之前；下一步检查刷新后的顺序是否保持。",
          status: "done",
          count: 2,
          phaseId: "phase-verify",
          timelineSequence: 7,
        }}
        events={[]}
      />,
    );

    // The detail text now renders in a thinking-detail card so the user can
    // read the full thought after clicking the thinking row in the chat.
    expect(
      screen.getByText(
        "已确认公开进展位于工具结果之后，最终回答之前；下一步检查刷新后的顺序是否保持。",
      ),
    ).toBeInTheDocument();
    // The summary label and transcript-only metadata stay out of the panel.
    expect(screen.queryByText("当前对话")).not.toBeInTheDocument();
    expect(screen.queryByText("phase-verify")).not.toBeInTheDocument();
    expect(screen.queryByText(/时间线第 7 条/)).not.toBeInTheDocument();
    expect(screen.queryByText("#7")).not.toBeInTheDocument();
    expect(
      screen.queryByText("暂无运行中的机器人进程"),
    ).not.toBeInTheDocument();
  });

  test.skip("renders selected execution detail in the summary panel", async () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        focusedEventId="write-9"
        focusedEventKind="execution"
        focusedEventView="summary"
        focusedEventNonce={1}
        focusedProcessEvent={{
          kind: "execution",
          summary: "已更新消息排版",
          detail: "修改了主对话里的进展行，右侧会展示对应证据。",
          status: "done",
          count: 1,
        }}
        events={[]}
      />,
    );

    // The detail text now renders in an execution-detail card.
    expect(
      screen.getByText("修改了主对话里的进展行，右侧会展示对应证据。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("当前对话")).not.toBeInTheDocument();
    expect(screen.queryByText("执行日志")).not.toBeInTheDocument();
  });

  test("renders every selected sub-agent frame as an independent conversation", async () => {
    const baseEvents = [
      event({
        id: "server-phases:turn-1",
        name: "todo_write",
        status: "running",
        input: {
          items: [
            { content: "Phase 1: Research", status: "in_progress" },
            { content: "Phase 2: Write up", status: "pending" },
          ],
        },
      }),
      event({
        id: "agent-2-spawn",
        name: "subagent_spawned",
        agentId: "agent-2",
        agentName: "Writer",
        subAgentRole: "writer",
        subagentCodename: "Spark-02",
        status: "running",
      }),
      event({
        id: "agent-2-step-1",
        name: "read_file",
        agentId: "agent-2",
        subAgentRole: "writer",
        status: "done",
        input: { path: "history.md" },
        startedAt: 2000,
      }),
      event({
        id: "agent-2-step-2",
        name: "read_file",
        agentId: "agent-2",
        subAgentRole: "writer",
        status: "running",
        input: { path: "current.md" },
        startedAt: 2100,
      }),
    ];
    renderWorkbench(
      <AgentWorkbenchPanel focusedAgentId="agent-2" events={baseEvents} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "执行画面" })).toHaveClass(
        "border-foreground/70",
      );
    });
    expect(
      screen.getByTestId("subagent-main-conversation"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("interleaved-process-timeline"),
    ).toBeInTheDocument();
    expect(screen.queryByText("子智能体对话")).not.toBeInTheDocument();
  });

  test("renders the deployed site in the browser tab once the run settles", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="browser"
        hasAnswer
        runSettled
        resultPreviewUrl="https://demo.vercel.app"
        events={[
          event({
            id: "read-1",
            name: "read_file",
            input: { path: "src/app.tsx" },
            output: "const value = 1;",
          }),
        ]}
      />,
    );

    expect(screen.getByTestId("mock-live-preview")).toHaveAttribute(
      "data-preview-url",
      "https://demo.vercel.app",
    );
    expect(
      screen.queryByTestId("mock-browser-preview"),
    ).not.toBeInTheDocument();
  });

  test("prefers the live inline preview while streaming and can switch to the deployed site", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="browser"
        resultPreviewUrl="https://demo.vercel.app"
        browserPreviewBlocks={{ html: "<div>hi</div>", css: "", js: "" }}
        events={[
          event({
            id: "read-1",
            name: "read_file",
            status: "running",
            input: { path: "src/app.tsx" },
          }),
        ]}
      />,
    );

    const preview = screen.getByTestId("mock-live-preview");
    expect(preview).toHaveAttribute("data-preview-url", "");
    expect(preview).toHaveAttribute("data-has-srcdoc", "true");

    fireEvent.click(screen.getByRole("button", { name: "已部署" }));
    expect(screen.getByTestId("mock-live-preview")).toHaveAttribute(
      "data-preview-url",
      "https://demo.vercel.app",
    );
  });

  test("uses server workspace focus as the default workbench tab", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "server-phases:turn-1",
            name: "todo_write",
            status: "running",
            input: {
              source: "turn.phases",
              workspaceFocus: {
                itemId: "file-change-1",
                view: "diff",
                title: "Editing src/app.ts",
              },
              items: [
                {
                  content: "Phase 1: Patch UI",
                  status: "in_progress",
                  activeItemId: "file-change-1",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("keeps explicit activeTab ahead of server workspace focus", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="terminal"
        events={[
          event({
            id: "server-phases:turn-1",
            name: "todo_write",
            status: "running",
            input: {
              source: "turn.phases",
              workspaceFocus: {
                itemId: "file-change-1",
                view: "diff",
                title: "Editing src/app.ts",
              },
              items: [
                {
                  content: "Phase 1: Patch UI",
                  status: "in_progress",
                  activeItemId: "file-change-1",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.queryByRole("tab", { name: "CLI" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "终端" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("tab", { name: "Diff" })).not.toBeInTheDocument();
  });

  test("maps terminal workspace focus to the terminal tab", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        events={[
          event({
            id: "shell-1",
            name: "shell_command",
            status: "running",
            input: { command: "pnpm test" },
          }),
          event({
            id: "server-phases:turn-1",
            name: "todo_write",
            status: "running",
            input: {
              source: "turn.phases",
              workspaceFocus: {
                itemId: "shell-1",
                view: "terminal",
                title: "Running tests",
              },
              items: [
                {
                  content: "Phase 1: Verify",
                  status: "in_progress",
                  activeItemId: "shell-1",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("tab", { name: "终端" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("keeps the redundant subagent event stream tab removed", () => {
    renderWorkbench(<AgentWorkbenchPanel events={[]} />);

    expect(
      screen.queryByRole("tab", { name: /子线程事件流/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Agent 工作台" }),
    ).toBeInTheDocument();
  });

  test("keeps the subagent tab hidden while preserving summary observability", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="subagents"
        events={[
          event({
            id: "parent-call-1",
            name: "call_agent_parallel",
            status: "running",
            input: {
              specs: [{ agent_id: "researcher", prompt: "pricing lane" }],
            },
          }),
          event({
            id: "spawn-1",
            name: "subagent",
            lifecycle: "spawned",
            status: "running",
            parentToolUseId: "parent-call-1",
            agentId: "researcher-a",
            subAgentRole: "researcher",
            subagentCodename: "Spark-01",
            thought: "Research lane: collect pricing signals",
          }),
          event({
            id: "bb-1",
            name: "bb_write",
            status: "done",
            parentToolUseId: "parent-call-1",
            subAgentRole: "researcher",
            startedAt: 2000,
            input: { key: "market.pricing" },
          }),
          event({
            id: "write-1",
            name: "write_file",
            status: "done",
            parentToolUseId: "parent-call-1",
            subAgentRole: "researcher",
            startedAt: 3000,
            input: { path: "reports/pricing.md" },
          }),
          event({
            id: "finish-1",
            name: "subagent",
            lifecycle: "finished",
            status: "done",
            parentToolUseId: "parent-call-1",
            agentId: "researcher-a",
            subAgentRole: "researcher",
            subagentCodename: "Spark-01",
            durationMs: 1500,
            filesTouched: ["reports/pricing.md"],
            startedAt: 4000,
          }),
        ]}
      />,
    );

    expect(
      screen.queryByRole("tab", { name: "\u5b50\u667a\u80fd\u4f53" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTitle("主电脑 · 执行任务中...")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看主电脑" }),
    ).toBeInTheDocument();
  });

  test("renders diff output as an Agent computer inner page", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="diff"
        hasAnswer
        runSettled
        events={[
          event({
            id: "write-1",
            name: "write_file",
            input: { path: "src/app.tsx" },
            output: {
              diff: "--- a/src/app.tsx\n+++ b/src/app.tsx\n@@\n-old\n+new",
            },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("+++ b/src/app.tsx")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
    expect(screen.queryByText("Agent 01")).not.toBeInTheDocument();
  });

  test("keeps diff output hidden until the answer finishes", () => {
    const events = [
      event({
        id: "write-1",
        name: "write_file",
        input: { path: "src/app.tsx" },
        output: {
          diff: "--- a/src/app.tsx\n+++ b/src/app.tsx\n@@\n-old\n+new",
        },
      }),
    ];
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel activeTab="diff" events={events} />,
    );

    expect(screen.getByRole("tab", { name: "Diff" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("+++ b/src/app.tsx")).not.toBeInTheDocument();
    expect(screen.queryByText("+new")).not.toBeInTheDocument();

    rerender(
      <AgentWorkbenchPanel
        activeTab="diff"
        hasAnswer
        runSettled
        events={events}
      />,
    );

    expect(screen.getByText("+++ b/src/app.tsx")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
  });

  test("defers generated artifacts and changed files while the answer streams", () => {
    const events = [
      event({
        id: "create-1",
        name: "write_file",
        input: {
          changes: [
            {
              path: "reports/nas_market_research_plan.md",
              op: "create",
              diff: [
                "--- /dev/null",
                "+++ b/reports/nas_market_research_plan.md",
                "@@ -0,0 +1,2 @@",
                "+# Plan",
                "+body",
              ].join("\n"),
            },
          ],
        },
      }),
      event({
        id: "edit-1",
        name: "edit_file",
        input: {
          changes: [
            {
              path: "src/app.tsx",
              op: "update",
              diff: "--- a/src/app.tsx\n+++ b/src/app.tsx\n@@\n-old\n+new",
            },
          ],
        },
      }),
    ];
    const generatedLabel = "\u751f\u6210\u4ea7\u7269";
    const changedLabel = "\u53d8\u66f4\u6587\u4ef6";
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel events={events} />,
    );

    expect(screen.queryByText(generatedLabel)).not.toBeInTheDocument();
    expect(screen.queryByText(changedLabel)).not.toBeInTheDocument();

    rerender(<AgentWorkbenchPanel hasAnswer runSettled events={events} />);

    expect(screen.getByText(generatedLabel)).toBeInTheDocument();
    expect(screen.getByText(changedLabel)).toBeInTheDocument();
  });

  test("puts newly created files under generated artifacts in the summary", () => {
    const onSelectTab = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        onSelectTab={onSelectTab}
        events={[
          event({
            id: "create-1",
            name: "write_file",
            input: {
              changes: [
                {
                  path: "reports/nas_market_research_plan.md",
                  op: "create",
                  diff: [
                    "--- /dev/null",
                    "+++ b/reports/nas_market_research_plan.md",
                    "@@ -0,0 +1,2 @@",
                    "+# Plan",
                    "+body",
                  ].join("\n"),
                },
              ],
            },
          }),
          event({
            id: "edit-1",
            name: "edit_file",
            input: {
              changes: [
                {
                  path: "src/app.tsx",
                  op: "update",
                  diff: "--- a/src/app.tsx\n+++ b/src/app.tsx\n@@\n-old\n+new",
                },
              ],
            },
          }),
        ]}
      />,
    );

    const generatedLabel = "\u751f\u6210\u4ea7\u7269";
    const changedLabel = "\u53d8\u66f4\u6587\u4ef6";
    const generatedList = listAfterSummaryLabel(generatedLabel);
    const changedList = listAfterSummaryLabel(changedLabel);

    expect(
      within(generatedList).getByText("nas_market_research_plan.md"),
    ).toBeInTheDocument();
    expect(
      within(generatedList).queryByText("app.tsx"),
    ).not.toBeInTheDocument();
    expect(within(changedList).getByText("app.tsx")).toBeInTheDocument();
    expect(
      within(changedList).queryByText("nas_market_research_plan.md"),
    ).not.toBeInTheDocument();
    expect(
      within(generatedList).queryByText("--- /dev/null"),
    ).not.toBeInTheDocument();
    expect(
      within(generatedList).queryByText(
        "+++ b/reports/nas_market_research_plan.md",
      ),
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(generatedList).getByRole("button", {
        name: /reports\/nas_market_research_plan\.md/,
      }),
    );
    expect(onSelectTab).toHaveBeenCalledWith("artifacts");

    fireEvent.click(
      within(changedList).getByRole("button", { name: /src\/app\.tsx/ }),
    );
    expect(onSelectTab).toHaveBeenCalledWith("diff");
  });

  test("opens artifact rows through onOpenArtifact with the entry path", () => {
    const onSelectTab = vi.fn();
    const onOpenArtifact = vi.fn();
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        onSelectTab={onSelectTab}
        onOpenArtifact={onOpenArtifact}
        events={[
          event({
            id: "create-1",
            name: "write_file",
            input: {
              changes: [
                {
                  path: "reports/nas_market_research_plan.md",
                  op: "create",
                  diff: [
                    "--- /dev/null",
                    "+++ b/reports/nas_market_research_plan.md",
                    "@@ -0,0 +1,2 @@",
                    "+# Plan",
                    "+body",
                  ].join("\n"),
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /^产物/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: /^上下文/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    const generatedLabel = "生成产物";
    const generatedList = listAfterSummaryLabel(generatedLabel);
    fireEvent.click(
      within(generatedList).getByRole("button", {
        name: /reports\/nas_market_research_plan\.md/,
      }),
    );
    expect(onOpenArtifact).toHaveBeenCalledWith(
      "reports/nas_market_research_plan.md",
    );
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  test("treats final output writes as generated artifacts without a diff", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "final-write-1",
            name: "write_text_file",
            input: {
              path: "data/workspaces/thread-1/output/final/nas_market_research_plan.md",
            },
          }),
        ]}
      />,
    );

    const generatedLabel = "\u751f\u6210\u4ea7\u7269";
    const changedLabel = "\u53d8\u66f4\u6587\u4ef6";
    const generatedList = listAfterSummaryLabel(generatedLabel);

    expect(
      within(generatedList).getByText("nas_market_research_plan.md"),
    ).toBeInTheDocument();
    expect(screen.queryByText(changedLabel)).not.toBeInTheDocument();
  });

  test("prefers file_change create details over the write command summary", () => {
    const fullPath =
      "F:\\新建文件夹\\echo-agent\\data\\workspaces\\thread-1\\output\\final\\nas_market_research_plan.md";
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "write-command-1",
            name: "write_text_file",
            input: {
              path: "nas_market_research_plan.md",
            },
            output:
              '(real tool execution succeeded) write_text_file {"path": "' +
              fullPath +
              '"}',
          }),
          event({
            id: "file-change-1",
            name: "file_change",
            input: {
              changes: [
                {
                  path: fullPath,
                  op: "create",
                  diff:
                    "--- /dev/null\n+++ b/" +
                    fullPath +
                    "\n@@ -0,0 +1,1 @@\n+# Plan",
                },
              ],
            },
          }),
        ]}
      />,
    );

    const generatedLabel = "\u751f\u6210\u4ea7\u7269";
    const changedLabel = "\u53d8\u66f4\u6587\u4ef6";
    const generatedList = listAfterSummaryLabel(generatedLabel);

    expect(within(generatedList).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByText(changedLabel)).not.toBeInTheDocument();
  });

  test("renders terminal as an Agent computer inner page", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        activeTab="terminal"
        events={[
          event({
            id: "shell-1",
            name: "shell_command",
            input: { command: "pnpm typecheck", cwd: "F:\\repo" },
            output: "Done in 10s",
          }),
        ]}
      />,
    );

    expect(screen.getByRole("tab", { name: "终端" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("mock-terminal-panel")).toHaveTextContent(
      "F:\\repo",
    );
    expect(screen.queryByText("Agent 01")).not.toBeInTheDocument();
  });

  test("marks stale approval progress complete after an answer exists", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "approval-1",
            name: "write_text_file",
            status: "waiting_approval",
            input: { path: "plan.md" },
          }),
        ]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getAllByText(/收拢答案/).length).toBeGreaterThan(0);
    expect(screen.getByTitle("主电脑 · 已完成")).toBeInTheDocument();
    // Summary page shows phases with StatusGlyph icons instead of text
  });

  test("keeps completed progress and context visible", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "research-1",
            name: "web_search",
            status: "done",
            input: { query: "agent ux research" },
            output: {
              results: [
                {
                  title: "Agent UX Research",
                  url: "https://example.com/agent-ux",
                },
              ],
            },
          }),
        ]}
      />,
    );

    const progress = screen.getByRole("button", { name: /进展/ });
    const context = screen.getByRole("button", { name: /上下文/ });

    expect(progress).toHaveAttribute("aria-expanded", "true");
    expect(progress).not.toHaveTextContent("来源");
    expect(context).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByText(/估算 token/)).not.toBeInTheDocument();
    expect(screen.getByText("Agent UX Research")).toBeInTheDocument();
  });

  test("does not show waiting copy for a completed empty selected phase", () => {
    renderWorkbench(
      <AgentWorkbenchPanel
        hasAnswer
        runSettled
        events={[
          event({
            id: "todo-1",
            name: "todo_write",
            input: {
              todos: [
                { content: "draft plan", status: "completed" },
                { content: "write report", status: "completed" },
              ],
            },
          }),
          event({
            id: "write-1",
            name: "write_file",
            status: "done",
            input: { path: "report.md" },
            startedAt: 2000,
          }),
        ]}
      />,
    );

    expandSummarySection(/(?:待办事项|进展)/);

    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    expect(screen.queryByText("待开始")).not.toBeInTheDocument();
  });

  test("follows the running phase as streamed todo progress advances", () => {
    const phaseOne = event({
      id: "todo-1",
      name: "todo_write",
      status: "done",
      input: {
        todos: [
          { content: "write plan.md", status: "in_progress" },
          { content: "run research", status: "pending" },
        ],
      },
    });
    const { rerender } = renderWorkbench(
      <AgentWorkbenchPanel events={[phaseOne]} />,
    );

    expandSummarySection(/(?:待办事项|进展)/);
    // The current phase follows the streamed todo while preserving its title.
    expect(screen.getAllByText(/write plan\.md/).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(/write plan\.md/).length).toBeGreaterThan(0);

    rerender(
      <AgentWorkbenchPanel
        events={[
          phaseOne,
          event({
            id: "todo-2",
            name: "todo_write",
            status: "done",
            startedAt: 2000,
            input: {
              todos: [
                { content: "write plan.md", status: "completed" },
                { content: "run research", status: "in_progress" },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getAllByText(/run research/).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(/run research/).length).toBeGreaterThan(0);
  });
});

// ── Sub-agent visualisation: role → emoji avatar ────────────
import { __testing } from "./agent-workbench-panel";
const { avatarForRole, ROLE_AVATAR, DEFAULT_AVATAR } = __testing;

describe("avatarForRole", () => {
  test("maps known roles to emoji", () => {
    expect(avatarForRole("researcher")).toBe("🔍");
    expect(avatarForRole("critic")).toBe("🛡️");
    expect(avatarForRole("synthesizer")).toBe("✍️");
    expect(avatarForRole("architect")).toBe("🏗️");
    expect(avatarForRole("implementer")).toBe("🔧");
    expect(avatarForRole("debugger")).toBe("🐛");
  });

  test("is case insensitive + trims whitespace", () => {
    expect(avatarForRole("Researcher")).toBe("🔍");
    expect(avatarForRole("CRITIC")).toBe("🛡️");
    expect(avatarForRole("  synthesizer  ")).toBe("✍️");
  });

  test("falls back to echo mascot for unknown role", () => {
    expect(avatarForRole("unknown_role_x")).toBe(DEFAULT_AVATAR);
  });

  test("returns undefined for empty / null", () => {
    expect(avatarForRole("")).toBeUndefined();
    expect(avatarForRole(null)).toBeUndefined();
    expect(avatarForRole(undefined)).toBeUndefined();
  });

  test("default avatar is echo", () => {
    expect(DEFAULT_AVATAR).toBe("🐙");
  });

  test("ROLE_AVATAR has the canonical role keys", () => {
    const required = [
      "researcher",
      "critic",
      "synthesizer",
      "architect",
      "implementer",
      "debugger",
      "fact_checker",
    ];
    for (const role of required) {
      expect(ROLE_AVATAR).toHaveProperty(role);
    }
  });
});
