import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import {
  decideEchoTaskApproval,
  fetchEchoTaskProjection,
  projectionCounts,
  replaceProjectedTaskCounts,
  resumeEchoTaskExecution,
  takeoverEchoTask,
  useEchoTaskProjection,
} from "./task-space";
import type { EchoTaskProjection } from "./task-space";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer browser-session" }),
}));
const actor = vi.hoisted(() => ({ value: "one" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => actor.value,
}));

beforeEach(() => {
  vi.unstubAllGlobals();
  actor.value = "one";
});

describe("Echo task projection client", () => {
  it("keeps optimistic terminal counts aligned with the desktop filter", () => {
    const base = {
      status: "cancelled",
      leaseHealth: { recoveryNeeded: false },
    } as EchoTaskProjection;
    expect(projectionCounts([base]).failed).toBe(1);
  });

  it("counts lease recovery as a visible disconnected failure", () => {
    const interrupted = {
      status: "running",
      displayStatus: "disconnected",
      leaseHealth: { recoveryNeeded: true },
    } as EchoTaskProjection;
    expect(projectionCounts([interrupted]).failed).toBe(1);
    expect(projectionCounts([interrupted]).recoveryNeeded).toBe(1);
  });

  it("adjusts one task without shrinking server-wide counts", () => {
    const previous = {
      status: "running",
      displayStatus: "disconnected",
      leaseHealth: { recoveryNeeded: true },
    } as EchoTaskProjection;
    const next = {
      status: "running",
      displayStatus: "running",
      leaseHealth: { recoveryNeeded: false },
    } as EchoTaskProjection;
    expect(
      replaceProjectedTaskCounts(
        {
          total: 101,
          active: 10,
          waitingApproval: 4,
          paused: 3,
          recoveryNeeded: 3,
          failed: 4,
          completed: 12,
        },
        previous,
        next,
      ),
    ).toEqual({
      total: 101,
      active: 11,
      waitingApproval: 4,
      paused: 3,
      recoveryNeeded: 2,
      failed: 3,
      completed: 12,
    });
  });

  it("submits an approval decision through the Agent task contract", async () => {
    const payload = {
      schema: "echo.task_run_approval_decision.v1",
      task_run: { task_id: "task-approval", status: "completed" },
      lease_health: { state: "terminal" },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      decideEchoTaskApproval("task-approval", true, "设备管理员批准"),
    ).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/task-runs/task-approval/approval-decision",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer browser-session",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ approved: true, reason: "设备管理员批准" }),
      },
    );
  });

  it("loads the read-only Agent task projection with appliance authentication", async () => {
    const payload = {
      schema: "echo.task_projection.v1",
      available: true,
      generatedAt: "2026-08-26T02:00:00Z",
      counts: {
        total: 0,
        active: 0,
        waitingApproval: 0,
        paused: 0,
        recoveryNeeded: 0,
        failed: 0,
        completed: 0,
      },
      auditIntegrity: { available: true, ok: true, entriesChecked: 12 },
      tasks: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchEchoTaskProjection()).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith("/api/appliance/tasks?limit=100", {
      headers: { Authorization: "Bearer browser-session" },
      signal: undefined,
    });
  });

  it("rejects an incompatible projection contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ schema: "legacy.tasks", tasks: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(fetchEchoTaskProjection()).rejects.toThrow(
      "任务服务返回了不兼容的数据",
    );
  });

  it("turns an expired appliance session into a login message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(fetchEchoTaskProjection()).rejects.toThrow(
      "登录已失效，请重新登录",
    );
  });

  it("clears a previous operator snapshot when projection access is disabled", async () => {
    const payload = {
      schema: "echo.task_projection.v1",
      available: true,
      generatedAt: "2026-08-26T02:00:00Z",
      counts: {
        total: 1,
        active: 0,
        waitingApproval: 0,
        paused: 0,
        recoveryNeeded: 0,
        failed: 0,
        completed: 1,
      },
      auditIntegrity: { available: true, ok: true, entriesChecked: 1 },
      tasks: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const rendered = renderHook(
      ({ enabled }: { enabled: boolean }) => useEchoTaskProjection(enabled),
      { initialProps: { enabled: true } },
    );
    await waitFor(() =>
      expect(rendered.result.current.projection).toEqual(payload),
    );
    rendered.rerender({ enabled: false });
    await waitFor(() => {
      expect(rendered.result.current.projection).toBeNull();
      expect(rendered.result.current.error).toBeNull();
      expect(rendered.result.current.loading).toBe(false);
    });
    rendered.unmount();
  });

  it("clears the previous operator snapshot before loading a new actor", async () => {
    const payload = (title: string) => ({
      schema: "echo.task_projection.v1",
      available: true,
      generatedAt: "2026-08-26T02:00:00Z",
      counts: {
        total: 1,
        active: 0,
        waitingApproval: 0,
        paused: 0,
        recoveryNeeded: 0,
        failed: 0,
        completed: 1,
      },
      auditIntegrity: { available: true, ok: true, entriesChecked: 1 },
      tasks: [{ title }],
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(payload("账号一的任务")), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValue(
        new Response(JSON.stringify(payload("账号二的任务")), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const rendered = renderHook(() => useEchoTaskProjection(true));
    await waitFor(() =>
      expect(rendered.result.current.projection?.tasks[0]?.title).toBe(
        "账号一的任务",
      ),
    );

    actor.value = "two";
    rendered.rerender();
    expect(rendered.result.current.projection).toBeNull();
    await waitFor(() =>
      expect(rendered.result.current.projection?.tasks[0]?.title).toBe(
        "账号二的任务",
      ),
    );
    rendered.unmount();
  });

  it("takes over an interrupted task through the bounded action contract", async () => {
    const payload = {
      schema: "echo.task_action.v1",
      action: "takeover",
      requiresWorkspaceResume: true,
      auditIntegrity: { available: true, ok: true, entriesChecked: 14 },
      task: { id: "task-interrupted" },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      takeoverEchoTask("task-interrupted", "device owner takeover"),
    ).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/tasks/task-interrupted/takeover",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer browser-session",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reason: "device owner takeover" }),
      },
    );
  });

  it("reports a takeover race as changed task state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "live lease" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(takeoverEchoTask("task-live", "takeover")).rejects.toThrow(
      "任务状态已经变化，请刷新后再试",
    );
  });

  it("starts checkpoint execution with a stable recovery request id", async () => {
    const payload = {
      schema: "echo.task_action.v1",
      action: "resume_execution",
      state: "turn_started",
      turnId: "trn-recovery",
      requestId: "echo-request-1",
      threadPath: "/workspace/realtime/thread-recovery",
      auditIntegrity: { available: true, ok: true, entriesChecked: 18 },
      task: { id: "task-recovery" },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      resumeEchoTaskExecution(
        "task-recovery",
        "continue verified checkpoint",
        "echo-request-1",
      ),
    ).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/appliance/tasks/task-recovery/resume-execution",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer browser-session",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          reason: "continue verified checkpoint",
          requestId: "echo-request-1",
        }),
      },
    );
  });
});
