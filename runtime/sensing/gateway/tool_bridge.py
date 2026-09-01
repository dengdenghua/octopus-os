"""
tool_bridge · the agentic-loop helper that turns Echo skills into
Claude-native ``tool_use`` calls and loops result → next turn.

Why a separate module
---------------------

Before this file, Echo had THREE ways to engage tools, none of
which worked reliably with Claude Sonnet 4.6 / Opus 4+ (the models
most users are on):

1. **LLMPlanner**  ·  asks the model to emit a strict TaskGraph
   JSON ``{"reasoning":..., "nodes":[{skill,args}]}``. Claude 4+
   writes prose instead, planner fails, falls back to direct LLM.
2. **ReAct loop**  ·  looks for ``Thought: / Action: name({args})
   / Final Answer:`` anchors. Claude 4+ writes Markdown tables
   and stories, anchors never appear, format-violation bail.
3. **Fast-path (added earlier this round)**  ·  skips both above
   for thinking-capable models → pure text streaming → zero tools.

All three paths **parse prose** for tool calls. That's the wrong
contract for Claude 4+, which has a protocol-level tool_use API.
This module is the fourth path — the only one that works:

    user query
        ↓
    build skill catalog → Anthropic ``tools=[...]`` spec
        ↓
    messages.stream(tools=[...]) → Claude emits ``tool_use`` blocks
        ↓
    execute each block via stack.executor  (existing Beak pipeline)
        ↓
    wrap results as ``tool_result`` content blocks
        ↓
    messages.stream(messages=[...prior..., tool_results]) → next turn
        ↓
    loop until the model responds with plain text (no more tool_use)

The loop is bounded (``MAX_TOOL_ROUNDS``) to prevent runaway spend
when a model gets stuck in a tool-use echo chamber.

Split notes
-----------

The god-file body was split into satellite modules (same directory),
following the ``_<module>_<responsibility>.py`` pattern used across
this package:

    ``_tool_bridge_native.py``     native stream + timeout + fingerprint/dedup
    ``_tool_bridge_protocol.py``   public checkpoint / protocol-tag cleaning
    ``_tool_bridge_policy.py``     goal / scope / budget / shell policy
    ``_tool_bridge_session.py``    session metadata + browser guidance
    ``_tool_bridge_exec.py``       tool execution + semantic error + XML recover
    ``_tool_bridge_scoring.py``    per-turn scoring + auto-evolution tick
    ``_tool_bridge_loop.py``       ``stream_agentic_fallback`` main loop

This module re-exports every symbol so existing importers and tests
(``from runtime.sensing.gateway.tool_bridge import ...``) are unchanged.
"""

from __future__ import annotations

from runtime.execution.tool_spec_builder import (  # re-exported
    build_anthropic_tool_specs,
)
from runtime.sensing.model_router.rescue_policy import (  # re-exported (monkeypatchable)
    is_retryable_model_error as _is_provider_unavailable_error,
)
from runtime.sensing.model_router.rescue_policy import (
    next_custom_model_fallback as _next_custom_model_fallback,
)

from ._tool_bridge_exec import (
    _execute_tool_call,
    _is_semantic_error,
    _recover_named_xml_tool_calls,
)
from ._tool_bridge_loop import stream_agentic_fallback
from ._tool_bridge_native import (
    _NATIVE_STREAM_DEADLINE,
    _NATIVE_STREAM_REDIRECTED,
    _deduplicate_native_tool_calls,
    _iter_native_model_stream_with_deadline,
    _native_call_failure_is_definitive,
    _native_definitive_failure_target,
    _native_failure_is_definitive,
    _native_model_recovery_timeout_s,
    _native_model_round_timeout_s,
    _native_post_tool_timeout_s,
    _native_tool_batch_fingerprint,
    _native_tool_call_fingerprint,
)
from ._tool_bridge_policy import (
    _CODE_MUTATION_TOOLS,
    _CODE_TERMINAL_VERIFIER_TOOLS,
    _CODE_VERIFICATION_TOOLS,
    _SERIAL_BARRIER_TOOLS,
    CODE_CHANGE_ROUND_BUDGET,
    DEFAULT_TOOL_ROUND_BUDGET,
    MAX_TOOL_ROUNDS,
    NARROW_WEB_RESEARCH_ROUND_BUDGET,
    PARALLEL_TOOL_USE_DEFAULT,
    PARALLEL_TOOL_USE_MAX_WORKERS,
    READ_ONLY_ROUND_BUDGET,
    REFLECTION_INTERVAL,
    TOOL_OUTPUT_MAX_CHARS,
    WEB_RESEARCH_ROUND_BUDGET,
    _filter_tool_specs_for_workspace_contract,
    _goal_forbids_local_workspace_access,
    _goal_is_narrow_single_source_research,
    _goal_is_read_only,
    _is_code_change_task,
    _is_evidence_task,
    _is_security_change_task,
    _is_shell_mutation,
    _is_shell_terminal_verifier,
    _is_shell_verification,
    _native_tool_round_budget,
    _reflection_checkpoint_message,
    _shell_command_text,
    _tool_uses_session_scope,
)
from ._tool_bridge_protocol import (
    _NATIVE_ROUND_TEXT_PREFIX_RE,
    _NATIVE_TEXT_STREAM_SUPPRESS_MARKERS,
    _NATIVE_TEXT_STREAM_TAIL_MARGIN,
    PUBLIC_NARRATIVE_SILENCE_S,
    PUBLIC_NARRATIVE_TIMEOUT_S,
    _batch_needs_live_public_narrative,
    _generate_native_action_checkpoint,
    _generate_native_evidence_checkpoint,
    _generate_native_public_checkpoint,
    _native_calls_with_public_checkpoint,
    _native_public_checkpoint,
    _native_result_checkpoint,
    _ordered_read_handoffs_requested,
    _public_checkpoint_language,
    _public_narrative_silence_s,
    _render_result_checkpoint,
    _safe_public_source_title,
    strip_leaked_protocol_tags,
)
from ._tool_bridge_scoring import (
    _auto_evolve_tick_safe,
    _record_score_safe,
)
from ._tool_bridge_session import (
    _browser_action_evidence,
    _browser_operation_guidance,
    _ensure_explicit_browser_skills,
    _required_browser_action_evidence,
    _session_metadata_from_intent,
)

