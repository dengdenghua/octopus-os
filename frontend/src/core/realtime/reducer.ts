// Conversation reducer — applies server-pushed events to Conversation state.
//
// Pure function. No I/O. No React. Each call returns a new Conversation
// (never mutates the input) so React state setters trigger re-render
// only when the slice actually changes. The function is exported as the
// single recombinator the WebSocket client uses; tests target it
// directly without spinning up a server.

import type {
  AgentPhaseSnapshot,
  Conversation,
  FileHunk,
  GroundingSource,
  Item,
  ItemStatus,
  McpToolProgress,
  Turn,
  TurnStatus,
  WorkbenchSnapshotV2,
  WorkspaceFocus,
} from "./items";

let errorItemSeq = 0;

// Tracks when each turn was locally interrupted so late-arriving deltas
// within a grace window are still accepted rather than silently dropped.
const interruptTimestamps = new Map<string, number>();
const INTERRUPT_GRACE_MS = 5_000;

// ── Streaming append buffer ─────────────────────────────────
//
// The naive ``text: it.text + delta`` rebuilds the whole string on
// EVERY frame — quadratic copying for long outputs (each delta is
// small, the accumulated text is not). Instead, deltas for a
// streaming item accumulate in a per-item chunk list (O(1) amortized
// per delta) and are joined ONCE per animation frame when React reads
// the state. Reads of settled items never pay anything.
//
// The buffer is keyed on the item OBJECT: a fresh object (snapshot
// upsert, turn close, resume merge) has no buffered chunks, and the
// wire field itself is always complete — so cleanup is automatic, no
// eviction bookkeeping, and a weak map lets dead entries be GC'd with
// their items.
const streamChunks = new WeakMap<Item, string[]>();
// Joined text per item object, memoized: N components reading the same
// item within one frame share a single join.
const streamJoined = new WeakMap<Item, string>();

// Reasoning deltas carry a ``contentIndex`` so the server can stream
// multiple reasoning blocks (e.g. interleaved chain-of-thought +
// encrypted content) into the same item. Deltas are bucketed per
// contentIndex; the final text is the concatenation of buckets in
// ascending index order — mirroring the OpenAI Responses API
// ``reasoning.content[].index`` ordering.
const streamReasoningBuckets = new WeakMap<Item, Map<number, string[]>>();
const streamReasoningJoined = new WeakMap<Item, string>();

// Text-bearing wire fields that stream via deltas. Used by snapshot
// paths (item/completed, turn close) to materialize buffered chunks
// INTO the wire field so the item object is self-contained for
// persistence and for readers that bypass ``itemText``.
type StreamTextField = "text" | "content" | "aggregatedOutput";

const STREAM_TEXT_FIELDS: Partial<Record<Item["type"], StreamTextField>> = {
  agentMessage: "text",
  reasoning: "content",
  plan: "text",
  commandExecution: "aggregatedOutput",
};

function appendStreamText<T extends Item>(item: T, delta: string): T {
  let chunks = streamChunks.get(item);
  if (!chunks) {
    chunks = [delta];
  } else {
    chunks.push(delta);
  }
  const updated = { ...item } as T;
  // Move the buffer to the replacement item. The reducer never mutates
  // Conversation state in place, so the fresh object becomes the sole owner
  // of the in-flight chunks while the old object can be collected.
  // CRITICAL: set new mapping BEFORE deleting old one — drift probe (line 573)
  // folds twice from same base, and delete-then-set loses chunks on second pass.
  streamChunks.set(updated, chunks);
  streamChunks.delete(item);
  streamJoined.delete(item);
  return updated;
}

// Join reasoning buckets in ascending contentIndex order. Each bucket's
// chunks are concatenated first, then buckets are concatenated together.
function joinReasoningBuckets(buckets: Map<number, string[]>): string {
  const indices = Array.from(buckets.keys()).sort((a, b) => a - b);
  let result = "";
  for (const idx of indices) {
    const chunks = buckets.get(idx);
    if (chunks) result += chunks.join("");
  }
  return result;
}

// Same buffer-moving semantics as ``appendStreamText``, but buckets the
// delta by ``contentIndex`` so interleaved multi-block reasoning streams
// reconstruct in the correct order.
function appendReasoningStreamText<T extends Item>(
  item: T,
  delta: string,
  contentIndex: number,
): T {
  let buckets = streamReasoningBuckets.get(item);
  if (!buckets) {
    buckets = new Map<number, string[]>();
    buckets.set(contentIndex, [delta]);
  } else {
    let chunks = buckets.get(contentIndex);
    if (!chunks) {
      chunks = [delta];
      buckets.set(contentIndex, chunks);
    } else {
      chunks.push(delta);
    }
  }
  const updated = { ...item } as T;
  // Same ordering fix as appendStreamText: set new mapping before deleting old.
  streamReasoningBuckets.set(updated, buckets);
  streamReasoningBuckets.delete(item);
  streamReasoningJoined.delete(item);
  return updated;
}

// Resolve the current streamed text for an item. Settled items (and
// anything snapshot-loaded) read their wire field directly — zero
// overhead outside the streaming hot path. Exported for renderers
// that want the freshest in-flight text.
export function itemStreamText(item: Item): string {
  // Reasoning uses contentIndex buckets — the joined text is the
  // concatenation of all buckets (in index order) on top of the
  // wire field.
  if (item.type === "reasoning") {
    const buckets = streamReasoningBuckets.get(item);
    if (!buckets || buckets.size === 0) return streamWireText(item);
    const cached = streamReasoningJoined.get(item);
    if (cached !== undefined) return cached;
    const joined = streamWireText(item) + joinReasoningBuckets(buckets);
    streamReasoningJoined.set(item, joined);
    return joined;
  }
  const chunks = streamChunks.get(item);
  if (!chunks || chunks.length === 0) return streamWireText(item);
  const cached = streamJoined.get(item);
  if (cached !== undefined) return cached;
  const joined = streamWireText(item) + chunks.join("");
  streamJoined.set(item, joined);
  return joined;
}

