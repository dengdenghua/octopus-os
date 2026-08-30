from __future__ import annotations

from typing import Any

from .schema import (
    AgentConfig,
    BudgetConfig,
    ImmunityConfig,
    LearnConfig,
    PlannerConfig,
)

_PRESETS: dict[str, dict[str, Any]] = {
    "personal": {
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=3, max_rules=30),
    },
    "team": {
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_usd=2.00),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=5, max_rules=60),
    },
    "enterprise": {
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_tokens=200000, max_usd=10.00),
        "immunity": ImmunityConfig(unknown_policy="reject"),
        "learn": LearnConfig(min_hits=5, max_rules=100),
    },
    "research": {
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_tokens=500000, max_usd=50.00),
        "immunity": ImmunityConfig(unknown_policy="allow"),
        "learn": LearnConfig(min_hits=2, max_rules=200),
    },
}


def apply_preset(name: str, base: AgentConfig | None = None) -> AgentConfig:
    if name not in _PRESETS:
        raise ValueError(f"unknown preset {name!r} · choose from: " + ", ".join(sorted(_PRESETS)))
    overrides = _PRESETS[name]
    if base is None:
        return AgentConfig(**overrides)
    base_dict = base.model_dump()
    base_dict.update(overrides)
    return AgentConfig(**base_dict)


def list_presets() -> list[str]:
    return sorted(_PRESETS.keys())


def get_preset_description(name: str) -> str:
    descriptions = {
        "personal": "个人用户 · 低成本 · 适合日常使用",
        "team": "小团队 · 中等预算 · 更多规则容量",
        "enterprise": "企业级 · 高预算 · 严格安全策略",
        "research": "研究实验 · 大预算 · 宽松策略 · 最大化进化探索",
    }
    return descriptions.get(name, "")
