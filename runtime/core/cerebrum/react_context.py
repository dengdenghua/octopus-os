"""ReAct context assembly: token budget, compression, prompt building.

This module is a thin facade that re-exports the implementation from the
``_react_context_*`` submodules. Pure structural split — no logic change.
"""

from __future__ import annotations

from runtime.core.cerebrum._react_context_attachments import (
    _attachment_context_appendix,
    _build_user_message_content,
    _image_blocks_from_attachments,
    _looks_like_image_attachment,
    _prefetch_related_files,
    _restore_messages_from_checkpoint,
    _serialize_messages_for_checkpoint,
)
from runtime.core.cerebrum._react_context_code import (
    _build_code_agent_mode_prompt,
    _build_code_context_prelude,
    _build_personal_agent_mode_prompt,
    _build_project_signals_prompt,
    _build_workflow_preset_prompt,
    _find_code_context_readme,
    _find_code_context_style_file,
    _read_code_context_file,
    _task_acceptance_context,
)
from runtime.core.cerebrum._react_context_helpers import (
    _compress_context,
    _content_to_text,
    _ensure_context_budget,
    _estimate_messages_tokens,
    _estimate_tokens,
    _format_skill_catalog,
    _suffix_within_token_budget,
    _summarize_messages,
    _trim_message_to_budget,
    context_budget_tokens_for_model,
    context_compaction_message_target_tokens,
    context_compaction_target_tokens,
)
from runtime.core.cerebrum._react_context_project import (
    _build_project_profile_prompt,
    _collect_initial_diagnostics,
    _git_status_summary,
    _load_project_rules,
)

__all__ = [
    "context_budget_tokens_for_model",
    "context_compaction_message_target_tokens",
    "context_compaction_target_tokens",
    "_attachment_context_appendix",
    "_build_code_agent_mode_prompt",
    "_build_code_context_prelude",
    "_build_personal_agent_mode_prompt",
    "_build_project_profile_prompt",
    "_build_project_signals_prompt",
    "_build_user_message_content",
    "_build_workflow_preset_prompt",
    "_collect_initial_diagnostics",
    "_compress_context",
    "_content_to_text",
    "_ensure_context_budget",
    "_estimate_messages_tokens",
    "_estimate_tokens",
    "_find_code_context_readme",
    "_find_code_context_style_file",
    "_format_skill_catalog",
    "_git_status_summary",
    "_image_blocks_from_attachments",
    "_load_project_rules",
    "_looks_like_image_attachment",
    "_prefetch_related_files",
    "_read_code_context_file",
    "_restore_messages_from_checkpoint",
    "_serialize_messages_for_checkpoint",
    "_suffix_within_token_budget",
    "_summarize_messages",
    "_task_acceptance_context",
    "_trim_message_to_budget",
]
