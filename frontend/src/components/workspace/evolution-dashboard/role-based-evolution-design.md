# 角色独立进化设计方案

## 核心理念
每个 Hub 角色是一个独立的"游戏角色"，有自己的等级、技能树、成就系统。

## UI 设计

### 1. Hub 集成：角色卡片显示等级

```tsx
// 在 Hub 角色选择界面
┌─────────────────────────────────────┐
│  👨‍💻 代码助手            Lv.23 ⭐⭐⭐ │
│  专注代码重构和 Bug 修复              │
│  [查看成长详情 →]                    │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  🎨 设计师                Lv.15 ⭐⭐  │
│  UI/UX 设计专家                      │
│  [查看成长详情 →]                    │
└─────────────────────────────────────┘
```

### 2. 进化页面：角色选择器

```
┌──────────────────────────────────────────────────┐
│  自进化系统                                        │
│                                                   │
│  选择角色：                                        │
│  ┌────────┬────────┬────────┬────────┐          │
│  │ 👨‍💻     │ 🎨     │ 📝     │ 🔧     │          │
│  │ 代码   │ 设计   │ 文档   │ 运维   │          │
│  │ Lv.23  │ Lv.15  │ Lv.18  │ Lv.12  │          │
│  │ [选中]  │        │        │        │          │
│  └────────┴────────┴────────┴────────┘          │
│                                                   │
│  🤖 代码助手                     Lv.23 ⭐⭐⭐      │
│  ██████████████░░░░ 85% → Lv.24                  │
│  需要完成 3 个任务升级                             │
│  ...（后面是游戏化界面）                           │
└──────────────────────────────────────────────────┘
```

### 3. 跨角色对比（可选）

```
┌──────────────────────────────────────────────────┐
│  📊 角色对比                                       │
│                                                   │
│  代码助手 vs 设计师                                │
│                                                   │
│  等级：     Lv.23 ████████░░    Lv.15 ██████░░░░ │
│  成功率：   82% ████████░░      75% ███████░░░   │
│  技能数：   8 个                6 个              │
│  成就数：   5 个                3 个              │
└──────────────────────────────────────────────────┘
```

## 数据结构调整

### API 层
```typescript
// 已有的 agent_id 支持
interface EvolutionOverview {
  agent_id: string;  // ✅ 已有
  // ... 其他字段
}

// 新增：按角色获取进化数据
export async function getEvolutionByAgent(
  agentId: string
): Promise<EvolutionOverview> {
  const response = await evolutionFetch(
    `/api/v1/evolution/overview?agent_id=${agentId}`
  );
  return response.json();
}

// 新增：获取所有角色的进化概览
export async function getAllAgentsEvolution(): Promise<{
  agents: Array<{
    agent_id: string;
    name: string;
    icon: string;
    level: number;
    stars: number;
  }>;
}> {
  const response = await evolutionFetch(`/api/v1/evolution/agents`);
  return response.json();
}
```

### 组件层
```typescript
// 页面状态管理
const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

// 获取当前选中角色的进化数据
const evolutionData = useEvolutionOverview(selectedAgentId);

// 渲染
<AgentSelector 
  onSelectAgent={setSelectedAgentId}
  selectedAgentId={selectedAgentId}
/>

{selectedAgentId && (
  <CharacterCard
    name={agentName}
    stats={transformToGameData(evolutionData)}
    // ...
  />
)}
```

## Hub 角色集成

### 方式 1：Hub 卡片上显示等级徽章
```tsx
// 在 Hub 角色卡片右上角添加等级徽章
<div className="absolute right-2 top-2">
  <div className="rounded-full bg-primary px-2 py-0.5 text-xs font-bold text-white">
    Lv.{agentLevel}
  </div>
</div>
```

### 方式 2：Hub 详情页添加"成长"标签
```tsx
// Hub 角色详情页
<Tabs>
  <TabsList>
    <TabsTrigger value="overview">概览</TabsTrigger>
    <TabsTrigger value="growth">
      <SparklesIcon className="mr-1 size-3" />
      成长 Lv.{agentLevel}
    </TabsTrigger>
  </TabsList>
  
  <TabsContent value="growth">
    <CharacterCard {...gameData} />
    <SkillTree skills={skills} />
    <GrowthTimeline events={timeline} />
  </TabsContent>
</Tabs>
```

### 方式 3：专门的"我的角色"页面
```tsx
// 新增路由：/workspace/my-agents
<Route path="/workspace/my-agents">
  <MyAgentsPage>
    {agents.map(agent => (
      <AgentCard
        key={agent.id}
        name={agent.name}
        level={agent.level}
        onClick={() => navigate(`/workspace/evolution?agent=${agent.id}`)}
      />
    ))}
  </MyAgentsPage>
</Route>
```

## 特殊场景处理

### 1. 多角色协作任务
```typescript
// 一个任务可能由多个 agent 完成
// 经验分配策略：
- 主力 agent：100% 经验
- 协助 agent：50% 经验
```

### 2. 角色切换
```typescript
// 用户切换 Hub 角色时
// - 保存当前角色的进化状态
// - 加载新角色的进化状态
// - UI 实时更新等级、技能等
```

### 3. 新角色初始化
```typescript
// 新建 Hub 角色时
// - 初始等级 Lv.1
// - 0 个技能
// - 显示新手引导
```

## 实施步骤

### Phase 1：数据层（1天）
1. 确认后端已支持 `agent_id` 过滤
2. 创建 `getEvolutionByAgent(agentId)` API
3. 创建 `getAllAgentsEvolution()` API

### Phase 2：UI 层（2天）
1. 在进化页面添加角色选择器
2. 传递 `agentId` 到游戏化组件
3. 根据 `agentId` 加载对应数据

### Phase 3：Hub 集成（1天）
1. Hub 卡片显示等级徽章
2. Hub 详情页添加"成长"标签
3. 点击跳转到进化页面并自动选中该角色

### Phase 4：测试优化（1天）
1. 多角色数据隔离测试
2. 切换流畅度优化
3. 空状态处理

## 用户体验提升

### 角色培养感
- "我的代码助手已经 Lv.23 了，重构技能很厉害！"
- "设计师还比较新手，需要多做几个设计任务"

### 专业化路线
- 代码助手：专精代码重构、Bug 修复、性能优化
- 设计师：专精 UI 设计、原型设计、视觉规范
- 文档助手：专精技术写作、API 文档、用户指南

### 成就系统
- "代码重构大师"成就：只有代码助手能解锁
- "UI 设计天才"成就：只有设计师能解锁
- 跨角色成就："全能专家"（所有角色都达到 Lv.20）

## 总结

✅ **推荐：角色独立进化**
- 符合现有数据结构（已有 agent_id）
- 用户体验更好（培养专精角色）
- 实施难度适中（主要是 UI 改动）
- 可扩展性强（未来支持角色技能迁移、角色融合等玩法）

下一步：你希望我实现哪个部分？
1. 角色选择器组件
2. Hub 卡片的等级徽章
3. 数据转换层（agent_id 关联）
