"""Lazy compatibility exports for helpers historically owned by react_loop."""

# ruff: noqa: F822 — every public name is resolved lazily by module __getattr__

from __future__ import annotations

from importlib import import_module
from typing import Any

_MODULE_NAMES = (
    "react_browser_iteration",
    "react_checkpointing",
    "react_context",
    "react_execution",
    "react_explicit_reads",
    "react_final_answer_guards",
    "react_guards",
    "react_loop_controls",
    "react_model_deadlines",
    "react_parsing",
    "react_public_updates",
    "react_quiet_evidence",
    "react_resume",
    "react_types",
    "todo_protocol",
)

__all__ = [
    "_CONTEXT_PRESSURE_NUDGE",
    "_MODEL_STREAM_DEADLINE",
    "_ResumeState",
    "_background_task_info_from_observation",
    "_beak_step_effective_success",
    "_browser_operation_requested",
    "_browser_task_iteration_limit",
    "_build_code_agent_mode_prompt",
    "_build_code_context_prelude",
    "_build_personal_agent_mode_prompt",
    "_build_project_signals_prompt",
    "_build_resume_context_prompt",
    "_build_user_message_content",
    "_build_workflow_preset_prompt",
    "_checkpoint_interval",
    "_checkpoint_mirror",
    "classify_turn_failure",
    "_code_mode_completion_guard",
    "_code_task_iteration_limit",
    "_collect_model_stream_text_with_deadline",
    "_completion_phrase_without_todo_guard",
    "_compute_resume_state",
    "_disabled_guard_labels",
    "_disabled_guards_from_yaml",
    "_ensure_browser_operation_skills",
    "_escape_md_brackets",
    "_estimate_context_fullness",
    "_execute_action_via_beak",
    "_explicit_no_tool_goal",
    "_explicit_read_only_goal",
    "_explicit_source_paths",
    "_extract_final_answer",
    "_failed_verification_followup_guard",
    "_final_answer_needs_pre_emit_guard",
    "_finish_reason_is_length_limited",
    "_format_background_task_heartbeat",
    "_format_skill_catalog",
    "_goal_requests_code_mutation",
    "_guard_hit_recorder",
    "_guard_reason_for_user",
    "_has_unrecovered_beak_failure",
    "_image_blocks_from_attachments",
    "_is_format_violation",
    "_is_scoped_artifact_write",
    "_iter_model_stream_with_deadline",
    "_long_task_budget_limits",
    "_looks_like_image_attachment",
    "_looks_like_special_tool_envelope",
    "_looks_like_unfinished_work",
    "_mirror_checkpoint",
    "_narrow_research_iteration_limit",
    "_native_tool_calls_missing_required_args",
    "_normalized_tool_call_from_react_action",
    "_note_guard_impasse",
    "_observed_read_fallback_update",
    "_parse_action",
    "_parse_reasoning_action_fallback",
    "_parse_step",
    "_persist_react_trajectory",
    "_placeholder_observation",
    "_quiet_evidence_targets",
    "_react_completion_receipt",
    "_record_rejected_step",
    "_recover_explicit_read_actions",
    "_redundant_green_verification_guard",
    "_rehydrate_messages_from_steps",
    "_reset_checkpoint_mirror_for_tests",
    "_reset_disabled_set_for_tests",
    "_reset_guard_telemetry_for_tests",
    "_reset_kg_throttle_for_tests",
    "_reset_react_variants_for_tests",
    "_safe_for_streamdown",
    "_safe_public_update",
    "_should_auto_checkpoint",
    "_skill_available_in_executor",
    "_stage_model_timeout_s",
    "_reasoning_only_watchdog_s",
    "_stage_update_timeout_fallback",
    "_summarize_observation",
    "_todo_completion_before_write_guard",
    "_todo_prewrite_guard",
    "_todo_reconciliation_guard",
    "_tool_event_extras_from_beak_step",
    "_unfinished_implementation_recovery_needed",
    "_unverified_write_followup_guard",
    "get_react_variant_stats",
    "pick_react_variant",
    "record_react_variant_result",
]  # noqa: F822, SIM905 — names resolve lazily through __getattr__


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    for module_name in _MODULE_NAMES:
        module = import_module(f"{__package__}.{module_name}")
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"compatibility export has no provider: {name}")
