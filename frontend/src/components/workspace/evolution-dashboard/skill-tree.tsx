import { motion } from "motion/react";
import { LockIcon, CheckCircleIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";

export interface Skill {
  id: string;
  name: string;
  icon: string;
  level: number;
  maxLevel: number;
  xp: number;
  xpToNextLevel: number;
  usageCount: number;
  successRate: number;
  unlocked: boolean;
  dependencies?: string[];
  nextLevelAbilities?: string[];
}

export interface SkillTreeProps {
  skills: Skill[];
  onSkillClick?: (skillId: string) => void;
  className?: string;
}

function SkillNode({
  skill,
  selected,
  onClick,
}: {
  skill: Skill;
  selected: boolean;
  onClick: () => void;
}) {
  const progressPercent = (skill.xp / skill.xpToNextLevel) * 100;
  const isMaxLevel = skill.level >= skill.maxLevel;

  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      className={cn(
        "relative flex flex-col items-center gap-2 rounded-lg border p-3 transition-colors",
        skill.unlocked
          ? selected
            ? "border-primary bg-primary/10"
            : "border-border bg-card hover:bg-muted/50"
          : "border-border-subtle bg-muted/30 opacity-60",
      )}
    >
      {/* Icon */}
      <div
        className={cn(
          "flex size-12 items-center justify-center rounded-full text-2xl",
          skill.unlocked ? "bg-primary/10" : "bg-muted-foreground/10",
        )}
      >
        {skill.unlocked ? (
          skill.icon
        ) : (
          <LockIcon className="size-5 text-muted-foreground" />
        )}
      </div>

      {/* Name & Level */}
      <div className="text-center">
        <div className="text-sm font-medium">{skill.name}</div>
        {skill.unlocked && (
          <div className="mt-0.5 text-xs text-muted-foreground">
            Lv.{skill.level}
            {!isMaxLevel && ` / ${skill.maxLevel}`}
          </div>
        )}
      </div>

      {/* XP Bar */}
      {skill.unlocked && !isMaxLevel && (
        <div className="w-full">
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      )}

      {/* Max Level Badge */}
      {isMaxLevel && (
        <div className="absolute -right-1 -top-1 flex size-5 items-center justify-center rounded-full bg-success text-white">
          <CheckCircleIcon className="size-3" />
        </div>
      )}
    </motion.button>
  );
}

function SkillDetail({ skill }: { skill: Skill }) {
  const progressPercent = (skill.xp / skill.xpToNextLevel) * 100;
  const isMaxLevel = skill.level >= skill.maxLevel;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-lg border border-border bg-card p-4"
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <span className="text-3xl" aria-hidden="true">
            {skill.icon}
          </span>
          <div>
            <h3 className="text-base font-semibold">{skill.name}</h3>
            <div className="mt-0.5 text-sm text-muted-foreground">
              Lv.{skill.level}
              {!isMaxLevel && ` / ${skill.maxLevel}`}
            </div>
          </div>
        </div>
        <button
          type="button"
          className="rounded-md border border-border bg-background px-3 py-1 text-xs hover:bg-muted"
        >
          查看详情
        </button>
      </div>

      <div className="mt-4 h-px bg-border" />

      {/* Stats */}
      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-muted-foreground">使用次数</div>
          <div className="mt-1 text-lg font-semibold">
            {skill.usageCount} 次
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">成功率</div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-lg font-semibold">{skill.successRate}%</span>
            <div className="flex-1">
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full transition-all",
                    skill.successRate >= 80
                      ? "bg-success"
                      : skill.successRate >= 50
                        ? "bg-warning"
                        : "bg-destructive",
                  )}
                  style={{ width: `${skill.successRate}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* XP Progress */}
      {!isMaxLevel && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>经验值</span>
            <span>
              {skill.xp} / {skill.xpToNextLevel}
            </span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
            <motion.div
              className="h-full bg-gradient-to-r from-primary via-primary/90 to-primary/80"
              initial={{ width: 0 }}
              animate={{ width: `${progressPercent}%` }}
              transition={{ duration: 0.5 }}
            />
          </div>
        </div>
      )}

      {/* Upgrade Requirements */}
      {!isMaxLevel && (
        <div className="mt-4 rounded-md bg-muted/50 p-3">
          <div className="text-xs font-medium">
            升级到 Lv.{skill.level + 1} 需要：
          </div>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            <li>• 再完成 {skill.xpToNextLevel - skill.xp} 次任务</li>
            <li>• 成功率保持在 80% 以上</li>
          </ul>
          {skill.nextLevelAbilities && skill.nextLevelAbilities.length > 0 && (
            <>
              <div className="mt-3 text-xs font-medium">解锁新能力：</div>
              <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                {skill.nextLevelAbilities.map((ability, i) => (
                  <li key={i}>• {ability}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {/* Max Level Message */}
      {isMaxLevel && (
        <div className="mt-4 flex items-center gap-2 rounded-md bg-success/10 p-3 text-xs text-success">
          <CheckCircleIcon className="size-4" />
          <span>已达到最高等级！</span>
        </div>
      )}
    </motion.div>
  );
}

export function SkillTree({ skills, onSkillClick, className }: SkillTreeProps) {
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const selectedSkill = skills.find((s) => s.id === selectedSkillId);
  const unlockedCount = skills.filter((s) => s.unlocked).length;

  const handleSkillClick = (skillId: string) => {
    setSelectedSkillId(skillId);
    onSkillClick?.(skillId);
  };

  return (
    <div className={cn("space-y-4", className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">技能树</h2>
        <span className="text-sm text-muted-foreground">
          已解锁: {unlockedCount}/{skills.length}
        </span>
      </div>

      {/* Skill Grid */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        {skills.map((skill) => (
          <SkillNode
            key={skill.id}
            skill={skill}
            selected={selectedSkillId === skill.id}
            onClick={() => handleSkillClick(skill.id)}
          />
        ))}
      </div>

      {/* Selected Skill Detail */}
      {selectedSkill && <SkillDetail skill={selectedSkill} />}
    </div>
  );
}
