# Getting Started · echo-agent

> 从 0 到会跑 · 真新人三分钟上手 · 然后知道去哪找细节。

## 装 · 跑

```bash
git clone <repo> && cd echo-agent
pip install -e ".[dev]"

# 可选依赖（按需装）
pip install anthropic              # 真 LLM 调用
pip install opentelemetry-api      # OTel trace
pip install mcp                     # 接外部 MCP server

# 跑 3800+ 自动化测试
make test                           # ≈ 60s

# 想 5 分钟掌握总览？→ QUICKSTART.md
```

---

## 第一次跑 · 3 个值得看的 demo

```bash
# ① 最基础：list+read+count 端到端
python -m runtime demo

# ② 真干活：造一个带 bug 的项目 · 自动 read → test → edit → commit
python -m runtime bugfix-demo

# ③ 证明自进化：跑 3 次 bugfix · SkillForge 真把 8 步 pattern promote 成新 skill
python -m runtime evolution-demo --runs 3
```

---

## CLI 子命令全集

```bash
python -m runtime demo              # 固定演示 · 最快验证
python -m runtime bugfix-demo       # 端到端：list+read+test+edit+test+git ×8
python -m runtime reflection-demo   # 6 个反思产出器一把跑（需 --runs N 预热 journal）
python -m runtime evolution-demo    # 反思 + 真 promote 新 skill 进 registry

python -m runtime run ...           # 跑用户目标（--config / --swarm / --planner llm）
python -m runtime bench ...         # 并发 speedup 基准
python -m runtime intel ...         # 主动抓 Web 情报
python -m runtime kg ...            # 查 KG
python -m runtime reflect ...       # 一键跑 6 条反思（批处理模式）
python -m runtime loop ...          # 反思外环：reflect → learn → plan → execute × N

python -m runtime hub search <kw>   # 查 CocoLoop 社区 skill 市场
python -m runtime hub install <id>  # 下载 + zip-slip 防护 + golden test + 注册

python -m runtime status            # 环境 + 能力盘点
python -m runtime ui                # 启动 FastAPI Web dashboard (8000)
python -m runtime serve --config    # 长跑进程（scheduler + UI + reflection）
```

### 最常用的一条链（单次）

```bash
# 1 · 抓当前情报到 Journal
python -m runtime intel \
    "claude opus 4.7" "agent framework news 2025" \
    --fetch-top 1 \
    --journal-file ~/intel.jsonl

# 2 · 反思所有事件，产生 rule/skill/KG/memory/recipe
python -m runtime reflect --from-journal ~/intel.jsonl --verbose

# 3 · 下次规划时带上学到的规避规则
python -m runtime run "write an AI news digest" \
    --planner llm \
    --learn-from ~/intel.jsonl
```

### 一键外环 · `loop`（**新**）

上面三步的自动化版本 —— 每轮跑前先把 journal 里的历史**全量反思**
（rules / memories / KG / recipe verdict 全部灌进 planner），然后 plan+execute，
新事件继续写 journal，下一轮接着用。

```bash
python -m runtime loop "write an AI news digest" \
    --config config.example.yaml \
    --journal ~/history.jsonl \
    --iterations 5
```

每轮打印：`learn · rules=N memories=M kg=K verdict=winning/losing/…`。

---

## 架构一图

```
┌──────────── 用户输入 ─────────────┐
│ CLI / API / Webhook             │
└─────────────┬───────────────────┘
              ▼
  ┌───────────────────┐
  │  Reflex (spinal)  │  命中即旁路 · 0 token
  └─────────┬─────────┘
            ▼ miss
  ┌───────────────────┐          ┌──────────────┐
  │  Cerebrum         │──learned─│ Journal (事) │
  │  · StaticPlanner  │          │ · InMemory   │
  │  · LLMPlanner     │          │ · JSONL      │
  └─────────┬─────────┘          └──────┬───────┘
            ▼                          ▲
  ┌───────────────────┐                │
  │  Ganglia          │                │
  │  · GraphRuntime   │──Trajectory────┘
  │  · SwarmRuntime   │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │  Arms / Beak      │──每步──→ Immunity + Budget + OTel
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │  Skills (Suckers) │
  │  · 5 builtins     │
  │  · 2 web skills   │
  │  · MCP tools      │
  │  · Forged skills  │
  └───────────────────┘

         Journal (累积的 trace)
             │
             ▼
  ┌──────────────────────────────────────────────────────────┐
  │  反思引擎 · 6/6 全闭环 (`reflect` + `loop`)              │
  ├──────────────────────────────────────────────────────────┤
  │  [1] SkillForge         新技能候选  →  SkillRegistry     │
  │  [2] RuleExtractor      规避规则    →  LLMPlanner prompt │
  │  [3] KGUpdater          三元组      →  LLMPlanner prompt │
  │  [4] MemoryConsolidator pattern     →  LLMPlanner prompt │
  │  [5] WorkflowRewriter   Rule 改写   →  StaticPlanner     │
  │  [6] RecipeEvaluator    配方评分    →  LLMPlanner 自省   │
  └──────────────────────────────────────────────────────────┘
  全部 5 条入口见 config.example.yaml 的 learn.* 段；
  每轮 `loop` 按需触发，不启用的留 null 即可。
```

