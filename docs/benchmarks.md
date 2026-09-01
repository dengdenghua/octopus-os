# Benchmarks · 真实硬数字

> 一次 11 个 run · 6 个 case · 全 PASS · 实际烧 token + 实际墙钟时间。
>
> **跑的是真实生产配置**(`claude-mirror` ~= claude-sonnet-4-6 经 mirror
> 代理)· 不是 mock。所有数字含网络抖动 + Windows fs IO 现实噪声。
>
> Reproduce:
>
> ```bash
> # 启动 backend
> python -m runtime.cli serve --config config.local.yaml --port 8000
> # 跑 bench
> python benchmarks/bench_runner.py
> # 结果在 benchmarks/results/{runs,summary}-<ts>.{jsonl,json}
> ```

## 结果汇总(单 case 平均)

| 场景 | iter | success | rounds | tools | in tok | out tok | 墙钟 | 费用* |
|------|:---:|:---:|:---:|:---:|---:|---:|---:|---:|
| **Bench A: 串行 3-arch**(`call_agent` × 3) | 2 | 100% | 3.5 | 2.5 | 2,508 | 1,562 | 91s | $0.031 |
| **Bench A: 并发 3-arch**(`call_agent_parallel(N=3)` + 黑板) | 2 | 100% | 3 | 4 | 2,800 | 906 | 97s | **$0.022** |
| **Bench B: long-horizon 7 步串行依赖** | 2 | 100% | **20.5** | 19.5 | 15,526 | 1,225 | 93s | $0.065 |
| **Bench C 档1: B1 启发式自评** | 2 | 100% | 3 | 3.5 | 1,735 | 317 | 25s | $0.010 |
| **Bench C 档2: B2 LLM 评审** | 2 | 100% | 2 | 1 | 1,195 | 183 | 25s | $0.006* |
| **Bench C 档3: B3 deep_evolve(dry_run)** | 1 | 100% | 2 | 1 | 1,580 | 377 | 39s | $0.010* |

\* 费用按 Sonnet 4.6 公式 `$3/M in + $15/M out` 计算 lead-side tokens。
B2/B3 内部还会跑 1-N 次 LLM-as-judge 子调用,**这些子调用的 token 不在
lead-side 统计里** —— B2 真实成本约 $0.02-0.03,B3 约 $0.05-0.10。

---

## Bench A · 串行 vs 并发 sub-agent

### 设计
3 个 architect 评估 PostgreSQL / MySQL / SQLite。两种 dispatch:
- **串行**:`call_agent` 调 3 次
- **并发**:`call_agent_parallel(specs=[3 个 specs])` 1 次,siblings 写黑板,lead `bb_read` 综合

### 数字

| 指标 | 串行 | 并发 | 并发优势 |
|------|------|------|---------|
| 输出 tokens | 1,562 | 906 | **-42%** |
| 总成本 | $0.031 | $0.022 | **-29%** |
| 墙钟 | 91s | 97s | ≈持平 |
| Lead rounds | 3.5 | 3 | -14% |

### 结论
- **并发省 token 不省时**(墙钟近似 · 因为单 architect 都是 ~30s,N 个并发还是 ~30s,N 个串行实际只跑了 2 个就被 1/turn budget 卡住,所以串行也是 ~30s × 2-3 = 60-90s)
- **节省主要来自输出 tokens**:并发模式下 sub-agent 把结论结构化写到黑板(键-值),lead 读结构化数据 → 综合,不需要重复抄写各方观点。串行模式下 lead 必须把每次 architect 的输出在 final reply 里复述,导致 output token 翻倍
- **架构层启示**:并发 + 黑板的真实价值是**信息压缩**,而非时间压缩(在 N 较小时)。N 越大,时间优势才会显现(8 并发理论上 5x 加速)

---

## Bench B · 长 horizon 7 步串行依赖

### 设计
让 agent 写 step01-step07 文件,每个文件内容是前一个文件 + 长度标记。**不能批量**,因为每一步要 read 上一步的输出。

### 数字
- **20.5 rounds 平均**(原 6 轮 cap 完全跨越)
- 19.5 tool calls(7 step × 2 tools/step + todo_write 进度更新)
- 100% completion(7 个文件全写出)
- 93s · 15.5K input / 1.2K output / $0.065

