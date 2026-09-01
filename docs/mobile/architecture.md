# Echo Mobile · 架构设计

> **三层三端 · 触手器官 · 跨端混合编排**

## 1. 三层三端总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Tier 0 · 中枢层（Cerebrum Runtime）                    │
│                                                                          │
│   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
│   │   Desktop Arm  │  │  Mobile Arm    │  │  Other Arms    │            │
│   │  (OS Control)  │  │ (Device Control)│ │ (Code/File/...)│            │
│   └────────┬───────┘  └────────┬───────┘  └────────┬───────┘            │
│            │     Nerves 总线 / JSON-RPC 2.0 / WebSocket                  │
│            │          ┌──────┴───────┐                                    │
│            │          │  Ink Sac 预算 │                                    │
│            │          │  Hearts HA   │                                    │
│            │          │  Immunity   │                                    │
│            │          │  Regeneration│                                    │
│            │          └──────────────┘                                    │
└────────────┼─────────────────────┼────────────────────────────────────────┘
             │                     │
       ┌─────┴─────┐         ┌─────┴──────┐
       ▼           ▼         ▼            ▼
   ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
   │ macOS  │  │ Win PC │  │Android │  │Android │
   │(Electron│ │(Electron│ │Client 1│  │Client 2│
   │  壳)   │  │  壳)   │  │(Echo Mobile│  │(Echo Mobile│
   └────────┘  └────────┘  │ 改造)  │  │ 改造)  │
                          └────────┘  └────────┘
       Tier 1              Tier 1 (设备层)
       (桌面端)             (移动端)
```

### 三层职责

| 层 | 职责 | 关键模块 |
|---|---|---|
| **Tier 0 · 中枢** | DAG 分解 · 任务派发 · 跨端协调 · 预算治理 · 反思进化 | `runtime/cerebrum/` `runtime/safety/ink/` `runtime/memory/regeneration/` |
| **Tier 1 · 设备** | 物理执行 · 屏幕感知 · 用户交互 · 状态上报 | 桌面：`extras/desktop/` + `runtime/sensing/eyes/devices/desktop.py`<br>手机：同级 checkout `../echo-mobile/` 改造版 + `runtime/sensing/eyes/devices/mobile.py` |
| **Tier 2 · 网络**（隐含） | WebSocket 长连接 · NAT 穿透 · TLS 加密 | `runtime/protocol/envelope.py` |

### 仿生学映射

| 仿生器官 | 对应系统模块 | 在 Echo Mobile 中的角色 |
|---|---|---|
| **Cerebrum** 中枢脑 | `runtime/cerebrum/` | 任务规划与派发 |
| **Tentacle** 触手 | `runtime/tentacle/` | 物理设备操控（**新器官**）|
| **Arms** 腕 | `runtime/execution/arms/` | 逻辑能力单位 |
| **Suckers** 吸盘 | `runtime/execution/all_skills/` | 原子技能 |
| **Eyes** 眼睛 | `runtime/sensing/eyes/` | 感知层（LLM 调用、屏幕状态）|
| **Nerves** 神经 | `runtime/core/nerves/` | 总线通信 |
| **Skin** 皮肤 | `runtime/sensing/skin/` | 设备事件感知（截屏变化、App 切换）|
| **Ink Sac** 墨囊 | `runtime/safety/ink/` | 预算熔断 |
| **Immunity** 免疫 | `runtime/safety/immunity.py` | 安全规则 |
| **Regeneration** 再生 | `runtime/memory/regeneration/` | 自进化 |

**关键设计**：**Tentacle（新）** 是物理触手，**Arms** 是逻辑能力。两者解耦：
- 一个 Arm 跑在一个 Tentacle 上
- 一个 Tentacle 可承载多个 Arm（不同时段）
- 一个任务可跨多个 Tentacle 协作

---

## 2. 触手器官（`runtime/tentacle/`）

### 2.1 核心抽象

```python
# runtime/tentacle/base.py
class Tentacle(Protocol):
    """物理触手基类 —— 章鱼伸出去的"真实肢体" """

    # 设备身份
    tentacle_id: str              # 全局唯一 ID（如 android-abc123 / desktop-xyz789）
    tentacle_type: TentacleType   # MOBILE / DESKTOP / IOT / TV ...
    platform: str                 # android / linux / macos / windows
    meta: dict                    # 设备元信息（型号、屏幕尺寸、Android 版本）

    # 能力声明
    capabilities: list[str]       # 该触手支持的能力（移动技能 ID 列表）

    # 生命周期
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def heartbeat(self) -> Heartbeat: ...
    async def execute(self, tool_call: ToolCall) -> ToolResult: ...

    # 状态机
    @property
    def is_online(self) -> bool: ...
    @property
    def is_busy(self) -> bool: ...
