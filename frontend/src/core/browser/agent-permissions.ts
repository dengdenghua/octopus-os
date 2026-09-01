import { swallow } from "@/core/utils/log";

export type BrowserAgentPermission = "ask" | "allow" | "block";

export interface BrowserAgentSitePermission {
  origin: string;
  permission: Exclude<BrowserAgentPermission, "ask">;
  updatedAt: number;
}

export interface BrowserAgentAuditEntry {
  id: string;
  origin: string;
  action: string;
  outcome: "allowed" | "blocked" | "confirmed" | "failed";
  createdAt: number;
  detail?: string;
}

const PERMISSIONS_KEY = "echo:browser-agent-permissions.v1";
const AUDIT_KEY = "echo:browser-agent-audit.v1";
export const BROWSER_AGENT_POLICY_EVENT = "echo:browser-agent-policy-change";
const MAX_AUDIT_ENTRIES = 200;

export function browserHttpOrigin(
  url: string | null | undefined,
): string | null {
  try {
    const parsed = new URL(url || "");
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.origin
      : null;
  } catch {
    return null;
  }
}

function readArray<T>(key: string): T[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (error) {
    swallow(error, "browser-agent-policy");
    return [];
  }
}

function writeArray<T>(key: string, value: T[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    window.dispatchEvent(new CustomEvent(BROWSER_AGENT_POLICY_EVENT));
  } catch (error) {
    swallow(error, "browser-agent-policy");
  }
}

export function listBrowserAgentPermissions(): BrowserAgentSitePermission[] {
  return readArray<BrowserAgentSitePermission>(PERMISSIONS_KEY)
    .filter(
      (entry) =>
        browserHttpOrigin(entry?.origin) === entry.origin &&
        (entry.permission === "allow" || entry.permission === "block") &&
        typeof entry.updatedAt === "number",
    )
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getBrowserAgentPermission(
  url: string | null | undefined,
): BrowserAgentPermission {
  const origin = browserHttpOrigin(url);
  if (!origin) return "block";
  return (
    listBrowserAgentPermissions().find((entry) => entry.origin === origin)
      ?.permission ?? "ask"
  );
}

export function setBrowserAgentPermission(
  url: string,
  permission: BrowserAgentPermission,
): void {
  const origin = browserHttpOrigin(url);
  if (!origin) return;
  const other = listBrowserAgentPermissions().filter(
    (entry) => entry.origin !== origin,
  );
  writeArray(
    PERMISSIONS_KEY,
    permission === "ask"
      ? other
      : [{ origin, permission, updatedAt: Date.now() }, ...other],
  );
}

export function listBrowserAgentAudit(): BrowserAgentAuditEntry[] {
  return readArray<BrowserAgentAuditEntry>(AUDIT_KEY)
    .filter(
      (entry) =>
        typeof entry?.id === "string" &&
        typeof entry.origin === "string" &&
        typeof entry.action === "string" &&
        typeof entry.createdAt === "number",
    )
    .slice(0, MAX_AUDIT_ENTRIES);
}

export function recordBrowserAgentAudit(
  entry: Omit<BrowserAgentAuditEntry, "id" | "createdAt">,
): void {
  const next: BrowserAgentAuditEntry = {
    ...entry,
    id: `audit-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    createdAt: Date.now(),
  };
  writeArray(
    AUDIT_KEY,
    [next, ...listBrowserAgentAudit()].slice(0, MAX_AUDIT_ENTRIES),
  );
}

export function clearBrowserAgentAudit(): void {
  writeArray(AUDIT_KEY, []);
}
