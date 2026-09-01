"""runtime.safety · 安全治理（Safety Governance）

子包速查：
  auth           → TrustEngine —— 来源信任 + 自适应风控
  budget_breaker → CircuitBreaker —— per-task 预算硬顶 + 三态熔断
  validation     → ValidationGate / LLMJudge —— 5 类规则 + LLM 判官
  experiments    → PromptEvolver —— A/B 变异 + 自然选择
  recovery       → Recovery —— 从失败中学习、锻造新技能
  chromatophores → SignalBus + BoidsArbitrator —— 腕间通信 + 群体仲裁
  gene_locks     → GeneLocks —— 防止盲目自修改
  invariants     → InvariantCheck —— 运行时数据结构约束
  hooks          → ToolLifecycleHooks —— pre/post + tool_edge_hooks
  governance     → ExecutionInstruction + allow/deny/hold/rewrite 决策
  evolution      → Evolution —— 多策略 fitness/rollback/drift 引擎
  organization   → TopologyEvolver —— 团队拓扑演化

  approval/      → 审批与取消（approval_gate / approval_policy_store /
                   cancellation / device_lock）
  sandboxing/    → 进程级沙箱（sandbox / container_sandbox）
  audit/         → 审计与外部信任（audit_chain / webhook_verify /
                   trust_gateway）
"""

from __future__ import annotations

# Backward-compat shims: legacy code does ``from runtime.safety import X``
# where X is the submodule name. Governance stays a direct subpackage import
# to avoid pulling process models back into this compatibility initializer.
from .approval import (  # noqa: F401
    approval_gate,
    approval_policy_store,
    cancellation,
    device_lock,
)
from .audit import (  # noqa: F401
    audit_chain,
    trust_gateway,
    webhook_verify,
)
from .hooks import tool_edge_hooks  # noqa: F401
from .sandboxing import (  # noqa: F401
    container_sandbox,
    sandbox,
)
