"""Tests for cheap-model routing in the subagent dispatch lane.

The bridge accepts a ``use_cheap_model`` keyword that, when true and
no explicit ``model_name`` is pinned in context, injects a
project-wide cheap default into the merged context the runner sees.
``call_agent_parallel`` carries a per-spec ``cheap`` flag with role-
based defaults so research-style roles auto-route to the cheap tier
while architects / synthesizers stay on the parent's primary model.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.subagents import bridge
from runtime.execution.suckers import delegation_skills

# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def capture_runner(monkeypatch):
    """Install stubs that capture the context the dispatcher passes to
    the runner.

    Builtin roles like ``researcher`` / ``architect`` go through the
    ephemeral lane (``run_ephemeral_role`` / ``run_ephemeral_definition``)
    not the persistent ``_RUNNER`` path, so we stub the ephemeral runner
    too. Either path lands here and writes the context it observed.
    """
    captured: dict[str, Any] = {}

    def _runner(prompt, *, subagent_name, context):
        captured["prompt"] = prompt
        captured["subagent_name"] = subagent_name
        captured["context"] = dict(context or {})
        return "Final Answer: ok."

    def _ephemeral_runner(call):
        captured["prompt"] = call.user_prompt
        captured["subagent_name"] = call.role.id
        captured["context"] = dict(call.context or {})
        return "Final Answer: ok."

    from runtime.execution.suckers import ephemeral_agents

    orig_runner = bridge._RUNNER
    orig_eph = ephemeral_agents._EPHEMERAL_RUNNER
    bridge._RUNNER = _runner
    ephemeral_agents._EPHEMERAL_RUNNER = _ephemeral_runner
    try:
        yield captured
    finally:
        bridge._RUNNER = orig_runner
        ephemeral_agents._EPHEMERAL_RUNNER = orig_eph


@pytest.fixture(autouse=True)
def _clear_cheap_model_env(monkeypatch):
    """Ensure each test starts with a clean env override."""
    monkeypatch.delenv("ECHO_SUBAGENT_CHEAP_MODEL", raising=False)


# ── _resolve_cheap_subagent_model ────────────────────────────


def test_resolve_returns_env_override_when_set(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "my-org-cheap-v2")
    assert bridge._resolve_cheap_subagent_model() == "my-org-cheap-v2"


def test_resolve_strips_whitespace_and_ignores_blank(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "   ")
    # Blank with no configured economy model leaves routing on the known-good
    # planner model instead of inventing an external model id.
    assert bridge._resolve_cheap_subagent_model() is None


def test_resolve_returns_default_when_no_env() -> None:
    assert bridge._resolve_cheap_subagent_model() is None


# ── call_subagent + use_cheap_model ──────────────────────────


def test_call_subagent_injects_configured_cheap_model_into_context(
    monkeypatch, capture_runner
) -> None:
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "glm-4-flash")
    bridge.call_subagent(
        agent_id="researcher",
        prompt="dig into X",
        use_cheap_model=True,
    )
    assert capture_runner["context"].get("model_name") == "glm-4-flash"


def test_call_subagent_explicit_model_name_wins_over_cheap(capture_runner) -> None:
    bridge.call_subagent(
        agent_id="researcher",
        prompt="dig into X",
        context={"model_name": "explicit-x"},
        use_cheap_model=True,
    )
    # The caller pinned a model — cheap routing must NOT override it.
    assert capture_runner["context"].get("model_name") == "explicit-x"


def test_call_subagent_without_cheap_flag_does_not_inject(capture_runner) -> None:
    bridge.call_subagent(agent_id="researcher", prompt="dig into X")
    assert "model_name" not in capture_runner["context"]


def test_call_subagent_env_override_is_used_when_cheap(
    monkeypatch,
    capture_runner,
) -> None:
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "qwen-flash")
    bridge.call_subagent(
        agent_id="researcher",
        prompt="dig into X",
        use_cheap_model=True,
    )
    assert capture_runner["context"].get("model_name") == "qwen-flash"


# ── _call_agent_parallel role-based defaults ─────────────────


def test_parallel_researcher_defaults_to_configured_cheap(monkeypatch, capture_runner) -> None:
    """A researcher spec with no explicit ``cheap`` flag auto-routes
    to the cheap model."""
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "glm-4-flash")
    delegation_skills._call_agent_parallel(
        specs=[{"role": "researcher", "task": "find X"}],
        timeout_s=5,
    )
    assert capture_runner["context"].get("model_name") == "glm-4-flash"


def test_parallel_explicit_cheap_false_disables_auto_route(capture_runner) -> None:
    """An explicit ``cheap: False`` on a researcher spec wins over the
    auto-cheap default."""
    delegation_skills._call_agent_parallel(
        specs=[{"role": "researcher", "task": "find X", "cheap": False}],
        timeout_s=5,
    )
    assert "model_name" not in capture_runner["context"]


def test_parallel_architect_defaults_to_primary(capture_runner) -> None:
    """``architect`` is a heavy-reasoning role and stays on the primary
    model unless the spec opts in explicitly."""
    delegation_skills._call_agent_parallel(
        specs=[{"role": "architect", "task": "design Y"}],
        timeout_s=5,
    )
    assert "model_name" not in capture_runner["context"]


def test_parallel_explicit_cheap_true_on_architect_routes_cheap(
    monkeypatch,
    capture_runner,
) -> None:
    """Explicit ``cheap: True`` overrides the architect default."""
    monkeypatch.setenv("ECHO_SUBAGENT_CHEAP_MODEL", "glm-4-flash")
    delegation_skills._call_agent_parallel(
        specs=[{"role": "architect", "task": "summarize Y", "cheap": True}],
        timeout_s=5,
    )
    assert capture_runner["context"].get("model_name") == "glm-4-flash"


def test_role_defaults_helper_classification() -> None:
    # Research-style roles → cheap.
    assert delegation_skills._role_defaults_to_cheap("researcher") is True
    assert delegation_skills._role_defaults_to_cheap("fact_checker") is True
    assert delegation_skills._role_defaults_to_cheap("security-review") is True
    assert delegation_skills._role_defaults_to_cheap("verifier") is True
    assert delegation_skills._role_defaults_to_cheap("debugger") is True
    # Heavy-reasoning roles → primary.
    assert delegation_skills._role_defaults_to_cheap("architect") is False
    assert delegation_skills._role_defaults_to_cheap("synthesizer") is False
    assert delegation_skills._role_defaults_to_cheap("designer") is False
    assert delegation_skills._role_defaults_to_cheap("implementer") is False
    # Unknown roles default to NOT cheap so user-defined agents keep
    # using the primary model unless asked otherwise.
    assert delegation_skills._role_defaults_to_cheap("totally_made_up") is False
    assert delegation_skills._role_defaults_to_cheap("") is False


# ── team_runner role classification ──────────────────────────


def test_team_runner_role_classification() -> None:
    from runtime.safety.organization.team_runner import _role_uses_cheap_model
    from runtime.safety.organization.topology import Role

    # Heavy-reasoning roles → primary.
    assert _role_uses_cheap_model(Role.PLANNER) is False
    assert _role_uses_cheap_model(Role.GENERATOR) is False
    assert _role_uses_cheap_model(Role.SYNTHESIZER) is False
    # Research / review roles → cheap.
    assert _role_uses_cheap_model(Role.RESEARCHER) is True
    assert _role_uses_cheap_model(Role.CRITIC) is True
    assert _role_uses_cheap_model(Role.EVALUATOR) is True


def test_team_runner_passes_cheap_flag_per_role() -> None:
    """When TeamRunner invokes a researcher role through a custom
    role_caller, the caller receives ``use_cheap_model=True``."""
    from runtime.safety.organization.team_runner import TeamRunner
    from runtime.safety.organization.topology import (
        AgentSpec,
        CoordinationProtocol,
        Role,
        TeamTopology,
    )

    seen: list[tuple[Role, bool]] = []

    def caller(*, agent_id, prompt, context, timeout_seconds, use_cheap_model=False):
        # Use the team_role from context to classify (the bridge sets
        # this before calling).
        team_role = context.get("team_role", "")
        seen.append((team_role, use_cheap_model))
        return {"output": f"<{agent_id}>", "success": True}

    topo = TeamTopology(
        name="cheap-routing-smoke",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="gen"),
            Role.RESEARCHER: AgentSpec(agent_id="res"),
            Role.SYNTHESIZER: AgentSpec(agent_id="syn"),
        },
    )
    runner = TeamRunner(role_caller=caller)
    runner.run(topo, "build something")
    role_to_cheap = {r: cheap for r, cheap in seen}
    assert role_to_cheap[str(Role.GENERATOR)] is False
    assert role_to_cheap[str(Role.SYNTHESIZER)] is False
    assert role_to_cheap[str(Role.RESEARCHER)] is True


def test_team_runner_legacy_caller_without_cheap_kwarg_still_works() -> None:
    """A custom role_caller written before cheap routing landed (no
    ``use_cheap_model`` kwarg) keeps working via the TypeError fallback
    path."""
    from runtime.safety.organization.team_runner import TeamRunner
    from runtime.safety.organization.topology import (
        AgentSpec,
        CoordinationProtocol,
        Role,
        TeamTopology,
    )

    def legacy_caller(*, agent_id, prompt, context, timeout_seconds):
        return {"output": f"<{agent_id}>", "success": True}

    topo = TeamTopology(
        name="legacy-caller-smoke",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="gen"),
        },
    )
    runner = TeamRunner(role_caller=legacy_caller)
    result = runner.run(topo, "task")
    assert result.success is True