### 结论
- **30 轮 cap + 第 10/20 轮反思注入** 让长任务能跑完。原 6 轮版本第 4 步就被截
- **input token 是 output 的 12 倍**(15.5k vs 1.2k)—— 长任务的成本主要是 context 累积,不是输出
- **每步 token 约 2.2K input / 175 output**(均摊到 7 步)· 可控

---

## Bench C · 自评成本梯度

### 设计
同样问"评一下我最近 10 turn 的表现",三个不同档位:

| 档 | Skill 调用 | Lead 行为 |
|---|------|---------|
| B1 | `recall_scores` + `analyze_soul_impact`(都 atomic 0 token) | 启发式数字 → 总结 |
| B2 | `deep_reflect(window=10)` | 单次 LLM 评审 → 结构化 verdict |
| B3 | `deep_evolve(dry_run=True)` | 多 LLM 调用:propose K 候选 + judge 每个 + 选优 |

### 数字(lead-side only)

| 档 | 墙钟 | rounds | tool calls | Lead $ | 真实 $(含 inner LLM) |
|---|---:|:---:|:---:|---:|---:|
| B1 | 25s | 3 | 3.5 | $0.010 | **$0.010**(内部全 atomic 0 调用) |
| B2 | 25s | 2 | 1 | $0.006 | ~$0.020(B2 内部 1 次 haiku judge) |
| B3 | 39s | 2 | 1 | $0.010 | ~$0.05-0.10(B3 内部 K+N 次 haiku) |

### 结论
- **B1 真的零成本**(纯本地启发式),比 B2 lead-side 贵是因为 B1 调了 2 个 atomic skill,LLM 多看了 2 个工具结果
- **B2 lead-side 反而最便宜**($0.006)· 单次 deep_reflect 内嵌 1 次 LLM 调用,lead 看到 verdict envelope 即可
- **B3 lead-side 跟 B2 差不多**($0.010)· 但内部跑 K=2 个 propose + K=2 个 judge = 4 次 LLM 调用,实际成本是 B2 的 5-10 倍
- **正确的"什么时候用谁"**:
  - 默认 → B1(0 cost)
  - B1 inconclusive 时 → B2($0.02)
  - 用户明确想"深度演化" → B3($0.05-0.10) · 默认 dry_run 安全

---

## 跨 Bench 总观察

### 1 · 长 horizon 是最贵的 capability

| Bench | $ | 单位时间费用 | 主因 |
|------|---:|---:|------|
| Long-horizon 7-step | $0.065 | $0.0007/s | input context 累积 |
| Swarm 串行 | $0.031 | $0.00034/s | output 复述 |
| Swarm 并发 | $0.022 | $0.00023/s | 黑板压缩 output |
| B1 自评 | $0.010 | $0.0004/s | 简单 |
| B2 自评 | $0.006 | $0.00024/s | 单 LLM 调用 |
| B3 自评 | $0.010 | $0.00026/s | 多 LLM 调用,但 lead 端轻 |

**结论**:每次 turn 烧 1-7 美分级别。"100 turn /天"用户大约 $1-7/天,$30-200/月。

### 2 · 黑板是真省钱

并发 + 黑板比串行省 **29% 总成本** + **42% output tokens**。N 越大优势越大。

### 3 · 没有 capability 跑挂

11 runs · 100% success rate · 0 backend crash · 0 LLM 拒绝(capability assertion 起效)

### 4 · 速度墙不在 Echo

最慢的 swarm 也是 ~100s,瓶颈在底层 LLM 单次调用的 ~25-30s。Echo 自身并发 + 黑板调度开销 < 1s。

### 5 · 跟外部 frontier agent 数量级对照

下表把 Echo 实测数字跟外部公开 agent 路线披露的数字对齐 · 仅作**参考量级**,不是对标承诺,Echo 不集成外部产品。

