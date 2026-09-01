"""Project caller context into the metadata trusted by tool sessions.

The projection is shared by every native tool loop.  It lives below the
gateway so execution backends can preserve the same scope without reaching
back into a transport/router module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_NESTED_CONTEXT_KEYS = (
    "mode",
    "team_id",
    "extra_workspaces",
    "workspace_path",
    "workspace_scope",
    "personal_workspace_path",
    "personal_workspace_enabled",
    "sandbox_mode",
    "permission_mode",
    "approval_policy",
    "execution_environment",
    "capability_mode",
    "code_mode",
    "agent_mode",
    "personal_mode",
    "personal_instructions",
    "mode_preset",
    "workflow_preset",
    "skill_pack_profile",
    "verification_policy",
    "mode_contract",
    "default_skill_packs",
    "default_plugins",
    "browser_regression_enabled",
    "project_signals",
    "runtime_surfaces",
    "tool_surface",
    "browser_operation_mode",
    "chrome_operation_mode",
    "browser_surface",
    "browser_session_policy",
    "browser_track_preference",
    "browser_permission_policy",
    "browser_evidence_policy",
    "automation_target",
    "allowed_write_paths",
    "sandbox_policy",
    # Prompt-injection taint must survive the projection. Producers put it on the
    # plain context dict (subagents/bridge.py, parallel_agents/orchestrator.py,
    # misc/parallel_runner.py) while the codex broker reads it back off session
    # metadata (codex_backend/dynamic_tools.py). Dropping it here silently
    # downgrades a tainted turn to "none" inside the child execution, so the
    # approval gate stops forcing `ask` on risky tools — the exact laundering the
    # taint is meant to prevent. Taint only ever tightens, never widens.
    "_inherited_injection_taint",
    "injection_taint",
)

_FLAT_CONTEXT_KEYS = (
    "mode",
    "team_id",
    "extra_workspaces",
    "workspace_scope",
    "personal_workspace_path",
    "personal_workspace_enabled",
    "attachment_read_roots",
    "sandbox_mode",
    "permission_mode",
    "approval_policy",
    "execution_environment",
    "capability_mode",
    "code_mode",
    "agent_mode",
    "personal_mode",
    "personal_instructions",
    "mode_preset",
    "workflow_preset",
    "skill_pack_profile",
    "verification_policy",
    "mode_contract",
    "default_skill_packs",
    "default_plugins",
    "browser_regression_enabled",
    "project_signals",
    "runtime_surfaces",
    "tool_surface",
    "browser_operation_mode",
    "chrome_operation_mode",
    "browser_surface",
    "browser_session_policy",
    "browser_track_preference",
    "browser_permission_policy",
    "browser_evidence_policy",
    "automation_target",
    "allowed_write_paths",
    "sandbox_policy",
    # See the note on the nested tuple: taint must cross the projection boundary
    # or the child execution silently runs untainted.
    "_inherited_injection_taint",
    "injection_taint",
)


def project_tool_session_metadata(
    user_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the allowlisted context that may survive tool-thread hops."""

    context = user_context or {}
    metadata: dict[str, Any] = {}
    nested = context.get("metadata")
    if isinstance(nested, Mapping):
        for key in _NESTED_CONTEXT_KEYS:
            value = nested.get(key)
            if value is not None:
                metadata[key] = value

    for key in _FLAT_CONTEXT_KEYS:
        value = context.get(key)
        if value is not None:
            metadata.setdefault(key, value)

    workspace_path = context.get("workspace_path")
    if isinstance(workspace_path, str) and workspace_path.strip():
        workspace_path = workspace_path.strip()
        metadata.setdefault("workspace_path", workspace_path)
        extra_workspaces = metadata.get("extra_workspaces")
        if not isinstance(extra_workspaces, list):
            metadata["extra_workspaces"] = [workspace_path]
        elif workspace_path not in extra_workspaces:
            metadata["extra_workspaces"] = [workspace_path, *extra_workspaces]
    return metadata


__all__ = ["project_tool_session_metadata"]
