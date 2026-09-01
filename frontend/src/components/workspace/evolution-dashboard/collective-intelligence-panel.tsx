import {
  BrainCircuitIcon,
  TrendingUpIcon,
  DatabaseIcon,
  SparklesIcon,
  ChevronRightIcon
} from "lucide-react";
import { cn } from "@/lib/utils";

interface CollectiveStats {
  healthScore: number;
  sharedRules: number;
  sharedFacts: number;
  autoSkills: number;
  recentOptimizations: Array<{
    id: string;
    type: "rule" | "skill" | "optimization";
    title: string;
    timestamp: string;
    impact?: string;
  }>;
}

interface CollectiveIntelligencePanelProps {
  stats: CollectiveStats;
  onViewDetails?: () => void;
  className?: string;
}

function HealthGauge({ score }: { score: number }) {
  const getColor = (score: number) => {
    if (score >= 80) return "text-success";
    if (score >= 60) return "text-warning";
    return "text-destructive";
  };

  const getLabel = (score: number) => {
    if (score >= 80) return "优秀";
    if (score >= 60) return "良好";
    return "需改进";
  };

  return (
    <div className="flex items-center gap-4">
      <div className="relative size-24">
        {/* Background circle */}
        <svg className="size-24 -rotate-90" viewBox="0 0 100 100">
          <circle
            cx="50"
            cy="50"
            r="40"
            fill="none"
            stroke="currentColor"
            strokeWidth="8"
            className="text-muted"
            opacity="0.2"
          />
          {/* Progress circle */}
          <circle
            cx="50"
            cy="50"
            r="40"
            fill="none"
            stroke="currentColor"
            strokeWidth="8"
            strokeLinecap="round"
            className={getColor(score)}
            strokeDasharray={`${(score / 100) * 251.2} 251.2`}
            style={{ transition: "stroke-dasharray 1s ease-out" }}
          />
        </svg>
        {/* Center text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className={cn("text-2xl font-bold tabular-nums", getColor(score))}>
            {score}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {getLabel(score)}
          </div>
        </div>
      </div>
      <div>
        <div className="text-sm font-medium">系统健康度</div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          综合评估所有角色的表现
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  subtitle
}: {
  icon: typeof DatabaseIcon;
  label: string;
  value: number | string;
  subtitle?: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card/60 p-3">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
        <Icon className="size-5 text-primary" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-0.5 text-lg font-bold tabular-nums">{value}</div>
        {subtitle && (
          <div className="mt-0.5 text-[10px] text-muted-foreground">{subtitle}</div>
        )}
      </div>
    </div>
  );
}

function OptimizationItem({
  optimization
}: {
  optimization: CollectiveStats["recentOptimizations"][0]
}) {
  const getIcon = () => {
    switch (optimization.type) {
      case "rule": return "💡";
      case "skill": return "✨";
      case "optimization": return "⚡";
      default: return "📌";
    }
  };

  const getTypeLabel = () => {
    switch (optimization.type) {
      case "rule": return "新规则";
      case "skill": return "新技能";
      case "optimization": return "优化";
      default: return "更新";
    }
  };

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - date.getTime()) / 86400000);

    if (diffDays === 0) return "今天";
    if (diffDays === 1) return "昨天";
    if (diffDays < 7) return `${diffDays}天前`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)}周前`;
    return `${Math.floor(diffDays / 30)}月前`;
  };

  return (
    <div className="flex items-start gap-2 rounded-md bg-muted/30 p-2">
      <span className="text-base" aria-hidden="true">{getIcon()}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-primary">{getTypeLabel()}</span>
          <span className="text-xs text-muted-foreground">{formatTime(optimization.timestamp)}</span>
        </div>
        <div className="mt-0.5 text-sm font-medium">{optimization.title}</div>
        {optimization.impact && (
          <div className="mt-0.5 text-xs text-muted-foreground">{optimization.impact}</div>
        )}
      </div>
    </div>
  );
}

export function CollectiveIntelligencePanel({
  stats,
  onViewDetails,
  className
}: CollectiveIntelligencePanelProps) {
  return (
    <div className={cn("space-y-4", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BrainCircuitIcon className="size-5 text-primary" />
          <h2 className="text-base font-semibold">🌐 群体智能</h2>
        </div>
        <span className="text-xs text-muted-foreground">
          所有角色共享的基础能力
        </span>
      </div>

      {/* Main Panel */}
      <div className="rounded-xl border border-border bg-gradient-to-br from-primary/5 via-background to-background p-5">
        {/* Health Score */}
        <HealthGauge score={stats.healthScore} />

        {/* Stats Grid */}
        <div className="mt-5 grid grid-cols-3 gap-3">
          <StatCard
            icon={DatabaseIcon}
            label="共享规则"
            value={stats.sharedRules}
            subtitle="通用准则"
          />
          <StatCard
            icon={DatabaseIcon}
            label="知识事实"
            value={stats.sharedFacts}
            subtitle="累积知识"
          />
          <StatCard
            icon={SparklesIcon}
            label="自动技能"
            value={stats.autoSkills}
            subtitle="系统提取"
          />
        </div>

        {/* Recent Optimizations */}
        {stats.recentOptimizations.length > 0 && (
          <div className="mt-5">
            <div className="mb-2 flex items-center gap-2">
              <TrendingUpIcon className="size-4 text-muted-foreground" />
              <h3 className="text-sm font-medium">最近系统优化</h3>
            </div>
            <div className="space-y-2">
              {stats.recentOptimizations.slice(0, 3).map((opt) => (
                <OptimizationItem key={opt.id} optimization={opt} />
              ))}
            </div>
          </div>
        )}

        {/* View Details Button */}
        {onViewDetails && (
          <button
            type="button"
            onClick={onViewDetails}
            className="mt-4 flex w-full items-center justify-center gap-1 rounded-lg border border-border bg-background/60 py-2 text-sm font-medium transition-colors hover:bg-muted"
          >
            查看详细数据
            <ChevronRightIcon className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}
