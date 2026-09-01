# ROADMAP · 章鱼进化路线图

> 从孵化（Hatch）到成年章鱼，分 5 个阶段演化。

---

## 📍 当前现状（2026-06 快照）

**5900+ tests 绿 · 0 lint-blocker · 0 新硬依赖**。
（活计数：`python tools/lint/count_tests.py`；CI 通过 `--check` 防止漂移。）

### ✅ 已闭合（原路线图 0-3 阶段 + 额外补齐）

| 器官 | 状态 |
|---|---|
| **Cerebrum** · LLMPlanner + StaticPlanner · 2 层 Agent（persona 包 ArmPool） | ✅ |
| **Ganglia** · GraphRuntime + resume · 断点续跑 | ✅（注：GraphRuntime 已实装，独立 Ganglion 自治层 / 断联自治未实装）|
| **Arms** · 6 preset agent（含 desktop_operator）· 细粒度 arm 7 种 | ✅ |
| **Suckers** · 30+ skill：file/web/browser(×8)/git(×6)/computer(×6)/exec · 原子/子集两层权限 | ✅ |
| **Hemolymph** · ContextComposer · 四桶配额 · 渐进披露 | ✅ |
| **Immunity** · TrustEngine + PathGuard + URLGuard + Identity + **JWT HS256** | ✅ |
| **Ink** · CircuitBreaker 三态机 + BreakerModelRouter | ✅ |
| **Mantle** · Local + Subprocess + Docker + **SSH + K8s**（四档全） | ✅ |
| **Genome** · Journal + 事件 journal_context（agent_id + conversation_id 注入） | ✅ |
| **Regeneration** · 6 条反思全跑通 · MemoryScope 三档 · SkillForge 真 promote | ✅ |
| **Knowledge Graph** · 内存 + SQLite + 三元组 + BFS | ✅ |
| **SpinalCord** · 三种 matcher（regex/deterministic/cache）· 80% 快路径 | ✅ |
| **Hearts** · FileLockCoordinator（同机）+ **RedisCoordinator** + **EtcdCoordinator**（跨机） | ✅ |
| **Nerves** · hooks + **TypedEventBus** + Registry 真 publisher | ⚠️ 单进程 |
| **Chromatophores** · signal_bus + Boids 仲裁 | ✅ |
| **Camouflage** · A/B splitter + PromptEvolver + **AutoRetireScheduler**（事件驱动自动汰劣） | ✅ |
| **Skin** · EnvSensor 协议 + **FileWatcher/GitHook/ProcessWatch** 三个 sensor | ✅ |
| **Swarm** · ThreadPoolExecutor + **topo_layers DAG** 依赖感知 | ✅ |
| **Siphon** · OpenAI gateway + Hub (CocoLoop) + Agents router + Channels router | ✅ |
| **Channels (IM)** · Slack + WeChat + Feishu + Telegram + Discord + **DingTalk** | ✅ |
| **Eyes** · Anthropic + OpenAI-compat + **Gemini** + Mock · 多模态 images_b64 | ✅ |
| **Scheduler** · BackgroundRunner · CronExpression · periodic/daemon | ✅ |
| **MCP** · StdioClient + PersistentClient + Bridge | ✅ |
| **Config** · pydantic schema + env 插值 + build_from_config | ✅ |
| **Invariants** · 139 条 + 10 条静态 lint + AppendOnlyList + **速查卡** | ✅ |
| **UI/HTTP** · FastAPI app + `/api/health` · `/api/agents` · `/api/conversations` · `/api/groups` · `/api/channels` · `/api/cocoloop` · `/v1/chat/completions` | ✅ |
| **3 个端到端 demo** · bugfix + reflection + evolution 证据链 | ✅ |
| **文档站** · mkdocs material 配置 · `echo-agent tour` 交互式 walkthrough | ✅ |

### ✅ 2026-04 补齐清单 · 上次 roadmap 的"社区共创点"已全部落地

