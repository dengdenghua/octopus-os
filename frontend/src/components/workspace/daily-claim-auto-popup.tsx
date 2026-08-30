/**
 * Auto-popup wrapper for the daily-credits claim dialog.
 *
 * Mount-and-forget: on workspace load we check today's claim state and,
 * if the user is linked to an official account, hasn't claimed yet, and hasn't
 * dismissed the popup today, we pop the dialog automatically. Dismissal
 * is stored in localStorage keyed by date so it auto-resets tomorrow.
 */
import { swallow } from "@/core/utils/log";
import { useEffect, useState } from "react";

import { useDailyClaimInfo, useOctLink } from "@/core/oct/hooks";
import { useAuth } from "@/providers/AuthProvider";

import { DailyClaimDialog } from "./daily-claim-dialog";

const DISMISS_KEY_PREFIX = "oct:dailyClaimDismissed:";

function todayKey(): string {
  // Match the user's local day boundary — dismissing in the evening
  // shouldn't auto-reset at midnight UTC.
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${DISMISS_KEY_PREFIX}${y}-${m}-${d}`;
}

function wasDismissedToday(): boolean {
  if (typeof window === "undefined") return true; // SSR: don't auto-pop
  try {
    return window.localStorage.getItem(todayKey()) === "1";
  } catch (e) {
    swallow(e);
    return true;
  }
}

function markDismissedToday() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(todayKey(), "1");
  } catch (e) {
    swallow(e, "storage");
  }
}

export function DailyClaimAutoPopup() {
  const { user } = useAuth();
  const link = useOctLink();
  const linked = Boolean(link.data);

  // Only fetch claim info when we know the user is actually linked —
  // otherwise we'd 404 on every page load.
  const info = useDailyClaimInfo(linked);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!user || !linked) return;
    if (info.isLoading || !info.data) return;
    const data = info.data.data;
    const claimed = Boolean(data?.claimedToday ?? false);
    if (claimed) return;
    if (wasDismissedToday()) return;
    setOpen(true);
  }, [user, linked, info.isLoading, info.data]);

  if (!user || !linked) return null;

  return (
    <DailyClaimDialog
      open={open}
      onOpenChange={setOpen}
      autoPopup
      onDismissToday={markDismissedToday}
    />
  );
}