---

## 三条黄金路径（最常用）

### 1 · 单腕串行（默认）

```bash
python -m runtime run "count words in current dir"
```

StaticPlanner 匹配 file_probe 规则 → 3 步 DAG → GraphRuntime 串行 → 事件入 Journal。

### 2 · 多腕并发（`--swarm`）

```bash
python -m runtime run "swarm parallel demo" --swarm
```

SwarmRuntime 分配节点给 3 条专长腕（code / text / generic）并发跑。

Template 依赖自动检测 · 有 `{nX.path}` 类引用自动回落单腕。

### 3 · 真 LLM 规划（`--planner llm`）

```bash
# Mock 路由（不需要 API key）
python -m runtime run "任务描述" --planner llm --model mock/planner

# 真 Claude
export ANTHROPIC_API_KEY=sk-ant-...
python -m runtime run "任务描述" --planner llm --model claude-haiku-4-5-20251001
```

LLM 返回 JSON plan → 解析成 TaskGraph → 剩下流程和 StaticPlanner 一样。

---

## 文件布局导览

```
runtime/
├── models/            数据契约（pydantic · frozen）
├── invariants/        @enforces / AppendOnlyList / 运行时守卫
├── instrumentation/   OTel 接入（软依赖）
├── cerebrum/          Planner · Static + LLM
├── ganglia/           GraphRuntime · 模板引擎 · 单腕
├── swarm/             SwarmRuntime · ThreadPoolExecutor 分发
├── arms/              Worker + ArmPool · 专长路由
├── chromatophores/    SignalBus + BoidsArbitrator
├── spinal_cord/       ReflexRouter · 4 种 matcher
├── beak/              ToolExecutor · 串联一切
├── mantle/            LocalBackend · 沙箱
├── eyes/              ModelRouter + Mock + Anthropic
├── immunity/          TrustEngine
├── suckers/           SkillRegistry · builtins · web · tests
├── genome/            Journal · InMemory + JSONL
├── knowledge_graph/   Triple + 多值 predicate KG
├── hemolymph/         ContextComposer · 4 桶 quota
├── mcp_client/        MCP 软依赖 + Mock + Stdio + bridge
├── regeneration/      反思 6 产出
│   ├── skill_forge          (1)
│   ├── rule_extractor       (2)
│   ├── kg_updater           (3)
│   ├── memory_consolidator  (4)
│   ├── workflow_rewriter    (5)
│   ├── recipe_evaluator     (6)
│   └── intel_collector      (主动学习 · 外部输入)
├── ui/                FastAPI Web Dashboard（软依赖 fastapi/uvicorn）
└── cli.py             9 个子命令（含 ui / loop）
tools/lint/            静态 lint (10 条不变量检查)
tests/                 580+ pytest
```

---

## 常用代码 snippets

### 加一个 skill · 5 行

```python
from runtime.execution.suckers import Skill, SkillRegistry, SkillExpect, SkillTestCase

registry = SkillRegistry()
registry.register(Skill(
    name="reverse_text",
    description="reverse a string",
    affinity=["text"],
    trusted_source="skill://public/reverse_text",
    handler=lambda text="", **kw: {"reversed": text[::-1]},
    tests=[
        SkillTestCase(
            name="basic",
            tier="golden",
            args={"text": "abc"},
            expect=SkillExpect(output_equals={"reversed": "cba"}),
        ),
    ],
))
# 注册时自动跑 golden tests · 不过不写盘
```

### 走一次完整 pipeline

```python
from runtime.execution.tool_engine import ToolExecutor
from runtime.core.cerebrum import StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.core.graph_runtime import GraphRuntime
from runtime.memory.journal import InMemoryJournal
from runtime.safety.auth import TrustEngine
from runtime.platform.models import (
    ArmId, Budget, BudgetLimits, BudgetSpec, ParsedIntent, SkillId,
)
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all

registry = SkillRegistry()
register_all(registry)

executor = ToolExecutor(
    registry=registry,
    immunity=TrustEngine(trusted_sources=["skill://public/*"]),
    journal=InMemoryJournal(),
)

planner = StaticPlanner(
    rules=[Rule(
        name="demo",
        intent_types=["task"],
        skill_sequence=[SkillId("list_cwd")],
    )],
    default_budget=BudgetSpec(tokens=10_000, usd=0.10),
)

intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="list stuff")
graph = planner.plan(intent)
budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=10_000, usd=0.10))

runtime = GraphRuntime(executor=executor, journal=executor.journal)
trajectory = runtime.run(
    graph, budget=budget, caller="arms/x", arm_id=ArmId("x")
)
print(f"success={trajectory.outcome.success}  steps={trajectory.step_count}")
```

