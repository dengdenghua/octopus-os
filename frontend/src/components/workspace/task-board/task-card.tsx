/**
 * TaskCard -- compact card for kanban columns and list view.
 *
 * Displays type icon, name, status badge, progress bar, and duration.
 * Clicking the card expands to show full details.
 */

import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ClockIcon,
  Loader2Icon,
  PauseIcon,
  RadarIcon,
  RocketIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useState } from "react";

import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import type {
  BoardStatus,
  TaskType,
  UnifiedTask,
} from "@/core/task-board/types";
import { formatDurationMs } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

export { formatDurationMs };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TYPE_ICONS: Record<TaskType, React.ReactNode> = {
  background: <ZapIcon className="size-3.5" />,
  quest: <RocketIcon className="size-3.5" />,
  scheduled: <ClockIcon className="size-3.5" />,
  intelligence: <RadarIcon className="size-3.5" />,
};

const TYPE_COLORS: Record<TaskType, string> = {
  background: "text-chart-1",
  quest: "text-chart-7",
  scheduled: "text-info",
  intelligence: "text-chart-3",
};

const STATUS_STYLE: Record<
  BoardStatus,
  { dotColor: string; badgeClass: string; icon: React.ReactNode }
> = {
  queued: {
    dotColor: "bg-muted-foreground",
    badgeClass:
      "bg-muted-foreground/10 text-muted-foreground border-muted-foreground/20",
    icon: <ClockIcon className="size-3" />,
  },
  running: {
    dotColor: "bg-warning",
    badgeClass:
      "bg-warning/10 text-warning border-warning/20",
    icon: <Loader2Icon className="size-3 animate-spin" />,
  },
  paused: {
    dotColor: "bg-warning",
    badgeClass:
      "bg-warning/10 text-warning border-warning/20",
    icon: <PauseIcon className="size-3" />,
  },
  completed: {
    dotColor: "bg-success",
    badgeClass:
      "bg-success/10 text-success border-success/20",
    icon: <CheckCircle2Icon className="size-3" />,
  },
  failed: {
    dotColor: "bg-destructive",
    badgeClass:
      "bg-destructive/10 text-destructive border-destructive/20",
    icon: <AlertCircleIcon className="size-3" />,
  },
  cancelled: {
    dotColor: "bg-muted-foreground",
    badgeClass:
      "bg-muted-foreground/10 text-muted-foreground border-muted-foreground/20",
    icon: <XIcon className="size-3" />,
  },
};

export function formatRelativeTime(
  isoString: string,
  translations?: {
    justNow: string;
    minutesAgo: string;
    hoursAgo: string;
    daysAgo: string;
  },
): string {
  if (!isoString) return "";
  const now = Date.now();
  const ts = new Date(isoString).getTime();
  const diff = now - ts;
  if (diff < 0) return translations?.justNow ?? "just now";
  if (diff < 60_000) return translations?.justNow ?? "just now";
  if (diff < 3_600_000)
    return `${Math.floor(diff / 60_000)}${translations?.minutesAgo ?? "m ago"}`;
  if (diff < 86_400_000)
    return `${Math.floor(diff / 3_600_000)}${translations?.hoursAgo ?? "h ago"}`;
  return `${Math.floor(diff / 86_400_000)}${translations?.daysAgo ?? "d ago"}`;
}

