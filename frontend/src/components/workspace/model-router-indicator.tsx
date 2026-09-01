/**
 * Model Router Indicator
 *
 * A compact indicator showing which model was auto-selected by the Dynamic
 * Model Router and why. Designed to sit near the model selector in the
 * input box footer.
 *
 * - Collapsed: "Auto: Claude Sonnet (coding)" badge
 * - Expanded (click): popover with routing decision details, score breakdown,
 *   and a link to routing history.
 */

import { useCallback, useMemo, useState } from "react";
import {
  BrainCircuitIcon,
  ChevronDownIcon,
  CodeIcon,
  CalculatorIcon,
  MessageSquareIcon,
  PaletteIcon,
  RotateCcwIcon,
  ZapIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { authHeaders } from "@/core/auth/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RoutingDecision {
  task_type: string;
  selected_model: string;
  original_model: string;
  score: number;
  scores_breakdown: Record<string, number>;
  preference: string;
  reason: string;
  timestamp: number;
  was_overridden: boolean;
}

interface ModelRouterIndicatorProps {
  /** The routing decision from the current thread state (_model_routing). */
  routingDecision?: RoutingDecision | null;
  className?: string;
}

// ---------------------------------------------------------------------------
// Task type icon / label mapping
// ---------------------------------------------------------------------------

const TASK_TYPE_ICONS: Record<
  string,
  { icon: typeof CodeIcon; color: string }
