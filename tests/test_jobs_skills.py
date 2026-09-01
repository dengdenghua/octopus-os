"""Model-facing jobs skills tests (dsh ``tool-jobs`` port).

Covers registration, the three generic controls, the background subagent
start skill, and the completion-notice lane into the durable report store
(parent wakeup / busy inject reuse the subagent report machinery).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from runtime.execution.jobs.registry import LocalJobRegistry
from runtime.execution.jobs.types import JobHooks, JobOutcome, JobStart
from runtime.execution.subagents.sessions import (
    SubagentSessionStore,
    set_subagent_session_store,
)
from runtime.execution.suckers.jobs_skills import (
    get_jobs_registry,
    register_jobs_skills,
    set_jobs_registry,
)
from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.process.session import Session, session_scope


@pytest.fixture(autouse=True)
def _jobs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate the registry singleton, the store singleton, and the
    subagent bridge from the real process state."""
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    set_jobs_registry(registry)
    store = SubagentSessionStore(base_dir=tmp_path / "sessions")
    set_subagent_session_store(store)
    monkeypatch.setattr(
        "runtime.execution.jobs.subagent_producer.call_subagent",
        _fake_call_subagent,
    )
    yield registry
    set_jobs_registry(None)
    set_subagent_session_store(None)


def _fake_call_subagent(**kwargs: Any) -> dict[str, Any]:
    """Canned bridge result: success unless the prompt asks for failure."""
    prompt = str(kwargs.get("prompt") or "")
    if "FAIL" in prompt:
        return {
            "agent_id": kwargs.get("agent_id", ""),
            "output": "",
            "success": False,
            "error": "boom",
            "error_type": "runtime_error",
        }
    return {
        "agent_id": kwargs.get("agent_id", ""),
        "output": "SUBAGENT OUT",
        "success": True,
    }


def _session(thread_id: str = "thr-1") -> Session:
    return Session(thread_id=thread_id, metadata={})


def _registered() -> SkillRegistry:
    skills = SkillRegistry()
    register_jobs_skills(skills)
    return skills


@pytest.mark.asyncio
async def test_register_jobs_skills_registers_four_and_attaches_controller() -> None:
    skills = _registered()
    names = set(skills.all_names())
    assert {"call_agent_background", "job_list", "job_output", "job_kill"} <= names
    # The controller attached at registration lets producers start work.
    producer = _StubProducer()
    get_jobs_registry().start(producer.start())


class _StubProducer:
    def __init__(self, outcome: JobOutcome | None = None) -> None:
        self.outcome = outcome or JobOutcome(status="completed", output="STUB")
        self.done: asyncio.Future[JobOutcome] | None = None

    def start(self) -> JobStart:
        def run() -> JobHooks:
            self.done = asyncio.get_running_loop().create_future()
            return JobHooks(cancel=lambda reason: None, done=self.done)

        return JobStart(kind="subagent", label="stub", run=run)


