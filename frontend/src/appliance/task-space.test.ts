import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchEchoTaskProjection,
  resumeEchoTaskExecution,
  takeoverEchoTask,
} from "./task-space";

vi.mock("@/appliance/auth", () => ({
  authHeader: () => ({ Authorization: "Bearer browser-session" }),
}));

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("Echo task projection client", () => {
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