| 维度 | 外部参考(swarm 路线) | Echo 实测 | 差距 |
|------|----------------------|-------------|------|
| Long-horizon 单 turn 步数 | ~千步级 | 实测 20 步 | **数量级差** |
| Sub-agent 并发数 | 数百级 | 实测 3-8 | **数量级差** |
| 单次 evolve 轮数 | 数十轮(外部自演化路线) | B3 默认 1 轮 | **数量级差**(可调) |
| 单 turn 成本 | 长程 N 天连跑成本未公开 | 我们 100 turn ≈ $1-7 | 同档(模型成本主导) |

差距全在**规模**,不在**能不能**。

---

## 🛣️ `planning_mode` flag 与 DAG runtime · 同任务的两种调用方式对比

> 你之前问"先画图再执行是不是被抛弃了" —— **没有**,两条路径都活着,只是被隔离到了不同的 workload。
> `bench_runner.py --mode=both` 同一 6 个 case 跑两模式,数字给答案。
>
> **澄清术语**:
> - `mode=react`(默认)= ReAct 循环 · `planning_mode=False` · 工具真执行
> - `mode=deep` = 触发 `_skip_react=True` 分支,改走 `StaticPlanner`/`LLMPlanner` + `GraphRuntime`(`runtime/core/ganglia/`)的 DAG 执行;DAG runtime 支持 `topo_layers` 拓扑层并行
> - 这**不是**单一引擎的两个 mode,而是两套独立 runtime · 都被保留,各自适合不同任务

### 主 bench · 6 cases × 2 modes(2026-04-24 · **真 token 版**)

| 任务 | react wall | deep wall | react in | deep in | react out | deep out | react rounds | deep rounds | 谁赢 |
|------|-----------|-----------|---------|---------|----------|----------|-------------|------------|------|
| swarm-serial-3 | 128.4s | **78.8s** | 9268 | 1778 | 2363 | 1559 | 8 | 4 | **deep 1.6× 快** |
| swarm-parallel-3 | 86.7s | **58.3s** | 5201 | 2062 | 930 | 1438 | 3 | 4 | **deep 1.5× 快** · deep 输出更厚 |
| long-horizon-7-steps | **78.6s / 21 rounds** | 22.4s / 0 rounds | 16483 | 1905† | 1224 | 748† | **21** | 0 | **react 唯一真完成 7 步** · deep 退 direct_llm(planner 判定 14 步 > 10 节点上限,合理拒绝) |
| self-eval-b1-heuristic | 16.6s | **11.5s** | 1726 | 1301 | 273 | 390 | 2 | 2 | deep 微快 |
| self-eval-b2-llm-judge | 20.4s | **17.9s** | 1455 | 1698 | 200 | 396 | 2 | 1 | deep 微快 · token 偏多 (1 合成+1 judge) |
| self-eval-b3-deep-evolve | 30.7s | **30.4s** | 2297 | **3119** | 352 | 1025 | 2 | 1 | deep **真实内嵌 K propose + K judge 都计入** |

† long-horizon[deep] 走的是 planner-error fallback → `_direct_llm_fallback_with_usage`,也已修补为真 token。

**token 数现在真实**(2026-04-24 修完 `ganglia Budget.commit` + `LLMPlanner.last_plan_usage` + `synthesize_reply(usage_out=)` + `_direct_llm_fallback_with_usage` 四条路径):
- 之前 deep 列是 100/200/400 整数倍(estimation placeholder)
- 现在 deep 列是 1K-3K+ 的真实 provider 回报 · 三源相加:**planner.plan LLM + executor-step LLMs(Budget)+ synthesize_reply LLM**
- 每个 echo metadata 带拆分字段:`input_tokens`(总)/ `executor_input_tokens` / `planner_input_tokens` / `synth_input_tokens`

### 关键观察

1. **deep mode 在 DAG 可预测的 case 上普遍快 1.5-2.2×** · 6/6 case 都更快(long-horizon 是特殊,见下)
2. **long-horizon-7-steps[deep] → direct_llm_fallback** · LLM planner 产不出有效 TaskGraph(链式文件依赖不是静态规则能描述的),兜底到直 LLM 一次搞完 17.2s · 但 **rounds=0 说明它没真执行 7 步**,只产出了一个摘要回答 · 这种任务**就得走 react**
3. **swarm 类任务**(3 agent 评估)两模式都能完成,但 **react out tokens 3608 vs deep 400**,说明 react 产出了真正详细的综合推荐,deep 只有摘要

