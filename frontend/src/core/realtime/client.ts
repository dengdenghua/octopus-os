// Realtime WebSocket client.
//
// One client per thread (or per conversation). Owns:
//
//   * The WebSocket lifecycle — connect, exponential reconnect, close.
//   * JSON-RPC id minting + pending request tracker.
//   * Approval handler — server-initiated requests are routed to a
//     caller-supplied callback that returns the decision; the client
//     replies on the same channel.
//   * Notification dispatch — pushed events go through the supplied
//     reducer; observers subscribe to state snapshots.
//
// Designed to live behind a React hook (see ``useRealtimeThread``)
// that exposes ``state``, ``send``, ``startTurn``, ``resolveApproval``.

import { nextBackoffDelay } from "@/core/streaming/backoff";
import { swallow } from "@/core/utils/log";
import { webSocketAuthProtocols } from "@/core/auth/websocket";
import {
  type Envelope,
  type JsonRpcError,
  type JsonRpcId,
  type JsonRpcRequest,
  type JsonRpcResponse,
  type Notification,
  isNotification,
  isRequest,
  isResponse,
} from "./envelope";

export interface RealtimeClientOptions {
  url: string;
  onIncomingRequest: (request: JsonRpcRequest) => Promise<unknown>;
  onNotification: (note: Notification) => void;
  onOpen?: () => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (err: Event | Error) => void;
  // Reconnect schedule. Each retry waits the indexed delay (ms) until
  // ``maxBackoffMs`` is reached, then plateaus there.
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  // Bearer token resolver. Called per-connect so a refreshed token is
  // picked up after reconnect without restarting the page.
  authToken?: () => string | null;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: JsonRpcError | Error) => void;
}

const DEFAULT_INITIAL_BACKOFF = 500;
const DEFAULT_MAX_BACKOFF = 15_000;

// Delta notification methods that participate in delta-coalescing.
// Used by the coalesce step (which merges N consecutive deltas with the
// same itemId into one). Snapshot-style notifications (item/started,
// item/completed) are batched too — see BATCHED_METHODS — but never
// merged.
const DELTA_METHODS = new Set([
  "item/agentMessage/delta",
  "item/reasoning/textDelta",
  "item/plan/delta",
  "item/commandExecution/outputDelta",
]);

// Application-level heartbeat interval. Keeps proxies and firewalls from
// silently closing idle WebSocket connections during long tool executions.
const HEARTBEAT_INTERVAL_MS = 25_000;
// If we don't see a pong for this long, the connection is considered
// wedged (silent TCP half-open, proxy black-hole, etc) and we force-close
// it so the reconnect path runs. Two missed pings + slack.
const PONG_TIMEOUT_MS = 70_000;

// Methods coalesced/batched on the next animation frame to keep React
// renders down. Includes both delta methods AND the lifecycle envelopes
// (item/started, item/completed) so the dispatch order matches the
// websocket arrival order even though item/* land synchronously and
// deltas hop through requestAnimationFrame. Without buffering started/
// completed too, a frame boundary between ``started`` and the deltas
// reorders them: completed lands first, the reducer's status guard
// then drops the deltas as "stale" and the user sees an empty bubble.
// ``item/fileChange/hunkDelta`` rides the buffer for the same ordering
// reason (the reducer drops hunk deltas for items it hasn't seen), but
// stays out of DELTA_METHODS: hunks are structured payloads, not
// appendable text, so they must never coalesce.
const BATCHED_METHODS = new Set([
  "item/started",
  "item/completed",
  "item/agentMessage/delta",
  "item/reasoning/textDelta",
  "item/plan/delta",
  "item/commandExecution/outputDelta",
  "item/fileChange/hunkDelta",
]);

export class RealtimeClient {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<JsonRpcId, PendingRequest>();
  private opts: RealtimeClientOptions;
  private closed = false;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private outbox: string[] = [];
  // Backoff bounds, captured once so changing the option after construct
  // doesn't behave inconsistently mid-flight.
  private readonly initialBackoff: number;
  private readonly maxBackoff: number;
  // Delta batching: buffer high-frequency delta notifications and flush
  // them together in a single animation frame to reduce React re-renders.
  private deltaBuffer: Notification[] = [];
  private deltaFlushPending = false;
  // Heartbeat timer — cleared on close and reconnect.
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  // Timestamp of the last pong (or open). Used by the heartbeat tick to
  // decide whether the connection should be considered dead.
  private lastPongAt = 0;

