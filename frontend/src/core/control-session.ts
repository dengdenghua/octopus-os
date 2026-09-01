import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export type ControlSurface =
  | "browser"
  | "chrome"
  | "electron_webview"
  | "backend_preview"
  | "computer";

export type ControlIndicatorMode = "idle" | "action" | "paused";

export type ControlStopReason =
  | "operator_stop"
  | "target_changed"
  | "lease_lost"
  | "timeout"
  | "session_replaced";

export type ControlActionDescriptor =
  | string
  | {
      type?: string;
      [key: string]: unknown;
    };

export interface ControlEvidence {
  id?: string;
  kind: "action" | "result" | "screenshot" | "dom" | "lease" | "log";
  at?: number;
  action?: string;
  ok?: boolean;
  summary?: string;
  detail?: unknown;
}

export interface ControlSessionOptions {
  sessionId?: string;
  backendSync?: boolean;
  ownerLabel?: string;
  ownerId?: string;
  surface?: ControlSurface;
  targetId?: string | number | null;
  timeoutMs?: number;
  getStopped?: () => boolean | ControlStopReason;
  setIndicator?: (
    mode: ControlIndicatorMode,
    detail?: Record<string, unknown>,
  ) => void | Promise<void>;
  recordEvidence?: (evidence: ControlEvidence) => void | Promise<void>;
  now?: () => number;
}

export type ControlSessionStatus =
  | "idle"
  | "running"
  | "paused"
  | "awaiting_confirmation"
  | "stopped"
  | "expired";

export type ControlActionStatus =
  | "queued"
  | "running"
  | "waiting_user"
  | "done"
  | "failed"
  | "cancelled"
  | "expired";

export interface ControlSessionRecord {
  session_id: string;
  owner_id: string;
  owner_label: string;
  surface: ControlSurface | string;
  target_id: string;
  status: ControlSessionStatus | string;
  paused: boolean;
  takeover_count: number;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  expires_at?: number | null;
}

export interface ControlActionRecord {
  action_id: string;
  session_id: string;
  surface: ControlSurface | string;
  target_id: string;
  action_type: string;
  status: ControlActionStatus | string;
  descriptor: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string;
  queued_at: number;
  started_at?: number | null;
  completed_at?: number | null;
  expires_at?: number | null;
}

export interface ControlSessionReplayTimelineItem {
  id: string;
  kind: "action" | "evidence" | string;
  phase: string;
  at: number;
  action_id?: string;
  evidence_id?: string;
  action?: string;
  status?: string;
  summary: string;
  cursor?: string;
  detail_href?: string;
  detail_schema?: string;
  truncated?: boolean;
}

export interface ControlSessionReplayTimeline {
  schema: "echo.control_session_replay_timeline.v1" | string;
  items: ControlSessionReplayTimelineItem[];
  count: number;
  session_id?: string;
  status?: string;
  after?: number;
  after_cursor?: string;
  next_after?: number;
  next_cursor?: string;
  has_more?: boolean;
}

export interface ControlSessionReplay {
  schema: "echo.control_session_replay.v1" | string;
  session: ControlSessionRecord;
  actions: ControlActionRecord[];
  evidence: Array<ControlEvidence & { evidence_id?: string; seq?: number }>;
  timeline?: ControlSessionReplayTimeline;
  playwright_script?: string;
}

export interface ControlSessionEvidenceDetail {
  schema: "echo.control_evidence_detail.v1" | string;
  session_id: string;
  evidence_id: string;
  source: "inline" | "blob" | string;
  detail: Record<string, unknown>;
}

export function getControlActionType(action: ControlActionDescriptor): string {
  if (typeof action === "string") return action;
  return typeof action.type === "string" && action.type
    ? action.type
    : "action";
}

export function getControlStopReason(
  control?: ControlSessionOptions,
): ControlStopReason | null {
  const stopped = control?.getStopped?.();
  if (!stopped) return null;
  return typeof stopped === "string" ? stopped : "operator_stop";
}

