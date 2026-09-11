"""Focused coverage for the realtime OpenCode host entry."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from runtime.execution.engines import EngineId, ExecutionPhase, select_execution_route
from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import Turn, TurnParams
from runtime.sensing.gateway import realtime_execution, realtime_opencode_backend
from runtime.sensing.gateway.realtime_execution import TurnExecutionRequest, bind_turn_execution


def test_opencode_is_a_distinct_explicit_route() -> None:
    route = select_execution_route(requested_engine=EngineId.OPENCODE)
    assert route.engine is EngineId.OPENCODE
    assert route.driver_for(ExecutionPhase.PRIMARY) == "opencode_server"
    assert route.driver_for(ExecutionPhase.STEERING) == "opencode_server"


def test_opencode_readiness_checks_the_requested_model(monkeypatch) -> None:
    import asyncio

    from runtime.execution import opencode_backend

    seen = []

    def ready(scope, selection=None):
        seen.append(selection)
        return {"available": True, "reason": None}

    monkeypatch.setattr(opencode_backend, "inspect_readiness", ready)
    turn = Turn(
        threadId="thread",
        params=TurnParams(
            threadId="thread",
            executionEngine="opencode",
            model="big-pickle",
        ),
    )
    intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
    route = asyncio.run(
        realtime_execution.select_turn_execution(
            SimpleNamespace(_stack=SimpleNamespace(config=None)),
            turn,
            object(),
            intent,
            project_command=False,
            group_fanout=False,
            topology_id=None,
            codex_partner=False,
            reflection_fast_path=False,
        )
    )
    assert route.engine is EngineId.OPENCODE
    assert seen == ["big-pickle"]


def test_opencode_adapter_binds_the_authenticated_host_request(tmp_path, monkeypatch) -> None:
    turn = Turn(
        threadId="thread",
        params=TurnParams(
            threadId="thread",
            executionEngine="opencode",
            owner_actor_id="alice",
            tenant_id="tenant",
        ),
    )
    turn.execution_workspace_path = str(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.turn_started(turn.thread_id, turn)
    emitter = SimpleNamespace(notify=AsyncMock(), is_turn_interrupted=lambda _id: False)
    seen = []

    async def fake_drive(runtime, current_turn, _log, _emitter, _intent, _agent, _provider, *, text):
        from runtime.execution.request import current_execution_request
        from runtime.platform.process.session import current_session

        request = current_execution_request()
        session = current_session()
        assert request is not None
        assert request.task.execution_engine == "opencode"
        assert request.task.actor_id == "alice"
        assert request.task.tenant_id == "tenant"
        assert session is not None
        assert session.actor == "alice"
        assert current_turn is turn
        seen.append(text)

    monkeypatch.setattr(realtime_opencode_backend, "drive_opencode", fake_drive)
    runtime = SimpleNamespace(
        _stack=SimpleNamespace(config=SimpleNamespace(budget=None)),
        _workspaces=None,
    )
    intent = ParsedIntent(
        raw="use OpenCode",
        intent_type="task",
        normalized_goal="use OpenCode",
        user_context={"mode": "chat", "workspace_path": str(tmp_path)},
    )
    execution = bind_turn_execution(
        runtime,
        turn,
        log,
        emitter,
        object(),
        object(),
        select_execution_route(requested_engine=EngineId.OPENCODE),
    )

    import asyncio

    asyncio.run(execution.execute(TurnExecutionRequest(intent, "use OpenCode", None)))

    assert seen == ["use OpenCode"]
    assert turn.execution is not None
    assert turn.execution.engine == "opencode"
    assert turn.execution.driver == "opencode_server"
