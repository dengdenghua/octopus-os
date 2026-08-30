// React hook bridging RealtimeClient + reducer to component state.
//
// Usage:
//
//   const { state, startTurn, resolveApproval } = useRealtimeThread({
//     threadId: "thread-abc",
//   });
//
// State is a Conversation; ``startTurn`` returns when the server emits
// turn/completed for that turn — or, if the socket drops mid-turn after
// the turn was confirmed started (turn/started observed), it resolves
// early and leaves turn-state recovery to reconnect + resume. It only
// rejects when the turn was never delivered. Approvals show up as
// ``state.pendingApprovals`` and are resolved via ``resolveApproval``.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getBackendTransportBaseURL } from "@/core/config";
import { getToken } from "@/core/auth/api";
import type { SandboxPolicy } from "@/core/permissions";
import type { ReasoningEffort } from "@/core/threads";

import { createDefaultClient, type RealtimeClient } from "./client";
import type { JsonRpcRequest } from "./envelope";
import {
  type Conversation,
  emptyConversation,
  type PendingApproval,
} from "./items";
import {
  type ConversationEvent,
  reduce,
  type ReducerDiagnostic,
} from "./reducer";
import { replayEvents, type SequencedLoggedEvent } from "./replay";
import {
  createDefaultReplayCache,
  type ReplayCacheStore,
} from "./replay-cache";
import {
  applyVitalNotification,
  emptyVitalsMarks,
  seedVitalsFromResumedTurn,
  type StreamVitals,
  type VitalsMarks,
} from "./stream-vitals";
import { useStreamVitals } from "./use-stream-vitals";
import {
  appendStreamTelemetry,
  createStreamTurnTelemetry,
  type StreamTurnOutcome,
} from "./stream-telemetry";

// Item types that represent the agent actively doing work — a running one
// keeps a silent turn out of the "slow / maybe-stuck" bucket (a 60s command
// or a busy subagent produces no deltas yet is plainly still working).
// Also covers long internal reasoning / plan generation where the backend
// does not stream intermediate deltas to the client — without these, the
// status strip flips to "still going, a bit slow" the moment the model
// spends >10s thinking before producing its first text token.
const WORK_ITEM_TYPES = new Set<string>([
  "commandExecution",
  "fileChange",
  "mcpToolCall",
  "subagent",
  "reasoning",
  "plan",
]);

export interface UseRealtimeThreadArgs {
  threadId: string;
  /** Local replay cache (IndexedDB in the app, in-memory in tests).
   * Enables instant cold-start rendering from the persisted event log;
   * omit to disable the cache entirely. */
  replayCache?: ReplayCacheStore;
  // Inject for tests. Defaults to a real client backed by the raw WebSocket
  // transport URL (the packaged renderer uses a custom origin for HTTP only).
  clientFactory?: (deps: {
    onIncomingRequest: (req: JsonRpcRequest) => Promise<unknown>;
    onNotification: (n: {
      method: string;
      params: Record<string, unknown>;
    }) => void;
  }) => RealtimeClient;
}

export function visibleConversationForThread(
  state: Conversation,
  threadId: string,
): Conversation {
  return state.threadId === threadId ? state : emptyConversation(threadId);
}

export interface UseRealtimeThreadValue {
  state: Conversation;
  connected: boolean;
  startTurn: (params: {
    input: string;
    /** Explicit local project directory. The backend validates/rewrites this
     * at the authenticated boundary before it becomes the execution cwd. */
    cwd?: string;
    /** Stable client-minted id reused by the server's UserMessageItem. */
    clientItemId?: string;
    attachments?: Record<string, unknown>[];
    approvalPolicy?: "never" | "on-request" | "untrusted";
    sandboxPolicy?: SandboxPolicy;
    planningMode?: boolean;
    model?: string;
    effort?: ReasoningEffort;
    metadata?: Record<string, unknown>;
    /** Optional topology id for callers that explicitly need the
     * runtime to route through the team topology path instead of
     * single-agent ReAct. The unified chat workspace sets this when
     * collaborators are pulled into the current task in 集群 mode.
     */
    topologyId?: string;
  }) => Promise<void>;
  /** Add a user correction to the currently running turn. The runtime
   * consumes it at the next safe model boundary (never halfway through a
   * tool side effect). */
  steer: (params: { input: string; itemId?: string }) => Promise<void>;
  resolveApproval: (requestId: string | number, accept: boolean) => void;
  /** Live streaming vitals for the active turn (TTFT, delta cadence, stall
   * detection). Lets the status strip tell "model still working" apart
   * from "connection stuck". ``phase: "idle"`` between turns. */
  vitals: StreamVitals;
  resume: () => Promise<void>;
  /** Page backwards: prepend the next batch of turns older than the
   * current `state.turns[0]`. No-op when `state.hasMoreTurns` is
   * false or a load is already in flight. */
  loadOlderTurns: () => Promise<void>;
  /** Cancel the turn that's currently in progress, if any. No-op when
   * no turn is live. The returned promise resolves once the server has
   * acknowledged the interrupt RPC — not when the turn actually ends;
   * watch ``state.turns[last].status === "interrupted"`` for that. */
  interrupt: () => Promise<void>;
  /** Persist a compacted summary turn for older history, then refresh. */
  compact: () => Promise<{
    compacted: boolean;
    reason?: string;
    turnCount?: number;
    keepRecent?: number;
  }>;
  /** Accept or reject a single hunk on a FileChange item. Rejection
   * reverse-applies that hunk's diff on the server; acceptance is
   * informational (the file already has the patched form). The
   * server broadcasts ``item/fileChange/hunkDecision`` so every
   * connected client updates uniformly. */
  decideHunk: (args: {
    turnId: string;
    itemId: string;
    hunkId: string;
    path: string;
    decision: "accepted" | "rejected";
    diff?: string;
  }) => Promise<void>;
}

// Newest turns fetched per thread/resume page. Large threads resume
// with the most recent window; older history pages in on demand via
// loadOlderTurns().
const RESUME_TURN_LIMIT = 20;
interface ResumeResponse {
  thread?: { id: string; path?: string };
  turns: Conversation["turns"];
  hasMore?: boolean;
  totalTurns?: number;
  lastTurnId?: string | null;
  lastTurnStatus?: string | null;
  incremental?: boolean;
  nextEventSequence?: number;
  eventStreamId?: string | null;
}

/** thread/events response — raw sequenced log slice for client-side
 * replay. Drift-check metadata describes the same authoritative snapshot
 * the events were cut from (meaningful on the final page). */
interface ThreadEventsResponse {
  thread?: { id: string; path?: string };
  events?: SequencedLoggedEvent[];
  cursor?: number;
  streamId?: string | null;
  requiresReset?: boolean;
  hasMore?: boolean;
  turnCount?: number;
  lastTurnId?: string | null;
  lastTurnStatus?: string | null;
}

