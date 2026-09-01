"""runtime.execution · 执行引擎（Execution Engine）

子包速查：
  agents       → Agent preset 工厂（user-facing roles）
  arms         → Arm + ArmPool —— Worker 池 + 腕足生命周期
  tool_engine  → ToolExecutor —— 单步工具调用 + 鉴权 + Journal
  suckers      → SkillRegistry —— 技能注册 + 发现 + 沙箱测试
  all_skills   → ~200 个 SKILL.md 资产目录
  swarm        → SwarmRuntime —— 多腕并行 + Boids 协调
  parallel_agents / subagents / slash_commands → 子 agent 派发
  misc/        → 杂项 helper（agent_avatar / agent_packs /
                 capability_catalog / capability_permissions /
                 file_write_leases / image_generation /
                 multiagent_contracts / parallel_runner / skill_policy）
"""

from __future__ import annotations

_LAZY_EXPORTS = {
    # Group subpackages.
    "agents": "runtime.execution.agents",
    "arms": "runtime.execution.arms",
    "tool_engine": "runtime.execution.tool_engine",
    "suckers": "runtime.execution.suckers",
    "all_skills": "runtime.execution.all_skills",
    "swarm": "runtime.execution.swarm",
    "parallel_agents": "runtime.execution.parallel_agents",
    "subagents": "runtime.execution.subagents",
    "slash_commands": "runtime.execution.slash_commands",
    "loops": "runtime.execution.loops",
    # Backward-compat shims for code that does `from runtime.execution import X`
    # where X is a submodule name now living under misc/.
    "agent_avatar": "runtime.execution.misc.agent_avatar",
    "agent_packs": "runtime.execution.misc.agent_packs",
    "capability_catalog": "runtime.execution.misc.capability_catalog",
    "capability_permissions": "runtime.execution.misc.capability_permissions",
    "file_write_leases": "runtime.execution.misc.file_write_leases",
    "image_generation": "runtime.execution.misc.image_generation",
    "multiagent_contracts": "runtime.execution.misc.multiagent_contracts",
    "parallel_runner": "runtime.execution.misc.parallel_runner",
    "skill_policy": "runtime.execution.misc.skill_policy",
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = import_module(target)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY_EXPORTS)
