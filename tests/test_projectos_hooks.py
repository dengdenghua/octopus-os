"""Project OS LLM hooks: tolerant JSON parsers + deterministic QA (no LLM)."""

from __future__ import annotations

from runtime.projectos.llm_hooks import (
    _criterion_touched,
    _extract_json_array,
    parse_milestones,
    parse_tasks,
    spec_qa,
    subagent_execute_task,
)
from runtime.projectos.model import Milestone, Task


def test_extract_json_array_handles_fences_and_prose() -> None:
    assert _extract_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert _extract_json_array('here you go: [{"a":1}] done') == [{"a": 1}]
    assert _extract_json_array("no json here") == []
    assert _extract_json_array('{"not":"array"}') == []


def test_parse_milestones_assigns_ids_and_resolves_deps() -> None:
    text = """[
      {"name":"Scope","goal":"scope it","success_criteria":["approved"]},
      {"name":"Build","goal":"build it","dependencies":["Scope"]},
      {"name":"Verify","goal":"verify","dependencies":["Build","Ghost"]}
    ]"""
    ms = parse_milestones(text)
    assert [m.id for m in ms] == ["MS1", "MS2", "MS3"]
    assert ms[1].dependencies == ["MS1"]  # "Scope" → MS1
    assert ms[2].dependencies == ["MS2"]  # "Build" → MS2; unknown "Ghost" dropped
    assert ms[0].success_criteria == ["approved"]


def test_parse_milestones_empty_on_garbage() -> None:
    assert parse_milestones("the model refused") == []


def test_parse_tasks_resolves_dag_deps() -> None:
    text = """[
      {"type":"research","goal":"survey"},
      {"type":"code","goal":"implement","depends_on":["survey"]},
      {"type":"review","goal":"check","depends_on":["2"]}
    ]"""
    tasks = parse_tasks(text, "MS1")
    assert [t.id for t in tasks] == ["MS1-T1", "MS1-T2", "MS1-T3"]
    assert tasks[1].depends_on == ["MS1-T1"]  # by goal name
    assert tasks[2].depends_on == ["MS1-T2"]  # by index "2"
    assert tasks[0].assigned_role == "research"  # role routed from type


def test_criterion_touched() -> None:
    assert _criterion_touched("power under 5W", "the power draw is 4W") is True
    assert _criterion_touched("心率监测", "已实现心率监测模块") is True
    assert _criterion_touched("latency under 100ms", "we shipped a UI") is False


def test_spec_qa_deterministic_without_router() -> None:
    qa = spec_qa(router=None)
    ms = Milestone(id="MS1", name="m", goal="g", success_criteria=["power under 5W"])
    t_ok = Task(id="T", milestone_id="MS1", type="code", goal="g", output="power is 4W, good")
    t_bad = Task(id="T", milestone_id="MS1", type="code", goal="g", output="did something else")
    t_empty = Task(id="T", milestone_id="MS1", type="code", goal="g", output="")
    assert qa(t_ok, ms)["approved"] is True
    assert qa(t_bad, ms)["approved"] is False
    assert qa(t_empty, ms)["approved"] is False


def test_subagent_execute_task_propagates_project_scope(monkeypatch) -> None:
    captured: dict = {}

    def fake_call_subagent(agent_id, prompt, **kwargs):
        captured.update({"agent_id": agent_id, "prompt": prompt, **kwargs})
        return {"success": True, "output": "shipped"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call_subagent)
    task = Task(
        id="T1",
        milestone_id="MS1",
        type="code",
        goal="implement",
        assigned_agent="engineer",
    )

    output = subagent_execute_task(
        task,
        {
            "project_id": "P1",
            "owner_id": "alice",
            "tenant_id": "acme",
            "thread_id": "thread-1",
            "milestone_goal": "deliver",
            "workspace_path": "/managed/thread-1",
            "runtime_session_metadata": {
                "workspace_path": "/managed/thread-1",
                "_artifact_output_root": "/managed/thread-1/output/final",
            },
        },
    )

    assert output == "shipped"
    assert captured["agent_id"] == "engineer"
    assert captured["context"]["thread_id"] == "thread-1"
    assert captured["context"]["actor"] == "alice"
    assert captured["context"]["workspace_path"] == "/managed/thread-1"
    assert captured["context"]["runtime_session_metadata"] == {
        "source": "projectos_task",
        "project_id": "P1",
        "tenant_id": "acme",
        "workspace_path": "/managed/thread-1",
        "_artifact_output_root": "/managed/thread-1/output/final",
    }

