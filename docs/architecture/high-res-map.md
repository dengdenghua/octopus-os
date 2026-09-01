# Echo 高清架构图

一张图讲清楚 20 个器官的职责、依赖、数据流向。

配合 [`core-path.md`](./core-path.md)（学习顺序）+ [`organ-tiering.md`](./organ-tiering.md)（维护分类）一起读。

> **浏览器用户**：同内容的 Mermaid 版本在 [`high-res-map.mermaid.md`](./high-res-map.mermaid.md)，
> GitHub / Obsidian / VSCode preview 直接渲染成可交互 SVG。本文件是 ASCII 版，终端/纯文本场景用。

---

## 第一张图 · 一次请求的完整旅程

从 HTTP 进来到回复出去。**粗线是主路径**，细线是辅助治理。

```
                              ┌───────────────────────────────────────┐
                              │  外部世界(IM 平台 / HTTP client / CLI) │
                              └──────────────────┬────────────────────┘
                                                 │
                                                 ▼
    ╔════════════════════════════════════════════════════════════════════╗
    ║                           🪸 Siphon                                 ║  ← 外部边界(HTTP/SSE/WS)
    ║  thread_compat_router · openai_gateway · channels_router · ...     ║
    ╚══════════════════════════╤═════════════════════════════════════════╝
                               │
                               ▼
                      ┌─────────────────┐  ← 快路径反射 · 80% 请求在这断
                      │ 🐚 SpinalCord   │    regex / cache / edge SLM
                      │  ReflexRouter   │    命中 → 直接回 response
                      └────────┬────────┘
                      未命中 → │
                               ▼
    ╔══════════════════════════════════════════════════════════════╗
    ║                       🧠 Cerebrum                             ║  ← LLM 规划中枢
    ║   LLMPlanner / StaticPlanner + learned_rules_section          ║     (一次 LLM 调用 产 TaskGraph)
    ║   - 读 intent.user_context + 注入 LEARNED_MITIGATIONS          ║
    ║   - 产 TaskGraph(DAG) 或 ReAct 循环                            ║
    ╚════════════════════════════╤═════════════════════════════════╝
                                 │
                           读:📘 Genome / 🩸 Hemolymph
                           选:👁 Eyes(LLM 路由)
                                 │
                                 ▼
    ╔══════════════════════════════════════════════════════════════╗
    ║                       🧬 Ganglia                              ║  ← 节点运行时
    ║   GraphRuntime(单 arm) · SwarmRuntime(并行 N arm)             ║     (消费 DAG · 纯执行 · LLM 不介入)
    ╚════════════════════════════╤═════════════════════════════════╝
                                 │
                                 ▼
    ╔══════════════════════════════════════════════════════════════╗
    ║                        🦑 Arms × N                            ║  ← 执行载体
    ║   code_arm · search_arm · browse_arm · file_arm · ...        ║     (8 类型 · 各配不同 model)
    ╚════════════════════════════╤═════════════════════════════════╝
                                 │
                                 ▼
    ╔══════════════════════════════════════════════════════════════╗
    ║                       🔱 Suckers                              ║  ← 技能注册表
    ║   SkillRegistry + 2000+ 候选技能                              ║     (原子能力单元)
    ╚════════════════════════════╤═════════════════════════════════╝
                                 │ 取 skill handler
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│ ▸                      🐦 Beak(核心执行器)                           ◂│
│                                                                       │
│   ① 🛡 Immunity.check(call, signature)  ← 白名单 / 风险评分          │
│   ② 🖋 Ink.reserve(cost)                ← 预算 / 熔断                │
│   ③ 🛡 PreToolUse hook                   ← 社区 hook 可改写 args     │
│   ④ [sandbox_dir 校验]                   ← Mode-gated write scope    │
│   ⑤ 🦠 Mantle.execute(handler, args)     ← 沙箱隔离实际跑            │
│   ⑥ 🖋 Ink.commit(actual_cost)                                       │
│   ⑦ 🛡 PostToolUse hook                  ← 社区 hook 可改输出        │
│   ⑧ 📘 Genome.write_step + write_immune  ← Journal 留痕              │
│   ⑨ 🆕 FileOpEvent / PreviewRefreshEvent ← 副作用广播(见右下)       │
│                                                                       │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                          ┌─────┴──────┐
                          ▼            ▼
                  ToolResult       SSE 事件流
                  返给 Ganglia     → 🪸 Siphon → UI
```

---

## 第二张图 · 治理侧的事件流

Beak 每步都往 Journal 写事件，这些事件反过来喂给 Regeneration 做反思：

```
           ┌─────────────────────────────────────┐
           │         🐦 Beak.execute_step         │
           └─────────────────────┬───────────────┘
                                 │
                                 ▼
                    ╔═══════════════════════╗
                    ║   📘 Genome.Journal    ║  append-only
                    ║   - StepEvent          ║  所有事件一条条写
                    ║   - TrajectoryEvent    ║  JSONL / InMem
                    ║   - ImmuneEvent        ║
                    ║   - BudgetEvent        ║
                    ║   - FileOpEvent        ║
                    ║   - PreviewRefresh     ║
                    ║   - ReflexHitEvent     ║
                    ╚══════════╤════════════╝
                               │
                               ▼
                   ┌───────────────────────┐
                   │  ⟳ Regeneration       │  ← umbrella · 6 个 producer
                   └──┬────┬───┬───┬──┬──┬─┘
                      │    │   │   │  │  │
                      ▼    ▼   ▼   ▼  ▼  ▼
              RuleExtractor    │   │  │  └─ RecipeEvaluator(Fitness 评分)
              ↓ 失败→规则      │   │  └─── WorkflowRewriter(改 rules DSL)
              → Cerebrum prompt│   └────── KGUpdater(triples)
                              │   └────── MemoryConsolidator(模式摘要)
                              └────────── SkillForge(重复子序列 → 新 skill)
                                            │
                                            ▼
                                  🎭 Camouflage(A/B 淘汰)
                                    ABSplitter + auto_retire
                                    (事前分流 · 和 Regeneration 是
                                     同一条 explore/exploit 回路)
```