function streamWireText(item: Item): string {
  switch (item.type) {
    case "agentMessage":
    case "plan":
      return item.text;
    case "reasoning":
      return item.content;
    case "commandExecution":
      return item.aggregatedOutput;
    default:
      return "";
  }
}

// Materialize buffered chunks into the item's own wire field. Called
// whenever an item leaves the streaming hot path (turn close) so the
// settled object is self-contained for persistence and for readers
// that bypass ``itemStreamText``.
function withMaterializedStreamText(item: Item): Item {
  // Reasoning materializes from contentIndex buckets, not the flat
  // chunk list used by other streaming text types.
  if (item.type === "reasoning") {
    const buckets = streamReasoningBuckets.get(item);
    if (!buckets || buckets.size === 0) return item;
    const materialized = {
      ...item,
      content: streamWireText(item) + joinReasoningBuckets(buckets),
    } as Item;
    streamReasoningBuckets.delete(item);
    streamReasoningJoined.delete(item);
    return materialized;
  }
  const chunks = streamChunks.get(item);
  if (!chunks || chunks.length === 0) return item;
  const field = STREAM_TEXT_FIELDS[item.type];
  if (!field) return item;
  return {
    ...item,
    [field]: streamWireText(item) + chunks.join(""),
  } as Item;
}

/**
 * Materialize every in-flight stream buffer across the whole conversation.
 *
 * Replay (``replay.ts``) folds deltas through the same chunk buffer the
 * live path uses; before the rebuilt Conversation is handed to persistence
 * or non-streaming readers, the trailing buffered chunks (typically an
 * unfinished final turn) must land in the items' wire fields. Identity is
 * preserved for untouched items/turns so downstream memoization survives.
 */
export function materializeStreamedItems(
  conversation: Conversation,
): Conversation {
  let changed = false;
  const turns = conversation.turns.map((turn) => {
    let turnChanged = false;
    const items = turn.items.map((item) => {
      const materialized = withMaterializedStreamText(item);
      if (materialized !== item) turnChanged = true;
      return materialized;
    });
    if (!turnChanged) return turn;
    changed = true;
    return { ...turn, items };
  });
  return changed ? { ...conversation, turns } : conversation;
}

// All events the reducer understands. The set is closed: anything not
// listed here is a no-op (useful — server adds new methods without
// breaking older clients), but the type unions a developer should
// review when bumping the protocol.

export type ConversationEvent =
  | { method: "thread/started"; params: { thread: { id: string } } }
  | {
      method: "thread/status/changed";
      params: {
        threadId: string;
        status: { type: string; activeFlags?: string[] };
      };
    }
  | {
      method: "thread/tokenUsage/updated";
      params: { threadId: string; tokenUsage: Record<string, unknown> };
    }
  | { method: "turn/started"; params: { threadId: string; turn: Turn } }
  | {
      method: "turn/completed";
      params: { threadId: string; turn: Turn };
    }
  | {
      // Replay-only terminal patch. ``turn/completed`` carries a whole Turn
      // snapshot (live path), but the persisted event log records only
      // ``status/completedAt/error`` — mirroring the Python replay semantics
      // in ``runtime/memory/threads/event_log.py::_apply_event``. Synthesizing
      // a full Turn would clobber ``startedAt`` and other live fields via the
      // ``{...existing, ...incoming}`` merge, so replay emits this instead.
      method: "turn/finalized";
      params: {
        threadId: string;
        turnId: string;
        status: TurnStatus;
        completedAt: string | null;
        error?: Record<string, unknown> | null;
      };
    }
  | {
      // Compaction replaces a contiguous range of prior turns with a single
      // summary turn. Semantics mirror the Python replay exactly: the summary
      // slots into the position of the oldest superseded turn.
      method: "turn/compacted";
      params: {
        threadId: string;
        supersededTurnIds: string[];
        summaryTurn: Turn;
      };
    }
  | {
      method: "turn/interrupted";
      params: {
        threadId: string;
        turnId: string;
        completedAt?: string;
        reason?: string;
      };
    }
  | {
      method: "turn/diff/updated";
      params: { threadId: string; turnId: string; diff: unknown };
    }
  | {
      method: "turn/plan/updated";
      params: {
        threadId: string;
        turnId: string;
        // Optional: live senders always include phases, but replayed
        // ``turn_updated`` log events may carry only workspaceFocus /
        // workbenchSnapshot. Absent phases leave ``turn.phases`` untouched.
        phases?: AgentPhaseSnapshot[];
        workspaceFocus?: WorkspaceFocus | null;
        workbenchSnapshot?: WorkbenchSnapshotV2 | null;
      };
    }
  | {
      method: "workbench/snapshot";
      params: {
        threadId: string;
        turnId: string;
        snapshot: WorkbenchSnapshotV2;
      };
    }
  | {
      // Lightweight keepalive for long-running swarm/cluster roles.
      // The reducer doesn't need to track state from this event — its
      // purpose is to prevent the frontend's pong-timeout (70s) from
      // killing the WS during roles that don't produce text deltas.
      method: "turn/heartbeat";
      params: {
        threadId: string;
        turnId: string;
        role?: string;
        agentId?: string;
        elapsedS?: number;
      };
    }
  | {
      // Soft hand-off hint emitted at turn start when the user's
      // prompt strongly matches a 能力包 / Meta-Skill. Reducer
      // attaches the hint to the matching turn so the chat page
      // can render a dismissible chip linking to the catalog. The
      // hint is informational — ReAct still runs in parallel.
      method: "turn/metaSkill/hint";
      params: {
        threadId: string;
        turnId: string;
        name: string;
        description: string;
        kind: string;
        affinity: string[];
        stepCount: number;
      };
    }
  | {
      // Codebase grounding: the project docs/chunks a code/project turn
      // folded into its prompt. Reducer attaches them to the matching turn;
      // the realtime adapter bridges them onto the AI reply's
      // ``additional_kwargs.grounding`` so the chat shows a grounding chip.
      method: "turn/grounding";
      params: {
        threadId: string;
        turnId: string;
        sources: GroundingSource[];
      };
    }
  | {
      method: "item/started";
      params: { threadId: string; turnId: string; item: Item };
    }
  | {
      method: "item/completed";
      params: { threadId: string; turnId: string; item: Item };
    }
  | {
      method: "item/agentMessage/delta";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        delta: string;
      };
    }
  | {
      method: "item/reasoning/textDelta";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        delta: string;
        contentIndex: number;
      };
    }
  | {
      method: "item/plan/delta";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        delta: string;
      };
    }
  | {
      method: "item/commandExecution/outputDelta";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        delta: string;
      };
    }
  | {
      method: "item/fileChange/hunkDelta";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        path: string;
        op: "create" | "update" | "delete";
        hunk: FileHunk;
        workspaceFocus?: WorkspaceFocus | null;
      };
    }
  | {
      method: "item/mcpToolCall/progress";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        progress: McpToolProgress;
        workspaceFocus?: WorkspaceFocus | null;
      };
    }
  | {
      method: "item/fileChange/hunkDecision";
      params: {
        threadId: string;
        turnId: string;
        itemId: string;
        hunkId: string;
        decision: "accepted" | "rejected";
        path: string;
      };
    }
  | {
      // Workflow (multi-agent orchestration) completion notification.
      // Informational — the reducer records it on the conversation so the
      // UI can surface a completion banner/chip; nothing else depends on it.
      method: "workflow/completed";
      params: {
        threadId: string;
        workflowName: string;
        workflowDescription: string;
        runId: string;
        stopReason: string;
        success: boolean;
        agentsStarted: number;
        error?: string | null;
      };
    }
  | {
      method: "error";
      params: {
        threadId: string;
        turnId?: string;
        error: {
          message: string;
          code?: unknown;
          additionalDetails?: unknown;
          codexErrorInfo?: unknown;
          info?: unknown;
          [key: string]: unknown;
        };
        willRetry: boolean;
      };
    };