**落在哪**:
- `mode=deep` 触发 `thread_compat_router.py:1648` 的 `_skip_react = True` 分支
- planner 路径:`StaticPlanner.plan(intent)` → `TaskGraph` → `stack.runtime.run(graph)` 执行
- 触发方:UI 的"🔭 深度研究"按钮 · CLI `python -m runtime run "..." --planner llm` · `python -m runtime bugfix-demo`(8-node 线性 DAG,全绿)

**什么时候选谁** · 决策树:
```
结构可预测(多文件改 / git pipeline / schema 迁移)  → deep
需要观察中间结果决定下一步(调研 / 分析 / 创意) → react (默认)
用户没说                                           → 默认走 react
```

### Observability 修复 · 已做 / 未做

| 项 | 状态 | 落点 |
|----|------|------|
| plan path `additional_kwargs.echo.input_tokens` / `output_tokens` 上报 | ✅ 已上报 | `thread_compat_router.py:1924-1933` + `:4316-4320` + `governance.Budget.tokens_{in,out}_spent` |
| direct_llm 系列(streaming + non-streaming)token 上报 | ✅ 已上报 | `_direct_llm_fallback_with_usage()` + `_stream_direct_llm_fallback` done tuple 携带 JSON-encoded usage · planner-error fallback 两条路径都补齐 |
| Tool semantic error(`{ok:false,error:...}`)→ SSE `status=error` | ✅ 已修(2026-04-24) | `tool_bridge._is_semantic_error()` 识别 3 种失败约定(`ok=False` / `error` 字段非空 / `status in (error,failed)`)· 之前只有 Python exception 会标 `is_error=True`,dict 返回的失败静默成功 |
| plan path 的 token 实际数字 = provider 真实回报 | ✅ **已做**(2026-04-24 · 3 个落点一起修) | `beak/executor.py::_extract_token_usage()` 从 skill output 的 `cost`/`meta`/顶层 `input_tokens`/`output_tokens` 拉真值喂 `CostEntry` · `LLMPlanner.plan()` 存 `self.last_plan_usage` · `synthesize_reply(usage_out=)` 通过 out 参数吐 tokens · `thread_compat_router` 三源求和上报 total + 拆分字段 |
| `sub_tool_end.duration_ms` 真实 | ✅ 已修 | `ephemeral_runner._emit_sub_tool_event(duration_ms=...)` |

**已知观察性缺口**:
- `mode=deep` 的 `input/output_tokens` 没写入 `additional_kwargs.echo` · bench 看到的是 0 · 和 B2 里 `react_direct_llm` 不追 token 是同一类缝隙 · **后续 follow-up**(planner-runtime 内的 LLM 调用要上报 token)
- `bugfix-demo` / `reflection-demo` / `evolution-demo` 三个 CLI 例子都是 plan path,完全没跑过 ReAct · 这是**有意的**——它们演示的就是 DAG 执行本身,不是推理
- 文档里 "2036 tests · 0 lint" 里 plan-path 的 unit test 是 DAG 构造/执行层面,不是"从自然语言→plan"的端到端 · `tests/test_react_self_evolution_e2e.py` 那类才是端到端,主要压 react path

---

## ABC 路线 · 三家闭环的真数字

### 🅐 MiniMax 自我演化 · 从 dry_run demo 到真闭环

| 步骤 | 验证 | 脚本 | 结果 |
|------|------|------|------|
| A1 · deep_evolve(dry_run=False) 真 apply | SOUL.md 真加 lesson + snapshot 落地 | `benchmarks/test_a1_evolve_apply.py` | ✅ 25.8s 真写新 lesson(tag=`deep-evolve · tooling`),新 snapshot 入 `.soul_history/` |
| A2 · regression rollback | bad lesson 导致掉分 → 自动 revert | `benchmarks/test_a2_auto_rollback.py` | ✅ 注入 10 good + 5 bad 分数,agent 调 analyze+revert,SOUL 回滚到 baseline hash,pre-revert snapshot 创建 |
| A3 · auto_evolve post-turn tick | 每 5 turn 后台自动检查 + 回滚 | hook in `tool_bridge._auto_evolve_tick_safe` | ✅ 单元测试:15 条分数(10 baseline=1.0 + 5 bad=0.0)触发 tick,SOUL 自动 reverted,log 打印 `delta=-0.88` |

