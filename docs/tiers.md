# TIERS · 分档交付计划

> **把"研究笔记"变成"工程蓝图"。**
>
> 现状：20 器官 + 15 协议 + 139 不变量 = 完整体系，但不是可立刻开工的蓝图。
> 本文的使命：**三档切分，让 MVP 有最短可跑路径**。

---

## 三档定义

| 档 | 时间窗 | 目标 | 规模 |
|---|---|---|---|
| **MVP** | 0–3 月 | 跑通一条闭环：INGEST → STORE，可断点续跑 | 8 器官 / 4 协议 / 14 不变量 |
| **Core** | 3–9 月 | 生产级治理 + 基础自进化 | 13 器官 / 9 协议 / ~40 不变量 |
| **Full** | 9 月+ | 完整自进化实体 | 20 器官 / 15 协议 / 139 不变量 |

**铁律**：禁止在 MVP 阶段"顺手实现"Core/Full 的东西 —— 那是复杂度失控的开始。

---

## 1. 器官分档

### 🟢 MVP 档（8 个，必须）
| 器官 | 最小版本 | 复杂度 |
|---|---|---|
| `cerebrum/` | Planner 一个文件，LLM 做分解 | 低 |
| `ganglia/` | 单 Ganglion，串行跑 | 低 |
| `arms/` | 1 条 code_arm | 低 |
| `suckers/` | 5–10 个手写 skill | 低 |
| `beak/` | fork E:\echo core | 零 |
| `mantle/` | 只用 local/docker | 低 |
| `eyes/` | fork echo models，单 provider | 零 |
| `genome/` | checkpoint + journal | 低 |

### 🟡 Core 档（+5 个，3–9 月）
| 器官 | 启用时机 |
|---|---|
| `hemolymph/` | 当 context 打包开始复杂（超 2 个数据源）|
| `ink/` | 当线上预算爆过一次之后 |
| `immunity/` | 当接入第三方 MCP server 之后 |
| `nerves/graph/` | 当任务需要真 DAG（不是线性序列）|
| `regeneration/` | 有 ≥ 100 条高质量 trajectory 之后 |

### 🔵 Full 档（+7 个器官 + `genome/dna` 扩展，9 月+）
| 器官 | 启用时机 |
|---|---|
| `spinal_cord/` | Reflex 命中率有积累数据之后 |
| `hearts/` | 多进程/多机部署之后 |
| `chromatophores/`（或拆分后）| 多 arm 并发之后 |
| `camouflage/` | 数据量足够做策略 A/B 之后 |
| `skin/` | 需要外部 webhook 触发之后 |
| `siphon/` | 对外流式 API 有实际消费者之后 |
| `tentacle/` | 需要移动端 / 跨设备执行触点之后 |
| `genome/dna/` | **最后**：架构已稳定 6 个月以上 |

---

## 2. 协议分档

### 🟢 MVP 必须（4 份）
| 协议 | 实现到什么程度 |
|---|---|
| [digestion.md](protocols/digestion.md) | 7 阶段串行；STORE 可同步 |
| [budget.md](protocols/budget.md) | **只要 reserve/commit 原子 + 预算单向**；circuit breaker 不做 |
| [immunity.md](protocols/immunity.md) | **只做 Innate（来源白名单）**；Memory/Adaptive 不做 |
| [evolution.md](protocols/evolution.md) | **只做 skill_forge 一条回路**；reflection/camouflage 延后 |

### 🟡 Core（+4 份）
| 协议 | 触发条件 |
|---|---|
| [skill_testing.md](protocols/skill_testing.md) | 第一个自动生成的 skill 上线之前 |
| [recipe.md](protocols/recipe.md) | F-Trajectory 足够稳定（≥ 1000 样本）|
| [reflex.md](protocols/reflex.md) | 观察到大量重复查询之后 |
| [genome.md](protocols/genome.md) | **只做三门**，不做 CRDT |

