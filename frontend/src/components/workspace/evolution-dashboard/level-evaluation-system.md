# 等级评价体系设计方案

## 问题分析

### 当前问题
- **机械计算**：每10个学习事件 = 1级，过于简单
- **缺乏意义**：Lv.23 代表什么？优秀还是普通？
- **无对比参考**：用户不知道自己的等级在什么水平

## 设计方案

### 方案 A：综合评分制（推荐）

#### 核心思路
等级不仅看数量，更看质量。综合多个维度计算等级。

#### 评分公式
```typescript
等级 = f(学习事件数, 成功率, 技能数, 记忆量)

具体：
基础分 = 学习事件数 / 10  // 基础经验
质量分 = 成功率 / 10        // 质量加成（最高10分）
技能分 = 技能数量 / 2        // 技能加成（最高10分）
记忆分 = log(记忆量) / 2     // 记忆加成（对数衰减）

最终等级 = 基础分 × (1 + 质量系数 + 技能系数 + 记忆系数)

其中：
质量系数 = (成功率 - 50) / 100  // 50%以下不加成，100%加成50%
技能系数 = min(技能数量 / 20, 0.5)  // 20个技能=50%加成
记忆系数 = min(log10(记忆量) / 10, 0.3)  // 记忆量加成，最高30%
```

#### 等级分段

| 等级范围 | 称号 | 描述 | 预期表现 |
|---------|------|------|---------|
| Lv.1-5 | 🌱 新手 | 刚开始学习 | 成功率40-60%，0-5个技能 |
| Lv.6-10 | 📖 学徒 | 基础扎实 | 成功率60-70%，5-10个技能 |
| Lv.11-20 | ⚙️ 熟手 | 能独立工作 | 成功率70-80%，10-15个技能 |
| Lv.21-35 | 🎯 专家 | 高效可靠 | 成功率80-90%，15-20个技能 |
| Lv.36-50 | 🏆 大师 | 领域精通 | 成功率90%+，20+个技能 |
| Lv.51-75 | 💎 宗师 | 近乎完美 | 成功率95%+，全面发展 |
| Lv.76-99 | ⭐ 传奇 | 理论极限 | 完美表现，引领方向 |

#### 星级映射
```typescript
星级 = Math.ceil(等级 / 20)
1星 = Lv.1-20   (新手到熟手)
2星 = Lv.21-40  (专家起步)
3星 = Lv.41-60  (大师级别)
4星 = Lv.61-80  (宗师级别)
5星 = Lv.81-99  (传奇级别)
```

#### 实现代码
```typescript
export function calculateComprehensiveLevel(data: EvolutionOverview): {
  level: number;
  title: string;
  grade: string;
  percentile: number; // 百分位（相对排名）
} {
  // 1. 基础经验
  const baseXP = data.learning_events / 10;
  
  // 2. 质量系数
  const successRate = data.skills.avg_success_rate;
  const qualityMultiplier = Math.max(0, (successRate - 50) / 100);
  
  // 3. 技能系数
  const skillMultiplier = Math.min(data.skills.total / 20, 0.5);
  
  // 4. 记忆系数
  const memoryMultiplier = Math.min(
    Math.log10(Math.max(data.memory.total_facts, 1)) / 10,
    0.3
  );
  
  // 5. 计算最终等级
  const totalMultiplier = 1 + qualityMultiplier + skillMultiplier + memoryMultiplier;
  const rawLevel = baseXP * totalMultiplier;
  const level = Math.min(Math.floor(rawLevel), 99); // 最高99级
  
  // 6. 确定称号和评级
  const { title, grade } = getLevelTitle(level);
  
  // 7. 计算百分位（模拟数据，实际应从后端获取）
  const percentile = calculatePercentile(level);
  
  return { level, title, grade, percentile };
}

function getLevelTitle(level: number): { title: string; grade: string } {
  if (level <= 5) return { title: "新手", grade: "🌱" };
  if (level <= 10) return { title: "学徒", grade: "📖" };
  if (level <= 20) return { title: "熟手", grade: "⚙️" };
  if (level <= 35) return { title: "专家", grade: "🎯" };
  if (level <= 50) return { title: "大师", grade: "🏆" };
  if (level <= 75) return { title: "宗师", grade: "💎" };
  return { title: "传奇", grade: "⭐" };
}

function calculatePercentile(level: number): number {
  // 基于正态分布模拟
  // 假设平均等级15，标准差10
  const mean = 15;
  const stdDev = 10;
  const z = (level - mean) / stdDev;
  
  // 简化的正态分布累积函数
  const percentile = 50 + 50 * Math.tanh(z / 2);
  return Math.round(percentile);
}
```

### 方案 B：段位制（竞技游戏风格）

#### 核心思路
像王者荣耀、英雄联盟一样的段位系统

#### 段位结构
```
青铜 I-III   (Bronze)    Lv.1-9
白银 I-III   (Silver)    Lv.10-19
黄金 I-III   (Gold)      Lv.20-29
铂金 I-III   (Platinum)  Lv.30-39
钻石 I-III   (Diamond)   Lv.40-49
大师         (Master)    Lv.50-74
王者         (King)      Lv.75-99
```

#### 显示方式
```
🥉 青铜 II
⚪ 白银 I
🥇 黄金 III
💠 铂金 I
💎 钻石 II
👑 大师
⭐ 王者
```

### 方案 C：混合制（推荐实施）

#### 核心思路
综合评分 + 称号 + 百分位

#### UI 展示
```
┌─────────────────────────────────────┐
│  🤖 代码助手                         │
│                                     │
│  Lv.23  🎯 专家                     │
│  超过 78% 的用户                     │
│  ⭐⭐⭐                              │
│                                     │
│  ████████████░░░░ 85% → Lv.24      │
│  需要完成 3 个任务升级               │
└─────────────────────────────────────┘
```

