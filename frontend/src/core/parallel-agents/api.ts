/**
 * Single source of truth for the `/api/agents/parallel/*` backend contract
 * and its fetchers. Previously these types and polling calls were duplicated
 * between `parallel-agents-panel.tsx` (ops monitoring UI) and
 * `swarm/live-driver.ts` (Kimi-style workbench). Both now consume this module.
 */
import { swallow } from "@/core/utils/log";
import { getToken } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";
import { openSseStream } from "@/core/streaming/sse";

// ---------------------------------------------------------------------------
// Backend shapes
// ---------------------------------------------------------------------------

export type ParallelTaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out"
  | "partial";

export interface TaskResult {
  task_id: string;
  batch_id: string;
  description?: string | null;
  status: ParallelTaskStatus | string;
  result: string | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  subagent_name: string;
  work_contract?: WorkContract | null;
}

export interface SubagentRouteDecision {
  schema: "echo.subagent_route_decision.v1";
  role: string;
  action: "allow" | "allow_with_warning" | "block" | string;
  reason: string;
  risk_level: "low" | "medium" | "high" | "critical" | string;
  verdict: string;
  score: number | null;
  confidence: number;
  evidence_item_ids: string[];
}

export interface WorkContract {
  contract_id: string;
  agent_id: string;
  role: string;
  task_ids: string[];
  depends_on: string[];
  owned_scope: string[];
  forbidden_scope: string[];
  write_paths?: string[];
  success_criteria: string[];
}

export interface BatchPhase {
  phase_index: number;
  task_ids: string[];
  parallel: boolean;
}

export interface BatchPlan {
  batch_id: string;
  strategy: string;
  max_concurrency: number;
  phases: BatchPhase[];
  contracts: WorkContract[];
}

export interface ParallelBatchCoordinationTask {
  task_id: string;
  subagent_name: string;
  status: ParallelTaskStatus | string;
  recommended_action: string;
  result_chars: number;
  error: string | null;
  depends_on: string[];
  write_paths: string[];
  duration_seconds: number | null;
}

