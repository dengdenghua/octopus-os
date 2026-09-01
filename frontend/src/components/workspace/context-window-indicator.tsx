import { BrainIcon, HistoryIcon, WrenchIcon, MonitorIcon } from "lucide-react";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface ContextBucket {
  name: string;
  used: number;
  allocated: number;
  icon: React.ElementType;
  color: string;
}

interface ContextWindowIndicatorProps {
  totalBudget: number;
  totalUsed: number;
  buckets: {
    system: { used: number; allocated: number };
    suckers: { used: number; allocated: number };
    memory: { used: number; allocated: number };
    history: { used: number; allocated: number };
  };
  className?: string;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

function BucketBar({
  bucket,
  totalBudget: _totalBudget,
}: {
  bucket: ContextBucket;
  totalBudget: number;
}) {
  const Icon = bucket.icon;
  const usedPct =
    bucket.allocated > 0
      ? Math.min(100, (bucket.used / bucket.allocated) * 100)
      : 0;
  const overflow = bucket.used > bucket.allocated;

  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icon className={cn("size-3", bucket.color)} />
          <span className="text-xs text-muted-foreground/70">
            {bucket.name}
          </span>
        </div>
        <span
          className={cn(
            "text-xs font-mono tabular-nums",
            overflow
              ? "text-warning"
              : "text-muted-foreground/50",
          )}
        >
          {formatTokens(bucket.used)}/{formatTokens(bucket.allocated)}
        </span>
      </div>
      <div className="h-1 rounded-full bg-muted/50 overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-slow",
            overflow ? "bg-warning" : bucket.color.replace("text-", "bg-"),
          )}
          style={{ width: `${usedPct}%` }}
        />
      </div>
    </div>
  );
}

export function ContextWindowIndicator({
  totalBudget,
  totalUsed,
  buckets,
  className,
}: ContextWindowIndicatorProps) {
  const { t } = useI18n();
  const utilization = totalBudget > 0 ? totalUsed / totalBudget : 0;
  const isHigh = utilization > 0.8;
  const isMedium = utilization > 0.5;

  const bucketList: ContextBucket[] = [
    {
      name: t.contextWindow?.system,
      used: buckets.system.used,
      allocated: buckets.system.allocated,
      icon: MonitorIcon,
      color: "text-info",
    },
    {
      name: t.contextWindow?.tools,
      used: buckets.suckers.used,
      allocated: buckets.suckers.allocated,
      icon: WrenchIcon,
      color: "text-chart-7",
    },
    {
      name: t.contextWindow?.memory,
      used: buckets.memory.used,
      allocated: buckets.memory.allocated,
      icon: BrainIcon,
      color: "text-chart-1",
    },
    {
      name: t.contextWindow?.history,
      used: buckets.history.used,
      allocated: buckets.history.allocated,
      icon: HistoryIcon,
      color: "text-success",
    },
  ];

  return (
    <div className={cn("space-y-2 px-3 py-2", className)}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground/70">
          {t.contextWindow?.title}
        </span>
        <span
          className={cn(
            "text-xs font-mono tabular-nums",
            isHigh
              ? "text-destructive"
              : isMedium
                ? "text-warning"
                : "text-muted-foreground/50",
          )}
        >
          {formatTokens(totalUsed)}/{formatTokens(totalBudget)}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-slow",
            isHigh ? "bg-destructive" : isMedium ? "bg-warning" : "bg-primary/60",
          )}
          style={{ width: `${Math.min(100, utilization * 100)}%` }}
        />
      </div>
      <div className="space-y-1.5">
        {bucketList.map((b) => (
          <BucketBar key={b.name} bucket={b} totalBudget={totalBudget} />
        ))}
      </div>
    </div>
  );
}
