# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
Versions follow [SemVer](https://semver.org/), pre-1.0 so breaking changes are allowed.

---

## [Unreleased] — 2026-05 · multi-author audit sweep

Non-breaking cleanup pass across backend + frontend + infra after the
project accumulated drift from multiple contributors / tools. Focus:
correctness > style.

### Security

- **Rotated leaked credentials** · `data/custom_models.json` carried
  two real-shape API keys (Anthropic `sk-ant-api03-...` + OpenAI-shape
  `sk-...`) in a gitignored-but-physically-present file. Replaced with
  `${ANTHROPIC_API_KEY}` / `${GLM_API_KEY}` env placeholders.
- **`/api/fs/revert` workspace whitelist** · the endpoint previously
  accepted any `workspace` / `path` a WS client could dereference
  (arbitrary `git checkout --` on the server host). Now rejects paths
  outside `ECHO_FS_ALLOWED_ROOTS` / `$ECHO_DATA_DIR` /
  `$ECHO_HOME` / CWD and enforces that `path` lives under
  `workspace`.
- **`verify_skills` shell-injection surface removed** · replaced
  `subprocess.run(cmd_str, shell=True)` with `argv + shell=False` for
  every `detect_project()` check. The legacy `base64+exec` inline
  Python trampoline is gone. Old `cmd` key path still honored (logs a
  WARNING) for external profile callers.
- **`ssh_mantle` MITM warning** · when `strict_host_key_checking=False`,
  both CLI and paramiko backends now log a WARNING once per session.
  Paramiko path additionally loads `known_hosts` when provided so
  already-pinned hosts still fail-closed on mismatch.

### Runtime hardening

- **`config.loader` env interpolation** · `${VAR}` now also matches
  bare `$VAR` form so `config.yaml: api_key: $ANTHROPIC_API_KEY` no
  longer resolves to the literal string `"$ANTHROPIC_API_KEY"`.
- **`runtime.protocol` re-exports** · `PlanItem`, `TodoListItem`,
  `TodoEntry` added to the package-level `__all__`.
- **`run_react_loop(thread_id=, approval_provider=)` restored** ·
  `runtime.execution.misc.parallel_runner` was calling it with those
  kwargs; the wrapper now forwards them to `stream_react_loop`.
- **Realtime gateway fastapi guard** · `runtime.sensing.gateway.realtime_gateway`
  now uses the `FASTAPI_AVAILABLE` try/except pattern so importing it
  without fastapi installed no longer blows up.
- **Bounded TTL on previously-unbounded state** · `PauseController._grants`
  + `_pending_resumes` gained 7-day TTL + GC · `channels_router._PairingStore.pending`
  gained per-channel 200-entry cap + 24h TTL · `ExtensionRegistry`
  retains `asyncio.create_task` references in `_pending_deactivations`
  so Python 3.12+ can't GC them mid-await.
- **`structured_logging` ContextVar default** · changed `_extra`
  default from mutable `{}` to `None` (mutable defaults on ContextVar
  leak across contexts that read before any write).
- **`metrics` histogram zip** · both `dict(zip(self.bucket_bounds, buckets))`
  sites now pass `strict=True` so bucket-length mismatches fail loudly.

### Retired / deleted

- **Backend SSE proxy** · `runtime.sensing.gateway.remote_transport.stream_proxy`
  + `POST /api/remote-backends/{id}/stream` removed (no frontend callers;
  WebSocket relay at `/api/remote-backends/{id}/realtime` is the sole
  path now).
- **Frontend SSE callsites fail-fast** · `client.runs.stream` /
  `.wait` / `.getStatus` / `.getEvents` now throw descriptive errors
  pointing at `/api/realtime` (previously silently 404'd against the
  retired backend). The three `/workspace/{chats,code,team}/[thread_id]`
  pages carry an amber "Use realtime UI →" header button linking to
  `/realtime/:threadId`. Approval fetches replaced with
  `window.dispatchEvent('echo:tool-approval-reply')` events.
- **`benchmarks/` suite** · all 8 scripts replaced with
  `sys.exit(2)` stubs — the SSE endpoints they targeted are gone;
  harness will be re-enabled once a realtime-WS bench adapter lands.

### Layout

- `config.yaml` (bio-named, 280 lines of `spinal_cord: / cerebrum: /
  ganglia: / ...` that pydantic silently dropped) moved to
  `docs/design/config.design.yaml` with a 26-line DEPRECATED header.
  `make up` / `make up-full` continue to auto-bootstrap a runnable
  `config.yaml` from `config.example.yaml`.
- `.echo-browser-relay/` (actual product surface — Chrome MV3
  extension) promoted from gitignored root dot-dir to
  `extensions/echo-browser-relay/`. `browser_router` now searches
  both locations; fresh checkouts create the new one.
- Root-level scratch / logs moved to `.echo/local/root-cleanup-2026-05-09/`
  (backend logs, `.runtime-logs/`, `artifacts/`, `logs/`).
- Empty placeholder dirs deleted (`frontend/src/app/workspace/admin/`,
  the two `[thread_id]` / `new` children, `frontend/src/app/observability/`
  old page, `frontend/tmp-postcss-test.mjs`).

### Docs / build

- **Docker image** · `Dockerfile` now COPYs `agents/`, `skills/`,
  `prompts/`, `protocols/`, `teams/`, `config.example.yaml` into the
  runtime stage (previously the image shipped with missing resource
  directories). `.dockerignore` no longer excludes `protocols/`.
- **`.env.example`** · added `REDIS_PASSWORD` + `GRAFANA_PASSWORD`
  required by `docker-compose.full.yml`.
- **docs link rewrites** · 13 files with upper-case markdown links
  (`[INVARIANTS.md]`, `[GENOME.md]`, ...) rewritten to actual
  lower-case filenames. CI templates (`PULL_REQUEST_TEMPLATE.md`,
  `feature_request.md`) link targets corrected.
- **mkdocs nav** · expanded from 8 to 38 entries covering all existing
  `docs/` files (入门 / 架构 / 仿生模型 / 不变量与标准 / 能力与基准 /
  ADR / 自动文档 / 工程笔记 / 贡献).
- **CHANGELOG header** · duplicate "# Changelog" block removed.
- **Test-count references** · `docs/roadmap.md`,
  `docs/getting-started.md`, and `PULL_REQUEST_TEMPLATE.md` now say
  "3800+" (historical CHANGELOG entries keep their original snapshot
  numbers).
- **`version_compat`** · `deploy/k8s/configmap.yaml` and
  `demos/demo_config.yaml` bumped from `"0.1"` to `"0.2"`.

### Frontend

- **`@codemirror/state` + `@codemirror/view`** added to
  `frontend/package.json` `dependencies` (were used by 5 editor files
  but never declared). `@types/canvas-confetti` added to
  `devDependencies`.
- **`vite.config.ts`** · `/api` proxy now carries `ws: true` so
  `ws://localhost:3000/api/realtime` tunnels to the backend in dev.
  Duplicate `mermaid` alias (both plugin + `resolve.alias`) deduped.
- **`env.js`** · added short `STATIC_WEBSITE_ONLY` alias alongside the
  `NEXT_PUBLIC_STATIC_WEBSITE_ONLY` long form so the 11 call-sites
  that use the short name resolve.
- **`extractThinkingPlanFromMessage`** · exported from
  `stream-utils.ts` (was module-private but `use-stream.ts:934`
  imported it across files).
- **Type narrowings** · `observability/page.tsx` `SubToolRecord`
  assignment, `messages/utils.ts` regex-group nullability,
  `page-agent-bridge.ts` `parent` self-reference, `task-board/timeline-view.tsx`
  `_viewWidth: number` annotation.

### Lint

- `ruff check runtime tests`: **389 → 31** (−92.0%). All remaining
  are pure readability (`SIM102` collapsible-if, `SIM103` needless-bool,
  one noqa'd `B023` loop-variable capture).

### Tests

- pytest collection: **3488 tests + 1 collection error** → **3802
  tests · 0 errors**. Collection was blocked by
  `tests/test_react_loop.py` importing helpers that had migrated to
  `react_parsing` / `react_execution` / `react_guards`; `react_loop.py`
  re-exports them via `__all__` again.
- Removed the 4 SSE-proxy tests that targeted the retired
  `/api/remote-backends/{id}/stream`; added no new tests (this pass
  was cleanup only).

### Not done (tracked for follow-up)

- Three `/workspace/{chats,code,team}/[thread_id]` pages still call
  `useThreadStream` (which hits the retired SSE endpoints and throws).
  Completing the migration needs a `Conversation ↔ AgentThreadState`
  adapter layer (~5k lines of dependent components) and browser UI
  verification — not mechanizable in one pass.
- `benchmarks/` harness needs a realtime-WS adapter before the 8 stub
  scripts can be un-retired.
- `runtime/sensing/siphon/openai_gateway.py` `/v1/chat/completions`
  intentionally stays on SSE for OpenAI-compat double-stack.
- `pnpm install` in `frontend/` pending — that's what lands the
  codemirror / canvas-confetti types.

---

## [Unreleased] — realtime WebSocket transport · item-oriented protocol

**Breaking: SSE + POST retired.** The `/api/threads/{id}/runs/stream`,
`/runs/stream`, `/api/threads/{id}/runs/wait`, `/runs/wait`,
`/api/threads/{id}/approve`, and `/api/runs/*` endpoints no longer exist.
Clients must speak JSON-RPC 2.0 over the WebSocket at `/api/realtime`.

### Added

- **`runtime.protocol`** — JSON-RPC 2.0 envelope + closed `ClientMethod` /
  `ServerMethod` enums + discriminated-union `Item` / `Turn` models.
- **`/api/realtime` WebSocket** (`RealtimeGateway`) — bidirectional
  JSON-RPC. Server-initiated requests (command approval, user input
  elicitation) ride the same channel; awaited via `asyncio.Future`
  bound to the connection. No global dict. No `threading.Event`.
- **`RealtimeRuntime` protocol** — `EchoRuntime` (reference,
  headless-safe) and `CerebrumRuntime` (react-loop backed; live when
  `stack is not None`).
- **`runtime.memory.threads.event_log.EventLog`** — per-thread
  append-only JSONL. `turn/started` / `item/started` / `item/delta` /
  `item/completed` / `turn/completed`. Rebuilds via `thread/resume`
  after reconnect or process restart.
- **`runtime.safety.approval.approval_gate.ApprovalProvider`** — injection point
  replacing the previous global `_pending` dict. Ships with
  `AutoApproveProvider` (tests) and `AutoDenyProvider` (fail-closed
  default). `stream_react_loop` takes `approval_provider=` kwarg.
- **Frontend `@/core/realtime`** — `RealtimeClient` (reconnect with
  jittered exponential backoff, outbox, approval reply tracker),
  `reduce(...)` pure reducer keyed by `itemId`, `useRealtimeThread`
  React hook. Routes `/realtime` (index) + `/realtime/:threadId`
  (full thread UI — reasoning / agentMessage / commandExecution /
  approval tray / composer).
- 10 backend realtime-gateway tests, 6 cerebrum-runtime tests, 9
  frontend reducer tests. Full suites green.
- `demos/realtime_echo.py` — runnable in-process smoke test.

### Removed

- `runtime/sensing/siphon/run_registry.py` — SSE run-status registry.
- `runtime/sensing/siphon/thread_compat_degraded_router.py` — no-stack
  SSE fallback; replaced by the realtime gateway's `EchoRuntime`.
- `runtime.platform.process.utils.sse_event()` — no callers remain.
- SSE helpers inside `thread_compat_router.py`: `_sse`, `sse_headers`,
  `_request_run_id`, `_record_sse_chunk`, `_stream_team_round`,
  `_message_frames`, `_stream_step_text`.
- `/api/threads/{id}/runs/stream`, `/runs/stream`, `/api/threads/{id}/runs/wait`,
  `/runs/wait`, `/api/threads/{id}/approve`, `/api/runs/{run_id}`,
  `/api/runs/{run_id}/events`, `/api/runs/{run_id}/cancel` (+ unprefixed
  aliases).
- `tests/test_thread_compat_stream.py`, `tests/test_sse_concurrent_isolation.py`,
  `tests/test_thread_compat_team_chat.py`, `tests/test_thread_compat_degraded_router.py`,
  `tests/test_react_self_evolution_e2e.py` — all exercised endpoints
  or helpers that no longer exist.

### Changed

- `thread_compat_router.py`: **6766 → 3002 lines** (SSE paths excised;
  CRUD surface retained for thread sidebars and history replay).
- `runtime.safety.approval.approval_gate` rewritten around `ApprovalProvider`.
  Legacy `request_approval` / `wait_for_approval` / `submit_decision`
  free functions deleted.
- `runtime.core.cerebrum.react_loop.stream_react_loop` accepts
  `approval_provider` kwarg.
- `runtime.platform.ui.thread_routes.mount_thread_compat_routes` is a
  no-op when `stack is None` (realtime gateway covers that path).

### Migration

Frontend callers that used to `POST /api/threads/{id}/runs/stream` and
parse SSE frames should:

1. Open a WebSocket to `/api/realtime`.
2. Send `{"jsonrpc":"2.0","id":1,"method":"turn/start","params":{...}}`.
3. Apply `item/started` / `item/<kind>/delta` / `item/completed` /
   `turn/completed` notifications to a client-side conversation
   reducer keyed by `itemId`.
4. On server-initiated `item/commandExecution/requestApproval` (or
   `item/tool/requestUserInput`, `mcpServer/elicitation/request`),
   reply with `{"jsonrpc":"2.0","id":<same>,"result":{"action":"accept"|"decline"}}`.
5. After reconnect, call `thread/resume` and the server replays the
   full turn list from the on-disk JSONL log.

TypeScript types, a stateless reducer, and a React hook for all of the
above live in `frontend/src/core/realtime/`.

---

## [0.2.0] — 2026-04-24 · Beta · 外部路线灵感落地 + ReAct/DAG 双路径可观测

**API 稳定性承诺**:从此版本起进入 **Beta** · `Development Status :: 4 - Beta` · semver 意义下 0.x 仍允许 breaking change,但承诺:
- **公开 skill handler 签名** 在 minor 版本内不破坏(新增 kwarg 有默认值不算破坏)
- **`additional_kwargs.echo` metadata 字段** 只增不删(`input_tokens` / `rounds` / `strategy` 等前端依赖的字段稳定)
- **SSE event 名字**(`tool_start` / `tool_end` / `sub_tool_start` / `sub_tool_end` / `messages-tuple` / `values`)稳定
- **CLI subcommand 列表** 从 `python -m runtime --help` 可见的命令只增不删
- **`ParsedIntent` / `TaskGraph` / `Trajectory` / `Budget` / `CostEntry`** 公开字段只增不删

变更将遵循 deprecation flow:先 warning 2 个 minor 版本 → 再删除。违反则必须 bump major。



**主线**:从外部 frontier agent 路线汲取的设计灵感(长 horizon ReAct 深度 / swarm 并发广度 / SOUL 自演化)三类能力都已落地;ReAct 循环与 DAG runtime 两条独立执行路径都活着,**成本完全可观测**。`2036 → 2800 tests · 0 lint · 0 regression`。

### Added

**MiniMax 风格自演化 · 5 层闭环**
- `runtime/memory/turn_scoring.py` · 零 token 启发式 per-turn 打分(`TurnScore` dataclass · 写 `.scores.jsonl` · 防自残 5000 行 trim)
- `runtime/memory/deep_evolution.py` · `deep_reflect`(B2 · 1 次 haiku judge)+ `deep_evolve`(B3 · K propose + K judge · dry_run 默认)· 带 inner-LLM 路由器 `set_evolve_router`
- `runtime/execution/suckers/memory_skills.py` · 新 6 skill:`update_soul` / `revert_soul`(带 auto-snapshot)/ `list_soul_history` / `recall_scores` / `analyze_soul_impact` / `auto_regression_check`
- **`tool_bridge._auto_evolve_tick_safe`** · post-turn 每 5 turn 后台自动跑 regression 检查 · fail-closed
- E2E 验证:`benchmarks/test_a1_evolve_apply.py`(真 apply + snapshot)· `benchmarks/test_a2_auto_rollback.py`(注入 regression + 自动 revert)· `demos/bugfix_demo_v2.py`(bug fix → update_soul → Round 2 复用 pattern)

**Kimi 风格 Skills 系统**
- `runtime/memory/skill_library.py` · `learn_skill_from_text` / `list_learned_skills` / `apply_skill` · 从高质量样例提取结构 + 风格 DNA,落地 `agents/<id>/skills/<name>.md`
- **C1 Golden gate** · `golden_samples` 参数强制通过 3-sample 结构保留率检查才 persist · 4/4 unit tests 绿(`tests/test_skill_library_golden_gate.py`)
- System prompt TRIGGERS 列表 · 让 agent 优先 `list_learned_skills`(0 cost)

**Bench 体系 + 7 个盲区修**
- `benchmarks/bench_runner.py --mode=both` · 6 case × 2 mode 双路径对照
- `benchmarks/bench_plan_vs_react.py` · 静态/开放任务分工数字(静态 plan 1.5-2.2× 快,开放 react 答案厚 20×)
- `benchmarks/bench_b1_cancel.py` · client 断连 → backend 4.2s drain(LLM streaming 边界)
- `benchmarks/bench_b2_multi_turn.py` · 10 turn 每轮 ~2140 input · **非线性累积**
- `benchmarks/bench_b3_failure_injection.py` · missing file → agent 恢复 · 发现 semantic error SSE status 缺口
- `benchmarks/_ui_sse_trace.py` · UI 回归工具(nested sub_tool timeline + deep_evolve 提案渲染)
- `docs/benchmarks.md` · 完整数字 + 决策树 "何时选 react vs plan"

**Observability 四路径真 token 透传**
- `runtime/platform/models/governance.py::Budget` · 新 `tokens_in_spent` / `tokens_out_spent` 属性(monotonic)
- `runtime/execution/beak/executor.py::_extract_token_usage()` · 从 skill 返回值的 `cost`/`meta`/顶层字段抓真 provider token 喂 CostEntry(替换之前的 est=100 placeholder)
- `runtime/core/cerebrum/llm_planner.py::LLMPlanner.last_plan_usage` · planner 自身 LLM call 的 tokens 暴露给调用方
- `runtime/sensing/siphon/openai_gateway.py::synthesize_reply(usage_out=)` · 合成 LLM 的 tokens 通过 out 参数吐出
- `_direct_llm_fallback_with_usage()` + `_stream_direct_llm_fallback` done tuple 带 JSON-encoded usage(streaming + non-streaming + planner-error fallback 三条兜底全覆盖)
- `additional_kwargs.echo` 现在带 `input_tokens` + `output_tokens` + 拆分字段(`executor_*` / `planner_*` / `synth_*`)· plan path 不再报 0
- `runtime/sensing/siphon/tool_bridge._is_semantic_error()` · tool 返回 `{ok:False}` / `{error:...}` / `{status:error}` 时触发 SSE `status=error` · 之前只有 Python exception 才算错
- `runtime/execution/suckers/ephemeral_runner.py` · `sub_tool_end` 带真实 `duration_ms`(之前恒 0)

**文档**
- `docs/agent-capabilities.md` · 外部路线灵感对照 + 决策树 · C.5 `planning_mode` flag / DAG runtime 段
- `docs/benchmarks.md` · 12 runs × 2 modes 真实数字 + UI 回归 2 场景 + observability 修复表
- `README.md` · bench 精华表(4 个代表 case)· minimal extras 引导 · 测试数 `2036 → 2800`
- `QUICKSTART.md` · A1/A2/A3 三个 smoke 命令 · minimal 依赖路径
- `demos/bugfix_demo_v2.py` + CLI `bugfix-demo-v2` · 真实演化闭环 demo

### Fixed

- `runtime/core/ganglia/runtime.py` · 重复代码块 IndentationError(pre-existing)
- `runtime/execution/suckers/layers.py` · `auto_regression_check` / `code_analyze` / `code_search` 加入 ATOMIC_SKILL_NAMES 防 `BASE_SKILL_IDS` drift
- `.gitignore` 加 `.tmp_*` pattern(外部 review 指出根目录 8 个临时 JSON 残留 · 清理 + 收敛)

---

## [Unreleased] — 2026-04-18 · 跨机生产就绪

补齐上一版 ROADMAP 标记的全部"社区共创点"· **1885 → 2036 tests · 0 lint · 0 新硬依赖**。

### Added

**跨机执行（Mantle 四档补齐）**
- `runtime/mantle/ssh_mantle.py` · `SshBackend` · CLI 后端（OpenSSH）+ 可选 paramiko 后端
- `runtime/mantle/k8s_mantle.py` · `K8sBackend` · `kubectl run --rm -i` ephemeral Pod + resource overrides
- 30 + 24 tests

**跨机 HA（Hearts Coordinator）**
- `runtime/hearts/redis_coordinator.py` · `RedisCoordinator` · SET NX PX + Lua CAS + INCR fencing
- `runtime/hearts/etcd_coordinator.py` · `EtcdCoordinator` · etcd v3 原生 lease + mod_revision 作 fencing token
- 17 + 14 tests

**Camouflage 自动汰劣闭环**
- `runtime/camouflage/auto_retire.py` · `AutoRetireScheduler` · 周期/事件/强制三种触发模式
- 新事件：`VariantRetired` / `VariantBoosted` / `EvolverStepTriggered`
- 12 tests

**Skin 环境感知层（19 器官最后一块）**
- `runtime/skin/` · EnvSensor 协议 + SensorManager
- `FileWatcherSensor`（watchdog 装了走 OS · 没装降级 polling）· `GitHookSensor`（git CLI polling）· `ProcessWatchSensor`（kill -0 / OpenProcess 跨平台）
- 新事件：`FileChanged` / `DirectoryChanged` / `GitCommitDetected` / `ProcessStateChanged` / `EnvironmentPing`
- 24 tests

**IM channel 扩展**
- `runtime/channels/dingtalk.py` · `DingTalkChannel` · HMAC-SHA256 加签模式 · 26 tests
- 现在 6 个 channel：Slack / WeChat / Feishu / Telegram / Discord / **DingTalk**

**文档 + 工具**
- `mkdocs.yml` · mkdocs material 配置（加 extras `[docs]`）
- `invariants-cheatsheet.md` · 30 条必背（10 LINT + 20 核心协议不变量）
- `runtime/tour.py` + CLI 子命令 `echo-agent tour` · 10 章 5 分钟 walkthrough · 每章真跑代码 + 结论
- 4 tests for tour

**pyproject extras**（全 opt-in soft-dep）
- `[mantle-ssh]` paramiko · `[mantle-k8s]` kubernetes · `[hearts-redis]` redis · `[hearts-etcd]` etcd3 · `[skin]` watchdog · `[docs]` mkdocs

### Changed
- ROADMAP / QUICKSTART / CONTRIBUTING 同步更新到"全部补齐 · 下一波留给社区" 口径
- README.md · 测试计数 1708 → 2036

---

## [0.1.0] — 2026-04-18 · MVP milestone

First coherent runtime. All MVP (TIERS.md) invariants守住；end-to-end pipeline真能跑。

### Added

**Theory / docs layer**
- `PRINCIPLES.md` — six bio-inspired design principles
- `GENOME.md` — editable DNA model
- `FITNESS.md` — fitness function design + Goodhart防御
- `GENE_LOCKS.md` — 6 lock types + 5 maturity levels
- `ARCHITECTURE.md`, `SIX_MODULES.md`, `NAMING.md`, `STANDARDS.md`
- `TIERS.md` — MVP / Core / Full 三档切分
- `INVARIANTS.md` — 139 invariants with cross-cutting index
- 14 protocol specs under `protocols/`
- `GETTING_STARTED.md` — 3-minute onboarding

**Runtime code**
- `runtime/models/` · Pydantic契约 (Source, CostEntry, ParsedIntent, TaskGraph, Trajectory, Budget, ImmuneReport, ContextPacket)
- `runtime/invariants/` · runtime装饰器 (`@enforces`, `@monotonic`, `@append_only`) + 容器 (`AppendOnlyList`, `AppendOnlyMapping`)
- `runtime/instrumentation/` · OpenTelemetry软依赖接入 + GenAI语义约定
- `runtime/genome/` · Journal (InMemory + JSONL,崩溃安全)
- `runtime/suckers/` · Registry + Skill + 5 builtins (list_cwd / read_file / count_words / hash_text / file_stats) + SkillTester (三层: golden/regression/synthesized)
- `runtime/immunity/` · TrustEngine (Tolerance + Innate)
- `runtime/beak/` · ToolExecutor (串联 Immunity + Budget + Journal + OTel)
- `runtime/mantle/` · LocalBackend (路径白名单沙箱)
- `runtime/cerebrum/` · StaticPlanner (规则) + LLMPlanner (LLM驱动)
- `runtime/ganglia/` · GraphRuntime (DAG + `{nX.key}`模板引擎)
- `runtime/hemolymph/` · ContextComposer (四桶 quota + progressive disclosure + 压缩)
- `runtime/eyes/` · ModelRouter + MockModelRouter + AnthropicModelRouter (真SDK)
- `runtime/cli.py` · `python -m runtime` demo/run + --planner + --journal-file + --show-cost

**Tooling**
- `tools/lint/invariant_check.py` · 7 static lint rules (LINT-01..10 subset)
- `pyproject.toml` / `Makefile` / `.pre-commit-config.yaml` / `.github/workflows/ci.yml`

### Invariants actively守住

- **DIG-I1** schema validation via pydantic
- **DIG-I3** reflex未绕免疫 (将由 spinal_cord 上线时验证)
- **DIG-I6** OTel span per stage
- **IMM-I1** beak.bite必前置immunity.check (静态 lint + runtime)
- **IMM-I5** 双信号攻击识别 (`ExecutionResult.is_attack_like`)
- **BDG-I1** Budget单向 (`@monotonic("tokens_spent", "up")`)
- **BDG-I2** reserve原子 (`threading.Lock`)
- **BDG-I3** reserve/commit成对 + 30s自动回收
- **EVO-I7** Evolver不改LLM权重 (静态 lint)
- **GEN-I6** genome变更必入Journal
- **SKT-I1** golden 100% 阈值
- **CC-1** "反射不绕免疫" (reflex落地时真测)
- **CC-5** append-only (`AppendOnlyList` + `AppendOnlyMapping`)
- **CC-8** 预算语义环闭合

### Benchmarks

- 200 pytest tests跑完 1.96s
- 0 lint errors
- Demo端到端 (3 steps, 10 events) 完成 < 20ms
- 真代码总量 ~7,800 lines (其中tests ~3,000)

### Known limitations (explicitly deferred to Core/Full)

- SpinalCord reflex未接入 (v0.2.0)
- Regeneration skill_forge未闭环 (v0.2.0)
- MCP Client未接入 (v0.3.0)
- 多Arm并发 + Chromatophores未实现
- CRDT / Boids / REM consolidation / KG upgrade 全部deferred

---

## [Unreleased] — Core layer · 反思外环全闭合

> 从 "MVP 能跑" 进化到 "反思 6/6 全闭环 + 9 CLI + Web UI + MCP 双模"。
> 580 tests · 0 lint · 12.2k LoC。

### Added · 反思反哺闭环（从 MVP 的"只产不用"升级为"全闭环"）

6/6 反思产出每条都有落地通路 · 可通过 `config.learn.*` 一键启用：

- **SkillForge** → 直接写 `SkillRegistry`（由 `reflect` 触发）
- **RuleExtractor** → `LLMPlanner.learn_from_journal()` · 注 "LEARNED MITIGATIONS" prompt 段
- **MemoryConsolidator** → `LLMPlanner.learn_memories_from_journal()` · 注 "CONSOLIDATED MEMORIES"
- **KGUpdater** → `LLMPlanner.learn_kg_from_journal()` + `attach_kg()` · 注 "RELATED FACTS"
- **WorkflowRewriter** → `StaticPlanner.rewrite_from_journal()` · 经 `apply_proposals_to_rules` 落地 4 种 kind
- **RecipeEvaluator** → `LLMPlanner.assess_recipe_from_journal()` · losing verdict 时注 "RECIPE SELF-ASSESSMENT" warning

每条都进入 `LLMPlanner.recipe_hash()` · 配方身份随学习变化自动更新。

### Added · 新子命令 / 新模块

- **`loop "<goal>" --config --journal --iterations N`** · 完整外环 CLI
  - 每轮先全量反思 journal，再 plan+execute · 新事件写回 journal · 下一轮接续
  - 端到端测试用 monkey-patch 证明 iter N 真看到 iter 1..N-1 的事件
- **`ui --port 8000`** · 嵌入式 FastAPI dashboard · 6 API routes · 单页 vanilla JS
- `runtime/ui/` · `create_app()` factory · 软依赖 fastapi/uvicorn
- `runtime/suckers/browser_skills.py` · `browser_get` + `browser_extract`
  · Playwright headless chromium · `cost_profile="high"`
- `runtime/mcp_client/persistent_client.py` · `PersistentStdioMCPClient`
  · 后台 asyncio 线程 + AsyncExitStack · 避免 per-call subprocess 成本

### Added · config 驱动的反思

`config.learn.*` 新增 4 条入口，全部文件不存在时静默跳过（不 crash）：

- `learn_memories_from_journal` + `learn_kg_from_journal` + `kg_max_triples`
- `rewrite_from_journal` + `rewrite_min_confidence` + `rewrite_min_severity`
- `assess_recipe_from_journal`

原 `learn_from_journal`（rules）保留。example yaml 写齐所有字段。

### Added · 知识图与配方组件

- `runtime/knowledge_graph/prompt.py` · `format_triples_for_prompt`
  · confidence 降序 + 字符预算 + 最低置信过滤
- `runtime/regeneration/workflow_applier.py` ·
  `apply_proposals_to_rules()` + `ApplyResult` · 4 种 kind 全覆盖 +
  confidence/severity 门槛 + 未知/重复/无效 skip

### Added · 测试基础设施

- `tests/test_ui.py` (11) · FastAPI TestClient
- `tests/test_browser_skills.py` (15) · 纯 fake Page · 无真 chromium 依赖
- `tests/test_llm_planner_kg_integration.py` (16)
- `tests/test_llm_planner_recipe_integration.py` (12)
- `tests/test_workflow_applier.py` (24)
- `tests/test_cli_loop.py` (8) · 含 "closure evidence" 三条强断言
- 9 条 memory-injection 集成测试追加到 `test_llm_planner_rules_integration.py`

### Changed

- `LLMPlanner` 构造签名加 `learned_memories_section` · 含 4 个可观察 counter
- `LLMPlanner.recipe_hash` 纳入 memories / kg size / max_triples 指纹
- `StaticPlanner` 新增 `apply_rewrite_proposals()` + `rewrite_from_journal()` 便捷接口
- CLI `status` 增加 `playwright` / `fastapi` 能力探针
- README / GETTING_STARTED · 9 子命令 · 反思 6/6 表

### Fixed

- StdioMCPClient 死锁场景 · per-call subprocess 重启（历史）
- `journal or InMemoryJournal()` · 空 `__len__` 导致 falsy 陷阱（历史）
- KG conflict resolution · 多值 predicate 默认白名单避免误覆盖（历史）
