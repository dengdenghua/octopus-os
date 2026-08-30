# 章鱼器官 · 生物学类比说明

> 这里**不是代码** · 只是每个"章鱼器官"命名的概念文档。
>
> 真实 Python 实现都在 [`runtime/`](../../runtime/) 下。
> 器官名和代码路径的映射见下方总表。

---

## 器官 → 代码映射总表

| 器官 | 代码路径 | 实际内容 | 状态 |
|---|---|---|---|
| Cerebrum | `runtime/core/cerebrum/` | ReAct loop + Planner + 安全检测 (30+ 文件) | **已实装** |
| Spinal Cord | `runtime/core/nerves/reflex/` | 规则引擎 + 自动回复 + git track | **已实装** |
| Ganglia | — | 无独立模块 | **未实装** |
| Arms | `runtime/execution/arms/` | Worker 池 + 技能路由（无自治逻辑）| **部分** |
| Tentacle | `runtime/tentacle/` | 移动端/跨设备连接器 | **已实装** |
| Suckers | `runtime/execution/suckers/` | 技能加载器 (60+ 技能) | **已实装** |
| Beak | `runtime/execution/tool_engine/executor.py` | 工具执行引擎 | **已实装** |
| Mantle | `runtime/safety/sandboxing/` | 沙箱 (local + Docker) | **已实装** |
| Siphon | `runtime/protocol/` + `runtime/platform/ui/` | JSON-RPC WebSocket + SSE | **已实装** |
| Eyes | `runtime/sensing/model_router/` | LLM 路由 + 设备管理 | **已实装** |
| Skin | — | 无独立模块 | **未实装** |
| Nerves | `runtime/core/nerves/` | 进程内事件总线 + hooks（无跨进程总线）| **已实装** |
| Chromatophores | `runtime/safety/chromatophores/` | Signal bus + Boids 仲裁 | **已实装** |
| Ink Sac | `runtime/safety/budget_breaker/` | 三态熔断器 | **已实装** |
| Immunity | `runtime/safety/auth/` | 信任引擎 + 攻击记忆 + 自适应免疫（单文件）| **部分** |
| Hearts | `runtime/core/hearts/` | 进程协调 + 分布式锁（无三心 HA）| **部分** |
| Genome | `runtime/safety/recovery/genome_registry.py` | 版本注册表（单文件，无进化回路）| **部分** |
| Hemolymph | `runtime/memory/hemolymph/composer.py` | 上下文组装器（单文件）| **已实装** |
| Camouflage | `runtime/safety/experiments/` | A/B 调度 + 提示词变体 | **已实装** |
| Regeneration | `runtime/safety/recovery/` + `runtime/memory/learning/` | 技能锻造 + 规则提取 + 反思闭环 | **已实装** |

---

## 有详细文档的器官

以下器官有独立的概念说明文档，点击可查看：

### 慢路径（Deliberative）
- [`cerebrum/`](cerebrum/) — 中枢脑 · 规划 / 分解 / 反思 *(占位符，详见 architecture/organs/)*

### 快路径（Reactive）
- [`nerves/`](nerves/) — 神经 · 消息总线 / 工作流图
- [`spinal_cord/`](spinal_cord/) — 脊髓 · 反射动作 · 不经大脑 *(占位符)*

### 执行单元
- [`arms/`](arms/) — 腕足 · 半自主 worker agent (×8)
- [`tentacle/`](tentacle/) — 触腕 · 移动 / 跨设备执行触点
- [`suckers/`](suckers/) — 吸盘 · 技能库
- [`beak/`](beak/) — 喙 · 工具执行引擎 *(占位符)*

### 感知
- [`eyes/`](eyes/) — 眼 · 模型适配 / 视觉输入
- [`skin/`](skin/) — 皮肤 · 环境感知 *(占位符)*

### 运输与边界
- [`siphon/`](siphon/) — 漏斗 · 流式 I/O *(占位符)*
- [`mantle/`](mantle/) — 外套膜 · 沙箱 (local/docker/ssh/k8s)
- [`hemolymph/`](hemolymph/) — 血淋巴 · 上下文流 + Blackboard *(占位符)*

### 记忆与进化
- [`genome/`](genome/) — 基因组 · 长时记忆 / 检查点
- [`regeneration/`](regeneration/) — 再生 · 反思 / 技能锻造 *(占位符)*
- [`camouflage/`](camouflage/) — 拟态 · 策略 A/B *(占位符)*

### 自我保护
- [`immunity/`](immunity/) — 免疫 · 身份 / 适应性风控
- [`ink/`](ink/) — 墨囊 · 熔断 / 预算上限 *(占位符)*
- [`hearts/`](hearts/) — 心脏 · HA 调度 (×3) *(占位符)*

### 广播
- [`chromatophores/`](chromatophores/) — 色素细胞 · 腕间状态广播 *(占位符)*

---

## 阅读指引

| 你想了解 | 去哪里 |
|---|---|
| 器官的仿生学概念和设计思路 | 本目录下有详细文档的子目录（arms, eyes, genome, immunity, mantle, nerves, suckers, tentacle） |
| 真实代码实现 | `runtime/` 下对应路径（见上方总表） |
| 工程架构（纯工程语言，无仿生术语）| [guide/architecture.md](../guide/architecture.md) |
| 完整仿生愿景（含未实装设计）| [vision/biomimetic-architecture.md](../vision/biomimetic-architecture.md) |
| 每个机制的实装状态 | [implementation-status.md](../implementation-status.md) |

标注 *(占位符)* 的目录只有一行重定向，无实质内容。对应器官的设计思路见 [vision/biomimetic-architecture.md](../vision/biomimetic-architecture.md)。
