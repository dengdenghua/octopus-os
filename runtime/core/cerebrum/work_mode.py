"""Unified work-mode resolution — one model for "what kind of work is this turn".

react_loop previously resolved a pile of overlapping mode signals inline
(``mode`` / ``capability_mode`` / ``agent_mode`` / ``personal_mode`` /
``workflow_mode`` / ``completion_policy`` / ``goal_mode`` / ``workspace_scope`` /
``workspace_path`` / ``personal_workspace_*``) across ~120 scattered lines. After
the project↔personal workspace merge, "project vs personal vs code" is no longer a
hard wall — a personal thread gets a per-thread cwd as an *effective* workspace and
flips into code mode like a bound project. This collapses that whole decision into
one pure resolver returning a single :class:`WorkMode`, so the work-type/scope of a
turn is decided in exactly one place.

This is the **work-type axis only**. Coordination (how many agents run a turn) is a
separate axis — see :mod:`runtime.memory.cowork.turn_plan`; the single-user case is
just its ``n == 1`` degenerate, not a different model.

Field nullability/casing mirrors the original inline reads exactly so behaviour is
preserved when react_loop reads these off the resolved object instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Coordination "swarm" aliases — kept here so the swarm check reads off the same
# resolved mode value react_loop uses (the iteration-lift side effect stays there).
SWARM_ALIASES: frozenset[str] = frozenset({"swarm", "swarms", "agent_swarm", "agent-swarm"})


def _lc(uc: dict[str, Any], metadata: dict[str, Any], key: str) -> str:
    """Lowercased ``uc[key] or metadata[key] or ""`` — the common pattern."""
    return str(uc.get(key) or metadata.get(key) or "").lower()


@dataclass(frozen=True)
class WorkMode:
    """The resolved work-type/scope of a single turn (one source of truth)."""

    # Workspace — original nullability preserved (str | None) for parity.
    project_workspace: str | None  # bound project dir (workspace_path)
    personal_workspace: str | None  # per-thread cwd / personal_workspace_path
    effective_workspace: str | None  # project, else personal cwd when enabled
    workspace_scope_value: str  # raw "workspace_scope" ("project"|"personal"|"")

    # Work-type signals (lowercased; "" when absent, except agent_mode→"coder").
    mode: str
    capability_mode: str
    agent_mode: str  # builder | coder | architect (code role)
    personal_mode: str  # build | research | general (personal-space role)
    workflow_mode: str
    completion_policy: str
    workflow_preset: str
    mode_contract: str  # NOT lowercased (original keeps case)

    # Passthrough (used downstream as-is).
    goal_mode_value: Any
    project_signals: Any

    # Derived.
    is_code: bool
    is_goal: bool
    is_plan_or_spec: bool

    @property
    def scope(self) -> str:
        """``project`` | ``personal`` | ``none`` — where the effective workspace
        comes from. A label, not a capability gate: both project and personal can
        run code (``is_code``)."""
        if isinstance(self.project_workspace, str) and self.project_workspace.strip():
            return "project"
        if isinstance(self.effective_workspace, str) and self.effective_workspace.strip():
            return "personal"
        return "none"

    @property
    def is_swarm(self) -> bool:
        return self.mode in SWARM_ALIASES or self.capability_mode in SWARM_ALIASES


def resolve_work_mode(user_context: dict[str, Any] | None) -> WorkMode:
    """Fold the scattered per-turn mode signals into one :class:`WorkMode`.

    Pure: depends only on ``user_context`` (and its nested ``metadata``), not on the
    goal text or iteration budget — those goal-dependent decisions (research-by-text,
    iteration lifts) stay in react_loop and read off this object.
    """
    uc = user_context or {}
    metadata = uc.get("metadata") or {}

    # ── Workspace (project → personal-cwd fallback) ──────────────────────────
    project_wp = uc.get("workspace_path") or metadata.get("workspace_path")
    personal_wp = (
        uc.get("personal_workspace_path")
        or metadata.get("personal_workspace_path")
        or uc.get("cwd")
        or metadata.get("cwd")
    )
    workspace_scope_value = (
        str(uc.get("workspace_scope") or metadata.get("workspace_scope") or "").strip().lower()
    )

    effective_wp = project_wp
    if not (isinstance(effective_wp, str) and effective_wp.strip()):
        personal_enabled = (
            uc.get("personal_workspace_enabled") is True
            or metadata.get("personal_workspace_enabled") is True
            or workspace_scope_value == "personal"
        )
        if personal_enabled and isinstance(personal_wp, str) and personal_wp.strip():
            effective_wp = personal_wp

    # ── Work-type signals ────────────────────────────────────────────────────
    mode = _lc(uc, metadata, "mode")
    capability_mode = _lc(uc, metadata, "capability_mode")
    agent_mode = str(uc.get("agent_mode") or metadata.get("agent_mode") or "coder").lower()
    workflow_preset = (
        str(uc.get("workflow_preset") or metadata.get("workflow_preset") or "").strip().lower()
    )
    workflow_mode = (
        str(uc.get("workflow_mode") or metadata.get("workflow_mode") or "").strip().lower()
    )
    # Backward compatibility: codex_mode is deprecated, map to workflow_mode
    if not workflow_mode:
        workflow_mode = (
            str(uc.get("codex_mode") or metadata.get("codex_mode") or "").strip().lower()
        )
    completion_policy = (
        str(uc.get("completion_policy") or metadata.get("completion_policy") or "").strip().lower()
    )
    mode_contract = str(uc.get("mode_contract") or metadata.get("mode_contract") or "").strip()
    personal_mode = (
        str(uc.get("personal_mode") or metadata.get("personal_mode") or "").strip().lower()
    )
    project_signals = uc.get("project_signals") or metadata.get("project_signals")

    # ── Derived ──────────────────────────────────────────────────────────────
    goal_mode_value = (
        uc.get("goal_mode")
        or metadata.get("goal_mode")
        or uc.get("completion_policy")
        or metadata.get("completion_policy")
    )
    is_goal = (
        goal_mode_value is True
        or (
            isinstance(goal_mode_value, str)
            and goal_mode_value.lower() in {"goal", "goal_mode", "true"}
        )
        or workflow_mode == "goal"
        or completion_policy == "goal"
    )

    # An isolated personal workspace is a capability surface, not by itself a
    # request to behave like a coding agent. Personal general/research turns
    # keep file tools available through ``capability_mode=code`` while using
    # their own operating contracts. Personal build remains full code mode.
    _personal_non_code_mode = (
        not (isinstance(project_wp, str) and project_wp.strip())
        and isinstance(effective_wp, str)
        and bool(effective_wp.strip())
        and personal_mode in {"general", "research"}
    )
    is_code = not _personal_non_code_mode and bool(
        uc.get("mode") == "code"
        or metadata.get("mode") == "code"
        or uc.get("capability_mode")
        or metadata.get("capability_mode")
        or (isinstance(effective_wp, str) and effective_wp.strip())
    )

    is_plan_or_spec = workflow_mode in {"plan", "spec"} or completion_policy in {"plan", "spec"}

    return WorkMode(
        project_workspace=project_wp,
        personal_workspace=personal_wp,
        effective_workspace=effective_wp,
        workspace_scope_value=workspace_scope_value,
        mode=mode,
        capability_mode=capability_mode,
        agent_mode=agent_mode,
        personal_mode=personal_mode,
        workflow_mode=workflow_mode,
        completion_policy=completion_policy,
        workflow_preset=workflow_preset,
        mode_contract=mode_contract,
        goal_mode_value=goal_mode_value,
        project_signals=project_signals,
        is_code=is_code,
        is_goal=is_goal,
        is_plan_or_spec=is_plan_or_spec,
    )


__all__ = ["WorkMode", "resolve_work_mode", "SWARM_ALIASES"]
