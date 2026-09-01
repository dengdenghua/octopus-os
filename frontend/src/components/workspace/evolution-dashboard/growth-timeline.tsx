import { motion } from "motion/react";
import {
  SparklesIcon,
  LightbulbIcon,
  TrophyIcon,
  AlertTriangleIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";

export type EventType = "skill" | "rule" | "achievement" | "warning";

export interface TimelineEvent {
  id: string;
  type: EventType;
  timestamp: string;
  title: string;
  description?: string;
  metadata?: {
    skillName?: string;
    skillLevel?: number;
    xpGain?: number;
    abilityUnlocked?: string;
    triggerTask?: string;
    impact?: string;
    achievementName?: string;
    achievementReward?: string;
  };
}

interface GrowthTimelineProps {
  events: TimelineEvent[];
  className?: string;
}

const EVENT_CONFIG = {
  skill: {
    icon: SparklesIcon,
    color: "text-primary",
    bgColor: "bg-primary/10",
    label: "获得新技能",
  },
  rule: {
    icon: LightbulbIcon,
    color: "text-warning",
    bgColor: "bg-warning/10",
    label: "领悟新规则",
  },
  achievement: {
    icon: TrophyIcon,
    color: "text-success",
    bgColor: "bg-success/10",
    label: "解锁成就",
  },
  warning: {
    icon: AlertTriangleIcon,
    color: "text-destructive",
    bgColor: "bg-destructive/10",
    label: "需要注意",
  },
};

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "刚刚";
  if (diffMins < 60) return `${diffMins} 分钟前`;
  if (diffHours < 24) return `${diffHours} 小时前`;
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (diffDays < 7) return `${diffDays} 天前`;

  return date.toLocaleDateString("zh-CN", {
    month: "short",
    day: "numeric",
  });
}

function TimelineEventCard({
  event,
  index,
}: {
  event: TimelineEvent;
  index: number;
}) {
  const config = EVENT_CONFIG[event.type];
  const Icon = config.icon;

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05, duration: 0.3 }}
      className="relative pl-8"
    >
      {/* Timeline Dot */}
      <div
        className={cn(
          "absolute left-0 top-2 flex size-6 items-center justify-center rounded-full",
          config.bgColor,
        )}
      >
        <Icon className={cn("size-3.5", config.color)} />
      </div>

      {/* Event Card */}
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className={cn("text-xs font-medium", config.color)}>
                {config.label}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatTimestamp(event.timestamp)}
              </span>
            </div>
            <h3 className="mt-1 font-semibold">{event.title}</h3>
            {event.description && (
              <p className="mt-1 text-sm text-muted-foreground">
                {event.description}
              </p>
            )}
          </div>
        </div>

        {/* Metadata */}
        {event.metadata && (
          <div className="mt-3 space-y-1 text-xs">
            {event.metadata.triggerTask && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">触发任务：</span>
                <span className="font-medium">
                  {event.metadata.triggerTask}
                </span>
              </div>
            )}
            {event.metadata.skillName && event.metadata.skillLevel && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">技能：</span>
                <span className="font-medium">
                  {event.metadata.skillName} 达到 Lv.{event.metadata.skillLevel}
                </span>
              </div>
            )}
            {event.metadata.xpGain && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">获得经验：</span>
                <span className="font-medium text-primary">
                  +{event.metadata.xpGain} XP
                </span>
              </div>
            )}
            {event.metadata.abilityUnlocked && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">解锁能力：</span>
                <span className="font-medium">
                  {event.metadata.abilityUnlocked}
                </span>
              </div>
            )}
            {event.metadata.impact && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">影响：</span>
                <span className="font-medium">{event.metadata.impact}</span>
              </div>
            )}
            {event.metadata.achievementName && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">成就：</span>
                <span className="font-medium">
                  {event.metadata.achievementName}
                </span>
              </div>
            )}
            {event.metadata.achievementReward && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">奖励：</span>
                <span className="font-medium text-success">
                  {event.metadata.achievementReward}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function groupEventsByDate(
  events: TimelineEvent[],
): Record<string, TimelineEvent[]> {
  const grouped: Record<string, TimelineEvent[]> = {};

  events.forEach((event) => {
    const date = new Date(event.timestamp);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    let dateKey: string;
    if (date.toDateString() === today.toDateString()) {
      dateKey = "今天";
    } else if (date.toDateString() === yesterday.toDateString()) {
      dateKey = "昨天";
    } else {
      dateKey = date.toLocaleDateString("zh-CN", {
        month: "long",
        day: "numeric",
      });
    }

    const dateEvents = (grouped[dateKey] ??= []);
    dateEvents.push(event);
  });

  return grouped;
}

export function GrowthTimeline({ events, className }: GrowthTimelineProps) {
  const [filter, setFilter] = useState<"all" | "today" | "week">("week");

  const filteredEvents = events.filter((event) => {
    if (filter === "all") return true;

    const eventDate = new Date(event.timestamp);
    const now = new Date();
    const diffMs = now.getTime() - eventDate.getTime();
    const diffDays = Math.floor(diffMs / 86400000);

    if (filter === "today") return diffDays === 0;
    if (filter === "week") return diffDays < 7;

    return true;
  });

  const groupedEvents = groupEventsByDate(filteredEvents);
  const dateKeys = Object.keys(groupedEvents);

  return (
    <div className={cn("space-y-6", className)}>
      {/* Header with Filter */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">成长日志</h2>
        <div className="flex gap-1 rounded-lg border border-border p-1">
          {(["today", "week", "all"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                filter === f
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {f === "today" ? "今天" : f === "week" ? "本周" : "全部"}
            </button>
          ))}
        </div>
      </div>

      {/* Timeline */}
      {filteredEvents.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-muted/30 p-8 text-center">
          <p className="text-sm text-muted-foreground">
            {filter === "today" ? "今天还没有成长记录" : "暂无成长记录"}
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {dateKeys.map((dateKey) => (
            <div key={dateKey}>
              {/* Date Header */}
              <div className="mb-4 flex items-center gap-3">
                <div className="h-px flex-1 bg-border" />
                <span className="text-sm font-medium text-muted-foreground">
                  {dateKey}
                </span>
                <div className="h-px flex-1 bg-border" />
              </div>

              {/* Events */}
              <div className="relative space-y-4">
                {/* Timeline Line */}
                <div className="absolute left-3 top-8 bottom-4 w-px bg-border" />

                {(groupedEvents[dateKey] ?? []).map((event, index) => (
                  <TimelineEventCard
                    key={event.id}
                    event={event}
                    index={index}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
