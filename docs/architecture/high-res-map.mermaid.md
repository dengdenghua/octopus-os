# Echo 架构图（Mermaid 版）

`high-res-map.md` 的 ASCII 图对应的 Mermaid 版。GitHub / GitLab / 大多数 Markdown 渲染器原生支持，能直接渲染成可交互的 SVG。

终端阅读者用 [ASCII 版](./high-res-map.md)；浏览器阅读者读这份。

---

## 图 1 · 一次请求的完整旅程

```mermaid
flowchart TB
    ext([外部世界<br/>IM / HTTP / CLI])
    siphon[🪸 <b>Siphon</b><br/>thread_compat_router<br/>openai_gateway<br/>channels_router]
    spine{🐚 <b>SpinalCord</b><br/>regex/cache/edge SLM}
    spineHit([直接回 response<br/>0 LLM])
    cerebrum[🧠 <b>Cerebrum</b><br/>LLMPlanner<br/>一次 LLM → TaskGraph<br/>注入 LEARNED_MITIGATIONS]
    ganglia[🧬 <b>Ganglia</b><br/>GraphRuntime / SwarmRuntime<br/>消费 DAG · LLM 不介入]
    arms[🦑 <b>Arms × N</b><br/>code · search · browse · file<br/>各配不同 model]
    suckers[🔱 <b>Suckers</b><br/>SkillRegistry]
    beak[[🐦 <b>Beak 执行器</b><br/>① Immunity.check<br/>② Ink.reserve<br/>③ PreToolUse hook<br/>④ Mantle.execute<br/>⑤ Ink.commit<br/>⑥ PostToolUse hook<br/>⑦ Genome.write_step]]
    result([ToolResult])
    sse([SSE 帧 → Siphon → UI])

    ext --> siphon
    siphon --> spine
    spine -- 命中 --> spineHit
    spine -- 未命中 --> cerebrum
    cerebrum -. 读 .-> genome[📘 Genome]
    cerebrum -. 读 .-> hemolymph[🩸 Hemolymph]
    cerebrum -. 选 .-> eyes[👁 Eyes]
    cerebrum --> ganglia
    ganglia --> arms
    arms --> suckers
    suckers --> beak
    beak --> result --> ganglia
    beak --> sse

    classDef primary fill:#4a154b,stroke:#333,color:#fff
    classDef governance fill:#d97706,stroke:#333,color:#fff
    classDef edge fill:#0ea5e9,stroke:#333,color:#fff
    class cerebrum,ganglia,arms,suckers,beak primary
    class genome,hemolymph,eyes governance
    class siphon,spine edge
```

---

## 图 2 · 治理侧事件流（自进化闭环）

```mermaid
flowchart TB
    beak[🐦 Beak.execute_step]
    journal[(📘 <b>Genome.Journal</b><br/>append-only<br/>Step / Trajectory<br/>Immune / Budget<br/>FileOp / PreviewRefresh<br/>ReflexHit)]
    reg[⟳ <b>Regeneration umbrella</b>]

    ruleExtr[RuleExtractor<br/>失败→规避规则]
    skillForge[SkillForge<br/>重复子序列→新技能]
    memCons[MemoryConsolidator<br/>模式摘要]
    kgUp[KGUpdater<br/>事件→三元组]
    wfRewrite[WorkflowRewriter<br/>改 rules DSL]
    recipeEval[RecipeEvaluator<br/>Fitness 评分]

    camo[🎭 <b>Camouflage</b><br/>ABSplitter + auto_retire<br/>事前分流流量]

    cerebrumPrompt[🧠 Cerebrum.learned_rules_section<br/>下次 plan prompt 自动带]

    beak --> journal
    journal --> reg
    reg --> ruleExtr --> cerebrumPrompt
    reg --> skillForge
    reg --> memCons --> cerebrumPrompt
    reg --> kgUp
    reg --> wfRewrite --> cerebrumPrompt
    reg --> recipeEval --> camo
    camo -. explore/exploit 闭环 .-> reg

    classDef sink fill:#059669,stroke:#333,color:#fff
    class cerebrumPrompt sink
```

---

## 图 3 · 内部事件总线

```mermaid
flowchart LR
    subgraph bus[⚡ Nerves.TypedEventBus]
        direction LR
        pub[publish/subscribe<br/>typed events]
    end

    skin[📱 Skin.sensors<br/>FileWatcher<br/>GitHook<br/>ProcessWatch]
    chromo[🎨 Chromatophores<br/>arm-to-arm broadcast<br/>+ Boids rules]
    suckersReg[🔱 Suckers.register<br/>SkillRegistered 事件]
    hooks[HookManager<br/>pre/post tool hooks]

    skin -- FileChanged<br/>GitCommitDetected --> bus
    chromo -- VariantRetired<br/>SignalBroadcast --> bus
    suckersReg -- SkillRegistered --> bus
    bus -- dispatch --> hooks

    note1{{Skin 新 import 路径:<br/>runtime.core.nerves.sensors<br/>老路径继续可用}}
    skin -.-> note1

    classDef emitter fill:#f59e0b,stroke:#333,color:#000
    classDef core fill:#4a154b,stroke:#333,color:#fff
    class skin,chromo,suckersReg emitter
    class bus core
```

