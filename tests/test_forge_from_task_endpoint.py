"""HTTP trigger for active single-demo forge:
``POST /api/evolution/skills/forge-from-task``.

A human picks one successful task run and mints a reusable skill now (no
min_hits wait). The immune gate still quarantines macros over dangerous
primitives — never auto-granting them.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers import (
    Skill,
    SkillRegistry,
    load_forged_skills_from_dir,
)
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.gateway.evolution_ops_router import create_evolution_ops_router


def _client(suckers: list[str], forged_skill_dir):
    journal = InMemoryJournal()
    registry = SkillRegistry()
    for name in set(suckers):
        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
    steps = []
    for i, s in enumerate(suckers):
        call = ToolCall(caller="test", sucker_id=s, args={})
        steps.append(
            Step(
                step_id=i,
                node_id=f"n{i}",
                action=call,
                result=ExecutionResult(call_id=call.call_id, status="success", output={"ok": True}),
            )
        )
    task_id = TaskId(uuid4())
    journal.write_trajectory(
        Trajectory(
            task_id=task_id,
            arm_id=ArmId("test"),
            strategy_id="react_loop",
            steps=steps,
            outcome=TrajectoryOutcome(success=True),
        )
    )
    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(
            journal=journal,
            registry=registry,
            forged_skill_dir=forged_skill_dir,
        )
    )
    return TestClient(app), registry, str(task_id)


def test_forge_from_task_single_demo_promotes_and_survives_restart(
    tmp_path,
    monkeypatch,
):
    """One demo (2 safe steps) → forged + registered, no 3x repetition."""
    data_dir = tmp_path / "data"
    forged_skill_dir = data_dir / "forged_skills"
    client, registry, task_id = _client(
        ["list_cwd", "count_words"],
        forged_skill_dir,
    )
    resp = client.post("/api/evolution/skills/forge-from-task", json={"task_id": task_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "promoted"
    assert len(data["promoted"]) == 1
    promoted_name = data["promoted"][0]
    assert registry.has(promoted_name)
    assert (forged_skill_dir / f"{promoted_name}.md").is_file()

    restarted_registry = SkillRegistry()
    for name in ("list_cwd", "count_words"):
        restarted_registry.register(registry.get(name), verify_tests=False)
    loaded = load_forged_skills_from_dir(forged_skill_dir, restarted_registry)
    assert promoted_name in loaded
    assert restarted_registry.has(promoted_name)

    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    from runtime.platform.ui.state import AppState

    restarted_state = AppState(journal=InMemoryJournal())
    assert restarted_state.registry.has(promoted_name)


def test_forge_from_task_quarantines_dangerous(tmp_path):
    """One demo over a dangerous primitive → quarantine, never auto-granted."""
    client, registry, task_id = _client(
        ["list_cwd", "exec_shell"],
        tmp_path / "forged_skills",
    )
    resp = client.post("/api/evolution/skills/forge-from-task", json={"task_id": task_id})
    data = resp.json()
    assert data["status"] == "quarantined"
    assert data["promoted"] == []
    assert len(data["quarantined"]) == 1


def test_forge_from_task_missing_id(tmp_path):
    client, _registry, _task_id = _client(
        ["list_cwd", "count_words"],
        tmp_path / "forged_skills",
    )
    resp = client.post("/api/evolution/skills/forge-from-task", json={})
    assert resp.json()["status"] == "missing_task_id"


def test_forge_from_task_unknown_id(tmp_path):
    client, _registry, _task_id = _client(
        ["list_cwd", "count_words"],
        tmp_path / "forged_skills",
    )
    resp = client.post("/api/evolution/skills/forge-from-task", json={"task_id": str(uuid4())})
    assert resp.json()["status"] == "no_successful_trajectory"


def test_forge_from_task_fails_closed_without_persistence_dependency():
    client, registry, task_id = _client(["list_cwd", "count_words"], None)

    response = client.post(
        "/api/evolution/skills/forge-from-task",
        json={"task_id": task_id},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "message": "skill forge dependencies unavailable",
        "missing": ["auto_persist_dir"],
    }
    assert registry.list_by_affinity("forged") == []

