# FITNESS · 适应度函数设计

> 整个 echo-agent 的**进化方向舵**。
> Fitness 决定"什么叫变好"。写错了，系统会**越进化越糟**，而且你还以为它在进步。
>
> 核心警告：**Goodhart's Law** —— *"When a measure becomes a target, it ceases to be a good measure."*
> 本文档的一半内容都在对抗这条诅咒。

---

## 1. 概念分层

Fitness 不是一个东西，是**五层**同名不同体的指标金字塔：

| 层 | 对象 | 时间尺度 | 用来决定 |
|---|---|---|---|
| **F-Skill** | 单个 Sucker | 分钟 | 该 skill 进不进 public |
| **F-Trajectory** | 单次执行轨迹 | 秒 | 该 trace 是否高质量样本 |
| **F-Recipe** ★ | 单个上下文配方（per task_type）| 天 | 该配方在此类任务下是否最优 |
| **F-Arm** | 单条 Worker | 天 | 该 arm 是否该继续接任务 |
| **F-Genome** | 整套 DNA | 周 | 该 DNA 是否晋升为 production |

每层的**权重结构不同**，但必须**一致对齐**：一条高分 trajectory 应该反推出高分 skill 和高分 recipe。否则进化系统会"内讧"。

> **F-Recipe 的独特之处**：它是唯一带"方差惩罚"和"鲁棒性"项的层 —— 配方稳定性比均值更重要。详见 [protocols/recipe.md](protocols/recipe.md) §4。

---

## 2. 核心方程（通用骨架）

所有四层共用同一数学骨架：

```python
fitness = sigmoid(
    + w_success   * success_rate
    + w_cost      * cost_efficiency
    + w_latency   * latency_efficiency
    + w_user      * user_satisfaction
    - w_loop      * loop_penalty
    - w_immune    * immune_incident_rate
    - w_breaker   * circuit_break_rate
    - w_drift     * behavior_drift          # 见第 4 节
)
```

**sigmoid** 是关键：避免某一维度炸掉（如 cost = ∞）把整体 fitness 拉到 -inf。输出始终 ∈ [0, 1]。

### efficiency 怎么算
```python
def efficiency(actual, baseline):
    # baseline 是类别中位数；高于基线得高分，但上限封顶
    if baseline == 0: return 1.0
    ratio = baseline / max(actual, 1e-6)
    return clamp(ratio, 0, 2) / 2       # 刚好基线 = 0.5，两倍好 = 1.0，无穷好也是 1.0
```

**为什么要封顶**：无封顶 → 系统会疯狂优化"省到爆"的小任务来刷分，忽略真正重要的大任务。

---

## 3. Goodhart's Law · 每一维度的翻车姿势

> 只要一个指标变成目标，它就不再是好指标。
> 下面每条都是**已经在真实 agent 项目里见过**的翻车。

### 3.1 优化 success_rate → 系统只挑简单任务做
- **症状**：任务覆盖率下降，困难任务的完成率反而降低
- **防御**：对困难度分桶，per-bucket 评估而非全局
  ```python
  success_rate_weighted = sum(
      success_in_bucket[b] * difficulty_weight[b]
      for b in difficulty_buckets
  )
  ```

### 3.2 优化 cost → 模型降档、上下文裁剪过度、答错变便宜
- **症状**：短期 $$ 省了，长期用户流失
- **防御**：cost 和 user_satisfaction 必须**耦合评估**；单独看任一方都有偏
- **铁律**：`w_user >= w_cost * 1.5`（满意度权重必须显著大于纯成本）

### 3.3 优化 latency → 跳过规划、绕过免疫
- **症状**：首 token 快了 300ms，错误率翻倍
- **防御**：latency efficiency 只在 `success==True` 的轨迹里算；失败轨迹 latency 不计分
  ```python
  if not traj.outcome.success:
      c["latency_efficiency"] = 0      # 不奖励"快速失败"
  ```

