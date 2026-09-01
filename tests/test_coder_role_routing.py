"""Cross-surface routing regressions for the standard Kane/Coder role."""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.agents import Agent
from runtime.execution.arms.base import ArmPool
from runtime.execution.arms.presets import make_web_read_arm
from runtime.execution.codex_backend.backend import CodexBackendUnavailable
from runtime.execution.codex_backend.role_runner import CodexRoleExecution
from runtime.execution.parallel_agents import make_stack_subagent_runner
from runtime.platform.process.session import Session, session_scope
from runtime.projectos.llm_hooks import subagent_execute_task
from runtime.projectos.model import Task
from runtime.safety.approval.approval_gate import (
    ApprovalProvider,
    AutoApproveProvider,
    AutoDenyProvider,
)


def _registered_coder(stack: Any, registry: Any) -> Agent:
    coder = Agent(
        agent_id="coder",
        display_name="Kane",
        description="White Ghost squad coder",
        soul="You are Kane, the White Ghost squad's coder.",
        arms=ArmPool([make_web_read_arm(stack.runtime)]),
        icon="💻",
        model="auto",
        capabilities={"execution_backend": "codex_app_server"},
    )
    registry.register(coder)
    return coder


def test_standard_coder_keeps_identity_across_runner_call_agent_and_projectos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.subagents import get_sub_agent_runner, set_sub_agent_runner
    from runtime.execution.suckers import delegation_skills
    from runtime.execution.suckers._delegation_skills_agent import _call_agent
    from tests.test_parallel_agents_stack_runner import _build_registry, _build_stack

    stack = _build_stack()
    registry = _build_registry()
    coder = _registered_coder(stack, registry)
    calls: list[dict[str, Any]] = []

    def fake_codex_role(
        incoming_stack: Any,
        incoming_agent: Any,
        goal: str,
        **kwargs: Any,
    ) -> CodexRoleExecution:
        assert incoming_stack is stack
        calls.append(
            {
                "agent_id": incoming_agent.agent_id,
                "display_name": incoming_agent.display_name,
                "goal": goal,
                "context": dict(kwargs.get("context") or {}),
            }
        )
        return CodexRoleExecution("Kane completed it", True, "completed")

    monkeypatch.setattr(
        "runtime.execution.codex_backend.role_runner.run_agent_role_sync",
        fake_codex_role,
    )
    runner = make_stack_subagent_runner(stack=stack, agent_registry=registry)

    assert (
        runner(
            "direct delegated task",
            subagent_name="coder",
            context={"workspace_path": str(tmp_path)},
            cancel_event=threading.Event(),
        )
        == "Kane completed it"
    )

    previous_runner = get_sub_agent_runner()
    set_sub_agent_runner(runner)
    monkeypatch.setattr(
        delegation_skills,
        "_check_absolute_cap",
        lambda _turn_id, *, budget=None: (0, True),
    )
    monkeypatch.setattr(delegation_skills, "_record_delegation", lambda *_a, **_kw: None)
    try:
        delegated = _call_agent(
            agent_id="coder",
            prompt="group member task",
            context={"workspace_path": str(tmp_path)},
        )
    finally:
        set_sub_agent_runner(previous_runner)

    assert delegated["success"] is True
    assert delegated["agent_id"] == "coder"
    assert delegated["display_name"] == "Kane"
    assert "resolved_to" not in delegated

    project_output = subagent_execute_task(
        Task(
            id="MS1-T1",
            milestone_id="MS1",
            type="code",
            goal="project task",
            assigned_agent="coder",
        ),
        {
            "thread_id": "project-thread",
            "owner_id": "person-7",
            "tenant_id": "tenant-3",
            "project_id": "project-1",
            "workspace_path": str(tmp_path),
        },
        subagent_runner=runner,
    )
    assert project_output == "Kane completed it"
    assert isinstance(calls[-1]["context"].get("caller_session"), Session)
    assert calls[-1]["context"]["caller_session"].actor == "person-7"

    from runtime.safety.organization import (
        AgentSpec,
        CoordinationProtocol,
        Role,
        TeamTopology,
    )
    from runtime.safety.organization.team_runner import TeamRunner

    topology = TeamTopology(
        name="coder_topology",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="coder")},
    )
    previous_runner = get_sub_agent_runner()
    set_sub_agent_runner(runner)
    try:
        with session_scope(
            Session(
                actor="person-7",
                thread_id="topology-thread",
                metadata={
                    "tenant_id": "tenant-3",
                    "workspace_path": str(tmp_path),
                },
            )
        ):
            topology_result = TeamRunner(timeout_seconds=30).run(
                topology,
                "topology task",
                context={"workspace_path": str(tmp_path)},
            )
    finally:
        set_sub_agent_runner(previous_runner)

    assert topology_result.success is True
    assert topology_result.final_output == "Kane completed it"
    assert topology_result.role_outputs[0].agent_id == "coder"
    assert [call["agent_id"] for call in calls] == ["coder", "coder", "coder", "coder"]
    assert calls[-2]["context"]["source"] == "projectos_task"
    assert calls[-2]["context"]["tenant_id"] == "tenant-3"
    assert calls[-1]["context"]["caller_session"].actor == "person-7"
    assert coder.soul.startswith("You are Kane")


