# PRINCIPLES · 去语境化的仿生 Agent 设计原则

> 把 "仿生 → Agent 框架" 的映射抬到**纯理论层**，与任何具体产品 / 业务脱钩。
> 本文档是**方法论地图**，下面的 echo-agent 器官是其**一种实现**，不是唯一实现。

---

## 六大抽象原则

```
1. 分层决策 · Reactive + Deliberative
2. 去中心协作 · Swarm + Blackboard
3. 内生安全 · Self / Memory / Adaptive
4. 进化优化 · Variation / Selection
5. 分布式执行 · Edge + Cloud
6. 流水线处理 · Pipeline
```

每条原则的**充要结构**如下。

---

### ① 分层决策 Reactive + Deliberative

**定义**：任何稳定智能体都至少具备两条并行通路 —— 一条**快而廉**（反射），一条**慢而强**（思考），顶上有一个**路由者**在二者之间切换。

**最小可工作结构**：
- Fast Path：规则 / cache / 小模型
- Slow Path：LLM / planner
- Meta-Control：决定哪条路径优先

**失败特征**：只有 Slow Path → 成本爆炸 + 延迟不稳 + 在简单问题上显得愚蠢。

---

### ② 去中心协作 Swarm + Blackboard

**定义**：复杂协作来自**大量简单体 + 共享通信介质**。三种主流通信模式可独立选用也可叠加：

| 模式 | 通信载体 | 类比 |
|---|---|---|
| Orchestrated | 中心指令 | 指挥家乐团 |
| Decentralized（Swarm）| 点对点消息/gossip | 蜂群信息素 |
| Blackboard | 共享状态面 | 蚁群巢穴 |

**最小可工作结构**：
- 多个具备局部决策能力的 agent
- 至少一种非中心化的通信介质

**失败特征**：所有决策都由主 agent 转发 → 主 agent 成瓶颈 + 单点失效即全盘死。

---

### ③ 内生安全 Self / Memory / Adaptive

**定义**：安全能力应**参与决策**，而非事后拦截。三个内生子能力缺一不可：

| 能力 | 问题 | 对应机制 |
|---|---|---|
| Self-recognition | 谁在调用？ | 来源签名 / 身份 |
| Memory | 历史有没有被打过？ | 攻击模式库 |
| Adaptive | 这次行为异常吗？ | 在线风险评分 |

**最小可工作结构**：
- 来源可追溯
- 失败模式可累积
- 风险评分可动态更新

**失败特征**：只有权限白名单 + 沙箱 → 新型攻击完全无法识别，一次受害就再次受害。

---

### ④ 进化优化 Variation / Selection

**定义**：系统演化不是"设计"，而是**生成多样性 → 环境筛选 → 继承**的循环。

**最小可工作结构**：
- **多版本并存**（prompt/policy/routing）
- **自动评估**（成功率 × 成本 × 用户满意度）
- **自动淘汰**（差的下线、好的放量）

**失败特征**：靠人工调优 → 版本越来越多 + 不敢下线 + 系统臃肿到没人理解。

---

### ⑤ 分布式执行 Edge + Cloud

**定义**：智能不是集中在一处，而是**按延迟/隐私/算力需求下沉**到合适的物理位置。

**最小可工作结构**：
- Edge：本地推理，低延迟、重隐私
- Cloud：全局推理，高算力、跨会话
- 动态调度：按 `latency_budget + privacy_class + task_weight` 路由

**失败特征**：所有决策都在云 → 离线即死 + 隐私外泄 + 网络抖动时整个系统晃动。

---

### ⑥ 流水线处理 Pipeline

**定义**：复杂任务是**分阶段消化**，不是端到端一次完成。

**最小可工作结构**：
```
Ingest → Parse → Plan → Execute → Synthesize → Store
```
每阶段：
- 输入/输出有明确类型
- 可独立替换 / 监控 / 回放
- 失败可定位到阶段

**失败特征**：一个 prompt 包打天下 → 出错无法定位 + 中间结果不可复用 + 无法加观测。

---

## 与主流框架的空白对照

| 原则 | 主流 A | 主流 B | 主流 C | 主流 D | 主流 E |
|---|---|---|---|---|---|
| ① 分层决策 | ✘ | ✘ | ✘ | ✘ | △ |
| ② 去中心协作 | △ | ✘ | △ | △ | ✘ |
| ③ 内生安全 | ✘ | ✘ | ✘ | ✘ | △ |
| ④ 进化优化 | ✘ | ✘ | ✘ | ✘ | ✘ |
| ⑤ 分布式执行 | ✘ | ✘ | ✘ | ✘ | ✘ |
| ⑥ 流水线 | ✔ | △ | ✔ | ✔ | ✔ |

**结论**：主流框架**最多覆盖 1–2 条原则**。Agent OS 的真正门槛不是"会思考"，而是剩下 4 条。

---

## 原则 → echo-agent 器官映射

这是"方法论"落到"一种实现"的桥：

| 原则 | 主责器官 | 协作器官 |
|---|---|---|
| ① Reactive + Deliberative | `spinal_cord/` + `cerebrum/` | `eyes/`（路由入口）|
| ② Swarm + Blackboard | `arms/` + `chromatophores/` + `hemolymph/` | `nerves/bus/` |
| ③ Self / Memory / Adaptive | `immunity/` | `mantle/`（先天）+ `ink/`（炎症）|
| ④ Variation / Selection | `regeneration/` + `camouflage/` | `genome/journal/` |
| ⑤ Edge + Cloud | `arms/edge/` vs `arms/cloud/` | `cerebrum/` 路由器 |
| ⑥ Pipeline | 整条 `eyes → cerebrum → ganglia（未实装）→ beak → genome` | 所有器官 |

**反向提醒**：如果哪天不用章鱼仿生，这六条原则依然成立 —— 换一套命名（比如"城市交通""工厂流水"），同样能搭出 Agent OS。**器官是 UI，原则才是 API**。

---

## 一个冷静的判断

> 现在绝大多数 Agent 框架，是"**会思考的工具链**"。
> 六大原则齐备的系统，才是"**具有生命特征的系统**"。

两者差的不是模型，不是 token，是**架构层的完整性**。

---

## 延伸阅读

- [architecture.md](architecture.md) — 本原则的章鱼仿生实现
- [ROADMAP.md](ROADMAP.md) — 按原则分层验收的 5 阶段路线
- [forklist.md](forklist.md) — 哪些组件可直接 fork 上游