// ---------------------------------------------------------------------------
// Status Badge subcomponent
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: BoardStatus }) {
  const { t } = useI18n();
  const cfg = STATUS_STYLE[status] ?? STATUS_STYLE.queued;
  const STATUS_LABELS: Record<BoardStatus, string> = {
    queued: t.taskBoard.queued,
    running: t.taskBoard.running,
    completed: t.taskBoard.completed,
    failed: t.taskBoard.failed,
    paused: t.taskBoard.paused,
    cancelled: t.taskBoard.cancelled,
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-xs font-medium leading-none",
        cfg.badgeClass,
      )}
    >
      {cfg.icon}
      {STATUS_LABELS[status]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// TaskCard
// ---------------------------------------------------------------------------

export function TaskCard({
  task,
  compact = false,
  onClick,
}: {
  task: UnifiedTask;
  compact?: boolean;
  onClick?: () => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const isRunning = task.status === "running";

  const TYPE_LABELS: Record<TaskType, string> = {
    background: t.taskBoard.background,
    quest: t.taskBoard.quest,
    scheduled: t.taskBoard.scheduled,
    intelligence: t.taskBoard.intelligence,
  };
  const STATUS_LABELS: Record<BoardStatus, string> = {
    queued: t.taskBoard.queued,
    running: t.taskBoard.running,
    completed: t.taskBoard.completed,
    failed: t.taskBoard.failed,
    paused: t.taskBoard.paused,
    cancelled: t.taskBoard.cancelled,
  };

  const handleClick = () => {
    if (onClick) {
      onClick();
    } else {
      setExpanded(!expanded);
    }
  };

  return (
    <TooltipProvider delayDuration={400}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={onClick ? undefined : expanded}
        aria-label={t.taskBoard.taskDetails(task.name || task.id)}
        className={cn(
          "ui-dense-row group relative cursor-pointer rounded-lg border bg-card transition-all duration-base",
          "hover:shadow-[var(--shadow-sm)] hover:border-border-strong hover:-translate-y-0.5",
          isRunning && "border-warning/30 shadow-warning/5",
          task.status === "failed" && "border-destructive/20",
          task.status === "completed" && "border-success/20",
        )}
        onClick={handleClick}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            handleClick();
          }
        }}
      >
        {/* Running indicator pulse */}
        {isRunning && (
          <div className="absolute -top-px -right-px size-2.5 rounded-lg">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-lg bg-warning opacity-75" />
            <span className="relative inline-flex size-2.5 rounded-lg bg-warning" />
          </div>
        )}

        {/* Header: type icon + name */}
        <div className="flex items-start gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn("mt-0.5 shrink-0", TYPE_COLORS[task.type])}>
                {TYPE_ICONS[task.type]}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              {TYPE_LABELS[task.type]} {t.taskBoard.task}
            </TooltipContent>
          </Tooltip>

          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium leading-tight">
              {task.name || task.id}
            </p>

            {/* Phase label for quests */}
            {task.phase && !compact && (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                {task.phase}
              </p>
            )}
          </div>
        </div>

        {/* Progress bar (only for running tasks with > 0%) */}
        {isRunning && task.progress_pct > 0 && (
          <div className="mt-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-muted-foreground">
                {t.taskBoard.progress}
              </span>
              <span className="text-xs font-medium text-warning">
                {Math.round(task.progress_pct)}%
              </span>
            </div>
            <Progress value={task.progress_pct} className="h-1.5" />
          </div>
        )}

        {/* Footer: status + duration */}
        <div className="mt-2 flex items-center justify-between gap-2">
          <StatusBadge status={task.status} />
          <span className="text-xs text-muted-foreground tabular-nums">
            {task.duration_ms > 0
              ? formatDurationMs(task.duration_ms)
              : formatRelativeTime(task.updated_at || task.created_at, {
                  justNow: t.taskBoard.justNow,
                  minutesAgo: t.taskBoard.minutesAgo,
                  hoursAgo: t.taskBoard.hoursAgo,
                  daysAgo: t.taskBoard.daysAgo,
                })}
          </span>
        </div>

        {/* Expanded detail */}
        {expanded && !compact && (
          <div className="mt-3 space-y-1.5 border-t pt-2 text-xs text-muted-foreground animate-in fade-in slide-in-from-top-1 duration-base">
            <div className="flex justify-between">
              <span>{t.taskBoard.type}</span>
              <span className="font-medium text-foreground">
                {TYPE_LABELS[task.type]}
              </span>
            </div>
            <div className="flex justify-between">
              <span>{t.taskBoard.status}</span>
              <span className="font-medium text-foreground">
                {STATUS_LABELS[task.status]}
              </span>
            </div>
            {task.phase && (
              <div className="flex justify-between">
                <span>{t.taskBoard.phase}</span>
                <span className="font-medium text-foreground">
                  {task.phase}
                </span>
              </div>
            )}
            {task.duration_ms > 0 && (
              <div className="flex justify-between">
                <span>{t.taskBoard.duration}</span>
                <span className="font-medium text-foreground tabular-nums">
                  {formatDurationMs(task.duration_ms)}
                </span>
              </div>
            )}
            {task.created_at && (
              <div className="flex justify-between">
                <span>{t.taskBoard.created}</span>
                <span className="font-medium text-foreground">
                  {formatRelativeTime(task.created_at, {
                    justNow: t.taskBoard.justNow,
                    minutesAgo: t.taskBoard.minutesAgo,
                    hoursAgo: t.taskBoard.hoursAgo,
                    daysAgo: t.taskBoard.daysAgo,
                  })}
                </span>
              </div>
            )}
            {task.error && (
              <div className="mt-1 rounded bg-destructive/10 px-2 py-1 text-destructive">
                {task.error}
              </div>
            )}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

export { StatusBadge, TYPE_ICONS, TYPE_COLORS, STATUS_STYLE };
