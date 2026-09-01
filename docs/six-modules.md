# SIX_MODULES · 六模块六边形 ↔ 章鱼架构映射

> 六边形 = **能力视图**（系统能学会什么）
> 章鱼 = **解剖视图**（身体怎么分工）
> 一种系统、两种看法。本文建双向桥。

---

## 1. 六边形全图（能力视图）

```
                      ┌─────────────────────┐
                      │  12 · 长任务引擎     │  执行层入口
                      └──────────┬──────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            │                    │                    │
 ┌──────────┴──────────┐  ┌──────┴──────┐  ┌──────────┴──────────┐
 │ 10 · 上下文工程     │  │   中心 ·    │  │  2 · 工作流进化      │
 │  执行前最后一关     │──│ 反思进化引擎 │──│                      │
 └──────────┬──────────┘  └──────┬──────┘  └──────────┬──────────┘
            │                    │                    │
 ┌──────────┴──────────┐         │         ┌──────────┴──────────┐
 │  8 · 记忆系统       │         │         │  4 · 技能进化        │
 │                     │         │         │                      │
 └──────────┬──────────┘         │         └──────────┬──────────┘
            │                    │                    │
            └────────────────────┼────────────────────┘
                                 │
                      ┌──────────┴──────────┐
                      │  6 · 知识图谱       │  知识层底座
                      └─────────────────────┘

 上半圈（12 / 2 / 4）= 执行类（会干活）
 下半圈（6 / 8 / 10）= 知识类（越干越懂）
```

**顺时针一圈 = 一次完整的执行—反思闭环。**

---

## 2. 六模块 × 章鱼器官映射表

### 模块 1 · 长任务引擎（12 点 · 执行层入口）

| 职责 | 落在章鱼哪个器官/协议 |
|---|---|
| 目标分解为 TaskNode 树 | `cerebrum/` Planner |
| 状态机驱动（pending/running/blocked/done）| `ganglia/` LocalRuntime（未实装）|
| Checkpoint + 断点续跑 | `genome/checkpoint/` |
| JSONL trajectory 落盘 | `genome/journal/` |
| 卡住探测 | `ink/` CircuitBreaker 的 `zero_gain_steps`（BDG-I5 附近）|
| Replan | `cerebrum/` + 触发来自 `chromatophores/` `alert.loop` |
| **完整协议** | [protocols/digestion.md](protocols/digestion.md) 就是本模块的详细规范 |

**拆解模板进化**：同一类目标成功拆解 ≥3 次 → 沉淀成 workflow（反向喂给模块 2）。

---

### 模块 2 · 工作流进化（2 点）

| 职责 | 落在章鱼哪个器官/协议 |
|---|---|
| DAG 定义 + 执行 | `nerves/graph/` |
| 复用已有 workflow | `nerves/graph/registry` + `suckers/` |
| 即兴编排 | `cerebrum/` 兜底 |
| A/B 分流跑两路径 | `camouflage/` StrategySelector |
| 节点热替换 | `genome/dna/patch/` Hot/Warm 级 |
| 失败模式反向改写 | `regeneration/reflection/` |
| **缺口** | **`regeneration/workflow_rewriter/` 还没写** —— 目前 Regeneration 只产 skill，不改 workflow |

**数据形态**：DAG + schema + (成功率 / 平均耗时 / 平均成本) + 可替代路径集。
前三项来自 `genome/journal/`，第四项由 `camouflage/` 产出。

---

### 模块 3 · 技能进化（4 点）

| 职责 | 落在章鱼哪个器官/协议 |
|---|---|
| 原子能力（SKILL.md）| `suckers/` |
| 从 trajectory 蒸馏新 skill | `regeneration/skill_forge/` |
| 打磨（更新成功率、边界）| `regeneration/evaluator/` |
| 版本化（旧版保留）| `suckers/custom/forged/<uuid>/versions/` |
| 淘汰（低成功率）| `regeneration/` 的 `retire()` + `ink/skill_cost_profile` 触发 |
| 成本画像 | `ink/` |
| 信任/风险打分 | `immunity/` Adaptive |
| **生命周期协议** | [protocols/evolution.md](protocols/evolution.md) |
| **缺口** | **每个 skill 自带回归测试集** —— 当前 shadow 验证用历史 trajectory 回放，但没有"skill 自带的 golden test"。这是"自进化不塌方的唯一保险"。要新增 `suckers/<id>/tests/`。|

---

### 模块 4 · 知识图谱（6 点 · 知识层底座）

| 职责 | 落在章鱼哪个器官/协议 |
|---|---|
| 实体-关系-属性三元组 | `genome/knowledge/` 需**升级**（当前只是 Wiki + FTS5）|
| 三元组来源（对话/工具/文档）| `eyes/Perception` + `arms/` 调用返回 |
| 带时间戳/来源/置信度 | 新增 schema 字段 |
| 冲突消解（新信息优先 + 证据支持）| **缺口 · 未实现** |
| 本体推理（子类继承属性）| **缺口 · 未实现** |
| **落地建议** | 用 Neo4j 或 Kùzu，不手搓 |
| **需新增目录** | `genome/knowledge/graph/`（与现有 `genome/knowledge/wiki/` 并列）|