def test_production_coder_rejects_context_selected_account_without_trusted_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.codex_backend.security import CodexSecurityError

    monkeypatch.setattr(role_runner, "require_codex_backend_enabled", lambda: None)
    monkeypatch.setattr(role_runner, "deployment_mode", lambda: "production")
    monkeypatch.setattr(role_runner, "current_session", lambda: None)

    with pytest.raises(CodexSecurityError, match="trusted principal session"):
        role_runner.build_codex_role_request(
            SimpleNamespace(),
            SimpleNamespace(agent_id="coder", capabilities={}),
            "do the work",
            context={
                "workspace_path": str(tmp_path),
                "owner_actor_id": "attacker-selected-actor",
                "tenant_id": "attacker-selected-tenant",
            },
        )


def test_standard_coder_ignores_ordinary_model_context_and_accepts_only_opaque_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.codex_backend.model_profile import CodexModelPreference
    from runtime.platform.models import custom_model_flags

    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: {})
    router = SimpleNamespace(default_model="deepseek-v4", call=lambda _request: None)
    stack = SimpleNamespace(
        config=SimpleNamespace(planner=SimpleNamespace(model="deepseek-v4")),
        planner=SimpleNamespace(planner_model="deepseek-v4", router=router),
    )
    coder = SimpleNamespace(
        agent_id="coder",
        model=None,
        capabilities={"execution_backend": "codex_app_server"},
    )
    preference = CodexModelPreference(
        mode="chatgpt",
        model="gpt-account-choice",
        reasoning_effort="high",
    )
    ordinary = role_runner._execution_profile(
        stack,
        coder,
        {
            "model_name": "deepseek-from-realtime",
            "reasoning_effort": "max",
        },
        preference=preference,
    )
    assert ordinary.effective_model == "gpt-account-choice"
    assert ordinary.reasoning_effort == "high"

    trusted = Session(
        actor="alice",
        metadata={
            "tenant_id": "tenant-a",
            "_server_codex_execution_override": role_runner.ServerCodexExecutionOverride(
                model="gpt-server-override",
                reasoning_effort="xhigh",
            ),
        },
    )
    overridden = role_runner._execution_profile(
        stack,
        coder,
        {
            "caller_session": trusted,
            "model_name": "still-ignored",
            "reasoning_effort": "minimal",
        },
        preference=preference,
    )
    assert overridden.effective_model == "gpt-server-override"
    assert overridden.reasoning_effort == "xhigh"

    embedded = SimpleNamespace(
        agent_id="coder",
        model=None,
        capabilities={
            "execution_backend": "codex_app_server",
        },
    )
    embedded_profile = role_runner._execution_profile(
        stack,
        embedded,
        {"model_name": "gpt-ignored", "reasoning_effort": "low"},
        preference=preference,
    )
    assert embedded_profile.effective_model == "gpt-account-choice"
    assert embedded_profile.reasoning_effort == "high"


def test_connector_bridge_requires_principal_selection_and_removes_echo_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.codex_backend.model_profile import (
        CodexModelPreference,
        CodexModelPreferenceStore,
    )
    from runtime.platform.models import custom_model_flags

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    monkeypatch.setattr(role_runner, "require_codex_backend_enabled", lambda: None)
    monkeypatch.setattr(role_runner, "deployment_mode", lambda: "local")
    monkeypatch.setattr(role_runner, "state_root_for_workspace", lambda _workspace: state_root)
    monkeypatch.setattr(
        role_runner,
        "codex_app_server_command",
        lambda _agent: ("codex", "app-server", "--listen", "stdio://"),
    )
    monkeypatch.setattr(
        role_runner,
        "compose_codex_role_instructions",
        lambda *_args, **_kwargs: "role instructions",
    )
    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: {})

    class _Broker:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.catalog = SimpleNamespace(
                specs=(
                    {
                        "type": "function",
                        "name": "read_file",
                        "description": "read",
                        "inputSchema": {"type": "object"},
                    },
                )
            )

    monkeypatch.setattr(role_runner, "CodexDynamicToolBroker", _Broker)
    stack = SimpleNamespace(
        config=SimpleNamespace(planner=SimpleNamespace(model="system-model")),
        planner=SimpleNamespace(
            planner_model="system-model",
            router=SimpleNamespace(default_model="system-model", call=lambda _request: None),
        ),
        executor=SimpleNamespace(registry=object()),
    )
    agent = SimpleNamespace(agent_id="researcher", model=None, capabilities={})

    with pytest.raises(role_runner.CodexSecurityError, match="not enabled"):
        role_runner.build_codex_role_request(
            stack,
            agent,
            "List recent files",
            context={"workspace_path": str(workspace), "_codex_app_id": "google_drive"},
        )

    CodexModelPreferenceStore(state_root / "model_profile.json").write(
        None,
        CodexModelPreference(mode="chatgpt", app_ids=("google_drive",)),
    )
    request, _broker, _provider = role_runner.build_codex_role_request(
        stack,
        agent,
        "List recent files",
        context={"workspace_path": str(workspace), "_codex_app_id": "google_drive"},
    )

    assert request.selected_app_ids == ("google_drive",)
    assert request.app_mentions == (("google_drive", "google_drive"),)
    assert request.dynamic_tools == ()
    assert request.dynamic_tool_handler is None
    assert "CONNECTOR BRIDGE" in str(request.developer_instructions)


