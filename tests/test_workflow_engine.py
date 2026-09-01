"""Workflow seam tests (dsh ``packages/workflow`` port).

Exercises the engine contract end to end through the real subprocess
worker: hook vocabulary, fatal-error discipline, caps, cancellation,
result materialization, lifecycle events, and the model-facing skill.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from runtime.execution.workflow import (
    WorkflowEngine,
    WorkflowError,
    WorkflowObserver,
)
from runtime.execution.workflow.types import (
    WorkflowAgentEndInfo,
    WorkflowRunInfo,
)

FAKE_META = {"name": "demo", "description": "demo run"}


async def _fake_dispatch(request: dict[str, Any]) -> dict[str, Any]:
    """Default child dispatcher: echo a canned success."""
    await asyncio.sleep(0.01)
    schema = request.get("schema")
    if schema is not None:
        return {
            "ok": True,
            "output": "plain",
            "structured": {"verdict": "yes"},
            "stop_reason": "completed",
            "child_id": "ch-schema",
        }
    return {
        "ok": True,
        "output": "OUT",
        "structured": None,
        "stop_reason": "completed",
        "child_id": "ch-1",
    }


def _engine(**kwargs: Any) -> WorkflowEngine:
    kwargs.setdefault("child_dispatch", _fake_dispatch)
    return WorkflowEngine(**kwargs)


def _run(engine: WorkflowEngine, **request: Any) -> Any:
    """Run one workflow to settlement inside a fresh loop."""

    async def _scenario() -> Any:
        run = engine.start(request)
        try:
            return await asyncio.wait_for(run.result, timeout=30)
        finally:
            await run.dispose()

    return asyncio.run(_scenario())


class _RecordingObserver(WorkflowObserver):
    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []

    def on_start(self, info: WorkflowRunInfo) -> None:
        self.events.append(("start", info.meta.name))

    def on_phase(self, info: WorkflowRunInfo, title: str) -> None:
        self.events.append(("phase", title))

    def on_log(self, info: WorkflowRunInfo, message: str) -> None:
        self.events.append(("log", message))

    def on_agent_start(self, info: WorkflowRunInfo, agent: Any) -> None:
        self.events.append(("agent-start", str(agent.seq), agent.label))

    def on_agent_end(self, info: WorkflowRunInfo, agent: WorkflowAgentEndInfo) -> None:
        self.events.append(("agent-end", str(agent.seq), agent.outcome))

    def on_end(self, info: WorkflowRunInfo, result: Any) -> None:
        self.events.append(("end", result.stop_reason, str(result.agents_started)))


# ── synchronous validation ──────────────────────────────────


def test_meta_validation_fails_loud() -> None:
    engine = _engine()
    for meta, expected in [
        ({"description": "x"}, "meta.name"),
        ({"name": "x"}, "meta.description"),
        ({"name": "x", "description": "y", "bogus": 1}, "unknown meta field"),
        ({"name": "x", "description": "y", "phases": [{"title": ""}]}, "phases[0].title"),
        ({"name": "x", "description": "y", "phases": [{"nope": 1}]}, "unknown field"),
        ("not-an-object", "must be an object"),
    ]:
        with pytest.raises(WorkflowError) as excinfo:
            engine.start({"script": "return 1", "meta": meta})
        assert excinfo.value.code == "META_INVALID"
        assert expected in excinfo.value.message


def test_script_parse_failure_is_synchronous() -> None:
    engine = _engine()
    for script, expected in [
        ("def broken(:\n    pass\n", "does not parse"),
        ("import os\nreturn 1", "imports are not supported"),
        ("return [].__class__.__name__", "dunder"),
        ("return __builtins__", "dunder"),
        ("meta = {'name': 'x'}\nreturn 1", "rides the `meta` request field"),
    ]:
        with pytest.raises(WorkflowError) as excinfo:
            engine.start({"script": script, "meta": FAKE_META})
        assert excinfo.value.code == "SCRIPT_PARSE"
        assert expected in excinfo.value.message


def test_non_string_script_rejected() -> None:
    with pytest.raises(WorkflowError) as excinfo:
        _engine().start({"script": 42, "meta": FAKE_META})
    assert excinfo.value.code == "SCRIPT_PARSE"


def test_request_cap_above_ceiling_rejected() -> None:
    engine = _engine(max_total_agents=2)
    with pytest.raises(WorkflowError) as excinfo:
        engine.start({"script": "return 1", "meta": FAKE_META, "maxTotalAgents": 3})
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert "exceeds the engine ceiling" in excinfo.value.message


# ── happy path ──────────────────────────────────────────────


def test_basic_run_vocabulary_and_events() -> None:
    observer = _RecordingObserver()
    engine = _engine(observer=observer, max_concurrent_agents=2)
    script = """
