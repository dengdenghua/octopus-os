# runtime.platform.models · MVP 数据契约

> 协议文档里的所有 `DataClass = { ... }` 伪代码**落成真正的 Python 类型**。
> 这是"**跨越理论—工程断层**"的第二块真代码（第一块是 tools/lint）。

## 覆盖范围（MVP 档）

| 协议文档 | 实现的模型 | 文件 |
|---|---|---|
| primitives (跨协议) | `Source`, `CostEntry`, ID 别名 | `primitives.py` |
| digestion.md 7 阶段 | `ParsedIntent`, `RouteDecision`, `TaskGraph`, `ArmAssignment`, `ArmResult`, `FinalResponse` | `pipeline.py` |
| immunity.md + digestion 执行 | `ToolCall`, `ExecutionResult`, `Step`, `Trajectory` | `execution.py` |
| recipe.md Hemolymph | `ContextPacket`, `QuotaAllocation`, `ContextSegment` | `context.py` |
| budget.md + immunity.md | `Budget`, `AntigenSignature`, `ImmuneReport`, `RiskScore` | `governance.py` |

**不覆盖**（Core 阶段再写）：
- Recipe / ForgedSkill / Workflow / RewritePatch
- GenomeDoc / Patch / CRDT types
- Assertion / Triple / SemanticMemory / ConflictRecord

## 设计选择

### 为什么 Pydantic v2（不是 dataclass）
- config.yaml 已声明 Pydantic 2.12+ 为运行时依赖
- 自动 schema validation → 直接守 DIG-I1（阶段契约严格）
- JSON 序列化零成本（Journal / A2A / MCP 都吃 JSON）

### 为什么大多数类 `frozen=True`
- 协议里的数据在阶段间流动；**不可变 = 不会被下游悄悄改**
- 每阶段要"改"就显式返回新实例 → 对应 digestion 的 in→out 契约

### 唯一的可变对象：`Budget`
- 它是**状态机**（reserve / commit / refund / freeze）
- 用 `threading.Lock` 保证 BDG-I2 的原子性
- 其他所有模型 frozen，只有这个例外

## 类图速览

```
RawInput → ParsedIntent ─┬─→ RouteDecision (reflex?)
                         └─→ TaskGraph ─────→ ArmAssignment ──→ ArmResult ──→ FinalResponse
                                                    │               │
                                                    ▼               ▼
                                              ContextPacket    Trajectory
                                                                    │
                                                                    ▼
                                                              [Step, Step, ...]
                                                                    │
                                                                    ▼
                                                              ToolCall + ExecutionResult

横切（所有阶段都依赖）：
  Source, CostEntry, TaskId, ArmId, SkillId
  Budget（状态机，EXECUTE 前 reserve / 后 commit）
  AntigenSignature + ImmuneReport（每个 ToolCall 走一趟）
```

## 使用示例

### 创建一个 Task 流水线

```python
from runtime.platform.models import (
    ParsedIntent, TaskGraph, TaskNode, BudgetSpec,
    Budget, BudgetLimits, CostEntry,
)

# 1. 用户输入被解析
intent = ParsedIntent(
    raw="修复这个测试失败",
    intent_type="debug",
    normalized_goal="fix failing test in tests/foo.py",
)

# 2. 规划成 TaskGraph
graph = TaskGraph(
    nodes=[
        TaskNode(node_id="n1", skill_ref="read_file"),
        TaskNode(node_id="n2", skill_ref="edit_code"),
        TaskNode(node_id="n3", skill_ref="run_pytest"),
    ],
    budget=BudgetSpec(tokens=50_000, usd=0.50),
)

# 3. 创建 Budget 账本
budget = Budget(
    task_id=graph.task_id,
    limits=BudgetLimits(tokens=50_000, usd=0.50),
)

# 4. 每次工具调用前 reserve
reservation = budget.reserve(CostEntry(tokens_in=500, tokens_out=200, usd=0.01))

# 5. 调用完 commit 实际花费
actual = CostEntry(tokens_in=480, tokens_out=210, usd=0.0098)
budget.commit(reservation, actual)
```

### Budget 护栏实测

```python
from runtime.platform.models import Budget, BudgetLimits, CostEntry, InsufficientBudget

b = Budget(task_id=..., limits=BudgetLimits(tokens=1000, usd=1.00))

# 撞顶会抛异常，不会悄悄超支（BDG-I1）
try:
    b.reserve(CostEntry(tokens_in=2000, tokens_out=0, usd=0))
except InsufficientBudget as e:
    print(f"blocked: {e}")  # prints the block

assert b.status == "exceeded"  # 预算已冻结
```

## 下一步

- [ ] 补 Core 档模型（Recipe / Workflow / ForgedSkill / Assertion / Triple）
- [ ] 补 Runtime invariant decorator（对应 BDG-I1/I3 的运行时断言）
- [ ] 接 `tools/lint/` 的 TASK_NEEDS_BUDGET 做端到端验证

## 测试

smoke 测试跑：

```bash
python -c "from runtime.platform.models import Budget, BudgetLimits, CostEntry, InsufficientBudget; \
    b = Budget.__new__(Budget); \
    from uuid import uuid4; \
    b.__init__(uuid4(), BudgetLimits(tokens=1000, usd=1.0)); \
    rid = b.reserve(CostEntry(tokens_in=100, tokens_out=50, usd=0.01)); \
    b.commit(rid, CostEntry(tokens_in=100, tokens_out=50, usd=0.01)); \
    print('ok, spent:', b.tokens_spent, b.usd_spent)"
```
