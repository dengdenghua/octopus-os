# Contributing to echo-agent

感谢你愿意贡献。这份文档覆盖**最小必要**的流程 · 不多规则。

## 开发环境

```bash
git clone <repo> && cd echo-agent
pip install -e ".[dev,serve,web,tracing]"
make test        # 或 python -m pytest tests/ -q
```

目标：**测试全绿 · lint 0 error**。

## 提交前硬性要求

- [ ] `python -m pytest tests/` 全绿
- [ ] `python -m tools.lint.invariant_check runtime/ tests/` 0 issue
- [ ] 新增/修改的逻辑必须带**测试**（unit 或 integration）
- [ ] 不引入新强依赖（默认只有 `pydantic`；其他必须走 `[project.optional-dependencies]` 软依赖）
- [ ] 违反不变量的改动必须同步更新 [docs/invariants.md](docs/invariants.md)

## 提 PR 的 checklist

1. 分支起好名字 · `feat/xxx` / `fix/xxx` / `docs/xxx`
2. commit message 简洁 · 说**做了什么**不是**怎么做**
3. PR 描述包含：
   - 为什么做（问题 / 动机）
   - 怎么做（方案选择 · 拒绝的 alternative）
   - 怎么测（新增测试数 / 手测场景）
4. 模块归属规则见 [docs/forklist.md](docs/forklist.md) — 任何从既有代码库引入的模块都需要在文件头注明 `# Provenance: <original repo>, <original path>`，并同步更新该清单

## 架构约束（hard constraints）

### 命名导航：先看职责，再看隐喻

本项目用章鱼器官命名模块，对外传播很有辨识度，但新贡献者不需要先学生物学。
下表是**工程职责优先**的速查——找到你要改的职责，再去对应目录：

| 你想改的东西 | 去哪里 | 章鱼名（助记） |
|---|---|---|
| 任务规划 / 路由 | `runtime/core/cerebrum/` | 中枢脑 |
| 快路径反射 / 缓存命中 | `runtime/core/spinal_cord/` | 脊髓 |
| DAG 执行 / 任务图 | `runtime/core/ganglia/` | 神经节 |
| Worker agent 实例 | `runtime/execution/arms/` | 腕足 |
| 技能注册 / 调用 | `runtime/execution/suckers/` | 吸盘 |
| 工具执行引擎 | `runtime/execution/beak/` | 角质喙 |
| 沙箱 / 安全边界 | `runtime/sensing/mantle/` | 外套膜 |
| I/O 流水线 / SSE | `runtime/sensing/siphon/` | 漏斗 |
| 输入解析 / 模型适配 | `runtime/sensing/eyes/` | 眼睛 |
| 环境感知 / 文件监听 | `runtime/sensing/skin/` | 皮肤 |
| 状态广播 / pub-sub | `runtime/safety/chromatophores/` | 色素细胞 |
| 熔断 / 预算上限 | `runtime/safety/ink/` | 墨囊 |
| 身份识别 / 风控 | `runtime/safety/immunity/` | 免疫 |
| HA 调度 / 心跳 | `runtime/core/hearts/` | 心脏 |
| 长时记忆 / checkpoint | `runtime/memory/genome/` | 基因组 |
| 每轮上下文流 | `runtime/memory/hemolymph/` | 血淋巴 |
| 策略 A/B / 灰度 | `runtime/safety/camouflage/` | 拟态 |
| 反思 / 自进化 | `runtime/safety/regeneration/` | 再生 |

PR 描述和 commit message 里**用工程名**（"fix planner fallback"而非"fix cerebrum fallback"），
代码里的包名保持章鱼命名不变。这样 git log 对不熟悉隐喻的人也可读。

这些规则在 lint 层强制（`tools/lint/invariant_check.py`）：

- **LINT-03** · 不重用 `hearts / arms / suckers / cerebrum / ...` 等 bio-namespaced 类名作为标识
- **LINT-04** · 不在 `eyes/` 以外 import LLM provider SDK（anthropic / openai）
- **LINT-07** · 不写 `journal or InMemoryJournal()` 这种 falsy 陷阱
- 完整清单见 [docs/invariants.md](docs/invariants.md) · 共 139 条

