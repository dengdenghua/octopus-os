# STANDARDS · 行业标准对接

> 不接标准 = 孤岛系统。
> 2025–2026 年 Agent 生态有四个事实标准必须支持：**MCP / A2A / Agent Protocol / OpenTelemetry**。
>
> 本文给每个标准一份"**我们系统的哪个模块对接、怎么接、契约是什么**"。

---

## 1. MCP · Model Context Protocol（Anthropic）

### 地位
**事实标准**。Claude / ChatGPT / Cursor / Windsurf 全部原生支持。

### 我们的对接
| 方向 | 模块 | 状态 |
|---|---|---|
| **作为 Client** · 调外部 MCP server | `suckers/mcp/` | ✅ fork 自 E:\echo |
| **作为 Server** · 暴露自己的技能给外部 | `siphon/mcp_server/` | ❌ 待实现 |

### Server 端暴露什么
本系统的**每个 public Sucker** 自动映射为一个 MCP tool：
```python
# auto-generation
for sucker in suckers.public.all():
    mcp_server.register_tool(
        name=sucker.name,
        description=sucker.description,
        input_schema=sucker.input_schema,
        handler=lambda args: ganglia.dispatch(sucker, args),
    )
```

### 约束
- Personal 级 Sucker **不暴露**（复用 DIS-I1）
- 暴露的 Sucker 必经 Immunity（外部调用=非 Tolerance 白名单）

---

## 2. A2A · Agent-to-Agent Protocol（Google）

### 地位
Google 主推（2025 年发布）。面向"**agent 之间跨组织协作**"。核心概念：
- `agent.json` 发现文件（类似 robots.txt）
- Task / Message / Artifact 数据模型
- HTTP + JSON-RPC

### 我们的对接
| 方向 | 模块 | 状态 |
|---|---|---|
| **作为 A2A Agent** · 被其他 agent 调 | `siphon/a2a_server/` | ❌ 待实现 |
| **调外部 A2A Agent** | `suckers/a2a_client/` | ❌ 待实现（可选）|

E:\echo 的 `backend/packages/harness/echo/a2a/` 目录有现成实现，可 fork。

### 映射表

A2A 概念 ↔ 本系统：

| A2A | 本系统 |
|---|---|
| Agent Card (agent.json) | `cerebrum/` 生成，含 arm_registry 摘要 |
| Task | `cerebrum` 接到的 ArmTask（复用 digestion 流水线）|
| Message | 每阶段的 In/Out 封装 |
| Artifact | `genome/journal` 产出的结构化结果 |
| Streaming | 复用 `siphon/` 的 SSE |

### agent.json 示例
```json
{
  "name": "echo-agent",
  "description": "Biomimetic multi-agent system",
  "version": "1.0",
  "capabilities": {
    "streaming": true,
    "push_notifications": false,
    "state_transition_history": true
  },
  "skills": [
    // 从 suckers/public/ 自动生成
  ],
  "authentication": {
    "schemes": ["bearer"]
  }
}
```

### 约束
- 来自 A2A 外部的 Task **trust_score 初始 0.3**（比 user=0.8 低 —— 不知道对方 agent 可信度）
- A2A Artifact 输出前过 DIS-I5 端加密检查

---

## 3. Agent Protocol（AI Engineer Foundation）

### 地位
开放 HTTP API 标准，较 A2A 更轻量。广泛被 AutoGPT / Smol-developer / AgentGPT 支持。
规范：https://agentprotocol.ai

### 核心端点
```
POST /ap/v1/agent/tasks              创建任务
GET  /ap/v1/agent/tasks              列所有任务
GET  /ap/v1/agent/tasks/{task_id}    看任务状态
POST /ap/v1/agent/tasks/{task_id}/steps   推进一步
GET  /ap/v1/agent/tasks/{task_id}/steps/{step_id}/artifacts/{id}
```

### 我们的对接
`siphon/agent_protocol/` —— 把我们的 digestion 流水线映射成 Agent Protocol 的 Task/Step 模型：

| Agent Protocol | 本系统 |
|---|---|
| Task | `cerebrum` 的 TaskGraph 入口 |
| Step | digestion 的一个 stage 或 Arm 的一次工具调用 |
| Artifact | Step 产出的结构化/非结构化 output |

### 为什么同时支持 A2A 和 Agent Protocol
- **A2A**：面向"企业内/跨组织多 agent 协作"
- **Agent Protocol**：面向"AI 工具链兼容"（你的 agent 可以被 LangSmith 等工具直接管理）

两者生态不重合。两个都暴露成本低，能进两边生态。

---

## 4. OpenTelemetry · 可观测性

### 地位
CNCF 毕业项目，**事实标准**。GenAI 语义约定已经成型：https://opentelemetry.io/docs/specs/semconv/gen-ai/

### 我们的对接
- 核心 SDK：`opentelemetry-sdk`（官方）
- 上层工具：**Langfuse** 或 **Traceloop OpenLLMetry**（二选一）

### Span 强制清单（对应 DIG-I6）

每次请求至少这些 span：

```
request
├── digestion.ingest              (Eyes)
├── digestion.classify            (SpinalCord)
├── digestion.plan                (Cerebrum)
│   └── llm.call{model, tokens}
├── digestion.dispatch            (Ganglia)
├── digestion.execute             (Arms → Beak)
│   ├── immunity.check
│   ├── ink.reserve
│   ├── beak.bite{sucker}
│   │   └── mantle.sandbox.execute
│   └── ink.commit
├── digestion.synthesize          (Cerebrum)
└── digestion.store               (Genome) [async]
```

