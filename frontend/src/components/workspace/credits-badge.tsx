import { Coins } from "lucide-react";

import { useOctLink } from "@/core/oct/hooks";
import { cn } from "@/lib/utils";
import { useI18n } from "@/core/i18n/hooks";

function formatCredits(n: number | undefined | null): string | null {
  if (n === undefined || n === null || Number.isNaN(n)) return null;
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(1)}亿`;
  if (n >= 10_000) return `${(n / 10_000).toFixed(n >= 100_000 ? 0 : 1)}万`;
  if (n >= 1_000) return n.toLocaleString();
  return String(n);
}

export function CreditsBadge({ className }: { className?: string }) {
  const { t } = useI18n();
  const { data } = useOctLink();
  const remaining = data?.credits?.surplusCredits;
  const formatted = formatCredits(remaining);

  if (formatted === null) return null;

  return (
    <button
      type="button"
      onClick={() => {
        window.dispatchEvent(
          new CustomEvent("echo:open-settings", {
            detail: { tab: "account" },
          }),
        );
      }}
      title={
        typeof remaining === "number"
          ? t.credits.remainingWithHint(remaining)
          : t.credits.credits
      }
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full",
        "bg-muted/60 px-2.5 py-1 text-xs font-medium text-foreground/70",
        "transition hover:bg-muted hover:text-foreground",
        className,
      )}
      aria-label={t.credits.credits}
    >
      <Coins className="size-3" />
      {formatted}
    </button>
  );
}
