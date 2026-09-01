# ADR-008 · Echo Mobile（移动触手 / 跨端编排）

Status: Accepted (Phase 0) · Updated: 2026-06-06 (方案 F 优化)
Date: 2026-06-06 (initial) | Last revision: 2026-06-06 (F-optimization)

## Context

echo-agent 已有 `desktop_operator_arm` —— 用 pyautogui / 屏幕截图 + 鼠标键盘
控制**本地桌面**。但随着移动设备成为主流生产工具，**手机端 AI 控制**和
**跨端协同**是用户真实需求（参考 2026-05 字节 Trae Solo 推出手机+桌面+Web 三端
互通，2026-05-29 OpenAI Codex 26.527 让手机远程遥控 Windows 电脑）。

我们面对三种可能的方案：

**A. 走纯 Echo Agent 自己的"computer_use_loop"路线**——用 vision 截图
   + pyautogui 等价物在 Android 上实现。
   优点：架构统一。
   缺点：包大（需打包完整 vision 框架）、性能差（截图每帧 2MB+）、
        反爬弱（WebView 默认暴露 webdriver）、不能装 Chrome 扩展。

**B. Fork 一个 Android 端独立 App**（如基于 Echo Mobile 改造）做本地单 Agent。
   优点：用户体验好、APK 小、运行快。
   缺点：与 Echo Agent Runtime 隔离，**没有跨端编排能力**，
        没法享受多 Arm 协作 / 自进化 / 预算治理。

**C. 新建"Tentacle"器官**（物理触手），与现有 Arms（逻辑腕）解耦，
   通过 WebSocket + JSON-RPC 2.0 让触手能接入 Runtime。
   优点：保留 Echo Mobile 现有 30 个 BaseTool 的成熟实现，复用 Echo Agent
        的多 Arm 协作、自进化、预算治理；**桌面端零破坏**。
   缺点：协议设计复杂、需要三方（client/server/device）协作。

## Decision

采用 **方案 C（架构基线） + 方案 F（决策层优化）**：

| 层 | 选型 | 原因 |
|---|---|---|
| **架构层** | C：Tentacle 器官 + WebSocket + JSON-RPC | 桌面端零破坏，跨端协同 |
| **决策层** | **F：纯执行器 + 轻量 LLM（无 LangChain4j）** | 见下文 |

### 关键认知升级（2026-06-06 修订）

**原来的方案 C 假设**：手机端必须装 LangChain4j 才能做本地决策。
**新认知**：LangChain4j 是 50k 行的"通用 LLM 框架"，**你不需要**。
**真正需要的只是**：调 LLM API（200 行）+ ReAct 循环（300 行）+ SKILL.md 解析（100 行）= **< 600 行 Kotlin**。

### 关键决策点

1. **新器官命名**：Tentacle（触手），与 Arms（腕）并列
   - Arms = 逻辑能力单位（"我能做什么"）
   - Tentacle = 物理设备（"我在哪里做"）
   - 一个 Arm 跑在一个 Tentacle 上

2. **不破坏桌面端**：所有新增走 `runtime/tentacle/` + Echo Mobile 端改造。`runtime/execution/arms/presets.py` 的 `make_desktop_operator_arm` **一字不改**，
   仅追加 `make_mobile_operator_arm`。

3. **协议选择**：JSON-RPC 2.0 over WebSocket
   - 复用 echo-agent 现有 envelope（`item/*`, `cocoloop/*`）
   - 新增 `device/*` 和 `tool/*` 两个 namespace
   - 详见 [protocol.md](../../mobile/protocol.md)

4. **设备启动模式**：三种并存（LOCAL_ONLY / RPC_ONLY / DUAL）
   - LOCAL_ONLY：跟现在 Echo Mobile 完全一样，断网时仍能用
   - RPC_ONLY：纯执行器，LLM 全在服务器
   - DUAL（默认）：远程优先，断网透明降级到本地

5. **配置双写**：本地 MMKV（兜底）+ Runtime KV（主）
   - 写策略：本地写 + 远程写串行，远程失败入 retry queue
   - 读策略：本地优先，后台异步拉远程
   - 冲突解决：以远程为权威（用户在 Web UI 改的优先级 > 手机本地）

6. **浏览器内核（远期）**：可选集成 Chromium for Android（Kiwi 思路）
   - Phase 7 才做
   - 用 CDP 协议（Chrome DevTools Protocol）暴露能力
   - 7 个新技能：navigate / get_dom / click / type / screenshot / evaluate / install_extension

### 仿生学一致性

`tentacle` 复用 ADR-001 的双轨命名契约：

| 维度 | 触手（生物名）| 工程名 |
|---|---|---|
| 目录 | `runtime/tentacle/` | 同 |
| 类 | `Tentacle` 是别名 | `Tentacle` Protocol / `MobileDevice` 实现 |
| 函数 | — | `connect`, `execute`, `heartbeat` |
| 日志 | — | "Device connected" ✅（"Tentacle connected" ❌）|

详见 [naming.md](../../naming.md) 第 5-8 行"2026 learning-curve amendment"。

## Alternatives considered

**A. 纯 vision 路线**：被否决，包大、性能差、不能装 Chrome 扩展。
**B. 纯 fork 独立 App**：被否决，跨端协作/自进化能力缺失。
**C + 纯 vision 混合**：远期可考虑（小米 14 顶级设备 + vision 做 OCR 兜底），
  Phase 7 再评估。
**D. 用 UIAutomator（Android 官方）替代无障碍服务**：被否决，UIAutomator 只
  能在 test 模式下跑，生产环境被禁止。

## 方案 F 优化（2026-06-06 修订）

### 核心洞察

最初方案 C 假设手机端必须装 LangChain4j 才能做本地决策。重新审视后
发现：

