from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.agents import AgentRegistry
from runtime.execution.subagents import get_sub_agent_runner
from runtime.platform.ui._app_context import AppContext
from runtime.platform.ui._app_stack import wire_persistent_subagent_runner
from runtime.platform.ui.app import create_app
from runtime.sensing.model_router import MockModelRouter


def test_persistent_subagent_runner_is_wired_and_owned_until_shutdown(monkeypatch) -> None:
    app = FastAPI()
    stack = SimpleNamespace(planner=object(), runtime=object())
    registry = object()
    captured: dict[str, object] = {}

    def runner(description: str, *, subagent_name: str, **kwargs) -> str:
        return f"{subagent_name}:{description}"

    def fake_factory(*, stack, agent_registry):
        captured["stack"] = stack
        captured["agent_registry"] = agent_registry
        return runner

    monkeypatch.setattr(
        "runtime.execution.parallel_agents.stack_runner.make_stack_subagent_runner",
        fake_factory,
    )
    ctx = AppContext(app=app, stack=stack, agent_registry=registry)

    wire_persistent_subagent_runner(ctx)

    assert app.state.subagent_runner_ready is True
    assert app.state.subagent_runner is runner
    assert get_sub_agent_runner() is runner
    assert captured == {"stack": stack, "agent_registry": registry}

    with TestClient(app):
        assert get_sub_agent_runner() is runner

    assert get_sub_agent_runner() is None


def test_persistent_subagent_runner_stays_disabled_without_stack() -> None:
    app = FastAPI()

    wire_persistent_subagent_runner(AppContext(app=app, stack=None))

    assert app.state.subagent_runner_ready is False
    assert get_sub_agent_runner() is None


def test_create_app_project_run_reaches_persistent_runner(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []

    def task_runner(description: str, *, subagent_name: str, context=None, **kwargs) -> str:
        calls.append(
            {
                "description": description,
                "subagent_name": subagent_name,
                "context": context,
            }
        )
        return "implementation completed"

    def fake_factory(*, stack, agent_registry):
        assert stack is fake_stack
        assert agent_registry is registry
        return task_runner

    def model_response(request) -> str:
        prompt = request.messages[-1].content
        if "Break the goal" in prompt:
            return '[{"name":"Build","goal":"build","success_criteria":["done"]}]'
        if "Decompose this milestone" in prompt:
            return '[{"type":"code","goal":"implement","team_mode":"single"}]'
        if "Does the OUTPUT satisfy" in prompt:
            return '{"approved":true,"reason":"verified"}'
        raise AssertionError(f"unexpected model prompt: {prompt}")

    router = MockModelRouter(response_fn=model_response)
    fake_stack = SimpleNamespace(
        journal=None,
        executor=SimpleNamespace(journal=None, registry=None),
        runtime=SimpleNamespace(journal=None),
        planner=SimpleNamespace(router=router, planner_model=None),
        config=SimpleNamespace(mcp_servers=None),
        is_llm_planner=False,
    )
    registry = AgentRegistry()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "runtime.execution.parallel_agents.stack_runner.make_stack_subagent_runner",
        fake_factory,
    )
    monkeypatch.setattr("runtime.execution.agents.watcher.start_agent_watcher", lambda **_: None)

    app = create_app(
        stack=fake_stack,
        agent_registry=registry,
        tentacle_enabled=False,
    )
    assert app.state.subagent_runner_ready is True

    with TestClient(app) as client:
        planned = client.post(
            "/api/projects",
            json={"name": "runner wiring", "goal": "prove the production path"},
        )
        assert planned.status_code == 200
        project_id = planned.json()["project"]["id"]
        moved = client.post(
            "/api/projects/move",
            json={"thread_id": "thread-project", "project_id": project_id},
        )
        assert moved.status_code == 200

        run = client.post(f"/api/projects/{project_id}/run", json={"max_ticks": 10})

        assert run.status_code == 200
        assert run.json()["final_status"] == "done"
        assert len(calls) == 1
        assert calls[0]["subagent_name"] == "engineer"
        assert calls[0]["context"]["thread_id"] == "thread-project"
        assert calls[0]["context"]["runtime_session_metadata"]["project_id"] == project_id

    assert get_sub_agent_runner() is None


def test_two_live_apps_keep_projectos_bound_to_their_own_runner(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def make_runner(label: str):
        def _runner(description: str, *, subagent_name: str, context=None, **kwargs) -> str:
            calls.append((label, subagent_name, str((context or {}).get("thread_id") or "")))
            return f"{label} implementation completed"

        return _runner

    runners = {"app-a": make_runner("app-a"), "app-b": make_runner("app-b")}

    def fake_factory(*, stack, agent_registry):
        return runners[stack.app_label]

    def model_response(request) -> str:
        prompt = request.messages[-1].content
        if "Break the goal" in prompt:
            return '[{"name":"Build","goal":"build","success_criteria":["done"]}]'
        if "Decompose this milestone" in prompt:
            return '[{"type":"code","goal":"implement","team_mode":"single"}]'
        if "Does the OUTPUT satisfy" in prompt:
            return '{"approved":true,"reason":"verified"}'
        raise AssertionError(f"unexpected model prompt: {prompt}")

    def stack_for(label: str):
        return SimpleNamespace(
            app_label=label,
            journal=None,
            executor=SimpleNamespace(journal=None, registry=None),
            runtime=SimpleNamespace(journal=None),
            planner=SimpleNamespace(
                router=MockModelRouter(response_fn=model_response),
                planner_model=None,
            ),
            config=SimpleNamespace(mcp_servers=None),
            is_llm_planner=False,
        )

    def run_project(client: TestClient, label: str) -> None:
        planned = client.post(
            "/api/projects",
            json={"name": label, "goal": f"ship {label}"},
        )
        assert planned.status_code == 200, planned.json()
        project_id = planned.json()["project"]["id"]
        thread_id = f"thread-{label}"
        moved = client.post(
            "/api/projects/move",
            json={"thread_id": thread_id, "project_id": project_id},
        )
        assert moved.status_code == 200, moved.json()
        result = client.post(f"/api/projects/{project_id}/run", json={"max_ticks": 10})
        assert result.status_code == 200, result.json()
        assert result.json()["final_status"] == "done", result.json()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "runtime.execution.parallel_agents.stack_runner.make_stack_subagent_runner",
        fake_factory,
    )
    monkeypatch.setattr("runtime.execution.agents.watcher.start_agent_watcher", lambda **_: None)

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "app-a-data"))
    app_a = create_app(
        stack=stack_for("app-a"),
        agent_registry=AgentRegistry(),
        tentacle_enabled=False,
    )
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "app-b-data"))
    app_b = create_app(
        stack=stack_for("app-b"),
        agent_registry=AgentRegistry(),
        tentacle_enabled=False,
    )

    # App B owns the legacy global fallback, but App A must still dispatch via
    # the runner captured during its own factory wiring.
    assert get_sub_agent_runner() is runners["app-b"]
    with TestClient(app_a) as client_a:
        run_project(client_a, "app-a")

    # App A's shutdown must not clear the fallback currently owned by App B.
    assert get_sub_agent_runner() is runners["app-b"]
    with TestClient(app_b) as client_b:
        run_project(client_b, "app-b")

    assert calls == [
        ("app-a", "engineer", "thread-app-a"),
        ("app-b", "engineer", "thread-app-b"),
    ]
    assert get_sub_agent_runner() is None