export interface PendingApprovalRequest {
  requestId: string | number;
  method: string;
  params: Record<string, unknown>;
}

// Event flag set returned alongside a reduced state so callers know what
// just happened without diffing the entire conversation. Avoids
// re-running expensive selectors when only a token landed.
export interface ReducerOutput {
  next: Conversation;
  changedTurnIds: string[];
  changedItemIds: string[];
}

// Behaviour switches for the two event sources that feed the reducer.
// ``live`` (default) keeps every realtime safeguard — interrupt grace
// windows, late-delta drops. ``replay`` trusts the persisted event log as
// authoritative: deltas apply regardless of item status (mirroring the
// Python ``_merge_delta``), and no wall-clock heuristics are consulted, so
// replaying the same log twice yields identical state.
export interface ReduceOptions {
  mode?: "live" | "replay";
}

function errorRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function parseEmbeddedError(message: string): Record<string, unknown> | null {
  const trimmed = message.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    return errorRecord(JSON.parse(trimmed));
  } catch {
    return null;
  }
}

function conciseErrorMessage(message: string): string {
  return message
    .replace(/(?:,?\s+url:)\s+https?:\/\/\S+/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function normalizeRealtimeError(error: {
  message: string;
  [key: string]: unknown;
}): { message: string; info: Record<string, unknown> | null } {
  const embedded = parseEmbeddedError(error.message);
  const embeddedMessage =
    typeof embedded?.message === "string" ? embedded.message : null;
  const message =
    conciseErrorMessage(embeddedMessage ?? error.message) || "turn failed";
  const info: Record<string, unknown> = {};
  const structuredInfo = errorRecord(error.info) ?? errorRecord(embedded?.info);
  if (structuredInfo) Object.assign(info, structuredInfo);
  for (const key of [
    "code",
    "codexErrorInfo",
    "additionalDetails",
    "status",
  ] as const) {
    const value = error[key] ?? embedded?.[key];
    if (value !== undefined && value !== null && value !== "")
      info[key] = value;
  }
  if (message !== error.message.trim()) {
    info.rawMessage = error.message.slice(0, 8_000);
  }
  return {
    message,
    info: Object.keys(info).length > 0 ? info : null,
  };
}

export function reduce(
  state: Conversation,
  evt: ConversationEvent,
  onDiagnostic?: ReducerDiagnosticHandler,
  options?: ReduceOptions,
): ReducerOutput {
  const replayMode = options?.mode === "replay";
  switch (evt.method) {
    case "thread/started":
      return {
        next: { ...state, resumeState: "resumed" },
        changedTurnIds: [],
        changedItemIds: [],
      };
    case "thread/tokenUsage/updated":
      return {
        next: { ...state, tokenUsage: evt.params.tokenUsage },
        changedTurnIds: [],
        changedItemIds: [],
      };
    case "thread/status/changed":
      return { next: state, changedTurnIds: [], changedItemIds: [] };
    case "turn/started": {
      const incoming = {
        ...evt.params.turn,
        items: orderTimelineItems(evt.params.turn.items),
      };
      const idx = state.turns.findIndex((t) => t.id === incoming.id);
      if (idx !== -1) {
        const existing = state.turns[idx]!;
        const merged = mergeStartedTurn(existing, incoming);
        if (merged === existing) return unchanged(state);
        return {
          next: {
            ...state,
            turns: replaceAt(state.turns, idx, merged),
          },
          changedTurnIds: [incoming.id],
          changedItemIds: [],
        };
      }
      return {
        next: { ...state, turns: [...state.turns, incoming] },
        changedTurnIds: [incoming.id],
        changedItemIds: [],
      };
    }
    case "turn/completed": {
      const incoming = evt.params.turn;
      const existingIndex = state.turns.findIndex(
        (turn) => turn.id === incoming.id,
      );
      if (existingIndex === -1) {
        const completed = normalizeTerminalTurn(incoming);
        return {
          next: { ...state, turns: [...state.turns, completed] },
          changedTurnIds: [incoming.id],
          changedItemIds: completed.items.map((item) => item.id),
        };
      }
      const changedItemIds: string[] = [];
      const turns = state.turns.map((t) => {
        if (t.id !== incoming.id) return t;
        const merged = mergeCompletedTurn(t, incoming);
        for (const item of merged.items) {
          const previous = t.items.find((it) => it.id === item.id);
          if (previous && previous.status !== item.status) {
            changedItemIds.push(item.id);
          }
        }
        return merged;
      });
      return {
        next: { ...state, turns },
        changedTurnIds: [incoming.id],
        changedItemIds,
      };
    }
    case "turn/finalized": {
      // Persisted-log terminal patch (see the event-type comment). The turn
      // must already exist — replay started it via ``turn_started``. Python
      // replay only patches status/completedAt/error; the client additionally
      // closes still-open items and materializes their buffered stream text,
      // the same repair ``closeItemsForTurn`` performs on live completion.
      const { turnId, status, completedAt, error } = evt.params;
      const turnIdx = state.turns.findIndex((t) => t.id === turnId);
      const turn = state.turns[turnIdx];
      if (!turn) return unchanged(state);
      const items = closeItemsForTurn(turn.items, status);
      const changedItemIds: string[] = [];
      for (let index = 0; index < items.length; index += 1) {
        if (items[index] !== turn.items[index]) {
          changedItemIds.push(items[index]!.id);
        }
      }
      const nextTurn: Turn = {
        ...turn,
        status,
        completedAt: completedAt ?? turn.completedAt,
        ...(error !== undefined ? { error } : {}),
        items,
      };
      return {
        next: { ...state, turns: replaceAt(state.turns, turnIdx, nextTurn) },
        changedTurnIds: [turnId],
        changedItemIds,
      };
    }
    case "turn/compacted": {
      // Mirrors ``_apply_event(turn_compacted)`` in event_log.py: drop the
      // superseded turns and insert the summary where the oldest superseded
      // turn sat (append when no superseded turn is found). The summary is
      // additionally de-duplicated by id so a repeated compaction event
      // replaces rather than duplicates — replay is idempotent by log
      // construction, live delivery is at-least-once.
      const { supersededTurnIds, summaryTurn } = evt.params;
      const superseded = new Set(supersededTurnIds);
      const firstIdx = state.turns.findIndex((t) => superseded.has(t.id));
      const removed = (t: Turn) =>
        superseded.has(t.id) || t.id === summaryTurn.id;
      const keep = state.turns.filter((t) => !removed(t));
      let insertAt: number;
      if (firstIdx === -1) {
        insertAt = keep.length;
      } else {
        // Entries removed BEFORE firstIdx shift the insertion point left.
        const removedBefore = state.turns
          .slice(0, firstIdx)
          .filter((t) => removed(t)).length;
        insertAt = Math.min(firstIdx - removedBefore, keep.length);
      }
      const turns = [
        ...keep.slice(0, insertAt),
        summaryTurn,
        ...keep.slice(insertAt),
      ];
      return {
        next: { ...state, turns },
        changedTurnIds: [...supersededTurnIds, summaryTurn.id],
        changedItemIds: [],
      };
    }
    case "turn/interrupted": {
      interruptTimestamps.set(evt.params.turnId, Date.now());
      const changedItemIds: string[] = [];
      const completedAt = evt.params.completedAt ?? new Date().toISOString();
      const interruptReason = evt.params.reason ?? null;
      const turns = state.turns.map((t) => {
        if (t.id !== evt.params.turnId) return t;
        const items = closeItemsForTurn(t.items, "interrupted");
        for (let index = 0; index < items.length; index += 1) {
          const item = items[index];
          if (item && item !== t.items[index]) {
            changedItemIds.push(item.id);
          }
        }
        return {
          ...t,
          status: "interrupted" as const,
          completedAt,
          items,
          interruptReason: interruptReason ?? t.interruptReason,
        };
      });
      return {
        next: { ...state, turns },
        changedTurnIds: [evt.params.turnId],
        changedItemIds,
      };
    }
    case "turn/diff/updated":
      return {
        next: state,
        changedTurnIds: [evt.params.turnId],
        changedItemIds: [],
      };
    case "turn/plan/updated":
      return applyPlanUpdate(
        state,
        evt.params.turnId,
        evt.params.phases,
        "phases" in evt.params,
        evt.params.workspaceFocus,
        "workspaceFocus" in evt.params,
        evt.params.workbenchSnapshot,
        "workbenchSnapshot" in evt.params,
      );
    case "workbench/snapshot":
      return applyWorkbenchSnapshot(
        state,
        evt.params.turnId,
        evt.params.snapshot,
      );
    case "turn/heartbeat":
      // No state change — purely a keepalive signal to prevent WS timeout.
      return { next: state, changedTurnIds: [], changedItemIds: [] };
    case "turn/metaSkill/hint": {
      // Attach the hint to the matching turn. If the turn isn't in
      // state yet (race against turn/started) we silently drop —
      // the hint is best-effort UX, not a contract.
      const { turnId, threadId, name, description, kind, affinity, stepCount } =
        evt.params;
      let touched = false;
      const turns = state.turns.map((t) => {
        if (t.id !== turnId) return t;
        touched = true;
        return {
          ...t,
          metaSkillHint: { name, description, kind, affinity, stepCount },
        };
      });
      if (!touched) {
        return { next: state, changedTurnIds: [], changedItemIds: [] };
      }
      void threadId;
      return {
        next: { ...state, turns },
        changedTurnIds: [turnId],
        changedItemIds: [],
      };
    }
    case "turn/grounding": {
      // Attach the consulted project docs/chunks to the matching turn.
      // Same race tolerance as the meta-skill hint: drop if the turn isn't
      // in state yet — grounding is best-effort UX, not a contract.
      const { turnId, threadId, sources } = evt.params;
      if (!Array.isArray(sources) || sources.length === 0) {
        return { next: state, changedTurnIds: [], changedItemIds: [] };
      }
      let touched = false;
      const turns = state.turns.map((t) => {
        if (t.id !== turnId) return t;
        touched = true;
        return { ...t, grounding: sources };
      });
      if (!touched) {
        return { next: state, changedTurnIds: [], changedItemIds: [] };
      }
      void threadId;
      return {
        next: { ...state, turns },
        changedTurnIds: [turnId],
        changedItemIds: [],
      };
    }
    case "item/started":
      return upsertItem(state, evt.params.turnId, evt.params.item, "started");
    case "item/completed":
      return upsertItem(state, evt.params.turnId, evt.params.item, "completed");
    case "item/agentMessage/delta":
      return mergeDelta(
        state,
        evt.params.turnId,
        evt.params.itemId,
        "agentMessage",
        evt.params.delta,
        onDiagnostic,
        replayMode,
      );
    case "item/reasoning/textDelta":
      return mergeDelta(
        state,
        evt.params.turnId,
        evt.params.itemId,
        "reasoning",
        evt.params.delta,
        onDiagnostic,
        replayMode,
        evt.params.contentIndex,
      );
    case "item/plan/delta":
      return mergeDelta(
        state,
        evt.params.turnId,
        evt.params.itemId,
        "plan",
        evt.params.delta,
        onDiagnostic,
        replayMode,
      );
    case "item/commandExecution/outputDelta":
      return mergeDelta(
        state,
        evt.params.turnId,
        evt.params.itemId,
        "commandOutput",
        evt.params.delta,
        onDiagnostic,
        replayMode,
      );
    case "item/fileChange/hunkDelta":
      return applyFileChangeHunkDelta(
        state,
        evt.params.turnId,
        evt.params.itemId,
        evt.params.path,
        evt.params.op,
        evt.params.hunk,
        evt.params.workspaceFocus,
        "workspaceFocus" in evt.params,
      );
    case "item/mcpToolCall/progress":
      return applyMcpToolProgress(
        state,
        evt.params.turnId,
        evt.params.itemId,
        evt.params.progress,
        evt.params.workspaceFocus,
        "workspaceFocus" in evt.params,
      );
    case "item/fileChange/hunkDecision":
      return applyHunkDecision(
        state,
        evt.params.turnId,
        evt.params.itemId,
        evt.params.hunkId,
        evt.params.decision,
      );
    case "error": {
      // Surface as a transient errorItem on the active turn (if any).
      // We do *not* mutate turn.status here — turn/completed authoritatively
      // sets that. Same convention as the server.
      const turnId =
        evt.params.turnId ?? state.turns[state.turns.length - 1]?.id;
      if (!turnId)
        return { next: state, changedTurnIds: [], changedItemIds: [] };
      const normalizedError = normalizeRealtimeError(evt.params.error);
      const errorItem: Item = {
        id: `err_${Date.now()}_${++errorItemSeq}`,
        type: "error",
        status: "failed",
        createdAt: new Date().toISOString(),
        message: normalizedError.message,
        willRetry: evt.params.willRetry,
        errorInfo: normalizedError.info,
      };
      return upsertItem(state, turnId, errorItem, "started");
    }
    case "workflow/completed": {
      const n = evt.params;
      const notification = {
        threadId: n.threadId,
        workflowName: n.workflowName,
        workflowDescription: n.workflowDescription,
        runId: n.runId,
        stopReason: n.stopReason,
        success: n.success,
        agentsStarted: n.agentsStarted,
        error: n.error ?? null,
        receivedAt: new Date().toISOString(),
      };
      const workflowNotifications = [
        ...state.workflowNotifications,
        notification,
      ].slice(-20);
      return {
        next: { ...state, workflowNotifications },
        changedTurnIds: [],
        changedItemIds: [],
      };
    }
    default:
      return { next: state, changedTurnIds: [], changedItemIds: [] };
  }
}

// ── Helpers ──────────────────────────────────────────────────

// Shared no-op result: same state reference, nothing flagged as changed.
// Returning the *same* reference matters — callers (and React setters)
// use identity to skip re-renders, so dropped deltas must not allocate.
function unchanged(state: Conversation): ReducerOutput {
  return { next: state, changedTurnIds: [], changedItemIds: [] };
}

// Copy ``arr`` with index ``idx`` replaced. Single allocation, no
// per-element predicate — the indexed counterpart of ``.map()`` for
// the hot single-item update paths below.
function replaceAt<T>(arr: readonly T[], idx: number, value: T): T[] {
  const next = arr.slice();
  next[idx] = value;
  return next;
}

// Rebuild Conversation with one item of one turn swapped out. Every
// streaming delta lands here, so the work is two indexed copies —
// no full-array scans beyond the initial findIndex at the call site.
function replaceTurnItem(
  state: Conversation,
  turn: Turn,
  turnIdx: number,
  itemIdx: number,
  item: Item,
  turnPatch?: Partial<Turn>,
): Conversation {
  const nextTurn: Turn = {
    ...turn,
    ...turnPatch,
    items: replaceAt(turn.items, itemIdx, item),
  };
  return { ...state, turns: replaceAt(state.turns, turnIdx, nextTurn) };
}

function mergeCompletedTurn(existing: Turn, incoming: Turn): Turn {
  const incomingItems = Array.isArray(incoming.items) ? incoming.items : [];
  const incomingById = new Map(incomingItems.map((item) => [item.id, item]));
  const existingItems = existing.items.map((item) => {
    const replacement = incomingById.get(item.id);
    if (replacement) {
      return preserveCompletedStreamText(item, replacement);
    }
    // Still-open items (and buffered chunks against them) are closed by
    // ``closeItemsForTurn`` below.
    return item;
  });
  const existingIds = new Set(existing.items.map((item) => item.id));
  const appended = incomingItems.filter((item) => !existingIds.has(item.id));
  const items = closeItemsForTurn(
    orderTimelineItems([...existingItems, ...appended]),
    incoming.status,
  );
  return {
    ...existing,
    ...incoming,
    items,
  };
}

/**
 * A duplicate/replayed turn start is never more authoritative than state we
 * already reduced from item deltas or a terminal turn snapshot. Preserve the
 * live copy, enrich missing timeline coordinates, and append genuinely new
 * items without allowing a stale start to erase text or reopen the turn.
 */
function mergeStartedTurn(existing: Turn, incoming: Turn): Turn {
  if (existing.status !== "inProgress") return existing;

  const incomingById = new Map(incoming.items.map((item) => [item.id, item]));
  const existingIds = new Set(existing.items.map((item) => item.id));
  const existingItems = existing.items.map((item) => {
    const replayed = incomingById.get(item.id);
    if (!replayed) return item;
    const timelineSequence =
      item.timelineSequence ?? replayed.timelineSequence ?? null;
    const parentItemId = item.parentItemId ?? replayed.parentItemId ?? null;
    const phaseId = item.phaseId ?? replayed.phaseId ?? null;
    if (
      timelineSequence === item.timelineSequence &&
      parentItemId === item.parentItemId &&
      phaseId === item.phaseId
    ) {
      return item;
    }
    return {
      ...withMaterializedStreamText(item),
      timelineSequence,
      parentItemId,
      phaseId,
    } as Item;
  });
  const appended = incoming.items.filter((item) => !existingIds.has(item.id));
  if (
    appended.length === 0 &&
    existingItems.every((item, index) => item === existing.items[index])
  ) {
    return existing;
  }
  return {
    ...incoming,
    ...existing,
    items: orderTimelineItems([...existingItems, ...appended]),
  };
}

function normalizeTerminalTurn(turn: Turn): Turn {
  return {
    ...turn,
    items: closeItemsForTurn(orderTimelineItems(turn.items), turn.status),
  };
}

/**
 * Reconcile item transport completion with the authoritative turn outcome.
 *
 * Older backends flushed an open public message as ``completed`` immediately
 * before publishing ``turn/interrupted``. The bytes arrived, but the final
 * message never became authoritative. Preserve earlier tools/checkpoints
 * while marking the last prose draft and every still-open item with the real
 * turn outcome. This also repairs persisted historical turns during replay.
 */
function closeItemsForTurn(
  items: readonly Item[],
  turnStatus: Turn["status"],
): Item[] {
  let interruptedMessageId: string | null = null;
  if (
    turnStatus === "interrupted" ||
    turnStatus === "paused" ||
    turnStatus === "cancelled"
  ) {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item?.type === "agentMessage") {
        interruptedMessageId = item.id;
        break;
      }
    }
  }

  const terminalStatus = itemTerminalStatus(turnStatus);
  return items.map((item) => {
    // Only streaming-capable items can carry buffered chunks; checking
    // the type first keeps this loop free of WeakMap lookups for the
    // common non-text items.
    if (STREAM_TEXT_FIELDS[item.type]) {
      item = withMaterializedStreamText(item);
    }
    const nextStatus =
      item.id === interruptedMessageId
        ? "interrupted"
        : item.status === "inProgress"
          ? terminalStatus
          : item.status;
    return nextStatus === item.status
      ? item
      : ({ ...item, status: nextStatus } as Item);
  });
}