说明：
- **Lv.23**：具体等级数字
- **🎯 专家**：等级称号
- **超过 78% 的用户**：百分位排名
- **⭐⭐⭐**：星级评价

## 实现细节

### 1. 等级显示优化

#### Before
```tsx
<div className="text-sm font-medium text-primary">
  Lv.{stats.level}
</div>
```

#### After
```tsx
<div className="flex items-center gap-2">
  <span className="text-sm font-medium text-primary">
    Lv.{stats.level}
  </span>
  <span className="text-xs text-muted-foreground">
    {stats.title}
  </span>
  {stats.percentile && (
    <span className="text-xs text-muted-foreground">
      · 超过 {stats.percentile}% 的用户
    </span>
  )}
</div>
```

### 2. 等级评价卡片

```tsx
function LevelEvaluationCard({ stats }: { stats: CharacterStats }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-3">
        <div className="flex size-16 items-center justify-center rounded-full bg-primary/10 text-3xl">
          {stats.grade}
        </div>
        <div>
          <div className="text-lg font-bold">
            Lv.{stats.level} {stats.title}
          </div>
          <div className="mt-0.5 text-sm text-muted-foreground">
            超过 {stats.percentile}% 的用户
          </div>
        </div>
      </div>
      
      {/* 等级描述 */}
      <div className="mt-3 text-sm text-muted-foreground">
        {getLevelDescription(stats.level)}
      </div>
      
      {/* 距离下一称号 */}
      {getNextTitle(stats.level) && (
        <div className="mt-3 rounded-md bg-muted/50 p-2 text-xs">
          <div className="flex items-center justify-between">
            <span>距离 <strong>{getNextTitle(stats.level)}</strong></span>
            <span>{getNextLevelThreshold(stats.level) - stats.level} 级</span>
          </div>
        </div>
      )}
    </div>
  );
}

function getLevelDescription(level: number): string {
  if (level <= 5) return "你正在学习基础技能，保持学习！";
  if (level <= 10) return "基础扎实，可以独立完成简单任务。";
  if (level <= 20) return "能力稳定，可以处理大部分常规任务。";
  if (level <= 35) return "高效可靠，已经是团队中的专家。";
  if (level <= 50) return "领域精通，能解决复杂问题。";
  if (level <= 75) return "接近完美，在各方面都表现出色。";
  return "传奇级别，引领技术方向。";
}

function getNextTitle(level: number): string | null {
  if (level <= 5) return "学徒";
  if (level <= 10) return "熟手";
  if (level <= 20) return "专家";
  if (level <= 35) return "大师";
  if (level <= 50) return "宗师";
  if (level <= 75) return "传奇";
  return null;
}

function getNextLevelThreshold(level: number): number {
  if (level <= 5) return 6;
  if (level <= 10) return 11;
  if (level <= 20) return 21;
  if (level <= 35) return 36;
  if (level <= 50) return 51;
  if (level <= 75) return 76;
  return 99;
}
```

### 3. 等级对比功能

```tsx
function LevelComparison({ myLevel, avgLevel }: { myLevel: number; avgLevel: number }) {
  const diff = myLevel - avgLevel;
  const isAbove = diff > 0;
  
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="text-xs text-muted-foreground">与平均水平对比</div>
      <div className="mt-2 flex items-center gap-2">
        <div className="text-lg font-bold">
          {isAbove ? "+" : ""}{diff} 级
        </div>
        <div className={cn(
          "text-xs",
          isAbove ? "text-success" : "text-muted-foreground"
        )}>
          {isAbove ? "超过平均" : "低于平均"}
        </div>
      </div>
      <div className="mt-2 flex gap-1">
        <div className="h-1 flex-1 rounded-full bg-muted" />
        <div 
          className="h-1 rounded-full bg-primary"
          style={{ width: `${(myLevel / 99) * 100}%` }}
        />
      </div>
    </div>
  );
}
```

## 数据支持

### 需要后端提供
1. **全局统计**：
   - 平均等级
   - 等级分布（直方图）
   - 各段位人数

2. **个人排名**：
   - 百分位（percentile）
   - 绝对排名（可选）

### API 设计
```typescript
GET /api/v1/evolution/leaderboard
Response: {
  my_level: 23,
  my_percentile: 78,  // 超过78%的用户
  avg_level: 15,
  distribution: {
    "1-10": 2500,   // 1-10级有2500人
    "11-20": 1800,
    "21-30": 1200,
    "31-40": 800,
    "41-50": 400,
    "51+": 200
  }
}
```

## 激励设计

### 1. 升级奖励
```
Lv.10 → 解锁"学徒"称号 + 100 XP奖励
Lv.20 → 解锁"熟手"称号 + 技能槽+1
Lv.30 → 解锁"专家"称号 + 专属头像框
```

### 2. 里程碑成就
```
首次达到专家级别 → "专家之路"成就
超过90%的用户 → "精英阶层"成就
```

### 3. 等级特权（可选）
```
Lv.20+ → 解锁高级功能
Lv.30+ → 优先处理队列
Lv.50+ → 专属支持渠道
```

## 总结

### 推荐方案：混合制
- **综合评分**：不只看数量，更看质量
- **称号系统**：给用户明确的定位
- **百分位排名**：提供社会对比
- **星级评价**：快速可视化

### 实施步骤
1. **Phase 1**：实现综合评分算法
2. **Phase 2**：添加称号和等级描述
3. **Phase 3**：接入后端排名数据
4. **Phase 4**：添加等级对比功能

这样，用户不仅知道"我是Lv.23"，还知道"我是专家级别，超过78%的用户"！