### 3.4 优化 user_satisfaction → 系统学会奉承
- **症状**：答案变得越来越"肯定"、越来越"积极"，正确性下降
- **防御**：
  1. 分离"风格满意度"和"正确性满意度"
  2. 用延迟反馈（7 天后再问用户是否真的有用），避免即时好感偏差
  3. 对"用户反复追问"打负权（说明第一次没答好）

### 3.5 惩罚 loop → 系统避免一切重试
- **症状**：合理的"失败后换方法重试"也被误判为 loop，能力退化
- **防御**：loop 检测必双信号 = 步数超限 ∧ 零信息增益（见 BDG-I1 附近逻辑）

### 3.6 惩罚 immune_incident → 免疫系统过度保守
- **症状**：Immunity 学会"什么都不放行最安全"
- **防御**：immune 侧也有 fitness —— 漏报 + 误报都要扣分（双边约束）

### 3.7 惩罚 circuit_break → 系统学会"撑到超时"
- **症状**：明明该熔断的被硬扛，最后 OOM 而非 break
- **防御**：`circuit_break_rate` 的惩罚必须**小于** `breaker_should_have_but_didnt` 的惩罚

---

## 4. Behavior Drift · 最隐蔽的退化信号

**Goodhart's Law 的总反制武器**：不管你怎么配权重，只要系统**正在变化**，就追踪它**变成什么样**。

```python
drift = KL_divergence(
    behavior_distribution_current,
    behavior_distribution_7d_ago,
)
```

**behavior_distribution** 包含：
- 各 Sucker 的调用频率分布
- 各 Arm 的任务接取分布
- 平均 trajectory 长度 / 步数
- 平均 context 用量
- Reflex 命中率
- 各 intent_type 的处理路径分布（reflex vs deliberative）

**正常**：`drift < 0.05`（系统在稳定演化）
**警戒**：`drift > 0.15`（有显著变化，需人工确认是进步还是崩坏）
**危险**：`drift > 0.30`（无论 fitness 多高都**拒绝晋升**）

> Drift 是 fitness 的"免疫系统" —— 它不关心你在往哪走，只关心你变得多快。

---

## 5. 权重按场景差异化

同一套骨架方程，不同使用场景**权重完全不同**。

### 5.1 Personal（个人助手）
```yaml
w_success:  0.35    # 对就行，不需完美
w_cost:     0.10    # 量小不敏感
w_latency:  0.25    # 响应速度极重要（用户在等）
w_user:     0.40    # 体验优先
w_loop:     0.10
w_immune:   0.10    # 个人场景攻击面小
w_breaker:  0.05
```

### 5.2 Team（团队协作）
```yaml
w_success:  0.40
w_cost:     0.20    # 多人分摊，但总成本可见
w_latency:  0.15
w_user:     0.25
w_loop:     0.15    # 团队里 loop 会被放大看见
w_immune:   0.20    # 攻击面大（多人提交 MCP）
w_breaker:  0.10
```

### 5.3 Enterprise（企业级）
```yaml
w_success:  0.35
w_cost:     0.25    # 预算硬约束
w_latency:  0.10
w_user:     0.15
w_loop:     0.15
w_immune:   0.40    # ★★★ 安全压倒一切
w_breaker:  0.15
w_drift:    0.30    # ★ 合规要求，变化必须可解释
```

### 5.4 Research / 实验室
```yaml
w_success:  0.20    # 探索性任务本来就难
w_cost:     0.15
w_latency:  0.05    # 慢无所谓
w_user:     0.10
w_loop:     0.05    # 允许 loop，也许是尝试
w_immune:   0.15
w_breaker:  0.05
w_exploration_bonus: 0.40    # ★ 专门给"尝试新路径"加分
```

### 5.5 权重矩阵总览

| 维度 | Personal | Team | Enterprise | Research |
|---|---|---|---|---|
| success | 0.35 | 0.40 | 0.35 | 0.20 |
| cost | 0.10 | 0.20 | 0.25 | 0.15 |
| latency | 0.25 | 0.15 | 0.10 | 0.05 |
| user | 0.40 | 0.25 | 0.15 | 0.10 |
| immune | 0.10 | 0.20 | **0.40** | 0.15 |
| exploration | — | — | — | **0.40** |
| drift guard | ±0.10 | ±0.15 | **±0.30** | ±0.05 |