function itemTerminalStatus(turnStatus: Turn["status"]): Item["status"] {
  if (turnStatus === "failed") return "failed";
  if (
    turnStatus === "interrupted" ||
    turnStatus === "paused" ||
    turnStatus === "cancelled"
  ) {
    return "interrupted";
  }
  return "completed";
}

function upsertItem(
  state: Conversation,
  turnId: string,
  item: Item,
  phase: "started" | "completed",
): ReducerOutput {
  const turnIdx = state.turns.findIndex((t) => t.id === turnId);
  const turn = state.turns[turnIdx];
  if (!turn) {
    return unchanged(state);
  }
  const idx = turn.items.findIndex((it) => it.id === item.id);
  let nextItems: Item[];
  if (idx === -1) {
    // Avoid a full re-sort on every appended item. Items normally arrive in
    // timeline order, so appending at the tail keeps the array sorted and the
    // rendered cards stable (no position jump when a parallel command lands
    // out of delivery order). Only re-sort when the new item's sequence
    // actually precedes the current tail — a rare out-of-order arrival.
    const next = [...turn.items, item];
    const last = turn.items[turn.items.length - 1];
    if (
      Number.isFinite(item.timelineSequence) &&
      last &&
      Number.isFinite(last.timelineSequence) &&
      Number(item.timelineSequence) < Number(last.timelineSequence)
    ) {
      nextItems = orderTimelineItems(next);
    } else {
      nextItems = next;
    }
  } else {
    // ``completed`` snapshots replace; ``started`` after ``completed`` is
    // a no-op (out-of-order delivery from a buffered queue).
    const existing = turn.items[idx];
    if (!existing) return unchanged(state);
    if (phase === "completed" || existing.status === "inProgress") {
      nextItems = orderTimelineItems(
        replaceAt(turn.items, idx, preserveCompletedStreamText(existing, item)),
      );
    } else {
      return unchanged(state);
    }
  }
  const nextTurn: Turn = { ...turn, items: nextItems };
  return {
    next: { ...state, turns: replaceAt(state.turns, turnIdx, nextTurn) },
    changedTurnIds: [turnId],
    changedItemIds: [item.id],
  };
}

