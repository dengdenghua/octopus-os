import type { TurnStatus } from "./items";
import { DEFAULT_VITALS_THRESHOLDS, type VitalsMarks } from "./stream-vitals";

const STORAGE_KEY = "echo:stream-telemetry:v1";
const MAX_RECORDS = 100;

export const STREAM_TELEMETRY_UPDATED_EVENT =
  "echo:stream-telemetry-updated";

export type StreamTurnOutcome = Exclude<TurnStatus, "inProgress">;

export interface StreamTurnTelemetry {
  id: string;
  threadId: string;
  turnId: string;
  startedAt: number;
  completedAt: number;
  durationMs: number;
  ttftMs: number | null;
  maxDeltaGapMs: number;
  stalledAtEnd: boolean;
  outcome: StreamTurnOutcome;
  // Deltas dropped by the reducer for already-settled items. Optional
  // so records persisted by older clients still validate. Anything
  // above 0 warrants investigation — text may be silently lost.
  lateDeltaDrops?: number;
}

export interface StreamTelemetrySummary {
  count: number;
  ttftP50Ms: number | null;
  ttftP95Ms: number | null;
  maxGapP95Ms: number | null;
  stalledRate: number;
  unsuccessfulRate: number;
}

function telemetryStorage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

function isTelemetryRecord(value: unknown): value is StreamTurnTelemetry {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<StreamTurnTelemetry>;
  return (
    typeof item.id === "string" &&
    typeof item.threadId === "string" &&
    typeof item.turnId === "string" &&
    typeof item.startedAt === "number" &&
    typeof item.completedAt === "number" &&
    typeof item.durationMs === "number" &&
    (item.ttftMs === null || typeof item.ttftMs === "number") &&
    typeof item.maxDeltaGapMs === "number" &&
    typeof item.stalledAtEnd === "boolean" &&
    (item.outcome === "completed" ||
      item.outcome === "paused" ||
      item.outcome === "cancelled" ||
      item.outcome === "interrupted" ||
      item.outcome === "failed")
  );
}

export function readStreamTelemetry(
  storage: Storage | null = telemetryStorage(),
): StreamTurnTelemetry[] {
  if (!storage) return [];
  try {
    const parsed: unknown = JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isTelemetryRecord).sort((a, b) => {
      return b.completedAt - a.completedAt;
    });
  } catch {
    return [];
  }
}

export function appendStreamTelemetry(
  record: StreamTurnTelemetry,
  storage: Storage | null = telemetryStorage(),
): StreamTurnTelemetry[] {
  if (!storage) return [];
  const records = [
    record,
    ...readStreamTelemetry(storage).filter((item) => item.id !== record.id),
  ].slice(0, MAX_RECORDS);
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(records));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(STREAM_TELEMETRY_UPDATED_EVENT));
    }
  } catch {
    // Diagnostics must never interfere with chat when storage is unavailable.
  }
  return records;
}

export function clearStreamTelemetry(
  storage: Storage | null = telemetryStorage(),
): void {
  if (!storage) return;
  try {
    storage.removeItem(STORAGE_KEY);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(STREAM_TELEMETRY_UPDATED_EVENT));
    }
  } catch {
    // Telemetry is intentionally best effort.
  }
}

export function createStreamTurnTelemetry(input: {
  threadId: string;
  turnId: string;
  outcome: StreamTurnOutcome;
  marks: VitalsMarks;
  completedAt?: number;
}): StreamTurnTelemetry | null {
  const completedAt = input.completedAt ?? Date.now();
  const startedAt = input.marks.turnStartedAt;
  if (startedAt == null) return null;
  if (
    input.marks.activeTurnId != null &&
    input.marks.activeTurnId !== input.turnId
  ) {
    return null;
  }

  const sinceActivityMs =
    input.marks.lastActivityAt == null
      ? completedAt - startedAt
      : completedAt - input.marks.lastActivityAt;
  return {
    id: `${input.threadId}:${input.turnId}`,
    threadId: input.threadId,
    turnId: input.turnId,
    startedAt,
    completedAt,
    durationMs: Math.max(0, completedAt - startedAt),
    ttftMs:
      input.marks.firstDeltaAt == null
        ? null
        : Math.max(0, input.marks.firstDeltaAt - startedAt),
    maxDeltaGapMs: input.marks.maxDeltaGapMs,
    stalledAtEnd: sinceActivityMs >= DEFAULT_VITALS_THRESHOLDS.activityStaleMs,
    outcome: input.outcome,
    lateDeltaDrops: input.marks.lateDeltaDrops ?? 0,
  };
}

function percentile(values: number[], fraction: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.max(0, Math.ceil(sorted.length * fraction) - 1);
  return sorted[index] ?? null;
}

export function summarizeStreamTelemetry(
  records: StreamTurnTelemetry[],
): StreamTelemetrySummary {
  const ttft = records.flatMap((item) =>
    item.ttftMs == null ? [] : [item.ttftMs],
  );
  const count = records.length;
  return {
    count,
    ttftP50Ms: percentile(ttft, 0.5),
    ttftP95Ms: percentile(ttft, 0.95),
    maxGapP95Ms: percentile(
      records.map((item) => item.maxDeltaGapMs),
      0.95,
    ),
    stalledRate: count
      ? records.filter((item) => item.stalledAtEnd).length / count
      : 0,
    unsuccessfulRate: count
      ? records.filter((item) => item.outcome !== "completed").length / count
      : 0,
  };
}