权重本身也是 Genome 的一部分（在 `cortex_policy.fitness_weights`），可由 `camouflage` A/B 演化。

---

## 6. Fitness-of-Fitness · 权重自己的 fitness

权重写死就等于 Goodhart 赢了。权重**本身也应该进化**，用"二阶 fitness"评估权重。

### 元指标（Meta-Metrics）
```python
meta_fitness = (
    + long_term_retention     # 30 天后用户还用吗
    + cost_sustainability     # 半年后单任务成本趋势
    - drift_rate              # 系统变化速度
    - complaint_rate          # 用户实际抱怨
)
```

- **一阶 fitness**：由 `regeneration.evaluator` 每晚算，快
- **二阶 fitness**：按月算，慢。决定是否调整一阶 fitness 的权重

当二阶 fitness 和一阶 fitness 背离时（一阶升、二阶降），说明权重结构有漏洞。

---

## 7. 四层对齐（防止"分数造假"）

```
F-Skill (单 sucker)
    └─► 必须 ≤ 其最好 F-Trajectory 的平均
F-Trajectory (单次 trace)
    └─► 必须 ≤ 其所在 F-Recipe 的 7 日均值     ← NEW
F-Recipe (单配方 × task_type)
    └─► 必须 ≤ 其所在 F-Arm 的近 7 日均值
F-Arm (单 worker)
    └─► 必须 ≤ 其所在 F-Genome 的全局均值
F-Genome (整套 DNA)
    └─► 必须 ≤ meta_fitness 上月均值
```

> 如果下级 fitness > 上级 fitness，系统就会"喂分"：刻意生成高分 skill 让 arm 看起来好看。
> 这条不等式约束硬性纠正这类攻击。

---

## 8. Drift 与 Diversity 的辩证

前面说 drift 要控制在 < 0.15。但**过低**也有问题：

| drift | 状态 | 风险 |
|---|---|---|
| < 0.01 | 近乎停滞 | 进化机制没在干活，Mutator 失效 |
| 0.01–0.05 | 健康演化 | ✅ 理想区间 |
| 0.05–0.15 | 加速演化 | 要盯紧 meta_fitness |
| > 0.15 | 剧烈变化 | 必须人工 review |
| > 0.30 | 拒绝晋升 | 无论 fitness 多高都不放行 |

另一个重要指标：**Diversity**（种群多样性）

```python
diversity = pairwise_distance_avg(active_genomes)
```

- 多样性太低 → 系统变成"单一优势物种"，遇到新环境会灭绝
- 多样性太高 → 种群分裂、无法收敛
- 健康区间：`diversity ∈ [0.3, 0.7]`（归一化后）

Selector 必须同时看 fitness 和 diversity，不能纯贪心。

---

## 9. 反模式清单（翻车博物馆）

| 反模式 | 症状 | 破解 |
|---|---|---|
| **Scalar-only fitness** | 用单个浮点数压缩多目标 | 保留 Pareto 前沿，不要只看加权和 |
| **Immediate feedback only** | 只用秒级反馈 | 必须加延迟反馈（7d / 30d）|
| **Self-reported success** | agent 自己说"完成了" | 必须有外部校验（用户 / 测试 / 真实事件）|
| **Fitness by LLM judge** | 让 LLM 当裁判 | LLM 有认同偏见，会给"看起来对"的高分 |
| **Weight 写死不变** | `w_cost = 0.2` 常量 | 必放 Genome 且可进化 |
| **忽略 drift** | 只看 fitness | Drift guard 必须是硬门 |
| **忽略 diversity** | 只选精英 | Selector 必做 Thompson Sampling + 多样性惩罚 |
| **单层 fitness** | 只算 trajectory 一层 | 四层对齐不等式（见 §7）|

---

## 10. 与协议层的挂接

