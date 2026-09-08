import { useCallback, useEffect, useRef, useState } from "react";

import { authHeader } from "@/appliance/auth";
import { currentActorId } from "@/core/auth/api";

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

/** Keep task-space filters and projection counters on the same status contract. */
export const ACTIVE_TASK_STATUSES: readonly string[] = [
  "pending",
  "running",
  "verifying",
  "repairing",
] as const;
export const FAILED_TASK_STATUSES: readonly string[] = [
  "failed",
  "disconnected",
  "cancelled",
] as const;

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
  /** Concrete runtime identity; absent on legacy task records. */
  executionEngine?: string | null;
  modelName?: string | null;
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
  resultArtifacts?: Array<{
    resourceId: string;
    area: "final" | "deploy";
    relativePath: string;
    name: string;
    size: number;
    modified: number;
  }>;
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

export type EchoTaskApprovalDecisionResponse = {
  schema: "echo.task_run_approval_decision.v1";
  task_run: Record<string, unknown>;
  lease_health: Record<string, unknown>;
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

export async function decideEchoTaskApproval(
  taskId: string,
  approved: boolean,
  reason: string,
): Promise<EchoTaskApprovalDecisionResponse> {
  const response = await fetch(
    `/api/task-runs/${encodeURIComponent(taskId)}/approval-decision`,
    {
      method: "POST",
      headers: {
        ...authHeader(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ approved, reason }),
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
      throw new Error(detail || "审批状态已经变化，请刷新后再试");
    }
    throw new Error(detail || "无法提交审批决定");
  }
  const result = (await response.json()) as EchoTaskApprovalDecisionResponse;
  if (
    result.schema !== "echo.task_run_approval_decision.v1" ||
    !result.task_run
  ) {
    throw new Error("任务服务返回了不兼容的审批结果");
  }
  return result;
}

export function projectionCounts(tasks: EchoTaskProjection[]): EchoTaskCounts {
  const counts: EchoTaskCounts = {
    total: tasks.length,
    active: 0,
    waitingApproval: 0,
    paused: 0,
    recoveryNeeded: 0,
    failed: 0,
    completed: 0,
  };
  for (const task of tasks) {
    const recovering = Boolean(task.leaseHealth?.recoveryNeeded);
    if (recovering) counts.recoveryNeeded += 1;
    if (ACTIVE_TASK_STATUSES.includes(task.status) && !recovering) {
      counts.active += 1;
    }
    if (task.status === "waiting_approval") counts.waitingApproval += 1;
    if (task.status === "paused") counts.paused += 1;
    if (FAILED_TASK_STATUSES.includes(task.displayStatus || task.status)) {
      counts.failed += 1;
    }
    if (task.status === "completed") counts.completed += 1;
  }
  return counts;
}

type IncrementalCountKey =
  | "active"
  | "waitingApproval"
  | "paused"
  | "recoveryNeeded"
  | "failed"
  | "completed";

function taskCountFlags(
  task: EchoTaskProjection,
): Record<IncrementalCountKey, boolean> {
  const recovering = Boolean(task.leaseHealth?.recoveryNeeded);
  const visibleStatus = task.displayStatus || task.status;
  return {
    active: ACTIVE_TASK_STATUSES.includes(task.status) && !recovering,
    waitingApproval: task.status === "waiting_approval",
    paused: task.status === "paused",
    recoveryNeeded: recovering,
    failed: FAILED_TASK_STATUSES.includes(visibleStatus),
    completed: task.status === "completed",
  };
}

/** Update server-provided full counts after replacing one visible task. */
export function replaceProjectedTaskCounts(
  counts: EchoTaskCounts,
  previous: EchoTaskProjection,
  next: EchoTaskProjection,
): EchoTaskCounts {
  const updated = { ...counts };
  const before = taskCountFlags(previous);
  const after = taskCountFlags(next);
  for (const key of Object.keys(before) as IncrementalCountKey[]) {
    if (before[key] === after[key]) continue;
    updated[key] = Math.max(0, updated[key] + (after[key] ? 1 : -1));
  }
  return updated;
}

function applyTaskAction(
  current: EchoTaskProjectionResponse,
  result: EchoTaskProjection,
  auditIntegrity: EchoTaskProjectionResponse["auditIntegrity"],
): EchoTaskProjectionResponse {
  const index = current.tasks.findIndex((task) => task.id === result.id);
  if (index < 0) {
    return { ...current, auditIntegrity };
  }
  const previous = current.tasks[index]!;
  const tasks = current.tasks.slice();
  tasks[index] = result;
  return {
    ...current,
    generatedAt: new Date().toISOString(),
    auditIntegrity,
    counts: replaceProjectedTaskCounts(current.counts, previous, result),
    tasks,
  };
}

export function useEchoTaskProjection(enabled = true) {
  const actor = currentActorId();
  const actorRef = useRef(actor);
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
      return applyTaskAction(current, result.task, result.auditIntegrity);
    });
    return result;
  }, []);
  const resumeExecution = useCallback(
    async (taskId: string, reason: string) => {
      const result = await resumeEchoTaskExecution(taskId, reason);
      setProjection((current) => {
        if (!current) return current;
        return applyTaskAction(current, result.task, result.auditIntegrity);
      });
      return result;
    },
    [],
  );
  const decideApproval = useCallback(
    async (taskId: string, approved: boolean, reason: string) => {
      const result = await decideEchoTaskApproval(taskId, approved, reason);
      setRevision((value) => value + 1);
      return result;
    },
    [],
  );

  useEffect(() => {
    const actorChanged = actorRef.current !== actor;
    actorRef.current = actor;
    if (!enabled) {
      // A disabled projection must not survive an auth/session transition.
      // Without clearing it here, a new device operator could briefly see the
      // previous operator's task snapshot while the first authorized poll is
      // still in flight.
      setProjection(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (actorChanged) {
      // A new authenticated principal must never see the previous principal's
      // task snapshot while its first authorized poll is in flight.
      setProjection(null);
      setError(null);
      setLoading(true);
    }
    let alive = true;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    let pollInFlight = false;
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
    const schedule = () => {
      if (!alive) return;
      const delay = document.visibilityState === "hidden" ? 30_000 : 5_000;
      timer = window.setTimeout(async () => {
        timer = null;
        if (!alive) return;
        if (!pollInFlight) {
          pollInFlight = true;
          try {
            await poll(false);
          } finally {
            pollInFlight = false;
          }
        }
        schedule();
      }, delay);
    };
    const rescheduleOnVisibilityChange = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      schedule();
    };
    void poll(true);
    document.addEventListener("visibilitychange", rescheduleOnVisibilityChange);
    schedule();
    return () => {
      alive = false;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener(
        "visibilitychange",
        rescheduleOnVisibilityChange,
      );
    };
  }, [actor, enabled, revision]);

  return {
    projection,
    loading,
    error,
    refresh,
    takeover,
    resumeExecution,
    decideApproval,
  };
}
