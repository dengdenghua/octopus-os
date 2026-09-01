"""Legacy compatibility shim for subagent dispatch.

Subagents are not skills. New code should import from
``runtime.execution.subagents`` and use ``call_subagent`` or the runner
injection helpers directly.

This module is kept so older imports continue to work. Its opt-in
``register_sub_agent_skill`` helper is intentionally not used by
``runtime.execution.all_skills.register_all``.
"""

from __future__ import annotations

from typing import Any

from runtime.execution.subagents import (
    SubAgentRunner,
    call_subagent,
    get_sub_agent_runner,
    set_sub_agent_runner,
)

_call_agent = call_subagent


def register_sub_agent_skill(registry: Any) -> None:
    """Deprecated opt-in shim that registers ``call_agent`` as a legacy skill.

    This exists only for old tests or integrations that explicitly opt in. The
    normal skill catalog does not call it, and planners reject ``call_agent`` as
    a skill even if a legacy registry contains it.
    """
    from runtime.execution.suckers import Skill
    from runtime.execution.suckers.testing import (
        SkillExpect,
        SkillTestCase,
    )

    registry.register(
        Skill(
            name="call_agent",
            description=(
                "Deprecated legacy bridge. Subagents are not skills; use the "
                "subagent/Agent dispatch channel instead."
            ),
            affinity=["legacy", "subagent"],
            cost_profile="high",
            trusted_source="skill://legacy/call_agent",
            handler=_call_agent,
            tests=[
                SkillTestCase(
                    name="no_runner_returns_error",
                    tier="golden",
                    args={"agent_id": "coder", "prompt": "do x"},
                    expect=SkillExpect(schema_keys=["success", "error"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("success") is False
                        and "runner" in (r.get("error") or "")
                    ),
                ),
            ],
        )
    )


__all__ = [
    "SubAgentRunner",
    "_call_agent",
    "call_subagent",
    "set_sub_agent_runner",
    "get_sub_agent_runner",
    "register_sub_agent_skill",
]