phase("research")
log("starting")
a = await agent("研究 A 主题", {"label": "A"})
results = await parallel([
    lambda: agent("并行1", {"label": "p1"}),
    lambda: agent("并行2", {"label": "p2"}),
])
pipe = await pipeline([1, 2, 3], lambda v, item, i: v * 2, lambda v, item, i: v + 1)
return {"a": a, "parallel": results, "pipeline": pipe, "arg0": args["x"]}
"""
    result = _run(
        engine,
        script=script,
        meta={
            "name": "demo",
            "description": "demo",
            "whenToUse": "when needed",
            "phases": [{"title": "research", "detail": "lanes"}],
        },
        args={"x": 42},
    )
    assert result.stop_reason == "completed"
    assert result.value == {
        "a": "OUT",
        "parallel": ["OUT", "OUT"],
        "pipeline": [3, 5, 7],
        "arg0": 42,
    }
    assert result.agents_started == 3
    methods = [e[0] for e in observer.events]
    assert methods[0] == "start"
    assert methods[-1] == "end"
    assert ("phase", "research") in observer.events
    assert ("log", "starting") in observer.events
    assert ("agent-start", "1", "A") in observer.events
    assert ("agent-end", "1", "completed") in observer.events
    assert ("agent-end", "3", "completed") in observer.events


def test_schema_agent_returns_structured_value() -> None:
    script = """
v = await agent("判断", {"schema": {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}})
return v
"""
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert result.value == {"verdict": "yes"}


def test_agent_option_provider_and_model_route_children() -> None:
    seen: list[dict[str, Any]] = []

    async def dispatch(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        return {"ok": True, "output": "OUT", "structured": None, "stop_reason": "completed"}

    script = 'return await agent("x", {"provider": "reviewer", "model": "gpt-5"})'
    result = _run(_engine(child_dispatch=dispatch), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert seen[0]["agent"] == "reviewer"
    assert seen[0]["model"] == "gpt-5"


# ── failure discipline ──────────────────────────────────────


def test_ordinary_child_failure_resolves_null() -> None:
    async def dispatch(request: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "child blew up", "stop_reason": "failed"}

    script = """
a = await agent("x", {"label": "failing"})
return a is None
"""
    result = _run(_engine(child_dispatch=dispatch), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert result.value is True


def test_fatal_option_error_kills_script() -> None:
    script = 'return await agent("x", {"effort": "high"})'
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "deferred" in (result.error or "")
    assert result.agents_started == 0


def test_fatal_propagates_through_parallel() -> None:
    script = """
try:
    await parallel([lambda: agent("x", {"effort": "high"})])
    return "no-error"
except Exception:
    return "propagated"
"""
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert result.value == "propagated"


def test_parallel_ordinary_error_nulled_other_items_run() -> None:
    script = """
out = await parallel([
    lambda: agent("ok1", {"label": "ok1"}),
    lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    lambda: agent("ok2", {"label": "ok2"}),
])
return out
"""
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert result.value == ["OUT", None, "OUT"]
    assert result.agents_started == 2


def test_pipeline_stage_error_nulls_only_that_item() -> None:
    script = """
