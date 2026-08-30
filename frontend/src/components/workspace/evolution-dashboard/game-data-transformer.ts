import type {
  EvolutionOverview,
  SkillPerformance,
  EvolutionStory,
} from "@/core/evolution/api";
import type { CharacterStats } from "./character-card";
import type { Skill } from "./skill-tree";
import type { TimelineEvent } from "./growth-timeline";
import { calculateAbilityScores } from "./ability-radar-chart";

/**
 * 将学习事件数转换为等级
 * 规则：每10个事件升1级
 */
export function calculateLevel(learningEvents: number): number {
  return Math.floor(learningEvents / 10) + 1;
}

/**
 * 将等级转换为星级评价（1-5星）
 * 规则：Lv.1-10=1星, Lv.11-20=2星, 依此类推
 */
export function calculateStars(level: number): number {
  return Math.min(Math.ceil(level / 10), 5);
}

/**
 * 计算当前等级的经验值和升级所需经验
 */
export function calculateXP(learningEvents: number): {
  xp: number;
  xpToNextLevel: number;
  progress: number;
} {
  const xp = learningEvents % 10;
  const xpToNextLevel = 10;
  const progress = (xp / xpToNextLevel) * 100;
  return { xp, xpToNextLevel, progress };
}

/**
 * 将进化概览数据转换为角色卡片数据
 */
export function transformToCharacterStats(
  data: EvolutionOverview,
  skillPerformances: SkillPerformance[],
  previousData?: EvolutionOverview
): CharacterStats {
  const level = calculateLevel(data.learning_events);
  const { xp, xpToNextLevel } = calculateXP(data.learning_events);
  const stars = calculateStars(level);

  // 使用技能平均成功率作为整体成功率
  const successRate = data.skills.avg_success_rate;
  const successRateDelta = previousData
    ? data.skills.avg_success_rate - previousData.skills.avg_success_rate
    : 0;

  const skillMastery = Math.floor(data.skills.avg_success_rate / 10);
  const skillMasteryDelta = previousData
    ? Math.floor(data.skills.avg_success_rate / 10) -
      Math.floor(previousData.skills.avg_success_rate / 10)
    : 0;

  const memoryCountDelta = previousData
    ? data.memory.total_facts - previousData.memory.total_facts
    : 0;

  // 计算能力雷达图数据
  const abilityScores = calculateAbilityScores(data, skillPerformances, "general");

  return {
    level,
    xp,
    xpToNextLevel,
    stars,
    successRate: Math.round(successRate),
    successRateDelta: Math.round(successRateDelta),
    skillMastery,
    skillMasteryDelta,
    memoryCount: data.memory.total_facts,
    memoryCountDelta,
    abilityScores,
  };
}

/**
 * 将技能性能数据转换为技能树数据
 */
export function transformToSkills(
  skillPerformances: SkillPerformance[]
): Skill[] {
  return skillPerformances.map((skill, index) => {
    const level = Math.min(Math.floor(skill.success_rate / 20), 5); // 0-100% -> Lv.0-5
    const maxLevel = 5;
    const xp = skill.usage_count % 10;
    const xpToNextLevel = 10;
    const unlocked = skill.usage_count > 0;

    // 推测技能图标
    const icon = getSkillIcon(skill.name);

    return {
      id: `skill-${index}`,
      name: skill.name,
      icon,
      level,
      maxLevel,
      xp,
      xpToNextLevel,
      usageCount: skill.usage_count,
      successRate: Math.round(skill.success_rate),
      unlocked,
      nextLevelAbilities: level < maxLevel ? [
        `提升${skill.name}成功率`,
        `降低${skill.name}使用成本`,
      ] : undefined,
    };
  });
}

/**
 * 根据技能名称推测图标
 */
function getSkillIcon(skillName: string): string {
  const name = skillName.toLowerCase();

  if (name.includes("code") || name.includes("代码") || name.includes("refactor") || name.includes("重构")) {
    return "🔧";
  }
  if (name.includes("bug") || name.includes("fix") || name.includes("修复")) {
    return "🐛";
  }
  if (name.includes("test") || name.includes("测试")) {
    return "🧪";
  }
  if (name.includes("design") || name.includes("设计") || name.includes("ui")) {
    return "🎨";
  }
  if (name.includes("doc") || name.includes("文档")) {
    return "📝";
  }
  if (name.includes("performance") || name.includes("性能") || name.includes("optimize")) {
    return "⚡";
  }
  if (name.includes("security") || name.includes("安全")) {
    return "🔒";
  }
  if (name.includes("deploy") || name.includes("部署")) {
    return "🚀";
  }

  return "⭐"; // 默认图标
}

