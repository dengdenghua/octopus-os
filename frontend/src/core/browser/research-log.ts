import { currentActorId } from "@/core/auth/api";
import { actorScopedStorageKey } from "@/core/auth/scoped-storage";

export interface BrowserResearchLogEntry {
  id: string;
  createdAt: number;
  platform: string;
  title: string;
  note: string;
  url?: string;
}

const RESEARCH_LOG_KEY = "echo:browser-research-log";

export function browserResearchLogStorageKey(actor = currentActorId()): string {
  return actorScopedStorageKey(RESEARCH_LOG_KEY, actor);
}

function isResearchLogEntry(value: unknown): value is BrowserResearchLogEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<BrowserResearchLogEntry>;
  return (
    typeof entry.id === "string" &&
    typeof entry.createdAt === "number" &&
    Number.isFinite(entry.createdAt) &&
    typeof entry.platform === "string" &&
    typeof entry.title === "string" &&
    typeof entry.note === "string" &&
    (entry.url === undefined || typeof entry.url === "string")
  );
}

export function readBrowserResearchLog(
  actor = currentActorId(),
): BrowserResearchLogEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(
      browserResearchLogStorageKey(actor),
    );
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter(isResearchLogEntry).slice(0, 80)
      : [];
  } catch {
    return [];
  }
}

export function writeBrowserResearchLog(
  entries: readonly BrowserResearchLogEntry[],
  actor = currentActorId(),
): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      browserResearchLogStorageKey(actor),
      JSON.stringify(entries.slice(0, 80)),
    );
  } catch {
    // Session storage is a convenience cache; a blocked quota must not stop browsing.
  }
}