**当前现状**：Wiki + FTS5 检索是"查得到"，但查不准推不动。升级到 KG 是本模块的核心工程任务。

---

### 模块 5 · 记忆系统（8 点）

四层分工 ↔ 章鱼已有结构的对应：

| 层 | 职责 | 落在 |
|---|---|---|
| 工作记忆 | 本轮会话 scratch pad | `hemolymph/` ContextPacket + Blackboard |
| 情景记忆 | 过往对话轨迹 | `genome/journal/`（就是 trajectory 本身）|
| 语义记忆 | 提炼过的模糊事实 | `genome/memory/` |
| 程序性记忆 | "此 skill 对 Gmail 有效，对 Outlook 不行"这类元经验 | `regeneration/reflection/` 产出的 mitigation rules，注入 Cerebrum planner prompt |

三大机制：

| 机制 | 落在 |
|---|---|
| **衰减** | `hemolymph/` TTL + `genome/journal/` retention policy + `suckers/` 低频退休 |
| **巩固（"睡眠"）** | `regeneration/` 夜间流水线（evaluator → skill_forge + rule_extraction）|
| **冲突消解** | **缺口 · 未实现**（KG 和 Memory 都缺这层）|

存储：向量库（`lancedb`）+ 关系库（`sqlite`）+ Markdown —— 本项目 config 已定。

---

### 模块 6 · 上下文工程（10 点 · 执行前最后一关）

| 职责 | 落在章鱼哪个器官/协议 |
|---|---|
| 按子任务决定 KG 召回 / 记忆召回 / skill 挂载 / token 预留 | `hemolymph/ContextComposer` |
| progressive disclosure | `suckers/loader/` |
| 压缩器（超限先压缩后截断）| `hemolymph/compress()` |
| 召回策略 | `hemolymph/compose()` 内含 |
| 配方评分（同样资料不同拼法，成功率差两倍）| **缺口 · 未实现** —— 需新增 `regeneration/recipe_evaluator/` |
| 坏配方自动下线 | **缺口** —— 依赖配方评分 |

> **这是最被低估的一层** —— 同模型、同记忆、同知识，**拼装方式不同成功率差 2×**。
> 我之前的 Fitness 分层只到 trajectory，没到"配方级"。要补。

---

### 中心 · 反思进化引擎

消费 trajectory，产出**五种信号**：

| 信号 | 去向 | 实现位置 |
|---|---|---|
| 新 skill 候选 | 模块 3 | `regeneration/skill_forge/` ✅ |
| workflow 改写建议 | 模块 2 | `regeneration/workflow_rewriter/` ❌ 缺口 |
| KG 新增三元组 | 模块 4 | `regeneration/kg_updater/` ❌ 缺口 |
| 记忆巩固指令 | 模块 5 | `regeneration/memory_consolidator/` ❌ 缺口 |
| 上下文配方打分 | 模块 6 | `regeneration/recipe_evaluator/` ❌ 缺口 |

**现状**：Regeneration 当前只有第一条完整（skill forge），另外四条都需要扩展。

触发模式：
- **实时**：每 N 次调用后（反射级别的小调整）
- **离线批**：夜间 Batch API（类"睡眠巩固"）—— 见 [protocols/evolution.md](protocols/evolution.md)

---

## 3. 章鱼器官的"归属分类"

把 19 + 两个新增（spinal_cord、immunity）器官按**是不是六模块的一部分**分组：

### 🟢 直接属于六模块

| 器官 | 归入模块 |
|---|---|
| `cerebrum/` | 1 长任务 |
| `ganglia/` | 1 长任务（未实装）|
| `nerves/graph/` | 2 工作流 |
| `suckers/` | 3 技能 |
| `genome/knowledge/` | 4 KG |
| `genome/memory/` | 5 记忆 |
| `genome/journal/` | 5 记忆（情景）|
| `hemolymph/` | 6 上下文 + 5 工作记忆 |
| `regeneration/` | 中心反思 |
| `camouflage/` | 2 工作流（A/B）+ 中心反思 |
| `genome/checkpoint/` | 1 长任务 |

### 🟡 基础设施层（六模块都要用）

不属于任何单一模块，是**所有模块共享的底座**：

| 器官 | 作用 |
|---|---|
| `arms/` | 容纳 worker agent 的壳 |
| `beak/` | 工具执行引擎 |
| `mantle/` | 沙箱隔离 |
| `eyes/models/` | LLM 适配 |
| `hearts/` | 调度节律 + 双循环隔离 |
| `nerves/bus/` | 分布式消息 |
| `siphon/` | 对外流式 I/O |
| `skin/` | 环境感知 |

### 🔴 治理/守护层（横切所有模块）

这层是**让系统不翻车**的护栏：