## 第三张图 · 事件总线侧（内部通讯）

Nerves 是内部 pub/sub；Chromatophores 是 arm-to-arm 特化广播；Skin 是环境感知源：

```
     ┌──────────────────────────────────────────────────────────┐
     │                   ⚡ Nerves.TypedEventBus                 │ ← 统一事件总线
     │                                                          │
     │ .publish(SkillRegistered)  .publish(VariantRetired)  ... │
     │         ▲▲▲                      ▲                       │
     └─────────┼┼┼──────────────────────┼───────────────────────┘
              Bridge                 Dispatch
               │││                     │
               │││                     ▼
               │││              ┌──────────────┐
               │││              │ HookManager  │ ← 社区 hook(pre/post tool)
               │││              └──────────────┘
               ││└── 📱 Skin.sensors        (EnvSensor · file / git / process)
               │└─── 🎨 Chromatophores      (arm-to-arm broadcast + Boids)
               └──── 🔱 Suckers.register    (SkillRegistered 事件)
```

Skin 的新 import 路径：`runtime.core.nerves.sensors`（工程心智）
老路径 `runtime.sensing.normalize` 继续可用（向后兼容 shim）

---

## 第四张图 · Hearts 的"3 心脏"真相

别被名字误导，Hearts 实际上就是：

```
┌──────────────────────────────────────────────────┐
│              ❤️ Hearts(facade)                    │
│                                                   │
│  ┌──────────────────┐    ┌──────────────────┐    │
│  │ BackgroundRunner │    │ CircuitBreaker×N │    │
│  │ (内部业务循环)    │    │ (外部 I/O 熔断)   │    │
│  │   = Scheduler    │    │   = 🖋 Ink 聚合   │    │
│  └──────────────────┘    └──────────────────┘    │
│                                                   │
│  ┌────────────────────────────────────────────┐  │
│  │ Coordinator(Redis / etcd / InMemory)        │  │
│  │   跨进程 leader 选举 · HA lease             │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘

         🚫 不是字面"3 颗心脏"
         ✅ 就是一个 Scheduler + CircuitBreakerGroup + HA Coordinator
```

---

## 第五张图 · 前端入口到后端的全链路

完整请求生命周期（合并前面所有图的主线）：

```
[React app]
    │  POST /api/threads/{id}/runs/stream (context.mode = "chat" | "react" | "deep")
    ▼
🪸 Siphon
    │  ParsedIntent + user_context (profile_memories 等)
    ▼
🐚 SpinalCord ─── 命中 ──► 直接返回 (0 LLM)
    │ 未命中
    ▼
[mode 分发]
    ├── "chat"(默认) ──► 🧠 Cerebrum ──► DAG ──► 🧬 Ganglia ──► 🦑 Arms ──► 🐦 Beak ... ──► Final 回复
    │                                                                      ↓
    │                                                                   每 step 写 📘 Journal
    │                                                                      ↓
    │                                                                   触发 ⟳ Regeneration
    │                                                                      ↓
    │                                                                   更新 Cerebrum.learned_rules
    │
    ├── "react"   ──► stream_react_loop(每步 LLM + 🐦 Beak + 🎭 Camouflage variant)
    │                   ↓
    │                流式 yield tool_start / tool_end (SSE custom 事件)
    │                   ↓
    │                _persist_react_trajectory → 📘 Journal + ⟳ Regeneration
    │
    └── "deep"    ──► 🧠 Cerebrum(deliberative + Opus) ──► 多 🦑 Arm 协作 ──► Final
```

---

## 数据持久化矩阵

所有状态如何落盘：

| 数据 | 存到哪 | 器官负责 |
|---|---|---|
| 所有事件 | `data/journal.jsonl` | 📘 Genome.JSONLJournal |
| 线程 / 消息 | `data/threads.jsonl` | ThreadStateStore |
| 学到的规则文本 | `data/learned_rules.yaml` | 🧠 Cerebrum.auto_persist_rules |
| 规则结构化 DSL | `data/workflow_rules.yaml` | 🧠 StaticPlanner.rules |
| 渠道绑定 + 配对 | `data/channel_state.json` | 🪸 Siphon.channels_router |
| 渠道凭证 | `data/channel_state.credentials.json` (AES-GCM 加密) | 同上 |
| AES-GCM 密钥 | `data/.credential_key` (chmod 600) | 同上 |
| Camouflage variants | `data/variants.yaml` | 🎭 Camouflage.dump_variants_to_yaml |

---

## 学习顺序建议

1. 看 [`core-path.md`](./core-path.md)（**学习优先级 5+3+5+6**）
2. 看本文件第一张图（**一次请求的完整旅程**）
3. 读 5 个主路径模块（`runtime/core/cerebrum/`, `.../ganglia/`, `runtime/execution/{arms,suckers,beak}/`）
4. 读 3 个治理模块（`runtime/safety/{immunity,ink}/`, `runtime/memory/genome/`）
5. 需要时再看本文件其他图

第一天读完 1-3，第二天读 4，一周内补齐 5。**不要试图一天啃完 20 个器官。**
