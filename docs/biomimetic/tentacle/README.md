# 🐙 Tentacle · 触手

> **生物原型**：章鱼有 8 腕，但章鱼能把任一腕伸出去 1 米远，抓住远处的目标。
> 章鱼的"腕"既是**逻辑能力**（"我能做什么"）也是**物理肢体**（"我在哪里做"）。
> Echo Agent 把这两层**解耦**了：
>
> - **Arms**（腕）= 逻辑能力单位（"我能写代码"、"我能控制桌面"）
> - **Tentacle**（触手）= 物理设备（"我在小米 14 上做"、"我在 MacBook 上做"）
>
> 一个 Arm 跑在一个 Tentacle 上。一根 Tentacle 可承载多个 Arm（不同时段）。
> 一个任务可跨多根 Tentacle 协作。

## 与 Arms 的关系

| 维度 | Arms（腕）| Tentacle（触手）|
|---|---|---|
| 抽象 | 逻辑能力 | 物理设备 |
| 数量 | 6+ preset（无限可加）| 0~N（用户/设备决定）|
| 创建者 | 代码（`presets.py`）| 运行时（设备接入）|
| 生命周期 | 进程内 | 跨进程 / 跨网络 |
| 通讯 | 进程内函数调用 | WebSocket + JSON-RPC 2.0 |
| 能力 | `allowed_skills` 列表 | `capabilities` 列表（运行时上报）|
| 失败处理 | Retry / Budget | 重连 / 设备锁 / 离线降级 |

## 仿生学隐喻

```
           ┌────────────┐
           │  Cerebrum  │      ← 中枢
           │  (Planner) │
           └─────┬──────┘
                 │ 派发
       ┌─────────┼─────────┐
       ▼         ▼         ▼
   ┌──────┐  ┌──────┐  ┌──────┐
   │Arm 1 │  │Arm 2 │  │Arm 3 │    ← Arms（逻辑腕）
   │coder │  │desktop│ │mobile│
   └──────┘  └───┬──┘  └───┬──┘
                 │          │
                 ▼          ▼
           ┌────────┐  ┌────────┐
           │MacBook │  │小米 14 │   ← Tentacles（物理触手）
           │  (Mac) │  │(Android)│
           └────────┘  └────────┘
```

> 类比：一个 Arm 是一根"逻辑腕"，
> 它伸出去抓住一台真实设备 = "Tentacle"。
> 章鱼能伸出 8 腕，但**每根腕能触达 1 台设备**，
> 所以实际上章鱼可触达的设备数 = 触手表（pool）的容量。

## 子目录

```
tentacle/
├── base.py            # Tentacle 抽象协议 + TentacleType 枚举
├── pool.py            # TentaclePool：设备池管理（多设备协调）
├── mobile.py          # MobileDevice（Android 设备实现）
├── desktop.py         # DesktopDevice（桌面端自指：把本地桌面包装为触手）
├── apks/              # Echo Mobile 集成相关
│   ├── __init__.py
│   ├── skill_export.py   # 把 30 个 BaseTool 转 SKILL.md
│   ├── tool_bridge.py    # 工具调用桥接（JSON-RPC envelope）
│   └── version.py        # Echo Mobile 端版本兼容
└── transport/         # 通讯层
    ├── __init__.py
    ├── websocket.py      # WebSocket 客户端
    └── envelope.py       # JSON-RPC 2.0 envelope
```

## 核心接口

```python
# runtime/tentacle/base.py
class Tentacle(Protocol):
    """物理触手 —— 章鱼伸出去的"真实肢体" """

    # 身份
    tentacle_id: str
    tentacle_type: TentacleType  # MOBILE / DESKTOP / IOT / TV
    platform: str                # android / macos / windows / linux
    meta: dict

    # 能力（动态上报）
    capabilities: list[str]

    # 生命周期
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def heartbeat(self) -> Heartbeat: ...
    async def execute(self, tool_call: ToolCall) -> ToolResult: ...

    # 状态
    @property
    def is_online(self) -> bool: ...
    @property
    def is_busy(self) -> bool: ...
```

详见 [architecture.md](../../mobile/architecture.md)。

## 当前已实现的触手

| 触手 | 形态 | 状态 | 文档 |
|---|---|---|---|
| **MobileDevice** | Android 设备（基于 Echo Mobile 改造）| ✅ Phase 0 | [mobile.md](mobile.md) |
| **DesktopDevice** | 本地桌面自指 | ⏳ 计划 | [desktop.md](desktop.md) |

## 未来触手

- **IoTDevice**（智能家居 / 路由器 / 摄像头）—— 远期
- **TVDevice**（Android TV / Apple TV）—— 远期
- **ARDevice**（Vision Pro / Meta Quest）—— 远期
- **CarDevice**（Android Auto / HarmonyOS 座舱）—— 远期

## 与其他器官的关系

| 器官 | 关系 |
|---|---|
| **Eyes** | Tentacle 上报屏幕状态 → Eyes 喂给 LLM |
| **Arms** | Arm 跑在 Tentacle 上（一个 Arm 可跨多个 Tentacle）|
| **Suckers** | Tentacle 的 capabilities 是 Sucker ID 列表 |
| **Hearts** | 多 Tentacle 协同时由 Hearts 提供分布式锁 |
| **Nerves** | Tentacle ↔ Runtime 通讯走 Nerves 总线（WebSocket 帧）|
| **Ink** | Tentacle 执行受 Ink 预算治理 |
| **Immunity** | Tentacle 注册必经 Immunity 鉴权 |
| **Regeneration** | Tentacle 上的成功轨迹可被 Regeneration 锻造成新 Sucker |

## 设计原则

1. **设备无关**：Tentacle 抽象**不暴露**任何设备特定 API，所有设备
   能力通过 SKILL.md 描述。
2. **离线友好**：Tentacle 必须能在断网时降级工作（Echo Mobile 本地模式）。
3. **状态可观测**：所有 Tentacle 状态通过 Nerves 总线广播，Cerebrum 实时可见。
4. **能力可扩展**：新 Tentacle 类型（iOS、IoT）只需实现 6 个方法即可接入。
5. **失败可恢复**：网络抖动 / 设备锁死 / 进程崩溃，统统可重连可恢复。

## 测试策略

- `tests/test_tentacle_*.py` — 触手基类、池化
- `tests/test_mobile_tentacle.py` — 手机触手（mock Android）
- `tests/test_desktop_tentacle.py` — 桌面触手
- `tests/test_tentacle_e2e.py` — 端到端（runtime + 真机，slow test）

---

> 🐙 **章鱼的 8 腕是有限的，触手是无限的。**
> 每一根触手都是章鱼对世界的延伸 —— 你的每台手机、每台电脑、每个 IoT 设备。