def bad_stage(v, item, i):
    if item == 2:
        raise ValueError("nope")
    return v * 10
return await pipeline([1, 2, 3], bad_stage, lambda v, item, i: v + 1)
"""
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "completed"
    assert result.value == [11, None, 31]


def test_pipeline_requires_stages() -> None:
    result = _run(_engine(), script="return await pipeline([1])", meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "at least one stage" in (result.error or "")


# ── caps ────────────────────────────────────────────────────


def test_total_agent_cap() -> None:
    script = """
a = await agent("1")
b = await agent("2")
c = await agent("3")
return "unreachable"
"""
    result = _run(_engine(max_total_agents=2), script=script, meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "total agent cap (2)" in (result.error or "")
    assert result.agents_started == 2


def test_item_cap() -> None:
    script = "return await parallel([lambda: 1] * 10)"
    result = _run(_engine(max_items_per_call=3), script=script, meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "over the per-call cap (3)" in (result.error or "")


def test_concurrent_agents_bounded() -> None:
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def dispatch(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return {"ok": True, "output": "OUT", "structured": None, "stop_reason": "completed"}

    script = "return await parallel([lambda: agent('x')] * 6)"
    result = _run(
        _engine(child_dispatch=dispatch, max_concurrent_agents=2), script=script, meta=FAKE_META
    )
    assert result.stop_reason == "completed"
    assert peak <= 2


# ── result materialization ──────────────────────────────────


def test_unserializable_result_fails_loud() -> None:
    script = "return {1, 2, 3}"
    result = _run(_engine(), script=script, meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "not plain JSON data" in (result.error or "")


# ── cancellation / bounds ───────────────────────────────────


def test_cancel_mid_run() -> None:
    async def slow_dispatch(request: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.5)
        return {"ok": True, "output": "OUT", "structured": None, "stop_reason": "completed"}

    async def scenario() -> Any:
        engine = _engine(child_dispatch=slow_dispatch, dispose_grace_ms=1000)
        run = engine.start({"script": "a = await agent('long')\nreturn a", "meta": FAKE_META})
        await asyncio.sleep(0.2)
        run.cancel("user asked to stop")
        try:
            return await asyncio.wait_for(run.result, timeout=10)
        finally:
            await run.dispose()

    result = asyncio.run(scenario())
    assert result.stop_reason == "cancelled"
    assert "user asked to stop" in (result.error or "")
    assert result.agents_started == 1


def test_early_cancel() -> None:
    async def scenario() -> Any:
        engine = _engine()
        run = engine.start({"script": "return 1", "meta": FAKE_META})
        run.cancel("early")
        try:
            return await asyncio.wait_for(run.result, timeout=10)
        finally:
            await run.dispose()

    result = asyncio.run(scenario())
    assert result.stop_reason == "cancelled"


def test_sync_timeout_terminates_runaway_script() -> None:
    script = "while True:\n    pass\nreturn 1"
    result = _run(_engine(sync_timeout_ms=400), script=script, meta=FAKE_META)
    assert result.stop_reason == "error"
    assert "synchronous-slice timeout" in (result.error or "")


def test_observer_failure_is_contained() -> None:
    class _BoomObserver(_RecordingObserver):
        def on_phase(self, info: WorkflowRunInfo, title: str) -> None:
            raise RuntimeError("observer exploded")

    result = _run(
        _engine(observer=_BoomObserver()),
        script='log("x")\nreturn 1',
        meta=FAKE_META,
    )
    assert result.stop_reason == "completed"
    assert result.value == 1


def test_dispose_is_idempotent() -> None:
    async def scenario() -> None:
        engine = _engine()
        run = engine.start({"script": "return 1", "meta": FAKE_META})
        await run.result
        await run.dispose()
        await run.dispose()

    asyncio.run(scenario())


# ── model-facing skill ──────────────────────────────────────


def test_workflow_skill_registers_and_runs() -> None:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.workflow_skill import (
        register_workflow_skills,
        set_workflow_engine,
    )

    registry = SkillRegistry()
    assert register_workflow_skills(registry) == 1
    skill = registry.get("workflow")
    assert skill is not None
    assert skill.trusted_source == "skill://public/workflow"

    set_workflow_engine(_engine())
    try:

        async def scenario() -> dict[str, Any]:
            return await skill.handler(  # type: ignore[misc]
                script='return await agent("x", {"label": "A"})',
                meta={"name": "demo", "description": "demo"},
            )

        out = asyncio.run(scenario())
        assert out["success"] is True
        assert out["result"] == "OUT"
        assert out["agentsStarted"] == 1
        assert isinstance(out["runId"], str)
    finally:
        set_workflow_engine(None)


def test_workflow_skill_validation_error_is_plain_dict() -> None:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.workflow_skill import (
        register_workflow_skills,
    )

    registry = SkillRegistry()
    register_workflow_skills(registry)
    skill = registry.get("workflow")

    async def scenario() -> dict[str, Any]:
        return await skill.handler(  # type: ignore[misc]
            script="return 1",
            meta={"name": "x"},  # missing description
        )

    out = asyncio.run(scenario())
    assert out["success"] is False
    assert "meta.description" in (out["error"] or "")


def test_default_child_bridge_routes_through_call_subagent() -> None:
    """The default dispatcher reaches the echo subagent seam (ephemeral
    role runner), not just the injected fake."""
    from runtime.execution.suckers.ephemeral_agents import (
        EphemeralCall,
        set_ephemeral_role_runner,
    )

    def fake_ephemeral(call: EphemeralCall) -> str:
        return f"ANSWER:{call.user_prompt[:10]}"

    previous = set_ephemeral_role_runner(fake_ephemeral)
    try:
        script = 'return await agent("帮我查一下 X")'
        result = _run(_engine(child_dispatch=None), script=script, meta=FAKE_META)
        assert result.stop_reason == "completed"
        assert result.value == "ANSWER:帮我查一下 X"
        assert result.agents_started == 1
    finally:
        set_ephemeral_role_runner(previous)


# ── Audit T-10: run-level duration cap + process-group termination ──────────


def test_run_total_duration_cap() -> None:
    """A run past its total duration cap settles promptly (cancelled with the
    cap reason) instead of waiting for the slow child to finish."""

    async def slow_dispatch(request: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {"ok": True, "output": "OUT", "structured": None, "stop_reason": "completed"}

    async def scenario() -> Any:
        engine = _engine(child_dispatch=slow_dispatch, run_timeout_ms=300, dispose_grace_ms=1000)
        run = engine.start({"script": "a = await agent('long')\nreturn a", "meta": FAKE_META})
        try:
            return await asyncio.wait_for(run.result, timeout=5)
        finally:
            await run.dispose()

    result = asyncio.run(scenario())
    assert result.stop_reason == "cancelled"
    assert "total duration cap" in (result.error or "")
    assert result.agents_started == 1


def test_worker_runs_in_own_session() -> None:
    """The worker subprocess must be a session leader so tree termination
    can kill its children (audit T-10)."""
    import os

    async def slow_dispatch(request: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.5)
        return {"ok": True, "output": "OUT", "structured": None, "stop_reason": "completed"}

    async def scenario() -> Any:
        engine = _engine(child_dispatch=slow_dispatch, run_timeout_ms=0)
        # The worker stays alive awaiting the slow agent dispatch.
        run = engine.start({"script": "a = await agent('x')\nreturn a", "meta": FAKE_META})
        proc = run._proc  # noqa: SLF001 — test inspects the spawned worker
        session_of_worker = os.getsid(proc.pid) if proc is not None else os.getsid(0)
        try:
            result = await asyncio.wait_for(run.result, timeout=10)
            return result, session_of_worker
        finally:
            await run.dispose()

    result, session_of_worker = asyncio.run(scenario())
    assert result.stop_reason == "completed"
    assert session_of_worker != os.getsid(0)