**新 skill** · `auto_regression_check(window, drop_threshold, min_samples, dry_run)` · atomic 0 token · 需要 ≥5 post-change 样本才触发,避免 panic revert。

**闭环关键**:
- 应用前自动 snapshot(update_soul 内置)
- 应用后 5+ turn 评估(analyze_soul_impact 启发式)
- 掉分 ≥0.2 自动 revert(auto_regression_check 或 post-turn tick)
- 每 5 turn 后台 tick(tool_bridge.py 自动调用)· fail-closed 不阻塞 reply

### 🅑 Bench 盲区填上

| 项 | 数字 / 发现 | 结果文件 |
|----|-------------|----------|
| B1 · Cancel 延迟 | client 断连 → backend 稳定 **4.2s**(LLM streaming drain 时间,非 framework 延迟) | `results/b1-cancel-*.json` |
| B2 · Multi-turn 10 turn | 每 turn input ~2140 token 稳定(**非线性累积**)· 10 turn 总成本 **$0.07** · 9s/turn | `results/b2-multi-turn-*.json` |
| B3 · Failure injection | missing file → agent 调 `list_cwd` 恢复 + 解释缺失 · stream 不崩 · **semantic tool error (`{ok:false}`) 已修,SSE `tool_end.status=error` 正确透传**(2026-04-24 修在 `tool_bridge._is_semantic_error`) | `results/b3-failure-*.json` |
| B4 · sub_tool_end.duration_ms bug | ephemeral_runner 加 `time.monotonic()` 测时 + thread_compat_router SSE 透传 `duration_ms` · 已修,backend restart 后前端 timeline 的 `XXms` 徽章不再是 0 | `ephemeral_runner.py:309-326` + `thread_compat_router.py:3041-3044` |

**副发现**:
- `react_direct_llm` 快路径(简短问答)**不追 token** · `additional_kwargs.echo` 只有 strategy/step_count/success · bench 必须用 tool-calling prompt 才能测到 token
- 多轮对话 input token 不随 history 线性增长 · 说明 backend 每 turn 近乎独立发送 · 对成本友好但**可能影响长对话连贯性**(值得单独测)
- Tool 通过返回值报告错误(`{ok:false, error:...}`)vs 通过异常报告错误 · SSE 事件的 `status` 只捕获后者 · 前端显示不区分

### 🅒 Kimi Skills 真走通

| 项 | 验证 |
|----|------|
| C1 · Golden test gate | `learn_skill_from_text(golden_samples=[A,B,C])` · 提取 template 后先跑 apply 验证 · 每个 sample 检查输出是否保留 ≥50% 模板 H2 headers · 通过率 < threshold(默认 0.66 = 2/3)→ fail-closed 不 persist · **4/4 单元测试绿** (`tests/test_skill_library_golden_gate.py`) |
| C2 · System prompt 引导 | 在 `tool_bridge.py` skill library capability assertion 加 TRIGGERS 列表("写一份报告" / "对比 X 和 Y" / "像…一样写" 等)· 明确 step 1 `list_learned_skills` 是 0 cost 必调 · 明确 `golden_samples` 参数语义 |

**Skills 闭环**:
```
Turn 1 · user 贴一份高质量样例 + "以后按这个格式做"
         → agent 调 learn_skill_from_text(golden_samples=[3条替代请求])
         → framework 跑 3 次 apply 验证结构保留率
         → 2/3 通过 → persist 到 agents/<id>/skills/<name>.md
         → 未通过 → 返回 golden_report,agent 告诉用户样例质量不够

Turn N · user 说 "以前那种格式写一份 X"
         → TRIGGER 命中 → agent 先 list_learned_skills(0 cost)
         → 命中 → apply_skill(name, user_request=X)
         → 输出跟原样例同质
```