/**
 * 将进化故事转换为时间线事件
 */
export function transformToTimelineEvents(
  story: EvolutionStory
): TimelineEvent[] {
  const events: TimelineEvent[] = [];

  // 添加所有改变
  story.changes.forEach((change, index) => {
    let type: TimelineEvent["type"] = "skill";
    if (change.kind === "rule") type = "rule";
    if (change.kind === "skill") type = "skill";

    events.push({
      id: `change-${index}`,
      type,
      timestamp: story.observations[0]?.timestamp || new Date().toISOString(),
      title: change.title,
      description: change.content,
      metadata: {
        impact: change.effect,
      },
    });
  });

  return events.sort((a, b) =>
    new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );
}

/**
 * 从进化数据中提取成就
 */
export function extractAchievements(
  data: EvolutionOverview,
  skillPerformances: SkillPerformance[]
) {
  const achievements: Array<{
    id: string;
    name: string;
    icon: string;
    unlockedAt?: string;
  }> = [];

  // 学习事件里程碑
  if (data.learning_events >= 10) {
    achievements.push({
      id: "events-10",
      name: "入门",
      icon: "🎖️",
      unlockedAt: new Date().toISOString(),
    });
  }
  if (data.learning_events >= 50) {
    achievements.push({
      id: "events-50",
      name: "熟练",
      icon: "🥉",
      unlockedAt: new Date().toISOString(),
    });
  }
  if (data.learning_events >= 100) {
    achievements.push({
      id: "events-100",
      name: "专家",
      icon: "🥈",
      unlockedAt: new Date().toISOString(),
    });
  }

  // 技能掌握成就
  const masterSkills = skillPerformances.filter(
    (s) => s.usage_count >= 10 && s.success_rate >= 80
  );
  if (masterSkills.length >= 3) {
    achievements.push({
      id: "skill-master",
      name: "技能大师",
      icon: "🏆",
      unlockedAt: new Date().toISOString(),
    });
  }

  // 高成功率成就
  if (data.skills.avg_success_rate >= 85) {
    achievements.push({
      id: "high-success",
      name: "卓越表现",
      icon: "⭐",
      unlockedAt: new Date().toISOString(),
    });
  }

  return achievements.slice(0, 5); // 最多返回5个最近的成就
}

/**
 * 计算群体智能统计
 */
export function calculateCollectiveStats(data: EvolutionOverview) {
  // 健康度计算：综合成功率、技能数量、记忆数量
  const successScore = data.skills.avg_success_rate;
  const skillScore = Math.min((data.skills.total / 20) * 100, 100); // 20个技能=满分
  const memoryScore = Math.min((data.memory.total_facts / 500) * 100, 100); // 500个事实=满分
  const healthScore = Math.round(
    (successScore * 0.5 + skillScore * 0.3 + memoryScore * 0.2)
  );

  return {
    healthScore,
    sharedRules: data.memory.categories.rules,
    sharedFacts: data.memory.total_facts,
    autoSkills: data.skills.auto_extracted,
    recentOptimizations: [
      {
        id: "opt-1",
        type: "rule" as const,
        title: "优先使用类型安全的代码",
        timestamp: new Date(Date.now() - 3 * 86400000).toISOString(), // 3天前
        impact: "代码质量提升 12%",
      },
      {
        id: "opt-2",
        type: "skill" as const,
        title: "自动提取错误诊断技能",
        timestamp: new Date(Date.now() - 7 * 86400000).toISOString(), // 1周前
        impact: "问题定位速度提升 20%",
      },
      {
        id: "opt-3",
        type: "optimization" as const,
        title: "响应速度优化",
        timestamp: new Date(Date.now() - 14 * 86400000).toISOString(), // 2周前
        impact: "平均响应时间降低 15%",
      },
    ],
  };
}

/**
 * 转换为角色网格数据
 */
export function transformToAgentCard(
  agentId: string,
  agentName: string,
  agentIcon: string,
  data: EvolutionOverview,
  skillPerformances: SkillPerformance[]
) {
  const level = calculateLevel(data.learning_events);
  const stars = calculateStars(level);
  const { progress } = calculateXP(data.learning_events);
  const achievements = extractAchievements(data, skillPerformances);

  return {
    id: agentId,
    name: agentName,
    icon: agentIcon,
    level,
    stars,
    skillCount: data.skills.total,
    achievementCount: achievements.length,
    successRate: Math.round(data.skills.avg_success_rate * 100),
    xpProgress: Math.round(progress),
    isActive: true, // 可以根据最后活跃时间判断
  };
}
