# Implementation Status · 架构声明与代码现实对照

> 本表回答一个问题：**architecture.md / constitution.md 里的每个机制，今天在代码里处于什么状态？**
> 状态分四档：**已接线**（默认路径在用）· **可选后端**（代码完整，按配置启用）· **休眠代码**（模块存在，无调用方）· **未实装**（仅文档）。
> 每一行都给出代码证据；更新本表时请先核查代码，不要照搬旧文档或审查报告的结论。
> 最后核查：2026-06-11。

## 安全治理

| 机制 | 状态 | 证据 |
|---|---|---|
| Constitution 出口检查（Rule 层：PII/secret 扫描、rewrite/block） | **已接线** | `runtime/safety/validation/gate.py` 的 `check_outbound`；所有渠道出口强制经过（`runtime/adapters/channels/base.py`，且 `channels/manager.py` 以 lint 强制 adapter 必须调用） |
| Constitution LLM-Judge 层 | **可选后端**（配置开启） | `runtime/safety/validation/bootstrap.py` 在 serve 启动时按 `safety.enable_llm_judge` / `ECHO_ENABLE_LLM_JUDGE` 注册 judge；gate Pass 3 消费。默认关（每条出口多一次模型调用）；strict 档硬执行、normal/lax 仅审计 |
| Constitution Human-Gate 层 | **已接线**（经审批体系） | `runtime/safety/approval/approval_gate.py` 风险评级 + 审批门；realtime 双向审批通道 |
| Immunity 先天层（信任源白名单、三态判决） | **已接线** | `runtime/safety/auth/trust_engine.py` |
| Immunity 记忆层（抗体记忆：重复违规晶化、命中即拒、可持久化） | **已接线** | `runtime/safety/auth/attack_memory.py`；TrustEngine 在 tolerance 之后最先查（可拦截信任 glob 内的已知攻击者）；`immunity.attack_memory_path` 配置持久化 |
| Immunity 自适应层（行为异常 z-score 评分） | **可选后端 · 实际惰性**（配置开启但当前无评分输入） | `runtime/safety/auth/adaptive_immunity.py`：每 sucker 滑动窗口基线 + z-score（取最异常轴，只收紧，I2 自旁路、I4 冷启动）。`executor.py` 执行后调 `TrustEngine.learn` 积累基线。**但 runtime 不填充 `ToolCall.predicted_cost`，`compute_risk` 收到 0/0 时按 no_prediction 返回冷启动**——故启用后基线会积累、却尚无预测成本可评分，该层目前对放行无实际影响。要真正生效需先填充预测成本。`immunity.enable_adaptive` 开启 |
| 预算熔断（三态 CircuitBreaker） | **已接线** | `runtime/safety/budget_breaker/breaker.py` |
| 敏感路径守卫（含 macOS /private 符号链接） | **已接线** | `runtime/safety/auth/path_guard.py`（沙箱前缀校验）；`file_safety.py` 凭据文件名黑名单（`.env`/`id_rsa`/`~/.ssh/*` 等）由 `runtime/execution/tool_engine/executor.py` 写路径在调 handler 前经 `check_file_write` 强制（写作用域管"写哪"，本层管"绝不写这些名字"） |
| 间接提示注入防御（不可信工具输出定界 + 注入启发式） | **已接线**（第一道，启发式非完备） | `runtime/safety/validation/prompt_injection.py`：`is_untrusted_tool`（web/browser 亲和或 `mcp_*` 前缀）+ `scan_for_injection`（override/role/exfil/control-token 等标记）+ `wrap_untrusted_observation`（围栏化为"数据非指令"并在命中时升级告警）；`react_loop.py` 单动作与并行两路在 observation 回灌 LLM 前对外部工具输出加固。**定界+标注，不改写内容**，是风险信号非保证；高危工具硬门控（taint→审批）仍待后续 |

## 分布式与编排

| 机制 | 状态 | 证据 |
|---|---|---|
| Hearts「3 心 HA 调度」叙事 | **可选后端**（用途比叙事小） | `runtime/core/hearts/` 有 coordinator/etcd/redis 实现；实际消费方是 `runtime/platform/process/distributed_lock.py`（Redis 分布式锁，按依赖可用性启用）与 tour 演示。无"三心互备调度循环" |
| Nerves 消息总线（进程内 TypedEventBus） | **已接线** | `runtime/core/nerves/bus.py`；skill/agent registry 事件发布在用。跨进程的 NATS/Redis 总线曾是休眠代码（零消费者），已于路线收尾时删除 |
| Chromatophores（信号广播 + Boids 仲裁） | **已接线** | `runtime/safety/chromatophores/`（signal_bus、boids）被 `runtime/execution/swarm/runtime.py`、`runtime/cli_run.py` 使用 |
| SpinalCord 反射快路径 | **已接线** | `runtime/core/nerves/reflex/` + 前端 `/workspace/reflex` 管理页 |
| 网状 Arm 直接互通（腕间 gossip 不经中枢） | **未实装**（按叙事口径） | Chromatophores 提供 pub/sub 原语，但 Arm↔Arm 直接协作路径未构建 |

## 自进化

| 机制 | 状态 | 证据 |
|---|---|---|
| 反思闭环（turn scoring → deep reflection → deep evolve） | **已接线** | `runtime/memory/learning/`（turn_scoring、deep_evolution、review_queue、promotion_applier） |
| Camouflage 提示词 A/B 与变体晋升 | **已接线** | `runtime/safety/experiments/scheduler.py`；状态持久化于 `data/camouflage_*.{yaml,json}` |
| Fitness 五层 + 漂移一票否决 | **已接线** | `runtime/safety/evolution/fitness.py`、`drift_monitor.py` |
| 写后自动诊断（代码模式快检注入观察） | **已接线** | `runtime/core/cerebrum/react_execution.py` 的 `_run_auto_diagnostics`（注意：检查器缺失时跳过，不报假阳性） |

## 浏览器自动化

| 机制 | 状态 | 证据 |
|---|---|---|
| Playwright 轨（后台无状态爬取/提取） | **已接线** | `runtime/execution/suckers/browser_skills.py` 的 `browser_*` 技能 |
| Electron webview 轨（桌面应用内可见操作） | **已接线**（仅 Electron 环境） | `runtime/execution/suckers/browser_act_skills.py` 的 `live_browser_*` 技能，经本地 bridge |
| 扩展 relay 轨（操控用户真实浏览器标签页） | **休眠代码** | `extensions/` 有扩展，但未接入 skill 路由 |
| 统一 BrowserBackend 抽象 + 路由优先级 | **已实装**（接缝+三轨 adapter，待真机联调） | `runtime/execution/suckers/browser_backend.py`（Protocol + `resolve_backend` 优先级 extension>electron>playwright）+ `browser_backends.py`（ElectronBackend/PlaywrightBackend/ExtensionBackend，包各轨现有函数，可注入 transport 单测）+ mock。adapter 映射已测；切换三轨现有 skill 调用点改走统一接口、以及真机端到端验证仍待有运行时的环境完成 |

## 维护本表

1. 改变某行状态的 PR，应同步更新本表（和必要时的 architecture.md 措辞）。
2. 「休眠代码」要么找到消费方接线，要么在下个清理周期删除——不要让它无限期停留。
3. 新增器官/机制时先过 `docs/architecture/organ-tiering.md` 的三问，再在此登记初始状态。
