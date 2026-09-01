# 🕸️ Ganglia · 神经节

> ⚠️ **状态：未实装** — `runtime/ganglia/` 目录不存在，`class Ganglion` 零命中。Arm 当前是纯执行单元，无独立规划或断联自治能力。本文档描述的是设计愿景。

**生物原型**：章鱼每条腕根部的神经节，是腕的"小脑"，能独立完成抓取动作。

## 职责
- 每条 Arm 配一个 Ganglion
- 把 Cerebrum 下发的 `ArmTask` 翻译成 Sucker 调用序列
- 本地 Checkpointer（指向 Genome）+ 本地预算护栏（指向 Ink）
- **断联自治**：Cerebrum 不可达时照常跑已接手任务

## 为什么独立于 Arm
Arm 是"业务人格"，Ganglion 是"执行内核"。换人格（换 prompt）不换内核。

## 接口（草案）
```python
class Ganglion:
    arm_id: str
    def accept(self, task: ArmTask) -> None: ...
    def tick(self) -> ArmTickResult: ...     # Heart 心跳驱动
    def checkpoint(self) -> None: ...
```

## 进化关联
**① 长任务引擎** 的分布式执行层。

## 部署
独立进程 / 独立容器。Hearts 的节律 tick 驱动每个 Ganglion。

## 结构图

```mermaid
flowchart TB
    taskGraph([TaskGraph from Cerebrum])
    graphRuntime[<b>GraphRuntime</b><br/>单 arm 顺序执行<br/>template 解析 nX.field]
    swarmRuntime[<b>SwarmRuntime</b><br/>多 arm 并行<br/>ArmPool + 拓扑分层]
    armPool[(ArmPool<br/>按 skill affinity 分配)]
    ganglion["Ganglion 对象<br/>(per arm)"]
    beak[🐦 Beak.execute_step]
    journal[(📘 Journal<br/>node_started / step)]

    taskGraph --> graphRuntime
    taskGraph --> swarmRuntime
    swarmRuntime --> armPool
    armPool --> ganglion
    graphRuntime --> ganglion
    ganglion --> beak
    beak --> journal

    classDef runtime fill:#4a154b,stroke:#333,color:#fff
    class graphRuntime,swarmRuntime runtime
```
