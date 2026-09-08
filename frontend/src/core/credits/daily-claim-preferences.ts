import { currentActorId } from "@/core/auth/api";
import {
  actorScopedStorageKey,
  readActorScopedStorageValue,
} from "@/core/auth/scoped-storage";

const DISMISS_KEY_PREFIX = "oct:dailyClaimDismissed:";

function localDateKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function dailyClaimDismissalStorageKey(
  date = new Date(),
  actor = currentActorId(),
): string {
  return actorScopedStorageKey(
    `${DISMISS_KEY_PREFIX}${localDateKey(date)}`,
    actor,
  );
}

/** Read today's dismissal, migrating the pre-account date key on first use. */
export function wasDailyClaimDismissedToday(date = new Date()): boolean {
  return (
    readActorScopedStorageValue(
      `${DISMISS_KEY_PREFIX}${localDateKey(date)}`,
    ) === "1"
  );
}

export function markDailyClaimDismissedToday(date = new Date()): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(dailyClaimDismissalStorageKey(date), "1");
  } catch {
    /* Keep the claim dialog usable when storage is blocked. */
  }
}