## 贡献方向

- 🟢 低门槛：加 Skill（见 [`suckers/builtins.py`](runtime/execution/suckers/builtins.py)）、加 skill tests、修文档
- 🟡 中门槛：新 ModelRouter provider（[`eyes/`](runtime/sensing/eyes/)）、新 reflection producer（[`regeneration/`](runtime/safety/regeneration/)）
- 🔴 高门槛：改 ToolExecutor / GraphRuntime / 不变量系统 —— 请先开 issue 讨论

## 明确"想要你做"的题目（good first / help wanted）

> 这些不是 backlog 的遗忘 · 是**故意留白**给社区 · 每一条都有清晰切入点。

见 [docs/roadmap.md · 社区共创点](docs/roadmap.md)。

**2026-04-24 更新**:外部 frontier 路线(深度 ReAct / swarm / 自演化)的设计灵感各自落到一组对应能力 · ReAct + DAG runtime 两条路径真实成本可观测 · 演化闭环 demo `bugfix-demo-v2` 可跑 · 3000+ tests 绿。详见 [CHANGELOG.md](CHANGELOG.md)。下列题目仍开着:

**2026-04 更新**:上一版 roadmap 的 6 条"共创点"已全部由维护者落地(Mantle SSH/K8s · Hearts Redis/Etcd · Camouflage auto-retire · Skin · DingTalk · mkdocs+tour)。下列是**新的下一波**题目 · 无一卡生产路径 · 按难度挑:

| 优先级 | 题目 | 为什么 |
|---|---|---|
| 🟢 | 新 IM channel（WhatsApp/iMessage/Teams） | 模板齐全 · 跟着 slack.py / weixin_bot.py 抄 |
| 🟢 | Grafana dashboard JSON 模板 | 零代码好题 · OTel trace 已铺好 |
| 🟢 | LLM-driven bugfix demo | gated by `ANTHROPIC_API_KEY` |
| 🟡 | Mantle Firecracker / gVisor | 上下文：issue 先聊清 isolation 级别 |
| 🟡 | Nerves 跨进程总线 | NATS / Redis Streams 适配 · 量大 |
| 🔴 | 多租户隔离 | Mantle 分账 + Genome 分片 · 需设计 review |

---

### 🟢 入门（2-3 天）

- **新 IM Channel**（DingTalk / WhatsApp / iMessage / Teams）
  - 切入：`channels/` 新增文件，继承 `Channel` ABC
  - 参考：`channels/slack.py`（webhook 签名式）或 `channels/weixin_bot.py`（polling 式）
  - 需求：`handle_webhook()` + `send()` + 签名验证

- **Skin 环境感知模块**（新目录）
  - 切入：`runtime/skin/` 建模块，定义 `EnvSensor` 协议
  - 把 FS watcher / git hook / dir change 翻译成 `TypedEventBus` 事件
  - 零耦合 · 不动核心

- **文档 polish**
  - `mkdocs.yml` + autodoc 配置
  - 139 条不变量的"速查卡"
  - `echo-agent tour` 交互式 walkthrough
  - Grafana dashboard JSON 模板

### 🟡 中等（3-5 天）

- **Mantle SSH / K8s 隔离**
  - 切入：`mantle/ssh_mantle.py` · `mantle/k8s_mantle.py`
  - 继承 `LocalBackend` · 实现 `run_command(argv)`
  - 参考 `subprocess_mantle.py` 和 `docker_mantle.py`

- **Hearts 跨机 HA Coordinator**
  - 切入：`hearts/redis_coordinator.py` 或 `etcd_coordinator.py`
  - 实现 `Coordinator` 协议 · 4 方法
  - 参考 `hearts/coordinator.py` 的 `FileLockCoordinator`

- **Camouflage A/B 自动退役**
  - 切入：`camouflage/evolver.py`
  - 订阅 `RecipeEvaluator` losing verdict · 自动 retire variant

