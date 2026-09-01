// Shared fetch-based SSE client.
//
// Why fetch + ReadableStream instead of EventSource: EventSource cannot
// set request headers, which forced the bearer token into the URL query
// string (``?token=``) where it lands in access logs, proxy logs and
// browser history. Fetch sends ``Authorization: Bearer`` like every
// other API call, and as a bonus gives us real HTTP status codes,
// explicit reconnect control and Last-Event-ID resume — things
// EventSource either hides or does opaquely.
//
// All SSE consumers in the app (observability, quest, run-review,
// background task output, parallel-agent batches) share this one
// implementation so reconnect/backoff/parsing behavior stays uniform.

import { getToken } from "@/core/auth/api";
import { swallow } from "@/core/utils/log";
import { nextBackoffDelay } from "./backoff";

export interface SseEventMessage {
  // Value of the ``event:`` field; "message" when the block omitted it
  // (matches the EventSource default event type).
  event: string;
  // ``data:`` lines joined with "\n", per spec.
  data: string;
  // Last ``id:`` seen before this dispatch, null when never set.
  id: string | null;
}

export type SseFetchImpl = (
  url: string,
  init: RequestInit,
) => Promise<Response>;

export interface OpenSseStreamOptions {
  // Static URL or a builder invoked per (re)connect — use the builder
  // form when the resume cursor lives in the query string.
  url: string | (() => string);
  // Defaults to a bearer-authenticated fetch with cookies. Tests and
  // exotic endpoints can inject their own.
  fetchImpl?: SseFetchImpl;
  // Returning true marks the stream terminal (e.g. a ``done`` event):
  // no reconnect, no error callback.
  onEvent: (message: SseEventMessage) => void | boolean;
  onOpen?: () => void;
  // Fired before each retry, one-based attempt number.
  onReconnecting?: (attempt: number) => void;
  // Fired only when retries are exhausted — transient failures surface
  // through ``onReconnecting`` instead.
  onError?: (err: Error) => void;
  // EventSource reconnects forever; that remains the default here.
  // Pass a finite number for bounded streams.
  maxRetries?: number;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  // External cancellation, in addition to the returned cleanup.
  signal?: AbortSignal;
  // Resume cursor sent as the ``Last-Event-ID`` request header on every
  // (re)connect — mirrors what EventSource does automatically.
  lastEventId?: () => string | null;
}

// Default transport: same-origin or absolute backend URL, cookies for
// session auth, Bearer header for token auth — mirrors the authedFetch
// pattern used across the app's API modules.
const defaultFetchImpl: SseFetchImpl = (url, init) => {
  const token = getToken();
  return fetch(url, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.headers as Record<string, string> | undefined),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
};

/**
 * Open an SSE stream with automatic reconnect. Returns a cleanup
 * function that aborts the connection and any pending retry timer.
 */
export function openSseStream(options: OpenSseStreamOptions): () => void {
  const maxRetries = options.maxRetries ?? Number.POSITIVE_INFINITY;
  const fetchImpl = options.fetchImpl ?? defaultFetchImpl;
  let aborted = false;
  let attempt = 0;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let lastError: Error | null = null;

  const resolveUrl = (): string =>
    typeof options.url === "function" ? options.url() : options.url;

  const cleanup = (): void => {
    aborted = true;
    if (retryTimer != null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    controller?.abort();
    controller = null;
  };

  const onExternalAbort = (): void => cleanup();
  if (options.signal) {
    if (options.signal.aborted) return cleanup;
    options.signal.addEventListener("abort", onExternalAbort, {
      once: true,
    });
  }

  const scheduleRetry = (): boolean => {
    if (aborted) return false;
    if (attempt >= maxRetries) {
      options.onError?.(lastError ?? new Error("SSE retries exhausted"));
      return false;
    }
    attempt += 1;
    options.onReconnecting?.(attempt);
    const wait = nextBackoffDelay(attempt - 1, {
      initialMs: options.initialBackoffMs,
      maxMs: options.maxBackoffMs,
    });
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void connect();
    }, wait);
    return true;
  };

  const connect = async (): Promise<void> => {
    if (aborted) return;
    controller = new AbortController();
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    const resumeId = options.lastEventId?.();
    if (resumeId) headers["Last-Event-ID"] = resumeId;
    try {
      const res = await fetchImpl(resolveUrl(), {
        signal: controller.signal,
        headers,
      });
      // Authentication/authorization failures are terminal for the current
      // page state. Retrying them forever created a background request storm
      // after logout and when a signed-in user lacked an optional capability.
      // A new authenticated mount will create a fresh subscription.
      if (res.status === 401 || res.status === 403) {
        lastError = new Error(`SSE HTTP ${res.status}`);
        aborted = true;
        options.onError?.(lastError);
        return;
      }
      if (!res.ok || !res.body) {
        throw new Error(`SSE HTTP ${res.status}`);
      }
      // A successful (re)connect resets the retry budget.
      attempt = 0;
      options.onOpen?.();
      const terminal = await readSseStream(res.body, options.onEvent);
      if (terminal || aborted) return;
      // Clean EOF without a terminal event: the server hung up. Treat
      // it as a retryable failure — EventSource does the same.
      lastError = new Error("SSE stream ended unexpectedly");
      scheduleRetry();
    } catch (err) {
      if (aborted) return;
      const isAbort = err instanceof DOMException && err.name === "AbortError";
      if (isAbort) return;
      lastError = err instanceof Error ? err : new Error(String(err));
      swallow(lastError);
      scheduleRetry();
    }
  };

  void connect();
  return cleanup;
}

/**
 * Incremental SSE frame parser. Handles multi-line ``data:``, named
 * ``event:``, ``id:`` bookkeeping, comment lines and CRLF. A pending
 * event block is also dispatched at EOF: servers commonly close right
 * after their final frame without a trailing blank line, and dropping
 * that frame loses terminal events (``done``/``batch_complete``).
 *
 * Resolves true when ``onEvent`` reported a terminal event.
 */
async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (message: SseEventMessage) => void | boolean,
): Promise<boolean> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "";
  let dataLines: string[] = [];
  let lastId: string | null = null;

  const dispatch = (): boolean => {
    if (dataLines.length === 0) {
      // Blocks without data are ignored per spec (e.g. ``event:``-only
      // heartbeats) — but the id still advances.
      eventType = "";
      return false;
    }
    const terminal =
      onEvent({
        event: eventType || "message",
        data: dataLines.join("\n"),
        id: lastId,
      }) === true;
    eventType = "";
    dataLines = [];
    return terminal;
  };

  const processLine = (rawLine: string): boolean => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line.startsWith(":")) return false;
    if (line === "") return dispatch();
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      eventType = value;
    } else if (field === "data") {
      dataLines.push(value);
    } else if (field === "id") {
      lastId = value;
    }
    return false;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        for (const line of buffer.split("\n")) {
          if (processLine(line)) return true;
        }
        buffer = "";
        if (eventType || dataLines.length > 0) {
          return dispatch();
        }
        return false;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (processLine(line)) return true;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
