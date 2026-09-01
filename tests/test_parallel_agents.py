"""Implementation note."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.execution.misc.file_write_leases import acquire_file_write_lease
from runtime.execution.parallel_agents import (
    DispatchTaskInput,
    ParallelAgentOrchestrator,
)
from runtime.sensing.gateway.parallel_agents_router import (
    create_parallel_agents_router,
)

# ═══════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def echo_runner():
    """Implementation note."""

    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"ECHO[{subagent_name}]: {description}"

    return runner


@pytest.fixture
def slow_runner():
    """Implementation note."""

    def runner(description, *, subagent_name, context=None, cancel_event=None):
        for _ in range(50):
            if cancel_event is not None and cancel_event.is_set():
                return ""
            time.sleep(0.02)
        return f"slow-done: {description}"

    return runner


@pytest.fixture
def orch(echo_runner):
    o = ParallelAgentOrchestrator(max_concurrency=4, task_runner=echo_runner)
    yield o
    o.shutdown(wait=False)


@pytest.fixture
def app_client(orch):
    app = FastAPI()
    app.include_router(create_parallel_agents_router(orchestrator=orch))
    with TestClient(app) as client:
        yield client


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestDispatch:
    def test_dispatch_exposes_plan_and_work_contracts(self, orch):
        batch = orch.dispatch(
            [
                DispatchTaskInput(
                    task_id="design",
                    description="draft the design",
                    subagent_name="Pro_Designer",
                ),
                DispatchTaskInput(
                    task_id="build",
                    description="implement the design",
                    subagent_name="Scaffold_Dev",
                    depends_on=["design"],
                ),
            ]
        )

        assert batch.plan is not None
        assert batch.plan.strategy == "parallel_agents"
        assert [phase.task_ids for phase in batch.plan.phases] == [
            ["design"],
            ["build"],
        ]
        assert len(batch.plan.contracts) == 2

        first = batch.plan.contracts[0]
        assert first.contract_id == "design"
        assert first.agent_id == "Pro_Designer"
        assert first.owned_scope == ["task:design"]
        assert first.forbidden_scope == ["task:build"]
        assert first.success_criteria == [
            "Complete task design",
            "Return a result or explicit error",
        ]
        assert batch.results[0].work_contract == first

    def test_depends_on_tasks_wait_for_dependencies(self):
        lock = threading.Lock()
        starts: dict[str, float] = {}
        finishes: dict[str, float] = {}

        def runner(description, *, subagent_name, context=None, cancel_event=None):
            with lock:
                starts[subagent_name] = time.monotonic()
            if subagent_name == "alpha":
                time.sleep(0.12)
            with lock:
                finishes[subagent_name] = time.monotonic()
            return f"done:{description}"

        o = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
        try:
            batch = o.dispatch(
                [
                    DispatchTaskInput(
                        task_id="a",
                        description="first",
                        subagent_name="alpha",
                    ),
                    DispatchTaskInput(
                        task_id="b",
                        description="second",
                        subagent_name="beta",
                        depends_on=["a"],
                    ),
                ]
            )

            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                assert snap is not None
                if snap.status == "completed":
                    break
                time.sleep(0.02)

            assert starts["beta"] >= finishes["alpha"]
        finally:
            o.shutdown(wait=False)

    def test_serial_write_path_dependency_authorizes_file_handoff(self, tmp_path):
        touched: list[str] = []

        def runner(description, *, subagent_name, context=None, cancel_event=None):
            ctx = context or {}
            metadata = ctx["runtime_session_metadata"]
            owner = ctx["file_write_owner"]
            contract = ctx["work_contract"]
            session = SimpleNamespace(metadata=metadata)
            for rel_path in contract["write_paths"]:
                acquire_file_write_lease(
                    session,
                    tmp_path / rel_path,
                    owner=owner,
                )
                touched.append(f"{owner}:{rel_path}")
            return f"done:{description}"

        o = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
        try:
            batch = o.dispatch(
                [
                    DispatchTaskInput(
                        task_id="a",
                        description="first",
                        write_paths=["src/app.py"],
                    ),
                    DispatchTaskInput(
                        task_id="b",
                        description="second",
                        depends_on=["a"],
                        write_paths=["src/app.py"],
                    ),
                ],
                context={"workspace_path": str(tmp_path)},
            )
            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                assert snap is not None
                if snap.status == "completed":
                    break
                time.sleep(0.02)

            snap = o.get_batch(batch.batch_id)
            assert snap is not None
            assert snap.status == "completed"
            assert touched == ["a:src/app.py", "b:src/app.py"]
            obs = snap.file_write_observability
            assert obs["lease_count"] == 1
            assert obs["pending_handoff_count"] == 0
            assert obs["handoff_count"] == 2
            assert obs["conflict_count"] == 0
            assert obs["leases"][0]["owner"] == "b"
            assert [
                event["event"]
                for event in obs["history"]
                if event["path"] == obs["leases"][0]["path"]
            ] == [
                "acquire",
                "handoff_authorized",
                "handoff_consumed",
            ]
        finally:
            o.shutdown(wait=False)

    def test_dispatch_runs_to_completion(self, orch):
        batch = orch.dispatch(
            [
                DispatchTaskInput(description="hello", subagent_name="writer"),
                DispatchTaskInput(description="world", subagent_name="coder"),
            ]
        )
        assert batch.total_tasks == 2
        assert batch.status in ("running", "completed")
        # Implementation note.
        for _ in range(100):
            snap = orch.get_batch(batch.batch_id)
            assert snap is not None
            if snap.status == "completed":
                break
            time.sleep(0.02)
        snap = orch.get_batch(batch.batch_id)
        assert snap.status == "completed"
        assert snap.completed_tasks == 2
        assert snap.failed_tasks == 0
        results = {r.subagent_name: r.result for r in snap.results}
        assert results["writer"] == "ECHO[writer]: hello"
        assert results["coder"] == "ECHO[coder]: world"
        assert snap.aggregated_content is not None
        assert "hello" in snap.aggregated_content
        event_types = [event.type for event in snap.event_log]
        assert "stage_change" in event_types
        assert "task_update" in event_types
        assert event_types[-1] == "batch_complete"
        sequences = [event.sequence for event in snap.event_log]
        assert sequences == list(range(1, len(snap.event_log) + 1))
        assert all(event.created_at for event in snap.event_log)

    def test_runner_can_emit_tool_lane_events(self):
        def runner(description, *, subagent_name, context=None, cancel_event=None):
            emit = (context or {}).get("emit_tool_event")
            assert callable(emit)
            emit(
                tool_name="read_file",
                status="completed",
                input_preview="path=README.md",
                output_preview="Echo Agent",
                artifact_paths=["/tmp/report.md"],
            )
            return f"done:{description}"

        o = ParallelAgentOrchestrator(max_concurrency=1, task_runner=runner)
        try:
            batch = o.dispatch([DispatchTaskInput(description="inspect")])
            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                assert snap is not None
                if snap.status == "completed":
                    break
                time.sleep(0.02)

            snap = o.get_batch(batch.batch_id)
            assert snap is not None
            tool_events = [event for event in snap.event_log if event.type == "tool_call"]
            assert len(tool_events) == 1
            event = tool_events[0]
            assert event.lane == "computer"
            assert event.tool_name == "read_file"
            assert event.tool_input_preview == "path=README.md"
            assert event.tool_output_preview == "Echo Agent"
            assert event.artifact_paths == ["/tmp/report.md"]
            assert event.sequence is not None
            assert event.created_at
        finally:
            o.shutdown(wait=False)

    def test_dispatch_empty_rejected(self, orch):
        with pytest.raises(ValueError):
            orch.dispatch([])

    def test_dispatch_accepts_dict_input(self, orch):
        """Implementation note."""
        batch = orch.dispatch(
            [
                {"description": "dict-form", "subagent_name": "x"},
            ]
        )
        assert batch.total_tasks == 1

    def test_dispatch_merges_extra_context(self):
        seen: list[dict] = []

        def runner(description, *, subagent_name, context=None, cancel_event=None):
            seen.append(context or {})
            return "ok"

        o = ParallelAgentOrchestrator(max_concurrency=1, task_runner=runner)
        try:
            batch = o.dispatch(
                [DispatchTaskInput(description="ctx")],
                thread_id="thread-1",
                context={"lead_agent_name": "general", "research_job_id": "research-1"},
            )
            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                assert snap is not None
                if snap.status == "completed":
                    break
                time.sleep(0.02)

            assert seen
            assert seen[0]["thread_id"] == "thread-1"
            assert seen[0]["lead_agent_name"] == "general"
            assert seen[0]["research_job_id"] == "research-1"
        finally:
            o.shutdown(wait=False)


class TestCancel:
    def test_cancel_pending_marks_cancelled(self, slow_runner):
        # Implementation note.
        o = ParallelAgentOrchestrator(max_concurrency=1, task_runner=slow_runner)
        try:
            batch = o.dispatch(
                [
                    DispatchTaskInput(description="t1"),
                    DispatchTaskInput(description="t2"),
                ]
            )
            # Implementation note.
            t2_id = batch.results[1].task_id
            ok = o.cancel_task(t2_id)
            assert ok is True
            # Implementation note.
            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                if snap.status in ("completed", "partial", "cancelled"):
                    break
                time.sleep(0.05)
            snap = o.get_batch(batch.batch_id)
            # Implementation note.
            statuses = {r.task_id: r.status for r in snap.results}
            assert statuses[t2_id] == "cancelled"
        finally:
            o.shutdown(wait=False)

    def test_cancel_unknown_returns_false(self, orch):
        assert orch.cancel_task("nope") is False

    def test_cancel_all(self, slow_runner):
        o = ParallelAgentOrchestrator(max_concurrency=2, task_runner=slow_runner)
        try:
            o.dispatch([DispatchTaskInput(description=f"t{i}") for i in range(5)])
            o.cancel_all()
            # Implementation note.
            time.sleep(0.3)
            st = o.status()
            # Implementation note.
            assert st.pending_count == 0
        finally:
            o.shutdown(wait=False)


class TestStatus:
    def test_status_counts(self, orch):
        batch = orch.dispatch([DispatchTaskInput(description=f"t{i}") for i in range(3)])
        # Implementation note.
        for _ in range(100):
            if orch.get_batch(batch.batch_id).status == "completed":
                break
            time.sleep(0.02)
        st = orch.status()
        assert st.completed_count == 3
        assert st.max_concurrency == 4
        assert batch.batch_id in st.batches


class TestSplit:
    def test_split_stub_returns_single_task(self, orch):
        r = orch.split("do big thing", max_subtasks=5)
        assert len(r.tasks) == 1
        assert r.tasks[0].description == "do big thing"
        assert r.total_levels == 1
        assert r.is_parallelizable is False

    def test_custom_splitter(self):
        def my_split(task, *, max_subtasks, context, model_name):
            from runtime.execution.parallel_agents.models import (
                SplitResult,
                SplitTask,
            )

            return SplitResult(
                tasks=[
                    SplitTask(task_id="a", description="sub-a"),
                    SplitTask(task_id="b", description="sub-b", depends_on=["a"]),
                ],
                dag_levels=[["a"], ["b"]],
                total_levels=2,
                is_parallelizable=True,
            )

        o = ParallelAgentOrchestrator(splitter=my_split)
        try:
            r = o.split("parent")
            assert r.total_levels == 2
            assert r.is_parallelizable is True
            assert len(r.tasks) == 2
        finally:
            o.shutdown(wait=False)


class TestRunnerError:
    def test_runner_exception_marks_failed(self):
        def boom(description, *, subagent_name, context=None, cancel_event=None):
            raise RuntimeError("boom!")

        o = ParallelAgentOrchestrator(max_concurrency=2, task_runner=boom)
        try:
            batch = o.dispatch([DispatchTaskInput(description="x")])
            for _ in range(100):
                snap = o.get_batch(batch.batch_id)
                if snap.status in ("failed", "partial", "completed"):
                    break
                time.sleep(0.02)
            snap = o.get_batch(batch.batch_id)
            assert snap.failed_tasks == 1
            assert snap.results[0].status == "failed"
            assert "boom" in (snap.results[0].error or "")
        finally:
            o.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════
# FastAPI router
# ═══════════════════════════════════════════════════════════════


class TestRouter:
    def test_status_endpoint(self, app_client):
        r = app_client.get("/api/agents/parallel/status")
        assert r.status_code == 200
        data = r.json()
        assert data["max_concurrency"] == 4
        assert isinstance(data["batches"], dict)

    def test_dispatch_endpoint_full_cycle(self, app_client, orch):
        # POST dispatch
        r = app_client.post(
            "/api/agents/parallel/dispatch",
            json={
                "tasks": [
                    {"description": "first", "subagent_name": "reader"},
                    {"description": "second", "subagent_name": "writer"},
                ],
                "aggregation_strategy": "concat",
            },
        )
        assert r.status_code == 200
        batch = r.json()
        assert batch["total_tasks"] == 2
        bid = batch["batch_id"]
        # Implementation note.
        for _ in range(100):
            g = app_client.get(f"/api/agents/parallel/batch/{bid}").json()
            if g["status"] == "completed":
                break
            time.sleep(0.02)
        g = app_client.get(f"/api/agents/parallel/batch/{bid}").json()
        assert g["completed_tasks"] == 2
        assert g["aggregated_content"]
        assert g["event_log"]
        assert {event["type"] for event in g["event_log"]} >= {
            "stage_change",
            "task_update",
            "batch_complete",
        }
        sequences = [event["sequence"] for event in g["event_log"]]
        assert sequences == list(range(1, len(g["event_log"]) + 1))
        assert all(event["created_at"] for event in g["event_log"])

    def test_batch_endpoint_exposes_file_write_observability(self, tmp_path):
        def runner(description, *, subagent_name, context=None, cancel_event=None):
            ctx = context or {}
            metadata = ctx["runtime_session_metadata"]
            owner = ctx["file_write_owner"]
            contract = ctx["work_contract"]
            session = SimpleNamespace(metadata=metadata)
            for rel_path in contract["write_paths"]:
                acquire_file_write_lease(
                    session,
                    tmp_path / rel_path,
                    owner=owner,
                )
            return f"done:{description}"

        orchestrator = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
        app = FastAPI()
        app.include_router(create_parallel_agents_router(orchestrator=orchestrator))
        try:
            with TestClient(app) as client:
                batch = orchestrator.dispatch(
                    [
                        DispatchTaskInput(
                            task_id="a",
                            description="first",
                            write_paths=["src/app.py"],
                        ),
                        DispatchTaskInput(
                            task_id="b",
                            description="second",
                            depends_on=["a"],
                            write_paths=["src/app.py"],
                        ),
                    ],
                    context={"workspace_path": str(tmp_path)},
                )
                for _ in range(100):
                    data = client.get(
                        f"/api/agents/parallel/batch/{batch.batch_id}",
                    ).json()
                    if data["status"] == "completed":
                        break
                    time.sleep(0.02)

                data = client.get(
                    f"/api/agents/parallel/batch/{batch.batch_id}",
                ).json()
                obs = data["file_write_observability"]
                assert obs["lease_count"] == 1
                assert obs["pending_handoff_count"] == 0
                assert obs["handoff_count"] == 2
                assert obs["leases"][0]["owner"] == "b"
        finally:
            orchestrator.shutdown(wait=False)

    def test_get_unknown_batch_404(self, app_client):
        r = app_client.get("/api/agents/parallel/batch/nope")
        assert r.status_code == 404

    def test_dispatch_empty_400(self, app_client):
        r = app_client.post(
            "/api/agents/parallel/dispatch",
            json={"tasks": []},
        )
        assert r.status_code == 400

    def test_cancel_unknown_task_404(self, app_client):
        r = app_client.post("/api/agents/parallel/cancel/nope")
        assert r.status_code == 404

    def test_cancel_all_always_ok(self, app_client):
        r = app_client.post("/api/agents/parallel/cancel-all")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_split_endpoint(self, app_client):
        r = app_client.post(
            "/api/agents/parallel/split",
            json={"task": "big thing", "max_subtasks": 3},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_levels"] == 1
        assert len(data["tasks"]) == 1


# ═══════════════════════════════════════════════════════════════
# SSE stream
# ═══════════════════════════════════════════════════════════════


class TestSSE:
    def test_stream_emits_task_update_and_batch_complete(self, app_client):
        # Implementation note.
        r = app_client.post(
            "/api/agents/parallel/dispatch",
            json={"tasks": [{"description": "one", "subagent_name": "w"}]},
        )
        bid = r.json()["batch_id"]

        with app_client.stream(
            "GET",
            f"/api/agents/parallel/stream/{bid}",
        ) as resp:
            assert resp.status_code == 200
            events: list[dict] = []
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("id:"):
                    assert int(line.split(":", 1)[1].strip()) >= 1
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line.split(":", 1)[1].strip())
                    events.append({"event": current_event, "data": payload})
                    if current_event == "batch_complete":
                        break
            types = {e["event"] for e in events}
            assert "stage_change" in types
            assert "task_update" in types
            assert "batch_complete" in types
            assert all(e["data"]["sequence"] for e in events)
            assert all(e["data"]["created_at"] for e in events)
            stages = [e["data"].get("stage") for e in events if e["event"] == "stage_change"]
            assert "matching_agents" in stages
            assert "final_report" in stages
            stage_payloads = [
                e["data"].get("payload", {}) for e in events if e["event"] == "stage_change"
            ]
            assert all("total_tasks" in payload for payload in stage_payloads)
            assert stage_payloads[-1]["completed_tasks"] == 1
            task_payloads = [e["data"] for e in events if e["event"] == "task_update"]
            assert task_payloads
            assert task_payloads[0]["lane"] == "agent"
            assert task_payloads[0]["node_ids"]
            assert "contract_id" in task_payloads[0]["payload"]
            assert events[-1]["event"] == "batch_complete"
            assert events[-1]["data"]["batch_id"] == bid

    def test_stream_after_sequence_resumes_without_replaying_old_events(self, app_client):
        r = app_client.post(
            "/api/agents/parallel/dispatch",
            json={"tasks": [{"description": "one", "subagent_name": "w"}]},
        )
        bid = r.json()["batch_id"]
        for _ in range(100):
            g = app_client.get(f"/api/agents/parallel/batch/{bid}").json()
            if g["status"] == "completed":
                break
            time.sleep(0.02)
        g = app_client.get(f"/api/agents/parallel/batch/{bid}").json()
        last_seen = g["event_log"][2]["sequence"]

        with app_client.stream(
            "GET",
            f"/api/agents/parallel/stream/{bid}?after_sequence={last_seen}",
        ) as resp:
            assert resp.status_code == 200
            sequences: list[int] = []
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    payload = json.loads(line.split(":", 1)[1].strip())
                    sequences.append(payload["sequence"])
                    if payload["type"] == "batch_complete":
                        break

        assert sequences
        assert min(sequences) > last_seen
        assert sequences == [
            event["sequence"] for event in g["event_log"] if event["sequence"] > last_seen
        ]

    def test_stream_unknown_batch_404(self, app_client):
        r = app_client.get("/api/agents/parallel/stream/nope")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════════


class TestCreateAppDefaultOrchestrator:
    def test_default_orchestrator_registered(self, tmp_path):
        from runtime.platform.ui import create_app

        app = create_app(journal_path=tmp_path / "events.jsonl")
        client = TestClient(app)
        # Implementation note.
        r = client.get("/api/agents/parallel/status")
        assert r.status_code == 200
        data = r.json()
        assert "max_concurrency" in data
        # Implementation note.
        assert data["max_concurrency"] == 4

    def test_injected_orchestrator_dispatch_works(self, tmp_path, echo_runner):
        from runtime.platform.ui import create_app

        orchestrator = ParallelAgentOrchestrator(
            max_concurrency=4,
            task_runner=echo_runner,
            worker_isolation="thread",
        )
        try:
            app = create_app(
                journal_path=tmp_path / "events.jsonl",
                parallel_agent_orchestrator=orchestrator,
            )
            client = TestClient(app)
            r = client.post(
                "/api/agents/parallel/dispatch",
                json={"tasks": [{"description": "hi", "subagent_name": "x"}]},
            )
            assert r.status_code == 200
            bid = r.json()["batch_id"]
            for _ in range(100):
                g = client.get(f"/api/agents/parallel/batch/{bid}").json()
                if g["status"] == "completed":
                    break
                time.sleep(0.02)
            g = client.get(f"/api/agents/parallel/batch/{bid}").json()
            assert g["completed_tasks"] == 1
            assert g["results"][0]["result"] == "ECHO[x]: hi"
        finally:
            orchestrator.shutdown(wait=False)