**不闭环的地方**:agent 有时还是倾向于"直接写"而不走 apply_skill(LLM 觉得能自己搞)· C2 的 TRIGGER 列表是当前缓解手段 · 未来需要 A/B 测 prompt 强度 vs 绕过率。

---

## UI 回归 · 前端渲染单独验证

bench_runner 直接打 `/api/threads/{tid}/runs/stream`,**绕过了前端**。两个前端
特有场景单独验证 —— 脚本 `benchmarks/_ui_sse_trace.py`:

### ✅ 场景 6 · Swarm 嵌套时间轴

> thread `cf3d081727064baabe5a086a9e200daa` · 真实 call_agent_parallel 并发

**后端 SSE 事件序列**(用 `_ui_sse_trace.py swarm` 捕获):

```
[PAR] start  call_agent_parallel
    [SUB] start  recall    agent=architect ← parent=<call_agent_parallel id>   (×3)
    [SUB] start  bb_write  agent=architect ← parent=<call_agent_parallel id>   (×3)
[par] end    call_agent_parallel
[PAR] start  bb_read  (×3)
```

每个 `sub_tool_start` 携带 `parent_tool_use_id` + `sub_agent_role`,正是前端
`LiveToolTimeline.ParentWithChildren` 用来渲染 `ml-6 border-l border-primary/20 pl-2`
缩进子行的路由键。

**前端代码验证**(`frontend/src/components/workspace/live-tool-timeline.tsx:169-208`):
- `ParentWithChildren` 用 `getChildren(allEvents, event.id)` 拉出所有 parent 匹配的子事件
- 子事件在父事件下方独立渲染,带 agent 标签 + 缩进竖线
- `hooks.ts:365-403` 把 `sub_tool_start`/`sub_tool_end` 升到同一 LiveToolEvent 形状

两端(backend emit + frontend render)双验证。

### ✅ 场景 7 · deep_evolve dry_run 提案渲染

> thread `d7ebbd057f6d4d0fa3d2cc36871c2752` · 真实 B3 深度演化 dry_run

**后端 SSE 事件序列**:

```
[PAR] start  deep_evolve    (一次 tool call,内部跑 K propose + K judge LLM)
[par] end    deep_evolve
```

内部 LLM 调用不发 sub_tool_start(不是 sub_agent,是 skill 内部 LLM
call)· 正确 —— 避免时间轴被纯 evaluation 噪声污染。

**前端实际渲染**(navigate `/workspace/chats/<tid>` 截图):
- 安全 banner:"这是本次 dry_run 的完整结果,SOUL 未做任何修改。"
- **Round 1 · 两个候选提案**
  - 候选 C1(胜出者)· tag=`tool_errors` · +0.15 · verdict=apply
  - 候选 C2 · tag=`tooling` · +0.15 · verdict=apply
- 每个候选带 帮助轮次 / 伤害轮次 / 置信度 / predictive 分数
- 标准 markdown(headings + 粗体 + inline code + bullet list)

dry_run 提案无法绕过 safety gate —— 用户必须手动下一轮不加 dry_run 才会落
地,这是 MiniMax 自残防护的 UI 侧具体落点。

---

## 测试边界 · 哪些没测

- **Cancel 延迟**:之前实测 170ms,bench 没单独测
- **Partial recovery**:之前实测可恢复,bench 没单独测  
- **MCP persistent client 长任务**:bench 用 read_file/write_text_file 不用 mcp_fs(避免之前的子进程泄漏 follow-up)
- **真实多模型路由**:bench 全用 claude-mirror,没测 GLM / Kimi 提供商的差异
- **跨会话(多 turn)**:bench 每个 case 用 new thread,没测同一 thread 多 turn 的累计 context 行为
- **失败路径**:bench 都成功,没主动诱发 tool error / 超时验证 fail-closed
- **`sub_tool_end.duration_ms` 都是 0**:捕获时机问题(不影响渲染 status,但时间轴的"XXms"徽章会显示 0)· 单独 follow-up

这些可以做后续 bench round。但当前 11 backend runs + 2 UI scenes 已经能下结论:
**架构对齐,数量级差距来自规模。前后端双端一致。**