### 🔵 Full（+7 份）
| 协议 | 触发条件 |
|---|---|
| [swarm.md](protocols/swarm.md) | 真有多 arm 并发之后 |
| [distribution.md](protocols/distribution.md) | 边端部署之后 |
| [workflow_rewrite.md](protocols/workflow_rewrite.md) | 工作流库 ≥ 20 个之后 |
| [conflict_resolution.md](protocols/conflict_resolution.md) | 有矛盾事实问题之后 |
| [knowledge_graph.md](protocols/knowledge_graph.md) | 事实检索需推理之后 |
| [memory_consolidation.md](protocols/memory_consolidation.md) | Journal > 10k 条之后 |
| [realtime_workbench.md](protocols/realtime_workbench.md) | 前端 workbench 需要可回放 current-frame 状态之后 |

---

## 3. 不变量分档

### 🟢 MVP 硬守（14 条）

这是**启动第一天**就必须在 lint / runtime assert 里的：

| ID | 内容 |
|---|---|
| DIG-I1 | 阶段契约严格 |
| DIG-I4 | 用户响应不被 STORE 阻塞 |
| DIG-I6 | 每阶段必发 OTel span |
| IMM-I1 | Beak.bite 前必过 immunity.check（哪怕空实现）|
| BDG-I1 | 预算单向 |
| BDG-I2 | Reserve 原子 |
| BDG-I3 | Reserve/Commit 成对 |
| EVO-I7 | Evolver 不改 LLM 权重 |
| GEN-I6 | Genome 变更必入 Journal |
| CC-1 | 反射不绕免疫（即使 reflex 未实现，占位 check 必留）|
| CC-8 | 预算语义环闭合 |
| LINT-03 | Bio name 不入代码（强制 NAMING）|
| LINT-04 | 无直接 LLM 调用（必经 eyes.ModelRouter）|
| LINT-05 | Task 必带预算 |

**14 条能写成 200 行 pyright plugin + pytest 断言集。这是工程落地的最小代价。**

### 🟡 Core（+26 条，共 40 条）
- 补齐 SKT-I1..I8（skill testing 全套）
- 补齐 RCP-I1..I9（recipe evolution）
- 补齐 IMM 适应性免疫部分
- 补齐 GEN-I1/I2/I3（三门 + nuclear 人审 + 可回滚）
- 补齐 CC-F1/F2/F3（Fitness 对齐 + drift guard）

### 🔵 Full（+99 条，共 139 条全集）
- 其余全部，含 CRDT、Boids、REM、conflict resolution、Gene Locks 完整六类等

---

## 4. Fitness 层级分档

原设计 5 层，MVP 阶段**只用 1 层**：

| 层 | MVP | Core | Full |
|---|---|---|---|
| F-Skill | ❌ | ✅ | ✅ |
| **F-Trajectory** | **✅ 唯一使用** | ✅ | ✅ |
| F-Recipe | ❌ | ✅ | ✅ |
| F-Arm | ❌ | ⚠️ 选做 | ✅ |
| F-Genome | ❌ | ❌ | ✅ |

对齐不等式也相应缩短 —— MVP 只有单层，不需要守不等式。

---

## 5. Gene Locks 分档

原设计 6 类锁 + 5 级成熟度，MVP 阶段**只用 2 类锁、0 级成熟度**：

| 锁类型 | MVP | Core | Full |
|---|---|---|---|
| IMMUTABLE | ✅ 简化版 | ✅ | ✅ |
| MONOTONIC | ❌ | ✅ | ✅ |
| QUORUM | ✅ 人审即可 | ✅ | ✅ |
| LEVEL（成熟度 0–4）| ❌ | ❌ | ✅ |
| CONDITIONAL | ❌ | ❌ | ✅ |
| TEMPORAL | ❌ | ⚠️ 选做 | ✅ |
| CASCADE | ❌ | ❌ | ✅ |

MVP 阶段的"锁"：一个白名单 + 人工 approve 的 PR 流程即可，不需要任何 runtime gate。

---

## 6. 被 MVP 踢出去的"高级设计"清单

明确**不做**（诚实面对）：

| 设计 | 为什么 MVP 不做 |
|---|---|
| CRDT 合并 | 单节点部署没有并发写 |
| Boids 三原则 | 单 Arm 没群体 |
| 双循环心脏 | asyncio + semaphore 就解决 |
| 5 层 Fitness | 没那么多数据层要对齐 |
| 成熟度 0–4 级 | 系统还没进化能力，锁什么 |
| REM 合成 | 没数据给它"做梦" |
| 冲突消解全套 | 第一批数据没机会冲突 |
| 上下文配方评估 | 没统计量支撑 A/B |
| 完整反思闭环 5 产出 | 只做 skill_forge 够了 |
| 分布式 edge/cloud | 一个进程解决一切 |

