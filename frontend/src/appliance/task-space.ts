import { useCallback, useEffect, useState } from "react";

import { authHeader } from "@/appliance/auth";

export type EchoTaskStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "paused"
  | "verifying"
  | "repairing"
  | "cancelled"
  | "disconnected"
  | "failed"
  | "completed"
  | string;

export type EchoTaskActivity = {
  id: string;
  at: string | null;
  kind: "capability-decision" | "approval" | "execution";
  action: string;
  capabilityId: string | null;
  target: string;
  outcome: string;
  reasonCode: string | null;
  risk: string | null;
};

export type EchoTaskProjection = {
  id: string;
  source: "echo-agent";
  threadId: string | null;
  parentTaskId: string | null;
  kind: string;
  title: string;
  summary: string | null;
  status: EchoTaskStatus;
  displayStatus: EchoTaskStatus;
  leaseHealth?: {
    state: string;
    recoveryNeeded: boolean;
    canTakeover: boolean;
    canResume: boolean;
    recommendedAction: string | null;
    reason: string | null;
  };
  progressPercent: number | null;
  mode: string | null;
  agentId: string | null;
  runtimeCapabilityGroups: string[];
  capabilityDecisions: EchoTaskActivity[];
  approval: {
    required: true;
    tool: string | null;
    action: string | null;
    reason: string | null;
  } | null;
  activity: EchoTaskActivity[];
  startedAt: string | null;
  updatedAt: string | null;
  completedAt: string | null;
  terminalReason: string | null;
  latestCheckpointId: string | number | null;
  executionRecovery?: {
    checkpointAvailable: boolean;
    canStart: boolean;
    requiresTakeover: boolean;
    checkpointId: string | number | null;
    iteration: number | null;
    phase: string | null;
    reason: string;
  };
};

export type EchoTaskCounts = {
  total: number;
  active: number;
  waitingApproval: number;
  paused: number;
  recoveryNeeded: number;
  failed: number;
  completed: number;
};

export type EchoTaskProjectionResponse = {
  schema: "echo.task_projection.v1";
  available: boolean;
  generatedAt: string;
  counts: EchoTaskCounts;
  auditIntegrity: {
    available: boolean;
    ok: boolean | null;
    entriesChecked: number;
  };
  tasks: EchoTaskProjection[];
};

export type EchoTaskActionResponse = {
  schema: "echo.task_action.v1";
  action: "takeover";
  requiresWorkspaceResume: true;
  auditIntegrity: EchoTaskProjectionResponse["auditIntegrity"];
  task: EchoTaskProjection;
};

export type EchoTaskResumeExecutionResponse = {
  schema: "echo.task_action.v1";
  action: "resume_execution";
  state: "queued" | "turn_started" | "replayed" | string;
  turnId: string | null;
  requestId: string | null;
  threadPath: string | null;
  auditIntegrity: EchoTaskProjectionResponse["auditIntegrity"];
  task: EchoTaskProjection;
};

export async function fetchEchoTaskProjection(
  signal?: AbortSignal,
): Promise<EchoTaskProjectionResponse> {
  const response = await fetch("/api/appliance/tasks?limit=100", {
    headers: authHeader(),
    signal,
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    throw new Error(detail || "无法读取 Agent 任务状态");
  }
  const result = (await response.json()) as EchoTaskProjectionResponse;
  if (
    result.schema !== "echo.task_projection.v1" ||
    !Array.isArray(result.tasks)
  ) {
    throw new Error("任务服务返回了不兼容的数据");
  }
  return result;
}

export async function takeoverEchoTask(
  taskId: string,
  reason: string,
): Promise<EchoTaskActionResponse> {
  const response = await fetch(
    `/api/appliance/tasks/${encodeURIComponent(taskId)}/takeover`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ reason }),
    },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    if (response.status === 404) throw new Error("任务已不存在");
    if (response.status === 409) {
      throw new Error("任务状态已经变化，请刷新后再试");
    }
    throw new Error(detail || "无法接管任务");
  }
  const result = (await response.json()) as EchoTaskActionResponse;
  if (
    result.schema !== "echo.task_action.v1" ||
    result.action !== "takeover" ||
    !result.task
  ) {
    throw new Error("任务服务返回了不兼容的操作结果");
  }
  return result;
}

function recoveryRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return `echo-${globalThis.crypto.randomUUID()}`;
  }
  return `echo-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export async function resumeEchoTaskExecution(
  taskId: string,
  reason: string,
  requestId = recoveryRequestId(),
): Promise<EchoTaskResumeExecutionResponse> {
  const response = await fetch(
    `/api/appliance/tasks/${encodeURIComponent(taskId)}/resume-execution`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ reason, requestId }),
    },
  );
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    if (response.status === 401) throw new Error("登录已失效，请重新登录");
    if (response.status === 404) throw new Error("任务已不存在");
    if (response.status === 409) {
      throw new Error(detail || "任务恢复状态已经变化，请刷新后再试");
    }
    throw new Error(detail || "无法恢复任务执行");
  }
  const result = (await response.json()) as EchoTaskResumeExecutionResponse;
  if (
    result.schema !== "echo.task_action.v1" ||
    result.action !== "resume_execution" ||
    !result.task
  ) {
    throw new Error("任务服务返回了不兼容的恢复结果");
  }
  return result;
}

function projectionCounts(tasks: EchoTaskProjection[]): EchoTaskCounts {
  return {
    total: tasks.length,
    active: tasks.filter(
      (task) =>
        ["pending", "running", "verifying", "repairing"].includes(
          task.status,
        ) && !task.leaseHealth?.recoveryNeeded,
    ).length,
    waitingApproval: tasks.filter((task) => task.status === "waiting_approval")
      .length,
    paused: tasks.filter((task) => task.status === "paused").length,
    recoveryNeeded: tasks.filter((task) => task.leaseHealth?.recoveryNeeded)
      .length,
    failed: tasks.filter((task) =>
      ["failed", "disconnected"].includes(task.status),
    ).length,
    completed: tasks.filter((task) => task.status === "completed").length,
  };
}

export function useEchoTaskProjection(enabled = true) {
  const [projection, setProjection] =
    useState<EchoTaskProjectionResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const takeover = useCallback(async (taskId: string, reason: string) => {
    const result = await takeoverEchoTask(taskId, reason);
    setProjection((current) => {
      if (!current) return current;
      const tasks = current.tasks.map((task) =>
        task.id === result.task.id ? result.task : task,
      );
      return {
        ...current,
        generatedAt: new Date().toISOString(),
        auditIntegrity: result.auditIntegrity,
        counts: projectionCounts(tasks),
        tasks,
      };
    });
    return result;
  }, []);
  const resumeExecution = useCallback(
    async (taskId: string, reason: string) => {
      const result = await resumeEchoTaskExecution(taskId, reason);
      setProjection((current) => {
        if (!current) return current;
        const tasks = current.tasks.map((task) =>
          task.id === result.task.id ? result.task : task,
        );
        return {
          ...current,
          generatedAt: new Date().toISOString(),
          auditIntegrity: result.auditIntegrity,
          counts: projectionCounts(tasks),
          tasks,
        };
      });
      return result;
    },
    [],
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let alive = true;
    let controller: AbortController | null = null;
    const poll = async (foreground: boolean) => {
      controller?.abort();
      const requestController = new AbortController();
      controller = requestController;
      if (foreground) setLoading(true);
      try {
        const next = await fetchEchoTaskProjection(requestController.signal);
        if (!alive) return;
        setProjection(next);
        setError(null);
      } catch (reason) {
        if (!alive || requestController.signal.aborted) return;
        setError(
          reason instanceof Error ? reason.message : "无法读取 Agent 任务状态",
        );
      } finally {
        if (alive && foreground) setLoading(false);
      }
    };
    void poll(true);
    const timer = window.setInterval(() => void poll(false), 5_000);
    return () => {
      alive = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [enabled, revision]);

  return {
    projection,
    loading,
    error,
    refresh,
    takeover,
    resumeExecution,
  };
}
