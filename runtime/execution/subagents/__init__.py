"""Subagent execution bridge.

Subagents are not skills. They are isolated agent turns launched through a
runner. The functions exported here are the programmatic bridge used by
routers and legacy compatibility shims.
"""

from .bridge import (
    SubAgentRunner,
    call_subagent,
    get_sub_agent_runner,
    get_subagent_registry,
    set_sub_agent_runner,
    set_subagent_registry,
)
from .registry import (
    SubagentDefinition,
    SubagentRegistry,
    load_subagent_file,
    load_subagent_registry,
)

__all__ = [
    "SubAgentRunner",
    "SubagentDefinition",
    "SubagentRegistry",
    "call_subagent",
    "get_sub_agent_runner",
    "get_subagent_registry",
    "load_subagent_file",
    "load_subagent_registry",
    "set_sub_agent_runner",
    "set_subagent_registry",
]
