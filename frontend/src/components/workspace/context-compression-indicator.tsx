import { useEffect, useState } from "react";
import { InfoIcon, Loader2Icon } from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface ContextCompressionIndicatorProps {
  isCompressing: boolean;
  contextTokens?: number;
  maxContextTokens?: number;
  onCancel?: () => void;
}

/**
 * 流式上下文压缩进度指示器
 *
 * 显示在对话界面上方，提供压缩进度的实时反馈，
 * 消除用户"系统卡住了"的困惑。
 *
 * 优化目标：
 * - 用户投诉率从 5% → <1%
 * - 等待容忍度提升 80%
 */
export function ContextCompressionIndicator({
  isCompressing,
  contextTokens,
  maxContextTokens,
  onCancel,
}: ContextCompressionIndicatorProps) {
  const { t } = useI18n();
  const [elapsed, setElapsed] = useState(0);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!isCompressing) {
      setElapsed(0);
      // 延迟隐藏，避免闪烁
      const timer = setTimeout(() => setVisible(false), 300);
      return () => clearTimeout(timer);
    }

    // 显示延迟 500ms，避免快速压缩时闪现
    const showTimer = setTimeout(() => setVisible(true), 500);

    const timer = setInterval(() => {
      setElapsed((e) => e + 100);
    }, 100);

    return () => {
      clearTimeout(showTimer);
      clearInterval(timer);
    };
  }, [isCompressing]);

  if (!visible && !isCompressing) return null;

  const progress =
    contextTokens && maxContextTokens
      ? Math.min(100, (contextTokens / maxContextTokens) * 100)
      : undefined;

  const estimatedRemaining = progress
    ? Math.ceil(((100 - progress) / progress) * elapsed)
    : undefined;

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm transition-opacity duration-300",
        isCompressing ? "opacity-100" : "opacity-0 pointer-events-none",
      )}
    >
      <div className="mx-4 w-full max-w-md rounded-lg border border-border-default bg-card p-6 shadow-lg">
        <div className="flex items-center gap-3">
          <Loader2Icon className="size-5 shrink-0 animate-spin text-primary" />
          <div className="flex-1">
            <h3 className="text-sm font-semibold text-foreground">
              {t.contextCompressor?.title ?? "正在压缩上下文"}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t.contextCompressor?.description ??
                "正在优化对话历史以提升响应速度"}
            </p>
          </div>
        </div>

        {/* 进度条 */}
        {progress !== undefined && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground mb-1.5">
              <span>
                {contextTokens?.toLocaleString()} / {maxContextTokens?.toLocaleString()} tokens
              </span>
              <span>{progress.toFixed(0)}%</span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all duration-300 ease-out"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}

        {/* 时间统计 */}
        <div className="mt-4 flex items-center justify-between text-xs">
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <InfoIcon className="size-3.5" />
            <span>
              {elapsed < 1000
                ? "分析中..."
                : `已用时 ${(elapsed / 1000).toFixed(1)}s`}
              {estimatedRemaining !== undefined &&
                estimatedRemaining > 1000 &&
                ` / 预计 ${(estimatedRemaining / 1000).toFixed(1)}s`}
            </span>
          </div>

          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              取消
            </button>
          )}
        </div>

        {/* 提示信息 */}
        <div className="mt-4 flex items-start gap-2 rounded-md bg-muted/50 px-3 py-2">
          <InfoIcon className="size-3.5 shrink-0 text-muted-foreground mt-0.5" />
          <p className="text-xs text-muted-foreground leading-relaxed">
            {t.contextCompressor?.tip ??
              "压缩完成后对话将自动继续，这通常需要 3-10 秒"}
          </p>
        </div>
      </div>
    </div>
  );
}
