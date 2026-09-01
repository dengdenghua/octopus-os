"""Execution / tool-dispatch helpers for the ReAct loop.

Extracted from ``react_loop.py`` (post-Wave-2 of the split documented in
``docs/design/react-loop-split-plan.md``). This module is now a thin
re-export hub. The responsibility-cohesive clusters live in the
``_react_execution_*`` submodules (``dispatch``, ``results``, ``progress``,
``trajectory``, ``phase6d``, ``phase6g``); the public API surface is
unchanged so ``react_loop`` and the tests keep importing these names from
here.

None of these submodules import ``react_loop`` at runtime, so this module
never participates in an import cycle with the loop body.
"""

from __future__ import annotations

from runtime.core.cerebrum._react_execution_dispatch import (
    _VERIFY_SKILLS,
    TOOL_OBSERVATION_MAX_CHARS,
    _execute_action_via_beak,
    _normalized_tool_call_from_react_action,
    _output_indicates_command_failure,
    _output_indicates_missing_tool,
    _run_auto_diagnostics,
)
from runtime.core.cerebrum._react_execution_phase6d import (
    _phase_6d_dispatch_and_observe,
)
from runtime.core.cerebrum._react_execution_phase6g import (
    _phase_6g_housekeeping,
)
from runtime.core.cerebrum._react_execution_progress import (
    _FILE_SKILLS,
    _KG_COUNTERS,
    _KG_REFRESH_EVERY,
    _PHASE_KEYWORDS,
    _RECIPE_COUNTERS,
    _RECIPE_REFRESH_EVERY,
    _WRITE_SKILLS,
    _build_progress_summary,
    _build_research_progress_summary,
    _detect_phase,
    _persist_react_trajectory,
    _public_progress_target,
    _react_kg_throttle,
    _react_recipe_throttle,
    _reset_kg_throttle_for_tests,
    _reset_recipe_throttle_for_tests,
    _update_working_set,
)
from runtime.core.cerebrum._react_execution_results import (
    _SCOPED_ARTIFACT_WRITE_TOOLS,
    _VERIFICATION_TOOL_KINDS,
    _background_task_info_from_observation,
    _beak_step_effective_success,
    _command_from_tool_step,
    _format_background_task_heartbeat,
    _has_unrecovered_beak_failure,
    _is_scoped_artifact_write,
    _react_completion_receipt,
    _skill_available_in_executor,
    _tool_event_extras_from_beak_step,
    _verification_kind_from_command,
    classify_turn_failure,
)

__all__ = [
    "TOOL_OBSERVATION_MAX_CHARS",
    "_FILE_SKILLS",
    "_KG_COUNTERS",
    "_KG_REFRESH_EVERY",
    "_PHASE_KEYWORDS",
    "_RECIPE_COUNTERS",
    "_RECIPE_REFRESH_EVERY",
    "_SCOPED_ARTIFACT_WRITE_TOOLS",
    "_VERIFICATION_TOOL_KINDS",
    "_VERIFY_SKILLS",
    "_WRITE_SKILLS",
    "_background_task_info_from_observation",
    "_beak_step_effective_success",
    "_build_progress_summary",
    "_build_research_progress_summary",
    "_command_from_tool_step",
    "_detect_phase",
    "_execute_action_via_beak",
    "_format_background_task_heartbeat",
    "_has_unrecovered_beak_failure",
    "_is_scoped_artifact_write",
    "_normalized_tool_call_from_react_action",
    "_output_indicates_command_failure",
    "_output_indicates_missing_tool",
    "_persist_react_trajectory",
    "_phase_6d_dispatch_and_observe",
    "_phase_6g_housekeeping",
    "_public_progress_target",
    "_react_completion_receipt",
    "_react_kg_throttle",
    "_react_recipe_throttle",
    "_reset_kg_throttle_for_tests",
    "_reset_recipe_throttle_for_tests",
    "_run_auto_diagnostics",
    "_skill_available_in_executor",
    "_tool_event_extras_from_beak_step",
    "_update_working_set",
    "_verification_kind_from_command",
    "classify_turn_failure",
]