> LangChain4j 在 Echo Mobile 中的**真实作用**只有 600 行 Kotlin 能覆盖：
> - LLM API 客户端（200 行 OkHttp）
> - ReAct 循环（300 行）
> - 工具注册桥接（100 行）
>
> **剩下 49,400 行是 LangChain4j 的"通用基础设施"**——本项目不需要。

### 方案 F 架构

```
┌──────────────────────────────────────────────────────────┐
│                   Echo Mobile · Android Client               │
│                                                          │
│  Channel Manager (IM 入口)                               │
│  RpcOperatorClient (RPC 通道)                            │
│         │                                                │
│         ▼                                                │
│  ┌──────────────────────────────────────────────────┐  │
│  │   决策层（两个实现可热切换）                       │  │
│  │   RemoteBrain (RPC → 母体)  ◄──► LightweightLocal │  │
│  │   默认在线时用                     离线时用        │  │
│  │            ▼                          ▼            │  │
│  │   ┌──────────────────────────────────────────┐    │  │
│  │   │   30 SKILL.md（单一事实源）              │    │  │
│  │   │   本地解析 → 喂本地轻量 LLM              │    │  │
│  │   │   同步给母体 → 母体决策                   │    │  │
│  │   └──────────────────────────────────────────┘    │  │
│  └──────────────────────────────────────────────────┘  │
│         │                                                │
│         ▼                                                │
│  动作层（30 BaseTool，跟现在 Echo Mobile 一模一样）          │
│  ClawAccessibilityService · ToolRegistry                │
└──────────────────────────────────────────────────────────┘
```

### 方案 F 选型对比

| 维度 | 方案 C（双内核）| 方案 F（轻量 LLM）|
|---|---|---|
| 新增代码 | ~2000 行 | **~500 行** |
| 新增依赖 | 强化 LangChain4j | **零** |
| 包大小 | +5MB | **+0MB** |
| 冷启动 | +50ms | **+0ms** |
| 离线能力 | ✅ 完整 | ✅ 完整 |
| 母体能力 | ✅ | ✅ |
| 状态一致性 | ⚠️ 双系统漂移 | ✅ 单一决策层 |
| 工具描述源 | 双描述 | **单源 SKILL.md** |
| 实施周期 | 2-3 周 | **1-2 周** |
| 维护成本 | 中 | **低** |

### 方案 F 实施清单

- [x] ADR-008 决策记录更新
- [ ] `LightweightLlmClient.kt`（< 200 行，裸 OkHttp 调 LLM API）
- [ ] `LightweightReAct.kt`（< 300 行，简易 ReAct 循环）
- [ ] `BrainModeSelector.kt`（决策层切换器）
- [ ] `SkillManifest.kt`（SKILL.md 解析器）
- [ ] 30 SKILL.md 精简版（极致压缩，节省 50% token）
- [ ] 集成测试 PoC（DeepSeek API + 简易 ReAct 跑通"打开微信"）
- [ ] Echo Mobile 现有 LangChain4j 代码**保留但不再使用**（兜底）

## Consequences

### 正面影响
- Echo Mobile 改造版可享受 Echo Agent 全栈能力（多 Arm、自进化、预算）
- 桌面端 90% 代码完全不动
- 30 个移动技能 100% 复用 Echo Mobile 现有实现
- 用户体验：本地模式跟现在 Echo Mobile 一模一样（无学习成本）
- 杀手锏：跨端混合编排（手机+电脑+多手机）

### 负面影响 + 缓解
1. **Nerves 总线事件量上升**（N 台手机 × 30s 心跳）→ 心跳 30s + 屏幕变化 5s 节流
2. **协议复杂度提升**（三方架构）→ Phase 0 早期专门做 1 天协议 review
3. **双写配置冲突** → 冲突检测 + 以远程为权威
4. **测试覆盖稀释** → Android 集成 test 单独 tag，CI 分段跑
5. **包大小 +80-100MB**（如启用浏览器内核）→ 动态下载，仅打包 arm64

### 关键不变量（受 [invariants.md](../../invariants.md) 约束）

- INV-1：所有 touch 操作必须经过 Safety / Approval Gate（关键操作）
- INV-2：Echo Mobile 自身的安全规则 10 条必须保留（prompt 层兜底）
- INV-3：设备锁（device/lock）由 Runtime 统一管理，Echo Mobile 端只执行

## Implementation Phases

详见 [roadmap.md](../../roadmap.md) 阶段 5「触手期 / 跨端期」。

| Phase | 周期 | 目标 |
|---|---|---|
| 0 | 3 天 | 概念验证：30+30 行代码 + add-only 假设 |
| 1 | 2 周 | 设备注册 + 心跳 + 简单工具执行 |
| 2 | 3 周 | 30 个移动技能完整接入 + Cerebrum 调度 |
| 3 | 2 周 | 双写配置 + 离线降级 |
| 4 | 2 周 | 屏幕状态增量上报 + 反爬基础 |
| 5 | 3 周 | 混合编排杀手锏场景 |
| 6 | 2 周 | 自进化闭环 |
| 7 | 4 周 | 浏览器内核集成（远期）|

## References

- [architecture.md](../../mobile/architecture.md) — 完整架构设计
- [protocol.md](../../mobile/protocol.md) — 协议规范
- [skills.md](../../mobile/skills.md) — 30 个移动技能
- [browser-integration.md](../../mobile/browser-integration.md) — 浏览器内核集成
- [biomimetic/tentacle/README.md](../../biomimetic/tentacle/README.md) — 触手器官说明
- [Echo Mobile 源码](https://github.com/echo-agent/echo-mobile) — Android 端实现参考
- ADR-001 — 双轨命名契约