function preserveTimelineCoordinates(existing: Item, incoming: Item): Item {
  return {
    ...incoming,
    timelineSequence:
      incoming.timelineSequence ?? existing.timelineSequence ?? null,
    parentItemId: incoming.parentItemId ?? existing.parentItemId ?? null,
    phaseId: incoming.phaseId ?? existing.phaseId ?? null,
  } as Item;
}

function preserveCompletedStreamText(existing: Item, incoming: Item): Item {
  const merged = preserveTimelineCoordinates(existing, incoming);
  const field = STREAM_TEXT_FIELDS[merged.type];
  if (!field) return merged;
  // Server snapshots can lag the live delta stream. The buffer is keyed
  // on the REPLACED object identity, so materialize its chunks into the
  // snapshot's wire field — otherwise those chunks would be stranded
  // and the text would silently shrink on settle.
  const existingText = streamWireText(withMaterializedStreamText(existing));
  if (!existingText) return merged;
  const snapshotText = streamWireText(merged);
  // Three prefix relationships — handle each correctly:
  //   snapshot ⊇ existing → snapshot is authoritative, use it
  //   existing ⊇ snapshot → snapshot lagged, keep existing (longer) text
  //   otherwise           → unrelated, keep existing text + snapshot tail
  if (snapshotText.startsWith(existingText)) return merged;
  if (existingText.startsWith(snapshotText)) {
    return { ...merged, [field]: existingText } as Item;
  }
  return { ...merged, [field]: existingText + snapshotText } as Item;
}