async def _wait_terminal(registry: LocalJobRegistry, job_id: str) -> None:
    for _ in range(200):
        if registry.get(job_id).status in ("completed", "killed", "failed"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not settle")


@pytest.mark.asyncio
async def test_job_list_returns_public_snapshots() -> None:
    registry = get_jobs_registry()
    producer = _StubProducer()
    with session_scope(_session("thr-1")):
        registry.start(producer.start())
        jobs = get_jobs_registry().list("thr-1")
        assert [job.id for job in jobs] == ["subagent-1"]
        public = jobs[0].to_public()
        assert public["status"] == "running"
        assert public["id"] == "subagent-1"
        # Ownership/bookkeeping fields are omitted (dsh PublicJobSnapshot).
        assert "reported" not in public
        assert "ownerSession" not in public


@pytest.mark.asyncio
async def test_job_output_waits_then_reads_terminal_output() -> None:
    registry = get_jobs_registry()
    producer = _StubProducer()
    with session_scope(_session("thr-1")):
        registry.start(producer.start())
        handler = _registered().get("job_output").handler
        # Live final-output job reads empty without waiting.
        result = await handler(job_id="subagent-1")
        assert result["text"] == ""
        assert result["job"]["status"] == "running"
        waiter = asyncio.create_task(handler(job_id="subagent-1", wait=True))
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(producer.done.set_result, producer.outcome)
        await _wait_terminal(registry, "subagent-1")
        result = await waiter
        assert result["text"] == "STUB"
        assert result["job"]["status"] == "completed"


@pytest.mark.asyncio
async def test_job_output_wait_timeout_returns_live_state() -> None:
    registry = get_jobs_registry()
    producer = _StubProducer()
    with session_scope(_session("thr-1")):
        registry.start(producer.start())
        handler = _registered().get("job_output").handler
        result = await handler(job_id="subagent-1", wait=True, timeout_ms=30)
        assert result["job"]["status"] == "running"
        assert registry.get("subagent-1").reported is False


@pytest.mark.asyncio
async def test_job_kill_requests_cancellation() -> None:
    registry = get_jobs_registry()
    producer = _StubProducer()
    with session_scope(_session("thr-1")):
        registry.start(producer.start())
        handler = _registered().get("job_kill").handler
        result = handler(job_id="subagent-1", reason="stop")
        assert result["outcome"] == "cancellation-requested"
        assert result["job"]["status"] == "stopping"
        result = handler(job_id="subagent-1")
        assert result["outcome"] == "cancellation-requested"
        # Already-finished after settlement returns the sentinel.
        asyncio.get_running_loop().call_soon_threadsafe(producer.done.set_result, producer.outcome)
        await _wait_terminal(registry, "subagent-1")
        result = handler(job_id="subagent-1")
        assert result["outcome"] == "already-finished"
        assert result["job"]["status"] == "completed"
        assert registry.get("subagent-1").reported is True


def _pump(loop: asyncio.AbstractEventLoop) -> None:
    loop.run_until_complete(asyncio.sleep(0.02))


def test_call_agent_background_validates_inputs() -> None:
    handler = _registered().get("call_agent_background").handler
    with session_scope(_session("thr-1")):
        result = handler(agent_id="", prompt="hi")
        assert result["success"] is False
        assert "agent_id is required" in result["error"]
        result = handler(agent_id="researcher", prompt="")
        assert result["success"] is False
        assert "prompt is required" in result["error"]


@pytest.mark.asyncio
async def test_call_agent_background_full_chain_with_notice(tmp_path: Path) -> None:
    """Start → settle → parent report lane carries the completion notice."""
    skills = _registered()
    handler = skills.get("call_agent_background").handler
    with session_scope(_session("thr-1")):
        result = handler(agent_id="researcher", prompt="research X")
        assert "job_id" in result
        assert "job" in result
        job_id = result["job_id"]
        assert job_id == "subagent-1"
        # The child runs on a worker thread; wait for the terminal state.
        registry = get_jobs_registry()
        for _ in range(400):
            snapshot = registry.get(job_id, "thr-1")
            if snapshot.status in ("completed", "killed", "failed"):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("background subagent did not settle")
        assert snapshot.status == "completed"
        read = registry.read(job_id, "thr-1")
        assert read.text == "SUBAGENT OUT"
        # The completion notice rode the durable report lane to the parent
        # thread (busy owner would have been injected instead).
        store = get_store()
        reports = store.pending_thread_reports("thr-1")
        assert len(reports) == 1
        content = reports[0][2].content
        assert "subagent-1" in content
        assert "用 job_output 读取结果" in content


def get_store() -> SubagentSessionStore:
    from runtime.execution.subagents.sessions import get_subagent_session_store

    store = get_subagent_session_store()
    assert store is not None
    return store


@pytest.mark.asyncio
async def test_call_agent_background_failure_settles_failed() -> None:
    skills = _registered()
    handler = skills.get("call_agent_background").handler
    with session_scope(_session("thr-1")):
        result = handler(agent_id="researcher", prompt="FAIL now")
        job_id = result["job_id"]
        registry = get_jobs_registry()
        for _ in range(400):
            snapshot = registry.get(job_id, "thr-1")
            if snapshot.status in ("completed", "killed", "failed"):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("background subagent did not settle")
        assert snapshot.status == "failed"
        assert snapshot.detail == "runtime_error"


def test_parent_job_key_uses_thread() -> None:
    with session_scope(_session("thr-9")):
        from runtime.execution.jobs import parent_job_key

        assert parent_job_key() == "thr-9"

