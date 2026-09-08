"""A server-registered live read may refresh without weakening durable writes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from runtime.execution.codex_backend.dynamic_tools import CodexDynamicToolBroker
from runtime.execution.codex_backend.types import ApprovalRequest
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import JSONLJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.safety.approval.approval_gate import AutoDenyProvider
from runtime.safety.auth import TrustEngine


def _run(executor, task_id, arguments=None):
    return executor.execute_step(
        0,
        "test-node",
        SkillId("test_tool"),
        arguments or {},
        caller="react_loop",
        task_id=task_id,
        arm_id=ArmId("test-arm"),
        budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=10_000, usd=1.0)),
    )


def _executor(tmp_path, skill):
    registry = SkillRegistry()
    registry.register(skill)
    return ToolExecutor(
        registry,
        TrustEngine(trusted_sources=["builtin://test/*"]),
        JSONLJournal(tmp_path / "events.jsonl"),
        effect_store_path=tmp_path / "effects.sqlite3",
    )


def test_new_refresh_policy_ignores_previously_committed_read_receipt(tmp_path):
    calls = []

    def handler():
        calls.append(True)
        return {"version": len(calls)}

    skill = Skill(
        name="test_tool", affinity=["read"], trusted_source="builtin://test/read", handler=handler
    )
    task_id = TaskId(uuid4())
    first = _run(_executor(tmp_path, skill), task_id)
    upgraded = skill.model_copy(update={"replay_policy": "refresh_read"})
    second = _run(_executor(tmp_path, upgraded), task_id)
    assert first.result.output == {"version": 1}
    assert second.result.output == {"version": 2}
    assert "durable_effect_replay" not in second.result.stderr_tags


def test_write_keeps_its_committed_receipt_even_with_read_refresh_metadata(tmp_path):
    calls = []

    def handler():
        calls.append(True)
        return {"writes": len(calls)}

    skill = Skill(
        name="test_tool",
        affinity=["write"],
        trusted_source="builtin://test/write",
        handler=handler,
        replay_policy="refresh_read",
    )
    task_id = TaskId(uuid4())
    first = _run(_executor(tmp_path, skill), task_id)
    second = _run(_executor(tmp_path, skill), task_id)
    assert first.result.output == second.result.output == {"writes": 1}
    assert len(calls) == 1
    assert "durable_effect_replay" in second.result.stderr_tags


def test_model_arguments_cannot_override_server_replay_policy(tmp_path):
    calls = []

    def handler(**_model_arguments):
        calls.append(True)
        return {"writes": len(calls)}

    skill = Skill(
        name="test_tool", affinity=["write"], trusted_source="builtin://test/write", handler=handler
    )
    task_id = TaskId(uuid4())
    arguments = {"replay_policy": "refresh_read"}
    first = _run(_executor(tmp_path, skill), task_id, arguments)
    second = _run(_executor(tmp_path, skill), task_id, arguments)
    assert skill.replay_policy == "durable"
    assert first.result.output == second.result.output == {"writes": 1}
    assert len(calls) == 1
    assert "durable_effect_replay" in second.result.stderr_tags


def _broker(executor, tmp_path):
    broker = CodexDynamicToolBroker(
        SimpleNamespace(executor=executor),
        SimpleNamespace(
            agent_id="test-agent",
            arms=[SimpleNamespace(arm_id="test-arm", allowed_skills=["test_tool"])],
            extra_skills=[],
        ),
        context={},
        goal="Inspect the test state",
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=str(tmp_path),
        tenant_id="test-tenant",
        principal_id="test-actor",
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
    )
    broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")
    return broker


def _request(broker, arguments=None):
    return ApprovalRequest(
        request_id=1,
        method="item/tool/call",
        params={
            "threadId": "inner-thread",
            "turnId": "inner-turn",
            "callId": "same-call",
            "tool": broker.catalog.names[0],
            "arguments": arguments or {},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("affinity,expected_calls", [(["read"], 2), (["write"], 1), ([], 1)])
async def test_codex_duplicate_refreshes_only_explicit_reads(tmp_path, affinity, expected_calls):
    calls = []

    def handler():
        calls.append(True)
        return {"version": len(calls)}

    executor = _executor(
        tmp_path,
        Skill(
            name="test_tool",
            affinity=affinity,
            replay_policy="refresh_read",
            trusted_source="builtin://test/read",
            handler=handler,
        ),
    )
    broker = _broker(executor, tmp_path)
    first = await broker(_request(broker))
    second = await broker(_request(broker))
    assert first["success"] is second["success"] is True
    assert json.loads(first["contentItems"][0]["text"]) == {"version": 1}
    assert json.loads(second["contentItems"][0]["text"]) == {"version": expected_calls}
    assert len(calls) == expected_calls


@pytest.mark.asyncio
async def test_codex_refresh_preserves_input_identity_and_live_skill_enablement(tmp_path):
    calls = []

    def handler(value: int):
        calls.append(value)
        return {"value": value}

    executor = _executor(
        tmp_path,
        Skill(
            name="test_tool",
            affinity=["read"],
            replay_policy="refresh_read",
            trusted_source="builtin://test/read",
            handler=handler,
        ),
    )
    broker = _broker(executor, tmp_path)
    assert (await broker(_request(broker, {"value": 1})))["success"] is True
    changed_input = await broker(_request(broker, {"value": 2}))
    assert changed_input["success"] is False
    assert "different input" in changed_input["contentItems"][0]["text"]
    executor.registry.disable("test_tool")
    disabled = await broker(_request(broker, {"value": 1}))
    assert disabled["success"] is False
    assert "disabled" in disabled["contentItems"][0]["text"]
    assert calls == [1]
