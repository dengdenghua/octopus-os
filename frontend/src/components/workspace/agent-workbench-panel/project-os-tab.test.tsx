import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { AgentWorkbenchPanel } from "../agent-workbench-panel";
import {
  boundProjectRefetchInterval,
  normalizeProjectProgress,
  ProjectOsTab,
  type ProjectFullState,
} from "./project-os-tab";

describe("boundProjectRefetchInterval", () => {
  it("stops polling after the backend confirms that no project is bound", () => {
    expect(boundProjectRefetchInterval(null)).toBe(false);
  });

  it("keeps the initial and bound-project refresh cadence", () => {
    expect(boundProjectRefetchInterval(undefined)).toBe(15_000);
    expect(boundProjectRefetchInterval(projectState)).toBe(15_000);
  });
});

const projectState: ProjectFullState = {
  project: {
    id: "P-1",
    name: "PM 演示",
    goal: "做一个真实项目管理模式的演示项目",
    status: "running",
    owner: "林然",
    created_at: "2026-08-20T00:00:00Z",
    started_at: "2026-08-20T01:00:00Z",
    finished_at: "",
  },
  milestones: [
    {
      id: "MS1",
      name: "需求梳理",
      goal: "明确产品边界",
      status: "done",
      priority: "P1",
      due_at: "2026-08-21",
      success_criteria: ["评审通过"],
    },
    {
      id: "MS2",
      name: "方案设计",
      goal: "形成可落地方案",
      status: "running",
      priority: "P1",
      due_at: "2026-08-22",
    },
  ],
  tasks: {
    MS1: [
      {
        id: "t0",
        milestone_id: "MS1",
        type: "research",
        goal: "整理需求文档",
        assigned_role: "产品经理",
        assigned_agent: "",
        team_mode: "single",
        priority: "P1",
        estimate: 1,
        due_at: "2026-08-21",
        acceptance_criteria: ["覆盖核心场景"],
        status: "done",
        attempts: 1,
        output: {
          name: "需求说明.md",
          path: "/workspace/output/需求说明.md",
          summary: "项目需求与验收边界",
        },
      },
    ],
    MS2: [
      {
        id: "t1",
        milestone_id: "MS2",
        type: "design",
        goal: "输出技术方案",
        assigned_role: "架构师",
        assigned_agent: "codex-cli",
        team_mode: "cluster",
        priority: "P0",
        estimate: 1,
        due_at: "2026-08-21",
        acceptance_criteria: ["完成技术评审"],
        status: "running",
        attempts: 1,
      },
    ],
  },
  pm: {
    project_id: "P-1",
    name: "PM 演示",
    status: "running",
    overall_progress: 0.42,
    done_tasks: 3,
    total_tasks: 7,
    remaining_estimate: 4,
    assignments: { "codex-cli": ["t1"] },
    milestones: [
      {
        id: "MS1",
        name: "需求梳理",
        status: "done",
        health: "completed",
        priority: "P1",
        due_at: "2026-08-21",
        done: 2,
        total: 2,
        failed: 0,
        progress: 1,
        success_criteria: ["评审通过"],
      },
      {
        id: "MS2",
        name: "方案设计",
        status: "running",
        health: "at_risk",
        priority: "P1",
        due_at: "2026-08-22",
        done: 1,
        total: 5,
        failed: 1,
        progress: 0.2,
      },
    ],
    risks: [{ type: "task", health: "at_risk", detail: "方案评审被驳回" }],
    blockers: [],
    next_actions: [
      {
        milestone: "方案设计",
        task_id: "t1",
        task: "输出技术方案",
        priority: "P0",
        estimate: 1,
        due_at: "2026-08-21",
      },
    ],
  },
  retro: null,
  available_actions: ["run", "tick"],
  action_specs: [
    {
      action: "run",
      label: "Run",
      api: {
        method: "POST",
        path: "/api/projects/P-1/run",
        body: { max_ticks: 5 },
      },
    },
    {
      action: "tick",
      label: "Tick",
      api: { method: "POST", path: "/api/projects/P-1/tick" },
    },
  ],
};