/**
 * Reorder only the slots that carry server-authored timeline coordinates.
 * Legacy/user items without coordinates keep their exact positions, so a
 * mixed old/new replay never moves the user's prompt behind assistant work.
 */
function orderTimelineItems(items: readonly Item[]): Item[] {
  const sequenced = items
    .filter((item) => Number.isFinite(item.timelineSequence))
    .map((item, stableIndex) => ({ item, stableIndex }))
    .sort((left, right) => {
      const delta =
        Number(left.item.timelineSequence) -
        Number(right.item.timelineSequence);
      return delta || left.stableIndex - right.stableIndex;
    })
    .map(({ item }) => item);
  if (sequenced.length < 2) return items.slice();
  let cursor = 0;
  return items.map((item) =>
    Number.isFinite(item.timelineSequence) ? sequenced[cursor++]! : item,
  );
}

type DeltaKind = "agentMessage" | "reasoning" | "plan" | "commandOutput";

// Anomalies the reducer wants to surface without owning a logger.
// ``lateDeltaDropped``: a delta arrived for an item that already left
// ``inProgress``. Since the client batches ``item/completed`` together
// with deltas (preserving arrival order), this should be ~zero — a
// sustained nonzero count means the wire protocol drifted (e.g.
// ``item/completed`` stopped carrying full text) and text is being
// silently lost.
export interface ReducerDiagnostic {
  type: "lateDeltaDropped";
  turnId: string;
  itemId: string;
  kind: DeltaKind;
  itemStatus: ItemStatus;
  deltaLength: number;
}

