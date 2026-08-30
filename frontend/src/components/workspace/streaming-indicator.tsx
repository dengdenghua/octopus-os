import { SparklesIcon } from "lucide-react";
import { memo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export const StreamingIndicator = memo(function StreamingIndicator({
  className,
  size = "normal",
  showLabel = true,
}: {
  className?: string;
  size?: "normal" | "sm";
  showLabel?: boolean;
}) {
  const isSm = size === "sm";
  const { t } = useI18n();

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2.5 rounded-lg border transition-colors duration-slow",
        isSm
          ? "border-primary/15 bg-primary/[0.03] px-3 py-1.5"
          : "border-primary/20 bg-primary/[0.05] px-4 py-2",
        className,
      )}
    >
      <div className="relative flex items-center justify-center">
        <div
          className={cn(
            "absolute rounded-lg bg-primary/20 animate-pulse will-change-transform",
            isSm ? "size-5" : "size-7",
          )}
        />
        <div
          className={cn(
            "relative flex items-center justify-center rounded-lg bg-primary/10",
            isSm ? "size-4" : "size-5",
          )}
        >
          <SparklesIcon
            className={cn("text-primary", isSm ? "size-2.5" : "size-3")}
          />
        </div>
      </div>
      {showLabel && (
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "font-medium text-foreground",
              isSm ? "text-xs" : "text-sm",
            )}
          >
            {t.streaming.thinking}
          </span>
          <div className="flex items-center gap-0.5">
            <span
              className={cn(
                "inline-block rounded-lg bg-primary/50 will-change-transform",
                isSm ? "size-1" : "size-1.5",
              )}
              style={{
                animation: "streaming-bounce 1.4s ease-in-out infinite",
              }}
            />
            <span
              className={cn(
                "inline-block rounded-lg bg-primary/50 will-change-transform",
                isSm ? "size-1" : "size-1.5",
              )}
              style={{
                animation: "streaming-bounce 1.4s ease-in-out 0.2s infinite",
              }}
            />
            <span
              className={cn(
                "inline-block rounded-lg bg-primary/50 will-change-transform",
                isSm ? "size-1" : "size-1.5",
              )}
              style={{
                animation: "streaming-bounce 1.4s ease-in-out 0.4s infinite",
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
});
