# Agent Capabilities

> **🔢 实测数字** · [docs/benchmarks.md](benchmarks.md) — 11 个真跑 case
> 的硬数字(成本 / 时间 / 轮数),不是纸面声明。

> What this doc covers
>
> Echo is its own runtime — a self-researched biomimetic agent stack.
> The architecture is a bionic nervous system:
> This page maps each capability to the file that implements it and
> the smoke that verifies it. Audience: engineers integrating Echo
> or auditing what's actually wired up vs. just claimed. Not a
> marketing pitch.

## TL;DR · 一套自研 runtime,三类能力

| 路线 | Echo 现状 | 关键文件 | 烟测 |
|------|--------------|---------|------|
| **长 horizon 单 agent**(多步连续)· 灵感来自 GLM 风格深度路线 | ReAct 循环默认 30 轮(research/swarm 100 · goal 10000)· 第 10 / 20 轮自动反思 | `runtime/core/cerebrum/react_loop.py` (`max_iterations` 默认 30 · per-mode 提升)· `runtime/sensing/siphon/tool_bridge.py` (`MAX_TOOL_ROUNDS=30`, `REFLECTION_INTERVAL=10`) | `tmp/smoke_long_no_mcp.py` · 实测 19 calls / 20 rounds / 7 步串行依赖 |
| **并发 sub-agent + 共享黑板** · 灵感来自 Kimi 风格 swarm 路线 | 8 并发 spawn + turn-scoped blackboard + 嵌套时间轴 | `runtime/memory/blackboard.py`, `runtime/execution/suckers/delegation_skills.py:_call_agent_parallel` | `tmp/smoke_swarm.py` · 实测 3 并发 architect + bb_write/read 闭环 |
| **自演化(改 SOUL + revert + LLM-judge)** · 灵感来自 MiniMax 风格自演化路线 | 4 层:`update_soul` / `revert_soul` / 启发式打分 / LLM 评审 / autonomous loop | `runtime/execution/suckers/memory_skills.py`, `runtime/memory/{turn_scoring,deep_evolution}.py` | `tmp/smoke_self_evolve.py` + `tmp/smoke_b2_b3.py` |
| **Skills 系统**(文档→可复用模板) | `learn_skill_from_text` 抽 DNA + `apply_skill` 复用 | `runtime/memory/skill_library.py` | `tmp/smoke_skill_library.py` |

---

## 完整能力矩阵

### A · 基础执行
| 能力 | 说明 | 实现 |
|------|------|------|
| 多 provider 路由 | Anthropic / OpenAI / Gemini 都跑得通,自定义模型 UI 注册 | `runtime/sensing/eyes/dispatch_router.py` + `runtime/sensing/siphon/config_router.py` |
| 原生 `tool_use` | Claude/OpenAI/Gemini 各自原生 function-calling 协议 | `runtime/sensing/siphon/tool_bridge.py` |
| 流式 SSE + cancel | 170ms cancel 延迟实测 | `runtime/sensing/siphon/thread_compat_router.py` + `runtime/adapters/runs_registry.py` |
| 部分恢复 | 中断后下次回话能看到上一轮的 partial output | thread state eager-flush + `run_status` tag |
| 内置 skills 集合(atomic + web + git + shell + MCP fs + computer + browser · 14 个 MCP filesystem · 持久 client) · 实际数量见 `len(runtime.execution.all_skills.ALL_SKILL_IDS)` | 见 `runtime/execution/all_skills/__init__.py` 注册的 ID 列表 | `runtime/execution/all_skills/__init__.py` |

### B · 长 horizon
| 能力 | 说明 | 关键常量 |
|------|------|---------|
| ReAct 循环默认 30 轮 · 多模式动态提升 | `react_loop.py` 默认 `max_iterations=30` · research/swarm 模式自动提到 100 · goal 模式 10000 · `tool_bridge.py` 限定 `MAX_TOOL_ROUNDS=30` | `react_loop.py:263 max_iterations=30` · `react_loop.py:368-397`(per-mode 提升)· `tool_bridge.py:MAX_TOOL_ROUNDS=30` |
| 第 10 / 20 轮自动反思注入 | system 消息提醒 agent "还在推进 / 已经完成 / 同 tool 反复失败该换方法" | `REFLECTION_INTERVAL = 10` |
| Per-sub-agent 5 轮 + 10K token 上限 | 防止 sub-agent 偷预算 | `EPHEMERAL_MAX_ROUNDS=5`, `EPHEMERAL_TOKEN_BUDGET=10_000` |

