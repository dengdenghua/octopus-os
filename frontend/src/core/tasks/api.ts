import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";

export type PauseReason =
  | "user_request"
  | "budget_near_limit"
  | "iteration_near_limit"
  | "model_spinning"
  | "client_disconnect"
  | "approval_required"
  | "external";

export interface PauseRequest {
  task_id: string;
  reason: PauseReason;
  requested_at: number;
  requested_by: string;
  note: string;
  thread_id: string;
  agent_id: string;
}

export interface ActiveTask {
  task_id: string;
  thread_id: string;
  agent_id: string;
  started_at: number;
  current_iteration: number;
  max_iterations: number;
  /** Cumulative model accounting across every call in this task. */
  tokens_spent: number;
  input_tokens_spent?: number;
  output_tokens_spent?: number;
  cache_read_tokens?: number;
  /** Provider-reported size of the latest live model request. */
  current_context_tokens?: number;
  context_capacity_tokens?: number;
  context_utilization?: number;
  cost_usd: number;
  max_tokens: number;
  max_usd: number;
}

export interface TasksListResponse {
  paused?: PauseRequest[];
  pending?: PauseRequest[];
  active?: ActiveTask[];
}

export interface TaskCheckpoint {
  iteration_completed: number;
  max_iterations: number;
  steps_count: number;
  has_final_answer: boolean;
}

export interface TaskDetail {
  task_id: string;
  is_pending_pause: boolean;
  is_paused: boolean;
  pause_request: PauseRequest | null;
  last_checkpoint?: TaskCheckpoint;
}

export interface ResumeTaskResponse {
  ok: boolean;
  task_id: string;
  extra_tokens: number;
  extra_iterations: number;
  message: string;
}

export async function listTasks(
  status?: "paused" | "pending" | "active" | "all",
  signal?: AbortSignal,
): Promise<TasksListResponse> {
  const qs = status ? `?status=${status}` : "";
  const res = await fetch(`${getBackendBaseURL()}/api/tasks${qs}`, {
    headers: authHeaders(),
    signal,
  });
  if (!res.ok) throw new Error(`Failed to list tasks: ${res.statusText}`);
  return (await res.json()) as TasksListResponse;
}

export async function getTask(taskId: string): Promise<TaskDetail> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/tasks/${encodeURIComponent(taskId)}`,
    { headers: authHeaders() },
  );
  if (!res.ok) throw new Error(`Failed to load task: ${res.statusText}`);
  return (await res.json()) as TaskDetail;
}

export async function pauseTask(
  taskId: string,
  reason: PauseReason = "user_request",
  note = "",
): Promise<{ ok: boolean; request: PauseRequest }> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/tasks/${encodeURIComponent(taskId)}/pause`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({ reason, note }),
    },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to pause: ${res.status} ${detail || res.statusText}`,
    );
  }
  return (await res.json()) as { ok: boolean; request: PauseRequest };
}

export async function resumeTask(
  taskId: string,
  opts: {
    extra_iterations?: number;
    extra_tokens?: number;
    extra_usd?: number;
  } = {},
): Promise<ResumeTaskResponse> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/tasks/${encodeURIComponent(taskId)}/resume`,
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify(opts),
    },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to resume: ${res.status} ${detail || res.statusText}`,
    );
  }
  return (await res.json()) as ResumeTaskResponse;
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/tasks/${encodeURIComponent(taskId)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(
      `Failed to delete: ${res.status} ${detail || res.statusText}`,
    );
  }
}