export type ReducerDiagnosticHandler = (diagnostic: ReducerDiagnostic) => void;

function mergeDelta(
  state: Conversation,
  turnId: string,
  itemId: string,
  kind: DeltaKind,
  delta: string,
  onDiagnostic?: ReducerDiagnosticHandler,
  replayMode?: boolean,
  contentIndex = 0,
): ReducerOutput {
  const turnIdx = state.turns.findIndex((t) => t.id === turnId);
  const turn = state.turns[turnIdx];
  if (!turn) {
    return unchanged(state);
  }
  const itemIdx = turn.items.findIndex((x) => x.id === itemId);
  const it = turn.items[itemIdx];
  if (!it) {
    return unchanged(state);
  }
  // Drop deltas that arrive after the item is already completed.
  // Without this, deltas that slip past the client's ordered batch
  // would append to the already-final ``text`` — doubling content.
  // Status check is the simplest gate; the diagnostic keeps the drop
  // observable instead of silent.
  // Exception: deltas arriving within the interrupt grace window are
  // still accepted so the last few chunks before interruption are not lost.
  // Only applies when the turn itself was interrupted — a completed item
  // in a non-interrupted turn should still reject late deltas.
  // Replay mode bypasses the gate entirely: the persisted log is
  // authoritative and the Python replay applies deltas unconditionally,
  // so gating here would make the two replays diverge.
  if (it.status !== "inProgress" && !replayMode) {
    const interruptedAt = interruptTimestamps.get(turnId);
    const withinGrace =
      interruptedAt !== undefined &&
      Date.now() - interruptedAt < INTERRUPT_GRACE_MS;
    if (!withinGrace || turn.status !== "interrupted") {
      onDiagnostic?.({
        type: "lateDeltaDropped",
        turnId,
        itemId,
        kind,
        itemStatus: it.status,
        deltaLength: delta.length,
      });
      return unchanged(state);
    }
  }
  let updated: Item | null = null;
  if (kind === "agentMessage" && it.type === "agentMessage") {
    updated = appendStreamText(it, delta);
  } else if (kind === "reasoning" && it.type === "reasoning") {
    updated = appendReasoningStreamText(it, delta, contentIndex);
  } else if (kind === "plan" && it.type === "plan") {
    updated = appendStreamText(it, delta);
  } else if (kind === "commandOutput" && it.type === "commandExecution") {
    updated = appendStreamText(it, delta);
  }
  if (updated === null) {
    return unchanged(state);
  }
  return {
    next: replaceTurnItem(state, turn, turnIdx, itemIdx, updated),
    changedTurnIds: [turnId],
    changedItemIds: [itemId],
  };
}

