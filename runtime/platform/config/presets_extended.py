"""Task-oriented presets and conversation personas.

Echo Native preset/persona system extension.

Presets define tool configurations and constraints for specific tasks:
- code-reviewer: Code review tools, strict validation
- researcher: Search/browser tools, exploratory mode
- debugger: Debugging tools, detailed tracing
- writer: Documentation tools, clarity focus

Personas define conversation style and tone:
- senior-engineer: Professional, concise, assumes expertise
- beginner-friendly: Patient, detailed explanations
- academic: Formal, citation-focused
- casual: Relaxed, conversational
"""

from __future__ import annotations

from typing import Any

from .schema import AgentConfig, BudgetConfig, ImmunityConfig, LearnConfig, PlannerConfig

# ── Task-oriented presets ────────────────────────────────────

_TASK_PRESETS: dict[str, dict[str, Any]] = {
    "code-reviewer": {
        "description": "代码审查 · 严格验证 · 关注质量和安全",
        "planner": PlannerConfig(type="llm", model="gpt-4o"),
        "budget": BudgetConfig(max_tokens=100000, max_usd=5.00),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=3, max_rules=50),
        "tool_allowlist": [
            "read_file",
            "list_files",
            "search_files",
            "run_tests",
            "lint_check",
        ],
        "system_prompt_additions": [
            "Focus on code quality, security, and maintainability.",
            "Flag potential bugs, security issues, and style violations.",
            "Provide specific line numbers and concrete suggestions.",
        ],
    },
    "researcher": {
        "description": "研究探索 · 搜索/浏览器工具 · 宽松策略",
        "planner": PlannerConfig(type="llm", model="gpt-4o"),
        "budget": BudgetConfig(max_tokens=500000, max_usd=25.00),
        "immunity": ImmunityConfig(unknown_policy="allow"),
        "learn": LearnConfig(min_hits=2, max_rules=100),
        "tool_allowlist": [
            "web_search",
            "web_fetch",
            "read_file",
            "write_file",
            "search_files",
        ],
        "system_prompt_additions": [
            "Gather comprehensive information from multiple sources.",
            "Cite sources and provide references.",
            "Synthesize findings into clear conclusions.",
        ],
    },
    "debugger": {
        "description": "调试诊断 · 详细追踪 · 问题定位",
        "planner": PlannerConfig(type="llm", model="gpt-4o"),
        "budget": BudgetConfig(max_tokens=200000, max_usd=10.00),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=3, max_rules=60),
        "tool_allowlist": [
            "read_file",
            "bash",
            "list_files",
            "search_files",
            "grep",
        ],
        "system_prompt_additions": [
            "Systematically diagnose issues with detailed tracing.",
            "Check logs, error messages, and stack traces.",
            "Propose concrete fixes with verification steps.",
        ],
    },
    "writer": {
        "description": "文档写作 · 清晰表达 · 结构化内容",
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_tokens=150000, max_usd=3.00),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=3, max_rules=40),
        "tool_allowlist": [
            "read_file",
            "write_file",
            "list_files",
            "search_files",
        ],
        "system_prompt_additions": [
            "Write clear, well-structured documentation.",
            "Use appropriate formatting and examples.",
            "Focus on readability and maintainability.",
        ],
    },
    "ops": {
        "description": "运维部署 · 系统管理 · 基础设施",
        "planner": PlannerConfig(type="llm", model="gpt-4o"),
        "budget": BudgetConfig(max_tokens=150000, max_usd=7.50),
        "immunity": ImmunityConfig(unknown_policy="reject"),
        "learn": LearnConfig(min_hits=4, max_rules=70),
        "tool_allowlist": [
            "bash",
            "read_file",
            "write_file",
            "list_files",
        ],
        "system_prompt_additions": [
            "Prioritize system stability and security.",
            "Verify commands before execution in production.",
            "Provide rollback plans for risky operations.",
        ],
    },
}

# ── Conversation personas ────────────────────────────────────

_PERSONAS: dict[str, dict[str, Any]] = {
    "senior-engineer": {
        "description": "资深工程师 · 专业简洁 · 假设有经验",
        "tone": "professional",
        "verbosity": "concise",
        "system_prompt_additions": [
            "Communicate as a senior engineer to a peer.",
            "Be concise and assume technical expertise.",
            "Focus on trade-offs and architectural implications.",
            "Skip basic explanations unless asked.",
        ],
    },
    "beginner-friendly": {
        "description": "新手友好 · 耐心详细 · 循序渐进",
        "tone": "friendly",
        "verbosity": "detailed",
        "system_prompt_additions": [
            "Explain concepts clearly with examples.",
            "Break down complex ideas into simple steps.",
            "Be patient and encourage learning.",
            "Provide context and background when needed.",
        ],
    },
    "academic": {
        "description": "学术风格 · 正式引用 · 研究导向",
        "tone": "formal",
        "verbosity": "detailed",
        "system_prompt_additions": [
            "Use formal academic language and structure.",
            "Cite sources and provide references.",
            "Present balanced analysis with multiple perspectives.",
            "Acknowledge limitations and uncertainties.",
        ],
    },
    "casual": {
        "description": "轻松对话 · 随意自然 · 易于理解",
        "tone": "casual",
        "verbosity": "balanced",
        "system_prompt_additions": [
            "Use conversational, approachable language.",
            "Keep explanations practical and relatable.",
            "Balance thoroughness with readability.",
        ],
    },
    "tutor": {
        "description": "导师模式 · 引导思考 · 启发式提问",
        "tone": "encouraging",
        "verbosity": "balanced",
        "system_prompt_additions": [
            "Guide through questions rather than direct answers.",
            "Encourage critical thinking and problem-solving.",
            "Provide hints and partial solutions when helpful.",
            "Celebrate progress and learning moments.",
        ],
    },
}

