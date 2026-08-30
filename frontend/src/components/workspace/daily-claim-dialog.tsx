/* Implementation note. */
import { Gift, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { useClaimDailyCredits, useDailyClaimInfo } from "@/core/oct/hooks";
import { cn } from "@/lib/utils";

type ClaimInfoData = {
  fixedCredits?: number;
  directCredits?: number;
  maxCredits?: number;
  maxDrawCredits?: number;
  claimedToday?: boolean;
} | null | undefined;

/**
 * Pull the best-known fields out of the account service response with
 * sensible fallbacks matching the reference UX (2500 / 4000).
 */
function extractClaimData(data: ClaimInfoData): {
  fixed: number;
  max: number;
  canClaim: boolean;
  claimed: boolean;
} {
  const fixed = data?.fixedCredits ?? data?.directCredits ?? 2500;
  const max = data?.maxCredits ?? data?.maxDrawCredits ?? 4000;
  const claimed = Boolean(data?.claimedToday ?? false);
  const canClaim = !claimed;
  return { fixed, max, canClaim, claimed };
}

/** Credits actually awarded by a claim call — tries multiple known paths. */
function extractAwardedCredits(result: Record<string, unknown>): number {
  const d = (result.data as Record<string, unknown> | undefined) ?? result;
  const n = d.credits ?? d.addedCredits;
  return typeof n === "number" && Number.isFinite(n) ? n : 0;
}

export interface DailyClaimDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /* Implementation note. */
  autoPopup?: boolean;
  /** Called when user dismisses with "don't show again today". */
  onDismissToday?: () => void;
}

export function DailyClaimDialog({
  open,
  onOpenChange,
  autoPopup = false,
  onDismissToday,
}: DailyClaimDialogProps) {
  const { t } = useI18n();
  const info = useDailyClaimInfo(open);
  const claim = useClaimDailyCredits();
  const [pendingDraw, setPendingDraw] = useState<boolean | null>(null);

  const { fixed, max, canClaim, claimed } = useMemo(
    () => extractClaimData(info.data?.data),
    [info.data],
  );

  const doClaim = async (draw: boolean) => {
    setPendingDraw(draw);
    try {
      const result = await claim.mutateAsync(draw);
      if (result && result.success === false) {
        toast.error(String(result.errMessage || t.dailyClaim.claimFailed));
        return;
      }
      const awarded = extractAwardedCredits(result);
      toast.success(
        awarded > 0
          ? t.dailyClaim.claimSuccess(awarded)
          : t.dailyClaim.claimSuccess(draw ? max : fixed),
      );
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t.dailyClaim.claimFailed,
      );
    } finally {
      setPendingDraw(null);
    }
  };

  const busy = claim.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-md overflow-hidden p-0 gap-0 border-0"
      >
        {/* Purple gradient header — mirrors Dangbei's reference screenshot */}
        <div className="relative bg-gradient-to-br from-violet-500 via-purple-500 to-indigo-500 px-6 pt-6 pb-10">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
            className="absolute right-4 top-4 flex size-8 items-center justify-center rounded-full bg-white/15 text-white transition hover:bg-white/25"
          >
            <X className="size-4" />
          </button>

          <DialogTitle className="max-w-[60%] text-2xl font-bold leading-tight text-white">
            {t.dailyClaim.title}
          </DialogTitle>

          {/* Gift illustration — lucide icon inside a soft disk */}
          <div className="absolute right-6 top-6 flex size-24 items-center justify-center">
            <div className="absolute inset-0 rounded-full bg-white/10 blur-sm" />
            <div className="relative flex size-20 items-center justify-center rounded-full bg-gradient-to-br from-pink-200 to-destructive shadow-[var(--shadow-md)]">
              <Gift className="size-10 text-destructive" strokeWidth={2.2} />
            </div>
            <Sparkles className="absolute -top-1 -left-1 size-4 text-warning" />
            <Sparkles className="absolute bottom-2 left-0 size-3 text-warning" />
          </div>
        </div>

        {/* Body — description + actions */}
        <div className="bg-card px-6 py-5 -mt-5 rounded-t-3xl">
          <DialogDescription className="text-center text-sm leading-relaxed text-foreground/80 mb-5">
            {info.isLoading
              ? t.common.loading
              : claimed
                ? t.dailyClaim.claimedToday
                : t.dailyClaim.description(fixed)}
          </DialogDescription>

          {/* Primary: spin for the draw */}
          <div className="relative mb-3">
            {canClaim && !info.isLoading && (
              <span className="absolute -top-2 right-3 z-10 rounded-full bg-destructive px-2 py-0.5 text-xs font-semibold text-white shadow">
                {t.dailyClaim.drawHint(max)}
              </span>
            )}
            <Button
              type="button"
              onClick={() => doClaim(true)}
              disabled={busy || !canClaim || info.isLoading}
              className={cn(
                "h-12 w-full rounded-lg bg-black text-base font-semibold text-white shadow",
                "hover:bg-black/90 disabled:opacity-60",
              )}
            >
              {pendingDraw === true
                ? t.dailyClaim.drawing
                : t.dailyClaim.drawButton}
            </Button>
          </div>

          {/* Secondary: take the fixed amount */}
          <Button
            type="button"
            variant="outline"
            onClick={() => doClaim(false)}
            disabled={busy || !canClaim || info.isLoading}
            className="h-12 w-full rounded-lg border text-base font-medium disabled:opacity-60"
          >
            {pendingDraw === false
              ? t.dailyClaim.claiming
              : t.dailyClaim.directClaim(fixed)}
          </Button>

          {autoPopup && (
            <button
              type="button"
              onClick={() => {
                onDismissToday?.();
                onOpenChange(false);
              }}
              className="mt-4 block w-full text-center text-xs text-muted-foreground hover:text-foreground"
            >
              {t.dailyClaim.dismissToday}
            </button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
