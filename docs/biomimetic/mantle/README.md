# 🛡️ Mantle · 外套膜

**生物原型**：章鱼外套膜包裹所有内脏，是身体与外界的隔离层。

## 职责
沙箱与安全边界。每条 Arm 默认进入独立 Mantle，互不污染。

## 四种 Provider（全部 fork 自 echo）

| 目录 | 场景 |
|---|---|
| `local/` | 开发调试 |
| `docker/` | 生产默认 |
| `ssh/` | 跨机器远程执行 |
| `k8s/` | 弹性集群部署 |

## 接口
```python
class Mantle:
    def enter(self, arm_id: str) -> SandboxContext: ...
    def bite(self, ctx, sucker, args) -> BiteResult: ...   # Beak 实际调 enter + bite
```

## 一条铁律
**Beak 永远不能在 Mantle 外执行**。即使是"读一个只读文件"也要进 Mantle。

## 进化关联
不直接对应某个进化模块，但是 **⑥ 成本治理** 和 **② 工作流**的安全底座 —— 没有它，Ink 的熔断也保不住失控的命令。
