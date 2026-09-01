// Streaming "vitals" — turn-level liveness telemetry the UI uses to tell
// "the model is still working" apart from "the connection actually stuck".
//
// The realtime transport surfaces a rich notification stream (text deltas,
// reasoning deltas, tool progress, per-turn heartbeats) but the status strip
// historically judged liveness by a single number: milliseconds since the
// turn started. That can't distinguish a model mid-thought from a wedged
// socket — after ~12s it always read "waiting for model", alarming or not.
//
// This module records timestamps off the notification stream (see
// ``applyVitalNotification``) and classifies them into a small phase enum
// (see ``classifyVitals``). It is pure and React-free so the classification
// is unit-testable; the ticking React wrapper lives in ``use-stream-vitals``.

/** Coarse liveness state for the active turn. */
export type StreamPhase =
  // No active turn — nothing streaming.
  | "idle"
  // Text deltas are landing right now.
  | "streaming"
  // Turn is active and something is happening server-side (a tool is
  // running, reasoning is streaming, or a heartbeat/activity landed
  // recently) but no text is flowing this instant. The reassuring state.
  | "working"
  // Active turn, no first token yet, still lively — waiting on the model
  // to begin. Distinct from ``working`` only so the label can say so.
  | "waiting"
  // Connected, but no activity of any kind for a suspicious stretch and no
  // tool is running. Genuinely ambiguous — the honest "maybe stuck" state.
  | "slow"
  // The transport is down (auto-reconnect in flight). Definitely not the
  // model's fault.
  | "disconnected";

/** Mutable timestamp record accumulated off the notification stream. All
 * fields are epoch-ms (Date.now) or null when not yet observed. */
export interface VitalsMarks {
  /** Turn these marks belong to. Null only when the wire event has no id. */
  activeTurnId: string | null;
  /** ``turn/started`` — the wall-clock origin for elapsed + TTFT. */
  turnStartedAt: number | null;
  /** First ``item/agentMessage/delta`` of the turn — fixes TTFT. */
  firstDeltaAt: number | null;
  /** Most recent text delta — drives "streaming" freshness. */
  lastDeltaAt: number | null;
  /** Most recent activity of ANY kind (delta, tool progress, heartbeat).
   * Drives stall detection. */
  lastActivityAt: number | null;
  /** First observable agent-side item for this turn. Heartbeats and the
   * user's own message do not count: until this is set, the server is alive
   * but the user is still honestly waiting for the model's first response. */
  firstAgentActivityAt: number | null;
  /** Most recent ``turn/heartbeat`` — team-mode keepalive. */
  lastHeartbeatAt: number | null;
  /** Server-reported elapsed seconds from the last heartbeat, if any. */
  heartbeatElapsedS: number | null;
  /** Worst gap observed between successive text deltas this turn (ms) —
   * the "streaming interval" metric. */
  maxDeltaGapMs: number;
  /** Deltas the reducer dropped because the target item had already
   * settled. Should stay 0 — a growing count means the wire protocol
   * drifted and text is being silently lost (see reducer diagnostics). */
  lateDeltaDrops: number;
}

/** Derived, render-ready liveness snapshot. */
export interface StreamVitals {
  phase: StreamPhase;
  /** Time-to-first-token (ms). Null until the first token / no turn. */
  ttftMs: number | null;
  /** Age of the most recent text delta (ms). Infinity when none yet. */
  lastDeltaAgeMs: number;
  /** Age of the most recent activity of any kind (ms). Infinity when none. */
  sinceActivityMs: number;
  /** Elapsed wall-time of the active turn (ms). */
  elapsedMs: number;
  /** Worst inter-delta gap seen this turn (ms). */
  maxDeltaGapMs: number;
  /** True once we're in a state worth flagging (slow / disconnected). */
  stalled: boolean;
}

export interface VitalsThresholds {
  /** Delta age below this → "streaming". */
  streamingFreshMs: number;
  /** Total silence (no activity, no running tool) beyond this → "slow". */
  activityStaleMs: number;
}

export const DEFAULT_VITALS_THRESHOLDS: VitalsThresholds = {
  streamingFreshMs: 1500,
  // Single-agent turns emit no heartbeat (only team topology does), so a
  // silent reasoning pause has no positive "alive" signal. 10s of total
  // silence with the socket up and no tool running is where "still
  // working" stops being a safe assumption — matches the pre-existing
  // 12s "waitingForModel" intuition while leaving headroom for a tick.
  activityStaleMs: 10_000,
};

