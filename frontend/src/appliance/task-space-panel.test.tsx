import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TaskSpacePanel } from "./task-space-panel";
import type { EchoTaskProjectionResponse } from "./task-space";

const projection: EchoTaskProjectionResponse = {
  schema: "echo.task_projection.v1",
  available: true,
  generatedAt: "2026-08-26T02:00:00Z",
  counts: {
    total: 2,
    active: 0,
    waitingApproval: 1,
    paused: 0,
    recoveryNeeded: 0,
    failed: 0,
    completed: 1,
  },
  auditIntegrity: { available: true, ok: true, entriesChecked: 8 },
  tasks: [
    {
      id: "task-app-start",
      source: "echo-agent",
      threadId: "thread-system",
      parentTaskId: null,
      kind: "system",
      title: "启动媒体服务",
      summary: "确认后启动家庭媒体服务",
      status: "waiting_approval",
      displayStatus: "waiting_approval",
      leaseHealth: {
        state: "ok",
        recoveryNeeded: false,
        canTakeover: false,
        canResume: false,
        recommendedAction: "await_operator_approval",
        reason: "task is waiting for approval",
      },
      progressPercent: 50,
      mode: "agent",
      agentId: "echo-eve",
      executionEngine: "echo",
      modelName: "qwen3.5-plus",
      runtimeCapabilityGroups: ["apps"],
      capabilityDecisions: [
        {
          id: "audit:1",
          at: "2026-08-26T01:59:00Z",
          kind: "capability-decision",
          action: "capability.decision",
          capabilityId: "apps.start",
          target: "apps.start",
          outcome: "ask",
          reasonCode: "PASSWORD_STEP_UP_REQUIRED",
          risk: "high",
        },
      ],
      approval: {
        required: true,
        tool: "apps.start",
        action: "app.start",
        reason: "需要设备管理员确认",
      },
      activity: [],
      startedAt: "2026-08-26T01:50:00Z",
      updatedAt: "2026-08-26T01:59:00Z",
      completedAt: null,
      terminalReason: null,
      latestCheckpointId: null,
    },
    {
      id: "task-photo-sort",
      source: "echo-agent",
      threadId: "thread-photos",
      parentTaskId: null,
      kind: "loop",
      title: "整理照片",
      summary: "已完成重复照片整理",
      status: "completed",
      displayStatus: "completed",
      leaseHealth: {
        state: "terminal",
        recoveryNeeded: false,
        canTakeover: false,
        canResume: false,
        recommendedAction: "none",
        reason: "task is already terminal",
      },
      progressPercent: 100,
      mode: "agent",
      agentId: "echo-eve",
      runtimeCapabilityGroups: ["files"],
      capabilityDecisions: [],
      approval: null,
      activity: [],
      startedAt: "2026-08-26T01:00:00Z",
      updatedAt: "2026-08-26T01:30:00Z",
      completedAt: "2026-08-26T01:30:00Z",
      terminalReason: null,
      latestCheckpointId: "checkpoint-1",
      resultArtifacts: [
        {
          resourceId:
            "workspace-file:v1:dGhyZWFkLXBob3Rvcw:ZmluYWw:cmVwb3J0Lm1k",
          area: "final",
          relativePath: "report.md",
          name: "report.md",
          size: 128,
          modified: 1_724_650_000,
        },
      ],
    },
  ],
};