  constructor(opts: RealtimeClientOptions) {
    this.opts = opts;
    this.initialBackoff = opts.initialBackoffMs ?? DEFAULT_INITIAL_BACKOFF;
    this.maxBackoff = opts.maxBackoffMs ?? DEFAULT_MAX_BACKOFF;
    // Flush the moment the tab goes hidden: a requestAnimationFrame
    // scheduled while visible never fires in a background tab, so
    // anything still buffered would sit there (and grow) until the tab
    // is foregrounded — meanwhile non-batched methods (turn/interrupted,
    // turn/completed, error) keep dispatching immediately and would
    // overtake the buffered item events. ``typeof document`` guard keeps
    // this SSR-safe.
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.onVisibilityChange);
    }
  }

  private onVisibilityChange = (): void => {
    if (document.hidden) {
      this.flushDeltaBuffer();
    }
  };

  // ── Lifecycle ──────────────────────────────────────────────

  connect(): void {
    if (this.closed) return;
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    const { url, protocols } = this.buildConnection();
    let ws: WebSocket;
    try {
      ws = new WebSocket(url, protocols);
    } catch (err) {
      swallow(err);
      this.opts.onError?.(err as Error);
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.lastPongAt = Date.now();
      this.flushOutbox();
      this.startHeartbeat();
      this.opts.onOpen?.();
    };
    ws.onmessage = (ev) => {
      this.dispatch(typeof ev.data === "string" ? ev.data : "");
    };
    ws.onerror = (ev) => {
      this.opts.onError?.(ev);
    };
    ws.onclose = (ev) => {
      this.stopHeartbeat();
      this.flushDeltaBuffer();
      this.opts.onClose?.(ev.code, ev.reason);
      this.failPending(
        new Error(`websocket closed (${ev.code} ${ev.reason || "no reason"})`),
      );
      // Clear the outbox on disconnect. Anything that was buffered
      // for "send on next open" was a request whose Promise has now
      // been rejected by failPending; replaying those frames after a
      // reconnect would re-send turn/start (or an interrupt, etc.)
      // with no caller listening for the response — duplicate work
      // on the server, ghost UI on the client. Leave the buffer
      // empty so callers re-issue requests explicitly when they want
      // them.
      this.outbox.length = 0;
      this.ws = null;
      if (!this.closed) {
        this.scheduleReconnect();
      }
    };
  }

  close(): void {
    this.closed = true;
    this.stopHeartbeat();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.onVisibilityChange);
    }
    this.flushDeltaBuffer();
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.failPending(new Error("client closed"));
  }

  // ── Send paths ─────────────────────────────────────────────

  request<R = unknown>(
    method: string,
    params: Record<string, unknown> = {},
  ): Promise<R> {
    if (this.closed) {
      return Promise.reject(new Error("client closed"));
    }
    const id = this.nextId++;
    const envelope: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
    return new Promise<R>((resolve, reject) => {
      this.pending.set(id, {
        resolve: (v: unknown) => resolve(v as R),
        reject,
      });
      this.send(envelope);
    });
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    this.send({ jsonrpc: "2.0", method, params });
  }

  // Reply to a server-initiated request. Bypasses the pending tracker —
  // we are the *responder*, not the requester.
  reply(id: JsonRpcId, result: unknown, error?: JsonRpcError): void {
    const env: JsonRpcResponse = error
      ? { jsonrpc: "2.0", id, error }
      : { jsonrpc: "2.0", id, result };
    this.send(env);
  }

  // ── Internal ───────────────────────────────────────────────

  // Credentials ride the ``Sec-WebSocket-Protocol`` handshake header
  // instead of a ``?token=`` query param. Query strings end up in access
  // logs, proxy logs and browser history. Base64url makes every UTF-8 token
  // legal inside the subprotocol header, so unusual credentials never need
  // to fall back to the URL. The gateway decodes the second protocol and
  // echoes only the non-secret ``bearer.b64`` marker.
  private buildConnection(): { url: string; protocols?: string[] } {
    const token = this.opts.authToken?.() ?? null;
    return { url: this.opts.url, protocols: webSocketAuthProtocols(token) };
  }

  private send(env: Envelope): void {
    // A deliberately closed client never reconnects. Do not retain late
    // approval replies/notifications in an outbox that can never flush.
    if (this.closed) return;
    const text = JSON.stringify(env);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(text);
      return;
    }
    // Buffer; flushed on next ``onopen``. Bound the buffer so a
    // perpetually-disconnected client doesn't grow without limit.
    if (this.outbox.length >= 256) {
      this.outbox.shift();
    }
    this.outbox.push(text);
  }

  private flushOutbox(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const pending = this.outbox.splice(0, this.outbox.length);
    for (const text of pending) {
      this.ws.send(text);
    }
  }

  private dispatch(text: string): void {
    if (!text) return;
    let env: Envelope;
    try {
      env = JSON.parse(text) as Envelope;
    } catch (err) {
      swallow(err);
      this.opts.onError?.(err as Error);
      return;
    }
    if (isResponse(env)) {
      const handler = this.pending.get(env.id);
      if (!handler) return;
      this.pending.delete(env.id);
      if (env.error) {
        handler.reject(env.error);
      } else {
        handler.resolve(env.result);
      }
      return;
    }
    if (isRequest(env)) {
      // Server-initiated. Route to the caller and reply with the
      // decision. Errors are translated to a JSON-RPC error response so
      // the server's awaiting future doesn't hang.
      this.opts
        .onIncomingRequest(env)
        .then((result) => this.reply(env.id, result))
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : String(err);
          this.reply(env.id, null, { code: -32603, message });
        });
      return;
    }
    if (isNotification(env)) {
      // Server-side liveness reply. Updating the watermark must happen
      // *before* any further routing — pong should never reach the
      // application reducer.
      if (env.method === "pong") {
        this.lastPongAt = Date.now();
        return;
      }
      if (BATCHED_METHODS.has(env.method)) {
        this.deltaBuffer.push(env);
        this.scheduleFlush();
      } else {
        // Ordering invariant: a non-batched notification must never
        // overtake events still sitting in the delta buffer. Within the
        // buffering window (a frame in the foreground, a macrotask in a
        // hidden tab) turn/completed, turn/interrupted, workbench
        // snapshots or errors would otherwise reach the reducer before
        // the buffered item/started they logically follow — e.g. an
        // interrupt lands first, the reducer marks the turn done, then
        // the late-flushed item spins forever. Flushing synchronously
        // here keeps every cross-method sequence in WebSocket arrival
        // order. Non-batched methods are low-frequency (a handful per
        // turn), so this doesn't erode the coalescing win; the already
        // scheduled rAF/setTimeout callback sees an empty buffer and
        // returns without re-dispatching.
        this.flushDeltaBuffer();
        this.opts.onNotification(env);
      }
    }
  }

  // Foreground: coalesce on the next animation frame (caps React
  // renders at the display rate during high-frequency streaming).
  // Hidden tab: rAF callbacks are suspended, so fall back to a
  // macrotask — otherwise the buffer grows unbounded and immediately-
  // dispatched methods reorder past the buffered item events.
  private scheduleFlush(): void {
    if (this.deltaFlushPending) return;
    this.deltaFlushPending = true;
    if (typeof document !== "undefined" && document.hidden) {
      setTimeout(() => this.flushDeltaBuffer(), 0);
    } else {
      requestAnimationFrame(() => this.flushDeltaBuffer());
    }
  }

  private flushDeltaBuffer(): void {
    this.deltaFlushPending = false;
    if (this.deltaBuffer.length === 0) return;
    const batch = coalesceDeltaNotifications(this.deltaBuffer.splice(0));
    for (const note of batch) {
      // A single bad notification (reducer bug, listener throw) used
      // to abort the whole RAF batch and silently drop subsequent
      // deltas — including the agentMessage chunks the user is
      // staring at. Isolate each handler call so one bad apple
      // doesn't poison the rest of the frame.
      try {
        this.opts.onNotification(note);
      } catch (err) {
        swallow(err);
        this.opts.onError?.(err as Error);
      }
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      // Detect a wedged connection: server is alive enough for the
      // socket to stay open but stopped responding (proxy half-open,
      // load-balancer ghost session). Force-close so onclose triggers
      // the reconnect path.
      if (
        this.lastPongAt > 0 &&
        Date.now() - this.lastPongAt > PONG_TIMEOUT_MS
      ) {
        this.ws?.close(4000, "pong timeout");
        return;
      }
      this.notify("ping", {});
    }, HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer != null) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.closed) return;
    if (this.reconnectTimer != null) return;
    const wait = nextBackoffDelay(this.reconnectAttempts, {
      initialMs: this.initialBackoff,
      maxMs: this.maxBackoff,
    });
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, wait);
  }

  private failPending(reason: Error): void {
    if (this.pending.size === 0) return;
    for (const handler of this.pending.values()) {
      handler.reject(reason);
    }
    this.pending.clear();
  }
}