__all__ = [
    # public constants
    "PUBLIC_NARRATIVE_SILENCE_S",
    "PUBLIC_NARRATIVE_TIMEOUT_S",
    "CODE_CHANGE_ROUND_BUDGET",
    "DEFAULT_TOOL_ROUND_BUDGET",
    "MAX_TOOL_ROUNDS",
    "NARROW_WEB_RESEARCH_ROUND_BUDGET",
    "PARALLEL_TOOL_USE_DEFAULT",
    "PARALLEL_TOOL_USE_MAX_WORKERS",
    "READ_ONLY_ROUND_BUDGET",
    "REFLECTION_INTERVAL",
    "TOOL_OUTPUT_MAX_CHARS",
    "WEB_RESEARCH_ROUND_BUDGET",
    # public entry points
    "build_anthropic_tool_specs",
    "stream_agentic_fallback",
    "strip_leaked_protocol_tags",
    # rescue-policy helpers (monkeypatched by tests)
    "_is_provider_unavailable_error",
    "_next_custom_model_fallback",
    # native stream / timeout / fingerprint helpers
    "_NATIVE_STREAM_DEADLINE",
    "_NATIVE_STREAM_REDIRECTED",
    "_deduplicate_native_tool_calls",
    "_iter_native_model_stream_with_deadline",
    "_native_call_failure_is_definitive",
    "_native_definitive_failure_target",
    "_native_failure_is_definitive",
    "_native_model_recovery_timeout_s",
    "_native_model_round_timeout_s",
    "_native_post_tool_timeout_s",
    "_native_tool_batch_fingerprint",
    "_native_tool_call_fingerprint",
    # goal / scope / budget / shell policy
    "_CODE_MUTATION_TOOLS",
    "_CODE_TERMINAL_VERIFIER_TOOLS",
    "_CODE_VERIFICATION_TOOLS",
    "_SERIAL_BARRIER_TOOLS",
    "_filter_tool_specs_for_workspace_contract",
    "_goal_forbids_local_workspace_access",
    "_goal_is_narrow_single_source_research",
    "_goal_is_read_only",
    "_is_code_change_task",
    "_is_evidence_task",
    "_is_security_change_task",
    "_is_shell_mutation",
    "_is_shell_terminal_verifier",
    "_is_shell_verification",
    "_native_tool_round_budget",
    "_reflection_checkpoint_message",
    "_shell_command_text",
    "_tool_uses_session_scope",
    # checkpoint / protocol-tag cleaning
    "_NATIVE_ROUND_TEXT_PREFIX_RE",
    "_NATIVE_TEXT_STREAM_SUPPRESS_MARKERS",
    "_NATIVE_TEXT_STREAM_TAIL_MARGIN",
    "_batch_needs_live_public_narrative",
    "_generate_native_action_checkpoint",
    "_generate_native_evidence_checkpoint",
    "_generate_native_public_checkpoint",
    "_native_calls_with_public_checkpoint",
    "_native_public_checkpoint",
    "_native_result_checkpoint",
    "_ordered_read_handoffs_requested",
    "_public_checkpoint_language",
    "_public_narrative_silence_s",
    "_render_result_checkpoint",
    "_safe_public_source_title",
    # scoring / auto-evolution
    "_auto_evolve_tick_safe",
    "_record_score_safe",
    # session metadata + browser guidance
    "_browser_action_evidence",
    "_browser_operation_guidance",
    "_ensure_explicit_browser_skills",
    "_required_browser_action_evidence",
    "_session_metadata_from_intent",
    # tool execution + semantic error + XML recover
    "_execute_tool_call",
    "_is_semantic_error",
    "_recover_named_xml_tool_calls",
]
