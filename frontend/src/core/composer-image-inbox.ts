export interface QueuedComposerImageEntry {
  id: string;
  threadId?: string | null;
  dataUrl: string;
  filename: string;
  sourceLabel?: string | null;
}

const COMPOSER_IMAGE_QUEUE_KEY = "echo:composer-image-queue";
const LAST_COMPOSER_TARGET_KEY = "echo:last-composer-target";

function readQueue(): QueuedComposerImageEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(COMPOSER_IMAGE_QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is QueuedComposerImageEntry =>
        item &&
        typeof item === "object" &&
        typeof item.id === "string" &&
        typeof item.dataUrl === "string" &&
        typeof item.filename === "string",
    );
  } catch {
    return [];
  }
}

function writeQueue(entries: QueuedComposerImageEntry[]): void {
  if (typeof window === "undefined") return;
  try {
    if (entries.length === 0) {
      window.sessionStorage.removeItem(COMPOSER_IMAGE_QUEUE_KEY);
      return;
    }
    window.sessionStorage.setItem(
      COMPOSER_IMAGE_QUEUE_KEY,
      JSON.stringify(entries),
    );
  } catch {
    // Best-effort only.
  }
}

export function queueComposerImageEntry(
  entry: Omit<QueuedComposerImageEntry, "id">,
): void {
  const queued = readQueue();
  queued.push({
    ...entry,
    id: `img_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
  });
  writeQueue(queued);
}

export function consumeComposerImageEntries(
  threadId?: string | null,
): QueuedComposerImageEntry[] {
  const queued = readQueue();
  if (queued.length === 0) return [];
  const consumed: QueuedComposerImageEntry[] = [];
  const remaining: QueuedComposerImageEntry[] = [];
  for (const entry of queued) {
    if (entry.threadId && threadId && entry.threadId !== threadId) {
      remaining.push(entry);
      continue;
    }
    if (entry.threadId && !threadId) {
      remaining.push(entry);
      continue;
    }
    consumed.push(entry);
  }
  writeQueue(remaining);
  return consumed;
}

export function rememberLastComposerTarget(path: string): void {
  if (typeof window === "undefined") return;
  try {
    if (!path.trim()) return;
    window.sessionStorage.setItem(LAST_COMPOSER_TARGET_KEY, path);
  } catch {
    // Best-effort only.
  }
}

export function readLastComposerTarget(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.sessionStorage.getItem(LAST_COMPOSER_TARGET_KEY);
    return typeof value === "string" && value.trim() ? value : null;
  } catch {
    return null;
  }
}
