# 模块清单 · echo-agent

> 本文件列出项目各模块的来源、改造强度、优先级。仅作内部架构参考。

---

## 模块总表

| # | 模块路径 | 改造强度 | 优先级 |
|---|---|---|---|
| 1 | `suckers/mcp/` | 低 | P0 |
| 2 | `mantle/{local,docker,ssh,k8s}/` | 低 | P0 |
| 3 | `nerves/graph/` | 中（扩节点类型）| P0 |
| 4 | `eyes/models/` | 低 | P0 |
| 5 | `suckers/loader/` + `suckers/public/` | 低 | P0 |
| 6 | `beak/core/` | 低 | P0 |
| 7 | `genome/checkpoint/` | 中（支持分布式 key）| P1 |
| 8 | `genome/journal/` | 低 | P1 |
| 9 | `nerves/hooks/` | 低 | P1 |
| 10 | `genome/knowledge/` | 中（改存储抽象）| P2 |
| 11 | `siphon/openai_gateway/` | 低 | P2 |
| 12 | `suckers/public/builtins/` | 低 | P2 |
| 13 | 独立仓或 `ui/` 子模块 | 高（对接新后端 API）| P3 |

---

## 具体 Fork 操作模板

以 MCP 客户端为例：

```bash
# 1. 拷贝源码（保留目录结构）
cp -r /e/echo/backend/packages/harness/echo/mcp/ /f/echo-agent/suckers/mcp/

# 2. 批量替换包名
cd /f/echo-agent/suckers/mcp
grep -rl "from echo" . | xargs sed -i 's/from echo/from runtime/g'
grep -rl "import echo" . | xargs sed -i 's/import echo/import runtime/g'

# 3. 移除对 FastAPI / 上层业务的耦合（按需）
# 4. 在 suckers/__init__.py 注册为 "mcp 吸盘簇"
```

**其他模块按同样流程**，只改三项：包名、顶层 import、注册到新模块的 `__init__.py`。

---

## 适配层

部分模块需要一层"生物化适配"让它们融入仿生架构：

### `mantle/` 适配
- `SandboxProvider` 接口基本不变
- 新增 `ArmMantle` 包装类：每条 Arm 启动时自动申请一个 Mantle 实例
- 出口统一为 `mantle.wrap(arm_id) -> SandboxContext`

### `nerves/graph/` 适配
- 保留 6 节点 / 4 边类型
- 新增 `ArmNode` 节点类型：代表"把这个子图交给某条 Arm 自主执行"
- 新增 `ChromatophoreEdge` 边类型：代表"触发广播而非直接调用"

### `suckers/loader/` 适配
- SKILL.md frontmatter 新增两个字段：
  - `affinity: [code, data, search, ...]` —— 该吸盘亲和哪类 Arm
  - `cost_profile: low | mid | high` —— 供 Ink 做预算预估
- 保留热加载机制

### `genome/checkpoint/` 适配
- Checkpointer 假设单进程
- 新增 `distributed_key = (task_id, arm_id)`，允许多 Ganglion 并发写
- 用 WAL 模式的 SQLite 或升级到 PostgreSQL

---

## 从零建的模块（差异化护城河）

| 模块 | 设计动机 |
|---|---|
| `cerebrum/` | 决策职责分离，按腕-层-群三层组织 |
| `ganglia/` | 腕本地自治 |
| `arms/` | 半自主 worker，具专长 |
| `chromatophores/` | 腕间广播 |
| `ink/` | 预算/熔断层 |
| `hearts/` | HA 调度 |
| `camouflage/` | 策略 A/B |
| `hemolymph/` | context 打包集中化 |
| `regeneration/` | 完整反思闭环 |

---

## 模块准入检查清单

每接入一个模块，过一遍：

- [ ] 路径已在本文档登记
- [ ] 包名已统一
- [ ] 顶层 import 已清理
- [ ] 单测能独立跑通
- [ ] 在 `<module>/README.md` 标注模块定位
- [ ] 在文件头注释模块出处

---

# Part 2 · 阶段 0/1 覆盖盘点

> 本节回答：起步阶段需要哪些模块就位？哪些是 v1 必须，哪些可推迟？

## 阶段 0 · 孵化期覆盖矩阵（1–2 周）

| 需求 | 实现位置 | 覆盖度 | 备注 |
|---|---|---|---|
| MCP 客户端 | `suckers/mcp/` | ✅ 100% | 带 OAuth |
| 沙箱（local/docker/ssh/k8s）| `mantle/{local,docker,ssh,k8s}/` | ✅ 100% | 四种 provider 全有 |
| DAG 执行器 | `nerves/graph/` | ✅ 100% | 6 节点 / 4 边，改两个新增类型 |
| 多 Provider 模型适配 | `eyes/models/` | ✅ 100% | 10+ provider |
| Beak Core (BaseTool/Message) | `beak/core/` | ✅ 100% | |
| SKILL.md 加载器 + 公共技能 | `suckers/loader/` + `suckers/public/` | ✅ 95% | 需加两个 frontmatter 字段 |
| Hooks（pre/post tool use）| `nerves/hooks/` | ✅ 100% | |
| Checkpointer | `genome/checkpoint/` | ✅ 95% | 单进程的，需加分布式 key |
| Journal | `genome/journal/` | ✅ 100% | |

**结论 · 阶段 0 全覆盖**。

## 阶段 1 · 单腕期覆盖矩阵（3–6 周）