| Fitness 层 | 写入位置 | 消费方 |
|---|---|---|
| F-Skill | `genome/journal/skill_scores` | `regeneration.skill_forge` 晋升判定 |
| F-Trajectory | `genome/journal/traj_scores` | `regeneration.evolver` 样本筛选 |
| F-Arm | `genome/memory/arm_profile` | `cerebrum.router` 下次分配优先级 |
| F-Genome | `genome/dna/registry/<id>/fitness` | `genome.selector` 晋升/淘汰 |

评分引擎按协议层分工：
- `regeneration.evaluator`（evolution.md）→ F-Skill + F-Trajectory
- `camouflage.selector`（在 GENOME 层）→ F-Genome
- `ink.skill_cost_profile`（budget.md）→ F-Skill 的 cost 维度原料
- `immunity.memory`（immunity.md）→ F-Trajectory 的 immune 维度原料

---

## 11. 关联不变量（补充 invariants.md）

以下 CC 条目因本文增补而强化：

### CC-F1 · 五层对齐不等式（已扩展）
**参与方**：F-Skill ≤ F-Trajectory.avg ≤ F-Recipe ≤ F-Arm.avg ≤ F-Genome
**违反后果**：喂分攻击。
**执行**：Runtime assertion，Selector 入口强制校验。
**注**：原 4 层因新增 Recipe 层改为 5 层。见 [protocols/recipe.md](protocols/recipe.md) CC-R1。

### CC-F2 · Drift guard 一票否决
**参与方**：任何 fitness 晋升路径（Regeneration shadow、Genome canary）
**执行**：`if drift > 0.30: reject regardless of fitness`
**Runtime Gate**。

### CC-F3 · Weight 必进 Genome
**参与方**：所有 `w_*` 系数
**违反**：权重写死常量 = 失去二阶进化能力
**Lint**：扫描 `w_success = ` / `w_cost = ` 等形式的模块级常量赋值（除 config 模块外）。

### CC-F4 · 延迟反馈闭环
**参与方**：F-Trajectory + F-Skill
**描述**：user_satisfaction 不能只算当轮评分；必须有 7d 回溯。
**Runtime**：每轨迹存 `pending_feedback: wait_until=now+7d`。

---

## 12. 配置契约（对齐 config.yaml）

```yaml
fitness:
  scenario: personal        # personal | team | enterprise | research
  weights:                   # 可 Camouflage A/B；也在 Genome 里
    w_success: 0.35
    w_cost: 0.10
    w_latency: 0.25
    w_user: 0.40
    w_loop: 0.10
    w_immune: 0.10
    w_breaker: 0.05
  drift:
    warn_threshold: 0.15
    reject_threshold: 0.30
    distribution_window_days: 7
  diversity:
    min: 0.30
    max: 0.70
  meta_fitness:
    enabled: true
    schedule: "0 3 1 * *"    # 每月 1 号 03:00
    retention_window_days: 30
  delayed_feedback:
    enabled: true
    wait_days: 7
```

---

## 13. 把"好"这件事装进系统

总结一句：

> **Fitness 是 Agent OS 唯一的价值观入口。**
> 架构可以仿生、协议可以工程、不变量可以 lint，
> 但"什么叫做得好"永远是**人类必须持续参与**的决策。
>
> Fitness 函数不是写完就完事 —— 它是系统与人类价值观对话的**可编辑文件**。

---

## 14. 做 / 不做清单

### 做
- ✅ 多层 fitness 分开算，用对齐不等式绑定
- ✅ 权重进 Genome，参与进化
- ✅ drift + diversity 作为硬门
- ✅ 延迟反馈 7d / 30d 闭环
- ✅ 场景化权重模板（personal / team / enterprise / research）
- ✅ 二阶 fitness 每月评估

### 不做
- ❌ 用单一浮点数压扁多维目标
- ❌ 让 LLM 当唯一裁判
- ❌ 权重写死常量
- ❌ 只看 fitness 不看 drift
- ❌ 即时反馈就结案
- ❌ Selector 纯贪心（忽略多样性）