---

## 图 4 · Hearts 去诗化真相

```mermaid
flowchart TB
    subgraph hearts[❤️ Hearts 聚合 facade]
        direction TB
        bg[BackgroundRunner<br/>内部业务循环<br/><b>= Scheduler</b>]
        cb[CircuitBreaker × N<br/>外部 I/O 熔断<br/><b>= Ink 聚合</b>]
        coord[Coordinator<br/>Redis / etcd / InMemory<br/><b>= HA leader 选举</b>]
    end

    truth{{🚫 不是字面 3 心脏<br/>✅ 就是 Scheduler + CircuitBreakerGroup + Coordinator}}
    hearts -.-> truth

    style hearts fill:#fecaca,stroke:#991b1b
    style truth fill:#fef3c7,stroke:#92400e
```

---

## 图 5 · 前端入口到后端全链路

```mermaid
flowchart TB
    ui[React app<br/>POST /api/threads/.../runs/stream]
    siphon[🪸 Siphon<br/>ParsedIntent + user_context]
    spine{🐚 SpinalCord}
    spineHit([直接回 · 0 LLM])

    modeDispatch{context.mode}

    chat[💬 chat<br/>默认]
    react[🧠 react<br/>ReAct 循环]
    deep[🔭 deep<br/>Opus + 多 arm]

    cerebrumChat[🧠 Cerebrum.plan<br/>→ TaskGraph]
    ganglia[🧬 Ganglia]
    arms[🦑 Arms]
    beak[🐦 Beak]
    finalOut([Final 回复])

    reactLoop[stream_react_loop<br/>每步 LLM + Beak + Camouflage variant]
    reactStream([SSE tool_start/end<br/>流式发])
    persist[_persist_react_trajectory]

    cerebrumDeep[🧠 Cerebrum + Opus<br/>deliberative=true]

    journal[📘 Journal]
    regen[⟳ Regeneration]
    cerebrumRules[🧠 Cerebrum.learned_rules<br/>下次 prompt 自动带]

    ui --> siphon --> spine
    spine -- 命中 --> spineHit
    spine -- 未命中 --> modeDispatch
    modeDispatch --> chat
    modeDispatch --> react
    modeDispatch --> deep

    chat --> cerebrumChat --> ganglia --> arms --> beak --> finalOut
    beak --> journal

    react --> reactLoop --> reactStream
    reactLoop --> persist --> journal

    deep --> cerebrumDeep --> ganglia

    journal --> regen --> cerebrumRules
    cerebrumRules -. 闭环 .-> cerebrumChat

    classDef mode fill:#6366f1,stroke:#333,color:#fff
    classDef out fill:#059669,stroke:#333,color:#fff
    class chat,react,deep mode
    class finalOut,cerebrumRules out
```

---

## 图 6 · 数据持久化矩阵（补充）

```mermaid
flowchart LR
    subgraph dataDir["./data/"]
        direction TB
        journal[(journal.jsonl<br/>所有事件)]
        threads[(threads.jsonl<br/>线程/消息)]
        rulesYaml[(learned_rules.yaml<br/>文本规则)]
        rulesDsl[(workflow_rules.yaml<br/>结构化 DSL)]
        chState[(channel_state.json<br/>绑定+配对)]
        chCreds[(channel_state.credentials.json<br/>🔒 AES-GCM 加密)]
        key[(.credential_key<br/>chmod 600)]
        variants[(variants.yaml<br/>Camouflage 变体池)]
    end

    genome[📘 Genome] --> journal
    threadStore[ThreadStateStore] --> threads
    cerebrum[🧠 Cerebrum] --> rulesYaml
    staticPlanner[StaticPlanner] --> rulesDsl
    channelsRouter[🪸 channels_router] --> chState
    channelsRouter --> chCreds
    chCreds -. 用 .-> key
    camouflage[🎭 Camouflage] --> variants
```

---

## 如何用这份文件

**浏览器**：GitHub / GitLab / Obsidian / VSCode Preview 都会自动渲染 `mermaid` 代码块为 SVG。
点击某个节点未来可加跳转（Mermaid 支持 `click` 指令，若想加点击跳源码行号）。

**PPT / 演示**：导出每个 Mermaid 为 PNG 再贴；或用 `mermaid-cli` 批量：
```bash
mmdc -i high-res-map.mermaid.md -o diagrams/ --outputFormat svg
```

**迭代**：想加新器官或改连线？**只改这份文件**，ASCII 版（`high-res-map.md`）作为降级不必每次同步；用户用哪份就用哪份。

---

## 对齐说明

本文件的 6 张图 **严格对齐** [`high-res-map.md`](./high-res-map.md) 的 ASCII 版（标题、节点、连线一致）；额外新增第 6 张数据持久化矩阵（ASCII 版是纯表格，Mermaid 版画成图更直观）。

若发现两版不一致 → 以 ASCII 版为准（是主 source），修 Mermaid 版来追齐。
