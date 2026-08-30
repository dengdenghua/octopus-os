# 🖥️ Tentacle · Desktop · 桌面触手

> **章鱼伸向桌面电脑的物理触手**（自指）

Desktop Tentacle 是一个"**自指**"的触手——它把 **Echo Agent Runtime
所在的那台机器本身** 包装为一根触手。

这听起来奇怪，但其实很合理：

- 在 Echo Agent Runtime 部署的机器上，`desktop_operator_arm` 跑 pyautogui
  是 "进程内" 控制
- 把它包成 Tentacle 后，**Cerebrum 可以用统一接口**（WebSocket + JSON-RPC）
  与之交互，与"远程手机触手"用同一套协议
- 也让 Cerebrum **真正意识到** "我的桌面也是根触手，我可以指挥它"

## 形态

```
┌─────────────────────────────────────────────────────┐
│                Echo Agent Runtime                 │
│   (Cerebrum + Ganglia + Arms + ...)                 │
│                                                     │
│   ┌─────────────┐       ┌─────────────┐            │
│   │ Desktop     │       │ Mobile      │            │
│   │ Operator    │       │ Operator    │   ← Arms   │
│   │ Arm         │       │ Arm         │            │
│   └──────┬──────┘       └──────┬──────┘            │
│          │                     │                    │
│          ▼                     ▼                    │
│   ┌─────────────┐       ┌─────────────┐            │
│   │ Desktop     │       │ Android     │            │
│   │ Tentacle    │       │ Tentacle    │  ← Tents   │
│   │ (本地)      │       │ (远程)      │            │
│   └──────┬──────┘       └──────┬──────┘            │
│          │                     │                    │
│          ▼                     ▼                    │
│   ┌─────────────┐       ┌─────────────┐            │
│   │ 本地桌面    │       │ 小米 14    │            │
│   │ (pyautogui) │       │ (Echo Mobile)  │            │
│   └─────────────┘       └─────────────┘            │
└─────────────────────────────────────────────────────┘
```

## 为什么需要 Desktop Tentacle

| 没有 Desktop Tentacle | 有 Desktop Tentacle |
|---|---|
| Desktop Arm 是"特殊公民"（进程内 pyautogui）| Desktop 是"普通触手"（同 Mobile 同等待遇）|
| Cerebrum 调度逻辑要特判 | 统一接口 |
| 跨设备协同时代码重复 | 统一抽象 |

## 实现要点

Desktop Tentacle 与 Mobile Tentacle 用**同一接口**：

```python
class DesktopTentacle:
    """本地桌面触手 —— 自指"""

    tentacle_id: str   # "desktop-{hostname}"
    tentacle_type: TentacleType.DESKTOP
    platform: str     # macos / windows / linux

    async def connect(self) -> None:
        # 不需要真正的连接（localhost）
        # 但要走 Nerves 总线注册
        ...

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        # 直接转给 desktop_operator_arm 内的 11 个 skills
        return await self._arm.execute(tool_call)
```

## 当前状态

⏳ **待实现**。Phase 5（混合编排杀手锏）期间实现。

优先级：低 —— Mobile Tentacle 价值更直接，Desktop Tentacle 是"锦上添花"
的统一抽象。

## 与现有 desktop_operator_arm 的关系

- **不替换** desktop_operator_arm
- **包装** desktop_operator_arm 为 Tentacle
- 跑 desktop_operator_arm 的代码路径 100% 复用

```python
# 初始化示例
desktop_tentacle = DesktopTentacle(
    tentacle_id=f"desktop-{socket.gethostname()}",
    platform=sys.platform,
    arm=desktop_operator_arm,  # 复用
)
await tentacle_pool.register(desktop_tentacle)
```

## 未来扩展

- **Remote Desktop Tentacle**：远程桌面（如 RDP/VNC），跨网络控制
- **Cloud Desktop Tentacle**：云端桌面（如 AWS WorkSpaces）

---

> 🖥️ **桌面触手是章鱼的家 —— 但家也是世界的一部分。**
