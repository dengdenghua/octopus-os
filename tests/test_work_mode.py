"""Unified work-mode resolver — the one model for a turn's work-type/scope."""

from __future__ import annotations

from runtime.core.cerebrum.work_mode import resolve_work_mode


def test_empty_context_is_general_non_code():
    wm = resolve_work_mode({})
    assert wm.is_code is False
    assert wm.scope == "none"
    assert wm.effective_workspace is None
    assert wm.agent_mode == "coder"  # default role even when not in code mode
    assert wm.mode == "" and wm.personal_mode == ""


def test_bound_project_is_code_with_project_scope():
    wm = resolve_work_mode({"workspace_path": "/repo"})
    assert wm.is_code is True
    assert wm.scope == "project"
    assert wm.effective_workspace == "/repo"


def test_personal_workspace_flips_into_code():
    """The merge: a personal thread with a per-thread cwd codes like a project."""
    wm = resolve_work_mode({"personal_workspace_enabled": True, "cwd": "/tmp/thread-123"})
    assert wm.is_code is True  # personal can code now
    assert wm.scope == "personal"  # …but labelled personal, not project
    assert wm.effective_workspace == "/tmp/thread-123"


def test_personal_general_and_research_are_work_styles_not_code_behavior():
    base = {
        "mode": "code",
        "capability_mode": "code",
        "personal_workspace_enabled": True,
        "personal_workspace_path": "/tmp/personal",
    }
    general = resolve_work_mode({**base, "personal_mode": "general"})
    research = resolve_work_mode({**base, "personal_mode": "research"})
    build = resolve_work_mode({**base, "personal_mode": "build"})

    assert general.scope == research.scope == build.scope == "personal"
    assert general.is_code is False
    assert research.is_code is False
    assert build.is_code is True


def test_workspace_scope_personal_also_enables():
    wm = resolve_work_mode({"workspace_scope": "personal", "personal_workspace_path": "/tmp/x"})
    assert wm.is_code is True and wm.scope == "personal"


def test_personal_cwd_without_enable_does_not_code():
    """A per-thread cwd alone (no enable / scope) stays non-code — parity with
    the inline guard."""
    wm = resolve_work_mode({"cwd": "/tmp/thread-123"})
    assert wm.is_code is False
    assert wm.effective_workspace is None
    assert wm.scope == "none"


def test_project_takes_precedence_over_personal():
    wm = resolve_work_mode(
        {
            "workspace_path": "/repo",
            "personal_workspace_enabled": True,
            "cwd": "/tmp/x",
        }
    )
    assert wm.effective_workspace == "/repo" and wm.scope == "project"


def test_mode_code_or_capability_forces_code_without_workspace():
    assert resolve_work_mode({"mode": "code"}).is_code is True
    assert resolve_work_mode({"capability_mode": "build"}).is_code is True


def test_metadata_fallback_is_read():
    wm = resolve_work_mode({"metadata": {"workspace_path": "/repo", "agent_mode": "architect"}})
    assert wm.is_code is True and wm.agent_mode == "architect"


def test_goal_mode_from_several_signals():
    assert resolve_work_mode({"goal_mode": True}).is_goal is True
    assert resolve_work_mode({"goal_mode": "goal"}).is_goal is True
    assert resolve_work_mode({"completion_policy": "goal"}).is_goal is True
    assert resolve_work_mode({"workflow_mode": "goal"}).is_goal is True
    assert resolve_work_mode({"goal_mode": "off"}).is_goal is False


def test_plan_or_spec():
    assert resolve_work_mode({"workflow_mode": "spec"}).is_plan_or_spec is True
    assert resolve_work_mode({"completion_policy": "plan"}).is_plan_or_spec is True
    assert resolve_work_mode({"workflow_mode": "goal"}).is_plan_or_spec is False


def test_swarm_detection():
    assert resolve_work_mode({"mode": "swarm"}).is_swarm is True
    assert resolve_work_mode({"capability_mode": "agent-swarm"}).is_swarm is True
    assert resolve_work_mode({"mode": "chat"}).is_swarm is False


def test_personal_and_string_flags_lowercased():
    wm = resolve_work_mode({"personal_mode": "Research", "mode": "CODE"})
    assert wm.personal_mode == "research"
    assert wm.mode == "code"


def test_mode_contract_keeps_case():
    wm = resolve_work_mode({"mode_contract": "MyContract"})
    assert wm.mode_contract == "MyContract"  # NOT lowercased


def test_project_signals_passthrough():
    sig = {"language": "python"}
    assert resolve_work_mode({"project_signals": sig}).project_signals is sig