export interface ParallelBatchCoordinationSummary {
  schema: "echo.parallel_batch_coordination.v1" | string;
  batch_id: string;
  status: ParallelTaskStatus | string;
  ready: boolean;
  primary_task_id: string | null;
  recommended_next_action: string;
  completed_task_ids: string[];
  failed_task_ids: string[];
  cancelled_task_ids: string[];
  dependency_blocked_task_ids: string[];
  conflict_count: number;
  contract_issue_count: number;
  contract_warning_count: number;
  output_present: boolean;
  aggregation_strategy: string;
  tasks: ParallelBatchCoordinationTask[];
  checkpoint: {
    batch_id?: string;
    after_sequence?: number;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface BatchResult {
  batch_id: string;
  status: ParallelTaskStatus | string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  created_at: string | null;
  completed_at: string | null;
  results: TaskResult[];
  aggregated_content: string | null;
  aggregation_strategy: string | null;
  conflicts: string[];
  plan?: BatchPlan | null;
  event_log?: BatchStreamEvent[];
  completion_receipt?: Record<string, unknown>;
  file_write_observability?: Record<string, unknown>;
  coordination_summary?: ParallelBatchCoordinationSummary;
}

export interface BatchRecoveryTask {
  task_id: string;
  status: ParallelTaskStatus | string;
  subagent_name: string;
  depends_on: string[];
  priority: number;
  write_paths: string[];
  description_preview?: string | null;
  result_preview?: string | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  artifact_paths: string[];
  work_contract?: WorkContract | null;
  route_decision?: Record<string, unknown>;
}

export interface BatchRecoverySnapshot {
  schema: "echo.parallel_batch_recovery_snapshot.v1" | string;
  batch_id: string;
  status: ParallelTaskStatus | string;
  terminal: boolean;
  resume_available: boolean;
  created_at: string | null;
  completed_at: string | null;
  task_count: number;
  completed_tasks: number;
  failed_tasks: number;
  cancelled_tasks: number;
  running_tasks: number;
  pending_tasks: number;
  tasks: BatchRecoveryTask[];
  dag: Record<string, string[]>;
  plan?: BatchPlan | null;
  event_sequence: {
    event_count?: number;
    first_sequence?: number | null;
    last_sequence?: number | null;
    next_after_sequence?: number;
    types?: Record<string, number>;
    [key: string]: unknown;
  };
  artifact_paths: string[];
  conflicts: string[];
  completion_receipt: Record<string, unknown>;
  file_write_observability: Record<string, unknown>;
  coordination_summary?: ParallelBatchCoordinationSummary;
  recovery_hints: {
    rerunnable_task_ids?: string[];
    failed_task_ids?: string[];
    cancelled_task_ids?: string[];
    pending_task_ids?: string[];
    running_task_ids?: string[];
    blocked_by_dependency?: string[];
    checkpoint?: {
      batch_id?: string;
      after_sequence?: number;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  safety: {
    raw_subagent_outputs_included?: boolean;
    event_payloads_included?: boolean;
    owner_id_included?: boolean;
    result_preview_max_chars?: number;
    description_preview_max_chars?: number;
    [key: string]: unknown;
  };
}

export interface OrchestratorStatus {
  active_count: number;
  pending_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  max_concurrency: number;
  batches: Record<string, string>;
}

export interface SplitTask {
  task_id: string;
  description: string;
  subagent_name: string;
  depends_on: string[];
  priority: number;
}

export interface SplitResult {
  tasks: SplitTask[];
  dag_levels: string[][];
  total_levels: number;
  is_parallelizable: boolean;
}

// ---------------------------------------------------------------------------
// Fetchers — return null on network/HTTP failure so callers can degrade
// gracefully instead of tearing down their render tree.
// ---------------------------------------------------------------------------

// Shared fetch options — include cookies for session-based auth and Bearer
// token for token-based auth. Match the rest of the app's auth pattern.
export function toBackendURL(pathOrUrl: string): string {
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
  return `${getBackendBaseURL()}${pathOrUrl}`;
}

async function authedFetch(
  pathOrUrl: string,
  init?: RequestInit,
): Promise<Response> {
  const token = getToken();
  return fetch(toBackendURL(pathOrUrl), {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
}

export async function fetchOrchestratorStatus(): Promise<OrchestratorStatus | null> {
  try {
    const res = await authedFetch("/api/agents/parallel/status");
    if (!res.ok) return null;
    return (await res.json()) as OrchestratorStatus;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export async function fetchBatch(batchId: string): Promise<BatchResult | null> {
  try {
    const res = await authedFetch(`/api/agents/parallel/batch/${batchId}`);
    if (!res.ok) return null;
    return (await res.json()) as BatchResult;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export async function fetchBatchRecoverySnapshot(
  batchId: string,
): Promise<BatchRecoverySnapshot | null> {
  try {
    const res = await authedFetch(
      `/api/agents/parallel/batch/${batchId}/recovery-snapshot`,
    );
    if (!res.ok) return null;
    return (await res.json()) as BatchRecoverySnapshot;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export async function cancelTask(taskId: string): Promise<boolean> {
  try {
    const res = await authedFetch(`/api/agents/parallel/cancel/${taskId}`, {
      method: "POST",
    });
    return res.ok;
  } catch (e) {
    swallow(e);
    return false;
  }
}

export async function cancelAll(): Promise<boolean> {
  try {
    const res = await authedFetch("/api/agents/parallel/cancel-all", {
      method: "POST",
    });
    return res.ok;
  } catch (e) {
    swallow(e);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Status helpers — shared visual language across UIs.
// ---------------------------------------------------------------------------

export const STATUS_TEXT_COLOR: Record<string, string> = {
  pending: "text-muted-foreground",
  running: "text-info",
  completed: "text-success",
  failed: "text-destructive",
  cancelled: "text-warning",
  timed_out: "text-warning",
  partial: "text-warning",
};

export const STATUS_BG: Record<string, string> = {
  pending: "bg-muted/50",
  running: "bg-info/10",
  completed: "bg-success/10",
  failed: "bg-destructive/10",
  cancelled: "bg-warning/10",
  timed_out: "bg-warning/10",
  partial: "bg-warning/10",
};

/**
 * Pick the "most interesting" batch for a surface that shows a single batch.
 * Prefers a running batch; otherwise the first batch entry (typically most
 * recently touched). Returns null if the orchestrator has no batches.
 */
export async function fetchFocusBatch(): Promise<BatchResult | null> {
  const status = await fetchOrchestratorStatus();
  if (!status) return null;
  const running = Object.entries(status.batches).find(
    ([, s]) => s === "running",
  );
  const entry = running ?? Object.entries(status.batches)[0];
  if (!entry) return null;
  return fetchBatch(entry[0]);
}

// ---------------------------------------------------------------------------
// SSE streaming — real-time batch progress via EventSource
// ---------------------------------------------------------------------------

export interface BatchStreamEvent {
  type: "stage_change" | "task_update" | "tool_call" | "batch_complete";
  batch_id: string;
  sequence?: number | null;
  created_at?: string | null;
  task_id?: string;
  lane?: "workflow" | "agent" | "computer" | "timeline";
  status?: string;
  subagent_name?: string;
  phase?: string;
  stage?: string;
  tool_name?: string;
  tool_input_preview?: string;
  tool_output_preview?: string;
  artifact_paths?: string[];
  node_ids?: string[];
  payload?: Record<string, unknown>;
  progress?: number;
  message?: string;
  description?: string;
  result_preview?: string;
  duration_seconds?: number;
  error?: string;
}

/**
 * Callbacks for SSE batch streaming events.
 */
export interface BatchStreamCallbacks {
  onStageChange?: (event: BatchStreamEvent) => void;
  onTaskUpdate?: (event: BatchStreamEvent) => void;
  onBatchComplete?: (event: BatchStreamEvent) => void;
  onError?: (error: Error) => void;
  onReconnecting?: (attempt: number) => void;
}

function streamURL(batchId: string, afterSequence: number): string {
  const base = `/api/agents/parallel/stream/${batchId}`;
  if (afterSequence <= 0) return base;
  return `${base}?after_sequence=${afterSequence}`;
}

/**
 * Subscribe to real-time SSE events for a parallel agent batch.
 *
 * Built on the shared ``openSseStream`` transport: Bearer token auth
 * via header (never the URL), jittered exponential backoff reconnect,
 * and ``after_sequence`` resume on reconnect so no events are missed
 * or replayed.
 *
 * @param batchId - The batch ID to subscribe to
 * @param callbacks - Event callbacks (onTaskUpdate, onBatchComplete, onError, onReconnecting)
 * @param options - Retry options (maxRetries, baseDelay)
 * @returns Cleanup function that aborts the SSE connection
 */
export function streamBatch(
  batchId: string,
  callbacks: BatchStreamCallbacks,
  options?: { maxRetries?: number; baseDelay?: number },
): () => void {
  let lastSequence = 0;
  return openSseStream({
    url: () => toBackendURL(streamURL(batchId, lastSequence)),
    maxRetries: options?.maxRetries ?? 3,
    initialBackoffMs: options?.baseDelay ?? 1000,
    onReconnecting: (attempt) => callbacks.onReconnecting?.(attempt),
    onError: (err) => callbacks.onError?.(err),
    onEvent: (msg) => {
      let data: BatchStreamEvent;
      try {
        data = JSON.parse(msg.data) as BatchStreamEvent;
      } catch (e) {
        swallow(e);
        return;
      }
      if (
        typeof data.sequence === "number" &&
        data.sequence <= lastSequence
      ) {
        return;
      }
      if (typeof data.sequence === "number") {
        lastSequence = data.sequence;
      }
      if (msg.event === "stage_change") {
        callbacks.onStageChange?.(data);
      } else if (msg.event === "task_update" || msg.event === "tool_call") {
        callbacks.onTaskUpdate?.(data);
      } else if (msg.event === "batch_complete") {
        callbacks.onBatchComplete?.(data);
        return true;
      }
    },
  });
}

export interface DispatchTaskInput {
  description: string;
  subagent_name?: string;
  task_id?: string;
  depends_on?: string[];
  priority?: number;
}

/**
 * Dispatch either a single prompt (wrapped as one task) or a pre-split
 * list of subtasks. When the backend's `/split` step has already produced
 * subtasks, pass them directly.
 */
export async function dispatchParallel(
  tasksOrPrompt: string | DispatchTaskInput[],
  options?: {
    subagent_name?: string;
    max_concurrency?: number;
    aggregation_strategy?: string;
    execution_mode?: string;
    thread_id?: string;
    model_name?: string;
  },
): Promise<BatchResult | null> {
  const tasks: DispatchTaskInput[] =
    typeof tasksOrPrompt === "string"
      ? [
          {
            description: tasksOrPrompt,
            subagent_name: options?.subagent_name ?? "general-purpose",
          },
        ]
      : tasksOrPrompt;
  try {
    const res = await authedFetch("/api/agents/parallel/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tasks,
        max_concurrency: options?.max_concurrency,
        aggregation_strategy: options?.aggregation_strategy,
        execution_mode: options?.execution_mode,
        thread_id: options?.thread_id,
        model_name: options?.model_name,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as BatchResult;
  } catch (e) {
    swallow(e);
    return null;
  }
}

export async function splitTask(
  prompt: string,
  options?: {
    max_subtasks?: number;
    context?: string;
    model_name?: string;
  },
): Promise<SplitResult | null> {
  try {
    const res = await authedFetch("/api/agents/parallel/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task: prompt,
        max_subtasks: options?.max_subtasks,
        context: options?.context,
        model_name: options?.model_name,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as SplitResult;
  } catch (e) {
    swallow(e);
    return null;
  }
}