**这些都是 Core / Full 阶段才值得引入的复杂度**。

---

## 7. MVP 的 3 个月具体交付

### Month 1 · 骨架 & P0 fork
- W1: 拷 E:\echo 的 P0 模块（mcp/sandbox/graph/models/core/hooks）
- W2: 改包名 + 剥 FastAPI 耦合
- W3: pytest + 基础 CI + 14 条 MVP 不变量的 lint/assert 实现
- W4: 烟测：手写一个 skill → 进 Mantle → Beak 执行

### Month 2 · 单腕跑通
- W5: Cerebrum Planner MVP（plan-and-execute 范式）
- W6: 单 Ganglion + code_arm + Checkpointer 接通
- W7: Hemolymph v1（三层 context 范式）
- W8: 接 OTel，span 全覆盖

### Month 3 · 长任务验收
- W9: 5 个手写 code_arm skill（参考 Aider edit block）
- W10: 压测 "5 步代码任务 + 断网续跑"
- W11: 简化版 Immunity（只来源白名单）+ Budget（只预算硬顶）
- W12: 验收里程碑：**给定真实 git 仓库 + 任务目标，能独立完成一个 5 步的代码补丁任务**

---

## 8. 从 MVP 到 Core 的触发条件（不用提前做）

每个 Core 功能引入都有一个**数据/痛点触发条件**。没达到条件就不要做：

| 功能 | 触发条件 |
|---|---|
| Skill Testing 全套 | 自动生成过 1 个 skill |
| Recipe 评估 | F-Trajectory 样本 ≥ 1000 |
| 完整 Immunity 适应层 | 接入过第三方 MCP server |
| Ink Circuit Breaker | 线上发生过预算事故 1 次 |
| Regeneration 反思循环 | Journal 积累 ≥ 100 条高质量 trajectory |
| Genome 三门 | 有过 ≥ 3 次配置失误导致的事故 |
| 基因锁 | 系统开始自主修改配置之后 |

---

## 9. 从 Core 到 Full 的触发条件

| 功能 | 触发条件 |
|---|---|
| CRDT | 正式上生产多 Edge 部署 |
| Boids 三原则 | 同任务并发 arms ≥ 3 |
| 5 层 Fitness | Genome 进化开始产生多版本 |
| KG 升级到 Neo4j/Kùzu | 知识检索需 ≥ 2 跳推理 |
| 成熟度 0–4 级 | 有过一次进化相关事故 |
| Workflow Rewrite | Workflow 库 ≥ 20 个 |

---

## 10. 关键判断

> **架构复杂度 ≠ 系统能力**。
>
> 139 条不变量的系统**不一定**比 14 条不变量的系统更稳。
> 早期过度治理反而会让系统**不敢演化**（每动一处都撞锁）。
>
> **MVP 阶段的目标不是"做全"，是"做对 14 条"**。
> 做对了，剩下 125 条会按需长出来。
> 做错了，长多少都白搭。

---

## 11. 双档版 config.yaml

MVP 档可能不到 100 行 config；Full 档是现在的 400 行。
建议用两个文件：
```
config.yaml          # MVP（最小可运行）
config.full.yaml     # 完整示例（参考用）
```

MVP config 只包含：
- cerebrum.planner_model
- arms.code_arm 的定义
- genome.checkpoint_backend
- eyes.providers 一个
- budget.per_task 硬顶
- immunity.trusted_sources 白名单

其他全靠默认值。

---

## 12. 文档读者路径

按**你是谁**读文档：

### "我要马上启动 MVP"
→ 只读：本文档 + [forklist.md](forklist.md) + [protocols/digestion.md](protocols/digestion.md) + [naming.md](naming.md)

### "我要理解全貌"
→ 加读：[principles.md](principles.md) + [architecture.md](architecture.md) + [six-modules.md](six-modules.md)

### "我要参与长期演化"
→ 加读：[genome.md](genome.md) + [fitness.md](fitness.md) + [gene-locks.md](gene-locks.md) + 全部 protocols/ + [invariants.md](invariants.md)

**三档读者，三档文档，各取所需。**