def test_audit_sandbox_is_read_only_for_direct_group_subagent_and_projectos_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.codex_backend.security import (
        PERMISSION_PROFILE,
        CodexSecurityPolicy,
        CodexSidecarSecurity,
    )
    from runtime.platform.models import custom_model_flags

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "model-state"
    monkeypatch.setattr(role_runner, "require_codex_backend_enabled", lambda: None)
    monkeypatch.setattr(role_runner, "deployment_mode", lambda: "local")
    monkeypatch.setattr(role_runner, "state_root_for_workspace", lambda _workspace: state_root)
    monkeypatch.setattr(
        role_runner,
        "codex_app_server_command",
        lambda _agent: ("codex", "app-server", "--listen", "stdio://"),
    )
    monkeypatch.setattr(
        role_runner,
        "compose_codex_role_instructions",
        lambda *_a, **_kw: "trusted coder instructions",
    )
    monkeypatch.setattr(custom_model_flags, "read_custom_models", lambda: {})

    class _Broker:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.catalog = SimpleNamespace(specs=())

    monkeypatch.setattr(role_runner, "CodexDynamicToolBroker", _Broker)
    router = SimpleNamespace(default_model="deepseek-system", call=lambda _request: None)
    stack = SimpleNamespace(
        config=SimpleNamespace(planner=SimpleNamespace(model="deepseek-system")),
        planner=SimpleNamespace(planner_model="deepseek-system", router=router),
        executor=SimpleNamespace(registry=object()),
    )
    coder = SimpleNamespace(
        agent_id="coder",
        model=None,
        capabilities={"execution_backend": "codex_app_server"},
    )

    def _parent(source: str, metadata: dict[str, Any]) -> Session:
        return Session(
            actor="alice",
            thread_id=f"{source}-thread",
            turn_id=f"{source}-turn",
            metadata={
                "tenant_id": "tenant-a",
                "workspace_path": str(workspace),
                "source": source,
                **metadata,
            },
        )

    cases: dict[str, dict[str, Any]] = {
        "direct": {
            "workspace_path": str(workspace),
            "workflow_preset": "audit.review",
            "sandbox_policy": {"type": "workspaceWrite"},
        },
        "group": {
            "workspace_path": str(workspace),
            "workflow_preset": "develop.iterate",
            "sandbox_policy": {"type": "workspaceWrite"},
            "caller_session": _parent(
                "realtime_group",
                {"workflow_preset": "audit.deep"},
            ),
        },
        "subagent": {
            "workspace_path": str(workspace),
            "workflow_preset": "audit.review",
            "sandbox_policy": {"type": "workspaceWrite"},
            "caller_session": _parent("call_subagent", {}),
        },
        "projectos": {
            "workspace_path": str(workspace),
            "workflow_preset": "develop.iterate",
            "sandbox_policy": {"type": "workspaceWrite"},
            "caller_session": _parent(
                "projectos_team_task",
                {"workflow_preset": "audit.deep"},
            ),
        },
    }
    requests = {}
    for surface, context in cases.items():
        request, _broker, _provider = role_runner.build_codex_role_request(
            stack,
            coder,
            f"{surface} audit",
            context=context,
        )
        assert request.sandbox_mode == "read-only", surface
        requests[surface] = request

    ordinary, _broker, _provider = role_runner.build_codex_role_request(
        stack,
        coder,
        "ordinary development",
        context={
            "workspace_path": str(workspace),
            "sandbox_policy": {"type": "workspaceWrite"},
        },
    )
    assert ordinary.sandbox_mode == "workspace-write"
    assert (
        role_runner.resolve_codex_sandbox_mode(
            {"sandbox_policy": {"type": "workspaceWrite"}},
            trusted_parent_metadata={"sandbox_policy": {"type": "readOnly"}},
        )
        == "read-only"
    )
    assert (
        role_runner.resolve_codex_sandbox_mode(
            {
                "workflow_preset": "develop.iterate",
                "metadata": {"workflow_preset": "audit.deep"},
            }
        )
        == "read-only"
    )

    direct = requests["direct"]
    sidecar = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=tmp_path / "sidecar-state",
            allowed_workspace_roots=(workspace,),
        )
    ).prepare(
        realm_id="realm",
        tenant_id=direct.tenant_id,
        thread_id=direct.outer_thread_id,
        task_id=direct.outer_turn_id,
        workspace=workspace,
        sandbox_mode=direct.sandbox_mode,
    )
    config = tomllib.loads(sidecar.config_path.read_text(encoding="utf-8"))
    profile = config["permissions"][PERMISSION_PROFILE]
    assert profile["filesystem"][str(workspace.resolve())] == "read"
    assert profile["filesystem"][str(sidecar.scratch_root)] == "write"