### C · 并发 sub-agent
| 能力 | 实现 | 烟测 |
|------|------|------|
| 并发 sub-agent spawn | `call_agent_parallel(specs=[{agent_id, prompt}])` · ThreadPool 8 | `tmp/smoke_swarm.py` |
| Turn-scoped blackboard | `bb_write(k,v)` / `bb_read(k)` / `bb_keys()` 三个 atomic skill,key 是 `Session.turn_id`,LRU-bounded 256 个 board × TTL 1h | `tmp/smoke_swarm_bb.py` 验证 sibling 互通 |
| Sub-agent 工具能力 | ephemeral runner 跑 5 轮 mini agentic loop,自动注入 `bb_*` 让 sibling 可写黑板 | `runtime/execution/suckers/ephemeral_runner.py` |
| Sub-tool nested 时间轴 | parent_tool_use_id 关联,frontend `LiveToolTimeline.ParentWithChildren` 缩进渲染 | `frontend/src/components/workspace/live-tool-timeline.tsx` |
| Builtin role 注册 · 当前 11 个(reviewer / researcher / debugger / architect / security-review / explorer / arbiter / planner / synthesizer / implementer / designer)· 以 `BUILTIN_ROLES` 为权威 | `runtime/execution/suckers/ephemeral_agents.py:BUILTIN_ROLES`(实际数量以 `len(BUILTIN_ROLES)` 为准) |
| User-defined `.claude/agents/*.md` | 加载用户自定义 sub-agent 定义 | `runtime/execution/subagents/registry.py` |

### C.5 · `planning_mode` 标志(同一 ReAct 循环内,非独立路径)

`planning_mode` 是 `react_loop.py` 的一个**布尔参数**,不是另一条执行路径。
当 `planning_mode=True`:同一 ReAct 循环跑,但工具实际执行被禁用,
LLM 只产出 plan;用户审过后再以 `planning_mode=False` 重新触发实际执行。
单独的 `bench_plan_vs_react.py` 对比的是这个 flag 不同取值下的同一循环行为,
而不是两套独立架构。

| 触发方式 | 行为 |
|---------|------|
| 默认(`planning_mode=False`) | ReAct 循环 + 工具真执行 |
| `planning_mode=True` | 同一循环 · 工具不执行 · 让 agent 先写 plan · 由用户/上层决定要不要切回 false 继续 |

历史上的 deep mode / `bugfix-demo` / `reflection-demo` / `evolution-demo` 走的是
`StaticPlanner`/`LLMPlanner` + `GraphRuntime`(DAG 拓扑层并行 + 重规划),这是
**另一套独立 runtime**(`runtime/core/ganglia/`),不在 ReAct 循环里。它和
ReAct 循环都活着,各自适合不同任务,不是同一引擎的两个模式。

**实测数字**(`benchmarks/bench_plan_vs_react.py` · 跑出来的是 `planning_mode` 开关 / DAG runtime 的对比 · 数字本身参考意义有限,口径以 `benchmarks/results/` 实跑文件为准):
- 同任务在结构可预测时,DAG / planning_mode 通常更快;在需要观察中间结果时, ReAct 答案更厚。具体数字**不要离开 benchmark 文件单独引用**。

### D · 自我演化 · 5 层
| 层 | Skill | 成本 | 触发 |
|----|-------|------|------|
| **写** | `update_soul(lesson, tag)` | 0(local file write) | agent 学到关于自己工作方式的教训时 |
| **回退** | `revert_soul(steps_back, reason)` | 0 | 发现教训反而有害 |
| **历史** | `list_soul_history(limit)` | 0 | 决定 revert 之前看历史 |
| **B1** | `recall_scores` + `analyze_soul_impact` | **0 token** | 启发式 turn 打分 + SOUL 改动前后对比 |
| **B2** | `deep_reflect(window)` | ~2-3¢ haiku | 启发式 inconclusive 时让 LLM 真评 |
| **B3** | `deep_evolve(max_rounds, dry_run)` | ~10-30¢ | 用户明确要求"深度演化"才用,默认 dry_run=True · **实测 `dry_run=False` 真落盘**(`benchmarks/test_a1_evolve_apply.py`) |
| **闭环** | `auto_regression_check(dry_run=False)` | 0 | 自动:每 5 turn 一跑 · 新 SOUL 5+ turn 掉分 ≥0.2 就自动 revert(`benchmarks/test_a2_auto_rollback.py`) |