// Convenience builder: returns a wired client with sensible defaults
// for an echo-agent backend.
export function createDefaultClient(args: {
  baseURL: string;
  threadId?: string;
  authToken?: () => string | null;
  onIncomingRequest: RealtimeClientOptions["onIncomingRequest"];
  onNotification: RealtimeClientOptions["onNotification"];
  onOpen?: RealtimeClientOptions["onOpen"];
  onClose?: RealtimeClientOptions["onClose"];
  onError?: RealtimeClientOptions["onError"];
}): RealtimeClient {
  const wsUrl = toWebSocketURL(args.baseURL, "/api/realtime");
  return new RealtimeClient({
    url: wsUrl,
    authToken: args.authToken,
    onIncomingRequest: args.onIncomingRequest,
    onNotification: args.onNotification,
    onOpen: args.onOpen,
    onClose: args.onClose,
    onError: args.onError,
  });
}

export function toWebSocketURL(httpBase: string, path: string): string {
  const trimmed = (httpBase || "").replace(/\/+$/, "");
  if (!trimmed) {
    // Same-origin (vite dev proxy routes /api/* through to backend).
    if (typeof window !== "undefined") {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${window.location.host}${path}`;
    }
    return path;
  }
  if (trimmed.startsWith("https://")) return `wss://${trimmed.slice(8)}${path}`;
  if (trimmed.startsWith("http://")) return `ws://${trimmed.slice(7)}${path}`;
  return `${trimmed}${path}`;
}

function coalesceDeltaNotifications(batch: Notification[]): Notification[] {
  const merged: Notification[] = [];
  // Deltas of an open merge run, joined ONCE when the run closes. The
  // naive ``merged.delta += next.delta`` recopies the growing prefix on
  // every merge — quadratic in a busy frame where dozens of deltas for
  // the same item land between animation frames.
  let runParts: string[] = [];
  const closeRun = (): void => {
    // A lone delta keeps its original envelope — nothing was merged.
    if (runParts.length <= 1) {
      runParts = [];
      return;
    }
    const seed = merged[merged.length - 1];
    if (seed) {
      merged[merged.length - 1] = {
        ...seed,
        params: {
          ...(seed.params as Record<string, unknown>),
          delta: runParts.join(""),
        },
      };
    }
    runParts = [];
  };
  for (const note of batch) {
    const last = merged[merged.length - 1];
    if (last && runParts.length > 0 && canMergeDeltaNotifications(last, note)) {
      runParts.push(
        String((note.params as Record<string, unknown>).delta ?? ""),
      );
      continue;
    }
    closeRun();
    merged.push(note);
    if (DELTA_METHODS.has(note.method)) {
      runParts = [String((note.params as Record<string, unknown>).delta ?? "")];
    }
  }
  closeRun();
  return merged;
}

function canMergeDeltaNotifications(
  left: Notification,
  right: Notification,
): boolean {
  if (left.method !== right.method) return false;
  // Only delta-style notifications carry appendable text. ``item/started``
  // / ``item/completed`` snapshots must never merge — they replace,
  // not append, so coalescing two of them would silently drop one.
  if (!DELTA_METHODS.has(left.method)) return false;
  const leftParams = left.params as Record<string, unknown>;
  const rightParams = right.params as Record<string, unknown>;
  if (left.method === "item/reasoning/textDelta") {
    return (
      leftParams.threadId === rightParams.threadId &&
      leftParams.turnId === rightParams.turnId &&
      leftParams.itemId === rightParams.itemId &&
      leftParams.contentIndex === rightParams.contentIndex
    );
  }
  return (
    leftParams.threadId === rightParams.threadId &&
    leftParams.turnId === rightParams.turnId &&
    leftParams.itemId === rightParams.itemId
  );
}
