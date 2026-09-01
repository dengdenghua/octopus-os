// Shared reconnect backoff for all streaming transports (WebSocket
// realtime client, fetch-based SSE). Extracted so every long-lived
// connection in the app retries on the same schedule instead of each
// transport growing its own flavor.

export interface BackoffOptions {
  // Delay base for the first retry (ms). Doubles each attempt.
  initialMs?: number;
  // Ceiling for the exponential base (ms).
  maxMs?: number;
  // Exponent cap — attempt index is clamped here so 2**idx can't
  // overflow into absurd bases on connections that stay down for days.
  maxAttemptCap?: number;
}

export const DEFAULT_INITIAL_BACKOFF_MS = 500;
export const DEFAULT_MAX_BACKOFF_MS = 15_000;
export const DEFAULT_BACKOFF_ATTEMPT_CAP = 12;

/**
 * Full-jitter exponential backoff: the base grows as
 * ``min(initial * 2**attempt, max)`` and the actual wait is uniform in
 * ``[0, base]``. Jitter keeps a thundering herd of clients from
 * reconnecting in lockstep after a server bounce.
 *
 * ``attempt`` is zero-based: the first retry passes 0.
 */
export function nextBackoffDelay(
  attempt: number,
  options: BackoffOptions = {},
): number {
  const initial = options.initialMs ?? DEFAULT_INITIAL_BACKOFF_MS;
  const max = options.maxMs ?? DEFAULT_MAX_BACKOFF_MS;
  const cap = options.maxAttemptCap ?? DEFAULT_BACKOFF_ATTEMPT_CAP;
  const idx = Math.min(Math.max(attempt, 0), cap);
  const base = Math.min(initial * 2 ** idx, max);
  return Math.floor(Math.random() * base);
}