> = {
  coding: { icon: CodeIcon, color: "text-info" },
  reasoning: { icon: BrainCircuitIcon, color: "text-chart-1" },
  creative: { icon: PaletteIcon, color: "text-chart-3" },
  simple: { icon: ZapIcon, color: "text-success" },
  math: { icon: CalculatorIcon, color: "text-warning" },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function shortModelName(name: string): string {
  // Shorten long model identifiers for badge display
  const parts = name.split("/");
  const base = parts[parts.length - 1] ?? name;
  // Remove date suffixes like -20250514
  return base.replace(/-\d{8}$/, "");
}

function formatScore(score: number): string {
  return (score * 100).toFixed(0) + "%";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ModelRouterIndicator({
  routingDecision,
  className,
}: ModelRouterIndicatorProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [history, setHistory] = useState<RoutingDecision[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const TASK_TYPE_LABELS: Record<string, string> = {
    coding: t.modelRouter.coding,
    reasoning: t.modelRouter.reasoning,
    creative: t.modelRouter.creative,
    simple: t.modelRouter.simple,
    math: t.modelRouter.math,
  };

  // All hooks hoisted BEFORE the ``if (!routingDecision)`` early
  // return. Pre-fix ``loadHistory`` / ``handleToggle`` / ``sortedScores``
  // sat below the guard · so rendering a message without a routing
  // decision before rendering one with it (normal chat flow · first
  // AI reply sometimes arrives without decision meta) triggered
  // "Rendered more hooks than during the previous render." All 3
  // hooks are defensively null-safe via optional chaining inside.
  const loadHistory = useCallback(async () => {
    if (loadingHistory || history.length > 0) return;
    setLoadingHistory(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/models/routing-history?limit=10`,
        { headers: authHeaders() },
      );
      if (res.ok) {
        const data = await res.json();
        setHistory(data.decisions ?? []);
      }
    } catch (e) {
      swallow(e);
      // Silently fail — history is optional
    } finally {
      setLoadingHistory(false);
    }
  }, [loadingHistory, history.length]);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next) loadHistory();
      return next;
    });
  }, [loadHistory]);

  const sortedScores = useMemo(() => {
    if (!routingDecision?.scores_breakdown) return [];
    return Object.entries(routingDecision.scores_breakdown)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5);
  }, [routingDecision?.scores_breakdown]);

  // Don't render if no routing decision
  if (!routingDecision) {
    return null;
  }

  const taskIconCfg = TASK_TYPE_ICONS[routingDecision.task_type] ?? {
    icon: MessageSquareIcon,
    color: "text-muted-foreground",
  };
  const taskLabel =
    TASK_TYPE_LABELS[routingDecision.task_type] ?? routingDecision.task_type;
  const TaskIcon = taskIconCfg.icon;

  const displayModel = shortModelName(routingDecision.selected_model);
  const isAutoRouted = !routingDecision.was_overridden;

  return (
    <div className={cn("relative inline-flex", className)}>
      <Tooltip delayDuration={300}>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={handleToggle}
            className={cn(
              "text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1 rounded-lg px-1.5 py-0.5 text-xs transition-colors",
              "hover:bg-accent/50",
              isAutoRouted && "border border-dashed border-border-default",
            )}
          >
            <RotateCcwIcon size={12} className="shrink-0 opacity-60" />
            <span className="truncate max-w-[var(--text-truncate-md)]">
              {isAutoRouted ? t.modelRouter.auto : t.modelRouter.manual}:{" "}
              <span className="font-medium">{displayModel}</span>
            </span>
            <Badge
              variant="outline"
              className={cn(
                "ml-0.5 h-4 px-1 text-xs font-normal leading-none",
                taskIconCfg.color,
              )}
            >
              <TaskIcon size={10} className="mr-0.5" />
              {taskLabel}
            </Badge>
            <ChevronDownIcon
              size={12}
              className={cn(
                "shrink-0 opacity-40 transition-transform",
                expanded && "rotate-180",
              )}
            />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" align="start" className="max-w-xs">
          <p className="text-xs">{routingDecision.reason}</p>
        </TooltipContent>
      </Tooltip>

      {/* Expanded panel */}
      {expanded && (
        <div
          className={cn(
            "bg-popover text-popover-foreground absolute bottom-full left-0 z-50 mb-1 w-72 rounded-lg border p-3 shadow-[var(--shadow-md)]",
            "animate-in fade-in-0 slide-in-from-bottom-2 duration-base",
          )}
        >
          {/* Header */}
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-xs font-semibold">{t.modelRouter.title}</h4>
            <Badge variant="secondary" className="h-4 text-xs">
              {routingDecision.preference}
            </Badge>
          </div>

          {/* Decision summary */}
          <div className="mb-2 space-y-1 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t.modelRouter.taskType}
              </span>
              <span
                className={cn(
                  "flex items-center gap-1 font-medium",
                  taskIconCfg.color,
                )}
              >
                <TaskIcon size={12} />
                {taskLabel}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t.modelRouter.selectedModel}
              </span>
              <span className="font-medium">{displayModel}</span>
            </div>
            {routingDecision.original_model !==
              routingDecision.selected_model && (
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">
                  {t.modelRouter.originalModel}
                </span>
                <span className="text-muted-foreground line-through">
                  {shortModelName(routingDecision.original_model)}
                </span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">
                {t.modelRouter.score}
              </span>
              <span className="font-mono font-medium">
                {formatScore(routingDecision.score)}
              </span>
            </div>
          </div>

          {/* Score breakdown */}
          {sortedScores.length > 0 && (
            <div className="mb-2">
              <h5 className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wider">
                {t.modelRouter.modelScores}
              </h5>
              <div className="space-y-0.5">
                {sortedScores.map(([model, score]) => (
                  <div
                    key={model}
                    className="flex items-center gap-2 text-xs"
                  >
                    <span
                      className={cn(
                        "flex-1 truncate",
                        model === routingDecision.selected_model
                          ? "font-medium"
                          : "text-muted-foreground",
                      )}
                    >
                      {shortModelName(model)}
                    </span>
                    <div className="h-1 w-16 overflow-hidden rounded-lg bg-muted">
                      <div
                        className={cn(
                          "h-full rounded-lg transition-all",
                          model === routingDecision.selected_model
                            ? "bg-primary"
                            : "bg-muted-foreground/40",
                        )}
                        style={{ width: `${Math.min(100, score * 100)}%` }}
                      />
                    </div>
                    <span className="w-8 text-right font-mono text-xs">
                      {formatScore(score)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent history */}
          {history.length > 0 && (
            <div>
              <h5 className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wider">
                {t.modelRouter.recentRouting}
              </h5>
              <div className="max-h-24 space-y-0.5 overflow-y-auto">
                {history.map((d, i) => {
                  const meta = TASK_TYPE_ICONS[d.task_type];
                  const Icon = meta?.icon ?? MessageSquareIcon;
                  return (
                    <div
                      key={`${d.timestamp}-${i}`}
                      className="flex items-center gap-1.5 text-xs text-muted-foreground"
                    >
                      <Icon size={10} className={meta?.color ?? ""} />
                      <span className="truncate flex-1">
                        {shortModelName(d.selected_model)}
                      </span>
                      <span className="font-mono">{formatScore(d.score)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {loadingHistory && (
            <p className="text-muted-foreground text-center text-xs">
              {t.modelRouter.loadingHistory}
            </p>
          )}
        </div>
      )}

      {/* Backdrop to close expanded panel */}
      {expanded && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setExpanded(false)}
        />
      )}
    </div>
  );
}