**关键安全保证**:
- `update_soul` 自动 snapshot 到 `.soul_history/<ms>.md`(防自残)
- `revert_soul` 撤销前先 snapshot 当前(撤销可撤销)
- `deep_evolve(dry_run=True)` 默认只返回提案,不改 SOUL · 用户审过才能 `dry_run=False`
- **`auto_regression_check` 需要 ≥5 post-change samples** · 一个 bad turn 不会触发回滚,避免 panic revert
- **post-turn 自动 tick**(`tool_bridge._auto_evolve_tick_safe` · 每 5 turn) · fail-closed,不会阻塞 user reply

**闭环演示**:
```
turn 1 · deep_evolve(dry_run=False) → SOUL 加新 lesson
turn 2-5 · 正常干活 (scored)
turn 6-10 · 还正常干活 (scored)
turn 15 (= 3×5) · auto_evolve_tick fires:
    - analyze_soul_impact: "regressed · delta=-0.88"
    - auto_regression_check: "reverted" · SOUL 自动回滚
    - 日志: "auto-evolve tick · agent=coder reverted SOUL"
```

**热重载**:`tool_bridge.py` 每个 turn 重读 `agents/<id>/agent-core/SOUL.md`,改完不需要重启 backend,下个 turn 立即生效。

### E · Skills 系统(文档→模板 + 质量门)
| 能力 | 说明 |
|------|------|
| `learn_skill_from_text(name, sample_text, sample_source, golden_samples?)` | LLM 从样例提取结构 + 风格 DNA,落地 `agents/<id>/skills/<name>.md`(frontmatter + template 含 placeholder + style notes)· **C1 golden gate**:传 `golden_samples=[A,B,C]` 三个替代请求 → 框架先跑 apply 测模板真能保留 ≥50% H2 headers · 通过率 < 0.66 → fail-closed 不 persist |
| `list_learned_skills()` | 列已学技能,带 `description` / `when_to_use` / `learned_at` |
| `apply_skill(name, user_request)` | LLM 用模板生成新内容 · system prompt 里 TRIGGER 触发词 → `list_learned_skills` 必调(0 cost) |

**Golden gate 测试**:`tests/test_skill_library_golden_gate.py` · 4/4 绿 · 覆盖 all-good / all-bad / no-gate(向后兼容)/ partial-threshold 四种场景。

### F · 持久记忆(全部 atomic · 0 token)
| Skill | 写到 | 用途 |
|-------|------|------|
| `remember(fact, tags)` | `MEMORY.md` | 关于**世界 / 项目**的事实 |
| `recall(query, limit)` | (读) | 找历史笔记 |
| `note_user(trait)` | `USER.md` | 关于**用户**的偏好 |
| `update_soul(lesson, tag)` | `SOUL.md` `## Lessons Learned` | 关于**自己**的教训 |
| `diary_write(entry)` | `diary/YYYY-MM-DD.md` | 叙事性日志 |

### G · UI 同步
| 能力 | 实现 |
|------|------|
| TodoPanel 实时勾选 | `frontend/src/components/workspace/todo-panel.tsx` 读 `todo_write` 的 `input_preview.items` |
| Token + 时长 + rounds badge | message footer 解析 `echo.{input_tokens, output_tokens, duration_ms, rounds}` |
| Strategy badge | `react_agentic` / `react_direct_llm` / `react_loop` 等 |
| ReAct 轨迹折叠 | streamdown markdown,轨迹用 `<details>` 包 |
| 嵌套 sub-tool 时间轴 | `LiveToolTimeline.ParentWithChildren` |
| Sidebar collapsible | inline-style 直接控宽,绕过 Tailwind 4 var() reflow 死锁 |