// Events fetched per thread/events page. Paging keeps a single response
// bounded for threads with huge logs; the client loops until hasMore.
const EVENTS_PAGE_LIMIT = 5000;

// A recovered in-flight turn may be owned by a different gateway worker,
// which means this socket cannot rely on that worker's in-memory live fanout.
// Poll the durable log while such a tail is active. The loop self-schedules
// only after the previous request settles, so a slow request can never stack
// another one on top of it.
const ACTIVE_TAIL_POLL_INTERVAL_MS = 750;
const ACTIVE_TAIL_POLL_MAX_BACKOFF_MS = 3_000;
// Normal same-worker turns primarily use live fanout. If their live-first
// dedupe ledger reaches this watermark, immediately start durable polling to
// confirm and compact it instead of letting it grow for the life of a tab.
const UNCONFIRMED_LIVE_EVENT_ID_POLL_THRESHOLD = 512;

// Live notifications and log-folded events both carry the persisted
// ``eventId``. Either path can deliver an event first (live push vs.
// incremental thread/events fetch), so each id is applied exactly once.
// Confirmed ids may be bounded because the durable cursor has moved beyond
// them. Live ids are held in a separate, unbounded-until-confirmed ledger
// below: trimming one before thread/events supplies its log coordinate could
// duplicate a delta when live fanout and polling overlap.
const SEEN_EVENT_ID_LIMIT = 10_000;

function markSeenEventId(seen: Set<string>, eventId: string): void {
  if (seen.has(eventId)) {
    // Refresh recency so hot ids survive the FIFO trim.
    seen.delete(eventId);
    seen.add(eventId);
    return;
  }
  seen.add(eventId);
  if (seen.size > SEEN_EVENT_ID_LIMIT) {
    const excess = seen.size - SEEN_EVENT_ID_LIMIT + 1_000;
    let removed = 0;
    for (const oldest of seen) {
      seen.delete(oldest);
      removed += 1;
      if (removed >= excess) break;
    }
  }
}

/** Replace changed turn snapshots without disturbing the surrounding
 * timeline. The server returns whole snapshots for affected turns, so
 * reconnect recovery never has to replay individual deltas in the UI. */
function mergeTurnSnapshots(
  existing: Conversation["turns"],
  changed: Conversation["turns"],
): Conversation["turns"] {
  if (changed.length === 0) return existing;
  const changedById = new Map(changed.map((turn) => [turn.id, turn]));
  const existingIds = new Set(existing.map((turn) => turn.id));
  return [
    ...existing.map((turn) => changedById.get(turn.id) ?? turn),
    ...changed.filter((turn) => !existingIds.has(turn.id)),
  ];
}