| # | 原缺口 | 落地文件 | 测试 |
|---|---|---|---|
| 1 | Mantle SSH | `mantle/ssh_mantle.py`（CLI + 可选 paramiko 后端） | `tests/test_ssh_mantle.py` (30) |
| 2 | Mantle K8s | `mantle/k8s_mantle.py`（kubectl CLI） | `tests/test_k8s_mantle.py` (24) |
| 3 | Hearts 跨机 HA | `hearts/redis_coordinator.py` + `hearts/etcd_coordinator.py` | `tests/test_redis_coordinator.py` (17) + `tests/test_etcd_coordinator.py` (14) |
| 4 | Camouflage 自动 retire | `camouflage/auto_retire.py` · 三种触发模式 + 事件发布 | `tests/test_auto_retire.py` (12) |
| 5 | Skin 环境感知 | `runtime/skin/` · EnvSensor 协议 + FileWatcher / GitHook / ProcessWatch | `tests/test_skin.py` (24) |
| 6 | DingTalk channel | `channels/dingtalk.py`（HMAC-SHA256 加签模式） | `tests/test_dingtalk.py` (26) |
| 7 | mkdocs + tour + 速查卡 | `mkdocs.yml` + `runtime/tour.py` + `invariants-cheatsheet.md` | `tests/test_tour.py` (4) |

**接入点没破坏 · 老代码零 breakage。所有新能力走 opt-in soft-dep**：
- `pip install 'echo-agent[mantle-ssh]'` → paramiko
- `pip install 'echo-agent[mantle-k8s]'` → kubernetes CLI 已够 · 想用 Python client 加 `[mantle-k8s]`
- `pip install 'echo-agent[hearts-redis]'` / `[hearts-etcd]`
- `pip install 'echo-agent[skin]'` → watchdog（没装降级 polling · 仍能用）
- `pip install 'echo-agent[docs]'` → mkdocs material

### 🤝 仍然留给社区的题目（下一波贡献点）

| 优先级 | 题目 | 切入 |
|---|---|---|
| 🟢 | WhatsApp Business channel | `channels/whatsapp.py` · 参考 `slack.py`（Meta webhook 签名） |
| 🟢 | iMessage channel | `channels/imessage.py` · 参考 `weixin_bot.py`（polling 模式） |
| 🟢 | Microsoft Teams channel | `channels/teams.py` · 参考 `discord.py`（签名验证） |
| 🟢 | Mantle Firecracker / gVisor | 继承 `LocalBackend` · 需要 opt-in 装 firecracker CLI |
| 🟡 | Nerves 跨进程总线 | NATS / Redis Streams 适配 · 现在只单进程 |
| 🟡 | 多租户隔离 | Mantle 分账 · Genome 分片 · 设计量大 · 先 issue |
| 🟢 | Grafana dashboard JSON 模板 | 基于 OTel trace · 零代码贡献好题 |
| 🟢 | LLM-driven bugfix-demo | gated by `ANTHROPIC_API_KEY` · 用真 LLMPlanner 代替 StaticPlanner |

### 🚫 明确不做（技术债清单）

| 不做的 | 为啥 |
|---|---|
| 从头写 Raft | 2000-5000 行起 · 成熟库（pysyncobj/etcd）更稳 · 我们留 `Coordinator` 接口 |
| WeChat 媒体（AES-128-ECB） | 加 cryptography 硬依赖 · ROI 低 · 文本覆盖 80% 用例 |
| 自研低代码 workflow editor | 非 agent 运行时职责 · 上游 xflow 够 |
| 非 MCP 的老 function-call 兼容 | MCP-first · 老协议用户可自写 bridge |
| 流式 LLM streaming（在 ModelRouter 层） | Planner 本身只要最终 text · SSE 已由 gateway 层各自实现 |

---

## 阶段 0 · 孵化期 Hatch （第 1–2 周）

**目标**：骨架立起来，fork 模块能跑
**对应生物**：卵孵化出浮游幼体

