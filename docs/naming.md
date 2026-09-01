# NAMING · 双轨命名契约

> **目录名是诗意（Bio）· 类/函数名是契约（Eng）**
>
> 人类在文档里看章鱼，代码里看标准的分布式系统术语。

---

## 2026 learning-curve amendment

Echo keeps the biomimetic kernel names, but public onboarding must now use a
three-layer language rule:

1. **Users** see outcome language first: goal, run, approve, inspect, recover.
2. **Developers** see engineering language first: planner, scheduler, worker,
   skill, tool executor, sandbox, journal, memory store.
3. **Kernel contributors** may use biomimetic names as stable internal aliases:
   cerebrum, ganglia, arms, suckers, beak, genome.

In user-facing docs, write `Planner (Cerebrum)` on first mention, then use
`Planner`. Do not require a new reader to memorize organ names before they can
run the product.

---

## 命名规则

### ✅ 必须
1. **目录与 package 名**：用**生物名**（`cerebrum`, `ganglia`, ...）
2. **类名 / 函数名 / 变量名**：用**工程名**（`Planner`, `LocalRuntime`, ...）
3. **公开 API 入口（`__init__.py`）**：同时导出两套名字，生物名作别名
4. **日志 / 指标 / trace span 名**：**工程名**（方便与非本系统的人沟通）
5. **用户面向文档**：**结果名 / 工程名优先**，生物名只作内核别名（降低学习曲线）

### ❌ 禁止
- 在类名/函数名里用生物词：`class Cerebrum` ❌、`def cerebrum_plan()` ❌
- 在代码 log 里用生物词："Cerebrum decomposed task" ❌ → "Planner decomposed task" ✅
- 为了"韵味"牺牲清晰度：`def regenerate()` 指代 eval 流水线 ❌

### 示例

```python
# 目录：runtime/cerebrum/
# 文件：runtime/cerebrum/planner.py

class Planner:                                   # 工程名
    """High-level task decomposition and routing."""
    def plan(self, goal): ...

# runtime/cerebrum/__init__.py
from .planner import Planner
Cerebrum = Planner                               # 生物别名，便于读文档的人 import

# 调用方
from runtime.core.cerebrum import Planner      # ✅ 推荐
from runtime.core.cerebrum import Cerebrum     # ✅ 也可（文档一致）
```

---

## 20 器官双轨命名总表

| # | 目录（Bio）| 主要类（Eng）| 工程别名 | 职责一词 |
|---|---|---|---|---|
| 1 | `cerebrum/` | `Planner` | planner | 慢路径规划 |
| 2 | `spinal_cord/` | `ReflexRouter` | reflex | 快路径反射 |
| 3 | `ganglia/` | `LocalRuntime` | local_runtime | 腕本地执行 |
| 4 | `arms/` | `Worker` | worker | 专长 agent 实例 |
| 5 | `suckers/` | `Skill`, `SkillRegistry` | skill | 技能原子 |
| 6 | `beak/` | `ToolExecutor` | executor | 工具执行 |
| 7 | `mantle/` | `Sandbox`, `SandboxProvider` | sandbox | 沙箱隔离 |
| 8 | `immunity/` | `TrustEngine`, `ImmuneMemory` | trust | 适应性安全 |
| 9 | `siphon/` | `IOGateway`, `StreamSink` | io_gateway | 对外流式输出 |
| 10 | `eyes/` | `Perception`, `ModelRouter` | perception | 显式感知 + 模型路由 |
| 11 | `skin/` | `AmbientSensor` | sensor | 隐式环境感知（纯上报）|
| 12 | `nerves/` | `MessageBus`, `GraphExecutor` | bus + graph | 消息总线 + 工作流 |
| 13 | `chromatophores/` | `SignalBus`, `EffectorPool`, `BoidsProtocol` | broadcast + effector | 广播 + 并行执行 + 群涌协议 |
| 14 | `ink/` | `CircuitBreaker`, `BudgetGuard` | breaker | 熔断 + 预算硬顶 |
| 15 | `hearts/` | `SystemicScheduler`, `BranchialPool` | scheduler + io_pool | 双循环节律 |
| 16 | `genome/` | `Checkpointer`, `Journal`, `MemoryStore` | persistence | 长时持久化 |
| 17 | `hemolymph/` | `ContextComposer`, `Blackboard` | context + blackboard | 上下文流 + 共享面 |
| 18 | `camouflage/` | `StrategySelector` | ab_tester | 策略 A/B |
| 19 | `regeneration/` | `Evolver`, `TrajectoryEvaluator`, `SkillForge` | evolver | 自进化流水线 |
| 20 | `tentacle/` | `TentaclePool`, `MobileDevice` | mobile_runtime | 移动 / 跨设备执行触点 |