export function controlInterruptionDetail(
  reason: ControlStopReason,
  control?: ControlSessionOptions,
): Record<string, unknown> {
  return compactRecord({
    code: "control_session_interrupted",
    reason,
    sessionId: control?.sessionId,
    ownerLabel: control?.ownerLabel,
    surface: control?.surface,
    targetId: control?.targetId,
  });
}

function compactRecord(
  record: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(record).filter(([, value]) => value !== undefined),
  );
}

function controlBaseURL() {
  return `${getBackendBaseURL()}/api/control-sessions`;
}

function canSyncBackend(control?: ControlSessionOptions): boolean {
  return Boolean(control?.sessionId && control.backendSync !== false);
}

function controlActionId(control: ControlSessionOptions, actionType: string) {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return `control-${String(control.sessionId).slice(0, 48)}-${actionType}-${random}`
    .replace(/[^A-Za-z0-9._:@-]+/g, "-")
    .slice(0, 220);
}

async function fetchControlJson<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const response = await fetch(`${controlBaseURL()}${path}`, {
    ...init,
    headers: {
      ...(init.body ? jsonAuthHeaders() : authHeaders()),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `control session request failed: ${response.status}${text ? ` ${text}` : ""}`,
    );
  }
  return (await response.json()) as T;
}

async function bestEffort<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

export async function ensureControlSession(
  control: ControlSessionOptions,
  status: ControlSessionStatus = "idle",
  metadata: Record<string, unknown> = {},
): Promise<ControlSessionRecord | null> {
  if (!canSyncBackend(control)) return null;
  const data = await bestEffort(() =>
    fetchControlJson<{ session: ControlSessionRecord }>("", {
      method: "POST",
      body: JSON.stringify({
        session_id: control.sessionId,
        owner_id: control.ownerId || control.ownerLabel || "agent",
        owner_label: control.ownerLabel || control.ownerId || "Agent",
        surface: control.surface || "browser",
        target_id:
          control.targetId === undefined || control.targetId === null
            ? "default"
            : String(control.targetId),
        status,
        metadata,
      }),
    }),
  );
  return data?.session ?? null;
}

export async function appendControlSessionAction(
  control: ControlSessionOptions,
  action: ControlActionDescriptor,
  status: ControlActionStatus = "running",
  actionId?: string,
): Promise<ControlActionRecord | null> {
  if (!canSyncBackend(control)) return null;
  const actionType = getControlActionType(action);
  const data = await bestEffort(() =>
    fetchControlJson<{ action: ControlActionRecord }>(
      `/${encodeURIComponent(String(control.sessionId))}/actions`,
      {
        method: "POST",
        body: JSON.stringify({
          action_id: actionId,
          action_type: actionType,
          status,
          surface: control.surface,
          target_id:
            control.targetId === undefined || control.targetId === null
              ? undefined
              : String(control.targetId),
          descriptor:
            typeof action === "string"
              ? { type: action }
              : { ...action, type: actionType },
        }),
      },
    ),
  );
  return data?.action ?? null;
}

export async function updateControlSessionAction(
  control: ControlSessionOptions,
  actionId: string,
  status: ControlActionStatus,
  result: Record<string, unknown> = {},
  error = "",
): Promise<ControlActionRecord | null> {
  if (!canSyncBackend(control) || !actionId) return null;
  const data = await bestEffort(() =>
    fetchControlJson<{ action: ControlActionRecord }>(
      `/${encodeURIComponent(String(control.sessionId))}/actions/${encodeURIComponent(
        actionId,
      )}`,
      {
        method: "PATCH",
        body: JSON.stringify({ status, result, error }),
      },
    ),
  );
  return data?.action ?? null;
}

export async function appendControlSessionEvidence(
  control: ControlSessionOptions,
  evidence: ControlEvidence & { actionId?: string },
): Promise<void> {
  if (!canSyncBackend(control)) return;
  await bestEffort(() =>
    fetchControlJson<{ ok: boolean }>(
      `/${encodeURIComponent(String(control.sessionId))}/evidence`,
      {
        method: "POST",
        body: JSON.stringify({
          evidence_id: evidence.id,
          action_id: evidence.actionId || "",
          kind: evidence.kind,
          action: evidence.action || "",
          ok: evidence.ok,
          summary: evidence.summary || "",
          detail:
            evidence.detail && typeof evidence.detail === "object"
              ? (evidence.detail as Record<string, unknown>)
              : { value: evidence.detail },
          created_at: evidence.at ? evidence.at / 1000 : undefined,
        }),
      },
    ),
  );
}

