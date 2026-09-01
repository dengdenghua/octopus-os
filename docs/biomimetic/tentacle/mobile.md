# 📱 Tentacle · Mobile · 手机触手

> **章鱼伸向 Android 设备的物理触手**

Mobile Tentacle 是 Echo Mobile 的一等公民。它让章鱼的中枢（Cerebrum）能
**真实操控** Android 手机——点屏幕、滑屏幕、装 App、读屏幕、自动化任何
用户操作。

## 形态与生命周期

```
┌──────────────────────────────────────────────────────────────────┐
│                       手机触手生命周期                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 安装    Echo Mobile APK 安装到 Android 设备                      │
│  2. 启动    ClawApplication 启动 → 决策启动模式（LOCAL/RPC/DUAL）│
│  3. 注册    手机通过 WebSocket 注册到 Runtime                    │
│  4. 心跳    30s 一次心跳（在线状态、电量、当前 App）             │
│  5. 屏幕    屏幕变化时增量上报（5s 节流 + 哈希去重）             │
│  6. 执行    接收 Runtime 的 tool/execute 消息 → 路由到 BaseTool  │
│  7. 锁/解锁 接收 Runtime 的 device/lock_* 消息                  │
│  8. 技能更新 接收 Runtime 的 skill/install 消息                  │
│  9. 离线    网络断开 → 切换到 LOCAL 模式（兜底）                │
│  10. 重连   网络恢复 → 自动重连 + 重新注册                       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 关键能力声明（affinity）

手机触手在 `capabilities` 字段中声明它支持的技能，让 Cerebrum 在任务派发
时知道**这根触手能做什么**。

```json
{
  "capabilities": [
    "android.tap", "android.swipe", "android.input_text",
    "android.get_screen_info", "android.take_screenshot",
    "android.open_app", "android.wait", "android.system_key",
    "android.browser.navigate", "android.browser.get_dom"
  ]
}
```

完整 30 个移动技能见 [skills.md](../../mobile/skills.md)。

## 仿生学映射

| 现实 | 仿生 | 实现 |
|---|---|---|
| Android 屏幕 | 章鱼的"眼睛" | `ClawAccessibilityService` + 截图 |
| 手指点击 | 章鱼的"吸盘" | `dispatchGesture` |
| 网络连接 | 章鱼的"神经脉冲" | WebSocket |
| 后台保活 | 章鱼的"墨囊" | `ForegroundService` + `KeepAliveJobService` |
| 配置存储 | 章鱼的"记忆" | MMKV（本地）+ Runtime KV（远程）|

## 关键不变量

1. **工具调用必须可中断**：长时工具（wait、scroll_to_find）支持 cancel
2. **屏幕状态必须一致**：手机端与 Runtime 的屏幕视图哈希必须最终一致
3. **设备锁必须互斥**：同一时刻一根触手只允许一个 Arm 操作
4. **失败必须可降级**：Runtime 不可达时无缝降级到 LOCAL 模式

详见 [ADR-008](../../adr/008-echo-mobile.md) 第 "关键不变量" 节。

## 启动模式详解

| 模式 | 触发条件 | LLM 跑哪 | 屏幕数据走哪 | 离线行为 |
|---|---|---|---|---|
| `LOCAL_ONLY` | 用户配置强制 | 手机本地 | 手机本地 | — |
| `RPC_ONLY` | 用户配置强制 | Runtime | Runtime | **完全失能**（无法工作）|
| `DUAL`（默认）| 自动 | Runtime 优先 | Runtime 优先 | **降级到 LOCAL** |

**DUAL 模式的状态机**：

```
                    ┌──────────┐
        ┌──────────►│  OFFLINE │◄──────────┐
        │           │ (本地模式)│           │
        │           └────┬─────┘           │
   WebSocket          检测到             WebSocket
   连接成功            WebSocket           断开
        │           ▲     │                 │
        │           │     ▼                 │
        │     ┌─────┴──────┐                │
        └─────┤  CONNECTING├────────────────┘
              │  (重连中)   │
              └─────────────┘
```

## 与 Android 客户端的关系

| Android 客户端现状 | Echo Mobile 改造 |
|---|---|
| 内置 `DefaultAgentService`（含 LLM 调用循环）| 保留为 LOCAL 模式，**不被删** |
| 6 个 IM 渠道（DingTalk/WeChat/Feishu/QQ/Discord/Telegram）| 全部保留 |
| 30 个 `BaseTool` 实现 | 全部保留，**但也支持 RPC 调用** |
| MMKV 配置存储 | 升级为 MMKV（兜底）+ Runtime KV（主）双写 |
| LAN 配置（9527 端口）| 保留作为本地调试入口 |

**Android 客户端的所有现有代码 100% 保留**，Echo Mobile 只是**新增**了
`echo_mobile/` 子包 + 启动模式决策逻辑。

## 实现细节

详见 [architecture.md](../../mobile/architecture.md) 第 3 节。

关键文件（在同级 checkout `../echo-mobile/app/src/main/java/com/apk/claw/android/echo_mobile/`）：

| 文件 | 职责 |
|---|---|
| `EchoMobileClient.kt` | WebSocket 客户端（OkHttp）|
| `Protocol.kt` | JSON-RPC envelope Kotlin data class |
| `HeartbeatReporter.kt` | 心跳上报（30s 周期）|
| `ScreenStreamer.kt` | 屏幕状态增量上报 |
| `ToolCallDispatcher.kt` | 接收 tool_call → 路由到 BaseTool |
| `SkillManifestExporter.kt` | 工具→SKILL.md 描述生成 |
| `DualConfigWriter.kt` | MMKV ↔ Runtime 双写 |
| `ConnectionStateMachine.kt` | online/offline/reconnecting 状态机 |

## 测试策略

- **单元测试**（`tests/test_tentacle_mobile.py`）—— Mock Android 设备，验证协议
- **集成测试**（`tests/test_mobile_e2e.py`）—— 真机或模拟器（slow test，CI 不强制）
- **兼容性测试**（`tests/test_echo_mobile_compat.py`）—— Echo Mobile 30 个 BaseTool 100% 兼容

---

> 📱 **手机触手 = 章鱼指尖的延伸 —— 触屏即触手，触手即意志。**
