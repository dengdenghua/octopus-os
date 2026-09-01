/* Implementation note. */
import { RefreshCcwIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { usePreviewRefresh } from "@/core/observability/file-ops";
import { useI18n } from "@/core/i18n/hooks";
import { canAccessOperatorControlPlane } from "@/core/auth/control-plane-access";
import { cn } from "@/lib/utils";
import { useOptionalAuth } from "@/providers/AuthProvider";

interface Props {
  className?: string;
}

export function PreviewRefreshIndicator({ className }: Props) {
  const { t } = useI18n();
  const auth = useOptionalAuth();
  const latest = usePreviewRefresh({
    enabled: canAccessOperatorControlPlane(
      auth?.authStatus ?? null,
      auth?.user ?? null,
    ),
  });
  const [flashKey, setFlashKey] = useState<number | null>(null);
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!latest) return;
    setFlashKey(Date.now());
    setCount((c) => c + 1);
  }, [latest]);

  if (!latest) return null;

  return (
    <button
      type="button"
      className={cn(
        "flex items-center gap-1 rounded-lg px-2 py-1 text-xs",
        "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        "transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        className,
      )}
      title={
        latest.reason
          ? t.activityIndicators.previewLastRefresh(latest.reason)
          : t.activityIndicators.previewWaitingForRefresh
      }
      data-testid="preview-refresh-indicator"
    >
      <RefreshCcwIcon
        key={flashKey ?? "idle"}
        className={cn(
          "size-3.5",
          flashKey !== null &&
            "animate-learn-pulse text-[color:var(--primary)]",
        )}
        onAnimationEnd={() => setFlashKey(null)}
      />
      <span>{t.activityIndicators.previewPrefix(count)}</span>
    </button>
  );
}