export async function getControlSessionReplay(
  sessionId: string,
): Promise<ControlSessionReplay> {
  return fetchControlJson<ControlSessionReplay>(
    `/${encodeURIComponent(sessionId)}/replay`,
    { method: "GET" },
  );
}

export async function setControlSessionState(
  sessionId: string,
  action: "pause" | "resume" | "stop" | "takeover",
  reason: string,
): Promise<ControlSessionRecord> {
  const data = await fetchControlJson<{ session: ControlSessionRecord }>(
    `/${encodeURIComponent(sessionId)}/${action}`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
  return data.session;
}

export async function getControlSessionTimeline(
  sessionId: string,
  options: { after?: number; afterCursor?: string; limit?: number } = {},
): Promise<ControlSessionReplayTimeline> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.after !== undefined) params.set("after", String(options.after));
  if (options.afterCursor !== undefined)
    params.set("after_cursor", options.afterCursor);
  const query = params.toString();
  return fetchControlJson<ControlSessionReplayTimeline>(
    `/${encodeURIComponent(sessionId)}/timeline${query ? `?${query}` : ""}`,
    { method: "GET" },
  );
}

function controlTimelineItemKey(
  item: ControlSessionReplayTimelineItem,
  fallbackIndex: number,
): string {
  if (item.cursor) return `cursor:${item.cursor}`;
  if (item.id) return `id:${item.id}`;
  return `anonymous:${item.kind}:${item.phase}:${item.at}:${fallbackIndex}`;
}

function controlTimelineSortKey(
  item: ControlSessionReplayTimelineItem,
): [number, string] {
  const at = Number.isFinite(item.at) ? item.at : 0;
  return [at, item.id || item.cursor || item.summary || ""];
}

export function mergeControlSessionTimelineItems(
  existing: ControlSessionReplayTimelineItem[],
  incoming: ControlSessionReplayTimelineItem[],
): ControlSessionReplayTimelineItem[] {
  const byKey = new Map<string, ControlSessionReplayTimelineItem>();
  const idToKey = new Map<string, string>();

  const add = (item: ControlSessionReplayTimelineItem, index: number) => {
    const key = controlTimelineItemKey(item, index);
    const idKey = item.id ? idToKey.get(item.id) : undefined;
    const finalKey = idKey || key;
    if (item.id) idToKey.set(item.id, finalKey);
    byKey.set(finalKey, { ...(byKey.get(finalKey) || {}), ...item });
  };

  existing.forEach(add);
  incoming.forEach((item, index) => add(item, existing.length + index));

  return Array.from(byKey.values()).sort((left, right) => {
    const [leftAt, leftId] = controlTimelineSortKey(left);
    const [rightAt, rightId] = controlTimelineSortKey(right);
    if (leftAt !== rightAt) return leftAt - rightAt;
    return leftId.localeCompare(rightId);
  });
}

export function mergeControlSessionTimeline(
  existing: ControlSessionReplayTimeline | null | undefined,
  incoming: ControlSessionReplayTimeline,
): ControlSessionReplayTimeline {
  if (!existing) return { ...incoming, count: incoming.items.length };
  const items = mergeControlSessionTimelineItems(
    existing.items || [],
    incoming.items || [],
  );
  return {
    ...existing,
    ...incoming,
    items,
    count: items.length,
    after: existing.after ?? incoming.after,
    after_cursor: existing.after_cursor ?? incoming.after_cursor,
    next_after: incoming.next_after ?? existing.next_after,
    next_cursor: incoming.next_cursor ?? existing.next_cursor,
    has_more: incoming.has_more ?? existing.has_more,
  };
}

