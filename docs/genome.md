# GENOME · 可编辑遗传密码（Editable DNA）

> 从"可进化的行为"升维到"可进化的架构"。
> `config.yaml` 是启动参数；**Genome 是活体 DNA**。
> 核心判断：**没有可编辑 Genome 的 Agent OS = 固定架构 + 手工优化，永远进化不动**。

---

## 核心等式

```
系统         = 章鱼个体
系统能力     = 遗传密码（Genome / DNA）
运行行为     = 表现型（Phenotype）
运行环境     = 任务 / 用户 / 负载
```

> **能力不是代码决定，而是基因表达决定。**

---

## Genome 数据模型

```python
EchoGenome = {
    "genome_id": uuid,              # 每个版本独立 id
    "parent_ids": list[uuid],       # 支持交叉（可多亲本）
    "created_at": datetime,
    "origin": "human" | "mutation" | "crossover" | "import",
    "schema_version": str,

    # ── 八大 DNA 片段 ──
    "cortex_policy":     CortexPolicy,     # Cerebrum 规划策略
    "scheduler_policy":  SchedulerPolicy,  # Hearts 节律 + 路由
    "memory_policy":     MemoryPolicy,     # Hemolymph 上下文配比 + Blackboard TTL
    "arm_registry":      list[ArmSpec],    # Arms 构成（哪些腕存在）
    "tool_affinity_map": dict[str, list],  # Sucker → Arm 路由
    "risk_profile":      RiskProfile,      # Immunity + Ink 的严苛度
    "event_topology":    EventTopology,    # Chromatophores 话题图 + Boids 权重
    "learning_rate":     float,            # Regeneration 步长

    # ── 元信息 ──
    "fitness": FitnessRecord | None,       # 由 Selector 评分填充
    "status": "draft" | "shadow" | "canary" | "production" | "retired",
    "deployed_ratio": float,               # 0..1，当前灰度比例
}
```

### 片段示例（实际值）

```json
{
  "scheduler_policy": "latency_first",
  "memory_policy": "recency_weighted",
  "arm_registry": ["search", "code", "browser"],
  "tool_affinity_map": {
    "search": ["arm-search"],
    "code":   ["arm-code"]
  },
  "risk_profile": "strict",
  "event_topology": "broadcast",
  "learning_rate": 0.2
}
```

同一 Genome 在不同环境有不同表现型：

| 环境 | 表现型 |
|---|---|
| 低延迟边端 | 强本地执行、Spinal Cord 命中率高 |
| 云端集群 | 强规划、多 Arm 并发 |
| 预算紧张 | Scheduler 降频、Reflex 阈值放宽 |

---

## 生物学进化机制 → 工程实现

### 1) 变异 Mutation
随机小幅修改 Genome 单个字段。
```text
learning_rate: 0.2 → 0.25
scheduler_policy: latency_first → cost_balanced
```
每次变异只动**一个维度**，便于归因。

### 2) 交叉 Crossover
取两条亲本 Genome，各取一半片段合成新 Genome。
```text
Genome A (快)  +  Genome B (准)  →  Genome C (衡)
```
需保证合成后 Schema 合法（见 [protocols/genome.md](protocols/genome.md) 的 validator）。

### 3) 选择 Selection
适应度函数（Fitness）：
```python
fitness = (
    w_success   * success_rate
  + w_latency   * (1 / latency)
  + w_cost      * (1 / cost)
  + w_user      * user_satisfaction
  - w_loop      * loop_penalty
  - w_immune    * immune_incident_rate
)
```
权重本身**也在 Genome 里**（learning_rate 旁边），所以"什么叫好"也能进化。

### 4) 表达 Expression
Genome 不是配置 —— 是"怎么运行"的规则。
Expression 引擎把 Genome 翻译成运行时行为：
```
Genome.scheduler_policy = "latency_first"
    ↓
Hearts.systemic.tick_interval_ms = 200 (更快)
Hearts.branchial.priority = "network_io_first"
```

