import { cn } from "@/lib/utils";
import type {
  EvolutionOverview,
  SkillPerformance,
} from "@/core/evolution/api";

export interface RadarDataPoint {
  dimension: string;
  value: number; // 0-100
  maxValue?: number;
}

export interface AbilityRadarChartProps {
  data: RadarDataPoint[];
  size?: number;
  className?: string;
}

/**
 * 多维能力雷达图
 * 用于展示角色在不同维度的能力水平
 */
export function AbilityRadarChart({
  data,
  size = 240,
  className,
}: AbilityRadarChartProps) {
  const center = size / 2;
  const radius = size / 2 - 40; // 留出边距显示标签
  const levels = 5; // 5个等级环

  // 计算每个点的坐标
  const calculatePoint = (index: number, value: number) => {
    const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
    const distance = (value / 100) * radius;
    return {
      x: center + Math.cos(angle) * distance,
      y: center + Math.sin(angle) * distance,
    };
  };

  // 计算标签位置（在最外圈外侧）
  const calculateLabelPosition = (index: number) => {
    const angle = (Math.PI * 2 * index) / data.length - Math.PI / 2;
    const distance = radius + 25;
    return {
      x: center + Math.cos(angle) * distance,
      y: center + Math.sin(angle) * distance,
      angle: (angle * 180) / Math.PI,
    };
  };

  // 生成多边形路径
  const generatePolygonPath = (valueMultiplier = 1) => {
    return data
      .map((item, index) => {
        const point = calculatePoint(index, item.value * valueMultiplier);
        return `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`;
      })
      .join(" ") + " Z";
  };

  // 生成背景网格多边形
  const generateGridPolygon = (level: number) => {
    const levelValue = (level / levels) * 100;
    return data
      .map((_, index) => {
        const point = calculatePoint(index, levelValue);
        return `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`;
      })
      .join(" ") + " Z";
  };

  // 生成轴线
  const generateAxisLines = () => {
    return data.map((_, index) => {
      const point = calculatePoint(index, 100);
      return (
        <line
          key={`axis-${index}`}
          x1={center}
          y1={center}
          x2={point.x}
          y2={point.y}
          stroke="currentColor"
          strokeWidth="1"
          className="text-border"
          opacity="0.3"
        />
      );
    });
  };

  return (
    <div className={cn("relative", className)}>
      <svg width={size} height={size} className="overflow-visible">
        {/* 背景网格 */}
        {Array.from({ length: levels }).map((_, level) => (
          <path
            key={`grid-${level}`}
            d={generateGridPolygon(level + 1)}
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
            className="text-border"
            opacity={0.2}
          />
        ))}

        {/* 轴线 */}
        {generateAxisLines()}

        {/* 数据填充区域 */}
        <path
          d={generatePolygonPath()}
          fill="currentColor"
          className="text-primary"
          opacity={0.2}
        />

        {/* 数据边界线 */}
        <path
          d={generatePolygonPath()}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-primary"
        />

        {/* 数据点 */}
        {data.map((item, index) => {
          const point = calculatePoint(index, item.value);
          return (
            <circle
              key={`point-${index}`}
              cx={point.x}
              cy={point.y}
              r="4"
              fill="currentColor"
              className="text-primary"
            />
          );
        })}

        {/* 维度标签 */}
        {data.map((item, index) => {
          const pos = calculateLabelPosition(index);
          return (
            <g key={`label-${index}`}>
              <text
                x={pos.x}
                y={pos.y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="text-xs font-medium fill-foreground"
              >
                {item.dimension}
              </text>
              <text
                x={pos.x}
                y={pos.y + 12}
                textAnchor="middle"
                dominantBaseline="middle"
                className="text-[10px] fill-muted-foreground"
              >
                {item.value}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/**
 * 预定义的能力维度配置
 */
export const ABILITY_DIMENSIONS = {
  // 代码类角色
  coding: [
    { key: "codeQuality", label: "代码质量" },
    { key: "bugFixing", label: "问题修复" },
    { key: "performance", label: "性能优化" },
    { key: "architecture", label: "架构设计" },
    { key: "testing", label: "测试覆盖" },
    { key: "documentation", label: "文档完善" },
  ],

  // 设计类角色
  design: [
    { key: "uiDesign", label: "界面设计" },
    { key: "uxOptimization", label: "体验优化" },
    { key: "prototyping", label: "原型设计" },
    { key: "branding", label: "品牌设计" },
    { key: "accessibility", label: "可访问性" },
    { key: "responsive", label: "响应式" },
  ],

  // 通用能力
  general: [
    { key: "efficiency", label: "执行效率" },
    { key: "accuracy", label: "准确度" },
    { key: "creativity", label: "创造力" },
    { key: "learning", label: "学习能力" },
    { key: "communication", label: "沟通表达" },
    { key: "problemSolving", label: "问题解决" },
  ],
};

/**
 * 从进化数据计算能力维度得分
 */
export function calculateAbilityScores(
  data: EvolutionOverview,
  skillPerformances: SkillPerformance[],
  dimensionType: keyof typeof ABILITY_DIMENSIONS = "general"
): RadarDataPoint[] {
  const dimensions = ABILITY_DIMENSIONS[dimensionType];

  // 这里是示例计算逻辑，实际应根据具体技能和数据计算
  return dimensions.map((dim) => {
    let value = 0;

    switch (dim.key) {
      case "efficiency":
        // 基于平均成功率
        value = data.skills.avg_success_rate;
        break;
      case "accuracy":
        // 基于成功率和一致性
        value = Math.min(data.skills.avg_success_rate + 5, 100);
        break;
      case "creativity":
        // 基于自动提取技能数量
        value = Math.min((data.skills.auto_extracted / 20) * 100, 100);
        break;
      case "learning":
        // 基于学习事件增长率
        value = Math.min((data.learning_events / 50) * 100, 100);
        break;
      case "communication":
        // 基于记忆数量（反映理解和表达）
        value = Math.min((data.memory.total_facts / 500) * 100, 100);
        break;
      case "problemSolving":
        // 综合成功率和技能多样性
        value = (data.skills.avg_success_rate * 0.7 +
                 Math.min((data.skills.total / 20) * 100, 100) * 0.3);
        break;

      // 代码相关维度
      case "codeQuality":
        value = getSkillScore(skillPerformances, ["refactor", "代码质量", "重构"]);
        break;
      case "bugFixing":
        value = getSkillScore(skillPerformances, ["bug", "修复", "fix"]);
        break;
      case "performance":
        value = getSkillScore(skillPerformances, ["performance", "性能", "optimize"]);
        break;
      case "architecture":
        value = getSkillScore(skillPerformances, ["architecture", "架构", "design"]);
        break;
      case "testing":
        value = getSkillScore(skillPerformances, ["test", "测试", "单元"]);
        break;
      case "documentation":
        value = getSkillScore(skillPerformances, ["doc", "文档", "documentation"]);
        break;

      // 设计相关维度
      case "uiDesign":
        value = getSkillScore(skillPerformances, ["ui", "界面", "design"]);
        break;
      case "uxOptimization":
        value = getSkillScore(skillPerformances, ["ux", "体验", "user"]);
        break;
      case "prototyping":
        value = getSkillScore(skillPerformances, ["prototype", "原型", "wireframe"]);
        break;

      default:
        // 默认使用整体成功率
        value = data.skills.avg_success_rate;
    }

    return {
      dimension: dim.label,
      value: Math.round(Math.max(0, Math.min(100, value))),
    };
  });
}

/**
 * 根据技能名称关键词获取技能得分
 */
function getSkillScore(
  skillPerformances: SkillPerformance[],
  keywords: string[]
): number {
  const matchedSkills = skillPerformances.filter((skill) =>
    keywords.some((keyword) =>
      skill.name.toLowerCase().includes(keyword.toLowerCase())
    )
  );

  if (matchedSkills.length === 0) {
    return 0;
  }

  // 取匹配技能的平均成功率
  const avgSuccessRate = matchedSkills.reduce(
    (sum, skill) => sum + skill.success_rate,
    0
  ) / matchedSkills.length;

  return avgSuccessRate;
}