| 需求 | 来源 | 覆盖度 | 缺口处理 |
|---|---|---|---|
| Cerebrum Planner MVP | — | ⚠️ 40% | **从零写**（决策职责分离）|
| Ganglia 本地自治 | — | ❌ 0% | **纯新写**（腕本地自治）|
| Arm 半自主 worker | — | ⚠️ 30% | **重写**（具专长半自主体）|
| code_arm 技能集 | `suckers/public/` | ✅ 60% | 基础技能够用，专长技能要补 |
| Hemolymph v1（上下文打包）| — | ⚠️ 30% | **集中化重写** |
| OpenAI-Compat Gateway | `siphon/openai_gateway/` | ✅ 100% | |
| Trajectory 观测 | `genome/journal/` | ⚠️ 60% | **补 OTel span**（见下节）|
| Multi-Session Thread State | `genome/journal/` | ✅ 100% | |

**结论 · 阶段 1 需要新写 3 处**：Cerebrum、Hemolymph、Trajectory 可观测性。

---

## 阶段 1 缺口补充

只推荐**实打实要用**的，不列无关项目。每条给 **github / license / 子模块要点**。

### 1. Cerebrum Planner 的参考实现

#### plan-and-execute 范式
- **实现要点**：planner → executor → replan 三段式职责分离
- 重点看：Planner node / Executor node / Replan node 三段式

#### 长任务状态管理
- 真实做过"从目标到步骤"的分解，代码清晰
- 重点看 `state.py` 怎么管长任务状态

### 2. Hemolymph 设计参考（上下文 + 记忆打包）

#### context 三层抽象
- **要点**：main_context / recall_storage / archival_storage 三层分离
- Block 抽象（可编辑的上下文段）很实用

#### hybrid retrieval
- 有 KG + 向量 + 图神经网络的混合召回
- 注意：架构偏重，只拿策略代码

### 3. Trajectory 可观测性

#### OTel SDK
- **直接用 SDK 不 fork**：`pip install langfuse`
  - 目标是给 `genome/journal/` 加 OTel span + trace view
  - 符合 DIG-I6（每阶段必发 OTel span）
  - 价格：自建免费，SaaS 有 free tier

#### OpenTelemetry 标准
- 如果想更标准的 OpenTelemetry，不绑 Langfuse 产品
- **直接用 SDK**：`pip install traceloop-sdk`

### 4. Code_arm 技能补充

#### edit block 格式
- **要点**：edit block 格式和 diff 应用策略
- **skill candidates to fork**: `git_diff_apply`, `file_edit_block`, `repo_map_builder`
- 对"怎么让 LLM 改代码不改坏"研究最深

#### context providers
- **要点**：repo map / recent edits 思路

### 5. Sandbox 增强（可选，如果要更快）

#### 远程 sandbox
- **替代自建 docker 沙箱**：已做好远程 sandbox + SDK
- 不 fork 代码，直接 `pip install e2b-code-interpreter`
- 适合 Cloud tier Arm，不适合 Edge
- 价格：有 free tier，高频要付费

### 6. Reflex / Semantic Router（为阶段 2 备料）

#### intent classification
- **要点**：intent classification
- 本项目 Spinal Cord 的 `edge_slm` 类反射直接用这个
- 不 fork 代码，直接 `pip install semantic-router`

---

## 外部源 Fork 优先级矩阵

按"阶段 1 要不要"分档：

| 外部源 | 阶段 1 必需？ | 理由 |
|---|---|---|
| plan-and-execute 范式 | ✅ 必读 | Cerebrum 职责分离的参考 |
| context 三层抽象 | ✅ 必读 | Hemolymph 设计原型 |
| OTel SDK | ✅ 必装 | DIG-I6 强制要求 OTel |
| edit block 格式 | ⭕ 推荐 | code_arm 核心技能，没它要重造轮子 |
| 长任务状态管理 | ⭕ 推荐 | 长任务状态管理参考 |
| hybrid retrieval | ⏳ 阶段 3 | hybrid recall 晚点再说 |
| 远程 sandbox | ⏳ 阶段 4 | Cloud tier 时再用 |
| intent classification | ⏳ 阶段 2 | Spinal Cord 需要时再装 |

---

## 不推荐 fork 的项目（避坑清单）

| 项目 | 不推荐理由 |
|---|---|
| AutoGPT / AgentGPT | 工程质量差，只能当教学材料看 |
| CrewAI | 抽象层太厚，拆出来反而累 |
| AutoGen | 好是好，但要整个生态跟着用，侵入性强 |
| LangChain Core | 过度抽象 + 版本破坏性更新频繁，不适合当地基 |
| LlamaIndex | 同上，且 agent 侧不是强项 |
| Agentscope | 国内项目，功能 OK 但英文社区小，长期维护风险 |

---

## 阶段 0/1 落地建议

**第 1 周**：P0 模块就位 + 跑通烟雾测试
**第 2 周**：包名统一 + 解耦 FastAPI 依赖 + CI 绿
**第 3 周**：写 Cerebrum/Hemolymph 设计
**第 4 周**：实现 Cerebrum MVP + 单 Ganglion + Hemolymph v1
**第 5 周**：接 OTel + 实现 code_arm（5 个 skill）
**第 6 周**：烟雾测试 "给定目标 → 计划 → 执行 → 断点续跑"

---


---

## 对等参考（架构思路）

本项目不 fork 外部代码。下列参考仅用于内部设计对照：

- **认知架构论文（CoALA）** — Princeton 论文 + 代码骨架
  - https://github.com/ysymyth/awesome-language-agents
- **Minecraft 风格 skill library + curriculum 原型**（Voyager, MIT NVIDIA）
  - https://github.com/MineDojo/Voyager
  - 对"skill 自进化"的最早工程原型之一
- **反思循环的最简参考**（ReAct / Reflexion 原始实现）
  - https://github.com/noahshinn/reflexion
  - 反思循环的最小参考
