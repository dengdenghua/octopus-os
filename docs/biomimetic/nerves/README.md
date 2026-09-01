# 🔌 Nerves · 神经

**生物原型**：章鱼的神经纤维连接所有器官 —— 本架构中唯一的通信基础设施。

## 子目录
```
nerves/
├── graph/       [fork]  DAG 执行器（6 节点 / 4 边）
├── hooks/       [fork]  pre_tool_use / post_tool_use 钩子
└── bus/                 分布式消息（NATS 或 Redis Streams）
```

## 三条通路

1. **命令神经**（纵向）：Cerebrum → Ganglion → Arm → Sucker
2. **感觉神经**（上行）：Eyes / Skin → Hemolymph → Cerebrum
3. **横向神经**（腕间）：Arm ↔ Chromatophores ↔ Arm

## Graph 扩展节点/边（相对 echo 原版）

| 新增 | 类型 | 作用 |
|---|---|---|
| `ArmNode` | 节点 | 把子图整体交给某条 Arm 自主执行 |
| `ChromatophoreEdge` | 边 | 触发腕间广播而非直接调用 |

## 进化关联
**② 工作流** 的全部骨架。也是所有跨模块调用的唯一通道 —— 便于观测和限流。