| 器官/文档 | 守护什么 |
|---|---|
| `spinal_cord/` | 快路径反射（绕 LLM 省钱）|
| `immunity/` | 内生安全 |
| `ink/` | 预算 + 熔断 |
| `chromatophores/` | Swarm 协作 + Boids |
| `genome/dna/` | 架构自进化（genome.md）|
| `gene-locks.md` | 字段锁 + 成熟度门 |
| `fitness.md` | 进化方向舵 |
| `invariants.md` | 68 条硬约束 |

### 一句话

> 六模块 = 章鱼的"**肌肉和大脑**"（能干活、能学习）
> 基础设施 = 章鱼的"**骨骼和循环**"（让身体跑得起来）
> 治理层 = 章鱼的"**免疫 + 基因调控**"（让系统不乱进化、不被黑）

---

## 4. 六模块视角暴露的 5 个真缺口（✅ 全部补齐）

对照六边形的职责清单，当前架构**明确没做**的，现在全部有协议覆盖：

### 缺口 1 · Workflow Rewriter（模块 2）→ ✅ [protocols/workflow_rewrite.md](protocols/workflow_rewrite.md)
- 从失败 trajectory 反推"哪个节点要重写"，自动改 DAG
- 位置：`regeneration/workflow_rewriter/`

### 缺口 2 · KG 升级 + 冲突消解（模块 4）→ ✅ [protocols/knowledge_graph.md](protocols/knowledge_graph.md) + [protocols/conflict_resolution.md](protocols/conflict_resolution.md)
- 三元组 schema + Kùzu/Neo4j + 简化本体（3 类推理）
- 冲突消解底座复用给记忆模块
- 位置：`genome/knowledge/graph/`

### 缺口 3 · Skill 回归测试集（模块 3）→ ✅ [protocols/skill_testing.md](protocols/skill_testing.md)
- 三层测试金字塔（Golden / Regression / Synthesized）
- 位置：`suckers/<id>/tests/`

### 缺口 4 · Context Recipe 评分（模块 6）→ ✅ [protocols/recipe.md](protocols/recipe.md)
- F-Recipe 层（方差 + 鲁棒性）+ per-task_type Thompson
- 位置：`regeneration/recipe_evaluator/`

### 缺口 5 · 记忆冲突消解（模块 5）→ ✅ [protocols/memory_consolidation.md](protocols/memory_consolidation.md)
- 四层记忆 + 轻/重巩固 + REM 合成 + 冲突消解复用
- 位置：`regeneration/memory_consolidator/`

**中心反思引擎 5 条产出信号全部闭环**：
新 skill / workflow 改写 / KG 三元组 / 记忆巩固 / recipe 打分 —— 全在协议层覆盖。

---

## 5. 六模块 ↔ 六大可持续进化模块（对齐）

回到最早我们讨论成本优化时列的六大进化模块，和本文的六模块对照：

| 最早的"六大进化层" | 本文的"六边形模块" |
|---|---|
| ① 长任务引擎 | 1 · 长任务引擎（完全对应）|
| ② 工作流 | 2 · 工作流进化（完全对应）|
| ③ 技能 | 3 · 技能进化（完全对应）|
| ④ 上下文/记忆 | **5 记忆 + 6 上下文**（拆成两个）|
| ⑤ 反思/自进化 | **中心 · 反思进化引擎**（居中）|
| ⑥ 成本治理 | **不在六边形里** —— 是治理层（横切）|

**差异**：
- 本文把"上下文/记忆"拆成了两个模块 —— 更正确（它们职责不同）
- 加了"知识图谱"作为底座（最早六大没有单列 KG）
- 成本治理不再是模块 —— 承认它是**横切关注点**

---

## 6. 推荐的目录重构

当前 `F:\echo-agent\` 按章鱼解剖分目录。如果要**按六模块重组**（产品视角），可以**加一层 views/**：

```
F:\echo-agent\
├── 章鱼解剖视图（当前实体目录）
│   ├── cerebrum/ ganglia/ arms/ suckers/ ...
│
├── views/                           ← NEW（只含软链接/README，不重复代码）
│   ├── capability/                    能力视图（= 六边形）
│   │   ├── 01_long_task/README.md
│   │   ├── 02_workflow/README.md
│   │   ├── 03_skill/README.md
│   │   ├── 04_knowledge_graph/README.md
│   │   ├── 05_memory/README.md
│   │   ├── 06_context/README.md
│   │   └── center_reflection/README.md
│   └── governance/                    治理视图
│       └── README.md                   (immunity / ink / gene_locks / ...)
```

**好处**：开发者按器官找代码，产品/架构师按能力看系统 —— 两种视图同一源码，零冗余。

---

## 7. 一句话总结

> **六边形是"**产品能力清单**"，章鱼是"**工程实现清单**"。**
>
> 六边形告诉你"这套系统能不能做自进化"（缺 5 处就是没做全）；
> 章鱼告诉你"这套系统骨架合不合理"（治理 + 基础设施都要有）。
>
> 两种视图必须**同时存在**：
> - 只有六边形 → 容易漏掉基础设施和治理，系统能学习但会翻车
> - 只有章鱼解剖 → 容易见树不见林，不知道进化闭环完不完整

缺口清单（§4）是 **接下来 2–3 个月该做的事**。
