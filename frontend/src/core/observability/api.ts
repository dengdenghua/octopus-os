import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { openSseStream } from "@/core/streaming/sse";

import type {
  ActiveAlert,
  AlertRule,
  MetricsSummary,
  Span,
  TelemetryStats,
  TraceSummary,
} from "./types";

/**
 * Process-global observability is intentionally separate from tenant-scoped
 * journal/progress/budget views.  The server requires both this explicit
 * opt-in and a principal with cross-tenant admin permission; keeping URL
 * construction here prevents individual panels from silently falling back to
 * an unscoped request that will always be rejected in shared deployments.
 */
export const GLOBAL_CONTROL_PLANE_ACCESS_CODE =
  "cross_tenant_admin_required" as const;

export class GlobalControlPlaneAccessError extends Error {
  readonly status = 403;
  readonly code = GLOBAL_CONTROL_PLANE_ACCESS_CODE;

  constructor() {
    super(GLOBAL_CONTROL_PLANE_ACCESS_CODE);
    this.name = "GlobalControlPlaneAccessError";
  }
}

export function globalControlPlaneUrl(path: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${getBackendBaseURL()}${path}${separator}cross_tenant=true`;
}

export async function requireGlobalControlPlaneResponse(
  response: Response,
  fallback: string,
): Promise<void> {
  if (response.ok) return;
  if (response.status === 403) {
    throw new GlobalControlPlaneAccessError();
  }
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string;
  };
  throw new Error(payload.detail ?? `${fallback}: ${response.status}`);
}

export async function getMetrics(): Promise<Record<string, unknown>> {
  const res = await fetch(`${getBackendBaseURL()}/api/metrics`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get metrics: ${res.statusText}`);
  return (await res.json()) as Record<string, unknown>;
}

export async function getMetricsSummary(): Promise<MetricsSummary> {
  const res = await fetch(`${getBackendBaseURL()}/api/metrics/summary`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get metrics summary: ${res.statusText}`);
  return (await res.json()) as MetricsSummary;
}

export async function getTraces(limit = 100): Promise<TraceSummary[]> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/trace/recent?limit=${limit}`,
    {
      headers: authHeaders(),
    },
  );
  if (!res.ok) throw new Error(`Failed to get traces: ${res.statusText}`);
  return (await res.json()) as TraceSummary[];
}

export async function getTrace(traceId: string): Promise<Span[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/trace/${traceId}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get trace: ${res.statusText}`);
  return (await res.json()) as Span[];
}

export async function getAlerts(): Promise<ActiveAlert[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/alerts`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get alerts: ${res.statusText}`);
  return (await res.json()) as ActiveAlert[];
}

export async function getAlertRules(): Promise<AlertRule[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/alerts/rules`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to get alert rules: ${res.statusText}`);
  return (await res.json()) as AlertRule[];
}

export async function createAlertRule(rule: AlertRule): Promise<AlertRule> {
  const res = await fetch(`${getBackendBaseURL()}/api/alerts/rules`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify(rule),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to create alert rule: ${res.statusText}`,
    );
  }
  return (await res.json()) as AlertRule;
}

export async function deleteAlertRule(
  name: string,
): Promise<{ success: boolean; name: string }> {
  const res = await fetch(`${getBackendBaseURL()}/api/alerts/rules/${name}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to delete alert rule: ${res.statusText}`);
  return (await res.json()) as { success: boolean; name: string };
}