# ── Existing usage-based presets ─────────────────────────────

_USAGE_PRESETS: dict[str, dict[str, Any]] = {
    "personal": {
        "description": "个人用户 · 低成本 · 日常使用",
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=3, max_rules=30),
    },
    "team": {
        "description": "小团队 · 中等预算 · 协作场景",
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_usd=2.00),
        "immunity": ImmunityConfig(unknown_policy="quarantine"),
        "learn": LearnConfig(min_hits=5, max_rules=60),
    },
    "enterprise": {
        "description": "企业级 · 高预算 · 严格安全",
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_tokens=200000, max_usd=10.00),
        "immunity": ImmunityConfig(unknown_policy="reject"),
        "learn": LearnConfig(min_hits=5, max_rules=100),
    },
    "research": {
        "description": "研究实验 · 大预算 · 宽松策略",
        "planner": PlannerConfig(type="llm", model="gpt-4o-mini"),
        "budget": BudgetConfig(max_tokens=500000, max_usd=50.00),
        "immunity": ImmunityConfig(unknown_policy="allow"),
        "learn": LearnConfig(min_hits=2, max_rules=200),
    },
}

# Combine all presets
_ALL_PRESETS = {**_USAGE_PRESETS, **_TASK_PRESETS}


def apply_preset(name: str, base: AgentConfig | None = None) -> AgentConfig:
    """Apply a preset configuration.

    Args:
        name: Preset name (usage-based or task-oriented)
        base: Optional base configuration to extend

    Returns:
        AgentConfig with preset applied

    Raises:
        ValueError: If preset name is unknown
    """
    if name not in _ALL_PRESETS:
        available = ", ".join(sorted(_ALL_PRESETS.keys()))
        raise ValueError(f"Unknown preset {name!r}. Available: {available}")

    overrides = _ALL_PRESETS[name].copy()
    # Remove non-AgentConfig fields
    overrides.pop("description", None)
    overrides.pop("tool_allowlist", None)
    overrides.pop("system_prompt_additions", None)

    if base is None:
        return AgentConfig(**overrides)

    base_dict = base.model_dump()
    base_dict.update(overrides)
    return AgentConfig(**base_dict)


def apply_persona(name: str) -> dict[str, Any]:
    """Get persona configuration.

    Args:
        name: Persona name

    Returns:
        Dictionary with persona settings (tone, verbosity, prompts)

    Raises:
        ValueError: If persona name is unknown
    """
    if name not in _PERSONAS:
        available = ", ".join(sorted(_PERSONAS.keys()))
        raise ValueError(f"Unknown persona {name!r}. Available: {available}")

    return _PERSONAS[name].copy()


def list_presets(category: str | None = None) -> list[str]:
    """List available presets.

    Args:
        category: Optional filter - "usage", "task", or None for all

    Returns:
        Sorted list of preset names
    """
    if category == "usage":
        return sorted(_USAGE_PRESETS.keys())
    if category == "task":
        return sorted(_TASK_PRESETS.keys())
    return sorted(_ALL_PRESETS.keys())


def list_personas() -> list[str]:
    """List available personas."""
    return sorted(_PERSONAS.keys())


def get_preset_description(name: str) -> str:
    """Get preset description."""
    preset = _ALL_PRESETS.get(name)
    if preset:
        return preset.get("description", "")
    return ""


def get_persona_description(name: str) -> str:
    """Get persona description."""
    persona = _PERSONAS.get(name)
    if persona:
        return persona.get("description", "")
    return ""


def get_preset_details(name: str) -> dict[str, Any]:
    """Get full preset configuration including metadata.

    Args:
        name: Preset name

    Returns:
        Dictionary with all preset fields

    Raises:
        ValueError: If preset name is unknown
    """
    if name not in _ALL_PRESETS:
        available = ", ".join(sorted(_ALL_PRESETS.keys()))
        raise ValueError(f"Unknown preset {name!r}. Available: {available}")

    return _ALL_PRESETS[name].copy()


def get_persona_details(name: str) -> dict[str, Any]:
    """Get full persona configuration.

    Args:
        name: Persona name

    Returns:
        Dictionary with all persona fields

    Raises:
        ValueError: If persona name is unknown
    """
    if name not in _PERSONAS:
        available = ", ".join(sorted(_PERSONAS.keys()))
        raise ValueError(f"Unknown persona {name!r}. Available: {available}")

    return _PERSONAS[name].copy()


__all__ = [
    "apply_preset",
    "apply_persona",
    "list_presets",
    "list_personas",
    "get_preset_description",
    "get_persona_description",
    "get_preset_details",
    "get_persona_details",
]
