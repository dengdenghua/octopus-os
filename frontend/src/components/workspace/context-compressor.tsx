/**
 * ContextCompressor — 上下文压缩指示器（精简版）
 *
 * 视觉与相邻的 TokenUsageIndicator 对齐：统一的 h-8 chip、一个小图标、
 * 一个百分比数字。颜色通过文本颜色反映阈值（muted → primary → warning
 * → destructive），不再用 SVG 圆环/双层动画/外部 pulse。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface ContextCompressorProps {
  currentTokens: number;
  maxTokens: number;
  compressThreshold?: number;
  isCompressing?: boolean;
  onCompress?: () => void | Promise<void>;
  disabled?: boolean;
  className?: string;
}

function getColorClass(progress: number): string {
  if (progress >= 0.95) return "text-destructive";
  if (progress >= 0.8) return "text-warning";
  if (progress >= 0.6) return "text-primary";
  return "text-muted-foreground";
}

export function ContextCompressor({
  currentTokens,
  maxTokens,
  compressThreshold = 0.9,
  isCompressing = false,
  onCompress,
  disabled = false,
  className,
}: ContextCompressorProps) {
  const { t } = useI18n();
  const [hasAutoCompressed, setHasAutoCompressed] = useState(false);
  const autoCompressRef = useRef(false);

  const progress = useMemo(() => {
    if (maxTokens <= 0) return 0;
    return Math.min(currentTokens / maxTokens, 1);
  }, [currentTokens, maxTokens]);

  const percentage = useMemo(() => Math.round(progress * 100), [progress]);

  useEffect(() => {
    if (
      progress >= compressThreshold &&
      !hasAutoCompressed &&
      !autoCompressRef.current &&
      !isCompressing &&
      !disabled &&
      onCompress
    ) {
      autoCompressRef.current = true;
      setHasAutoCompressed(true);
      void onCompress();
    }
  }, [
    progress,
    compressThreshold,
    hasAutoCompressed,
    isCompressing,
    disabled,
    onCompress,
  ]);

  useEffect(() => {
    if (progress < compressThreshold * 0.8) {
      setHasAutoCompressed(false);
      autoCompressRef.current = false;
    }
  }, [progress, compressThreshold]);

  const handleClick = useCallback(() => {
    if (disabled || isCompressing || !onCompress) return;
    void onCompress();
  }, [disabled, isCompressing, onCompress]);

  const colorClass = getColorClass(progress);
  const isFull = progress >= 0.95;
  const circumference = 2 * Math.PI * 7;
  const strokeLength = circumference * progress;
  const canCompress = Boolean(onCompress) && !isCompressing && !disabled;
  const contextLabel = `${t.contextCompressor?.contextUsage ?? "Context Usage"}: ${percentage}%`;

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={handleClick}
          disabled={!canCompress}
          className={cn(
            "hover:bg-muted flex size-8 items-center justify-center rounded-lg border border-transparent text-xs transition-colors",
            "hover:text-foreground",
            canCompress ? "cursor-pointer" : "cursor-default opacity-60",
            disabled && "opacity-60",
            colorClass,
            className,
          )}
          aria-label={`${contextLabel}. ${t.contextCompressor?.clickToCompress ?? "Click to compress context"}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percentage}
        >
          <svg aria-hidden viewBox="0 0 18 18" className="size-4 -rotate-90">
            <circle
              cx="9"
              cy="9"
              r="7"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="opacity-20"
            />
            {progress > 0 && (
              <circle
                cx="9"
                cy="9"
                r="7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={`${strokeLength} ${circumference}`}
                className={cn(
                  "transition-[stroke-dasharray] duration-slow",
                  isCompressing && "animate-pulse",
                )}
              />
            )}
          </svg>
          <span className="sr-only">{percentage}%</span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" align="center">
        <div className="space-y-1 text-xs">
          <div className="font-medium">{contextLabel}</div>
          <div className="text-muted-foreground">
            {t.contextCompressor?.clickToCompress ??
              "Click to compress context"}
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Tokens</span>
            <span className="font-mono">
              {currentTokens.toLocaleString()} / {maxTokens.toLocaleString()}
            </span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">
              {t.contextCompressor?.threshold ?? "Auto-compress at"}
            </span>
            <span className="font-mono">
              {Math.round(compressThreshold * 100)}%
            </span>
          </div>
          {isFull && (
            <div className="border-t border-border-default pt-1 font-medium text-destructive">
              {t.contextCompressor?.contextFull ?? "Context nearly full!"}
            </div>
          )}
          {hasAutoCompressed && (
            <div className="text-primary">
              {t.contextCompressor?.autoCompressed ?? "Auto-compressed"}
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

export function useContextCompressor(
  maxTokens: number,
  compressThreshold?: number,
) {
  const [currentTokens, setCurrentTokens] = useState(0);
  const [isCompressing, setIsCompressing] = useState(false);

  const compress = useCallback(async () => {
    if (isCompressing) return;
    setIsCompressing(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      setCurrentTokens((prev) => Math.floor(prev * 0.6));
    } finally {
      setIsCompressing(false);
    }
  }, [isCompressing]);

  return {
    currentTokens,
    setCurrentTokens,
    isCompressing,
    compress,
    maxTokens,
    compressThreshold,
  };
}