### 自定义反思

```python
from runtime.memory.journal import JSONLJournal
from runtime.safety.recovery import (
    RuleExtractor, format_rules_for_prompt,
)

journal = JSONLJournal("past_runs.jsonl")
report = RuleExtractor(journal).extract()
print(format_rules_for_prompt(report.rules_produced))

# 把结果注入 LLMPlanner
planner.update_learned_rules(report.rules_produced)
```

### 连真 MCP server

```python
from runtime.adapters.mcp_client import (
    StdioMCPClient, MCPServerConfig, register_mcp_tools_as_skills,
)
client = StdioMCPClient(MCPServerConfig(
    name="fs",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
))
register_mcp_tools_as_skills(registry, client)
# MCP tools 现在是 registry 里的普通 Skill · 可被 LLMPlanner 规划
```

---

## 能力盘点 · `echo-agent status`

```bash
$ python -m runtime status
```

一次显示所有软依赖的启用状态 + 已注册的 builtin skill 列表 + 环境检查。
具体见 `cli.py::run_status`。

---

## 关键文档索引

| 文档 | 看它当你想知道 |
|---|---|
| [README.md](README.md) | 项目速览 + 当前状态 |
| **[getting-started.md](getting-started.md)** | **本文 · 上手 + 常用示例** |
| [architecture.md](architecture.md) | 仿生理论（章鱼器官 / 神经节 / 免疫）|
| [six-modules.md](six-modules.md) | 六大能力模块（长任务 / 工作流 / 技能 / KG / 记忆 / 上下文）|
| [tiers.md](tiers.md) | MVP / Core / Full 三档切分 |
| [invariants.md](invariants.md) | 139 条不变量清单 |
| [standards.md](standards.md) | MCP / A2A / OTel 对接规范 |
| [naming.md](naming.md) | 双轨命名契约（bio name / engineering name）|
| [CHANGELOG.md](CHANGELOG.md) | 版本历史 |
| `protocols/*.md` | 14 个协议规范（digestion / reflex / swarm ...）|
| `tools/lint/` | 10 条静态不变量 lint |

---

## 最小心智模型 · 记住这五件事

1. **所有工具调用都经 ToolExecutor** —— 它串 Immunity → Budget → 执行 → Journal。你永远不用手动写这 4 步，跑 `execute_step()` 就得。

2. **Journal 是唯一权威来源** —— 所有反思都读它，所有学习都从它来。JSONL 文件化之后可以跨 session / 跨 agent / 跨机器。

3. **两种 Planner 同签名** —— StaticPlanner（规则）和 LLMPlanner（LLM）都是 `plan(intent) → TaskGraph`。换一行代码就能换。

4. **六种反思产出相互独立** —— Skill / Rule / KG / Memory / Workflow / Recipe。各自聚类维度不同，从不冲突，只需从同一 Journal 拉就行。

5. **所有外部 LLM 必经 eyes/** —— 禁止业务代码 `import anthropic`（LINT-04 静态拦截）。装 Anthropic SDK 后用 `AnthropicModelRouter`。

---

## 常见问题

**Q: tests 跑不过怎么办？**
A: 装齐可选依赖 `pip install -e ".[dev]" httpx mcp`。如果仍报错，issue 里贴 `python -m runtime status` 输出。

**Q: 不想装 Anthropic SDK，但想试 LLM 路径？**
A: 用 `MockModelRouter`（`--model mock/planner`）。Mock 响应可 `--mock-response '{"reasoning":"x","nodes":[...]}'` 控制。

**Q: MCP server 没 Node.js 怎么办？**
A: 用 `MockMCPClient` 本地测试 bridge 逻辑；生产环境再接真 server。

**Q: 想换自己的 LLM provider？**
A: 在 `runtime/eyes/` 加一个继承 `ModelRouter` 的类，实现 `call(request) -> ModelResponse`。LINT-04 只允许 `eyes/` 下直 import 厂商 SDK。

**Q: Journal 文件多大算大？**
A: JSONLJournal 每事件 ~500 bytes。10 万事件 ≈ 50 MB。用 `--show-cost` 时超过这量级建议切换分片或 archive。

---

## 下一步学习路径

```
先会跑         → 本文 + demo 命令
                    ↓
理解能干啥     → six-modules.md（六大能力）+ CLI 六子命令
                    ↓
理解为什么这样 → architecture.md（章鱼仿生）+ principles.md（6 条原则）
                    ↓
开始改代码     → tests/ 里找最像你要改的，照抄；LINT-04/LINT-03 拦一下就懂规矩
                    ↓
深入           → protocols/*.md（14 个 · 从 digestion.md 开始）
                    ↓
贡献           → invariants.md（懂了这 139 条再 review 别人 PR）
```

**一个简单判断**：改之前先 `make lint + make test`，改完再跑一次。两次都绿就敢 commit。
