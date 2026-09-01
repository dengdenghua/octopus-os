"""PHASE 3 — system + volatile prompt assembly for the ReAct loop.

Extracted from ``react_loop.py`` (post-Wave-2 of the split documented in
``docs/design/react-loop-split-plan.md``). Pure sequential assembly:
builds the byte-stable system prompt (mode contracts, workspace rules,
project profile, cadence/tool policy, delegation guidance, soul,
constitution, team roster) and the per-turn volatile overlays (date,
grounding, resume intent, output style, thinking guidance, capability
activation, memory recall, camouflage variant), then composes the
initial ``messages`` list (system prefix + volatile user message +
conversation history + startup code context + the user's request).

Depends only on react_* leaf modules and platform layers; never imports
react_loop.

This module is now a thin orchestrator + re-export hub. The responsibility-
cohesive clusters live in the ``_react_prompt_assembly_*`` submodules
(``sections``, ``guidance``, ``tools``, ``memory``, ``messages``,
``bootstrap``, ``events``); the public API surface is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.core.cerebrum._react_prompt_assembly_bootstrap import (
    _emit_turn_start_events,
    _resolve_turn_bootstrap,
    _TurnBootstrap,
)
from runtime.core.cerebrum._react_prompt_assembly_guidance import (
    _assemble_core_guidance,
    _assemble_delegation_guidance,
    _assemble_tool_sections,
)
from runtime.core.cerebrum._react_prompt_assembly_sections import (
    _assemble_early_sections,
)
from runtime.core.cerebrum._react_prompt_assembly_state import (
    _assemble_memory_sections,
    _assemble_messages,
    _AssemblyState,
)
from runtime.core.cerebrum.react_goal_analysis import derive_effective_execution_goal

__all__ = [
    "_PromptAssembly",
    "_TurnBootstrap",
    "_assemble_prompt_and_messages",
    "_emit_turn_start_events",
    "_resolve_turn_bootstrap",
]


@dataclass
class _PromptAssembly:
    """Everything PHASE 3 produces that later phases consume."""

    messages: list = field(default_factory=list)
    max_iterations: int = 30
    metadata: dict = field(default_factory=dict)
    effective_wp: Any = None
    is_goal_mode: bool = False
    is_code_mode: bool = False
    browser_operation_mode: bool = False
    todo_protocol_required: bool = False
    todo_protocol_visible: bool = False
    file_inspection_tools_visible: bool = False
    read_only_turn: bool = False
    observed_read_sequence: bool = False
    final_guard_grounded_source_paths: Any = None
    guard_impasse_state: dict = field(default_factory=dict)
    prior_grounding_text: str = ""
    budget_auto_pause_enabled: bool = False
    budget_pause_threshold: float = 0.0
    realtime_public_orientation_requested: bool = False
    grounding_sources: list = field(default_factory=list)
    is_swarm_mode: bool = False
    is_research_mode: bool = False
    active_max_tokens_budget: Any = None
    active_max_usd_budget: Any = None
    effective_goal: str = ""


def _assemble_prompt_and_messages(
    *,
    intent: Any,
    agent: Any,
    stack: Any,
    executor: Any,
    approval_provider: Any,
    resume_task_id: Any,
    planning_mode: bool,
    tools_active: bool,
    native_mode: bool,
    no_tool_turn: bool,
    strict_explicit_reads: bool,
    camouflage_suffix: str,
    max_iterations: int,
    max_tokens_budget: Any,
    max_usd_budget: Any,
) -> _PromptAssembly:
    """PHASE 3 · build the system/volatile prompts and initial messages.

    Orchestrates the responsibility-clustered helpers in the
    ``_react_prompt_assembly_*`` submodules. Reads the turn wiring
    (``intent`` / ``agent`` / ``stack`` / ``executor``) and the PHASE 1/2
    mode flags; returns every local the later phases consume via
    ``_PromptAssembly``. ``max_iterations`` may be lifted by the
    swarm/browser/research floors and is handed back in the result.
    """
    state = _AssemblyState(
        intent=intent,
        agent=agent,
        stack=stack,
        executor=executor,
        approval_provider=approval_provider,
        resume_task_id=resume_task_id,
        planning_mode=planning_mode,
        tools_active=tools_active,
        native_mode=native_mode,
        no_tool_turn=no_tool_turn,
        strict_explicit_reads=strict_explicit_reads,
        camouflage_suffix=camouflage_suffix,
        max_iterations=max_iterations,
        max_tokens_budget=max_tokens_budget,
        max_usd_budget=max_usd_budget,
        user_context=intent.user_context or {},
    )
    state.metadata = state.user_context.get("metadata") or {}
    state.effective_goal = derive_effective_execution_goal(
        str(intent.normalized_goal or intent.raw or ""),
        state.user_context.get("conversation_messages"),
    )

    _assemble_early_sections(state)
    _assemble_core_guidance(state)
    _assemble_delegation_guidance(state)
    _assemble_tool_sections(state)
    _assemble_memory_sections(state)
    _assemble_messages(state)

    return _PromptAssembly(
        messages=state.messages,
        max_iterations=state.max_iterations,
        metadata=state.metadata,
        effective_wp=state.effective_wp,
        is_goal_mode=state.is_goal_mode,
        is_code_mode=state.is_code_mode,
        browser_operation_mode=state.browser_operation_mode,
        todo_protocol_required=state.todo_protocol_required,
        todo_protocol_visible=state.todo_protocol_visible,
        file_inspection_tools_visible=state.file_inspection_tools_visible,
        read_only_turn=state.read_only_turn,
        observed_read_sequence=state.observed_read_sequence,
        final_guard_grounded_source_paths=state.final_guard_grounded_source_paths,
        guard_impasse_state=state.guard_impasse_state,
        prior_grounding_text=state.prior_grounding_text,
        budget_auto_pause_enabled=state.budget_auto_pause_enabled,
        budget_pause_threshold=state.budget_pause_threshold,
        realtime_public_orientation_requested=state.realtime_public_orientation_requested,
        grounding_sources=state.grounding_sources,
        is_swarm_mode=state.is_swarm_mode,
        is_research_mode=state.is_research_mode,
        active_max_tokens_budget=state.active_max_tokens_budget,
        active_max_usd_budget=state.active_max_usd_budget,
        effective_goal=state.effective_goal,
    )
