// Banner shown above the first message when thread/resume returned a
// paginated window (Conversation.hasMoreTurns). Clicking pages one more
// batch of older turns in; the hook ignores re-entrant calls, so the
// local pending flag is purely cosmetic.

import { useState } from "react";
import { HistoryIcon, Loader2Icon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { swallow } from "@/core/utils/log";

export function LoadOlderTurnsBanner({
  onLoad,
}: {
  onLoad: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(false);

  const handleClick = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await onLoad();
    } catch (err) {
      swallow(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex justify-center py-1">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={loading}
        className="inline-flex items-center gap-1.5 rounded-full border border-border-default bg-muted/30 px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
      >
        {loading ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <HistoryIcon className="size-3.5" />
        )}
        {loading ? t.message.loadingOlderTurns : t.message.loadOlderTurns}
      </button>
    </div>
  );
}