- [ ] 仓库初始化，Python 3.11+ / Pydantic 2.x / uv
- [ ] 按 forklist.md P0 fork：`suckers/mcp/`、`mantle/`、`nerves/graph/`、`eyes/models/`、`beak/core/`、`suckers/loader/`
- [ ] 批量改包名 → `runtime`
- [ ] 跑通最小闭环：加载一个 SKILL.md → 进 Mantle → Beak 执行
- [ ] CI：pytest + pre-commit

**验收**：`pytest tests/smoke/` 绿灯。

---

## 阶段 1 · 单腕期 One-Arm （第 3–6 周）

**目标**：1 条 Arm 能独立完成长任务
**对应生物**：幼体长出第一条腕

- [ ] `cerebrum/` MVP：把目标拆成 ArmTask 序列
- [ ] `ganglia/` MVP：单 Ganglion 驱动 Arm 跑完整个 DAG（未实装）
- [ ] `arms/code_arm.py`：第一条腕（代码腕），带 5–10 个 Suckers
- [ ] `genome/checkpoint/` 接入，断点续跑
- [ ] `hemolymph/` v1：context packet 打包器
- [ ] `siphon/openai_gateway/` fork 上线，外部 SDK 能调

**验收**：给定"读这个仓库 + 写个补丁 + 跑测试"这类多步任务，能断网续跑。

---

## 阶段 2 · 八腕期 Eight-Arms （第 7–10 周）

**目标**：8 条 Arm 并行协作 + 腕间广播
**对应生物**：长齐 8 条腕，开始抓多物体

- [ ] 8 条专长腕：code / data / search / browse / file / comm / deploy / observe
- [ ] `nerves/bus/` 分布式消息（NATS 或 Redis Streams）
- [ ] **★ Chromatophores 拆分重构**：由单模块（pub/sub + effector）拆为 `signal_bus/` + `effector_pool/` 两个独立模块（设计债清理，MVP 阶段延迟到此）
- [ ] `signal_bus/` pub/sub，5 类事件上线
- [ ] `ink/` v1：`per_task_budget` + `circuit_breaker`
- [ ] Cerebrum 的路由器按 Arm affinity 分发
- [ ] 压测：8 腕并行跑 20 个任务不互相污染

**验收**：一个任务被 Cerebrum 切给 3 条 Arm 并发做，完成时间 ≤ 单腕串行的 50%。

---

## 阶段 3 · 学习期 Learning （第 11–14 周）

**目标**：Regeneration 反思流水线跑起来，系统开始自己变聪明
**对应生物**：章鱼建立巢穴、学会打开罐子

- [ ] `regeneration/trajectory/` 采集每条 Arm 的执行轨迹
- [ ] `regeneration/evaluator/` 接 Anthropic Batch API 夜间打分
- [ ] `regeneration/skill_forge/` 把高频成功路径结晶成新 Sucker
- [ ] `regeneration/reflection/` 失败路径归纳成 Cerebrum 的规避规则
- [ ] `genome/memory/` 长时记忆层，Teach-Repeat 入库
- [ ] A/B 对比：开启 Regeneration 前后，同类任务成本曲线

**验收**：连续运行 2 周后，新生成的 Sucker 数量 ≥ 10，且命中率 > 60%。

---

## 阶段 4 · 心跳期 Three-Hearts （第 15–18 周）

**目标**：HA + 策略自适应，达到"生产级"
**对应生物**：3 颗心脏齐跳，能变色拟态

- [ ] `hearts/` 三心脏调度：1 主 + 2 备，任一停跳自动接管
- [ ] `camouflage/` 策略 A/B：同任务并行跑多种 prompt/model 组合，按 ROI 收敛
- [ ] `ink/skill_cost_profile`：每 Sucker 成本画像，异常涨价告警
- [ ] `hearts/` 节律调度：预算紧缩时降频，空闲加速反思流水线
- [ ] `mantle/k8s/` 上生产集群
- [ ] 可观测性：OpenTelemetry + Langfuse

