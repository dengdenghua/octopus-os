import { motion } from "motion/react";
import { ChevronRightIcon, SparklesIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface AgentCard {
  id: string;
  name: string;
  icon: string;
  level: number;
  stars: number;
  skillCount: number;
  achievementCount: number;
  successRate: number;
  xpProgress: number; // 0-100
  isActive?: boolean;
}

interface AgentGridProps {
  agents: AgentCard[];
  onSelectAgent?: (agentId: string) => void;
  selectedAgentId?: string | null;
  className?: string;
}

function StarRating({ stars, max = 5 }: { stars: number; max?: number }) {
  return (
    <div className="flex gap-0.5">
      {Array.from({ length: max }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "text-xs",
            i < stars ? "text-yellow-500" : "text-muted-foreground/30"
          )}
        >
          ⭐
        </span>
      ))}
    </div>
  );
}

function AgentCardItem({
  agent,
  selected,
  onClick,
  index
}: {
  agent: AgentCard;
  selected: boolean;
  onClick: () => void;
  index: number;
}) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.3 }}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      className={cn(
        "group relative overflow-hidden rounded-xl border p-4 text-left transition-all",
        selected
          ? "border-primary bg-primary/5 shadow-lg"
          : "border-border bg-card hover:border-primary/50 hover:shadow-md"
      )}
    >
      {/* Active Badge */}
      {agent.isActive && (
        <div className="absolute right-2 top-2">
          <div className="flex items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
            <div className="size-1.5 rounded-full bg-success animate-pulse" />
            活跃
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary/20 to-primary/5 text-2xl">
          {agent.icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-semibold">{agent.name}</h3>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-sm font-medium text-primary">
              Lv.{agent.level}
            </span>
            <StarRating stars={agent.stars} />
          </div>
        </div>
      </div>

      {/* XP Progress Bar */}
      <div className="mt-3">
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <motion.div
            className="h-full bg-gradient-to-r from-primary via-primary/90 to-primary/80"
            initial={{ width: 0 }}
            animate={{ width: `${agent.xpProgress}%` }}
            transition={{ duration: 0.8, ease: "easeOut" }}
          />
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {agent.xpProgress}% → Lv.{agent.level + 1}
        </div>
      </div>

      {/* Stats */}
      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-xs text-muted-foreground">技能</div>
          <div className="mt-0.5 text-sm font-semibold">{agent.skillCount}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">成就</div>
          <div className="mt-0.5 text-sm font-semibold">{agent.achievementCount}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">成功率</div>
          <div className={cn(
            "mt-0.5 text-sm font-semibold",
            agent.successRate >= 80 ? "text-success" :
            agent.successRate >= 60 ? "text-warning" : "text-destructive"
          )}>
            {agent.successRate}%
          </div>
        </div>
      </div>

      {/* View Details Indicator */}
      <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-3">
        <span className="text-xs text-muted-foreground">
          点击查看详情
        </span>
        <ChevronRightIcon
          className={cn(
            "size-4 text-muted-foreground transition-transform",
            "group-hover:translate-x-1"
          )}
        />
      </div>

      {/* Selected Indicator */}
      {selected && (
        <div className="absolute inset-0 rounded-xl border-2 border-primary pointer-events-none" />
      )}
    </motion.button>
  );
}

export function AgentGrid({
  agents,
  onSelectAgent,
  selectedAgentId,
  className
}: AgentGridProps) {
  const totalLevel = agents.reduce((sum, agent) => sum + agent.level, 0);
  const avgSuccessRate = agents.length > 0
    ? Math.round(agents.reduce((sum, agent) => sum + agent.successRate, 0) / agents.length)
    : 0;

  return (
    <div className={cn("space-y-4", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <SparklesIcon className="size-5 text-primary" />
          <h2 className="text-base font-semibold">👤 我的角色</h2>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span>总等级: {totalLevel}</span>
          <span>平均成功率: {avgSuccessRate}%</span>
        </div>
      </div>

      {/* Empty State */}
      {agents.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/30 p-12 text-center">
          <div className="mx-auto mb-3 flex size-16 items-center justify-center rounded-full bg-muted text-3xl">
            🤖
          </div>
          <h3 className="text-sm font-medium">还没有角色</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            创建第一个 Hub 角色开始你的成长之旅
          </p>
        </div>
      ) : (
        /* Agent Cards Grid */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {agents.map((agent, index) => (
            <AgentCardItem
              key={agent.id}
              agent={agent}
              selected={selectedAgentId === agent.id}
              onClick={() => onSelectAgent?.(agent.id)}
              index={index}
            />
          ))}
        </div>
      )}
    </div>
  );
}