export async function getTelemetryStats(): Promise<TelemetryStats> {
  const res = await fetch(`${getBackendBaseURL()}/api/telemetry/stats`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get telemetry stats: ${res.statusText}`);
  return (await res.json()) as TelemetryStats;
}

export async function getObservabilityHealth(): Promise<
  Record<string, unknown>
> {
  const res = await fetch(`${getBackendBaseURL()}/api/observability/health`, {
    headers: authHeaders(),
  });
  if (!res.ok)
    throw new Error(`Failed to get observability health: ${res.statusText}`);
  return (await res.json()) as Record<string, unknown>;
}

export type ToolEffectState =
  | "claimed"
  | "started"
  | "committed"
  | "indeterminate"
  | "retry_authorized";

export interface ToolEffectReceipt {
  effect_key: string;
  task_id: string;
  step_id: number;
  sucker_id: string;
  side_effecting: boolean;
  state: ToolEffectState;
  holder_id: string;
  fencing_token: number;
  lease_expires_at: number;
  call_id: string;
  reason: string;
  updated_at: number;
  has_result: boolean;
}

export interface ToolEffectsSnapshot {
  backend: string;
  shared_across_hosts: boolean;
  can_authorize_retry: boolean;
  count: number;
  state_counts: Partial<Record<ToolEffectState, number>>;
  receipts: ToolEffectReceipt[];
}

export interface ToolEffectAuthorizationResponse {
  ok: boolean;
  effect_key: string;
  state: "retry_authorized";
  fencing_token: number;
  actor: string;
  audit_warning: string;
}

export async function getToolEffectsSnapshot({
  limit = 100,
  signal,
}: {
  limit?: number;
  signal?: AbortSignal;
} = {}): Promise<ToolEffectsSnapshot> {
  const safeLimit = Math.max(1, Math.min(Math.trunc(limit), 500));
  const res = await fetch(
    globalControlPlaneUrl(`/api/tool-effects?limit=${safeLimit}`),
    { headers: authHeaders(), signal },
  );
  await requireGlobalControlPlaneResponse(res, "Failed to load tool effects");
  return (await res.json()) as ToolEffectsSnapshot;
}

export async function authorizeToolEffectRetry(
  receipt: ToolEffectReceipt,
  reason: string,
): Promise<ToolEffectAuthorizationResponse> {
  const path = encodeURIComponent(receipt.effect_key);
  const res = await fetch(
    globalControlPlaneUrl(`/api/tool-effects/${path}/authorize-retry`),
    {
      method: "POST",
      headers: jsonAuthHeaders(),
      body: JSON.stringify({
        confirm: "AUTHORIZE RETRY",
        fencing_token: receipt.fencing_token,
        reason,
      }),
    },
  );
  if (!res.ok) {
    if (res.status === 403) {
      throw new GlobalControlPlaneAccessError();
    }
    const error = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(error.detail ?? `Failed to authorize retry: ${res.status}`);
  }
  return (await res.json()) as ToolEffectAuthorizationResponse;
}

// Implementation note.
// Implementation note.
// Implementation note.

export interface EvolutionStatus {
  enabled: boolean;
  reason?: string;
  rules_count?: number;
  memories_count?: number;
  rules_section?: string;
  memories_section?: string;
  rules_lines?: string[];
  memories_lines?: string[];
  trajectories?: {
    total: number;
    react_loop: number;
    react_loop_failures: number;
  };
  react_variants?: ReActVariantStat[];
}

export interface ReActVariantStat {
  name: string;
  max_iterations: number;
  temperature: number;
  assignments: number;
  successes: number;
  failures: number;
  success_rate: number;
}

export async function getEvolutionStatus(
  signal?: AbortSignal,
): Promise<EvolutionStatus> {
  const res = await fetch(globalControlPlaneUrl("/api/evolution/status"), {
    headers: authHeaders(),
    signal,
  });
  await requireGlobalControlPlaneResponse(
    res,
    "Failed to get evolution status",
  );
  return (await res.json()) as EvolutionStatus;
}

export interface ReflectionReport {
  skill_forge?: unknown;
  rule_extractor?: { rules: number };
  kg?: { accepted: number; total: number };
  memory?: { memories: number };
  workflow?: { proposals: number; by_kind?: Record<string, number> };
  recipe?: { recipes: number; best: string | null };
  error?: string;
}

/* Implementation note. */
export async function kickReflection(): Promise<ReflectionReport> {
  const res = await fetch(`${getBackendBaseURL()}/api/reflect`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Failed to kick reflection: ${res.statusText}`);
  }
  return (await res.json()) as ReflectionReport;
}

export async function forgetRule(
  index: number,
): Promise<{ dropped: string; remaining: number }> {
  const res = await fetch(
    globalControlPlaneUrl(`/api/evolution/rules/${index}`),
    { method: "DELETE", headers: authHeaders() },
  );
  await requireGlobalControlPlaneResponse(res, "Failed to delete rule");
  return (await res.json()) as { dropped: string; remaining: number };
}

export async function forgetMemory(
  index: number,
): Promise<{ dropped: string; remaining: number }> {
  const res = await fetch(
    globalControlPlaneUrl(`/api/evolution/memories/${index}`),
    { method: "DELETE", headers: authHeaders() },
  );
  await requireGlobalControlPlaneResponse(res, "Failed to delete memory");
  return (await res.json()) as { dropped: string; remaining: number };
}

// Implementation note.

export interface FileOpEvent {
  event_type: "file_op";
  ts: string;
  task_id: string | null;
  arm_id: string | null;
  path: string;
  action: "create" | "write" | "edit" | "delete" | "rename";
  bytes_delta: number;
  old_size: number | null;
  new_size: number | null;
  sucker_id: string;
  /* Implementation note. */
  diff: string | null;
}

/* Implementation note. */
export function subscribeFileOps(
  onEvent: (e: FileOpEvent) => void,
  onError?: (err: Error) => void,
): () => void {
  // Track the last seen SSE event id so a reconnect can resume from the
  // server's ``Last-Event-ID`` replay instead of losing the gap.
  let lastId: string | null = null;
  return openSseStream({
    url: `${getBackendBaseURL()}/api/files/stream`,
    lastEventId: () => lastId,
    onEvent: (msg) => {
      if (msg.id != null) lastId = msg.id;
      if (msg.event !== "file_op") return;
      try {
        onEvent(JSON.parse(msg.data) as FileOpEvent);
      } catch (e) {
        console.error("file_op parse failed", e, msg.data);
      }
    },
    onError,
  });
}

// ─── Preview refresh events ────────────────────────────────

export interface PreviewRefreshEvent {
  event_type: "preview_refresh";
  ts: string;
  target: string;
  trigger_path: string;
  reason: string;
}

/* Implementation note. */
export function subscribePreviewRefresh(
  onEvent: (e: PreviewRefreshEvent) => void,
  onError?: (err: Error) => void,
): () => void {
  // Track the last seen SSE event id so a reconnect resumes from the
  // server's ``Last-Event-ID`` replay instead of losing the gap.
  let lastId: string | null = null;
  return openSseStream({
    url: `${getBackendBaseURL()}/api/preview/stream`,
    lastEventId: () => lastId,
    onEvent: (msg) => {
      if (msg.id != null) lastId = msg.id;
      if (msg.event !== "preview_refresh") return;
      try {
        onEvent(JSON.parse(msg.data) as PreviewRefreshEvent);
      } catch (e) {
        console.error("preview_refresh parse failed", e, msg.data);
      }
    },
    onError,
  });
}