describe("normalizeProjectProgress", () => {
  it("normalizes Project OS ratios and legacy percentage values", () => {
    expect(normalizeProjectProgress(0.42)).toBe(42);
    expect(normalizeProjectProgress(1)).toBe(100);
    expect(normalizeProjectProgress(42)).toBe(42);
    expect(normalizeProjectProgress(200)).toBe(100);
    expect(normalizeProjectProgress(-1)).toBe(0);
    expect(normalizeProjectProgress("not-a-number")).toBe(0);
  });
});

describe("ProjectOsTab", () => {
  it("renders a normalized overview and the five unified project tabs", () => {
    renderWithProviders(<ProjectOsTab state={projectState} />, {
      locale: "zh-CN",
    });

    expect(screen.getByText("PM 演示")).toBeInTheDocument();
    expect(
      screen.getByText("做一个真实项目管理模式的演示项目"),
    ).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByLabelText("项目整体进度 42%")).toBeInTheDocument();
    expect(screen.getByText("开始推进")).toBeInTheDocument();
    expect(screen.getByText("推进一步")).toBeInTheDocument();
    expect(screen.getByText("管理页")).toBeInTheDocument();

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "总览",
      "里程碑2 项",
      "事项2 项",
      "资料1 项",
      "成员1 项",
    ]);
  });

  it("deduplicates the project goal and links a zero-estimate metric to assets", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          project: { ...projectState.project, goal: " PM 演示 " },
          pm: { ...projectState.pm!, remaining_estimate: 0 },
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getAllByText("PM 演示")).toHaveLength(1);
    const assetMetric = screen.getByRole("button", {
      name: /项目资料\s*1/,
    });
    fireEvent.click(assetMetric);

    expect(screen.getByRole("tab", { name: /资料/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("需求说明.md")).toBeInTheDocument();
  });

  it("collapses a project heading that repeats the group or conversation title", () => {
    const { rerender } = renderWithProviders(
      <ProjectOsTab state={projectState} groupTitle="  PM   演示  " />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("project-workbench-context")).toHaveAttribute(
      "data-project-title-collapsed",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "项目工作台" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "PM 演示" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("管理页")).toBeInTheDocument();

    rerender(
      <ProjectOsTab state={projectState} currentThreadTitle="另一个群聊" />,
    );
    expect(screen.getByTestId("project-workbench-context")).toHaveAttribute(
      "data-project-title-collapsed",
      "false",
    );
    expect(
      screen.getByRole("heading", { name: "PM 演示" }),
    ).toBeInTheDocument();
  });

  it("renders the latest projected decisions with optional metadata", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          decisions: [
            {
              id: "decision-old",
              title: "先做桌面端",
              decision: "第一阶段只覆盖桌面端工作台。",
              actor: "林然",
              created_at: "2026-08-20T09:30:00Z",
            },
            {
              id: "decision-new",
              title: "发布会定在周五",
              summary: "团队已确认发布节奏",
              decision: "首版物料需要在周三完成确认。",
              actor: "产品负责人",
              milestone_id: "MS2",
              created_at: "2026-08-22T10:15:00Z",
            },
            { summary: "仅有摘要也能形成一条决策" },
          ],
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(
      screen.getByRole("region", { name: "项目决策" }),
    ).toBeInTheDocument();
    expect(screen.getByText("3 条沉淀")).toBeInTheDocument();
    expect(screen.getByText("发布会定在周五")).toBeInTheDocument();
    expect(
      screen.getByText("首版物料需要在周三完成确认。"),
    ).toBeInTheDocument();
    expect(screen.getByText("记录者 产品负责人")).toBeInTheDocument();
    expect(screen.getByText("里程碑 MS2")).toBeInTheDocument();
    expect(screen.getByText("仅有摘要也能形成一条决策")).toBeInTheDocument();

    const decisionTitles = screen
      .getAllByRole("heading", { level: 5 })
      .map((heading) => heading.textContent);
    expect(decisionTitles.slice(0, 2)).toEqual([
      "发布会定在周五",
      "先做桌面端",
    ]);
  });

  it("does not add a decision section without a usable decision", () => {
    renderWithProviders(
      <ProjectOsTab state={{ ...projectState, decisions: [{}] }} />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByText("项目决策")).not.toBeInTheDocument();
  });

  it("navigates milestones, tasks, assets and members without leaving chat", () => {
    const onOpenArtifact = vi.fn();
    const onInvitePeople = vi.fn();
    renderWithProviders(
      <ProjectOsTab
        state={projectState}
        onOpenArtifact={onOpenArtifact}
        onInvitePeople={onInvitePeople}
        rosterSeats={[
          { id: "reviewer", name: "评审 Agent", role: "member" },
          {
            id: "actor-guest",
            name: "受邀同事",
            role: "访客",
            kind: "human",
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.click(screen.getByRole("tab", { name: /里程碑/ }));
    expect(screen.getByText("需求梳理")).toBeInTheDocument();
    expect(screen.getByText("方案设计")).toBeInTheDocument();
    expect(screen.getByLabelText("方案设计进度 20%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /事项/ }));
    expect(screen.getByText("整理需求文档")).toBeInTheDocument();
    expect(screen.getByText("输出技术方案")).toBeInTheDocument();
    expect(screen.getByText("codex-cli")).toBeInTheDocument();
    expect(screen.getByText("cluster")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /资料/ }));
    fireEvent.click(screen.getByRole("button", { name: /需求说明\.md/ }));
    expect(onOpenArtifact).toHaveBeenCalledWith(
      "/workspace/output/需求说明.md",
    );

    fireEvent.click(screen.getByRole("tab", { name: /成员/ }));
    expect(screen.getByText("林然")).toBeInTheDocument();
    expect(screen.queryByText("codex-cli")).not.toBeInTheDocument();
    expect(screen.getByText("评审 Agent")).toBeInTheDocument();
    expect(screen.getByText("AI 成员")).toBeInTheDocument();
    expect(screen.getByText("受邀同事")).toBeInTheDocument();
    expect(screen.getByText("访客")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "邀请真人" }));
    expect(onInvitePeople).toHaveBeenCalledTimes(1);
  });

  it("keeps task-only assignees out of members while retaining ownership for authoritative members", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          members: [
            {
              id: "owner-projection",
              name: "林然",
              role: "重复负责人",
              kind: "human",
            },
            {
              id: "codex-cli",
              name: "codex-cli",
              role: "执行成员",
              kind: "agent",
            },
          ],
          pm: {
            ...projectState.pm!,
            assignments: {
              "codex-cli": ["t1"],
              "shadow-agent": ["t-shadow"],
            },
          },
        }}
        rosterSeats={[
          { id: "codex-cli", name: "codex-cli", role: "member" },
          { id: "reviewer", name: "评审 Agent", role: "member" },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByRole("tab", { name: "成员3 项" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /成员/ }));

    expect(screen.getByText("林然")).toBeInTheDocument();
    expect(screen.getByText("codex-cli")).toBeInTheDocument();
    expect(screen.getByText("评审 Agent")).toBeInTheDocument();
    expect(screen.queryByText("shadow-agent")).not.toBeInTheDocument();
    expect(screen.getByText("1 项进行中")).toBeInTheDocument();
    expect(screen.getByText("0 项已完成")).toBeInTheDocument();
  });

  it("renders useful empty states for a newly planned project", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          project: {
            ...projectState.project,
            id: "P-empty",
            status: "planning",
            owner: "",
          },
          milestones: [],
          tasks: {},
          pm: null,
          action_specs: [],
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("project-empty-launch-card")).toHaveTextContent(
      "从第一个里程碑开始",
    );
    expect(screen.getByRole("link", { name: /打开项目管理/ })).toHaveAttribute(
      "href",
      "/workspace/projects",
    );
    expect(screen.queryByText("整体进度")).not.toBeInTheDocument();
    expect(screen.queryByText("待推进事项")).not.toBeInTheDocument();
    expect(screen.queryByText("风险与阻塞")).not.toBeInTheDocument();
    expect(screen.queryByText("项目资料")).not.toBeInTheDocument();
    expect(screen.queryByText("下一步")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "里程碑" }));
    expect(screen.getByText("还没有里程碑")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "事项" }));
    expect(screen.getByText("还没有事项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "资料" }));
    expect(screen.getByText("还没有项目资料")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "成员" }));
    expect(screen.getByText("还没有项目成员")).toBeInTheDocument();
  });

  it("keeps the data overview when a project has assets but no plan yet", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          milestones: [],
          tasks: {},
          pm: null,
          artifacts: [
            {
              id: "brief",
              name: "项目简报.md",
              path: "/workspace/项目简报.md",
            },
          ],
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByTestId("project-empty-launch-card")).toBeNull();
    expect(screen.getByText("整体进度")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /项目资料\s*1/ })).toBeVisible();
  });

  it("focuses the matching internal tab from project timeline entity events", () => {
    renderWithProviders(<ProjectOsTab state={projectState} />, {
      locale: "zh-CN",
    });

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:project-entity-focus", {
          detail: { projectId: "P-1", kind: "task", entityId: "t1" },
        }),
      );
    });
    expect(screen.getByRole("tab", { name: /事项/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("输出技术方案")).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("echo:project-entity-focus", {
          detail: { projectId: "another-project", kind: "artifact" },
        }),
      );
    });
    expect(screen.getByRole("tab", { name: /事项/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("does not expose raw runtime failure details in the PM summary", () => {
    const rawFailure =
      "error: RuntimeError: sub-agent runner not configured during bootstrap";
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          pm: {
            ...projectState.pm!,
            risks: [
              {
                type: "task",
                task_id: "t1",
                task: "输出技术方案",
                health: "failed",
                detail: rawFailure,
              },
            ],
          },
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.queryByText(rawFailure)).toBeNull();
    expect(
      screen.getByText("输出技术方案执行失败或受阻，请检查执行环境后重试。"),
    ).toBeInTheDocument();
  });

  it("renders retro when the project is finished", () => {
    renderWithProviders(
      <ProjectOsTab
        state={{
          ...projectState,
          project: { ...projectState.project, status: "done" },
          retro: {
            project_id: "P-1",
            name: "PM 演示",
            goal: "",
            status: "done",
            milestone_count: 2,
            task_count: 7,
            done_tasks: 5,
            failed_tasks: 2,
            rejected_tasks: 1,
            attempts_total: 9,
            total_estimate: 8,
            duration_days: 2,
            blocked_milestones: [],
            recommendations: ["拆分大任务"],
          },
        }}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByText("项目复盘")).toBeInTheDocument();
    expect(screen.getByText("拆分大任务")).toBeInTheDocument();
  });
});

