# 自进化系统 UX/UI 规划（基于现有架构）

## 🏗️ 现有架构分析

### 布局结构
```
WorkspaceLayout (layout.tsx)
├─ SidebarProvider
│  ├─ WorkspaceSidebar          ← 左侧导航栏
│  └─ SidebarInset
│     ├─ StubResponseBannerHost
│     └─ Outlet                 ← 页面内容区
│        └─ /workspace/evolution/page.tsx
```

### 关键发现
1. **没有全局 HUD 层**：布局中没有右上角固定的 HUD 区域
2. **单一 Sidebar**：只有左侧边栏，右侧是纯内容区
3. **页面独立**：每个页面在 Outlet 中渲染，互不影响

---

## 🎯 重新规划的方案

### 方案 A：轻量级集成（推荐）

#### 1. 左侧导航栏增强
**位置**：`WorkspaceSidebar` 中的菜单项

**实现**：
```tsx
// workspace-sidebar.tsx
const NAV_ROUTES = [
  {
    to: "/workspace/evolution",
    labelKey: "navEvolution",
    icon: DnaIcon,
    // 新增：动态徽章
    badge: () => {
      const { data } = useEvolutionOverview();
      const level = calculateLevel(data?.learning_events || 0);
      return `Lv.${level}`;
    },
    badgeClassName: "bg-primary/10 text-primary text-[10px]"
  }
];
```

**效果**：
```
🧬 自进化  [Lv.23]
```

**优势**：
- ✅ 零侵入性，不改变整体布局
- ✅ 利用现有 Sidebar 组件系统
- ✅ 实现简单，风险低

---

#### 2. 进化页面优化布局
**位置**：`/workspace/evolution/page.tsx`

**当前结构**：
```tsx
<WorkspaceContainer>
  <WorkspaceBody>
    <header>头部</header>
    <div>
      <EvolutionDashboard />  // 现有
    </div>
  </WorkspaceBody>
</WorkspaceContainer>
```

**优化后结构**：
```tsx
<WorkspaceContainer>
  <WorkspaceBody>
    {/* 顶部操作栏 */}
    <header className="border-b p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1>自进化系统</h1>
          <p className="text-sm text-muted-foreground">
            AI 持续成长，让每次对话更智能
          </p>
        </div>
        
        {/* 视图切换 */}
        <ToggleGroup value={viewMode} onValueChange={setViewMode}>
          <ToggleGroupItem value="gamified">
            <GamepadIcon className="mr-1 size-3" />
            游戏化
          </ToggleGroupItem>
          <ToggleGroupItem value="classic">
            经典视图
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
    </header>

    {/* 内容区 - 可滚动 */}
    <div className="flex-1 overflow-y-auto p-6">
      {viewMode === "gamified" ? (
        <GameifiedEvolutionDashboard />
      ) : (
        <EvolutionDashboard />
      )}
    </div>
  </WorkspaceBody>
</WorkspaceContainer>
```

---

#### 3. 游戏化面板内部布局（已实现）
**位置**：`gamified-evolution-dashboard.tsx`

**采用垂直布局**：
```tsx
<div className="space-y-6">
  {/* 1. 群体智能 - 可折叠 */}
  <Collapsible defaultOpen>
    <CollapsibleTrigger className="flex items-center gap-2">
      <ChevronDownIcon className="size-4" />
      <h2>🌐 群体智能</h2>
    </CollapsibleTrigger>
    <CollapsibleContent>
      <CollectiveIntelligencePanel {...} />
    </CollapsibleContent>
  </Collapsible>

  {/* 2. 我的角色 */}
  <div>
    <h2>👤 我的角色</h2>
    <AgentGrid 
      agents={agents}
      selectedAgentId={selectedAgentId}
      onSelectAgent={setSelectedAgentId}
    />
  </div>

  {/* 3. 选中角色详情（条件渲染）*/}
  {selectedAgentId && (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex items-center justify-between border-b pb-2">
        <h2>{selectedAgent.icon} {selectedAgent.name} 的成长详情</h2>
        <Button 
          variant="ghost" 
          size="sm"
          onClick={() => setSelectedAgentId(null)}
        >
          ← 返回角色列表
        </Button>
      </div>
      
      <Tabs defaultValue="overview" className="mt-4">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="skills">技能树</TabsTrigger>
          <TabsTrigger value="timeline">成长日志</TabsTrigger>
        </TabsList>
        
        <TabsContent value="overview">
          <CharacterCard {...characterStats} />
        </TabsContent>
        <TabsContent value="skills">
          <SkillTree skills={skills} />
        </TabsContent>
        <TabsContent value="timeline">
          <GrowthTimeline events={timelineEvents} />
        </TabsContent>
      </Tabs>
    </motion.div>
  )}
</div>
```