**验收**：杀掉任一 Heart，系统 30s 内恢复；Camouflage 跑满 100 次实验后，系统平均成本较阶段 3 再降 ≥ 20%。

---

## 阶段 5 · 成年期 Adult （第 19 周起，持续）

**目标**：生态 + 开源社区
**对应生物**：成年章鱼繁殖

- [ ] SKILL Hub：社区 Sucker 市场，安全扫描
- [ ] IM 集成（Feishu/Slack/Telegram）—— fork echo 现成模块
- [ ] 多租户：Mantle 隔离 + Genome 分片
- [ ] 协议：MCP 已有 + 考虑 ACP / A2A
- [ ] 文档站 + demo 视频

---

## 阶段 6 · 触手期 Tentacles / 跨端期 Cross-Device （2026-Q3 起，~3-4 月）

> **Echo Mobile · 让章鱼的触手真正"触达"Android 设备与桌面**
>
> 详见 [docs/mobile/README.md](mobile/README.md) · [docs/adr/008-echo-mobile.md](adr/008-echo-mobile.md) · [docs/biomimetic/tentacle/README.md](biomimetic/tentacle/README.md)

**目标**：让 echo-agent 能**真实操控** Android 手机 + 桌面电脑，并实现跨端混合编排
**对应生物**：章鱼伸出触手（章鱼能伸出腕外肢体抓住远处目标）

### Phase 0 · 概念验证（✅ 已完成 2026-06-06）

- [x] 30+30 行代码验证 add-only 假设（桌面端零破坏）
- [x] `runtime/tentacle/` 触手器官代码骨架
- [x] `mobile_operator_arm` + `mobile_browser_operator_arm` preset
- [x] 核心 10 个 SKILL.md（tap/swipe/input_text/get_screen_info/...）
- [x] Echo Mobile RPC 客户端骨架（Kotlin）
- [x] ADR-008 决策记录 + docs/mobile/ 完整文档

### Phase 1 · 设备注册 + 简单工具（2 周）

- [ ] Echo Mobile 端 `EchoMobileClient` WebSocket 客户端接通
- [ ] `device/hello` 协议握手 + `device/heartbeat` 30s/次
- [ ] `tool/execute` 通路：tap / swipe / input_text / open_app
- [ ] 设备池 `TentaclePool` 上线，Cerebrum 可见"我的手机已连接"
- [ ] 端到端：用户说"打开微信"，Runtime 派发到手机，Echo Mobile 真机执行

### Phase 2 · 30 技能完整接入（3 周）

- [ ] 补全剩余 20 个 SKILL.md（与 Echo Mobile BaseTool 一一对应）
- [ ] 工具调用双轨：本地 LLM 模式 + RPC 模式
- [ ] Cerebrum 加 `mobile_operator_arm` 调度
- [ ] 工具执行 token 优化（参考 Echo Mobile 三级压缩）

### Phase 3 · 双写配置 + 离线降级（2 周）

- [ ] `DualConfigWriter`（MMKV 兜底 + Runtime KV 主）
- [ ] 冲突检测 + 远程为权威的合并策略
- [ ] 离线时透明降级到 `LOCAL_ONLY` 模式
- [ ] 重新上线后自动重连 + 重新同步

### Phase 4 · 屏幕状态增量上报（2 周）

- [ ] `ScreenStreamer` 屏幕变化事件流
- [ ] 哈希去重 + 5s 节流
- [ ] 引用追踪（稳定 ref ID）
- [ ] Cerebrum 端订阅 → 决策更准

### Phase 5 · 混合编排杀手锏（3 周）

- [ ] `DesktopDevice` 包装本地桌面为触手（与 mobile 并列）
- [ ] 跨端 DAG 示例：写代码 + 真机验收
- [ ] 跨端 DAG 示例：多手机比价 + 飞书汇报
- [ ] Nerves 总线压力测试（10 设备 × 24h）