describe("AgentWorkbenchPanel project tab", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => projectState,
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces the 项目 tab when the thread has a bound project", async () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        activeTab="project"
        events={[]}
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("project-workbench-loading")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("PM 演示")).toBeInTheDocument();
    });
    expect(screen.getByText("项目")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects/by-thread/thread-1"),
      expect.anything(),
    );
  });

  it("keeps Echo Design out of the milestone project workbench", async () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        activeTab="design"
        events={[]}
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "项目" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("tab", { name: "设计" })).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("embedded-project-design"),
    ).not.toBeInTheDocument();
  });

  it("forwards the current group title to the project surface", async () => {
    renderWithProviders(
      <AgentWorkbenchPanel
        activeTab="project"
        events={[]}
        threadId="thread-1"
        groupTitle="PM 演示"
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => {
      expect(screen.getByTestId("project-workbench-context")).toHaveAttribute(
        "data-project-title-collapsed",
        "true",
      );
    });
    expect(
      screen.getByRole("heading", { name: "项目工作台" }),
    ).toBeInTheDocument();
  });

  it("falls back to the agent surface when no project is bound", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
    });

    renderWithProviders(
      <AgentWorkbenchPanel
        activeTab="project"
        events={[]}
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => {
      expect(screen.queryByTestId("project-workbench-loading")).toBeNull();
      expect(screen.queryByTestId("project-workbench")).toBeNull();
    });
  });

  it("keeps the requested project surface visible when loading fails", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503 });

    renderWithProviders(
      <AgentWorkbenchPanel
        activeTab="project"
        events={[]}
        threadId="thread-1"
      />,
      { locale: "zh-CN" },
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("项目工作台加载失败");
    });
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
