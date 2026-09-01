import type { AutomationTarget } from "@/core/computer/api";

const TARGET_STORAGE_PREFIX = "echo:automation-target:";

function targetStorageKey(threadId?: string | null): string {
  return `${TARGET_STORAGE_PREFIX}${threadId?.trim() || "new"}`;
}

export function loadAutomationTarget(
  threadId?: string | null,
): AutomationTarget | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(targetStorageKey(threadId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<AutomationTarget>;
    if (
      (value.kind !== "browser_tab" && value.kind !== "desktop_window") ||
      typeof value.id !== "string" ||
      !value.id.trim() ||
      typeof value.title !== "string" ||
      !value.title.trim()
    ) {
      return null;
    }
    return value as AutomationTarget;
  } catch {
    return null;
  }
}

export function saveAutomationTarget(
  threadId: string | null | undefined,
  target: AutomationTarget | null,
): void {
  if (typeof window === "undefined") return;
  try {
    const key = targetStorageKey(threadId);
    if (target) {
      window.localStorage.setItem(key, JSON.stringify(target));
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Storage can be unavailable in private/restricted browser contexts.
  }
}
