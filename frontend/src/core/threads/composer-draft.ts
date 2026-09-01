/**
 * Per-thread composer draft persistence.
 *
 * The composer keeps its draft in component state, which meant switching
 * threads or reloading the page silently discarded half-typed messages.
 * Drafts are mirrored to localStorage keyed by thread id (a dedicated key
 * for the not-yet-created "new thread" composer). Writes are debounced by
 * the caller; storage failures (quota, private mode) are swallowed — a
 * lost draft is annoying, a thrown exception mid-typing is worse.
 *
 * Values are stored as a small JSON envelope carrying a write timestamp.
 * Thread ids can be ephemeral (e.g. the agent-creation page generates a
 * fresh uuid per visit), so stale entries are pruned after 30 days to
 * keep the key space from growing unboundedly.
 */

const DRAFT_KEY_PREFIX = "echo:composer-draft:";
export const NEW_THREAD_DRAFT_KEY = "__new__";
const DRAFT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

interface DraftEnvelope {
  v: 1;
  text: string;
  savedAt: number;
}

function storageKey(threadId: string | undefined | null): string {
  return DRAFT_KEY_PREFIX + (threadId?.trim() ? threadId : NEW_THREAD_DRAFT_KEY);
}

function readDraft(raw: string | null): string | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<DraftEnvelope>;
    if (parsed?.v === 1 && typeof parsed.text === "string") {
      return parsed.text.length > 0 ? parsed.text : null;
    }
  } catch {
    // Legacy plain-text draft persisted before the envelope format.
  }
  return raw.length > 0 ? raw : null;
}

function pruneExpiredDrafts(now: number): void {
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i);
    if (!key || !key.startsWith(DRAFT_KEY_PREFIX)) continue;
    const raw = window.localStorage.getItem(key);
    if (!raw) continue;
    try {
      const parsed = JSON.parse(raw) as Partial<DraftEnvelope>;
      if (
        parsed?.v === 1 &&
        typeof parsed.savedAt === "number" &&
        now - parsed.savedAt > DRAFT_MAX_AGE_MS
      ) {
        window.localStorage.removeItem(key);
      }
    } catch {
      // Legacy plain text has no timestamp; leave it for its owner.
    }
  }
}

export function loadComposerDraft(
  threadId: string | undefined | null,
): string | null {
  try {
    return readDraft(window.localStorage.getItem(storageKey(threadId)));
  } catch {
    return null;
  }
}

export function saveComposerDraft(
  threadId: string | undefined | null,
  draft: string,
): void {
  try {
    if (draft) {
      const now = Date.now();
      const envelope: DraftEnvelope = { v: 1, text: draft, savedAt: now };
      window.localStorage.setItem(storageKey(threadId), JSON.stringify(envelope));
      pruneExpiredDrafts(now);
    } else {
      window.localStorage.removeItem(storageKey(threadId));
    }
  } catch {
    // Storage unavailable (private mode / quota): drafts stay in-memory.
  }
}
