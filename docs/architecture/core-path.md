# Echo 核心路径地图

新人阅读顺序：这 5 个走主路径，再看 3 个治理，其余按需。

---

## 第一层 · 主路径（5 个，必看）

一条请求从 HTTP 到回复的最短链路：

```
HTTP request
    │
    ▼
┌─────────┐     一次 LLM 调用
│Cerebrum │ ─── 规划成 TaskGraph (DAG) ─── runtime/core/cerebrum/
└────┬────┘
     ▼
┌─────────┐     消费 DAG，逐节点调度
│Ganglia  │ ─── GraphRuntime / SwarmRuntime ─ runtime/core/ganglia/
└────┬────┘
     ▼
┌─────────┐     执行载体，8 类 worker
│Arms     │ ─── 每 arm 可配不同 model ─── runtime/execution/arms/
└────┬────┘
     ▼
┌─────────┐     技能原子（"能做什么"的最小单元）
│Suckers  │ ─── SkillRegistry + builtins ─ runtime/execution/suckers/
└────┬────┘
     ▼
┌─────────┐     真正调工具 + 全程审计
│Beak     │ ─── ToolExecutor ─────────── runtime/execution/beak/
└─────────┘
```

**读这 5 个**，就理解了 Echo 怎么把"用户一句话"变成"tool 调用 + 返回"。

---

## 第二层 · 治理三件套（3 个，必看）

Beak 每次 `execute_step` 都会经过这 3 个关卡：

| 器官 | 位置 | 管什么 |
|---|---|---|
| **Immunity** | `runtime/safety/immunity/` | 谁能调 / 谁被拒 · AntigenSignature + TrustEngine |
| **Ink** | `runtime/safety/ink/` | 预算/熔断 · CircuitBreaker + Budget |
| **Genome** | `runtime/memory/genome/` | Journal 持久化 · 所有事件 append-only |

---

## 第三层 · 能力增强（5 个，按需）

你不一定立刻需要，但某些场景必读：

| 器官 | 何时读 |
|---|---|
| **SpinalCord** | 想优化首字节延迟 · 80% 请求不走 LLM 的短路 |
| **Regeneration** | 想理解自演化闭环 · 含 6 个反思生产者 |
| **Camouflage** | 想跑 A/B 策略对照 · 实现上挂在 Regeneration 下 |
| **Eyes** | 新增 LLM 供应商 · Anthropic / OpenAI / Gemini |
| **Mantle** | 加沙箱策略 · Local / Subprocess / Docker / SSH / K8s |

---

## 第四层 · 基础设施（6 个，几乎不用改）

写插件基本碰不到这层：

| 器官 | 实现本质 |
|---|---|
| **Hearts** | `Scheduler + CircuitBreakerGroup + Coordinator` 的 facade（不是字面"3 心脏"） |
| **Nerves** | TypedEventBus + HookManager · 内部事件总线 |
| **Chromatophores** | Arm-to-arm 广播 + Boids 群仲裁 · 和 Nerves bus 分工 |
| **Skin** | 环境 sensor 层 · 挂在 Nerves 上（新路径 `nerves.sensors`） |
| **Siphon** | HTTP/SSE/WS **外部**边界 · 不要和 Nerves 内部总线混淆 |
| **Hemolymph** | 上下文配额管理 · system 15 / 技能 10 / 记忆 30 / 历史 45 |
| **Constitution** | Soul 注入 + 伦理门 |

---

## 命名诚实声明

部分器官名带仿生浪漫色彩，但代码里是工程实现。以下是本质翻译：

| 诗化名 | 工程本质 |
|---|---|
| Hearts | HA coordinator + bulkhead 熔断聚合 |
| Chromatophores | arm-to-arm pub/sub 带 Boids 协调规则 |
| Regeneration | 6 个反思生产者（RuleExtractor / SkillForge / ...）的 umbrella |
| Camouflage | A/B 策略分流 + 自动淘汰 |

读代码时遇到仿生名卡住，直接查本表，别琢磨生物学。

---

## 概念重分组（shim 层）

为降低认知负担，以下两个器官有**工程心智**下的新 import 路径（老路径继续可用）：

| 老路径（物理位置） | 新路径（概念归属） |
|---|---|
| `runtime.sensing.normalize.*` | `runtime.core.nerves.sensors.*` |
| `runtime.safety.experiments.*` | `runtime.safety.recovery.camouflage.*` |

新代码优先用新路径 · 老代码保持可用（shim 100% 向后兼容）。

---

## 一句话总结

> **仿生是设计灵感，不是架构枷锁。** 主路径 Cerebrum → Ganglia → Arms → Suckers → Beak，治理 Immunity + Ink + Genome，其余按需读。20 个器官，8 个是核心。