---

## 跨器官术语统一词典（工程口径）

这是团队内部用的**扁平词汇表**。任何新同学不懂生物名也能读代码。

| 术语 | 含义 | 等价生物名 |
|---|---|---|
| **Planner** | 高层任务分解器 | Cerebrum |
| **Reflex** | 快路径规则/缓存响应 | Spinal Cord |
| **Worker** | 专长 agent 实例 | Arm |
| **LocalRuntime** | Worker 的执行内核 | Ganglion |
| **Skill** | 技能原子（含 SKILL.md）| Sucker |
| **ToolExecutor** | 把 Skill 参数化执行 | Beak |
| **Sandbox** | 执行隔离环境 | Mantle |
| **TrustEngine** | 来源/行为风险引擎 | Immunity |
| **CircuitBreaker** | 预算/失败熔断 | Ink |
| **SystemicScheduler** | 内部业务主循环 | Systemic Heart |
| **BranchialPool** | 外部 I/O 异步池 | Branchial Heart |
| **MessageBus** | 分布式消息底座 | Nerves/bus |
| **SignalBus** | 腕间状态广播 | Chromatophores（信号侧）|
| **EffectorPool** | 多动作并行触发 | Chromatophores（效应侧）|
| **BoidsProtocol** | 避撞/对齐/聚合规则 | Chromatophores（群涌侧）|
| **Blackboard** | 短时共享状态面 | Hemolymph（共享态侧）|
| **ContextComposer** | 每轮上下文打包器 | Hemolymph（流动侧）|
| **Checkpointer** | 任务快照持久化 | Genome/checkpoint |
| **MemoryStore** | 长时记忆存储 | Genome/memory |
| **Evolver** | 离线反思流水线 | Regeneration |
| **StrategySelector** | 策略多臂老虎机 | Camouflage |
| **Perception** | 输入解析器 | Eyes |
| **ModelRouter** | LLM Provider 适配 + 分层路由 | Eyes/models |
| **AmbientSensor** | 被动环境事件上报 | Skin |
| **IOGateway** | 流式对外协议层 | Siphon |
| **MobileDevice** | 移动端 / 端侧设备会话 | Tentacle |
| **TentaclePool** | 跨设备连接池和锁定调度 | Tentacle |

---

## 仓库结构内的命名边界

```
runtime/
├── cerebrum/                 ← 生物名（目录）
│   ├── __init__.py             导出 Planner；提供别名 Cerebrum
│   ├── planner.py              ← class Planner  (工程名)
│   ├── router.py               ← class TaskRouter
│   └── arbiter.py              ← class ConflictArbiter
│
├── hearts/
│   ├── __init__.py
│   ├── systemic.py             ← class SystemicScheduler
│   ├── branchial.py            ← class BranchialPool
│   └── bulkhead.py             ← class BulkheadIsolator
│
└── ...
```

---

## 为什么采用双轨而不是单轨

### 反对 "全生物名" 的理由
- `def chromatophore_publish()` 在 stack trace 里让新人崩溃
- log 里 "Ganglion routing to Sucker via Beak" 对运维毫无信息量
- Grep 某个标准分布式概念（"circuit breaker"）找不到

### 反对 "全工程名" 的理由
- 文档诗意全失，仿生学只剩装饰
- 团队失去共同叙事，降低识别度
- 把精心设计的器官-原则映射扁平化

### 双轨的副作用（承认）
- 需要维护本命名契约
- PR review 时要检查命名是否越界
- 新人初期有小幅学习成本

**但**：新人学两个名字 vs 错把生物比喻写进代码里造出一堆难以 grep 的方法 —— 前者成本可控，后者是长期债务。

---

## 相关文档

- [principles.md](principles.md) — 六大抽象原则（工程名版本就藏在这里）
- [architecture.md](architecture.md) — 章鱼器官视角（生物名版本）
- [ROADMAP.md](ROADMAP.md) — 实施路线