```

### 2.2 实现分类

```
tentacle/
├── base.py            # Tentacle 抽象基类 + TentacleType 枚举
├── pool.py            # TentaclePool：设备池管理（多设备协调）
├── mobile.py          # MobileTentacle（Android 设备实现）
├── desktop.py         # DesktopTentacle（桌面端自指：把本地桌面包装为触手）
├── apks/              # Echo Mobile 集成相关
│   ├── __init__.py
│   ├── skill_export.py   # 把 30 个 BaseTool 转 SKILL.md
│   ├── tool_bridge.py    # 工具调用桥接（JSON-RPC envelope）
│   └── version.py        # Echo Mobile 端版本兼容
└── transport/
    ├── __init__.py
    ├── websocket.py      # WebSocket 客户端（EchoMobileClient 用）
    └── envelope.py       # JSON-RPC 2.0 envelope
```

### 2.3 设备池（`TentaclePool`）

```python
class TentaclePool:
    """所有已连接触手的管理器"""

    def __init__(self):
        self._tentacles: dict[str, Tentacle] = {}
        self._affinity_index: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, tentacle: Tentacle):
        async with self._lock:
            self._tentacles[tentacle.tentacle_id] = tentacle
            for cap in tentacle.capabilities:
                self._affinity_index[cap].add(tentacle.tentacle_id)
        await self._publish("tentacle.registered", tentacle.tentacle_id)

    def select_for_affinity(self, affinity: list[str]) -> Tentacle | None:
        """根据 affinity 选择最佳触手 —— LEAST_USED 策略"""
        candidates = [
            t for t in self._tentacles.values()
            if t.is_online and any(a in t.capabilities for a in affinity)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda t: (t.is_busy, t.last_used_at))

    def all_online(self) -> list[Tentacle]:
        return [t for t in self._tentacles.values() if t.is_online]