### H · 工程基础
| 能力 | 实现 |
|------|------|
| MCP filesystem 持久化 | `runtime/adapters/mcp_client/persistent_client.py` 后台 asyncio 线程 + 自动重连 + WeakSet 进程级管理 |
| 多模型动态注册 | UI `自定义` tab + `runtime/sensing/siphon/config_router.py` |
| Capability assertion | system prompt 反训练性 denial(memory / SOUL / delegation / skill_library 4 个独立 assertion) |
| Tool-intent 关键词路由 | 触发 agentic 而非 fast-path,保证工具能用 |
| Session ContextVar 透传到 worker thread | 让并发 sub-agent / blackboard skills 看到正确 turn_id / agent_id |

---

## 设计灵感对照

下表把 Echo 现有能力跟外部 frontier agent 设计中观察到的同类模式做对照,
**仅作灵感来源标注**;Echo 不集成、不"融合"、不依赖任何这些产品。

| 维度 | 外部参考(GLM 风格) | 外部参考(Kimi 风格) | 外部参考(MiniMax 风格) | Echo |
|------|---------------------|---------------------|------------------------|---------|
| 长 horizon | 千步级深度循环 | — | — | ✅ ReAct 默认 30 轮(per-mode 100/10000)+ 反思 |
| 并发 sub-agent | — | 数百并发 | — | ✅ 8 并发(可调) |
| 共享黑板 | — | 有 | — | ✅ turn-scoped |
| Skills 文档→模板 | — | 有 | — | ✅ |
| Sub-agent 工具能力 | — | 有 | — | ✅ 5 轮 mini |
| 自改 scaffold | — | — | 有 | ✅ `update_soul` |
| 自动 revert | — | — | 有(canary) | ✅ `revert_soul` + 自动 pre-revert snapshot |
| LLM 自评 | — | — | 有 | ✅ `deep_reflect` |
| Autonomous evolution | — | — | 多轮 | ✅ `deep_evolve`(dry_run 默认) |

差距主要在**规模**(外部 agent 训练专用模型 + 大规模并发)和**底层模型**;
Echo 在通用 chat model 上做架构层面的对齐,数量级追赶留给后续训练自家模型时再说。

---

## Quick start · 试每个能力

每个能力都有一个 30 秒可跑的 smoke。前提:`python -m runtime.cli serve --config config.local.yaml --port 8000` 已启动。

```bash
# 长 horizon
python tmp/smoke_long_no_mcp.py

# 并发 swarm + 黑板
python tmp/smoke_swarm.py

# 跨 thread + SOUL 热重载
python tmp/smoke_self_evolve.py

# B1+B2+B3 三档自评
python tmp/smoke_b2_b3.py

# Skills 系统(learn → list → apply)
python tmp/smoke_skill_library.py

# Token + 时长 stats
python tmp/smoke_token_stats.py
```

---

## 本仓库的设计原则

1. **架构对齐 > 数量对标**。核心架构思想在设计阶段就已植入,数量级追赶留给训练自家模型时再说。
2. **失败 fail-closed,不污染主路径**。所有"meta" 能力(scoring / 自评 / 反思)出错时只 log 警告,never blocks user reply。
3. **Anti-denial 三件套**(关键词路由 / capability assertion / 高描述强度 skill)。LLM 经常本能否认它能做的事;这三层是补丁。
4. **Atomic-by-default + 显式高级 skill**。所有 read-only 能力都是 atomic 自动可用;高成本的(deep_evolve / call_agent_parallel)显式注册并加 budget。
5. **Snapshot 优先于 mutation**。任何会改 agent persona 的写操作前先快照,保证"吃后悔药"永远 1 行命令。

---

## 还差什么

工程层短期路线:
- **集群规模**:单进程 8 并发 → 跨进程 / 跨机部署
- **Skills 系统**:目前只支持 markdown 文本样例;支持 PDF / PPT / Excel 解析
- **B3 真闭环**:目前 LLM-judge 是预测 impact,真正的 canary 评估需要 holdout 集
- **自动 revert 决策**:目前 B1 只给建议,不自动 revert;加用户授权后可自动

模型层(需要换/训底层模型):
- 协调器原生(Kimi 把 swarm coordination 训进了模型本身)
- 长程 stability(Kimi 5 天连续 vs 我们几小时见 token cost 撞上限)
- 文档式 skill 抽取的高保真度(K2.6 专门训练过)
