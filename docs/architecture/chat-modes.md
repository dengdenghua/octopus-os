# Chat 工作流：经典 ReAct vs Echo 模式

本文用两张对照图说明 Echo Agent 的 chat 执行链路与经典 ReAct 循环的异同。

---

## 一、经典 ReAct 模式（单回路）

每一轮"想一步 → 做一步 → 看结果"都需要 LLM 介入，是一个紧耦合的闭环。

```
     用户输入
        │
        ▼
   ┌─────────┐
   │ 理解意图 │◄──────────────────────────────────┐
   └────┬────┘                                   │
        ▼                                        │
   ┌─────────┐       每一轮都要 LLM 介入         │
   │ 规划一步 │  ← Thought                        │
   └────┬────┘                                   │
        ▼                                        │
   ┌─────────┐                                   │
   │ 调用工具 │  ← Action                         │ 重
   └────┬────┘                                   │ 新
        ▼                                        │ 规
   ┌─────────┐                                   │ 划
   │ 执行动作 │                                   │
   └────┬────┘                                   │
        ▼                                        │
   ┌─────────┐                                   │
   │ 观察结果 │  ← Observation                    │
   └────┬────┘                                   │
        ▼                                        │
    ╱─────╲       完成？                         │
   ╱ 判断  ╲─────No───────────────────────────────┘
   ╲ 完成  ╱
    ╲─────╱
        │ Yes
        ▼
      回复
```

---

## 二、Echo 模式（反射 + 规划即地图）

核心哲学："**先反射，反射不行再规划一张完整地图，然后照图执行，执行中广播事件，失败的教训沉淀成下次规划的先验。**"

```
     用户输入
        │
        ▼
   ┌──────────────┐      命中 (regex/cache/rule)
   │ SpinalCord   │────────────────────► 直接回复（0 LLM）
   │   反射门     │
   └──────┬───────┘ 未命中
          ▼
   ┌──────────────┐
   │  理解意图    │  ParsedIntent（历史+记忆+档案）
   └──────┬───────┘
          ▼
   ┌──────────────┐    ★ 只调用 1 次 LLM
   │   Cerebrum   │      产出完整 DAG
   │   规划全图   │   ┌──► n1 ──┐
   └──────┬───────┘   │         ▼
          │           n0 ──►   n3
          ▼           │         ▲
   ┌──────────────┐   └──► n2 ──┘
   │ GraphRuntime │   （TaskGraph）
   │ 消费 DAG     │
   └──────┬───────┘
          ▼  每节点并行展开
   ┌─────────────────────────────────────┐
   │   Immunity → Beak → Mantle 沙箱      │  （纯执行，无 LLM）
   │     (信任检查)(工具路由)(隔离运行)    │
   └──────┬──────────────────────────────┘
          ▼
   ┌──────────────┐      ┌──────────────┐
   │  观察结果    │─────►│   Journal    │──► SSE ──► 前端
   │  写 Hemolymph│      │  事件广播    │
   └──────┬───────┘      └──────────────┘
          ▼
    ╱───────────╲
   ╱  节点状态   ╲
    ╲───────────╱
     │    │    │
   成功 失败  策略差
     │    │    │
     ▼    ▼    ▼
   下节点  Regeneration  Camouflage
          (抽规则→     (A/B 淘汰
           下次先验)    变体)
     │
     ▼
   全图完成 ──► 合成终回复
```

---

## 三、核心差异速览

|              | 经典 ReAct            | Echo                   |
| ------------ | --------------------- | ------------------------- |
| **LLM 调用** | 每轮都要              | 仅规划 1 次 + 反思离线    |
| **并行度**   | 串行（单链）          | DAG 并行（多 arm）        |
| **失败处理** | 即时重想              | fail-fast + 规则沉淀      |
| **闲聊/简单**| 也走 LLM              | SpinalCord 直接短路       |
| **可观测**   | 轮次日志              | Journal 全链事件流        |
| **心智模型** | "边想边做"            | "先画地图再开车"          |

---

## 四、步骤 ↔ 器官映射

| 经典步骤       | Echo 对应                                    | 代码位置 |
| -------------- | ----------------------------------------------- | -------- |
| ① 输入         | Siphon 接收                                     | `runtime/sensing/siphon/` |
| ② 理解意图     | `ParsedIntent` 构建（含历史/记忆）              | `runtime/sensing/siphon/thread_compat_router.py:1651` |
| ③ 规划任务     | `Cerebrum.LLMPlanner.plan()` → TaskGraph        | `runtime/core/cerebrum/` |
| ④ 调用工具     | Beak（先过 Immunity → Budget）                  | `runtime/execution/beak/` |
| ⑤ 执行动作     | Mantle 沙箱（Local/Docker/SSH/K8s）             | `runtime/sensing/mantle/` |
| ⑥ 观察结果     | step 写回 Hemolymph + Journal 广播              | `runtime/memory/hemolymph/` |
| ⑦ 完成/重规划  | GraphRuntime（MVP fail-fast / Core replan）     | `runtime/core/ganglia/runtime.py` |

---

## 五、三个关键差异点

1. **② 前面多一道 SpinalCord 反射门**
   80% 简单意图根本不进规划环——regex/cache/rule 直接返回。这是"章鱼的腕足不需要请示大脑"的工程体现。

2. **③→④ 不是逐轮 ReAct**
   Cerebrum **一次性**产出完整 TaskGraph（DAG），而不是"想一步→做一步→再想"。经典 ReAct 每轮都要 LLM 介入；这里 LLM 只介入规划/重规划，执行阶段由 Ganglia 纯粹消费 DAG。

3. **⑦ 重规划的触发条件分三档**
   - 节点失败 → `Regeneration.RuleExtractor` 抽教训，下次 plan 时注入 `LEARNED_MITIGATIONS`
   - 策略本身差 → `Camouflage` A/B 自动淘汰低绩效变体
   - 计划性错误 → Cerebrum replan（Core 版；MVP 是 fail-fast）

---

## 参考

- 前端流处理：`frontend/src/core/api/use-stream.ts`
- 后端入口：`runtime/sensing/siphon/thread_compat_router.py:1618`
- 事件总线：`runtime/memory/genome/journal.py`
- 运行时执行：`runtime/core/ganglia/runtime.py` · `runtime/execution/swarm/runtime.py`