### Phase 6 · 自进化闭环（2 周）

- [ ] Regeneration 收集 mobile_operator_arm 轨迹
- [ ] 夜间锻造"专用 Android 技能"（如 android_taobao_add_to_cart_v1）
- [ ] `skill/install` 远程下发协议
- [ ] Echo Mobile 热加载新技能，无需更新 APK

### Phase 7 · 浏览器内核集成（远期 4 周）

> **引擎优先级**：GeckoView（主力）→ SystemWebView（兜底）→ Chromium AAR（后备）
> 详见 `docs/mobile/chromium-build/AGENT_BUILD_BRIEF.md`

- [ ] 集成 GeckoView（Maven 一行依赖，自带 WebExtension 支持）
- [ ] GeckoView WebExtension API 桥接（原生支持，无需 CDP）
- [ ] CrxToXpiConverter：Chrome 扩展(.crx) → Firefox 扩展(.xpi) 自动转换
- [ ] ExtensionInstaller：扩展热加载 / 卸载 / 权限管理
- [ ] 7 个浏览器技能（navigate/get_dom/click/type/screenshot/evaluate/install_extension）
- [ ] 反爬实测：Cloudflare 验证网站通过率
- [ ] （后备）Chromium AAR 编译：仅在需要 Chromium 指纹或 CRX 原生格式时启动

### 关键不变量（受 [invariants.md](invariants.md) 约束）

- INV-T1：所有 touch 操作必须经过 Safety / Approval Gate（关键操作）
- INV-T2：Echo Mobile 自身的安全规则 10 条必须保留（prompt 层兜底）
- INV-T3：设备锁（device/lock）由 Runtime 统一管理，Echo Mobile 端只执行
- INV-T4：本地模式（LOCAL_ONLY）跟现有 Echo Mobile **完全一致**，无体验降级

### 验收标准

- 5 台手机 + 1 台桌面，1 个 Runtime，跑 24h 0 崩
- 30 个技能 100% 通过单元测试
- Cerebrum 可在 1 秒内把任务派发给最合适的设备
- 离线时本地模式无缝接管，断网恢复后自动重新连接

---

## 里程碑 × 六大进化模块交叉表

| 阶段 | ① 长任务 | ② 工作流 | ③ 技能 | ④ 记忆 | ⑤ 反思 | ⑥ 成本 |
|---|---|---|---|---|---|---|
| 0 孵化 | — | fork | fork | — | — | — |
| 1 单腕 | ✅ MVP | ✅ | ✅ 5–10 个 | ✅ ckpt | — | — |
| 2 八腕 | ✅ 并发 | — | ✅ 亲和分发 | — | — | ✅ Ink v1 |
| 3 学习 | — | — | ✅ 自生长 | ✅ 长时记忆 | ✅ 流水线 | — |
| 4 心跳 | — | — | ✅ 画像 | — | — | ✅ Hearts 节律 + Camouflage |
| 5 成年 | — | — | ✅ Hub | ✅ 多租户 | — | — |

---

## 风险与护栏

| 风险 | 对应器官 | 护栏 |
|---|---|---|
| 腕失控疯狂烧钱 | Ink | per-task 预算硬顶，超限即停 |
| 单点中枢挂掉 | Hearts | 3 心脏 HA + Ganglion 断联自治（未实装）|
| 反思反而贵 | Regeneration | 全走 Batch API，夜间跑 |
| 腕之间抢资源 | Chromatophores | 状态广播 + Cerebrum 仲裁 |
| 技能爆炸污染上下文 | Suckers | Progressive disclosure，按 affinity 只挂相关子集 |

---

## 非目标（不做）

- 不自研 LLM 推理框架 —— 直接用 Provider API
- 不做通用低代码平台 —— 聚焦 agent 运行时
- 不做可视化编辑器 —— fork echo 的 xflow 够用
- 不兼容非 MCP 的老 Function Call 生态 —— MCP first