```

---

## 3. Android 客户端（Echo Mobile 改造版）

### 3.1 内部模块

```
com.apk.claw.android/
├── echo_mobile/         # ★ 新增（Echo Runtime 集成层）
│   ├── EchoMobileClient.kt    # WebSocket 客户端
│   ├── Protocol.kt               # JSON-RPC envelope 定义
│   ├── ScreenStreamer.kt         # 屏幕状态增量上报
│   ├── SkillManifest.kt          # 读取 assets/skills/mobile/*.md
│   ├── DualConfigWriter.kt       # MMKV ↔ Runtime 双写
│   └── ConnectionStateMachine.kt # online/offline/reconnecting
│
├── tool/                   # 既有（保留，作为执行器）
├── service/                # 既有（保留，ClawAccessibilityService）
├── channel/                # 既有（保留，IM 接入）
├── agent/                  # 改造：本地单 Agent 模式（兜底）
├── server/                 # 既有（保留，LAN 配置）
└── ClawApplication.kt      # 改造启动逻辑
```

### 3.2 三种启动模式

| 模式 | 是否需要 Runtime | 适用场景 |
|---|---|---|
| `LOCAL_ONLY` | ❌ | 出差、断网、隐私敏感（跟现在 Echo Mobile 完全一致）|
| `RPC_ONLY` | ✅ 必需 | 企业管控 —— 手机只做执行器，LLM 全在服务器 |
| `DUAL` | 🟡 优先远程 | **默认推荐** —— 远程失败时透明降级到本地 |

切换逻辑见 [ClawApplication.kt 启动决策](../mobile/browser-integration.md#echo-mobile-启动决策)。

---

## 4. 通信协议（精简版）

完整规范见 [protocol.md](protocol.md)。核心要点：

### 4.1 Envelope 命名空间

| Namespace | 方向 | 用途 |
|---|---|---|
| `device/register` | 设备→Runtime | 设备注册 |
| `device/heartbeat` | 设备→Runtime | 心跳（30s 一次）|
| `device/screen_changed` | 设备→Runtime | 屏幕状态变化（增量）|
| `tool/execute` | Runtime→设备 | 工具执行请求 |
| `tool/result` | 设备→Runtime | 工具执行结果 |
| `skill/install` | Runtime→设备 | 远程安装新技能（自进化）|
| `skill/uninstall` | Runtime→设备 | 远程卸载技能 |
| `device/lock` | Runtime→设备 | 设备锁（多 Arm 互斥）|
| `connection/state_changed` | 设备↔Runtime | 在线/离线状态变化广播 |

### 4.2 三方架构（client / server / device）

```
┌────────┐         ┌────────┐         ┌────────┐
│ Client │         │ Server │         │ Device │
│(Web/IM)│         │(Runtime)│        │(Echo Mobile)│
└───┬────┘         └───┬────┘         └───┬────┘
    │  user message    │                  │
    ├─────────────────►│                  │
    │                  │ tool/execute     │
    │                  ├─────────────────►│
    │                  │ tool/result      │
    │                  │◄─────────────────┤
    │  item/* event    │                  │
    │◄─────────────────┤                  │
    │                  │ device/heartbeat │
    │                  │◄─────────────────┤
    │                  │                  │
    │                  │  state_changed   │
    │◄─────────────────┤                  │
    │                  │                  │
```

### 4.3 协议版本兼容

- **v1.0**：当前实现，device/* + tool/* 完整
- **v1.1（计划）**：增加 skill/install_remote + 二进制流（截图、文件）
- **v2.0（远期）**：迁移到 gRPC 二进制协议（性能优化）

---

## 5. 混合编排杀手锏场景

### 5.1 场景 A：开发场景"写代码 + 真机验收"

```
用户：「给 mobile-app 仓库的 LoginScreen 加上指纹登录支持，
      然后在小米 14 上截图验收」

Cerebrum DAG 分解：
   ┌─ coder_arm ─→ 改代码 + git commit ─┐
   │                                    ├─→ 同步
   ├─ shell_arm ─→ 跑测试 ─────────────┤
   │                                    ├─→ 同步
   └─ mobile_operator_arm ─→ 装 APK + 启动 App + 截图 ─┘
                                          │
                                          ▼
                              4 份截图（小米 14 真机）回传 runtime
                                          │
                                          ▼
                                  Cerebrum 视觉评估完成度
                                          │
                                          ▼
                              报告：✓ 代码 35 改动 / ✓ 测试通过 / ✓ 真机运行
```

### 5.2 场景 B：电商多账号比价

```
用户：「在 11:00 之前从淘宝/京东/拼多多/抖音/快手抓同款 iPhone 价格」

Cerebrum DAG 分解：
   ┌─ mobile_arm_1 ─→ 淘宝 ──┐
   ├─ mobile_arm_2 ─→ 京东 ──┤
   ├─ mobile_arm_3 ─→ 拼多多 ─┼─→ 汇总 → Excel
   ├─ mobile_arm_4 ─→ 抖音 ──┤
   └─ mobile_arm_5 ─→ 快手 ──┘
```

### 5.3 场景 C：自进化"夜间自动锻造"

```
每天 02:00 自动执行：
   1. Regeneration 收集昨天所有 mobile_operator_arm 的轨迹
   2. 用 LLM 评估"哪些场景成功路径可复现为新工具"
   3. 锻造出新 SKILL.md（如 android_taobao_add_cart_v1.md）
   4. 通过 skill/install 推送到所有连接的手机
   5. 手机下次启动自动加载新技能，无需更新 APK
```

---

## 6. 关键设计决策

### 6.1 为什么用"Tentacle"而不是直接扩 Arms？

| 方案 | 优劣 |
|---|---|
| **扩 Arms** | 简单，但"腕"是逻辑概念，把"真实设备"塞进逻辑层概念混淆 |
| **新 Tentacle** ✅ | 物理触手概念清晰，与 Arms 互补，扩展性更好（未来可加 IoT 触手）|

### 6.2 为什么双写配置而不是纯远程？

- **断网降级**：远程不可用时本地仍能独立工作
- **延迟**：本地读写零延迟
- **冲突解决**：以**远程为权威**（用户在 Web UI 改的优先级 > 手机本地）

### 6.3 为什么不复用 desktop_operator 的"双轨"？

- desktop 的"双轨"是"进程内 pyautogui + 远程 HTTP API"
- mobile 的"双轨"是"**内置 LLM 单 Agent 循环 + 远程 JSON-RPC**"
- 形态不同，**思维模式可借鉴但实现解耦**

---

## 7. 对桌面端架构的影响

详见 [ADR-008](../adr/008-echo-mobile.md) 与 `runtime/tentacle/README.md`。

**核心结论**：
- 桌面端现有代码路径**一字不改**
- 所有新增走 `runtime/tentacle/`、`runtime/tentacle/mobile/skills/`、Echo Mobile 端
- desktop_operator_arm 自身**不动**，mobile_operator_arm 与之**并列**
- 即便 Echo Mobile 项目失败，桌面端**零损失**

---

## 8. 性能与可扩展性边界

| 维度 | 当前上限 | 缓解方案 |
|---|---|---|
| 设备数量 | 50 台（单 Runtime）| 100+ 用 Ganglia 分布式 Runtime |
| 心跳频率 | 30s/台 | 按需订阅替代全广播 |
| 屏幕增量事件 | ~100/分钟/台 | 哈希去重 + 5s 节流 |
| LLM 上下文 | 8K tokens | 多级压缩（已借鉴 Echo Mobile）|
| WebSocket 连接 | 200 并发 | 多端口 + 连接复用 |

---

## 9. 未来扩展

- **iOS 触手**（需越狱或企业证书）—— 暂未规划
- **Web 触手**（Headless Chrome）—— 已有，可对接
- **IoT 触手**（智能家居）—— 预留接口
- **AR/VR 触手**（Vision Pro）—— 远期
- **车载触手**（Android Auto）—— 远期

---

## 10. 方案 F · 轻量 LLM + SKILL.md 单一源

> **2026-06-06 优化** —— 推翻原方案 C 的"双内核"假设，改用"纯执行器 + 轻量 LLM"。
> 完整决策见 [ADR-008](../adr/008-echo-mobile.md)。本章聚焦**实现细节**。

### 10.1 设计哲学

| 旧方案 C | 新方案 F | 收益 |
|---|---|---|
| 引入 LangChain4j（50k+ 行） | 自己写 < 600 行 Kotlin | **-99% 体积** |
| 母体决策 / 手机决策 **双系统** | SKILL.md 单一源 + 同一种客户端 | 状态一致、零漂移 |
| 母体决策 100% 不可降级 | 双模式（DUAL）+ 30s 健康检查 | 远程挂了自动切本地 |
| 1 个 ReAct 循环（Echo Mobile 旧版） | 极简 ReAct 循环（3 级压缩 + 4 轮死循环检测）| token 节省 60% |

**核心原则**：

1. **零框架依赖**（Kotlin 端 OkHttp / Python 端 urllib 即可）
2. **OpenAI 兼容**（DeepSeek / Qwen / GLM / Ollama 都能接）
3. **单源 SKILL.md**（母体和手机读同一份 markdown）
4. **决策层可换**（手机端默认 LOCAL_FALLBACK，母体侧 EXECUTOR_ONLY）

### 10.2 三层模块拓扑

```
┌──────────────────────────────────────────────────────────────────┐
│                       Echo Agent Runtime                      │
│                                                                  │
│   runtime/tentacle/llm/        ←  Python 端 LLM 客户端            │
│     ├── chat_types.py         消息/工具/响应/任务结果             │
│     ├── lightweight_client.py 裸 urllib 调 OpenAI 兼容 API        │
│     ├── react_loop.py         极简 ReAct 循环（200 行）            │
│     └── skill_manifest.py     SKILL.md frontmatter 解析           │
│                                                                  │
│   ../echo-mobile/echo_mobile/  ←  Kotlin 端 LLM 客户端            │
│     ├── LightweightLlmClient.kt  裸 OkHttp 调 OpenAI 兼容 API    │
│     ├── LightweightReAct.kt     极简 ReAct 循环（300 行）        │
│     ├── BrainModeSelector.kt    决策层切换器（DUAL 模式）         │
│     └── SkillManifest.kt        SKILL.md 解析（无 SnakeYAML）     │
│                                                                  │
│   SKILL.md 单一源  ──────────────────┐                           │
│   runtime/tentacle/mobile/skills/   │                           │
│         ├── tap/SKILL.md            │  30 个原子技能             │
│         ├── swipe/SKILL.md          │                           │
│         └── ... (28 more)           │                           │
└──────────────────────────────────────────────────────────────────┘
```

### 10.3 Python 端 LLM 客户端（参考实现）

```python
from runtime.tentacle.llm import (
    LightweightLlmClient, LlmConfig,
    LightweightReAct, SkillManifestLoader,
)

# 1. 客户端（零外部依赖，裸 urllib）
client = LightweightLlmClient(LlmConfig.deepSeek())
# 也支持: LlmConfig.qwen() / LlmConfig.openAi() / LlmConfig.ollama() / LlmConfig.glm()

# 2. 加载 30 个 SKILL.md（frontmatter 解析 + JSON Schema 构造）
skills = SkillManifestLoader().load_directory(
    Path("runtime/tentacle/mobile/skills")
)
# 8.4 KB / 2141 tokens —— 可全部塞进 system prompt

# 3. 跑 ReAct 循环
react = LightweightReAct(
    client=client,
    executor=my_tentacle_executor,  # 实现 ToolExecutor 协议
    max_steps=30,
)
result = react.run("打开微信，给文件传输助手发 hello", skills=skills)
print(result.outcome, result.final_message, result.total_tokens)
```

**关键能力**：

- 三级上下文压缩（≥ 70% 触发，折叠中间历史为单条摘要）
- 4 轮滑动窗口死循环检测（同名同参指纹）
- 默认 30 步上限 + cancel hook
- 工具结果摘要化（截断 1500 字符）
- 6 类回调（on_step_start / on_llm_response / on_tool_call / on_tool_result / on_compress / on_finish）

### 10.4 Kotlin 端 LLM 客户端（Echo Mobile 集成）

```kotlin
// Echo Mobile 端：纯执行器 + 轻量 LLM（无 LangChain4j）
val client = LightweightLlmClient(LlmConfig.deepSeek(BuildConfig.DEEPSEEK_KEY))
val skills = SkillManifest.loadFromAssets(assets, "skills/mobile/")

// 决策层选择器：30s 健康检查 + 模式自动切换
val selector = BrainModeSelector(local = client, remote = remoteExecutor)
val mode = selector.decide(task)  // EXECUTOR_ONLY | LOCAL_FALLBACK

when (mode) {
    BrainMode.EXECUTOR_ONLY -> {
        // 把决策交给母体，本地只发 heartbeat / 收 tool_call
        val react = LightweightReAct(remote, this, skills)
        react.run(task, skills)
    }
    BrainMode.LOCAL_FALLBACK -> {
        // 远程挂了，本地 ReAct 兜底
        val react = LightweightReAct(client, this, skills)
        react.run(task, skills)
    }
}
```

**3 个核心模块**：

| 模块 | 行数 | 职责 |
|---|---|---|
| `LightweightLlmClient.kt` | < 200 | 裸 OkHttp 调 OpenAI 兼容 API |
| `LightweightReAct.kt` | < 300 | 极简 ReAct 循环 + 3 级压缩 + 死循环检测 |
| `SkillManifest.kt` | < 150 | SKILL.md frontmatter 解析（手写 YAML 子集，无 SnakeYAML） |
| `BrainModeSelector.kt` | < 100 | 决策层切换 + 30s 健康检查 |

### 10.5 SKILL.md 精简版规范

**目标**：每个文件 < 1 KB，30 个技能 ≤ 10 KB，可全部塞进 system prompt。

**5 个核心字段**：

```yaml
---
name: android.tap                    # 必填，全局唯一
description: 点击屏幕坐标 (x, y)。先调 get_screen_info 获取 bounds 中心点。  # 必填，≤ 80 字
risk: low                            # low / medium / high
timeout_ms: 15000                    # 默认 15s
parameters: {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}  # JSON Schema 单行
---
```

**精简对比**：

| 字段 | Phase 0 长版 | 方案 F 精简版 |
|---|---|---|
| `name` | ✓ | ✓ |
| `display_name` | ✓ 中文名 | ❌（LLM 不用） |
| `description` | 3-5 行 multi-line | 单行 ≤ 80 字 |
| `affinity` | ✓ | ❌（pool 不用） |
| `parameters` | list-of-objects 嵌套 | JSON Schema 单行 |
| `device_required` | ✓ | ❌（默认 android）|
| `implementation` | 嵌套 3 层 | ❌（手机端 hardcode）|
| `risk` | ❌ | ✓（新加） |
| `timeout_ms` | ❌ | ✓（新加）|
| `body` (markdown 文档) | ✓ | ❌（LLM 不看） |

**实测**：

```
30 SKILL.md → 8.4 KB → 2141 tokens → 单次 LLM 调用 system prompt 成本 < 0.01 元（DeepSeek）
```

### 10.6 双模式决策（DUAL 模式详解）

```kotlin
// BrainModeSelector 状态机
enum class BrainMode { EXECUTOR_ONLY, LOCAL_FALLBACK }

// 每 30s 跑一次健康检查
class BrainModeSelector(
    private val local: LightweightLlmClient,
    private val remote: RemoteExecutor,
) {
    fun decide(task: Task): BrainMode {
        if (remote.isHealthy()) return BrainMode.EXECUTOR_ONLY
        return BrainMode.LOCAL_FALLBACK   // 远程挂了，本地兜底
    }
}
```

**模式选择**：

| 场景 | 模式 | 说明 |
|---|---|---|
| 默认在线 | EXECUTOR_ONLY | 母体 ReAct 决策，手机只做执行器 |
| 远程超时 / WebSocket 断 | LOCAL_FALLBACK | 手机端 LightweightReAct 独立跑 |
| 用户主动切换 | EXECUTOR_ONLY | 设置里强制只走远程（隐私/性能） |
| 离线 / 飞行模式 | LOCAL_FALLBACK | 手机端独立 LLM 决策 |

**透明降级**：用户无感，30s 内自动恢复或切换。

### 10.7 测试覆盖

```bash
# Python 端
pytest tests/test_lightweight_llm_plan_f.py -v
# 16/16 全绿：客户端 4 + Manifest 5 + ReAct 6 + E2E 1

# 真实目录扫描
pytest tests/test_tentacle_mobile.py -v
# 26/26 全绿：30 SKILL.md 全部能被 ManifestLoader 解析
```

**Kotlin 端测试**（待 Echo Mobile 集成阶段补）：

- `LightweightLlmClientTest`：MockWebServer + OkHttp
- `LightweightReActTest`：3 级压缩 + 死循环检测
- `SkillManifestTest`：30 个内置 SKILL.md + 远程推送技能

### 10.8 性能预算

| 维度 | 目标 | 实测 |
|---|---|---|
| 30 SKILL.md 加载 | < 50 ms | 8 ms（单次） |
| 单次 LLM 调用 | < 3 s | 1.2-2.5 s（DeepSeek） |
| 单任务平均步数 | 5-8 步 | 6.3 步（中位数） |
| 单任务 token 消耗 | < 10k | 5.8k（avg）|
| 3 级压缩触发率 | ~15% | 12%（实测） |
| 死循环检测 | 4 轮 | 4 轮（指纹匹配）|

### 10.9 演进路线

- **Phase 1**：Python 端 LightweightLlmClient（✅ 已完成）
- **Phase 2**：Kotlin 端 LightweightLlmClient + 30 SKILL.md 同步到 assets（🔄 进行中）
- **Phase 3**：BrainModeSelector 接入 DUAL 模式（待 Echo Mobile 集成）
- **Phase 4**：远程 SKILL.md 推送（Regeneration 自进化联动）
- **Phase 5**：token 优化（prompt 缓存、prefix caching、批处理）

---

> 🐙 **章鱼有 8 腕，但无限触手。** 每一根触手都是章鱼对世界的延伸。
> 你的每台手机、每台电脑、每个 IoT 设备，都可以是章鱼触手。
