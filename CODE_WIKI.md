> [!WARNING]
> 这是独立 `echo-agent` 的历史代码快照，不是当前 Echo OS 的实现说明。文中的
> `runtime/`、`agents/`、旧工作台路径不属于 OS wheel。当前边界与文档入口请看
> [docs/README.md](docs/README.md) 和
> [docs/AGENT_OS_BOUNDARY.md](docs/AGENT_OS_BOUNDARY.md)。

# Echo Agent 深度代码 Wiki

> **版本**: 0.2.0 Beta | **类型**: 仿生学自演化 Agent 操作系统 | **许可**: Apache-2.0

---

## 目录

1. [项目概述与设计哲学](#1-项目概述与设计哲学)
2. [顶层架构与数据流](#2-顶层架构与数据流)
3. [目录结构全览](#3-目录结构全览)
4. [运行时核心 (runtime/core/)](#4-运行时核心-runtimecore)
   - 4.1 [Cerebrum — 规划与 ReAct 循环](#41-cerebrum--规划与-react-循环)
   - 4.2 [Ganglia — 图执行器与 Swarm 调度](#42-ganglia--图执行器与-swarm-调度)
   - 4.3 [Hearts — 心跳 / 协调器 / HA](#43-hearts--心跳--协调器--ha)
   - 4.4 [Spinal Cord — 快速反射路由](#44-spinal-cord--快速反射路由)
   - 4.5 [Nerves — 消息总线 / 钩子系统](#45-nerves--消息总线--钩子系统)
5. [执行层 (runtime/execution/)](#5-执行层-runtimeexecution)
   - 5.1 [Arms — 腕足 / Worker Agent](#51-arms--腕足--worker-agent)
   - 5.2 [Beak — 工具执行引擎](#52-beak--工具执行引擎)
   - 5.3 [Suckers — 技能注册与发现](#53-suckers--技能注册与发现)
   - 5.4 [Swarm — 多 Agent 集群编排](#54-swarm--多-agent-集群编排)
6. [感知层 (runtime/sensing/)](#6-感知层-runtimesensing)
   - 6.1 [Eyes — 多模型路由](#61-eyes--多模型路由)
   - 6.2 [Siphon — API 网关层 (60+ 路由)](#62-siphon--api-网关层-60-路由)
   - 6.3 [Mantle — 多后端沙箱](#63-mantle--多后端沙箱)
7. [记忆层 (runtime/memory/)](#7-记忆层-runtimememory)
   - 7.1 [Genome Journal — 事件日志与轨迹](#71-genome-journal--事件日志与轨迹)
   - 7.2 [MemoryHub — 统一记忆检索](#72-memoryhub--统一记忆检索)
   - 7.3 [Knowledge Graph — 知识图谱](#73-knowledge-graph--知识图谱)
8. [安全层 (runtime/safety/)](#8-安全层-runtimesafety)
   - 8.1 [CircuitBreaker — 熔断器](#81-circuitbreaker--熔断器)
   - 8.2 [Immunity — 免疫系统](#82-immunity--免疫系统)
   - 8.3 [Regeneration — 自演化/技能锻造](#83-regeneration--自演化技能锻造)
   - 8.4 [Camouflage — 拟态 / A/B 实验](#84-camouflage--拟态--ab-实验)
   - 8.5 [Constitution — 宪章 / 伦理约束](#85-constitution--宪章--伦理约束)
9. [平台层 (runtime/platform/)](#9-平台层-runtimeplatform)
   - 9.1 [Models — 核心数据模型](#91-models--核心数据模型)
   - 9.2 [Session — 会话上下文传递](#92-session--会话上下文传递)
   - 9.3 [Workspaces — 工作区隔离](#93-workspaces--工作区隔离)
   - 9.4 [Config — 配置管理](#94-config--配置管理)
10. [协议层 (runtime/protocol/)](#10-协议层-runtimeprotocol)
   - 10.1 [JSON-RPC 2.0 Envelope](#101-json-rpc-20-envelope)
   - 10.2 [Item 状态模型 (Turn / Item 体系)](#102-item-状态模型-turn--item-体系)
11. [适配器层 (runtime/adapters/)](#11-适配器层-runtimeadapters)
12. [前端架构 (frontend/)](#12-前端架构-frontend)
13. [Agent 定义与技能系统 (agents/ + skills/)](#13-agent-定义与技能系统-agents--skills)
14. [完整数据流追踪](#14-完整数据流追踪)
15. [关键类与接口速查表](#15-关键类与接口速查表)
16. [运行与部署](#16-运行与部署)
17. [扩展与开发指南](#17-扩展与开发指南)

---

## 1. 项目概述与设计哲学

### 1.1 核心理念

Echo Agent 是一个**仿生学自演化 Agent 操作系统**。受章鱼神经系统启发，将经典 Agent 架构映射为生物器官：

```
章鱼生物原理                                   工程实现
═══════════════════════════════════════════════════════════════════
中枢脑 (Cerebrum)   ─ 集中规划、路由仲裁     → ReAct 循环 / LLM Planner
脊髓 (Spinal Cord)   ─ 不经大脑的快速反射     → 规则引擎 / 缓存命中旁路
神经节 (Ganglia)     ─ 腕足自带小脑           → TaskGraph 分布式执行器
腕足 (Arms)          ─ 8条独立运动腕           → Worker Agent 实例
吸盘 (Suckers)       ─ 每个吸盘独立控制        → SKILL.md 技能单元
角质喙 (Beak)        ─ 唯一硬质工具            → 工具调用执行引擎
外套膜 (Mantle)      ─ 保护性边界              → 沙箱安全边界
漏斗 (Siphon)        ─ 喷射推进 / 吞吐         → I/O 流水线 (HTTP/SSE/WS)
眼睛 (Eyes)          ─ 高度发达视觉            → 多模态模型路由
皮肤 (Skin)          ─ 感知环境变化            → 纯感知信号上报层
神经 (Nerves)        ─ 分布式消息传递          → 事件总线 / 钩子系统
色素细胞 (Chromatophores) ─ 状态展示           → 状态广播 / 并行效应器
墨囊 (Ink Sac)       ─ 逃生机制                → 熔断 / 预算超限停止
免疫 (Immunity)      ─ 识别异己                → 身份验证 / 攻击记忆
心脏 (Hearts)        ─ 三心脏/双循环           → HA 调度 / 心跳节律
基因组 (Genome)      ─ 可遗传编码              → 可编辑遗传密码 / 长时记忆
血淋巴 (Hemolymph)   ─ 开放式循环              → 每轮上下文流
拟态 (Camouflage)    ─ 变色伪装                → 策略切换 / A/B 实验
再生 (Regeneration)  ─ 断腕再生                → 反思 / 自进化 / 技能锻造
```

### 1.2 设计原则

- **去中心智能**: 中枢只做规划与仲裁，执行智能下沉到腕 (Ganglia + Arms)
- **器官可替换**: 每个模块职责单一，通过 Protocol 接口解耦
- **自适应进化**: 再生 (Regeneration)、拟态 (Camouflage)、喷墨逃命 (Ink)
- **双路径决策**: 快路径 (Spinal Cord 规则/缓存) vs 慢路径 (Cerebrum LLM 推理)

---

## 2. 顶层架构与数据流

### 2.1 系统拓扑

```
                        ┌─────────────────────────────────┐
                        │        Frontend (Next.js)        │
                        │   React + Tailwind + Shadcn UI   │
                        └──────────────┬──────────────────┘
                                       │ WebSocket (JSON-RPC 2.0)
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Siphon (API Gateway Layer)                        │
│                                                                      │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐   │
│  │ Realtime Gateway │  │ OpenAI Compat API│  │  REST Routers×60+ │   │
│  │ (JSON-RPC/WS)    │  │ /v1/chat/compl.  │  │  (agents/skills…) │   │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬──────────┘   │
│           │                     │                      │              │
└───────────┼─────────────────────┼──────────────────────┼──────────────┘
            │                     │                      │
            ▼                     ▼                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   Spinal Cord (Reflex Router)                         │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ looks_like_tool_intent() / CHITCHAT_RE / TOOL_INTENT_RE       │    │
│  │ → 快路径: 直接返回 local_non_tool_reply() 或缓存命中          │    │
│  │ → 慢路径: 转发至 Cerebrum                                     │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
            │
            ▼ (慢路径)
┌──────────────────────────────────────────────────────────────────────┐
│                         Cerebrum (Planner)                            │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ stream_react_loop()                                           │    │
│  │   ├─ Step 1: _build_system_prompt() → prompt 组装             │    │
│  │   ├─ Step 2: _prepare_history() → 记忆注入                    │    │
│  │   ├─ Step 3: Eyes LLM call → 流式响应                         │    │
│  │   ├─ Step 4: react_parsing → XML/JSON/fence 解析              │    │
│  │   ├─ Step 5: react_execution → 工具调用分发                   │    │
│  │   └─ Step 6: react_guards → 收敛检查 / 循环上限               │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────────┐
│               Ganglia → Beak → Suckers (Tool Execution)               │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ Ganglia._dispatch() → Beak.execute() → Sucker handler()       │    │
│  │   ├─ Budget.reserve() → 预算检查 (Ink)                        │    │
│  │   ├─ Immunity.guard() → 免疫检查 (tool/path/url guard)        │    │
│  │   ├─ hooks.run_pre() → 前置钩子                               │    │
│  │   ├─ skill.handler(**args) → 实际技能执行                     │    │
│  │   │    └─ Mantle sandbox → 沙箱隔离执行                       │    │
│  │   ├─ hooks.run_post() → 后置钩子                              │    │
│  │   └─ Journal.write_step() → 事件持久化                        │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 一次完整 Turn 的生命周期

```
Client send (turn/start)
  │
  ├─ 1. Siphon RpcConnection 接收 JSON-RPC Request
  ├─ 2. spinal_cord.realtime_turn_routing 判断路由
  │     ├─ looks_like_tool_intent(goal) → True: 进入 Cerebrum
  │     └─ looks_like_tool_intent(goal) → False: 本地回复
  ├─ 3. session_scope(Session(...)) 建立会话上下文
  ├─ 4. WorkspaceManager.allocate(thread_id) 创建隔离工作区
  ├─ 5. Cerebrum.stream_react_loop() 启动主循环
  │     ├─ Eyes 路由选择合适的 LLM provider
  │     ├─ 构建 system + user + memory 上下文
  │     ├─ while not converged:
  │     │   ├─ LLM 调用 → parse response → extract tool_calls
  │     │   ├─ Beak.execute() → 工具执行
  │     │   ├─ emit item/* deltas → WS push to client
  │     │   └─ budget.check() / circuit_breaker.record()
  │     └─ yield final Turn result
  ├─ 6. Journal.write_trajectory() 持久化轨迹
  └─ 7. Client receives turn/completed
```

### 2.3 关键 ContextVar 上下文传递

```python
# 通过 ContextVar 实现跨异步任务/线程的隐式上下文传递
_current_session: ContextVar[Session | None]    # 主 Session
_current_agent_id: ContextVar[str | None]       # 当前 Agent ID (legacy)
_current_actor: ContextVar[str | None]          # 当前调用者 (legacy)
```

---

## 3. 目录结构全览

```
echo-agent/
│
├── runtime/                          # ★ Python Agent OS 运行时 (核心)
│   ├── __init__.py
│   ├── cli.py                        # CLI 入口 (python -m runtime)
│   │
│   ├── core/                         # 核心决策与调度
│   │   ├── cerebrum/                 # 规划器
│   │   │   ├── react_loop.py         # 主 ReAct 循环 (stream_react_loop)
│   │   │   ├── react_execution.py    # 工具调用执行
│   │   │   ├── react_parsing.py      # LLM 响应解析 (XML/JSON/fence)
│   │   │   ├── react_context.py      # 上下文组装 (system/user/memory)
│   │   │   ├── react_guards.py       # 循环守卫 (收敛/上限)
│   │   │   ├── react_types.py        # State / Config / 事件类型
│   │   │   ├── llm_planner.py        # LLM 规划器
│   │   │   ├── pause_control.py      # 暂停/恢复机制
│   │   │   ├── todo_protocol.py      # Todo 协议 (目标模式)
│   │   │   └── thinking_mode.py      # 思考模式切换
│   │   ├── ganglia/                  # 图执行器
│   │   │   └── runtime.py            # TaskGraph → Arm 调度
│   │   ├── hearts/                   # 心跳 / HA 协调
│   │   │   ├── hearts.py             # Hearts 管理器
│   │   │   └── coordinator.py        # Leader 选举 (Redis/etcd)
│   │   ├── spinal_cord/              # 快速反射路由
│   │   │   └── reflex_router.py      # 规则引擎 / 缓存命中
│   │   └── nerves/                   # 消息总线
│   │       ├── bus.py                # 事件总线
│   │       └── hooks.py              # 前后置钩子系统
│   │
│   ├── execution/                    # 执行层
│   │   ├── arms/                     # Worker Agent
│   │   │   ├── base.py               # Worker 基类
│   │   │   └── pool.py               # Arm 池 (负载均衡)
│   │   ├── beak/                     # 工具执行器
│   │   │   └── executor.py           # BeakExecutor: 统一工具调度
│   │   ├── suckers/                  # 技能注册与发现
│   │   │   ├── registry.py           # SkillRegistry
│   │   │   ├── loader.py             # SKILL.md 加载
│   │   │   ├── skills_builtin.py     # 内置技能实现
│   │   │   ├── skills_web.py         # Web 技能
│   │   │   ├── skills_files.py       # 文件操作技能
│   │   │   └── skills_code.py        # 代码编辑技能
│   │   ├── swarm/                    # 多 Agent 集群
│   │   │   └── runtime.py            # 分层并行调度
│   │   └── agents/                   # Agent 实例管理
│   │       ├── base.py               # Agent 基类
│   │       ├── resolver.py           # Agent 解析
│   │       └── loader.py             # Agent 加载
│   │
│   ├── sensing/                      # 感知层
│   │   ├── eyes/                     # 多模型路由
│   │   │   ├── models.py             # 模型原语 / Provider 接口
│   │   │   ├── multi_router.py       # 多模型路由器
│   │   │   ├── anthropic_provider.py # Anthropic provider
│   │   │   ├── openai_provider.py    # OpenAI provider
│   │   │   └── stream.py             # 流式事件处理
│   │   ├── siphon/                   # API 网关 (60+ 路由)
│   │   │   ├── realtime_gateway.py   # ★ JSON-RPC WebSocket 网关
│   │   │   ├── realtime_turn_routing.py # 意图检测 / 路由分流
│   │   │   ├── openai_gateway_router.py # OpenAI 兼容 API
│   │   │   ├── thread_state_router.py   # 线程管理
│   │   │   ├── agents_router.py         # Agent CRUD
│   │   │   ├── skills_router.py         # 技能管理
│   │   │   ├── ... (50+ 其他路由)
│   │   │   └── __init__.py
│   │   ├── mantle/                   # 沙箱
│   │   │   ├── local.py              # 本地进程沙箱
│   │   │   ├── docker_mantle.py      # Docker 沙箱
│   │   │   ├── ssh_mantle.py         # SSH 远程沙箱
│   │   │   ├── k8s_mantle.py         # Kubernetes Pod 沙箱
│   │   │   ├── subprocess_mantle.py  # 子进程沙箱
│   │   │   └── _streaming.py         # 流式输出处理
│   │   └── skin/                     # 文件监控 / 环境感知
│   │
│   ├── memory/                       # 记忆层
│   │   ├── hub.py                    # ★ MemoryHub: 统一检索入口
│   │   ├── genome/                   # 基因组 (Journal)
│   │   │   ├── journal.py            # JournalEvent / Journal 类
│   │   │   └── journal_context.py    # Journal 上下文
│   │   ├── knowledge_graph/          # 知识图谱
│   │   ├── threads/                  # 对话线程
│   │   ├── user_store.py             # 用户记忆存储
│   │   └── scope_paths.py            # 作用域路径
│   │
│   ├── safety/                       # 安全层
│   │   ├── immunity/                 # 免疫系统
│   │   │   ├── trust_engine.py       # 信任引擎
│   │   │   ├── tool_guard.py         # 工具守卫
│   │   │   ├── path_guard.py         # 路径保护
│   │   │   └── url_guard.py          # URL 防护
│   │   ├── ink/                      # 熔断 / 预算
│   │   │   ├── breaker.py            # ★ CircuitBreaker (三态熔断)
│   │   │   └── budget.py             # Token/USD 预算
│   │   ├── regeneration/             # 自演化 (20 个文件)
│   │   │   ├── skill_forge.py        # ★ SkillForge: 技能锻造
│   │   │   ├── rule_extractor.py     # 规则提取
│   │   │   ├── kg_updater.py         # 知识图谱更新
│   │   │   ├── workflow_rewriter.py  # 工作流重写
│   │   │   ├── camouflage.py         # 拟态 / 策略选择
│   │   │   ├── variant_evaluator.py  # 变体评估
│   │   │   ├── gepa_*.py             # GEPA (遗传-表观-种群) 模块
│   │   │   └── ...
│   │   ├── constitution/             # 宪章 / 伦理
│   │   ├── invariants/               # 不变量约束
│   │   ├── hooks/                    # 社区钩子
│   │   │   └── runner.py             # dispatch_pre_tool / dispatch_notification
│   │   └── approval_gate.py          # 人工审批门
│   │
│   ├── platform/                     # 平台层
│   │   ├── config/                   # 配置
│   │   │   ├── schema.py             # Pydantic 配置模型
│   │   │   ├── loader.py             # YAML/ENV 加载
│   │   │   └── builder.py            # 配置构建
│   │   ├── models/                   # ★ 核心数据模型
│   │   │   ├── primitives.py         # TaskId, ArmId, CostEntry, Source
│   │   │   ├── pipeline.py           # ParsedIntent, TaskGraph, RouteDecision
│   │   │   ├── execution.py          # Step, Trajectory, ExecutionResult
│   │   │   ├── context.py            # ContextPacket, QuotaAllocation
│   │   │   └── governance.py         # Budget, ImmuneVerdict, RiskScore
│   │   ├── session.py               # ★ Session 上下文传递
│   │   ├── workspaces.py            # ★ WorkspaceManager 工作区隔离
│   │   ├── ui.py                    # FastAPI 应用工厂
│   │   ├── scope.py                 # 写入作用域解析
│   │   ├── paths.py                 # 项目路径工具
│   │   └── auth.py                  # 认证
│   │
│   ├── protocol/                     # 协议层
│   │   ├── envelope.py              # ★ JSON-RPC 2.0 消息封装
│   │   └── items.py                 # ★ Item/Turn 状态模型
│   │
│   └── adapters/                     # 适配器层
│       ├── __init__.py
│       ├── instrumentation.py        # OpenTelemetry 追踪
│       ├── scheduler.py              # BackgroundRunner
│       └── ...
│
├── frontend/                         # ★ TypeScript 前端 (Next.js 14)
│   ├── src/
│   │   ├── app/                      # Next.js App Router 页面
│   │   │   ├── page.tsx              # 首页 (重定向到 /workspace)
│   │   │   ├── realtime/             # 实时对话路由
│   │   │   │   ├── page.tsx          # 无 thread 入口
│   │   │   │   └── [thread_id]/page.tsx  # 特定 thread
│   │   │   ├── workspace/            # 工作区
│   │   │   │   ├── layout.tsx        # 布局 (侧边栏 + 顶栏)
│   │   │   │   ├── chats/            # 聊天视图
│   │   │   │   ├── code/             # 代码视图
│   │   │   │   ├── team/             # 团队协作
│   │   │   │   ├── evolution/        # 演化面板
│   │   │   │   ├── reflex/           # 反射/GEPA 面板
│   │   │   │   ├── agents/           # Agent 管理
│   │   │   │   ├── skills/           # 技能浏览
│   │   │   │   ├── knowledge/        # 知识图谱
│   │   │   │   ├── swarm/            # Swarm 集群
│   │   │   │   ├── workflows/        # 工作流编辑器
│   │   │   │   ├── observability/    # 可观测性
│   │   │   │   ├── diagnostics/      # 诊断
│   │   │   │   └── settings/         # 设置
│   │   │   ├── login/                # 登录
│   │   │   ├── register/             # 注册
│   │   │   └── about/                # 关于
│   │   ├── components/               # 共享组件
│   │   └── core/                     # 前端核心逻辑
│   │       ├── page-agent-bridge.ts  # ★ PageAgent 桥接 (前端内部 Agent)
│   │       └── clipboard.ts
│   ├── electron/                     # Electron 桌面应用
│   ├── package.json
│   └── next.config.ts
│
├── agents/                           # 预置 Agent 定义
│   └── <agent-name>/
│       ├── profile.jsonc             # Agent 配置
│       ├── agent-core/
│       │   ├── SOUL.md               # 系统提示 (灵魂)
│       │   └── USER.md               # 用户提示
│       └── avatar.svg                # 头像
│
├── skills/                           # 预置技能库
│   └── <skill-name>/
│       └── SKILL.md                  # 技能定义 (YAML frontmatter + body)
│
├── protocols/                        # 协议定义
├── prompts/                          # Prompt 模板
├── config.example.yaml               # 配置示例
├── .env.example                      # 环境变量示例
├── pyproject.toml                    # Python 项目元数据
├── Dockerfile
├── docker-compose.yml
├── deploy/                           # 部署配置
│   ├── k8s/                          # Kubernetes
│   │   ├── namespace.yaml
│   │   ├── configmap.yaml
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── ingress.yaml
│   ├── prometheus.yml
│   └── grafana-datasources.yml
├── docs/                             # 文档
├── tests/                            # Python 测试
├── benchmarks/                       # 基准测试
└── demos/                            # 演示脚本
```

---

## 4. 运行时核心 (runtime/core/)

### 4.1 Cerebrum — 规划与 ReAct 循环

**位置**: [runtime/core/cerebrum/](file:///f:/新建文件夹/echo-agent/runtime/core/cerebrum/)

#### 4.1.1 核心类与类型

```python
# react_loop.py / react_types.py —— ReAct 循环参数与 recipes

# react_loop.py:263 函数签名默认值
async def react_loop(
    ...,
    max_iterations: int = 30,           # 默认 30 轮(per-mode 自动提升)
    ...,
):
    # research/swarm 模式自动提到 100 (react_loop.py:368-388)
    # goal 模式自动提到 _GOAL_MODE_MAX_ITER=10000 (react_loop.py:396-397)

# react_types.py 内置 recipes
_DEFAULT_REACT_RECIPES = [
    ReActRecipe(name="conservative", max_iterations=18, temperature=0.1),
    ReActRecipe(name="balanced",     max_iterations=30, temperature=0.3),
    ReActRecipe(name="aggressive",   max_iterations=45, temperature=0.5),
]

@dataclass
class ReactState:
    """ReAct 循环运行时状态"""
    iteration: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    converged: bool = False
    reason: str = ""
    items: list[Item]                   # 累积的 Item
    todo: list[TodoEntry]              # Todo 条目
    tool_call_count: int = 0
    user_messages_since_last_tool: int = 0
    last_tool_result: Any = None
```

#### 4.1.2 `stream_react_loop()` — 主循环

[react_loop.py](file:///f:/新建文件夹/echo-agent/runtime/core/cerebrum/react_loop.py) 中的核心异步生成器：

```python
async def stream_react_loop(
    goal: str,                          # 用户目标
    *,
    config: ReactConfig,
    model_router: EyesRouter,           # 模型路由
    skill_registry: SkillRegistry,      # 技能注册表
    beak: BeakExecutor,                 # 工具执行器
    journal: Journal | None,            # 事件日志
    session: Session | None,            # 会话上下文
    emitter: EventEmitter | None,       # 事件发射器 (WS)
    initial_system_prompt: str | None,
    initial_plan: ParsedIntent | None,
    pause_callback: Callable | None,    # 暂停回调
) -> AsyncGenerator[ReactYield, None]:
```

**执行流程**:

```
Step 1: _build_system_prompt()
  ├─ 加载 Agent SOUL.md
  ├─ 注入技能列表 (skill descriptions)
  ├─ 注入 MEMORY.md 内容 (MemoryHub)
  ├─ 注入 Journal 历史
  ├─ 注入 Constitution 规则
  └─ 应用 Todo 协议 (目标模式)

Step 2: _prepare_history()
  ├─ 从 threads 模块加载历史对话
  ├─ 压缩超长历史 (滑动窗口)
  └─ 注入 Hemolymph 上下文

Step 3: while not converged:
  ├─ 3a. Eyes.send_message() → LLM 调用
  │     ├─ 选择 provider (Anthropic/OpenAI/Gemini/local)
  │     ├─ 发送 messages
  │     └─ 流式接收响应 (yield delta items)
  │
  ├─ 3b. react_parsing.parse_response()
  │     ├─ 提取 <think> 块
  │     ├─ 提取 <tool_call> XML 或 ```json 代码块
  │     ├─ 解析 tool_name + tool_args
  │     └─ 检测 stop_reason (end_turn / max_tokens / tool_use)
  │
  ├─ 3c. emit ReasoningItem (思考过程)
  ├─ 3d. emit AgentMessageItem (文本回复)
  │
  ├─ 3e. if tool_calls present:
  │     ├─ budget.check() → 预算检查 (Ink)
  │     ├─ for each tool_call:
  │     │   ├─ immunity.guard(tool_name, args) → 免疫检查
  │     │   ├─ beak.execute(tool_name, args) → 工具执行
  │     │   │   └─ emit CommandExecutionItem / FileChangeItem
  │     │   └─ journal.write_step()
  │     └─ beak.execute() 返回观察结果 → 追加到 messages
  │
  │ 3f. if stop_reason == "end_turn":
  │     └─ converged = True
  │
  │ 3g. react_guards.check(state)
  │     ├─ iteration > max_iterations → 超限停止
  │     ├─ tokens > max_budget_tokens → 预算超限
  │     └─ usd > max_budget_usd → 费用超限
  │
  └─ 3h. pause_control.check()
        └─ 如需暂停 → 等待用户继续

Step 4: yield final Turn result
Step 5: journal.write_trajectory() → 持久化
```

#### 4.1.3 `react_parsing.py` — 响应解析

支持三种 LLM 响应格式：

| 格式 | 示例 | 解析器 |
|------|------|--------|
| **XML 工具调用** | `<tool_call>{"name":"read_file","args":{...}}</tool_call>` | `_parse_xml_tool_calls()` |
| **JSON fence** | ` ```json\n{"name":"read_file",...}\n``` ` | `_parse_fence_json()` |
| **纯 JSON** | `{"name":"read_file","arguments":{...}}` | `_parse_inline_json()` |

还支持 `thinking` 标签提取：
```python
# 提取  thinking 标签用于 ReasoningItem
thinking = _extract_thinking(content)  # 返回 str | None
```

#### 4.1.4 `react_execution.py` — 工具执行

```python
class ToolCall:
    id: str
    name: str          # e.g. "read_file"
    arguments: dict    # e.g. {"path": "src/main.py", "limit": 200}

async def execute_tool_calls(
    tool_calls: list[ToolCall],
    *,
    beak: BeakExecutor,
    budget: Budget,
    immunity: ImmunityEngine,
    hooks: HookRunner | None,
    journal: Journal,
    emitter: EventEmitter,
    session: Session,
) -> list[ToolResult]:
```

#### 4.1.5 `llm_planner.py` — 计划先行模式

当 `planning_mode=True` 时，先调用 LLM 生成计划，再执行：

```python
class LLMPlanner:
    async def plan(goal: str, ...) -> ParsedIntent:
        """生成结构化计划 (TaskGraph)"""
    
    async def refine(plan: ParsedIntent, feedback: str) -> ParsedIntent:
        """根据用户反馈修改计划"""
```

#### 4.1.6 `todo_protocol.py` — 目标模式

```python
class TodoProtocol:
    """多步骤目标的 Todo 追踪"""
    todos: list[TodoEntry]
    
    def mark_in_progress(self, todo_id: str): ...
    def mark_completed(self, todo_id: str): ...
    def add(self, title: str): ...
    def is_all_completed(self) -> bool: ...
```

#### 4.1.7 `pause_control.py` — 暂停控制

```python
@dataclass
class PauseControl:
    pause_on_tool: bool
    pause_after_n_tools: int | None
    require_approval_for: set[str]     # 需审批的工具
    
    async def check_and_wait(
        self, tool_name: str, args: dict,
        approval_manager: ApprovalManager,
    ) -> bool:  # True = continue, False = cancelled
```

---

### 4.2 Ganglia — 图执行器与 Swarm 调度

**位置**: [runtime/core/ganglia/runtime.py](file:///f:/新建文件夹/echo-agent/runtime/core/ganglia/runtime.py)

```python
class GangliaRuntime:
    """将 TaskGraph 分发到 Arm 池执行"""
    
    def __init__(
        self,
        arm_pool: ArmPool,
        beak_factory: Callable[[], BeakExecutor],
        journal: Journal | None,
    ): ...
    
    async def execute(self, graph: TaskGraph) -> ExecutionResult:
        """按拓扑排序执行 TaskGraph"""
```

TaskGraph 执行支持三种策略：
- **sequential**: 串行执行每个节点
- **per_node**: 每个节点独立 Arm
- **topo_layers**: 按拓扑层并行（无依赖节点可并行）

---

### 4.3 Hearts — 心跳 / 协调器 / HA

**位置**: [runtime/core/hearts/](file:///f:/新建文件夹/echo-agent/runtime/core/hearts/)

```python
class Hearts(AbstractContextManager["Hearts"]):
    """双循环心脏: systemic (后台任务) + branchial (熔断器频道)"""
    
    systemic: BackgroundRunner        # 后台周期性任务
    branchial: dict[str, CircuitBreaker]  # 熔断器频道
    coordinator: Coordinator | None   # Leader 选举

    def start(self) -> None: ...
    def stop(self, timeout: float) -> None: ...
    def snapshot(self) -> HeartsSnapshot: ...
    def healthy(self) -> bool: ...
    def acquire_leadership(self, scope: str, ttl: float) -> LeaderGuard: ...
    def is_leader(self, scope: str) -> bool: ...
```

**Leader 选举** (coordinator.py):
- 后端可选: Redis (`hearts-redis`) / etcd (`hearts-etcd`)
- 无 coordinator 时退化为 `_AlwaysLeaderGuard`（单机模式始终是 leader）

---

### 4.4 Spinal Cord — 快速反射路由

**位置**: [runtime/core/spinal_cord/reflex_router.py](file:///f:/新建文件夹/echo-agent/runtime/core/spinal_cord/reflex_router.py)

三层意图检测正则：

```python
# 1. 闲聊检测
_CHITCHAT_RE: 问候/感谢/再见等 → 本地回复，不消耗 LLM

# 2. 知识问答检测
_KNOWLEDGE_QA_RE: "是什么" "怎么样" 等 → 若无文件信号，可能本地回复

# 3. 工具意图检测
_TOOL_INTENT_RE: 搜索/编辑/运行/文件操作等 → 进入工具执行模式
```

关键函数：

```python
def looks_like_tool_intent(goal: str) -> bool:
    """判断一个 turn 是否需要工具能力"""
    # 闲聊 → False
    # 纯知识问答(无文件信号) → False
    # 工具意图匹配 → True

def local_non_tool_reply(goal: str) -> str | None:
    """无 LLM 时的最后兜底回复"""
```

---

### 4.5 Nerves — 消息总线 / 钩子系统

**位置**: [runtime/core/nerves/hooks.py](file:///f:/新建文件夹/echo-agent/runtime/core/nerves/hooks.py)

```python
@dataclass
class HookContext:
    phase: Literal["pre", "post"]       # 前置/后置
    task_id: TaskId
    arm_id: ArmId
    sucker_id: SkillId
    node_id: str
    step_id: StepId
    call: ToolCall
    result: ExecutionResult | None

class HookRunner:
    """工具调用前后置钩子"""
    def run_pre(self, ctx: HookContext) -> HookResult | None: ...
    def run_post(self, ctx: HookContext) -> None: ...

class HookError(Exception):
    """钩子拒绝执行"""
    reason: str
```

---

## 5. 执行层 (runtime/execution/)

### 5.1 Arms — 腕足 / Worker Agent

**位置**: [runtime/execution/arms/base.py](file:///f:/新建文件夹/echo-agent/runtime/execution/arms/base.py)

```python
class Worker:
    """一个 Arm 实例 = 一个可以独立工作的 Agent 执行单元"""
    arm_id: ArmId
    agent: Agent
    skills: SkillRegistry
    mantle: MantleProvider

    async def execute(
        self, node: TaskNode, context: ContextPacket
    ) -> ArmResult: ...

class ArmPool:
    """Arm 池: 管理多个 Worker, 按亲和度分配任务"""
    def pick_for(self, assignment: ArmAssignment) -> Worker | None: ...
    def register(self, worker: Worker) -> None: ...
```

### 5.2 Beak — 工具执行引擎

**位置**: [runtime/execution/beak/executor.py](file:///f:/新建文件夹/echo-agent/runtime/execution/beak/executor.py)

```python
class BeakExecutor:
    """统一的工具调用执行引擎"""
    
    def __init__(
        self,
        registry: SkillRegistry,
        budget: Budget | None,
        immunity: ImmunityEngine | None,
        hooks: HookRunner | None,
        journal: Journal | None,
        mantle: MantleProvider | None,
    ): ...

    async def execute(
        self,
        sucker_id: SkillId,      # 技能 ID
        args: dict,               # 参数
        *,
        task_id: TaskId,
        arm_id: ArmId,
        node_id: str,
        step_id: StepId,
        actor: str | None,
        caller: str,
    ) -> Step:
```

**执行管线** (每个工具调用经过以下检查链):

```
1. SkillRegistry.lookup(sucker_id)      → 查找技能定义
2. Budget.reserve(estimated_cost)       → 预算预留
3. Immunity.guard_tool(sucker_id, args) → 工具安全检查
4. Safety hooks: dispatch_pre_tool()    → 社区前置钩子
5. Skill.handler(**args)                → 实际执行
   ├─ Session 注入 (skill 声明 session 参数时)
   ├─ Sandbox 注入 (skill 声明 sandbox_dir 时)
   └─ Path resolution (root/cwd/path 参数自动映射到工作区)
6. Budget.commit(reservation, actual)   → 预算确认
7. Safety hooks: dispatch_post_tool()   → 社区后置钩子
8. Journal.write_step()                 → 记录到日志
9. CircuitBreaker.record()              → 熔断器记录
```

### 5.3 Suckers — 技能注册与发现

**位置**: [runtime/execution/suckers/](file:///f:/新建文件夹/echo-agent/runtime/execution/suckers/)

```python
class Skill(BaseModel):
    """技能定义"""
    sucker_id: SkillId
    name: str
    description: str
    parameters: list[SkillParameter]
    handler: Callable              # Python 函数
    affinity: list[str] | None     # ["file", "web", "code", ...]
    test_cases: list[SkillTestCase]
    dangerous: bool = False

class SkillRegistry:
    """技能注册表"""
    def register(self, skill: Skill) -> None: ...
    def lookup(self, sucker_id: SkillId) -> Skill: ...
    def list_all(self) -> list[Skill]: ...
    def search(self, query: str) -> list[Skill]: ...
    def load_skills_from_dir(self, path: Path) -> int: ...
```

**SKILL.md 格式** (前端 YAML + 实现描述):
```markdown
---
name: read_file
description: 读取文件内容
parameters:
  - name: path
    type: string
    description: 文件路径
  - name: limit
    type: integer
    description: 读取行数限制
    default: 200
---
读取指定文件的内容...
```

**内置技能列表**:
| 技能 | 文件 | 描述 |
|------|------|------|
| `list_cwd` | skills_files.py | 列出目录内容 |
| `read_file` | skills_files.py | 读取文件 |
| `write_text_file` | skills_files.py | 写入文本文件 |
| `edit_code` | skills_code.py | 代码编辑 (search/replace) |
| `run_command` | skills_builtin.py | 执行 shell 命令 |
| `web_search` | skills_web.py | 网络搜索 |
| `web_fetch` | skills_web.py | 获取网页内容 |
| `browser_navigate` | skills_builtin.py | 浏览器导航 |
| `todo_write` | skills_builtin.py | Todo 列表管理 |

### 5.4 Swarm — 多 Agent 集群编排

**位置**: [runtime/execution/swarm/runtime.py](file:///f:/新建文件夹/echo-agent/runtime/execution/swarm/runtime.py)

```python
@dataclass
class SwarmResult:
    task_id: TaskId
    arm_results: list[ArmResult]         # 所有 Arm 的执行结果
    parallelism_achieved: int            # 实际并行度
    total_wall_ms: float
    total_cost_usd: float
    all_successful: bool
    plan: SwarmPlan
    events: list[SwarmEvent]
    handoffs: list[AgentHandoff]
    phase_reports: list[SwarmPhaseReport]

class SwarmRuntime:
    """按拓扑分层并行调度 TaskGraph"""
    
    # 三种拆分策略
    SplitStrategy = Literal["per_node", "single", "topo_layers"]
    
    async def run(self, graph: TaskGraph, budget: Budget) -> SwarmResult:
        """主入口: 拆分→准备→分阶段执行"""
        # 1. split → 按策略拆分节点到层
        # 2. prepare → 每层内分配 Arm + 生成 WorkContract
        # 3. dispatch → 逐层并行执行
        # 4. aggregate → 收集结果
```

---

## 6. 感知层 (runtime/sensing/)

### 6.1 Eyes — 多模型路由

**位置**: [runtime/sensing/eyes/](file:///f:/新建文件夹/echo-agent/runtime/sensing/eyes/)

```python
# models.py
class ModelProvider(Protocol):
    """模型供应商接口"""
    async def send_message(
        self, messages: list[dict], *, stream: bool, **kwargs
    ) -> AsyncGenerator[str, None]: ...

class ModelInfo(BaseModel):
    provider: str              # "anthropic" / "openai" / "gemini"
    model_id: str              # "claude-3-5-sonnet-20241022"
    max_tokens: int
    cost_per_1k_in: float
    cost_per_1k_out: float

# multi_router.py
class MultiModelRouter:
    """多模型路由器: 按策略选择最优模型"""
    providers: dict[str, ModelProvider]
    
    def register(self, provider_id: str, provider: ModelProvider): ...
    
    async def route(
        self, messages: list[dict], *,
        preferred_model: str | None,
        strategy: str = "cost_optimized",  # cost_optimized | best_quality | fallback
    ) -> AsyncGenerator[StreamEvent, None]: ...
```

**支持的 Provider**:
- `anthropic_provider.py` — Anthropic Claude
- `openai_provider.py` — OpenAI GPT
- 本地模型 (可扩展)

### 6.2 Siphon — API 网关层 (60+ 路由)

**位置**: [runtime/sensing/siphon/](file:///f:/新建文件夹/echo-agent/runtime/sensing/siphon/)

#### 6.2.1 Realtime Gateway (核心)

[realtime_gateway.py](file:///f:/新建文件夹/echo-agent/runtime/sensing/siphon/realtime_gateway.py) 实现了 **JSON-RPC 2.0 over WebSocket** 的生产级传输层:

```python
class RealtimeRuntime(Protocol):
    """Turn 循环实现的契约接口"""
    async def start_turn(
        self, params: dict[str, Any], emitter: EventEmitter
    ) -> Turn: ...
    
    async def handle_request(
        self, method: str, params: dict, emitter: EventEmitter
    ) -> Any: ...

class RpcConnection:
    """每个 WebSocket 连接一个实例"""
    ws: WebSocket
    approval_manager: ApprovalManager   # 请求→响应 Future 配对
    
    async def notify(method, params): ...      # 单向推送
    async def request_approval(method, params): # 等待客户端响应
    def is_turn_interrupted(turn_id) -> bool:   # 协作式取消信号
    def register_turn(turn_id): ...             # 注册当前 turn

class ApprovalManager:
    """服务器→客户端请求管理 (per-connection)"""
    async def open() -> tuple[int, Future]:     # 保留 request id
    async def resolve(req_id, response): ...    # Future.set_result
    async def cancel_all(reason): ...           # WS 断开时清理
```

**RPC 方法体系**:

| Method | 方向 | 说明 |
|--------|------|------|
| `turn/start` | Client→Server | 发起新 turn |
| `turn/interrupt` | Client→Server | 中断当前 turn |
| `turn/steer` | Client→Server | 运行中注入指令 |
| `item/started` | Server→Client | Item 开始 |
| `item/agentMessage/delta` | Server→Client | 文本流式增量 |
| `item/commandExecution/outputDelta` | Server→Client | 命令输出增量 |
| `item/completed` | Server→Client | Item 完成 |
| `turn/completed` | Server→Client | Turn 完成 |
| `approval/request` | Server→Client | 请求审批 |
| `approval/response` | Client→Server | 审批回复 |

#### 6.2.2 路由一览

| 路由文件 | 功能 |
|---------|------|
| `realtime_gateway.py` | ★ JSON-RPC WebSocket 网关 |
| `realtime_turn_routing.py` | 意图检测与路由分流 |
| `openai_gateway_router.py` | OpenAI 兼容 `/v1/chat/completions` |
| `thread_state_router.py` | 线程 CRUD |
| `agents_router.py` | Agent 管理 |
| `agent_world_router.py` | Agent 世界/市场 |
| `skills_router.py` | 技能管理 |
| `skill_market_router.py` | 技能市场 |
| `subagents_router.py` | 子 Agent 管理 |
| `parallel_agents_router.py` | 并行 Agent |
| `team_rooms_router.py` | 团队房间 |
| `deep_research_router.py` | 深度研究 |
| `evolution_router.py` | 演化接口 |
| `evolution_ops_router.py` | 演化操作 |
| `journal_router.py` | Journal 查询 |
| `streaming_journal.py` | Journal 流 |
| `memory_router.py` | 记忆管理 |
| `wiki_router.py` | Wiki 生成 |
| `wiki_generic.py` | 通用 Wiki |
| `prompts_router.py` | Prompt 管理 |
| `fs_router.py` | 文件系统操作 |
| `workspaces_router.py` | 工作区管理 |
| `terminal_router.py` | 终端 |
| `lsp_router.py` | LSP 集成 |
| `browser_router.py` | 浏览器 |
| `computer_router.py` | 计算机操作 |
| `mcp_router.py` | MCP 协议 |
| `dag_debugger_router.py` | DAG 调试 |
| `observability_router.py` | 可观测性 |
| `metrics_router.py` | 指标 |
| `cron_router.py` | 定时任务 |
| `deployments_router.py` | 部署管理 |
| `system_router.py` | 系统信息 |
| `debug_router.py` | 调试 |
| `invariants_router.py` | 不变量检查 |
| `channels_router.py` | 频道管理 |
| `ambient_suggestions_router.py` | 环境建议 |
| `intelligence_router.py` | 情报分析 |
| `organizations_router.py` | 组织管理 |
| `account_usage_router.py` | 账户用量 |
| `config_router.py` | 配置管理 |
| `verify_router.py` | 验证 |
| `completion_router.py` | 自动补全 |
| `index_router.py` | 索引 |
| `workflow_editor_router.py` | 工作流编辑器 |
| `remote_transport.py` | 远程传输 |
| `remote_backends_router.py` | 远程后端 |
| `cocoloop_router.py` | CoCoLoop |
| `stub_router.py` | Stub/占位 |
| `meta_router.py` | 元数据 |

### 6.3 Mantle — 多后端沙箱

**位置**: [runtime/sensing/mantle/](file:///f:/新建文件夹/echo-agent/runtime/sensing/mantle/)

```python
class MantleProvider(Protocol):
    """沙箱接口"""
    async def run_command(
        self, cmd: str, *, cwd: str, env: dict, timeout: float
    ) -> CommandResult: ...
    
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def delete_file(self, path: str) -> None: ...

# 五种沙箱实现:
# local.py           → 本地进程 (默认)
# docker_mantle.py   → Docker 容器隔离
# ssh_mantle.py      → SSH 远程执行
# k8s_mantle.py      → Kubernetes Pod
# subprocess_mantle.py → 子进程 (带流式输出)
```

---

## 7. 记忆层 (runtime/memory/)

### 7.1 Genome Journal — 事件日志与轨迹

**位置**: [runtime/memory/genome/journal.py](file:///f:/新建文件夹/echo-agent/runtime/memory/genome/journal.py)

```python
# 事件类型 (30+ 种)
JournalEventType = Literal[
    "step",              # 单个工具步骤
    "trajectory",        # 完整轨迹
    "immune",            # 免疫判定
    "budget_squirt",     # 预算喷射 (拒绝)
    "budget_commit",     # 预算提交
    "budget_breaker_reset", # 熔断重置
    "genome_patch",      # 基因组补丁
    "reflex_hit",        # 反射命中
    "task_started",      # 任务开始
    "node_started",      # 节点开始
    "task_checkpoint",   # 任务检查点
    "react_checkpoint",  # ReAct 检查点
    "task_paused",       # 任务暂停
    "task_resumed",      # 任务恢复
    "token_usage",       # Token 使用
    "file_op",           # 文件操作
    "preview_refresh",   # 预览刷新
    "skill_proposal_decision", # 技能提案决策
    "curriculum_goal_decision", # 课程目标决策
    "mcp_proposal_decision",    # MCP 提案决策
    "protocol_drift_decision",  # 协议漂移决策
    "sub_tool_start",    # 子工具开始
    "sub_tool_end",      # 子工具结束
    "browser_artifact",  # 浏览器产物
]

# 事件基类
class JournalEvent(BaseModel):
    schema_version: int = 1          # Schema 版本 (支持迁移)
    event_id: UUID
    event_type: JournalEventType
    task_id: TaskId | None
    arm_id: ArmId | None
    actor: str | None
    agent_id: str | None
    conversation_id: str | None
    ts: datetime
    source: Source | None

# 派生事件类
class StepEvent(JournalEvent):       # 单个工具步骤
class TrajectoryEvent(JournalEvent): # 完整轨迹
class ImmuneEvent(JournalEvent):     # 免疫判定
class BudgetEvent(JournalEvent):     # 预算事件
class TaskStartedEvent(JournalEvent):# 任务开始
class TaskCheckpointEvent(JournalEvent): # 检查点

class Journal:
    """基于 JSONL 的追加式事件日志"""
    def write_event(self, event: JournalEvent) -> None: ...
    def write_step(self, task_id, arm_id, step, actor) -> None: ...
    def write_trajectory(self, task_id, trajectory, actor) -> None: ...
    def write_budget(self, event_type, task_id, actor, reason) -> None: ...
    def replay(self, since: datetime | None) -> Iterator[JournalEvent]: ...
    def close(self) -> None: ...

# Schema 版本迁移机制:
# - 当前版本: CURRENT_SCHEMA_VERSION = 1
# - 旧事件通过 _EVENT_MIGRATIONS 适配器解析
# - 目标: 历史 jsonl journals 不受重构影响
```

### 7.2 MemoryHub — 统一记忆检索

**位置**: [runtime/memory/hub.py](file:///f:/新建文件夹/echo-agent/runtime/memory/hub.py)

```python
class MemoryRecord:
    """统一记忆记录"""
    id: str
    kind: MemoryKind        # fact | memory_md | learned_rule | learned_memory | intelligence_report
    content: str
    source: str             # user_store | memory_md | planner
    scope: str              # global | project | team | agent
    scope_key: str
    confidence: float       # 0.0 - 1.0
    tags: list[str]
    evidence_refs: list[str]
    score: float            # 检索相关度分数

class MemoryHub:
    """只读门面: 统一多种记忆源的检索"""
    
    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]:
        """检索并按相关度排序"""
    
    def collect(self, query: MemoryQuery) -> list[MemoryRecord]:
        """从所有源收集记录"""
        # 1. _collect_user_store()     → 用户事实库
        # 2. _collect_memory_md()      → 全局/项目/团队/Agent 级 MEMORY.md
        # 3. _collect_planner_sections() → Planner 学习段落
```

**记忆源覆盖**:
| 源 | 位置 | 范围 |
|----|------|------|
| `user_store` | 用户事实数据库 | 跨项目用户偏好 |
| `~/.echo/MEMORY.md` | 全局记忆文件 | 全局偏好/规则 |
| `<project>/.echo/MEMORY.md` | 项目记忆文件 | 项目特定知识 |
| `<project>/teams/<team>/MEMORY.md` | 团队记忆 | 团队协作知识 |
| `<project>/teams/<team>/agents/<id>/MEMORY.md` | Agent 记忆 | Agent 个人知识 |
| Planner learned sections | 自动学习 | 运行中积累的规则 |

### 7.3 Knowledge Graph — 知识图谱

支持后端: SQLite / Kuzu (图数据库)
三元组存储 + 相似度检索 + 实体关系查询。

---

## 8. 安全层 (runtime/safety/)

### 8.1 CircuitBreaker — 熔断器

**位置**: [runtime/safety/ink/breaker.py](file:///f:/新建文件夹/echo-agent/runtime/safety/ink/breaker.py)

标准三态熔断模式:

```python
CircuitState = Literal["closed", "open", "half_open"]

class CircuitBreaker:
    """滑动窗口熔断器"""
    
    def __init__(
        self, *,
        window_seconds: float = 60.0,          # 窗口大小
        max_calls_per_window: int | None,       # 最大调用数
        max_cost_usd_per_window: float | None,  # 最大费用
        max_errors_per_window: int | None,      # 最大错误数
        cooldown_seconds: float = 30.0,         # 冷却时间
    ): ...

    def check(self) -> CircuitState:
        """调用前检查: closed→可执行, open→CircuitOpen 异常, half_open→探针限制"""
    
    def record(self, *, success: bool, cost_usd: float) -> None:
        """调用后记录: half_open 成功→closed 重置, 失败→re-trip"""
    
    def snapshot(self) -> dict: ...
    def reset(self) -> None: ...

# 三态转换:
# closed ──(超阈值)──→ open ──(冷却期满)──→ half_open
# half_open ──(成功)──→ closed
# half_open ──(失败)──→ open
```

### 8.2 Immunity — 免疫系统

**位置**: [runtime/safety/immunity/](file:///f:/新建文件夹/echo-agent/runtime/safety/immunity/)

```python
class ImmunityEngine:
    """多层免疫检查"""
    
    def guard_tool(self, tool_name: str, args: dict) -> ImmuneVerdict:
        """工具守卫: 检查危险工具"""
    
    def guard_path(self, path: str, operation: str) -> ImmuneVerdict:
        """路径守卫: 防止越权访问"""
    
    def guard_url(self, url: str) -> ImmuneVerdict:
        """URL 守卫: 防护恶意链接"""
    
    def trust_score(self, source: Source) -> float:
        """信任评分 (0.0-1.0)"""

class ImmuneVerdict(BaseModel):
    allowed: bool
    risk_score: RiskScore           # low | medium | high | critical
    reason: str
    require_approval: bool
```

**信任评分体系** (primitives.py):
```python
TRUST_USER_DEFAULT = 0.80        # 用户输入
TRUST_TOOL_DEFAULT = 0.75        # 工具输出
TRUST_DOC_DEFAULT = 0.60         # 文档
TRUST_TRAJECTORY_DEFAULT = 0.55  # 历史轨迹
TRUST_INFERENCE_DEFAULT = 0.50   # LLM 推理 (封顶)
TRUST_SYSTEM = 1.0               # 系统无条件信任
```

### 8.3 Regeneration — 自演化/技能锻造

**位置**: [runtime/safety/regeneration/](file:///f:/新建文件夹/echo-agent/runtime/safety/regeneration/) (20 个文件)

#### 核心锻造管道 (skill_forge.py)

```python
class ForgeConfig:
    min_hits: int = 3                  # 最少出现次数
    min_success_rate: float = 0.70     # 最低成功率
    shadow_runs: int = 5               # 影子运行次数
    shadow_success_threshold: float = 0.80  # 影子验证阈值
    max_candidates_per_run: int = 10   # 单次最多候选数

class SkillForge:
    """从成功轨迹中自动锻造技能"""
    
    def propose(self) -> list[ForgedSkillCandidate]:
        """1. 聚类成功轨迹 → 识别可复用模式 → 生成候选技能"""
    
    def shadow_test(self, candidates) -> SkillForgeResult:
        """2. 影子运行验证 → 自动生成测试用例 → 回归验证"""
    
    def promote(self, candidates) -> SkillForgeResult:
        """3. 通过的候选提升为公开技能"""

class ForgedSkillCandidate:
    candidate_id: str
    name: str
    path_signature: str              # 路径签名 (BLAKE2b hash)
    underlying_sequence: list[str]   # 子技能序列
    source_trajectory_ids: list[str] # 源轨迹
    source_success_rate: float
    generated_tests: list[SkillTestCase]
    step_templates: list[dict]       # 步骤间数据流模板
    status: str                      # proposed|shadow_pass|shadow_fail|public|retired
```

#### 其他演化模块

| 模块 | 功能 |
|------|------|
| `rule_extractor.py` | 从轨迹中提取规则 |
| `kg_updater.py` | 更新知识图谱 |
| `workflow_rewriter.py` | 工作流重写优化 |
| `workflow_applier.py` | 工作流应用 |
| `camouflage.py` | 拟态策略管理 |
| `variant_evaluator.py` | Prompt/策略变体评估 |
| `memory_consolidator.py` | 记忆巩固 |
| `recipe_evaluator.py` | Recipe 评估 |
| `intel_collector.py` | 情报收集 |
| `forge_auto_tick.py` | 自动触发锻造 |
| `gepa_variants.py` | GEPA 变体管理 |
| `gepa_bridge.py` | GEPA 桥接 |
| `gepa_optimizer.py` | GEPA 优化器 |
| `gepa_runs.py` | GEPA 运行 |
| `gepa_addendum_store.py` | GEPA 附录存储 |
| `genome_registry.py` | 基因组注册 |
| `lightweight_shadow.py` | 轻量影子测试 |
| `scheduler.py` | 演化调度 |

### 8.4 Camouflage — 拟态 / A/B 实验

策略切换与 Prompt 变体管理，支持 A/B 实验和优胜劣汰。

### 8.5 Constitution — 宪章 / 伦理约束

定义 Agent 必须遵守的安全规则和伦理约束，注入到 system prompt 中。

---

## 9. 平台层 (runtime/platform/)

### 9.1 Models — 核心数据模型

**位置**: [runtime/platform/models/](file:///f:/新建文件夹/echo-agent/runtime/platform/models/)

#### primitives.py — 基础原语

```python
# 强类型 ID
TaskId = NewType("TaskId", UUID)
ArmId = NewType("ArmId", str)
SkillId = NewType("SkillId", str)
TrajectoryId = NewType("TrajectoryId", UUID)
StepId = NewType("StepId", int)
GenomeId = NewType("GenomeId", UUID)
RecipeId = NewType("RecipeId", UUID)

# 信任源
class Source(BaseModel):
    source_id: str
    source_type: SourceType  # user|tool|doc|trajectory|inference|system
    trust_score: float       # 0.0-1.0

# 成本记录
class CostEntry(BaseModel):
    tokens_in: int
    tokens_out: int
    usd: float
    latency_ms: float
    
    @property
    def tokens(self) -> int: ...
    def __add__(self, other: CostEntry) -> CostEntry: ...
```

#### pipeline.py — 管线模型

```python
# Stage 1: 意图解析
class ParsedIntent(BaseModel):
    intent_id: UUID
    raw: str                    # 原始输入
    intent_type: IntentType     # query|task|event|command|plan|refactor|debug|design|chitchat
    normalized_goal: str
    modalities: list[Modality]  # text|file|image|audio|webhook
    privacy: PrivacyClass       # public|internal|confidential|personal

# Stage 2: 路由决策
class RouteDecision(BaseModel):
    path: RoutePath             # reflex|deliberative
    reflex_rule_id: str | None
    reflex_confidence: float | None

# Stage 3: 任务图
class TaskGraph(BaseModel):
    task_id: TaskId
    nodes: list[TaskNode]       # 执行节点
    edges: list[WorkflowEdge]   # 依赖边
    budget: BudgetSpec
    strategy: str
    task_type: str
    
    # 验证器: 边引用校验 + 无环校验 (Kahn's algorithm)

class TaskNode(BaseModel):
    node_id: str
    kind: NodeKind              # sucker|subgraph|validator|branch|merger|arm
    skill_ref: SkillId | None
    args_template: dict
    failure_retry: int
    timeout_ms: int
```

#### execution.py — 执行模型

```python
class Step(BaseModel):
    step_id: StepId
    sucker_id: SkillId
    status: ExecutionStatus    # success|failed|rejected|timeout
    input: ToolCall
    output: Any
    cost: CostEntry

class Trajectory(BaseModel):
    trajectory_id: TrajectoryId
    steps: list[Step]
    outcome: TrajectoryOutcome
    step_count: int

class ExecutionResult(BaseModel):
    status: ExecutionStatus
    output: Any
    cost: CostEntry
    error_type: str | None
    stderr_tags: list[str]
```

#### governance.py — 治理模型

```python
class Budget(BaseModel):
    limits: BudgetLimits
    usd_spent: float
    tokens_spent: int
    
    def reserve(self, estimated: CostEntry) -> BudgetReservation: ...
    def commit(self, reservation: BudgetReservation, actual: CostEntry) -> None: ...

class ImmuneVerdict(BaseModel):
    allowed: bool
    risk_score: RiskScore
    reason: str

class AntigenSignature(BaseModel):
    """攻击特征签名"""
```

### 9.2 Session — 会话上下文传递

**位置**: [runtime/platform/session.py](file:///f:/新建文件夹/echo-agent/runtime/platform/session.py)

```python
@dataclass(slots=True)
class Session:
    """Turn 级别会话上下文"""
    actor: str | None                    # 调用者身份
    agent: Agent | None                  # 当前 Agent
    thread_id: str | None                # 线程 ID
    conversation_id: str | None          # 对话 ID (OpenAI 兼容)
    turn_id: str                         # 当前 Turn 的唯一 ID
    started_at: float                    # Turn 开始时间戳
    metadata: dict[str, Any]             # 扩展元数据

    @property
    def agent_id(self) -> str | None: ...
    @property
    def elapsed_seconds(self) -> float: ...

# ContextVar 传递
_current_session: ContextVar[Session | None]

def current_session() -> Session | None: ...
def current_actor() -> str | None: ...
def current_agent_id() -> str | None: ...

@contextmanager
def session_scope(session: Session) -> Iterator[Session]:
    """激活 Session, 同时兼容 legacy ContextVar"""
```

### 9.3 Workspaces — 工作区隔离

**位置**: [runtime/platform/workspaces.py](file:///f:/新建文件夹/echo-agent/runtime/platform/workspaces.py)

```python
@dataclass(frozen=True)
class WorkspaceManager:
    """Per-thread 隔离工作区管理"""
    root: Path

    def allocate(self, thread_id: str) -> Path:
        """创建隔离工作区: <root>/<thread_id>/"""
    
    def layout(self, thread_id: str) -> WorkspaceLayout:
        """标准布局: upload/ output/stages/ output/final/ deploy/ skills/"""
    
    def discard(self, thread_id: str) -> bool:
        """删除工作区 (带安全性检查, 防止路径穿越)"""
    
    def resolve_cwd(self, thread_id: str, explicit: str | None) -> str:
        """确定 Turn 的工作目录"""

@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path
    upload: Path
    output: Path
    stages: Path       # output/stages
    final: Path        # output/final
    deploy: Path
    skills: Path
    manifest: Path     # workspace.json
```

### 9.4 Config — 配置管理

基于 Pydantic 的 YAML + ENV 配置系统，支持:

```yaml
# config.example.yaml
server:
  host: "0.0.0.0"
  port: 8000

models:
  default: "claude-3-5-sonnet"
  providers:
    anthropic:
      api_key: "${ANTHROPIC_API_KEY}"
    openai:
      api_key: "${OPENAI_API_KEY}"

safety:
  budget:
    max_tokens_per_turn: 200000
    max_usd_per_turn: 2.0
  circuit_breaker:
    window_seconds: 60
    max_errors_per_window: 10
    cooldown_seconds: 30

workspaces:
  root: "./data/workspaces"

journal:
  path: "./data/journal.jsonl"
```

---

## 10. 协议层 (runtime/protocol/)

### 10.1 JSON-RPC 2.0 Envelope

**位置**: [runtime/protocol/envelope.py](file:///f:/新建文件夹/echo-agent/runtime/protocol/envelope.py)

```python
# 三种消息类型
Message = JsonRpcRequest | JsonRpcResponse | Notification

class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"]
    id: JsonRpcId              # int | str
    method: str                # "turn/start", "turn/interrupt"...
    params: dict[str, Any]

class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"]
    id: JsonRpcId
    result: Any | None         # 成功时
    error: JsonRpcError | None # 失败时
    # 校验: 必须恰好有一个 (result XOR error)

class Notification(BaseModel):
    """无 id, 不期望回复的推送消息"""
    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any]

# 自定义错误码
class JsonRpcErrorCode(IntEnum):
    APPROVAL_DENIED = -32000
    APPROVAL_TIMEOUT = -32001
    THREAD_NOT_FOUND = -32010
    TURN_NOT_ACTIVE = -32011
    UNAUTHORIZED = -32020
    SERVER_BUSY = -32030
```

**序列化**:
```python
encode_message(message: Message) -> str  # model_dump_json(exclude_none=True)
decode_message(payload: str) -> Message  # 按 shape 自动分派到 Request/Response/Notification
```

### 10.2 Item 状态模型 (Turn / Item 体系)

**位置**: [runtime/protocol/items.py](file:///f:/新建文件夹/echo-agent/runtime/protocol/items.py)

#### Item 生命周期

```
Server emits:
  item/started  ─── 初始 Item 快照 (status=inProgress)
  item/*/delta  ─── 零或多个增量更新 (增量 merge 到 item)
  item/completed ── 最终 Item 快照 (status=completed/failed/interrupted)
```

#### Item 类型

| Item | 字段 | Delta 通知 |
|------|------|-----------|
| `UserMessageItem` | text, attachments | - |
| `SteeringUserMessageItem` | text, targetTurnId | - |
| `AgentMessageItem` | text | `item/agentMessage/delta` |
| `ReasoningItem` | summary, content | `item/reasoning/delta` |
| `PlanItem` | text | - |
| `TodoListItem` | explanation, plan[] | `item/todoList/update` |
| `CommandExecutionItem` | command, aggregatedOutput, exitCode, processId | `item/commandExecution/outputDelta` |
| `FileChangeItem` | changes[] (diff, hunks, path, op) | `item/fileChange/hunkDelta` |
| `McpToolCallItem` | server, tool, arguments, result, durationMs | - |
| `ErrorItem` | message, willRetry, errorInfo | - |

#### Turn 模型

```python
class Turn(BaseModel):
    id: str                          # "trn_..."
    thread_id: str
    status: TurnStatus               # inProgress|completed|interrupted|failed
    started_at: datetime
    completed_at: datetime | None
    items: list[Item]                # 有序 Item 列表
    error: dict | None
    params: TurnParams | None

class TurnParams(BaseModel):
    thread_id: str
    input: list[dict]                # 用户输入消息列表
    cwd: str | None
    approval_policy: Literal["never", "on-request", "untrusted"]
    sandbox_policy: dict             # {type, networkAccess}
    model: str | None
    effort: Literal["minimal", "low", "medium", "high"]
    summary: Literal["none", "auto", "detailed"]
    output_schema: dict | None
    planning_mode: bool              # 计划模式
    topology_id: str | None          # 团队拓扑 ID
```

---

## 11. 适配器层 (runtime/adapters/)

| 模块 | 功能 |
|------|------|
| `instrumentation.py` | OpenTelemetry 追踪 (`trace_stage` 装饰器) |
| `scheduler.py` | `BackgroundRunner` — 后台周期性任务执行器 |

---

## 12. 前端架构 (frontend/)

**技术栈**: Next.js 14 + React 18 + TypeScript + Tailwind CSS + Shadcn UI + Zustand

### 12.1 页面路由

| 路由 | 页面 |
|------|------|
| `/` | 首页 → 重定向到 `/workspace` |
| `/workspace/layout` | 主布局 (侧边栏 + 顶栏) |
| `/workspace/chats/[thread_id]` | 聊天对话视图 |
| `/workspace/code/[thread_id]` | 代码编辑器视图 |
| `/workspace/team/[thread_id]` | 团队协作视图 |
| `/workspace/agents/` | Agent 管理 |
| `/workspace/skills/` | 技能浏览 |
| `/workspace/knowledge/` | 知识图谱 |
| `/workspace/evolution/` | 演化面板 |
| `/workspace/reflex/` | GEPA 反射面板 |
| `/workspace/swarm/` | Swarm 集群可视化 |
| `/workspace/workflows/` | 工作流编辑器 |
| `/workspace/observability/` | 可观测性 |
| `/workspace/diagnostics/` | 诊断 |
| `/workspace/settings/` | 设置 |
| `/workspace/mcp/` | MCP 管理 |
| `/workspace/intelligence/` | 情报分析 |
| `/workspace/browser/` | 浏览器 |
| `/workspace/computer/` | 计算机操作 |
| `/workspace/desktop-organizer/` | 桌面组织 |
| `/workspace/pairing/` | 设备配对 |
| `/workspace/store/` | 商店 |
| `/workspace/architecture/` | 架构视图 |
| `/workspace/channels/` | 频道管理 |
| `/workspace/plugins/` | 插件管理 |
| `/workspace/app-auth/` | 应用授权 |
| `/realtime/[thread_id]` | 实时对话 (旧版路由) |
| `/login/` | 登录 |
| `/register/` | 注册 |
| `/about/` | 关于 |
| `/browser/` | 浏览器页面 |

### 12.2 PageAgent Bridge

[page-agent-bridge.ts](file:///f:/新建文件夹/echo-agent/runtime/../frontend/src/core/page-agent-bridge.ts) — 前端内部的 Agent 框架:

```typescript
interface EchoPageAgentBridge {
    version: string
    snapshot(): PageAgentSnapshot        // 获取当前页面状态快照
    run(action): Promise<RunResult>      // 执行页面操作
    
    // 支持的操作:
    // - click(id, confirm?)     → 点击元素
    // - input(id, text, clear?) → 输入文本
    // - submit(id, confirm?)    → 提交表单
    // - capability(id, input?)  → 触发能力
}

// 全局注册: window.__echoPageAgent
// 能力注册: registerPageAgentCapability({...})
```

---

## 13. Agent 定义与技能系统 (agents/ + skills/)

### 13.1 Agent 定义结构

```
agents/<agent-name>/
├── profile.jsonc           # Agent 元数据
│   {
│     "name": "...",
│     "description": "...",
│     "model": "claude-3-5-sonnet",
│     "skills": ["read_file", "write_file", ...],
│     "constitution": "default"
│   }
├── agent-core/
│   ├── SOUL.md             # 系统提示 (注入到 LLM system message)
│   └── USER.md             # 用户提示模板
└── avatar.svg              # 头像
```

### 13.2 技能定义结构

```
skills/<skill-name>/
└── SKILL.md                # YAML frontmatter + Markdown body
```

SKILL.md 会被解析为 `Skill` 对象，注册到 `SkillRegistry`。前端 YAML 定义参数 schema，body 描述实现逻辑（供 LLM 理解）。

---

## 14. 完整数据流追踪

### 14.1 用户发送 "帮我分析 src/main.py 的代码质量"

```
1. Client → WebSocket send(JSON-RPC)
   {
     "jsonrpc": "2.0", "id": 1, "method": "turn/start",
     "params": {
       "threadId": "th_abc123",
       "input": [{"text": "帮我分析 src/main.py 的代码质量"}]
     }
   }

2. Siphon RpcConnection 接收
   → decode_message() → JsonRpcRequest
   → dispatch → RealtimeRuntime.start_turn()

3. Realtime Turn Routing
   → looks_like_tool_intent("帮我分析 src/main.py 的代码质量")
   → _TOOL_INTENT_RE: 匹配 "分析" + "main.py" → True
   → 路由到 Cerebrum

4. Session 建立
   → session_scope(Session(actor=user, thread_id="th_abc123", ...))

5. Workspace 分配
   → WorkspaceManager.allocate("th_abc123")
   → 创建 data/workspaces/th_abc123/

6. Cerebrum.stream_react_loop()
   ├─ System Prompt 组装:
   │   ├─ Agent SOUL.md
   │   ├─ MemoryHub.retrieve("代码质量")
   │   ├─ Skill descriptions: read_file, edit_code, ...
   │   └─ Constitution rules
   │
   ├─ LLM Call (Anthropic Claude):
   │   Messages: [system, user("帮我分析 src/main.py 的代码质量")]
   │   → 流式响应
   │   → emit ReasoningItem (thinking)
   │   → parse: <tool_call>{"name":"read_file","args":{"path":"src/main.py"}}</tool_call>
   │
   ├─ Tool Execution (Beak):
   │   ├─ budget.reserve(CostEntry(tokens_in=500, usd=0.005))
   │   ├─ immunity.guard_tool("read_file", {"path": "src/main.py"})
   │   │   └─ path_guard: path is within workspace → allowed
   │   ├─ hooks.run_pre()
   │   ├─ skill.handler(path="src/main.py")
   │   │   └─ Mantle (local): read file from workspace
   │   ├─ emit CommandExecutionItem
   │   │   → "Reading src/main.py..."
   │   │   → outputDelta: [file content stream]
   │   │   → completed
   │   ├─ budget.commit(reservation, actual)
   │   ├─ journal.write_step()
   │   └─ circuit_breaker.record(success=True)
   │
   ├─ Observation 注入 LLM context
   │   → next LLM call with file content as tool result
   │
   ├─ LLM Call 2:
   │   → thinking: 分析代码结构、复杂度、潜在问题...
   │   → emit AgentMessageItem: "这段代码有以下问题..."
   │   → stop_reason: "end_turn"
   │
   └─ converged = True

7. Journal 持久化
   → TrajectoryEvent 写入 data/journal.jsonl

8. Turn 完成
   → emit turn/completed notification
   → Client 收到最终状态
```

---

## 15. 关键类与接口速查表

### 15.1 核心接口 (Protocol)

| 接口 | 位置 | 职责 |
|------|------|------|
| `RealtimeRuntime` | siphon/realtime_gateway.py | Turn 循环契约 |
| `EventEmitter` | siphon/realtime_gateway.py | 事件推送器 |
| `ModelProvider` | sensing/eyes/models.py | LLM Provider |
| `MantleProvider` | sensing/mantle/ | 沙箱接口 |

### 15.2 核心类

| 类 | 位置 | 职责 |
|-----|------|------|
| `Cerebrum` | core/cerebrum/react_loop.py | ReAct 主循环 |
| `BeakExecutor` | execution/beak/executor.py | 工具执行引擎 |
| `SkillRegistry` | execution/suckers/registry.py | 技能注册表 |
| `SwarmRuntime` | execution/swarm/runtime.py | 集群调度 |
| `MultiModelRouter` | sensing/eyes/multi_router.py | 多模型路由 |
| `RpcConnection` | sensing/siphon/realtime_gateway.py | WS 连接管理 |
| `Journal` | memory/genome/journal.py | 事件日志 |
| `MemoryHub` | memory/hub.py | 统一记忆检索 |
| `CircuitBreaker` | safety/ink/breaker.py | 三态熔断 |
| `SkillForge` | safety/regeneration/skill_forge.py | 技能锻造 |
| `Hearts` | core/hearts/hearts.py | 心跳/HA 管理 |
| `Session` | platform/session.py | 会话上下文 |
| `WorkspaceManager` | platform/workspaces.py | 工作区隔离 |
| `TaskGraph` | platform/models/pipeline.py | 任务图定义 |

### 15.3 关键模型

| 模型 | 位置 |
|------|------|
| `Turn` / `TurnParams` | protocol/items.py |
| `Item` (Union of 10 types) | protocol/items.py |
| `JsonRpcRequest/Response/Notification` | protocol/envelope.py |
| `TaskId/ArmId/SkillId/...` | platform/models/primitives.py |
| `CostEntry` | platform/models/primitives.py |
| `Source` (trust model) | platform/models/primitives.py |
| `ParsedIntent` | platform/models/pipeline.py |
| `TaskGraph/TaskNode/WorkflowEdge` | platform/models/pipeline.py |
| `Step/Trajectory/ExecutionResult` | platform/models/execution.py |
| `Budget/ImmuneVerdict` | platform/models/governance.py |
| `JournalEvent + 15 subtypes` | memory/genome/journal.py |
| `Skill` | execution/suckers/registry.py |

---

## 16. 运行与部署

### 16.1 依赖组

```bash
# pyproject.toml 定义的 extras:
pip install -e ".[minimal]"           # 最小运行: pydantic + httpx
pip install -e ".[dev]"               # 开发: pytest + ruff
pip install -e ".[serve]"             # API 服务: fastapi + uvicorn
pip install -e ".[web]"               # 网络: httpx + trafilatura
pip install -e ".[anthropic]"         # Anthropic provider
pip install -e ".[browser]"           # Playwright
pip install -e ".[mantle-docker]"     # Docker 沙箱
pip install -e ".[mantle-ssh]"        # SSH 沙箱
pip install -e ".[mantle-k8s]"        # K8s 沙箱
pip install -e ".[hearts-redis]"      # Redis 协调
pip install -e ".[hearts-etcd]"       # etcd 协调
pip install -e ".[desktop]"           # 桌面操作
pip install -e ".[mcp]"               # MCP 协议
pip install -e ".[all]"               # 全功能
```

### 16.2 命令行

```bash
python -m runtime --help
python -m runtime status              # 系统状态
python -m runtime serve --config config.local.yaml --port 8000
python -m runtime ui --port 8000
python -m runtime run "目标描述"
python -m runtime run "目标" --planner llm --model claude-3-5-sonnet
python -m runtime quickstart --non-interactive
python -m runtime demo
python -m runtime bugfix-demo
python -m runtime reflection-demo
python -m runtime evolution-demo
python -m runtime kg --from-journal journal.jsonl
python -m runtime reflect --from-journal journal.jsonl
python -m runtime backup
python -m runtime restore --input backup.tar.gz
python -m runtime wiki --from-journal journal.jsonl
python -m runtime hub search "关键词"
python -m runtime hub install skill_id
```

### 16.3 Docker

```bash
docker compose up -d
docker compose logs -f echo-agent
```

### 16.4 Kubernetes

```bash
kubectl apply -f deploy/k8s/
```

### 16.5 前端

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev            # 开发模式
pnpm build          # 构建
pnpm electron:dev   # Electron 桌面应用
```

---

## 17. 扩展与开发指南

### 17.1 添加新 Skill

1. 创建 `skills/<skill-name>/SKILL.md`
2. 编写 YAML frontmatter (name, description, parameters)
3. (可选) 在 `runtime/execution/suckers/skills_*.py` 中实现 Python handler
4. 内置技能自动加载；自定义技能通过 `SkillRegistry.load_skills_from_dir()` 加载

### 17.2 添加新 Agent

1. 创建 `agents/<agent-name>/` 目录
2. 编写 `profile.jsonc` (配置)
3. 编写 `agent-core/SOUL.md` (系统提示)
4. 编写 `agent-core/USER.md` (用户提示)
5. 添加 `avatar.svg` (头像)

### 17.3 添加新 Provider

实现 `ModelProvider` 协议 (sensing/eyes/models.py):
```python
class MyProvider:
    async def send_message(self, messages, *, stream, **kwargs):
        # 实现 LLM 调用
        ...
```
注册到 `MultiModelRouter`。

### 17.4 添加新 Mantle 后端

实现 `MantleProvider` 协议 (sensing/mantle/):
```python
class MyMantle:
    async def run_command(self, cmd, *, cwd, env, timeout): ...
    async def read_file(self, path): ...
    async def write_file(self, path, content): ...
```

### 17.5 安全钩子

使用 `runtime/safety/hooks/runner.py` 的钩子系统:
```python
from runtime.safety.hooks.runner import dispatch_pre_tool, dispatch_notification

# 在工具调用前拦截
pre_decision = dispatch_pre_tool(
    sucker_id="read_file",
    args={"path": "..."},
    caller="cerebrum",
    session=current_session(),
)
if pre_decision.cancelled:
    return  # 取消执行
if pre_decision.modified_args:
    args = pre_decision.modified_args  # 使用修改后的参数
```

---

*文档版本: 2.0 (深度版)*  
*最后更新: 2026-05-29*  
*生成方式: 基于源码深度阅读 + AST 分析*