describe("Echo task space", () => {
  it("shows real task state, approval context, capability policy, and filters", async () => {
    const user = userEvent.setup();
    render(
      <TaskSpacePanel
        open
        projection={projection}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("dialog", { name: "Echo 任务空间" }),
    ).toBeInTheDocument();
    expect(screen.getByText("启动媒体服务")).toBeInTheDocument();
    expect(screen.getByText("整理照片")).toBeInTheDocument();
    expect(screen.getByText("需要设备管理员确认")).toBeInTheDocument();
    expect(screen.getByText("apps.start")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "查看任务：启动媒体服务" }),
    );
    expect(screen.getByText("Echo 原生")).toBeInTheDocument();
    expect(screen.getByText("qwen3.5-plus")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "收起任务详情" }));

    await user.click(screen.getByRole("button", { name: "待确认" }));
    expect(screen.getByText("启动媒体服务")).toBeInTheDocument();
    expect(screen.queryByText("整理照片")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "待确认" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("opens a completed result from the task projection in the original thread", async () => {
    const user = userEvent.setup();
    const onOpenWorkspace = vi.fn();
    render(
      <TaskSpacePanel
        open
        projection={{ ...projection, tasks: [projection.tasks[1]!] }}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={onOpenWorkspace}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "查看任务：整理照片" }),
    );
    await user.click(screen.getByRole("button", { name: /report\.md/ }));
    expect(onOpenWorkspace).toHaveBeenCalledWith(
      projection.tasks[1],
      "workspace-file:v1:dGhyZWFkLXBob3Rvcw:ZmluYWw:cmVwb3J0Lm1k",
    );
  });

  it("connects refresh, workspace, and close controls", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onRefresh = vi.fn();
    const onOpenWorkspace = vi.fn();
    render(
      <TaskSpacePanel
        open
        projection={projection}
        loading={false}
        error={null}
        onClose={onClose}
        onRefresh={onRefresh}
        onOpenWorkspace={onOpenWorkspace}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await user.click(screen.getByRole("button", { name: "打开工作台" }));
    await user.click(screen.getByRole("button", { name: "关闭任务空间" }));

    expect(onRefresh).toHaveBeenCalledOnce();
    expect(onOpenWorkspace).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("makes a projection error recoverable without hiding the empty state", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    render(
      <TaskSpacePanel
        open
        projection={null}
        loading={false}
        error="无法连接任务服务"
        onClose={vi.fn()}
        onRefresh={onRefresh}
        onOpenWorkspace={vi.fn()}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
        onApprovalDecision={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("无法连接任务服务");
    expect(screen.getByText("任务服务暂时不可用")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(7);
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("focuses its close control and closes with Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <TaskSpacePanel
        open
        projection={projection}
        loading={false}
        error={null}
        onClose={onClose}
        onRefresh={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
      />,
    );

    const close = screen.getByRole("button", { name: "关闭任务空间" });
    await waitFor(() => expect(close).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("separates an expired Agent lease and confirms a bounded takeover", async () => {
    const user = userEvent.setup();
    const onTakeover = vi.fn().mockResolvedValue(undefined);
    const interrupted = {
      ...projection.tasks[1]!,
      id: "task-interrupted",
      title: "中断的任务",
      status: "running",
      displayStatus: "disconnected",
      completedAt: null,
      leaseHealth: {
        state: "expired",
        recoveryNeeded: true,
        canTakeover: true,
        canResume: false,
        recommendedAction: "takeover",
        reason: "task has no live lease",
      },
    };
    render(
      <TaskSpacePanel
        open
        projection={{
          ...projection,
          counts: {
            ...projection.counts,
            active: 0,
            recoveryNeeded: 1,
          },
          tasks: [interrupted],
        }}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onTakeover={onTakeover}
        onResumeExecution={vi.fn()}
      />,
    );

    expect(screen.getByText("已断开")).toBeInTheDocument();
    expect(screen.getByText("上次执行已中断")).toBeInTheDocument();
    expect(
      screen.getByText("任务租约已失效，请在工作台检查后重新接管。"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "查看任务：中断的任务" }),
    );
    expect(
      screen.getByRole("complementary", { name: "任务详情" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Agent 租约需要恢复")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "接管任务租约…" }));
    expect(
      screen.getByRole("alertdialog", { name: "确认接管任务" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/这个动作不会自动执行任务/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认接管" }));

    expect(onTakeover).toHaveBeenCalledWith(
      "task-interrupted",
      "设备管理员从 Echo 任务空间接管中断任务",
    );
    expect(
      await screen.findByText(
        "任务租约已接管。Agent 会重新核验检查点，再允许恢复执行。",
      ),
    ).toBeInTheDocument();
  });

  it("keeps approval decisions in the original Agent task", async () => {
    const user = userEvent.setup();
    const onOpenWorkspace = vi.fn();
    const onApprovalDecision = vi.fn().mockResolvedValue(undefined);
    render(
      <TaskSpacePanel
        open
        projection={projection}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={onOpenWorkspace}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
        onApprovalDecision={onApprovalDecision}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "查看任务：启动媒体服务" }),
    );
    expect(screen.getByText("等待人工确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开原任务" }));
    expect(onOpenWorkspace).toHaveBeenCalledWith(projection.tasks[0]);
    await user.click(screen.getByRole("button", { name: "批准操作" }));
    expect(
      screen.getByRole("alertdialog", { name: "确认审批决定" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认" }));
    expect(onApprovalDecision).toHaveBeenCalledWith(
      "task-app-start",
      true,
      "设备管理员批准任务操作",
    );
    expect(
      screen.getByText("操作已批准，任务将继续执行。"),
    ).toBeInTheDocument();
  });

  it("keeps running and failed states discoverable through their filters", async () => {
    const user = userEvent.setup();
    const running = {
      ...projection.tasks[0]!,
      id: "task-running",
      title: "正在同步资料",
      status: "running",
      displayStatus: "running",
      approval: null,
      leaseHealth: {
        ...projection.tasks[0]!.leaseHealth!,
        state: "active",
        recoveryNeeded: false,
      },
    };
    const failed = {
      ...projection.tasks[1]!,
      id: "task-failed",
      title: "导出失败的任务",
      status: "failed",
      displayStatus: "failed",
      completedAt: null,
      terminalReason: "导出目标不可写",
    };
    const paused = {
      ...projection.tasks[1]!,
      id: "task-paused",
      title: "等待资源的任务",
      status: "paused",
      displayStatus: "paused",
      completedAt: null,
    };
    const disconnected = {
      ...running,
      id: "task-disconnected",
      title: "租约失效的任务",
      displayStatus: "disconnected",
      leaseHealth: {
        ...running.leaseHealth!,
        state: "expired",
        recoveryNeeded: true,
      },
    };
    render(
      <TaskSpacePanel
        open
        projection={{
          ...projection,
          counts: {
            ...projection.counts,
            total: 4,
            active: 1,
            paused: 1,
            failed: 2,
            recoveryNeeded: 1,
            completed: 0,
          },
          tasks: [running, failed, paused, disconnected],
        }}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "进行中" }));
    expect(screen.getByText("正在同步资料")).toBeInTheDocument();
    expect(screen.queryByText("导出失败的任务")).not.toBeInTheDocument();
    expect(screen.queryByText("等待资源的任务")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "已暂停" }));
    expect(screen.getByText("等待资源的任务")).toBeInTheDocument();
    expect(screen.queryByText("正在同步资料")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "异常" }));
    expect(screen.getByText("导出失败的任务")).toBeInTheDocument();
    expect(screen.getByText("租约失效的任务")).toBeInTheDocument();
    expect(screen.queryByText("正在同步资料")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "查看任务：导出失败的任务" }),
    );
    expect(screen.getByText("结束原因")).toBeInTheDocument();
    expect(screen.getByText("导出目标不可写")).toBeInTheDocument();
  });

  it("does not present an unknown backend status as a pending task", () => {
    const unknown = {
      ...projection.tasks[0]!,
      id: "task-unknown-status",
      title: "等待新版本状态",
      status: "suspended_by_provider",
      displayStatus: "suspended_by_provider",
      approval: null,
    };
    render(
      <TaskSpacePanel
        open
        projection={{
          ...projection,
          counts: { ...projection.counts, total: 1, waitingApproval: 0 },
          tasks: [unknown],
        }}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onTakeover={vi.fn()}
        onResumeExecution={vi.fn()}
        onApprovalDecision={vi.fn()}
      />,
    );
    expect(screen.getByText("状态未知")).toBeInTheDocument();
    expect(screen.queryByText("准备中")).not.toBeInTheDocument();
  });

  it("confirms checkpoint execution and then opens the original task", async () => {
    const user = userEvent.setup();
    const onResumeExecution = vi.fn().mockResolvedValue(undefined);
    const onOpenWorkspace = vi.fn();
    const resumable = {
      ...projection.tasks[1]!,
      id: "task-resumable",
      title: "恢复照片任务",
      status: "paused",
      displayStatus: "paused",
      completedAt: null,
      executionRecovery: {
        checkpointAvailable: true,
        canStart: true,
        requiresTakeover: false,
        checkpointId: 73,
        iteration: 8,
        phase: "verify",
        reason: "检查点已由 Agent 验证，可以在原线程恢复执行",
      },
    };
    render(
      <TaskSpacePanel
        open
        projection={{
          ...projection,
          counts: { ...projection.counts, paused: 1, completed: 0 },
          tasks: [resumable],
        }}
        loading={false}
        error={null}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onOpenWorkspace={onOpenWorkspace}
        onTakeover={vi.fn()}
        onResumeExecution={onResumeExecution}
      />,
    );

    expect(screen.getByText("检查点已验证")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "查看任务：恢复照片任务" }),
    );
    expect(screen.getByText("Agent 执行检查点")).toBeInTheDocument();
    expect(screen.getByText("第 8 轮")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "恢复执行并打开原任务…" }),
    );
    expect(
      screen.getByRole("alertdialog", { name: "确认恢复任务执行" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/不会自动批准/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认恢复" }));

    expect(onResumeExecution).toHaveBeenCalledWith(
      "task-resumable",
      "设备管理员从 Echo 任务空间确认恢复检查点执行",
    );
    expect(onOpenWorkspace).toHaveBeenCalledWith(resumable);
  });
});
