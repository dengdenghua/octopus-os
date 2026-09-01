"""Tests for the declarative workflow DSL (P2 orchestration)."""

from __future__ import annotations

import time

import pytest

from runtime.execution.parallel_agents.orchestrator import ParallelAgentOrchestrator
from runtime.execution.parallel_agents.workflow_dsl import (
    WorkflowSpec,
    build_dispatch_inputs,
    dispatch_workflow,
    load_and_dispatch,
    parse_workflow_dict,
    parse_workflow_yaml,
)


def _valid_workflow() -> dict:
    return {
        "name": "wf",
        "max_concurrency": 2,
        "tasks": [
            {"id": "a", "agent": "researcher", "prompt": "do a"},
            {"id": "b", "agent": "researcher", "prompt": "do b"},
            {"id": "c", "agent": "synthesizer", "prompt": "do c", "depends_on": ["a", "b"]},
        ],
    }


# ── parse + validate ────────────────────────────────────────


def test_parse_dict_and_mapping():
    spec = parse_workflow_dict(_valid_workflow())
    assert isinstance(spec, WorkflowSpec)
    inputs = build_dispatch_inputs(spec)
    assert [t.task_id for t in inputs] == ["a", "b", "c"]
    assert inputs[0].subagent_name == "researcher"
    assert inputs[0].description == "do a"
    assert inputs[2].depends_on == ["a", "b"]


def test_duplicate_task_id_rejected():
    data = _valid_workflow()
    data["tasks"].append({"id": "a", "agent": "x", "prompt": "dup"})
    with pytest.raises(ValueError, match="duplicate task id"):
        parse_workflow_dict(data)


def test_dangling_dependency_rejected():
    data = _valid_workflow()
    data["tasks"][0]["depends_on"] = ["ghost"]
    with pytest.raises(ValueError, match="unknown task"):
        parse_workflow_dict(data)


def test_self_dependency_rejected():
    data = _valid_workflow()
    data["tasks"][0]["depends_on"] = ["a"]
    with pytest.raises(ValueError, match="depends on itself"):
        parse_workflow_dict(data)


def test_empty_tasks_rejected():
    with pytest.raises(ValueError, match="invalid workflow"):
        parse_workflow_dict({"name": "wf", "tasks": []})


def test_from_yaml_roundtrip(tmp_path):
    path = tmp_path / "wf.yaml"
    path.write_text(
        'name: wf\ntasks:\n  - id: a\n    agent: researcher\n    prompt: "do a"\n',
        encoding="utf-8",
    )
    spec = parse_workflow_yaml(path)
    assert spec.name == "wf"
    assert spec.tasks[0].id == "a"


# ── integration with the real orchestrator ──────────────────


@pytest.fixture
def orch():
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"done:{description}"

    o = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
    yield o
    o.shutdown(wait=False)


def _wait_done(orch, batch_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        batch = orch.get_batch(batch_id)
        if batch and batch.status in {"completed", "failed", "cancelled"}:
            return
        time.sleep(0.05)
    raise AssertionError("orchestrator did not finish in time")


def test_dispatch_workflow_runs_in_dependency_order(orch):
    spec = parse_workflow_dict(_valid_workflow())
    batch = dispatch_workflow(orch, spec)
    assert batch.plan is not None
    # Dependency order: phase 1 = {a, b}, phase 2 = {c}.
    assert [sorted(phase.task_ids) for phase in batch.plan.phases] == [
        ["a", "b"],
        ["c"],
    ]
    _wait_done(orch, batch.batch_id)
    finished = orch.get_batch(batch.batch_id)
    assert finished.status == "completed"
    assert {r.task_id for r in finished.results} == {"a", "b", "c"}


def test_load_and_dispatch_convenience(orch, tmp_path):
    path = tmp_path / "wf.yaml"
    path.write_text(
        'name: wf\ntasks:\n  - id: only\n    agent: researcher\n    prompt: "solo"\n',
        encoding="utf-8",
    )
    batch = load_and_dispatch(path, orch)
    _wait_done(orch, batch.batch_id)
    assert orch.get_batch(batch.batch_id).status == "completed"


def test_dispatch_override_wins_over_document(monkeypatch):
    spec = parse_workflow_dict(_valid_workflow())
    captured = {}

    def spy_dispatch(self, tasks, **kwargs):
        captured["kwargs"] = kwargs
        captured["tasks"] = tasks
        return object()

    monkeypatch.setattr(ParallelAgentOrchestrator, "dispatch", spy_dispatch)
    dispatch_workflow(ParallelAgentOrchestrator(), spec, max_concurrency=1)
    assert captured["kwargs"]["max_concurrency"] == 1
    # Document defaults flow through when no override is given.
    dispatch_workflow(ParallelAgentOrchestrator(), spec)
    assert captured["kwargs"]["max_concurrency"] == 2
    assert captured["kwargs"]["aggregation_strategy"] is None
    assert [t.task_id for t in captured["tasks"]] == ["a", "b", "c"]