def test_group_can_supply_interactive_approval_but_project_defaults_to_auto_deny() -> None:
    from runtime.execution.codex_backend.role_runner import _approval_provider

    project_parent = Session(
        actor="person-7",
        thread_id="project-thread",
        metadata={"source": "projectos_task"},
    )
    assert isinstance(_approval_provider({}, project_parent), AutoDenyProvider)

    group_provider = AutoApproveProvider()
    group_parent = Session(
        actor="person-7",
        thread_id="group-thread",
        metadata={"source": "realtime_group"},
    )
    assert (
        _approval_provider(
            {"_codex_approval_provider": group_provider},
            group_parent,
        )
        is group_provider
    )


@pytest.mark.asyncio
async def test_builtin_coder_unavailable_fails_closed_without_legacy_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway import realtime_codex_backend as mod
    from tests.test_drive_codex_app_server import (
        _agent,
        _FakeEmitter,
        _FakeRuntime,
        _install_fake_session,
        _prepare_driver,
    )

    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    request = mod._request_for_turn(object(), turn, intent, _agent(), text="do it")
    monkeypatch.setattr(mod, "_request_for_turn", lambda *_a, **_kw: request)
    _instances, operations = _install_fake_session(
        monkeypatch,
        start_error=CodexBackendUnavailable("unsupported API"),
    )
    coder = SimpleNamespace(
        agent_id="coder",
        display_name="Kane",
        capabilities={"execution_backend": "codex_app_server"},
    )

    with pytest.raises(CodexBackendUnavailable, match="unsupported API"):
        await mod.drive_codex_app_server(
            _FakeRuntime(),
            turn,
            object(),
            _FakeEmitter(),
            intent,
            coder,
            object(),
            text="do it",
        )

    assert operations == ["start", "close"]


def test_group_roster_coder_fans_out_and_cannot_hijack_parent_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.memory.cowork.group_store import GroupStore
    from runtime.memory.cowork.service import invite_member, set_mode
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from tests.test_realtime_cerebrum import _drive

    store = GroupStore(base_dir=tmp_path / "cowork")
    invite_member(store, "th-coder-group", actor="u", target_id="coder", kind="agent")
    invite_member(store, "th-coder-group", actor="u", target_id="ui-agent", kind="agent")
    set_mode(store, "th-coder-group", actor="u", mode="swarm")
    member_calls: list[dict[str, Any]] = []

    def fake_member_call(
        *,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        member_calls.append(
            {"agent_id": agent_id, "prompt": prompt, "context": dict(context or {})}
        )
        return {"success": True, "output": f"{agent_id} replied", "error": None}

    async def forbidden_parent_codex(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("a roster Coder must not take over the parent group turn")

    monkeypatch.setattr(
        "runtime.execution.suckers.delegation_skills._call_agent",
        fake_member_call,
    )
    monkeypatch.setattr(CerebrumRuntime, "_drive_codex_app_server", forbidden_parent_codex)
    coder = SimpleNamespace(
        agent_id="coder",
        display_name="Kane",
        capabilities={"execution_backend": "codex_app_server"},
    )
    runtime = CerebrumRuntime(
        stack=object(),
        agent=coder,
        logs_root=str(tmp_path / "threads"),
        cowork_group_store=store,
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-coder-group",
                "input": [
                    {
                        "type": "text",
                        "text": "everyone review this",
                        "metadata": {"context": {"permission_mode": "bypassPermissions"}},
                    }
                ],
                "approvalPolicy": "never",
            },
        )

    assert out["response"].result["turn"]["status"] == "completed"
    assert {call["agent_id"] for call in member_calls} == {"coder", "ui-agent"}
    coder_context = next(call["context"] for call in member_calls if call["agent_id"] == "coder")
    assert isinstance(coder_context.get("_codex_approval_provider"), ApprovalProvider)
    assert "_server_auto_approve" not in coder_context


