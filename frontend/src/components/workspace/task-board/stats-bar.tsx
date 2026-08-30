/**
 * StatsBar -- top summary bar showing task board aggregate statistics.
 *
 * Displays: Total tasks, Running, Success Rate, Average Duration.
 * Each stat is a compact card with an icon and value.
 */

import {
  ActivityIcon,
  ClockIcon,
  LayersIcon,
  TrendingUpIcon,
} from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import { Skeleton as _Skeleton } from "@/components/ui/skeleton";
import type { TaskBoardStats } from "@/core/task-board/types";
import { cn } from "@/lib/utils";

import { formatDurationMs } from "./task-card";

// ---------------------------------------------------------------------------
// Mini sparkline component (shows recent activity as tiny bars)
// ---------------------------------------------------------------------------

function MiniSparkline({
  data,
  className,
  color = "bg-primary/40",
}: {
  data: number[];
  className?: string;
  color?: string;
}) {
  const max = Math.max(...data, 1);
  return (
    <div className={cn("flex items-end gap-px", className)}>
      {data.map((value, i) => (
        <div
          key={i}
          className={cn(
            "w-1 min-h-[2px] rounded-lg transition-colors duration-slow",
            color,
          )}
          style={{ height: `${Math.max(2, (value / max) * 20)}px` }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  label,
  value,
  subValue,
  iconColor,
  sparkline,
  sparklineColor,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  subValue?: string;
  iconColor?: string;
  sparkline?: number[];
  sparklineColor?: string;
}) {
  return (
    <div className="ui-dense-row flex items-center gap-3 rounded-lg border bg-card transition-colors hover:bg-accent/30">
      <div
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/5",
          iconColor,
        )}
      >
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <div className="flex items-baseline gap-2">
          <p className="text-lg font-semibold tabular-nums leading-tight">
            {value}
          </p>
          {subValue && (
            <span className="text-xs text-muted-foreground">
              {subValue}
            </span>
          )}
        </div>
      </div>
      {sparkline && sparkline.length > 0 && (
        <MiniSparkline data={sparkline} color={sparklineColor} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatsBar
// ---------------------------------------------------------------------------

export function StatsBar({
  stats,
  loading,
}: {
  stats: TaskBoardStats;
  loading?: boolean;
}) {
  const { t } = useI18n();
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-[72px] border"
          />
        ))}
      </div>
    );
  }

  // Build a pseudo-sparkline from the by_type distribution
  const typeSparkline = [
    stats.by_type.background ?? 0,
    stats.by_type.quest ?? 0,
    stats.by_type.scheduled ?? 0,
    stats.by_type.intelligence ?? 0,
  ];

  // Build a pseudo-sparkline from the status distribution
  const statusSparkline = [
    stats.by_status.queued ?? 0,
    stats.by_status.running ?? 0,
    stats.by_status.completed ?? 0,
    stats.by_status.failed ?? 0,
  ];

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        icon={<LayersIcon className="size-4 text-primary" />}
        label={t.taskBoard.totalTasks}
        value={stats.total}
        subValue={
          Object.keys(stats.by_type).length > 0
            ? `${Object.keys(stats.by_type).length} ${t.taskBoard.types}`
            : undefined
        }
        sparkline={typeSparkline}
        sparklineColor="bg-primary/50"
      />

      <StatCard
        icon={<ActivityIcon className="size-4 text-warning" />}
        iconColor="bg-warning/10"
        label={t.taskBoard.running}
        value={stats.running_count}
        subValue={
          stats.queued_count > 0
            ? `+${stats.queued_count} ${t.taskBoard.queued.toLowerCase()}`
            : undefined
        }
        sparkline={statusSparkline}
        sparklineColor="bg-warning/50"
      />

      <StatCard
        icon={<TrendingUpIcon className="size-4 text-success" />}
        iconColor="bg-success/10"
        label={t.taskBoard.successRate}
        value={stats.total > 0 ? `${Math.round(stats.success_rate)}%` : "--"}
        subValue={
          stats.by_status.completed
            ? `${stats.by_status.completed} ${t.taskBoard.completed.toLowerCase()}`
            : undefined
        }
      />

      <StatCard
        icon={<ClockIcon className="size-4 text-info" />}
        iconColor="bg-info/10"
        label={t.taskBoard.avgDuration}
        value={
          stats.avg_duration_ms > 0
            ? formatDurationMs(stats.avg_duration_ms)
            : "--"
        }
        subValue={stats.total > 0 ? t.taskBoard.across(stats.total) : undefined}
      />
    </div>
  );
}