### 标准属性（GenAI 语义约定）
```python
span.set_attribute("gen_ai.system", "anthropic")
span.set_attribute("gen_ai.request.model", "claude-opus-4-7")
span.set_attribute("gen_ai.usage.input_tokens", 1234)
span.set_attribute("gen_ai.usage.output_tokens", 567)
span.set_attribute("gen_ai.response.finish_reasons", ["stop"])
```

### 本系统额外属性
```python
span.set_attribute("echo.arm_id", "code_arm")
span.set_attribute("echo.sucker_id", "run_pytest")
span.set_attribute("echo.recipe_id", "code_fix_light")
span.set_attribute("echo.genome_version", "v0042")
span.set_attribute("echo.cost_usd", 0.023)
```

### 三个工具选择

| 工具 | License | 用于 |
|---|---|---|
| 纯 OTel SDK + 自建 Jaeger | Apache 2.0 | 完全自控，工程代价最大 |
| **Langfuse** | MIT | MVP 推荐，LLM 友好，有 UI |
| Traceloop OpenLLMetry | Apache 2.0 | 如果不想绑 Langfuse 生态 |

**默认选 Langfuse** —— SaaS free tier 够 MVP 用，自托管也方便。

---

## 5. 实现优先级

按 MVP/Core/Full 分档：

| 标准 | MVP | Core | Full |
|---|---|---|---|
| MCP Client | ✅ fork 即用 | ✅ | ✅ |
| **OpenTelemetry + Langfuse** | ✅ **必装** | ✅ | ✅ |
| Agent Protocol server | ⚠️ 小工程，看对外需求 | ✅ | ✅ |
| MCP Server (对外暴露 skill) | ❌ | ✅ | ✅ |
| A2A Server | ❌ | ⚠️ | ✅ |
| A2A Client | ❌ | ❌ | ✅（跨组织协作时）|

---

## 6. 为什么这四个必接（而不是别的）

| 别的候选 | 不接的理由 |
|---|---|
| OpenAI Assistants API | 已经事实上被更现代的 Responses API 取代；有 OpenAI-compat 网关够了 |
| AutoGen 协议 | 生态封闭，跟 AutoGen 绑死 |
| CrewAI 协议 | 同上 |
| LangSmith 私有 API | 非标，只能与 LangChain 绑 |

**选择逻辑**：**协议开放 + 多厂家实现 + 至少 2026 年还会存在**。

---

## 7. 总集成图

```
                     ┌──────────────────────────────────┐
                     │        外部生态                   │
                     └──────────────────────────────────┘
                          ↓                  ↑
         ┌────────────────┴──────────────────┴────────┐
         │              siphon/                        │
         │  ┌──────────┬──────────┬──────────┬──────┐ │
         │  │ OpenAI   │ Agent    │   A2A    │ MCP  │ │
         │  │ Compat   │ Protocol │  Server  │Server│ │
         │  └─────┬────┴──────┬───┴────┬─────┴──────┘ │
         └────────┼───────────┼────────┼──────────────┘
                  ↓           ↓        ↓
           ┌───────────────────────────────────┐
           │  统一内部调用：cerebrum.dispatch   │
           └───────────────────────────────────┘

         ┌─────────────────────────────────────────────┐
         │      eyes/ · 出去调外部                       │
         │  ┌──────────┬──────────┬──────────┬──────┐ │
         │  │ MCP      │ A2A      │ LLM      │ HTTP │ │
         │  │ Client   │ Client   │ Providers│ APIs │ │
         │  └──────────┴──────────┴──────────┴──────┘ │
         └─────────────────────────────────────────────┘

         ┌─────────────────────────────────────────────┐
         │          OpenTelemetry Spans                 │
         │  (every stage, every tool call, every LLM)   │
         └─────────────────────────────────────────────┘
```

---

## 8. 不变量补充

本文应该在 invariants.md 里注册两条新 CC：

- **CC-STD1** · 所有入口（OpenAI-compat / Agent Protocol / A2A / MCP Server）必经统一的 immunity 检查
- **CC-STD2** · 所有 LLM 调用 必出 OTel GenAI 标准 span（gen_ai.* 属性齐备）

---

## 9. 测试用例（互操作性基线）

MVP 阶段必须有：

- **Langfuse**：用 Langfuse 控制台看到一条完整 trace 从 ingest 到 store
- **OpenAI-compat**：`curl http://localhost:8000/v1/chat/completions` 返回合法流式响应

Core 阶段补：

- **Agent Protocol**：外部 AutoGPT 能把我们当后端跑 3 步任务
- **MCP Server**：Claude Desktop 能连上我们暴露的 tool

Full 阶段补：

- **A2A**：两个 echo-agent 实例互为 peer 协作

---

## 10. 一句话总结

> **接标准不是炫技，是生态信用**。
>
> Agent OS 真正的护城河不是你的内部架构多漂亮，而是**你能不能被别人调、别人能不能被你调**。
> MCP + OTel 两个接好，你就不是孤岛；Agent Protocol + A2A 两个接好，你就是生态节点。