- **LLM 驱动 bugfix demo**
  - 切入：`demos/bugfix_demo_llm.py`
  - 用真 LLMPlanner 代替 StaticPlanner · 测试 gated by `ANTHROPIC_API_KEY`

### 🔴 有想法再开

- Mantle 的 Firecracker / gVisor 集成（做好 issue 先聊）
- Nerves 分布式图执行（跨进程 NATS / Redis Streams · 设计量大）
- 多租户隔离（Mantle 分账 · Genome 分片）

## 不会接受的 PR

- 裸 `import anthropic` 在 eyes/ 以外（LINT-04 自动拦）
- 删除或弱化 invariant lint 规则
- 引入 langchain / llamaindex / crewai 等重框架作为依赖
- 直接替换 `SubprocessBackend` / `CircuitBreaker` / `TrustEngine` 接口
- 无测试的 PR
- **Channel adapter 的 `send()` 不过 constitution gate** (见下节)


## 写新 Channel Adapter (社区开发者必读)

新平台支持 (Telegram / DingTalk / QQ / ...) · **outbound `send()` 必须走
constitution gate** · 否则 agent 可能把用户邮箱、API key、违法内容原样
推到公共频道。详见 [docs/constitution.md](docs/constitution.md) · 特别是
PRIV-2 / PRIV-4 / LAWF-1..6。

### 正确的样板

```python
from runtime.adapters.channels.base import Channel, OutboundMessage

class MyPlatformChannel(Channel):
    channel_id = "myplatform"

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def send(self, msg: OutboundMessage) -> None:
        # ① 过 constitution gate · 扫 PII / secrets / 违规意图
        verdict = self.safe_send(msg)

        # ② 根据 verdict 决定怎么发
        if verdict.action == "block":
            # 已 block · 不要发 · 可选 surface 给用户
            return

        # ③ 用 sanitized 文本调平台 API (rewrite 时是 PII-scrubbed 文本)
        self._platform_api.post_message(verdict.sanitized)
```

### 错的样板 (会触发 audit 警告)

```python
def send(self, msg: OutboundMessage) -> None:
    # ❌ 直接发 · 绕过 constitution gate
    self._platform_api.post_message(msg.content)
```

`ChannelManager.register()` 会在注册时 **扫你的 send() 源码** · 没找到
``safe_send(`` 或 ``check_outbound(`` 调用 → stderr 打警告。

警告不阻塞注册 (软强制 · 保持向后兼容) · 但 **下一个 major release 会改成
template-method 硬约束** · `send()` 由 base class 内置 · 子类实现
`_deliver()` · 届时不合规的 adapter 无法工作。

### 为什么强制这个

- **PRIV-2**: 防止把用户邮箱 / 手机号 / 身份证号原样吐到公共频道
- **PRIV-4**: 防止把 .env / secrets.json 读到的 API key / token 外发
- **LAWF-5**: 防止代用户发明显骚扰 / 诈骗 / phishing 内容

Adapter 的 `send()` 是 agent 向平台**说话的唯一出口** ·
这里不拦 · 后面所有条款都是装饰。


## 讨论

- 架构设计：开 issue · 标 `design` label
- Bug：先贴 `python -m runtime status` 输出 + 复现步骤
- 生态集成（MCP server / 新 provider）：欢迎 · 按 FORKLIST 模式

## 加 Skill 时记录来源

新增或修改 `skills/public/` 等目录下的 SKILL.md 时，请在 front matter
记录来源与许可，便于 [NOTICE](NOTICE) 保持准确：

```yaml
---
name: my-skill
description: ...
source: <原始出处 URL 或 "original">   # 改编自他处务必注明
license: <如 Apache-2.0 / MIT / original>
author: <作者或团队>
---
```

搬运/改编社区技能必须注明出处并确认许可与 Apache-2.0 兼容；无法确认来源
的技能不要并入 `skills/public/`。

## 许可

提交 PR 即视为同意以 [Apache-2.0](LICENSE) 许可贡献。第三方/捆绑内容的
归属见 [NOTICE](NOTICE)。
