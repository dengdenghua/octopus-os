/**
 * Auto-new-session hand-off.
 *
 * When the user sends a message into a thread that has been idle longer than
 * the configured threshold, we don't just keep appending to the stale thread —
 * we open a *fresh* thread and carry the message over. The fresh thread is
 * opened via the normal "new task" route (which seeds the composer with the
 * prompt), and the actual auto-send is driven by a transient hand-off stored
 * here in `sessionStorage`.
 *
 * Why `sessionStorage` and not a URL param: a URL param would survive a page
 * refresh and re-trigger the send (duplicate message). `sessionStorage` is
 * read-and-cleared on consume, so a refresh can never re-send. It is also
 * scoped to the tab, which matches the "this specific send" semantics.
 */

const PENDING_NEW_SESSION_KEY = "echo:pending-new-session";

/** Max age of a pending hand-off. Leftovers from a much earlier navigation
 * (e.g. a backgrounded tab) must not suddenly fire into a brand-new thread. */
const PENDING_MAX_AGE_MS = 60_000;

type PendingNewSession = {
  text: string;
  ts: number;
};

export function writePendingNewSession(text: string): void {
  if (typeof window === "undefined") return;
  try {
    const payload: PendingNewSession = { text, ts: Date.now() };
    window.sessionStorage.setItem(
      PENDING_NEW_SESSION_KEY,
      JSON.stringify(payload),
    );
  } catch {
    // Storage full / disabled — auto-new-session is best-effort; the message
    // still lands in the current (stale) thread if this fails.
  }
}

/**
 * Read and immediately clear a pending hand-off. Returns the text to auto-send,
 * or `null` when there is nothing valid to send (missing, malformed, or too
 * old). Clearing-on-read is what makes the hand-off refresh-safe.
 */
export function consumePendingNewSession(): string | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(PENDING_NEW_SESSION_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    window.sessionStorage.removeItem(PENDING_NEW_SESSION_KEY);
    const parsed = JSON.parse(raw) as Partial<PendingNewSession>;
    if (
      typeof parsed.text === "string" &&
      parsed.text.trim().length > 0 &&
      typeof parsed.ts === "number" &&
      Date.now() - parsed.ts <= PENDING_MAX_AGE_MS
    ) {
      return parsed.text;
    }
  } catch {
    // Malformed payload — already removed above, just don't send.
  }
  return null;
}

/**
 * Whether a thread whose last activity was at `updatedAt` is stale relative to
 * the configured idle threshold (hours). `0`/negative hours means "never stale"
 * (feature disabled). Missing/garbage timestamps are treated as not stale so we
 * never surprise the user by silently opening a new session.
 */
export function isThreadStale(
  updatedAt: string | undefined | null,
  hours: number,
): boolean {
  if (!hours || hours <= 0 || !updatedAt) return false;
  const ts = new Date(updatedAt).getTime();
  if (Number.isNaN(ts)) return false;
  return Date.now() - ts > hours * 3_600_000;
}
