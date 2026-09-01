import { motion } from "motion/react";
import { TrendingUpIcon, TrendingDownIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { AbilityRadarChart, type RadarDataPoint } from "./ability-radar-chart";

export interface CharacterStats {
  level: number;
  xp: number;
  xpToNextLevel: number;
  stars: number;
  successRate: number;
  successRateDelta: number;
  skillMastery: number;
  skillMasteryDelta: number;
  memoryCount: number;
  memoryCountDelta: number;
  abilityScores?: RadarDataPoint[]; // 新增：能力雷达图数据
}

export interface Achievement {
  id: string;
  name: string;
  icon: string;
  unlockedAt?: string;
}

export interface CharacterCardProps {
  name: string;
  stats: CharacterStats;
  recentAchievements: Achievement[];
  className?: string;
}

function StarRating({ stars, max = 5 }: { stars: number; max?: number }) {
  return (
    <div className="flex gap-0.5">
      {Array.from({ length: max }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "text-sm",
            i < stars ? "text-yellow-500" : "text-muted-foreground/30",
          )}
        >
          ⭐
        </span>
      ))}
    </div>
  );
}

function XPBar({ current, max }: { current: number; max: number }) {
  const percentage = Math.min((current / max) * 100, 100);

  return (
    <div className="relative">
      <div className="h-3 overflow-hidden rounded-full bg-muted">
        <motion.div
          className="h-full bg-gradient-to-r from-primary via-primary/90 to-primary/80"
          initial={{ width: 0 }}
          animate={{ width: `${percentage}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </div>
      <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {current} / {max} XP
        </span>
        <span>
          {percentage.toFixed(0)}% → Lv.{Math.floor(current / max) + 1}
        </span>
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  delta,
  unit = "",
}: {
  icon: string;
  label: string;
  value: number | string;
  delta?: number;
  unit?: string;
}) {
  const hasDelta = typeof delta === "number" && delta !== 0;
  const isPositive = (delta ?? 0) > 0;

  return (
    <div className="rounded-lg border border-border bg-card/60 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xl" aria-hidden="true">
          {icon}
        </span>
        {hasDelta && (
          <div
            className={cn(
              "flex items-center gap-0.5 text-xs font-medium",
              isPositive ? "text-success" : "text-destructive",
            )}
          >
            {isPositive ? (
              <TrendingUpIcon className="size-3" />
            ) : (
              <TrendingDownIcon className="size-3" />
            )}
            {isPositive ? "+" : ""}
            {typeof delta === "number" && delta % 1 === 0
              ? delta
              : delta?.toFixed(1)}
            {unit}
          </div>
        )}
      </div>
      <div className="mt-2 text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-lg font-bold tabular-nums">
        {value}
        {unit}
      </div>
    </div>
  );
}

function AchievementBadge({ achievement }: { achievement: Achievement }) {
  return (
    <motion.div
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", bounce: 0.4 }}
      className="flex items-center gap-1.5 rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-xs"
      title={achievement.name}
    >
      <span className="text-base" aria-hidden="true">
        {achievement.icon}
      </span>
      <span className="font-medium text-foreground">{achievement.name}</span>
    </motion.div>
  );
}

export function CharacterCard({
  name,
  stats,
  recentAchievements,
  className,
}: CharacterCardProps) {
  const tasksToNextLevel = stats.xpToNextLevel - stats.xp;

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-gradient-to-br from-primary/10 via-background to-background p-5 shadow-sm",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-2xl" aria-hidden="true">
              🤖
            </span>
            <h2 className="text-lg font-bold">{name}</h2>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-sm font-medium text-primary">
              Lv.{stats.level}
            </span>
            <StarRating stars={stats.stars} />
          </div>
        </div>
      </div>

      {/* XP Progress */}
      <div className="mt-4">
        <XPBar current={stats.xp} max={stats.xpToNextLevel} />
        <p className="mt-1 text-xs text-muted-foreground">
          需要完成 {tasksToNextLevel} 个任务升级
        </p>
      </div>

      {/* Stats Grid */}
      <div className="mt-5">
        <h3 className="mb-2 text-sm font-semibold">💪 能力值</h3>
        <div className="grid grid-cols-3 gap-2">
          <StatCard
            icon="🎯"
            label="任务成功率"
            value={stats.successRate}
            delta={stats.successRateDelta}
            unit="%"
          />
          <StatCard
            icon="📚"
            label="技能掌握度"
            value={`Lv.${stats.skillMastery}`}
            delta={stats.skillMasteryDelta}
          />
          <StatCard
            icon="🧠"
            label="记忆容量"
            value={stats.memoryCount}
            delta={stats.memoryCountDelta}
            unit=" 条"
          />
        </div>
      </div>

      {/* Ability Radar Chart */}
      {stats.abilityScores && stats.abilityScores.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-3 text-sm font-semibold">📊 能力雷达</h3>
          <div className="flex justify-center">
            <AbilityRadarChart data={stats.abilityScores} size={240} />
          </div>
        </div>
      )}

      {/* Recent Achievements */}
      {recentAchievements.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-2 text-sm font-semibold">🏆 最近成就（本周）</h3>
          <div className="flex flex-wrap gap-2">
            {recentAchievements.map((achievement) => (
              <AchievementBadge
                key={achievement.id}
                achievement={achievement}
              />
            ))}
            {recentAchievements.length > 3 && (
              <button
                type="button"
                className="rounded-md border border-border bg-muted/50 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
              >
                +{recentAchievements.length - 3} 更多
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