export function useRealtimeThread(
  args: UseRealtimeThreadArgs,
): UseRealtimeThreadValue {
  const [state, setState] = useState<Conversation>(() =>
    emptyConversation(args.threadId),
  );
  const [connected, setConnected] = useState(false);

  // Pending approvals are surfaced through state, but the resolution
  // map (requestId → resolver) lives here so we can reply on the
  // socket without round-tripping through React render cycles.
  const approvalResolvers = useRef<
    Map<
      string | number,
      (decision: { action: string; reason?: string }) => void
    >
  >(new Map());
  // Client-side expiry timers, keyed like the resolvers. The server
  // denies on its own timeout (params.timeoutMs); these keep the
  // dialog from outliving that decision as a zombie prompt.
  const approvalTimers = useRef<
    Map<string | number, ReturnType<typeof setTimeout>>
  >(new Map());
  const clientRef = useRef<RealtimeClient | null>(null);
  // Streaming-vitals timestamps, mutated off the notification stream (no
  // re-render) and read by a ticking hook. A ref so the ``onNotification``
  // closure sees the live object across reconnects.
  const vitalsMarksRef = useRef<VitalsMarks>(emptyVitalsMarks());
  // Latest reduced snapshot for callbacks that don't want React's stale
  // closure semantics. Updated synchronously alongside ``setState``.
  const stateRef = useRef<Conversation>(state);
  // One-based physical event-log cursor returned by thread/resume. It is
  // intentionally independent of rendered item sequence: reconnect asks
  // only for turns changed after this durable server position.
  const resumeCursorRef = useRef<number | null>(null);
  // Stable identity of the append-only stream behind the numeric cursor.
  // If a log is replaced or restored, the server returns a full snapshot
  // instead of interpreting the old cursor inside unrelated history.
  const resumeStreamIdRef = useRef<string | null>(null);
  // Event ids whose physical log position has been observed by
  // thread/events. These can use the bounded FIFO because the resume cursor
  // is already beyond them.
  const seenEventIdsRef = useRef<Set<string>>(new Set());
  // Live event ids without a confirmed log position. Never FIFO-trim these:
  // a slow/cross-worker poll may fetch the matching delta much later. A
  // fetched page moves the id into ``seenEventIdsRef``.
  const unconfirmedLiveEventIdsRef = useRef<Set<string>>(new Set());
  // Lazily created default replay cache (IndexedDB where available).
  // Explicit ``args.replayCache`` wins; ``null`` here means "not yet
  // created", so tests injecting their own store never pay for one.
  const defaultReplayCacheRef = useRef<ReplayCacheStore | null>(null);
  if (!defaultReplayCacheRef.current) {
    defaultReplayCacheRef.current = createDefaultReplayCache();
  }
  const replayCache = args.replayCache ?? defaultReplayCacheRef.current;
  // Delivery watches for in-flight turn/start requests. The server holds
  // the turn/start RPC response until the whole turn has run to
  // completion, so ANY mid-turn socket drop rejects the pending request
  // even though the turn was accepted and the user message persisted.
  // A turn/started notification observed after the request went out is
  // the real delivery signal — ``startTurn`` uses it to swallow later
  // transport rejections (reconnect + resume recover the turn state).
  const turnDeliveryWatchesRef = useRef<
    Set<{ delivered: boolean; clientItemId?: string }>
  >(new Set());

  // Reducer anomalies feed the per-turn vitals marks so the turn's
  // telemetry record carries them. Keep the callback stable — it sits
  // in ``applyEvent``'s dependency list.
  const onReducerDiagnostic = useCallback(
    (diagnostic: ReducerDiagnostic): void => {
      if (diagnostic.type !== "lateDeltaDropped") return;
      vitalsMarksRef.current.lateDeltaDrops += 1;
      if (import.meta.env.DEV) {
        console.warn(
          `[realtime] late delta dropped for ${diagnostic.kind} item ${diagnostic.itemId} (status=${diagnostic.itemStatus}, +${diagnostic.deltaLength} chars)`,
        );
      }
    },
    [],
  );

  const persistTurnTelemetry = useCallback(
    (turnId: string, outcome: StreamTurnOutcome, completedAt = Date.now()) => {
      const record = createStreamTurnTelemetry({
        threadId: args.threadId,
        turnId,
        outcome,
        marks: vitalsMarksRef.current,
        completedAt,
      });
      if (record) appendStreamTelemetry(record);
    },
    [args.threadId],
  );

  const applyEvent = useCallback(
    (evt: ConversationEvent) => {
      setState((prev) => {
        // Second line of defense: reject events that belong to a different
        // thread than the one currently held in state. This guards against
        // any in-flight notifications from a previous thread's WebSocket
        // that slip through between cleanup and the socket actually closing.
        const eventThreadId =
          "threadId" in evt.params ? evt.params.threadId : evt.params.thread.id;
        if (
          typeof eventThreadId === "string" &&
          eventThreadId !== prev.threadId
        ) {
          return prev;
        }
        const { next } = reduce(prev, evt, onReducerDiagnostic);
        stateRef.current = next;
        return next;
      });
    },
    [onReducerDiagnostic],
  );

  // Build/teardown the client when threadId changes.
  useEffect(() => {
    setState(emptyConversation(args.threadId));
    stateRef.current = emptyConversation(args.threadId);
    resumeCursorRef.current = null;
    resumeStreamIdRef.current = null;
    vitalsMarksRef.current = emptyVitalsMarks();
    const resolvers = approvalResolvers.current;
    const timers = approvalTimers.current;
    const onIncomingRequest = async (req: JsonRpcRequest): Promise<unknown> =>
      new Promise((resolve) => {
        const pending: PendingApproval = {
          requestId: req.id,
          method: req.method,
          params: req.params,
          createdAt: new Date().toISOString(),
        };
        setState((prev) => {
          const next: Conversation = {
            ...prev,
            pendingApprovals: [...prev.pendingApprovals, pending],
          };
          stateRef.current = next;
          return next;
        });
        approvalResolvers.current.set(req.id, (decision) => {
          // Strip from pendingApprovals once resolved.
          setState((prev) => {
            const next: Conversation = {
              ...prev,
              pendingApprovals: prev.pendingApprovals.filter(
                (p) => p.requestId !== req.id,
              ),
            };
            stateRef.current = next;
            return next;
          });
          approvalResolvers.current.delete(req.id);
          const timer = approvalTimers.current.get(req.id);
          if (timer !== undefined) {
            clearTimeout(timer);
            approvalTimers.current.delete(req.id);
          }
          resolve(decision);
        });
        // Expire in lockstep with the server: once its timeout lapses
        // the request id is dead — the server already denied — so a
        // reply would go nowhere. Auto-decline locally to drop the
        // dialog and settle the promise (an unsettled promise here
        // leaks the client's reply tracker entry forever).
        const timeoutMs =
          typeof req.params?.timeoutMs === "number" && req.params.timeoutMs > 0
            ? req.params.timeoutMs
            : 600_000;
        approvalTimers.current.set(
          req.id,
          setTimeout(() => {
            approvalResolvers.current.get(req.id)?.({
              action: "decline",
              reason: "timeout",
            });
          }, timeoutMs),
        );
      });

    let cancelled = false;
    let openedOnce = false;
    let online = false;
    let resumeSeq = 0;
    let resumeInFlight = false;

    // The confirmed ledger is safe to bound only because every id in it has
    // been observed in a fetched log slice. Keep live-only ids separate until
    // a slice supplies their durable position.
    seenEventIdsRef.current.clear();
    unconfirmedLiveEventIdsRef.current.clear();

    type EventFetchOutcome =
      | { reset: true }
      | {
          reset: false;
          events: SequencedLoggedEvent[];
          finalPage: ThreadEventsResponse;
        };

    const fetchEventPages = async (
      client: RealtimeClient,
      afterSequence: number,
      eventStreamId: string | null,
    ): Promise<EventFetchOutcome> => {
      const fetchPage = (after: number): Promise<ThreadEventsResponse> =>
        client.request<ThreadEventsResponse>("thread/events", {
          threadId: args.threadId,
          afterSequence: after,
          ...(eventStreamId ? { eventStreamId } : {}),
          limit: EVENTS_PAGE_LIMIT,
        });
      let page = await fetchPage(afterSequence);
      if (page.requiresReset === true) return { reset: true };
      const events = [...(page.events ?? [])];
      while (page.hasMore === true && events.length > 0) {
        const lastSequence = events[events.length - 1]!.sequence;
        page = await fetchPage(lastSequence);
        if (page.requiresReset === true) return { reset: true };
        events.push(...(page.events ?? []));
      }
      return { reset: false, events, finalPage: page };
    };

    /** Fold one authoritative event slice and advance the shared cursor.
     * Returns null when drift makes a snapshot reset necessary. */
    const foldFetchedEvents = (
      events: SequencedLoggedEvent[],
      finalPage: ThreadEventsResponse,
    ): string | null => {
      const confirmed = seenEventIdsRef.current;
      const liveUnconfirmed = unconfirmedLiveEventIdsRef.current;
      const fresh = events.filter(
        (event) =>
          typeof event.eventId !== "string" ||
          (!confirmed.has(event.eventId) &&
            !liveUnconfirmed.has(event.eventId)),
      );
      const probe = replayEvents(fresh, {
        base: stateRef.current,
      }).conversation;
      const lastTurn = probe.turns[probe.turns.length - 1];
      const tailDrift =
        typeof finalPage.lastTurnId === "string" &&
        (!lastTurn ||
          lastTurn.id !== finalPage.lastTurnId ||
          lastTurn.status !== finalPage.lastTurnStatus);
      const countDrift =
        typeof finalPage.turnCount === "number" &&
        stateRef.current.hasMoreTurns === false &&
        probe.turns.length !== finalPage.turnCount;
      if (tailDrift || countDrift) return null;

      if (
        typeof finalPage.cursor === "number" &&
        Number.isFinite(finalPage.cursor) &&
        finalPage.cursor >= 0
      ) {
        resumeCursorRef.current = finalPage.cursor;
      }
      if (typeof finalPage.streamId === "string") {
        resumeStreamIdRef.current = finalPage.streamId;
      }
      void replayCache
        .append(args.threadId, events, {
          streamId: resumeStreamIdRef.current,
          cursor: resumeCursorRef.current ?? 0,
        })
        .catch(() => {});

      // Cursor confirmation comes before the React fold so a simultaneous
      // live duplicate cannot append the same delta twice.
      for (const event of events) {
        if (typeof event.eventId !== "string") continue;
        liveUnconfirmed.delete(event.eventId);
        markSeenEventId(confirmed, event.eventId);
      }
      setState((prev) => {
        const folded = replayEvents(fresh, { base: prev }).conversation;
        const next: Conversation = { ...folded, resumeState: "resumed" };
        const resumedActive = [...next.turns]
          .reverse()
          .find((turn) => turn.status === "inProgress");
        seedVitalsFromResumedTurn(
          vitalsMarksRef.current,
          resumedActive ?? null,
          Date.now(),
        );
        stateRef.current = next;
        return next;
      });
      return typeof finalPage.lastTurnStatus === "string"
        ? finalPage.lastTurnStatus
        : (lastTurn?.status ?? "");
    };

    let tailPollActive = false;
    let tailPollInFlight = false;
    let tailPollSettlement: Promise<void> | null = null;
    let tailPollTimer: ReturnType<typeof setTimeout> | null = null;
    let tailPollGeneration = 0;
    let tailPollFailures = 0;
    let terminalDrainNeeded = false;
    let terminalDrainStarted = false;

    const clearTailPollTimer = (): void => {
      if (tailPollTimer === null) return;
      clearTimeout(tailPollTimer);
      tailPollTimer = null;
    };

    const stopTailPolling = (): void => {
      tailPollActive = false;
      tailPollGeneration += 1;
      clearTailPollTimer();
      tailPollFailures = 0;
      terminalDrainNeeded = false;
      terminalDrainStarted = false;
    };

    const latestTailIsActive = (): boolean =>
      stateRef.current.turns.at(-1)?.status === "inProgress";

    const scheduleTailPoll = (
      client: RealtimeClient,
      delayMs: number,
    ): void => {
      if (
        cancelled ||
        !online ||
        !tailPollActive ||
        tailPollInFlight ||
        tailPollTimer !== null
      ) {
        return;
      }
      tailPollTimer = setTimeout(() => {
        tailPollTimer = null;
        void runTailPoll(client);
      }, delayMs);
    };

    const startTailPolling = (
      client: RealtimeClient,
      recoveredActive = false,
      initialDelayMs = ACTIVE_TAIL_POLL_INTERVAL_MS,
    ): void => {
      if (cancelled || !online || (!recoveredActive && !latestTailIsActive())) {
        return;
      }
      if (!tailPollActive) {
        tailPollActive = true;
        tailPollFailures = 0;
        terminalDrainNeeded = false;
        terminalDrainStarted = false;
      }
      if (initialDelayMs === 0 && !tailPollInFlight) {
        clearTailPollTimer();
      }
      scheduleTailPoll(client, initialDelayMs);
    };

    const observeTailTerminal = (client: RealtimeClient): void => {
      if (!tailPollActive || terminalDrainNeeded) return;
      terminalDrainNeeded = true;
      if (!tailPollInFlight) {
        clearTailPollTimer();
        scheduleTailPoll(client, 0);
      }
    };
    const requestResume = (
      client: RealtimeClient,
      mode: "preserve-live" | "replace",
    ): void => {
      const pendingTailPoll = tailPollSettlement;
      stopTailPolling();
      const seq = ++resumeSeq;
      resumeInFlight = true;
      setState((prev) => {
        if (prev.resumeState === "resuming") return prev;
        const next: Conversation = { ...prev, resumeState: "resuming" };
        stateRef.current = next;
        return next;
      });
      const afterSequence = resumeCursorRef.current;
      const eventStreamId = resumeStreamIdRef.current;
      // Incremental reconnect: fold only the events we missed via
      // thread/events instead of re-pulling whole turn snapshots. The
      // snapshot path below remains the authoritative fallback (first
      // resume, stream replacement, drift).
      if (afterSequence !== null) {
        const beginEventResume = (): void => {
          if (cancelled || seq !== resumeSeq) return;
          runEventResume(client, seq, afterSequence, eventStreamId);
        };
        // A transport can report close/open before a mocked or unusual
        // request implementation rejects its old poll. Preserve strict
        // single-flight across that boundary: catch-up starts only after the
        // physical request settles, not merely after its generation is stale.
        if (pendingTailPoll) {
          void pendingTailPoll.then(beginEventResume);
        } else {
          beginEventResume();
        }
        return;
      }
      const liveIdsBeforeSnapshot = new Set(unconfirmedLiveEventIdsRef.current);
      void client
        .request<ResumeResponse>("thread/resume", {
          threadId: args.threadId,
          limit: RESUME_TURN_LIMIT,
        })
        .then((result) => {
          if (cancelled || seq !== resumeSeq) return;
          resumeInFlight = false;
          if (
            typeof result.nextEventSequence === "number" &&
            Number.isFinite(result.nextEventSequence) &&
            result.nextEventSequence >= 0
          ) {
            resumeCursorRef.current = result.nextEventSequence;
            // These ids were already live-applied before the authoritative
            // snapshot was requested; its returned cursor is proof that the
            // snapshot covers their durable prefix. Live ids arriving while
            // the request was in flight remain unconfirmed for the next poll.
            for (const eventId of liveIdsBeforeSnapshot) {
              unconfirmedLiveEventIdsRef.current.delete(eventId);
              markSeenEventId(seenEventIdsRef.current, eventId);
            }
          }
          if (typeof result.eventStreamId === "string") {
            resumeStreamIdRef.current = result.eventStreamId;
          }
          // A full snapshot means the cache is either empty (first open)
          // or was just cleared (reset) — refill it in the background so
          // the NEXT open takes the instant event-mode path. Read-only
          // RPC, fire-and-forget, never blocks the live flow.
          if (result.incremental !== true) {
            void backfillReplayCache(
              client,
              typeof result.eventStreamId === "string"
                ? result.eventStreamId
                : null,
            );
          }
          const current = stateRef.current;
          const serverTurns = result.turns ?? [];
          // A full snapshot is authoritative about the tail lifecycle. Do
          // not preserve a locally hydrated in-progress turn when the
          // server has already reaped it and reports a terminal tail; that
          // combination leaves the UI stuck on “thinking” after a dropped
          // socket or a stale replay-cache entry.
          const authoritativeTerminal =
            result.incremental !== true &&
            typeof result.lastTurnStatus === "string" &&
            result.lastTurnStatus !== "inProgress";
          const projectedTurns =
            result.incremental === true
              ? mergeTurnSnapshots(current.turns, serverTurns)
              : mode === "preserve-live" &&
                  !authoritativeTerminal &&
                  current.turns.length > 0 &&
                  (!result.thread?.id || current.threadId === result.thread.id)
                ? mergeTurnSnapshots(serverTurns, current.turns)
                : serverTurns;
          setState((prev) => {
            const turns =
              result.incremental === true
                ? mergeTurnSnapshots(prev.turns, serverTurns)
                : mode === "preserve-live" &&
                    !authoritativeTerminal &&
                    prev.turns.length > 0 &&
                    (!result.thread?.id || prev.threadId === result.thread.id)
                  ? mergeTurnSnapshots(serverTurns, prev.turns)
                  : serverTurns;
            const next: Conversation = {
              ...prev,
              turns,
              resumeState: "resumed",
              hasMoreTurns:
                result.incremental === true
                  ? prev.hasMoreTurns
                  : result.hasMore === true,
            };
            const resumedActive = [...turns]
              .reverse()
              .find((turn) => turn.status === "inProgress");
            seedVitalsFromResumedTurn(
              vitalsMarksRef.current,
              resumedActive ?? null,
              Date.now(),
            );
            stateRef.current = next;
            return next;
          });
          if (projectedTurns.at(-1)?.status === "inProgress") {
            startTailPolling(client, true);
          }
        })
        .catch(() => {
          if (cancelled || seq !== resumeSeq) return;
          resumeInFlight = false;
          setState((prev) => {
            const next: Conversation = { ...prev, resumeState: "needsResume" };
            stateRef.current = next;
            return next;
          });
        });
    };

    /**
     * Incremental resume in event mode: fetch only the persisted events
     * after our durable cursor and fold them through ``replayEvents``.
     *
     * Correctness pillars:
     *  - ``eventId`` dedupe — events already applied via live push are
     *    skipped on fold (and vice versa), so the two delivery paths
     *    compose without double-appending deltas;
     *  - drift probe — the fold is recomputed purely against the server's
     *    authoritative tail metadata (same snapshot the events were cut
     *    from); any mismatch discards the event path and falls back to the
     *    snapshot resume below;
     *  - ``requiresReset`` (stream replacement, compaction inside the
     *    window) likewise defers to the snapshot path.
     */
    const runEventResume = (
      client: RealtimeClient,
      seq: number,
      afterSequence: number,
      eventStreamId: string | null,
    ): void => {
      const fallbackToSnapshot = (): void => {
        stopTailPolling();
        resumeCursorRef.current = null;
        resumeStreamIdRef.current = null;
        seenEventIdsRef.current.clear();
        unconfirmedLiveEventIdsRef.current.clear();
        resumeInFlight = false;
        // The stream was replaced or the window is unsafe — the cached
        // prefix is no longer interpretable either. Refilled by the
        // backfill after the snapshot resume lands.
        void replayCache.clear(args.threadId).catch(() => {});
        requestResume(client, "replace");
      };
      void fetchEventPages(client, afterSequence, eventStreamId)
        .then((outcome) => {
          if (cancelled || seq !== resumeSeq) return;
          if (outcome.reset) {
            fallbackToSnapshot();
            return;
          }
          const tailStatus = foldFetchedEvents(
            outcome.events,
            outcome.finalPage,
          );
          if (tailStatus === null) {
            if (import.meta.env.DEV) {
              console.warn(
                "[realtime] event-mode resume diverged from authoritative tail; falling back to snapshot resume",
              );
            }
            fallbackToSnapshot();
            return;
          }
          resumeInFlight = false;
          if (tailStatus === "inProgress") startTailPolling(client, true);
        })
        .catch(() => {
          if (cancelled || seq !== resumeSeq) return;
          resumeInFlight = false;
          setState((prev) => {
            const next: Conversation = { ...prev, resumeState: "needsResume" };
            stateRef.current = next;
            return next;
          });
          // The cursor remains valid after a transient fetch error. Let the
          // recovered-tail loop retry with bounded backoff if work is live.
          startTailPolling(client);
        });
    };

    async function runTailPoll(client: RealtimeClient): Promise<void> {
      if (cancelled || !online || !tailPollActive || tailPollInFlight) return;
      if (!terminalDrainNeeded && !latestTailIsActive()) {
        terminalDrainNeeded = true;
      }
      const isFinalDrain = terminalDrainNeeded && !terminalDrainStarted;
      if (terminalDrainNeeded && !isFinalDrain) return;
      const afterSequence = resumeCursorRef.current;
      if (afterSequence === null) {
        stopTailPolling();
        requestResume(client, "replace");
        return;
      }

      const generation = tailPollGeneration;
      const eventStreamId = resumeStreamIdRef.current;
      tailPollInFlight = true;
      if (isFinalDrain) terminalDrainStarted = true;
      const fetchPromise = fetchEventPages(
        client,
        afterSequence,
        eventStreamId,
      );
      const settlement = fetchPromise.then(
        () => undefined,
        () => undefined,
      );
      tailPollSettlement = settlement;
      const releasePhysicalRequest = (): void => {
        if (tailPollSettlement !== settlement) return;
        tailPollSettlement = null;
        tailPollInFlight = false;
      };
      try {
        const outcome = await fetchPromise;
        releasePhysicalRequest();
        if (
          cancelled ||
          !online ||
          !tailPollActive ||
          generation !== tailPollGeneration
        ) {
          if (tailPollActive && online) {
            scheduleTailPoll(client, ACTIVE_TAIL_POLL_INTERVAL_MS);
          }
          return;
        }
        if (outcome.reset) {
          stopTailPolling();
          resumeCursorRef.current = null;
          resumeStreamIdRef.current = null;
          seenEventIdsRef.current.clear();
          unconfirmedLiveEventIdsRef.current.clear();
          void replayCache.clear(args.threadId).catch(() => {});
          requestResume(client, "replace");
          return;
        }
        const tailStatus = foldFetchedEvents(outcome.events, outcome.finalPage);
        if (tailStatus === null) {
          stopTailPolling();
          resumeCursorRef.current = null;
          resumeStreamIdRef.current = null;
          seenEventIdsRef.current.clear();
          unconfirmedLiveEventIdsRef.current.clear();
          void replayCache.clear(args.threadId).catch(() => {});
          requestResume(client, "replace");
          return;
        }

        tailPollFailures = 0;
        if (isFinalDrain) {
          stopTailPolling();
          return;
        }
        if (terminalDrainNeeded || tailStatus !== "inProgress") {
          terminalDrainNeeded = true;
          scheduleTailPoll(client, 0);
          return;
        }
        scheduleTailPoll(client, ACTIVE_TAIL_POLL_INTERVAL_MS);
      } catch {
        releasePhysicalRequest();
        if (
          cancelled ||
          !online ||
          !tailPollActive ||
          generation !== tailPollGeneration
        ) {
          if (tailPollActive && online) {
            scheduleTailPoll(client, ACTIVE_TAIL_POLL_INTERVAL_MS);
          }
          return;
        }
        if (isFinalDrain) terminalDrainStarted = false;
        tailPollFailures += 1;
        const retryDelay = Math.min(
          ACTIVE_TAIL_POLL_INTERVAL_MS * 2 ** (tailPollFailures - 1),
          ACTIVE_TAIL_POLL_MAX_BACKOFF_MS,
        );
        scheduleTailPoll(client, retryDelay);
      }
    }

    /**
     * Refill the replay cache in the background after a full snapshot
     * resume. Pages the whole log from sequence 0 (bounded), appending
     * each authoritative slice; a mid-fill stream reset clears and stops.
     * Never interacts with React state — the cache only pays off on the
     * NEXT cold start.
     */
    const backfillReplayCache = async (
      client: RealtimeClient,
      streamId: string | null,
    ): Promise<void> => {
      try {
        const existing = await replayCache.load(args.threadId);
        if (existing && existing.events.length > 0) return;
        let after = 0;
        let knownStreamId = streamId;
        // Safety bound: 20 pages × EVENTS_PAGE_LIMIT events.
        for (let page = 0; page < 20; page += 1) {
          if (cancelled) return;
          // Backfill starts from an empty dedupe ledger and an empty cache,
          // so server-side `coalesce` is safe here (unlike incremental
          // event-mode resume) and cuts the payload dramatically.
          const result = await client.request<ThreadEventsResponse>(
            "thread/events",
            {
              threadId: args.threadId,
              afterSequence: after,
              ...(knownStreamId ? { eventStreamId: knownStreamId } : {}),
              limit: EVENTS_PAGE_LIMIT,
              mode: "coalesce",
            },
          );
          if (cancelled) return;
          if (result.requiresReset === true) {
            await replayCache.clear(args.threadId).catch(() => {});
            return;
          }
          const events = result.events ?? [];
          if (typeof result.streamId === "string") {
            knownStreamId = result.streamId;
          }
          await replayCache
            .append(args.threadId, events, {
              streamId: knownStreamId,
              cursor: result.cursor ?? after,
            })
            .catch(() => {});
          if (result.hasMore !== true || events.length === 0) return;
          // Page by the server's RAW cursor, not the last returned event:
          // coalescing may drop the raw tail events of a slice (their item
          // completed earlier in the same slice), so the last returned
          // sequence can lag behind what was actually consumed.
          after = result.cursor ?? events[events.length - 1]!.sequence;
        }
      } catch {
        // Backfill is best-effort; a failure costs the next open one
        // snapshot resume, nothing more.
      }
    };

    const onNotification = (note: {
      method: string;
      params: Record<string, unknown>;
    }): void => {
      if (cancelled) return;
      const belongsToThread = note.params?.threadId === args.threadId;
      // Live-first ids stay unconfirmed until a fetched cursor includes the
      // same persisted event. This prevents a long recovered turn from
      // FIFO-evicting an id and then double-appending its delta via polling.
      const eventId = note.params?.eventId;
      if (belongsToThread && typeof eventId === "string") {
        if (
          seenEventIdsRef.current.has(eventId) ||
          unconfirmedLiveEventIdsRef.current.has(eventId)
        ) {
          return;
        }
        unconfirmedLiveEventIdsRef.current.add(eventId);
        if (
          unconfirmedLiveEventIdsRef.current.size >=
          UNCONFIRMED_LIVE_EVENT_ID_POLL_THRESHOLD
        ) {
          const activeClient = clientRef.current;
          if (activeClient) startTailPolling(activeClient, true, 0);
        }
      }
      // Record liveness telemetry before the reducer runs. Cheap, pure,
      // ref-mutating — never triggers a render on its own.
      if (belongsToThread) {
        applyVitalNotification(vitalsMarksRef.current, note, Date.now());
      }
      if (belongsToThread && note.method === "turn/completed") {
        const turn = note.params?.turn as
          | { id?: unknown; status?: unknown }
          | undefined;
        const outcome = turn?.status;
        if (
          typeof turn?.id === "string" &&
          (outcome === "completed" ||
            outcome === "paused" ||
            outcome === "cancelled" ||
            outcome === "interrupted" ||
            outcome === "failed")
        ) {
          persistTurnTelemetry(turn.id, outcome);
        }
      } else if (belongsToThread && note.method === "turn/interrupted") {
        const turnId = note.params?.turnId;
        if (typeof turnId === "string") {
          persistTurnTelemetry(turnId, "interrupted");
        }
      }
      if (
        note.method === "turn/started" &&
        note.params?.threadId === args.threadId
      ) {
        // Historical turns replay through the thread/resume *response*,
        // never through this notification path, so a turn/started seen
        // here means a live turn actually began after the watched
        // turn/start request went out on this connection. The server
        // runs turns sequentially per thread, so starts pair FIFO with
        // in-flight requests — mark only the oldest undelivered watch,
        // not all of them (an overlapping second send must not inherit
        // the first turn's start).
        for (const watch of turnDeliveryWatchesRef.current) {
          if (!watch.delivered) {
            watch.delivered = true;
            break;
          }
        }
      } else if (
        note.method === "item/started" &&
        note.params?.threadId === args.threadId
      ) {
        // The durable user-item receipt is an equally authoritative delivery
        // anchor. In a fast failure the detached turn can publish item/started
        // while the originating socket misses turn/started; treating the later
        // RPC rejection as a send failure then restores an already-persisted
        // draft and invites a duplicate retry.
        const item = note.params?.item as { id?: unknown } | undefined;
        if (typeof item?.id === "string") {
          for (const watch of turnDeliveryWatchesRef.current) {
            if (!watch.delivered && watch.clientItemId === item.id) {
              watch.delivered = true;
              break;
            }
          }
        }
      }
      // ``ConversationEvent`` is a discriminated union over a closed
      // method set. Cast through ``unknown`` because the wire side is
      // open-ended; the reducer no-ops anything it doesn't recognize.
      applyEvent(note as unknown as ConversationEvent);
      const turn = note.params?.turn as { status?: unknown } | undefined;
      const terminalObserved =
        belongsToThread &&
        ((note.method === "turn/completed" &&
          (turn?.status === "completed" ||
            turn?.status === "paused" ||
            turn?.status === "cancelled" ||
            turn?.status === "interrupted" ||
            turn?.status === "failed")) ||
          note.method === "turn/interrupted");
      if (terminalObserved) {
        const activeClient = clientRef.current;
        if (activeClient) observeTailTerminal(activeClient);
      }
    };

    const onClose = (_code: number, _reason: string): void => {
      if (cancelled) return;
      online = false;
      stopTailPolling();
      // The socket is gone — flip ``connected`` to false so the UI
      // can show a "reconnecting..." pill. The auto-reconnect logic
      // inside ``RealtimeClient`` will call onOpen again when the
      // new socket is up.
      setConnected(false);
      // The server cancels every pending approval future when the
      // connection drops (ApprovalManager.cancel_all), so the request
      // ids are dead. Drop the dialogs and timers now — replying after
      // reconnect would target a request the server no longer knows.
      for (const timer of approvalTimers.current.values()) {
        clearTimeout(timer);
      }
      approvalTimers.current.clear();
      approvalResolvers.current.clear();
      if (stateRef.current.pendingApprovals.length > 0) {
        setState((prev) => {
          const next: Conversation = { ...prev, pendingApprovals: [] };
          stateRef.current = next;
          return next;
        });
      }
      // Do not invent a turn outcome from a transport failure. The server
      // owns turn lifecycle and may still be running or may have persisted
      // an interruption. Keep the live timeline intact while disconnected;
      // incremental resume reconciles the authoritative snapshot on reopen.
    };

    const onOpen = (): void => {
      if (cancelled) return;
      online = true;
      // Real socket open — only now can the client actually send /
      // receive. Previously we set ``connected`` true the moment
      // ``client.connect()`` returned, which was optimistic: the
      // promise resolves before the WebSocket handshake completes,
      // so a startup screen could "look connected" while sends were
      // queueing in the outbox. Drive the flag from the actual
      // socket open event instead.
      setConnected(true);
      const client = clientRef.current;
      if (
        client &&
        (openedOnce ||
          (stateRef.current.resumeState !== "resumed" && !resumeInFlight))
      ) {
        requestResume(client, "replace");
      } else {
        openedOnce = true;
      }
      openedOnce = true;
    };

    const factory =
      args.clientFactory ??
      ((deps: {
        onIncomingRequest: (req: JsonRpcRequest) => Promise<unknown>;
        onNotification: (n: {
          method: string;
          params: Record<string, unknown>;
        }) => void;
        onOpen?: () => void;
        onClose?: (code: number, reason: string) => void;
      }) =>
        createDefaultClient({
          baseURL: getBackendTransportBaseURL(),
          authToken: () => getToken(),
          onIncomingRequest: deps.onIncomingRequest,
          onNotification: deps.onNotification,
          onOpen: deps.onOpen,
          onClose: deps.onClose,
        }));

    const client = factory({
      onIncomingRequest,
      onNotification,
      onOpen,
      onClose,
    });
    clientRef.current = client;
    // Cold start: hydrate from the local replay cache BEFORE the first
    // resume goes out. A hydrated cursor routes the initial resume into
    // event mode (fetch only what changed since the cache was written);
    // a stale stream id falls back to the snapshot path automatically.
    // Cache failures must never block or break the thread flow.
    let started = false;
    const start = (): void => {
      if (started || cancelled) return;
      started = true;
      requestResume(client, "preserve-live");
      client.connect();
    };
    void replayCache
      .load(args.threadId)
      .then((cached) => {
        if (cancelled || !cached || cached.events.length === 0) return;
        const replayed = replayEvents(cached.events, {
          threadId: args.threadId,
        }).conversation;
        for (const event of cached.events) {
          if (typeof event.eventId === "string") {
            markSeenEventId(seenEventIdsRef.current, event.eventId);
          }
        }
        resumeCursorRef.current = cached.cursor;
        resumeStreamIdRef.current = cached.streamId;
        const hydrated: Conversation = {
          ...replayed,
          resumeState: "resumed",
          // A trimmed cache holds only the recent window; older turns
          // page in through the snapshot path as usual.
          hasMoreTurns: cached.partialFrom > 1,
        };
        stateRef.current = hydrated;
        setState(() => hydrated);
      })
      .catch(() => {})
      .finally(start);
    // Note: do NOT setConnected(true) here. The previous optimistic
    // flag has been replaced — onOpen drives it now (see comment on
    // ``onOpen`` above).

    return () => {
      cancelled = true;
      online = false;
      stopTailPolling();
      // A turn is server-resident and survives its originating WebSocket.
      // Release this invisible route's connection immediately so a visible,
      // reconnected watcher can receive live events and approval requests.
      // Keeping the old owner alive intercepted approvals in an unmounted
      // React tree and could make a healthy long task time out waiting for a
      // prompt the user could never see.
      client.close();
      // Settle client-side incoming-request promises before clearing their
      // timers. The server connection is already closing, so this is local
      // resource cleanup rather than an authoritative approval decision.
      for (const resolvePending of Array.from(resolvers.values())) {
        resolvePending({ action: "decline", reason: "client disconnected" });
      }
      clientRef.current = null;
      resolvers.clear();
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
      setConnected(false);
    };
  }, [
    args.threadId,
    args.clientFactory,
    replayCache,
    applyEvent,
    persistTurnTelemetry,
  ]);

  const startTurn = useCallback<UseRealtimeThreadValue["startTurn"]>(
    async (input) => {
      const client = clientRef.current;
      if (!client) throw new Error("realtime client not ready");
      const watch = {
        delivered: false,
        ...(input.clientItemId ? { clientItemId: input.clientItemId } : {}),
      };
      turnDeliveryWatchesRef.current.add(watch);
      try {
        await client.request("turn/start", {
          threadId: args.threadId,
          input: [
            {
              type: "text",
              text: input.input,
              ...(input.attachments && input.attachments.length > 0
                ? { attachments: input.attachments }
                : {}),
              ...(input.metadata ? { metadata: input.metadata } : {}),
            },
          ],
          ...(input.clientItemId ? { userItemId: input.clientItemId } : {}),
          ...(input.cwd ? { cwd: input.cwd } : {}),
          approvalPolicy: input.approvalPolicy ?? "on-request",
          ...(input.sandboxPolicy
            ? { sandboxPolicy: input.sandboxPolicy }
            : {}),
          ...(input.planningMode ? { planningMode: input.planningMode } : {}),
          ...(input.effort ? { effort: input.effort } : {}),
          model: input.model,
          ...(input.topologyId ? { topologyId: input.topologyId } : {}),
        });
      } catch (err) {
        // The turn/start response only arrives once the whole turn has
        // finished, so a disconnect at any point of a long turn rejects
        // the pending request even though the message was delivered and
        // persisted server-side. If turn/started was observed after this
        // request went out, resolve normally: surfacing the rejection
        // would make callers flag a successful send as failed (error
        // banner + draft restore → duplicate sends). Turn state is
        // recovered by the reconnect/resume path.
        const persistedAfterStart = input.clientItemId
          ? stateRef.current.turns.some((turn) =>
              turn.items.some((item) => item.id === input.clientItemId),
            )
          : false;
        if (!watch.delivered && !persistedAfterStart) throw err;
      } finally {
        turnDeliveryWatchesRef.current.delete(watch);
      }
    },
    [args.threadId],
  );

  const steer = useCallback<UseRealtimeThreadValue["steer"]>(
    async ({ input, itemId }) => {
      const client = clientRef.current;
      if (!client) throw new Error("realtime client not ready");
      const turns = stateRef.current.turns;
      const active = turns.length > 0 ? turns[turns.length - 1] : null;
      if (!active || active.status !== "inProgress") {
        throw new Error("there is no active turn to steer");
      }
      const text = input.trim();
      if (!text) return;
      const generatedId =
        itemId ??
        `itm_steer_${
          typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : `${Date.now()}_${Math.random().toString(16).slice(2)}`
        }`;
      await client.request("turn/steer", {
        threadId: args.threadId,
        turnId: active.id,
        itemId: generatedId,
        text,
      });
    },
    [args.threadId],
  );

  const resolveApproval = useCallback<
    UseRealtimeThreadValue["resolveApproval"]
  >((requestId, accept) => {
    const resolver = approvalResolvers.current.get(requestId);
    if (!resolver) return;
    resolver({ action: accept ? "accept" : "decline" });
  }, []);

  const resume = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    const afterSequence = resumeCursorRef.current;
    const eventStreamId = resumeStreamIdRef.current;
    const result = await client.request<ResumeResponse>("thread/resume", {
      threadId: args.threadId,
      limit: RESUME_TURN_LIMIT,
      ...(afterSequence !== null ? { afterSequence } : {}),
      ...(afterSequence !== null && eventStreamId ? { eventStreamId } : {}),
    });
    if (
      typeof result.nextEventSequence === "number" &&
      Number.isFinite(result.nextEventSequence) &&
      result.nextEventSequence >= 0
    ) {
      resumeCursorRef.current = result.nextEventSequence;
    }
    if (typeof result.eventStreamId === "string") {
      resumeStreamIdRef.current = result.eventStreamId;
    }
    setState((prev) => {
      const turns =
        result.incremental === true
          ? mergeTurnSnapshots(prev.turns, result.turns ?? [])
          : (result.turns ?? []);
      const next: Conversation = {
        ...prev,
        turns,
        resumeState: "resumed",
        hasMoreTurns:
          result.incremental === true
            ? prev.hasMoreTurns
            : result.hasMore === true,
      };
      const resumedActive = [...turns]
        .reverse()
        .find((turn) => turn.status === "inProgress");
      seedVitalsFromResumedTurn(
        vitalsMarksRef.current,
        resumedActive ?? null,
        Date.now(),
      );
      stateRef.current = next;
      return next;
    });
  }, [args.threadId]);

  // Guards concurrent backwards-pagination; a ref (not state) because
  // double-invocation protection must be synchronous.
  const loadingOlderRef = useRef(false);

  const loadOlderTurns = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    if (loadingOlderRef.current) return;
    const current = stateRef.current;
    if (!current.hasMoreTurns) return;
    const oldest = current.turns[0];
    if (!oldest) return;
    loadingOlderRef.current = true;
    try {
      type ResumeResponse = {
        turns: Conversation["turns"];
        hasMore?: boolean;
      };
      const result = await client.request<ResumeResponse>("thread/resume", {
        threadId: args.threadId,
        limit: RESUME_TURN_LIMIT,
        beforeTurnId: oldest.id,
      });
      setState((prev) => {
        // Drop any overlap defensively (the cursor is exclusive, but a
        // concurrent full resume may have already prepended them).
        const known = new Set(prev.turns.map((t) => t.id));
        const older = (result.turns ?? []).filter((t) => !known.has(t.id));
        const next: Conversation = {
          ...prev,
          turns: [...older, ...prev.turns],
          hasMoreTurns: result.hasMore === true,
        };
        stateRef.current = next;
        return next;
      });
    } finally {
      loadingOlderRef.current = false;
    }
  }, [args.threadId]);

  const interrupt = useCallback<
    UseRealtimeThreadValue["interrupt"]
  >(async () => {
    const client = clientRef.current;
    if (!client) return;
    // Pull the active turn id off the latest state. If there is no
    // active turn we silently skip — clicking "stop" on a finished
    // conversation should not throw.
    const turns = stateRef.current.turns;
    const active = turns.length ? turns[turns.length - 1] : null;
    if (!active || active.status !== "inProgress") return;
    const result = await client.request<{ interrupted?: boolean }>(
      "turn/interrupt",
      {
        threadId: args.threadId,
        turnId: active.id,
      },
    );
    if (result.interrupted !== true) {
      // A false acknowledgement means this worker did not accept the stop.
      // Reconcile from durable server state, but never manufacture a terminal
      // event or telemetry receipt on the client.
      await resume();
      throw new Error("the active turn could not be interrupted");
    }

    // Approval requests belong to the acknowledged running turn. Settle their
    // local request promises now; the authoritative turn terminal still comes
    // from live fanout or the durable polling fold.
    for (const pending of stateRef.current.pendingApprovals) {
      if (pending.params.turnId !== active.id) continue;
      approvalResolvers.current.get(pending.requestId)?.({
        action: "decline",
        reason: "turn interrupted",
      });
    }
  }, [args.threadId, resume]);

  const compact = useCallback<UseRealtimeThreadValue["compact"]>(async () => {
    const client = clientRef.current;
    if (!client) throw new Error("realtime client not ready");
    const result = await client.request<{
      compacted: boolean;
      reason?: string;
      turnCount?: number;
      keepRecent?: number;
    }>("thread/compact", {
      threadId: args.threadId,
    });
    await resume();
    return result;
  }, [args.threadId, resume]);

  const decideHunk = useCallback<UseRealtimeThreadValue["decideHunk"]>(
    async ({ turnId, itemId, hunkId, path, decision, diff }) => {
      const client = clientRef.current;
      if (!client) throw new Error("realtime client not ready");
      await client.request("item/fileChange/hunkDecide", {
        threadId: args.threadId,
        turnId,
        itemId,
        hunkId,
        path,
        decision,
        diff,
      });
    },
    [args.threadId],
  );

  // React renders once with the previous hook state before the thread-change
  // effect clears it. Never expose that stale frame to the next task: it can
  // otherwise promote `/new` to a fake existing thread and leak old messages,
  // approvals, or activity into the fresh workspace.
  const visibleState = useMemo(
    () => visibleConversationForThread(state, args.threadId),
    [args.threadId, state],
  );

  // Derive the two state-dependent inputs the vitals classifier needs.
  const activeTurn = visibleState.turns[visibleState.turns.length - 1];
  const turnActive = activeTurn?.status === "inProgress";
  const hasRunningWork = useMemo(() => {
    if (!activeTurn || activeTurn.status !== "inProgress") return false;
    return activeTurn.items.some(
      (it) => it.status === "inProgress" && WORK_ITEM_TYPES.has(it.type),
    );
  }, [activeTurn]);

  const vitals = useStreamVitals({
    marksRef: vitalsMarksRef,
    connected,
    turnActive,
    hasRunningWork,
  });

  return useMemo(
    () => ({
      state: visibleState,
      connected,
      vitals,
      startTurn,
      steer,
      resolveApproval,
      resume,
      loadOlderTurns,
      interrupt,
      compact,
      decideHunk,
    }),
    [
      visibleState,
      connected,
      vitals,
      startTurn,
      steer,
      resolveApproval,
      resume,
      loadOlderTurns,
      interrupt,
      compact,
      decideHunk,
    ],
  );
}