**优势**：
- ✅ 一屏看全：群体智能 + 所有角色
- ✅ 渐进式展示：选中角色后才显示详情
- ✅ 流畅过渡：使用 motion 动画
- ✅ 可折叠：群体智能不需要时可折叠

---

### 方案 B：添加全局 HUD（可选，侵入性高）

如果你确实想要全局 HUD（右上角固定状态卡）：

#### 修改 WorkspaceLayout
```tsx
// layout.tsx
<SidebarProvider>
  <WorkspaceSidebar />
  <SidebarInset>
    {/* 新增：全局 HUD */}
    <div className="absolute right-4 top-4 z-50">
      <EvolutionHUD />
    </div>
    
    <StubResponseBannerHost />
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Outlet />
    </div>
  </SidebarInset>
</SidebarProvider>
```

#### EvolutionHUD 组件
```tsx
// components/workspace/evolution-hud.tsx
export function EvolutionHUD() {
  const { data } = useEvolutionOverview();
  const [isOpen, setIsOpen] = useState(false);
  
  if (!data) return null;
  
  const level = calculateLevel(data.learning_events);
  const { progress } = calculateXP(data.learning_events);
  const stars = calculateStars(level);
  
  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <button className="rounded-lg border bg-card/80 backdrop-blur p-2 shadow-sm hover:bg-card">
          <div className="flex items-center gap-2">
            <span className="text-lg">🤖</span>
            <div className="text-left">
              <div className="text-xs font-medium">
                Lv.{level} {getStars(stars)}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {progress}% → Lv.{level + 1}
              </div>
            </div>
          </div>
        </button>
      </PopoverTrigger>
      
      <PopoverContent align="end" className="w-80">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold">快速概览</h3>
            <Link to="/workspace/evolution">
              <Button variant="ghost" size="sm">
                查看详情 →
              </Button>
            </Link>
          </div>
          
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">成功率</span>
              <span className="font-medium">{data.skills.avg_success_rate}%</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">技能数</span>
              <span className="font-medium">{data.skills.total}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">记忆量</span>
              <span className="font-medium">{data.memory.total_facts} 条</span>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
```

**缺点**：
- ❌ 需要修改核心布局文件
- ❌ 可能与其他功能冲突（如通知、用户菜单）
- ❌ 增加页面复杂度

---

## 📊 最终推荐

### 推荐：方案 A（轻量级）

**理由**：
1. **符合现有架构**：不改变核心布局
2. **实现简单**：只需增强导航栏 + 优化页面
3. **风险低**：不会影响其他功能
4. **用户体验好**：信息渐进式呈现

### 实施步骤

#### Phase 1：左侧导航徽章（30分钟）
```tsx
// 在 workspace-sidebar.tsx 的自进化菜单项上添加徽章
{
  to: "/workspace/evolution",
  labelKey: "navEvolution",
  icon: DnaIcon,
  badge: <EvolutionLevelBadge /> // 显示 Lv.23
}
```

#### Phase 2：页面布局优化（1小时）
```tsx
// 在 evolution/page.tsx 添加视图切换
// 已完成 ✅
```

#### Phase 3：游戏化面板垂直布局（1小时）
```tsx
// 在 gamified-evolution-dashboard.tsx 实现
// 1. 群体智能可折叠
// 2. 角色网格
// 3. 选中角色详情（动画展开）
```

---

## 🎨 交互流程

### 用户旅程
```
1. 用户看到左侧导航
   🧬 自进化 [Lv.23]  ← 吸引注意

2. 点击进入
   ↓
   看到页面：[🎮 游戏化] [经典视图]

3. 默认游戏化视图
   ↓
   🌐 群体智能（可折叠）
   👤 4个角色卡片

4. 点击某个角色
   ↓
   动画展开详情
   [概览] [技能树] [成长日志]

5. 查看雷达图、技能等
   ↓
   点击"返回角色列表"
   ↓
   回到步骤3
```

---

## 💡 关键设计决策

### 1. 不添加全局 HUD
**原因**：
- 现有架构没有 HUD 层
- 避免视觉干扰
- 左侧导航徽章足够

### 2. 垂直布局而非 Tab
**原因**：
- 一屏看到群体+角色全貌
- 符合"从宏观到微观"的信息层次
- Tab 会隐藏信息

### 3. 群体智能可折叠
**原因**：
- 不是所有用户都关心
- 节省屏幕空间
- 让角色成为焦点

### 4. 选中角色后才显示详情
**原因**：
- 避免信息过载
- 渐进式呈现
- 明确的操作流程

---

## 🚀 下一步

需要我：
1. **实施 Phase 1** - 添加导航徽章？
2. **实施 Phase 3** - 优化面板布局（折叠+动画）？
3. **其他优化**？

告诉我你的决定！
