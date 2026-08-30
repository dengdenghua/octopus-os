"""
agents.presets · directory-backed agent loaders.

As of the `agents/<id>/` directory layout migration, agent persona
files (SOUL / IDENTITY / USER / MEMORY / AGENTS / BOOTSTRAP / TOOLS)
live on disk under ``agents/<id>/agent-core/``. The source of truth
moved from Python strings in this file to user-editable markdown.

This module keeps small convenience loaders for callers that explicitly
ask for one agent, but roster creation is directory-driven:

    from runtime.execution.agents import make_all_agent_presets
    agents = make_all_agent_presets(runtime)

Internally, ``make_all_agent_presets`` delegates to
``loader.load_all_agents``. Add a new agent = drop a folder under
``agents/`` with a ``profile.jsonc`` + ``agent-core/`` tree; no Python
roster change needed.
"""

from __future__ import annotations

from runtime.core.graph_runtime import GraphRuntime

from .base import Agent
from .loader import default_agents_root, load_agent, load_all_agents


def _load_one(agent_id: str, runtime: GraphRuntime) -> Agent:
    root = default_agents_root()
    return load_agent(root / agent_id, runtime, root / "_shared")


def make_general_agent(runtime: GraphRuntime) -> Agent:
    return _load_one("general", runtime)


def make_coder_agent(runtime: GraphRuntime) -> Agent:
    return _load_one("coder", runtime)


def make_vibe_selling_agent(runtime: GraphRuntime) -> Agent:
    return _load_one("vibe_selling", runtime)


def make_ecommerce_mind_agent(runtime: GraphRuntime) -> Agent:
    return _load_one("ecommerce_mind", runtime)


def make_desktop_operator_agent(runtime: GraphRuntime) -> Agent:
    """Construct the desktop-operator (Raven) persona agent.

    Since #22 (CUA productization) this is a first-class user-facing
    persona again and is auto-registered via ``load_all_agents`` from its
    ``agents/desktop_operator/`` folder. This factory remains for callers
    that explicitly request the persona by id.
    """
    return _load_one("desktop_operator", runtime)


def make_admin_agent(runtime: GraphRuntime) -> Agent:
    """Construct the admin agent for code mode.

    The admin agent is a dedicated system-level administrator persona
    with full privileges, separate from the "coder" used in chat/team
    modes. This ensures code mode has unrestricted workspace access.
    """
    return _load_one("admin", runtime)


def make_all_agent_presets(runtime: GraphRuntime) -> list[Agent]:
    """Load the user-facing local agents from ``agents/``.

    ``admin`` is a dedicated system persona for code-mode / privileged
    operations and is intentionally excluded from the default preset
    roster so normal registry routing is not skewed toward admin.
    Call ``make_admin_agent()`` explicitly when that persona is needed.
    ``desktop_operator`` IS part of the roster (first-class CUA persona).
    """
    return [
        agent for agent in load_all_agents(runtime) if getattr(agent, "agent_id", None) != "admin"
    ]
