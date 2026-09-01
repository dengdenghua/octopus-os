import type { Message } from "@/core/api/types";
import { CoinsIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState, memo } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { accumulateUsage, formatTokenCount } from "@/core/messages/usage";
import { cn } from "@/lib/utils";

interface TokenUsageIndicatorProps {
  messages: Message[];
  className?: string;
}

export const TokenUsageIndicator = memo(function TokenUsageIndicator({
  messages,
  className,
}: TokenUsageIndicatorProps) {
  const { t } = useI18n();

  const usage = useMemo(() => accumulateUsage(messages), [messages]);

  // Streaming speed tracking
  const [tokensPerSec, setTokensPerSec] = useState<number | null>(null);
  const streamStartRef = useRef<number>(0);
  const prevTokensRef = useRef<number>(0);

  useEffect(() => {
    if (!usage) return;
    const now = Date.now();
    if (streamStartRef.current === 0) {
      streamStartRef.current = now;
      prevTokensRef.current = usage.outputTokens;
      return;
    }
    const elapsed = (now - streamStartRef.current) / 1000;
    if (elapsed > 0.5) {
      const newTokens = usage.outputTokens - prevTokensRef.current;
      setTokensPerSec(Math.round(newTokens / elapsed));
    }
  }, [usage]);

  // Reset on new message
  useEffect(() => {
    streamStartRef.current = 0;
    prevTokensRef.current = 0;
    setTokensPerSec(null);
  }, [messages.length]);

  if (!usage) {
    return null;
  }

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            "text-muted-foreground hover:text-foreground hover:bg-muted flex h-8 cursor-default items-center gap-1 border border-transparent px-2 text-xs transition-colors",
            className,
          )}
        >
          <CoinsIcon size={14} />
          <span>{formatTokenCount(usage.totalTokens)}</span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end">
        <div className="space-y-1 text-xs">
          <div className="font-medium">{t.tokenUsage.title}</div>
          <div className="flex justify-between gap-4">
            <span>{t.tokenUsage.input}</span>
            <span className="font-mono">
              {formatTokenCount(usage.inputTokens)}
            </span>
          </div>
          <div className="flex justify-between gap-4">
            <span>{t.tokenUsage.output}</span>
            <span className="font-mono">
              {formatTokenCount(usage.outputTokens)}
            </span>
          </div>
          <div className="border-t border-border-default pt-1">
            <div className="flex justify-between gap-4">
              <span>{t.tokenUsage.total}</span>
              <span className="font-mono font-medium">
                {formatTokenCount(usage.totalTokens)}
              </span>
            </div>
          </div>
          {tokensPerSec !== null && tokensPerSec > 0 && (
            <div className="flex justify-between gap-4">
              <span>{t.tokenUsage.speed}</span>
              <span className="font-mono">{tokensPerSec} tok/s</span>
            </div>
          )}
          {usage && (
            <div className="border-t border-border-default pt-1 mt-1">
              <div className="flex justify-between gap-4 mb-1">
                <span>{t.tokenUsage.context}</span>
                <span className="font-mono">
                  {formatTokenCount(usage.totalTokens)} / 100K
                </span>
              </div>
              <div className="h-1 rounded-lg bg-muted overflow-hidden">
                <div
                  className={cn(
                    "h-full rounded-lg transition-all",
                    usage.totalTokens > 80000
                      ? "bg-destructive dark:bg-destructive"
                      : usage.totalTokens > 50000
                        ? "bg-warning dark:bg-warning"
                        : "bg-success dark:bg-success",
                  )}
                  style={{
                    width: `${Math.min(100, (usage.totalTokens / 100000) * 100)}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
});
