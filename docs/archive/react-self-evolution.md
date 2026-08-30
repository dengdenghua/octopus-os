# ReAct 模式与自进化闭环

本文档归档 2026-04-22 交付的 chat "经典 ReAct" 模式及其完整自演化管线。

---

## 一、用户视角入口

1. 在 `/workspace/chats/*` 打开对话
2. 输入框左下 `ReasoningModePicker` 四档：快速 / 推理 / 深度研究 / **经典**
3. 选"经典" → 本次对话走 `stream_react_loop`（Cerebrum ReAct 循环）
4. 输入框左下看 `EvolutionIndicator`："🧠 N 规则 · M 记忆"
5. 点击指示器 → `EvolutionPanel`（规则 / 记忆 / 配方统计 / 立即反思 / 逐条遗忘）

---

## 二、后端执行链路

```
POST /api/threads/{id}/runs/stream
        │
        ▼
 ParsedIntent 构建
        │
        ▼
 context.mode == "react"?
        │ 是
        ▼
 ┌──────────────────────────────────┐
 │ pick_react_variant()              │  ← Camouflage ABSplitter
 │   → ReActRecipe{max_iter, temp}   │     conservative/balanced/aggressive
 └──────────────┬───────────────────┘
                ▼
 stream_react_loop(recipe 参数)
        │
        ▼  iter 1..N
 ┌──────────────────────────────────┐
 │ LLM 输出 Thought / Action / ...   │
 └──────────┬───────────────────────┘
            │
 ┌──────────┴───────────────┐
 │ Action 解析 · skill 注册? │
 └──────────┬───────────────┘
       是    │    否
 ┌──────────┴───────────┐
 │ Beak.execute_step()   │   ← Immunity + Budget + Handler + Journal
 │   yield tool_start    │      SSE custom → LiveToolTimeline
 │   run handler         │
 │   yield tool_end      │
 └──────────┬───────────┘
            │
            ▼  N 轮或 Final Answer 后
 ┌──────────────────────────────────┐
 │ _persist_react_trajectory()       │
 │   journal.write_trajectory(...)   │   strategy_id="react_loop"
 │   失败 → RuleExtractor            │   负向回路
 │   总是 → MemoryConsolidator       │   正向回路
 │   每 5 次 → KGUpdater (节流)      │   知识图谱
 └──────────┬───────────────────────┘
            ▼
 record_react_variant_result(success)   ← 回报 Camouflage
            │
            ▼
 assistant_message = <details>trace</details> + Final Answer
```

---

## 三、自进化回路

### 负向 · 失败学习

```
ReAct 失败 (tool_status != success 累计 ≥3 次同签名)
        ↓
Journal.read_by_type("trajectory")
        ↓
RuleExtractor.extract()  · 按 (sucker_id, error_sig) 聚类
        ↓
LearnedRule 列表  · pattern + mitigation + severity
        ↓
LLMPlanner.update_learned_rules(...)
        ↓
planner.learned_rules_section  · 系统 prompt 注入点
        ↓
下次 planner.plan() 自动带 "LEARNED MITIGATIONS:"
        ↓
LLM 规划时避开同一个坑
```

### 正向 · 模式统计

```
ReAct 成功或失败均写 Journal
        ↓
MemoryConsolidator.consolidate()  · 按 (arm_id, strategy_id) 聚类
        ↓
ConsolidatedMemory  · success_rate / avg_steps / cost / tier
        ↓
LLMPlanner.update_learned_memories(...)
        ↓
planner.learned_memories_section  · 系统 prompt 注入点
        ↓
下次 planner.plan() 自动带 "CONSOLIDATED MEMORIES:"
        ↓
LLM 优先选高成功率的策略组合
```

### Camouflage A/B

```
3 个默认 ReActRecipe 变体
  - conservative  (max_iter=3, temp=0.1)
  - balanced      (max_iter=4, temp=0.3)
  - aggressive    (max_iter=6, temp=0.5)
        ↓
每次 ReAct 请求 ABSplitter.next_variant()  · 按权重随机
        ↓
记录 success → VariantStats.successes / failures
        ↓
/api/evolution/status 暴露成功率表格
        ↓
(未来) auto_retire 按表现淘汰低效变体
```

---

## 四、文件与代码位置

### 后端
| 功能 | 文件 |
|---|---|
| ReAct 主循环 | `runtime/core/cerebrum/react_loop.py` |
| 模式路由分发 | `runtime/sensing/siphon/thread_compat_router.py`（FLASH/DEEP/REACT 分支） |
| 自进化 API | `runtime/sensing/siphon/observability_router.py`（`/api/evolution/*`） |
| 反思生产者 | `runtime/safety/regeneration/{rule_extractor,memory_consolidator,kg_updater}.py` |
| Camouflage 变体 | `runtime/safety/camouflage/variant.py` |

### 前端
| 功能 | 文件 |
|---|---|
| 模式选择器 | `frontend/src/components/workspace/reasoning-mode-picker.tsx` |
| 进化指示器 | `frontend/src/components/workspace/evolution-indicator.tsx` |
| 详情面板 | `frontend/src/components/workspace/evolution-panel.tsx` |
| API 客户端 | `frontend/src/core/observability/api.ts` |
| 动画关键帧 | `frontend/src/styles/globals.css`（`learn-pulse` / `learn-badge`） |

### 测试
| 覆盖范围 | 文件 | 数量 |
|---|---|---|
| ReAct 循环 + 自进化 | `tests/test_react_loop.py` | 36 |
| 模式分发 / evolution 端点 | `tests/test_thread_compat_stream.py` · `tests/test_app_observability_endpoints.py` | 17 |
| 前端组件 | `frontend/src/components/**` | 106 |

---

## 五、设计决策备忘

1. **规划即地图，不是 ReAct** — Echo 默认 Cerebrum 一次产 DAG，ReAct 是**对外兼容 + 故障回退**的第二条路径，不是主路径。
2. **工具调用共享 task_id** — 同一次 ReAct 回合内的所有 `Beak.execute_step` 用同一个 `react_task_id`，便于 Journal 聚合成一条 Trajectory。
3. **反思节流** — `learn_from_journal` 读全量 journal，所以只在失败时触发（min_hits=3 自带防抖）。KG 更新最贵，每 5 次触发。
4. **删除规则不回写磁盘** — `DELETE /api/evolution/rules/{i}` 只改内存 section；`auto_persist_rules_path` 负责的是结构化 `LearnedRule` 列表，二者不冲突。下次 `learn_from_journal` 调用会从 Journal 重新生成规则。
5. **变体权重不变** — 当前 ABSplitter 三档等权，成功率统计仅用于可视化。引入 `auto_retire.py` 后权重可动态调整。

---

## 六、尚未闭合的环

- **WorkflowRewriter** 只作用于 StaticPlanner，LLMPlanner 路径不受益
- **SkillForge** 需要重复子序列，ReAct 单回合信息密度低
- **auto_retire** 未对 ReAct 变体启用（需要 RecipeEvaluator 喂数据）