export async function getControlSessionEvidenceDetail(
  sessionId: string,
  evidenceId: string,
): Promise<ControlSessionEvidenceDetail> {
  return fetchControlJson<ControlSessionEvidenceDetail>(
    `/${encodeURIComponent(sessionId)}/evidence/${encodeURIComponent(
      evidenceId,
    )}/detail`,
    { method: "GET" },
  );
}

export async function runControlSessionAction<T>(
  action: ControlActionDescriptor,
  run: () => Promise<T>,
  options: {
    control?: ControlSessionOptions;
    interrupted: (reason: ControlStopReason) => T;
  },
): Promise<T> {
  const { control } = options;
  const actionType = getControlActionType(action);
  const now = control?.now ?? Date.now;
  const actionId =
    control && canSyncBackend(control)
      ? controlActionId(control, actionType)
      : "";
  const stoppedBefore = getControlStopReason(control);
  if (stoppedBefore) {
    await control?.setIndicator?.("paused", {
      action: actionType,
      reason: stoppedBefore,
    });
    await control?.recordEvidence?.({
      kind: "result",
      at: now(),
      action: actionType,
      ok: false,
      summary: `interrupted:${stoppedBefore}`,
      detail: controlInterruptionDetail(stoppedBefore, control),
    });
    if (control) {
      await appendControlSessionEvidence(control, {
        kind: "result",
        at: now(),
        action: actionType,
        ok: false,
        summary: `interrupted:${stoppedBefore}`,
        detail: controlInterruptionDetail(stoppedBefore, control),
      });
    }
    return options.interrupted(stoppedBefore);
  }

  if (control) {
    await ensureControlSession(control, "running", { source: "frontend" });
    await appendControlSessionAction(control, action, "running", actionId);
  }
  await control?.setIndicator?.(
    "action",
    compactRecord({
      action: actionType,
      surface: control.surface,
      targetId: control.targetId,
      sessionId: control.sessionId,
      ownerLabel: control.ownerLabel,
    }),
  );
  await control?.recordEvidence?.({
    kind: "action",
    at: now(),
    action: actionType,
    summary: "started",
  });
  if (control) {
    await appendControlSessionEvidence(control, {
      actionId,
      kind: "action",
      at: now(),
      action: actionType,
      summary: "started",
    });
  }

  try {
    const result = await run();
    const stoppedAfter = getControlStopReason(control);
    if (stoppedAfter) {
      await control?.setIndicator?.("paused", {
        action: actionType,
        reason: stoppedAfter,
      });
      await control?.recordEvidence?.({
        kind: "result",
        at: now(),
        action: actionType,
        ok: false,
        summary: `interrupted:${stoppedAfter}`,
        detail: controlInterruptionDetail(stoppedAfter, control),
      });
      if (control) {
        await updateControlSessionAction(
          control,
          actionId,
          "cancelled",
          {},
          `interrupted:${stoppedAfter}`,
        );
        await appendControlSessionEvidence(control, {
          actionId,
          kind: "result",
          at: now(),
          action: actionType,
          ok: false,
          summary: `interrupted:${stoppedAfter}`,
          detail: controlInterruptionDetail(stoppedAfter, control),
        });
      }
      return options.interrupted(stoppedAfter);
    }
    await control?.recordEvidence?.({
      kind: "result",
      at: now(),
      action: actionType,
      ok: true,
      summary: "completed",
    });
    if (control) {
      await updateControlSessionAction(control, actionId, "done", {
        completed: true,
      });
      await appendControlSessionEvidence(control, {
        actionId,
        kind: "result",
        at: now(),
        action: actionType,
        ok: true,
        summary: "completed",
      });
    }
    return result;
  } catch (error) {
    if (control) {
      await updateControlSessionAction(
        control,
        actionId,
        "failed",
        {},
        error instanceof Error ? error.message : String(error),
      );
      await appendControlSessionEvidence(control, {
        actionId,
        kind: "result",
        at: now(),
        action: actionType,
        ok: false,
        summary: error instanceof Error ? error.message : String(error),
      });
    }
    throw error;
  } finally {
    if (!getControlStopReason(control)) {
      await control?.setIndicator?.(
        "idle",
        compactRecord({
          action: actionType,
          sessionId: control?.sessionId,
        }),
      );
    }
  }
}
