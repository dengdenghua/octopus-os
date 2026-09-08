"""Independent host model services retain permissions and control unattended calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from runtime.execution.model_services import background_model_calls_enabled, background_model_router
from runtime.platform.config.schema import AgentConfig, ExecutionConfig
from runtime.safety.approval.guardian_review import (
    AutoReviewApprovalProvider,
    approval_router_for_stack,
)


def stack_with_policy(value, **services):
    return SimpleNamespace(
        config=SimpleNamespace(execution=ExecutionConfig(background_model_calls=value)), **services
    )


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1])
def test_background_policy_rejects_non_boolean_config(invalid):
    with pytest.raises(ValidationError):
        ExecutionConfig(background_model_calls=invalid)


def test_explicit_service_does_not_load_a_native_planner():
    reviewer = object()
    background = object()

    class Host:
        approval_router = reviewer
        background_router = background

        @property
        def planner(self):
            raise AssertionError("independent services must not access the planner")

    host = Host()
    assert approval_router_for_stack(host) is reviewer
    assert background_model_router(host) is background
    host.approval_router = None
    host.background_router = None
    assert approval_router_for_stack(host) is None
    assert background_model_router(host) is None


def test_built_stack_inherits_services_but_preserves_explicit_none():
    from runtime.platform.config.builder import BuiltStack

    router = object()
    kwargs = dict(
        config=AgentConfig(),
        registry=None,
        journal=None,
        immunity=None,
        executor=None,
        runtime=None,
        planner=SimpleNamespace(router=router),
    )
    inherited = BuiltStack(**kwargs)
    assert inherited.approval_router is router
    assert inherited.background_router is router
    explicit = BuiltStack(**kwargs, approval_router=None, background_router=None)
    assert explicit.approval_router is None and explicit.background_router is None
    disabled = BuiltStack(
        **{**kwargs, "config": AgentConfig(execution={"background_model_calls": False})}
    )
    assert disabled.approval_router is router and disabled.background_router is None


def test_missing_review_service_denies_instead_of_borrowing_execution_model():
    from runtime.safety.approval.approval_gate import ApprovalRequest

    forbidden_router = Mock(side_effect=AssertionError("must not fall back"))
    host = SimpleNamespace(approval_router=None, planner=SimpleNamespace(router=forbidden_router))
    provider = AutoReviewApprovalProvider(approval_router_for_stack(host), user_intent="inspect")
    decision = provider.request(
        ApprovalRequest(
            thread_id="thread", tool_name="exec_shell", tool_call_id="call", args_preview="pytest"
        )
    )
    assert decision.approved is False
    forbidden_router.assert_not_called()


@pytest.mark.parametrize("selected,expected", [(None, True), (True, True), (False, False)])
def test_legacy_background_policy_and_independent_review(selected, expected):
    router = object()
    stack = stack_with_policy(selected, planner=SimpleNamespace(router=router))
    assert background_model_calls_enabled(stack) is expected
    assert background_model_router(stack) is (router if expected else None)
    assert approval_router_for_stack(stack) is router


def test_memory_distill_rechecks_policy_on_each_tick(monkeypatch):
    from runtime.cli_serve import register_memory_distill_task

    router = object()
    stack = stack_with_policy(True, background_router=router)
    runner = Mock()
    distill = Mock()
    monkeypatch.setenv("ECHO_MEMORY_DISTILL_SECONDS", "60")
    monkeypatch.setattr("runtime.memory.users.distill.distill_user_memory", distill)
    assert register_memory_distill_task(runner, stack) == 1
    tick = runner.add_periodic.call_args.kwargs["callback"]
    tick()
    assert distill.call_args.args == (router,)
    stack.config.execution = ExecutionConfig(background_model_calls=False)
    tick()
    assert distill.call_args.args == (None,)  # Deterministic local maintenance remains.


def test_disabled_policy_blocks_scheduler_and_queued_evolution(monkeypatch):
    from runtime.safety.evolution.auto_trigger import EvolutionAutoTrigger
    from runtime.safety.experiments.scheduler import CamouflageScheduler

    stack = stack_with_policy(False)
    evolve = Mock(side_effect=AssertionError("background model work must not run"))
    monkeypatch.setattr("runtime.memory.learning.deep_evolution.deep_evolve", evolve)
    auto = EvolutionAutoTrigger()
    auto.start(stack)
    auto._trigger_evolve("general")
    assert auto.status()["running"] is False
    assert auto.status()["background_model_calls"] is False
    evolve.assert_not_called()
    scheduler = CamouflageScheduler()
    scheduler._stack = stack_with_policy(True)
    scheduler.start(stack)
    assert scheduler._thread is None
    assert scheduler._stack is stack
    scheduler._auto_retire = Mock()
    scheduler._tick_once()
    scheduler._auto_retire.tick.assert_not_called()


def test_disabled_prompt_evolution_does_not_access_planner(tmp_path):
    from runtime.cli_serve import maybe_setup_prompt_evolution

    stack = stack_with_policy(False)
    result = maybe_setup_prompt_evolution(
        stack,
        Mock(),
        prompt_variants_path=tmp_path / "unused.yaml",
        evolve_interval_s=1,
        mutator_model="unused",
        color=False,
    )
    assert result == (None, 0)


def test_periodic_reflection_rechecks_policy_and_retains_local_forge():
    from runtime.cli_serve import register_reflection_tasks
    from runtime.core.cerebrum import LLMPlanner

    planner = object.__new__(LLMPlanner)
    methods = (
        "learn_from_journal",
        "learn_memories_from_journal",
        "learn_kg_from_journal",
        "assess_recipe_from_journal",
    )
    for method in methods:
        setattr(planner, method, Mock())
    stack = stack_with_policy(True, planner=planner, journal=object())
    runner = Mock()
    assert register_reflection_tasks(runner, stack, 60) == 5
    callbacks = {call.args[0]: call.args[2] for call in runner.add_periodic.call_args_list}
    for name, callback in callbacks.items():
        if name != "reflect_skill_forge":
            callback()
    for method in methods:
        getattr(planner, method).assert_called_once_with(stack.journal)
        getattr(planner, method).reset_mock()
    stack.config.execution = ExecutionConfig(background_model_calls=False)
    for name, callback in callbacks.items():
        if name != "reflect_skill_forge":
            callback()
    for method in methods:
        getattr(planner, method).assert_not_called()
    disabled_runner = Mock()
    assert register_reflection_tasks(disabled_runner, stack, 60) == 1
    assert disabled_runner.add_periodic.call_args.args[0] == "reflect_skill_forge"


@pytest.mark.parametrize("available", [True, False])
def test_codex_tool_review_uses_host_service_not_codex_model(tmp_path, monkeypatch, available):
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.suckers import SkillRegistry
    from runtime.safety.approval.approval_gate import ApprovalRequest

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(role_runner, "require_codex_backend_enabled", lambda: None)
    monkeypatch.setattr(role_runner, "deployment_mode", lambda: "local")
    monkeypatch.setattr(role_runner, "state_root_for_workspace", lambda _: tmp_path / "state")
    monkeypatch.setattr(role_runner, "codex_app_server_command", lambda _: ("codex", "app-server"))
    monkeypatch.setattr(role_runner, "compose_codex_role_instructions", lambda *a, **kw: "test")
    monkeypatch.setattr(
        role_runner,
        "_execution_profile",
        lambda *a, **kw: SimpleNamespace(
            proxy_required=True,
            effective_model="test/execution",
            reasoning_effort=None,
            provider_profile=None,
        ),
    )
    review = Mock(
        return_value=SimpleNamespace(text='{"outcome":"allow","risk":"low","reason":"test"}')
    )
    execution_call = Mock(side_effect=AssertionError("must not borrow execution model"))
    stack = stack_with_policy(
        False,
        approval_router=SimpleNamespace(call=review) if available else None,
        planner=SimpleNamespace(router=SimpleNamespace(call=execution_call)),
        executor=SimpleNamespace(registry=SkillRegistry()),
    )
    agent = SimpleNamespace(agent_id="test", capabilities={})
    context = {
        "workspace_path": str(tmp_path),
        "permission_mode": "acceptEdits",
        "model": "chatgpt/gpt-6-astra",
    }
    if not available:
        with pytest.raises(role_runner.CodexSecurityError, match="automatic approval review"):
            role_runner.build_codex_role_request(stack, agent, "inspect", context=context)
    else:
        request, broker, _ = role_runner.build_codex_role_request(
            stack, agent, "inspect", context=context
        )
        assert request.approval_reviewer == "auto_review"
        decision = broker._approval_provider.request(
            ApprovalRequest(
                thread_id=request.outer_thread_id,
                tool_name="read_file",
                tool_call_id="read",
                args_preview="notes.txt",
            )
        )
        assert decision.approved
        assert review.call_args.args[0].model == "auto"
    execution_call.assert_not_called()