---

## 与既有 Regeneration 的分工

| 层 | 对象 | 例 |
|---|---|---|
| **Regeneration**（已有）| 进化**内容**：skill / rule / strategy | "学会了写 pytest 补丁的 sucker" |
| **Genome Evolution**（本文档）| 进化**结构**：拓扑 / 策略 / 注册表 | "学会了 browse_arm 在云端比边端合适" |

两者在 `regeneration/evolver/` 流水线里都是消费者，但动的东西不同：
- Regeneration 的产物 → 写入 `suckers/custom/` 或 `cerebrum` 的 prompt 段
- Genome Evolution 的产物 → 写入 `genome/dna/registry/` 的新版本

---

## 种群（Population）概念

**不再是"一个系统 + 一份配置"**，而是：

```
种群 = 同时在线的多个 Genome 版本（v1 / v2 / v3 ...）
    每个接一定比例的生产流量
    Fitness 高的逐步扩大比例
    Fitness 低的被 retire
```

这把**架构本身**做成了 Thompson Sampling 的老虎机。Camouflage 原本只对**策略参数**做 A/B，现在对**整套 DNA** 做 A/B。

---

## 架构图

```
       ┌──────────────────────────┐
       │   Genome Registry        │
       │   所有版本的 DNA         │
       │   (CRDT-backed)          │
       └───────────┬──────────────┘
                   │
    ┌──────────────┴──────────────┐
    │                             │
    ▼                             ▼
┌────────────┐              ┌──────────────┐
│ Mutator    │              │ Crossover    │
│ 单字段变异 │              │ 亲本合成     │
└─────┬──────┘              └──────┬───────┘
      └──────────────┬─────────────┘
                     ▼
              ┌──────────────┐
              │ Simulation   │   Shadow Execution
              │ 影子验证     │   (不影响生产)
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Fitness Eval │
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Selector     │   按 fitness + 预算淘汰
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Expression   │   → 应用到 Runtime
              │ + Patch      │   (CRDT 热更新)
              └──────────────┘
```

---

## 三个哲学转折

### 从"设计"到"演化"
```
以前：写死系统架构
现在：定义进化空间（哪些维度可变 + 边界 + fitness）
```

### 从"调优"到"选择"
```
❌ 人工调 scheduler 参数
✅ 定义 fitness，系统自己选
```

### 从"单版本"到"种群"
```
多 Genome 并行存在 / 竞争 / 交配 / 淘汰
```

---

## Genome vs Config.yaml 的关系

- `config.yaml` **= 初始 Genome** + 常量系统设置（密钥、端口等）
- 启动后，DNA 部分被拷贝到 `genome/dna/registry/v0`，进入进化循环
- 非 DNA 部分（密钥/端口）仍由 config 管，不可变异

**清晰分界**：哪些字段属于 Genome？
> 改了之后系统行为会变、且"变好变坏"可被 fitness 评估的 —— 就是 DNA。
> 改了之后系统连不上 / 起不来的 —— 就是配置。

---

## 四条硬约束（不变量预告）

1. **任何 DNA 变更必先 Shadow 再 Canary**（不得直接进生产）
2. **Schema 校验不过的 Genome 不入 Registry**（防 NaN 染色体）
3. **Production Genome 必须随时可回滚**（上一代全量保留）
4. **Genome 变更事件必入 Journal**（可审计）

详细工程协议见 [protocols/genome.md](protocols/genome.md)。

---

## 最终判断

> 🐙 章鱼不是"一个系统"，是**一个能在环境中不断改写自己结构的系统**。

没有可编辑 Genome：固定架构 + 手工优化，天花板触顶。
有可编辑 Genome：**Evolutionary Agent OS** —— 可进化实体。

这是 Agent OS 从"工程产品"跨入"人工生命"的门槛。