function applyPlanUpdate(
  state: Conversation,
  turnId: string,
  phases: AgentPhaseSnapshot[] | undefined,
  hasPhases: boolean,
  workspaceFocus: WorkspaceFocus | null | undefined,
  hasWorkspaceFocus: boolean,
  workbenchSnapshot: WorkbenchSnapshotV2 | null | undefined,
  hasWorkbenchSnapshot: boolean,
): ReducerOutput {
  let touched = false;
  const turns = state.turns.map((turn) => {
    if (turn.id !== turnId) return turn;
    touched = true;
    return {
      ...turn,
      ...(hasPhases ? { phases } : {}),
      ...(hasWorkspaceFocus ? { workspaceFocus: workspaceFocus ?? null } : {}),
      ...(hasWorkbenchSnapshot
        ? { workbenchSnapshot: workbenchSnapshot ?? null }
        : {}),
    };
  });
  if (!touched) {
    return { next: state, changedTurnIds: [], changedItemIds: [] };
  }
  return {
    next: { ...state, turns },
    changedTurnIds: [turnId],
    changedItemIds: [],
  };
}

function applyWorkbenchSnapshot(
  state: Conversation,
  turnId: string,
  snapshot: WorkbenchSnapshotV2,
): ReducerOutput {
  let touched = false;
  const turns = state.turns.map((turn) => {
    if (turn.id !== turnId) return turn;
    touched = true;
    return {
      ...turn,
      workbenchSnapshot: snapshot,
      phases: snapshot.phases,
      ...(snapshot.workspaceFocus !== undefined
        ? { workspaceFocus: snapshot.workspaceFocus ?? null }
        : {}),
    };
  });
  if (!touched) {
    return { next: state, changedTurnIds: [], changedItemIds: [] };
  }
  return {
    next: { ...state, turns },
    changedTurnIds: [turnId],
    changedItemIds: [],
  };
}

function applyMcpToolProgress(
  state: Conversation,
  turnId: string,
  itemId: string,
  progress: McpToolProgress,
  workspaceFocus: WorkspaceFocus | null | undefined,
  hasWorkspaceFocus: boolean,
): ReducerOutput {
  const turnIdx = state.turns.findIndex((t) => t.id === turnId);
  const turn = state.turns[turnIdx];
  if (!turn) {
    return unchanged(state);
  }
  const itemIdx = turn.items.findIndex(
    (item) => item.id === itemId && item.type === "mcpToolCall",
  );
  const existing = turn.items[itemIdx];
  if (!existing) {
    return unchanged(state);
  }
  const updated = { ...existing, progress } as Item;
  const turnPatch = hasWorkspaceFocus
    ? { workspaceFocus: workspaceFocus ?? null }
    : undefined;
  return {
    next: replaceTurnItem(state, turn, turnIdx, itemIdx, updated, turnPatch),
    changedTurnIds: [turnId],
    changedItemIds: [itemId],
  };
}

function applyFileChangeHunkDelta(
  state: Conversation,
  turnId: string,
  itemId: string,
  path: string,
  op: "create" | "update" | "delete",
  hunk: FileHunk,
  workspaceFocus: WorkspaceFocus | null | undefined,
  hasWorkspaceFocus: boolean,
): ReducerOutput {
  const turnIdx = state.turns.findIndex((t) => t.id === turnId);
  const turn = state.turns[turnIdx];
  if (!turn) {
    return unchanged(state);
  }
  const itemIdx = turn.items.findIndex(
    (entry) => entry.id === itemId && entry.type === "fileChange",
  );
  const item = turn.items[itemIdx];
  if (!item || item.type !== "fileChange") {
    return unchanged(state);
  }
  const changeIndex = item.changes.findIndex((change) => change.path === path);
  const change = item.changes[changeIndex];
  let changes;
  if (!change) {
    changes = [...item.changes, { path, op, hunks: [hunk] }];
  } else {
    const hunks = change.hunks ?? [];
    const hunkIndex = hunks.findIndex((existing) => existing.id === hunk.id);
    const nextHunks =
      hunkIndex === -1 ? [...hunks, hunk] : replaceAt(hunks, hunkIndex, hunk);
    changes = replaceAt(item.changes, changeIndex, {
      ...change,
      op: change.op ?? op,
      hunks: nextHunks,
    });
  }
  const updated = { ...item, changes } as Item;
  const turnPatch = hasWorkspaceFocus
    ? { workspaceFocus: workspaceFocus ?? null }
    : undefined;
  return {
    next: replaceTurnItem(state, turn, turnIdx, itemIdx, updated, turnPatch),
    changedTurnIds: [turnId],
    changedItemIds: [itemId],
  };
}

function applyHunkDecision(
  state: Conversation,
  turnId: string,
  itemId: string,
  hunkId: string,
  decision: "accepted" | "rejected",
): ReducerOutput {
  const turnIdx = state.turns.findIndex((t) => t.id === turnId);
  const turn = state.turns[turnIdx];
  if (!turn) return unchanged(state);
  const itemIdx = turn.items.findIndex(
    (it) => it.id === itemId && it.type === "fileChange",
  );
  const item = turn.items[itemIdx];
  if (!item || item.type !== "fileChange") return unchanged(state);
  let hit = false;
  const changes = item.changes.map((ch) => {
    if (!ch.hunks) return ch;
    const hunkIndex = ch.hunks.findIndex((h) => h.id === hunkId);
    const target = ch.hunks[hunkIndex];
    if (!target) return ch;
    hit = true;
    return {
      ...ch,
      hunks: replaceAt(ch.hunks, hunkIndex, { ...target, decision }),
    };
  });
  if (!hit) return unchanged(state);
  const updated = { ...item, changes } as Item;
  return {
    next: replaceTurnItem(state, turn, turnIdx, itemIdx, updated),
    changedTurnIds: [turnId],
    changedItemIds: [itemId],
  };
}