// A live connection can keep heartbeating while the provider has not emitted
// any agent event. Past one minute this is no longer an ordinary TTFT window:
// surface it as a delayed first response without calling the socket stalled.
export const FIRST_RESPONSE_DELAY_NOTICE_MS = 60_000;

export function emptyVitalsMarks(): VitalsMarks {
  return {
    activeTurnId: null,
    turnStartedAt: null,
    firstDeltaAt: null,
    lastDeltaAt: null,
    lastActivityAt: null,
    firstAgentActivityAt: null,
    lastHeartbeatAt: null,
    heartbeatElapsedS: null,
    maxDeltaGapMs: 0,
    lateDeltaDrops: 0,
  };
}

export function emptyVitals(): StreamVitals {
  return {
    phase: "idle",
    ttftMs: null,
    lastDeltaAgeMs: Infinity,
    sinceActivityMs: Infinity,
    elapsedMs: 0,
    maxDeltaGapMs: 0,
    stalled: false,
  };
}

/** Compact, stable wall-time label shared by the header and the inline
 * activity lane. Long waits should read as ``1m 44s`` instead of ``104s``;
 * keeping the formatter here prevents the two surfaces drifting apart. */
export function formatStreamElapsed(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;

  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (totalMinutes < 60) {
    return `${totalMinutes}m ${String(seconds).padStart(2, "0")}s`;
  }

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

// Notification methods that count as "the server is doing work". Anything
// under ``item/`` (text/reasoning deltas, tool progress, lifecycle) plus
// the explicit per-turn heartbeat. Kept broad on purpose: a new item
// sub-method should register as activity without a code change here.
function isActivityMethod(method: string): boolean {
  return method.startsWith("item/") || method === "turn/heartbeat";
}

/** Fold one realtime notification into the marks, in place. Call BEFORE
 * the reducer sees it; ``now`` is injected for testability. */
export function applyVitalNotification(
  marks: VitalsMarks,
  note: { method: string; params?: Record<string, unknown> },
  now: number,
): void {
  const { method, params } = note;

  if (method === "turn/started") {
    // A fresh turn resets everything; the started event is itself activity.
    const wireTurn = params?.turn;
    const wireTurnId =
      wireTurn && typeof wireTurn === "object" && "id" in wireTurn
        ? (wireTurn as { id?: unknown }).id
        : params?.turnId;
    marks.activeTurnId = typeof wireTurnId === "string" ? wireTurnId : null;
    marks.turnStartedAt = now;
    marks.firstDeltaAt = null;
    marks.lastDeltaAt = null;
    marks.lastActivityAt = now;
    marks.firstAgentActivityAt = null;
    marks.lastHeartbeatAt = null;
    marks.heartbeatElapsedS = null;
    marks.maxDeltaGapMs = 0;
    marks.lateDeltaDrops = 0;
    return;
  }

  if (method === "item/agentMessage/delta") {
    if (marks.lastDeltaAt != null) {
      marks.maxDeltaGapMs = Math.max(
        marks.maxDeltaGapMs,
        now - marks.lastDeltaAt,
      );
    }
    if (marks.firstDeltaAt == null) marks.firstDeltaAt = now;
    if (marks.firstAgentActivityAt == null) marks.firstAgentActivityAt = now;
    marks.lastDeltaAt = now;
    marks.lastActivityAt = now;
    return;
  }

  if (method === "turn/heartbeat") {
    marks.lastHeartbeatAt = now;
    const elapsed = params?.elapsedS;
    if (typeof elapsed === "number" && Number.isFinite(elapsed)) {
      marks.heartbeatElapsedS = elapsed;
    }
    marks.lastActivityAt = now;
    return;
  }

  if (isActivityMethod(method)) {
    if (
      marks.firstAgentActivityAt == null &&
      isAgentActivityNotification(note)
    ) {
      marks.firstAgentActivityAt = now;
    }
    marks.lastActivityAt = now;
  }
}

function isAgentActivityNotification(note: {
  method: string;
  params?: Record<string, unknown>;
}): boolean {
  if (!note.method.startsWith("item/")) return false;
  const item = note.params?.item;
  const itemType =
    item && typeof item === "object" && "type" in item
      ? (item as { type?: unknown }).type
      : undefined;
  if (itemType === "userMessage" || itemType === "steeringUserMessage") {
    return false;
  }
  // Delta methods encode their item type in the method even when the compact
  // payload omits the full item snapshot.
  return !/item\/(?:userMessage|steeringUserMessage)(?:\/|$)/.test(note.method);
}

/** Refresh liveness when thread/resume confirms that a turn is still active.
 * A different turn resets stale marks; the same turn preserves TTFT and gap
 * observations collected before reconnect. */
export function seedVitalsFromResumedTurn(
  marks: VitalsMarks,
  turn: {
    id?: unknown;
    status?: unknown;
    startedAt?: unknown;
    items?: unknown;
  } | null,
  now: number,
): void {
  if (!turn || typeof turn.id !== "string" || turn.status !== "inProgress") {
    return;
  }

  if (marks.activeTurnId !== turn.id) {
    Object.assign(marks, emptyVitalsMarks());
    marks.activeTurnId = turn.id;
    const parsedStartedAt =
      typeof turn.startedAt === "string" ? Date.parse(turn.startedAt) : NaN;
    marks.turnStartedAt = Number.isFinite(parsedStartedAt)
      ? Math.min(parsedStartedAt, now)
      : now;
    const items = Array.isArray(turn.items) ? turn.items : [];
    if (
      items.some(
        (item) =>
          item &&
          typeof item === "object" &&
          "type" in item &&
          (item as { type?: unknown }).type !== "userMessage" &&
          (item as { type?: unknown }).type !== "steeringUserMessage",
      )
    ) {
      marks.firstAgentActivityAt = now;
    }
  }
  // The resume response is positive evidence from the server and starts a
  // fresh silence window. If nothing follows, classification becomes slow.
  marks.lastActivityAt = now;
}

export interface ClassifyInput {
  marks: VitalsMarks;
  /** Transport is up (WebSocket open). */
  connected: boolean;
  /** The most recent turn is still ``inProgress``. */
  turnActive: boolean;
  /** A tool/subagent item is currently running — protects long silent
   * tool calls (a 60s command) from being flagged "slow". */
  hasRunningWork: boolean;
}

/** Classify accumulated marks into a render-ready snapshot. Pure. */
export function classifyVitals(
  input: ClassifyInput,
  now: number,
  thresholds: VitalsThresholds = DEFAULT_VITALS_THRESHOLDS,
): StreamVitals {
  const { marks, connected, turnActive, hasRunningWork } = input;

  const ttftMs =
    marks.turnStartedAt != null && marks.firstDeltaAt != null
      ? Math.max(0, marks.firstDeltaAt - marks.turnStartedAt)
      : null;
  const lastDeltaAgeMs =
    marks.lastDeltaAt != null ? Math.max(0, now - marks.lastDeltaAt) : Infinity;
  const sinceActivityMs =
    marks.lastActivityAt != null
      ? Math.max(0, now - marks.lastActivityAt)
      : Infinity;
  const elapsedMs =
    marks.turnStartedAt != null ? Math.max(0, now - marks.turnStartedAt) : 0;

  const base = {
    ttftMs,
    lastDeltaAgeMs,
    sinceActivityMs,
    elapsedMs,
    maxDeltaGapMs: marks.maxDeltaGapMs,
  };

  if (!turnActive) return { ...emptyVitals(), ...base, phase: "idle" };
  if (!connected) return { ...base, phase: "disconnected", stalled: true };

  // Text is flowing this instant.
  if (lastDeltaAgeMs < thresholds.streamingFreshMs) {
    return { ...base, phase: "streaming", stalled: false };
  }

  // No local telemetry for this turn yet — e.g. a turn resumed mid-flight,
  // where marks are empty until the first live notification lands. Don't
  // accuse the connection on absent evidence; assume the server is working.
  if (marks.lastActivityAt == null) {
    return { ...base, phase: "working", stalled: false };
  }

  // Before the first real agent item (commentary, reasoning, tool, answer)
  // has arrived, we are still waiting for the model to start producing.
  // This is the TTFT window — the model may legitimately take 10s+ of
  // server-side reasoning (especially for non-thinking models that never
  // emit intermediate tokens). Do NOT flag this as "slow/stuck"; stay in
  // the honest "waiting" state until the first sign of life appears.
  // Exception: if a tool/subagent is already running (hasRunningWork),
  // the server is clearly active even without a text/reasoning item.
  if (marks.firstAgentActivityAt == null && !hasRunningWork) {
    return { ...base, phase: "waiting", stalled: false };
  }

  // Past this point we have seen agent output. Silent too long with
  // nothing running → the honest "maybe stuck" state. A running tool or
  // a fresh activity/heartbeat keeps us out of here.
  if (!hasRunningWork && sinceActivityMs >= thresholds.activityStaleMs) {
    return { ...base, phase: "slow", stalled: true };
  }

  // Active and recently alive: tool running, reasoning streaming, or a
  // between-chunks pause the server is clearly still working through.
  return { ...base, phase: "working", stalled: false };
}
