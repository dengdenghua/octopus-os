/**
 * Auto-popup wrapper for the daily-credits claim dialog.
 *
 * Mount-and-forget: on workspace load we check today's claim state and,
 * if the user is linked to an official account, hasn't claimed yet, and hasn't
 * dismissed the popup today, we pop the dialog automatically. Dismissal
 * is stored in localStorage keyed by date so it auto-resets tomorrow.
 */
import { useEffect, useState } from "react";

import { useDailyClaimInfo, useOctLink } from "@/core/oct/hooks";
import {
  markDailyClaimDismissedToday,
  wasDailyClaimDismissedToday,
} from "@/core/credits/daily-claim-preferences";
import { useAuth } from "@/providers/AuthProvider";

import { DailyClaimDialog } from "./daily-claim-dialog";

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
    if (wasDailyClaimDismissedToday()) return;
    setOpen(true);
  }, [user, linked, info.isLoading, info.data]);

  if (!user || !linked) return null;

  return (
    <DailyClaimDialog
      open={open}
      onOpenChange={setOpen}
      autoPopup
      onDismissToday={markDailyClaimDismissedToday}
    />
  );
}
