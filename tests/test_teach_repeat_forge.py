"""REC button (teach-repeat) now forges a reusable skill from the conversation's
real journal trajectory via the active single-demo forge — instead of the old
empty-template stub. The immune gate still quarantines dangerous macros.
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
from runtime.sensing.gateway.recorder_store import RecorderStore
from runtime.sensing.gateway.teach_repeat_router import create_teach_repeat_router


def _client(
    thread_id: str,
    suckers: list[str],
    recording_root,
    *,
    wire_persistence: bool = True,
):
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
    # The trajectory react_loop would have written for this conversation.
    journal.write_trajectory(
        Trajectory(
            task_id=TaskId(uuid4()),
            thread_id=thread_id,
            arm_id=ArmId("react_arm"),
            strategy_id="react_loop",
            steps=steps,
            outcome=TrajectoryOutcome(success=True),
        )
    )
    app = FastAPI()
    app.include_router(
        create_teach_repeat_router(
            journal=journal,
            registry=registry,
            auto_persist_dir=(recording_root / "forged_skills" if wire_persistence else None),
            recording_store=RecorderStore(recording_root),
        )
    )
    return TestClient(app), registry


def test_rec_stop_forges_skill_from_thread_trajectory_and_survives_restart(tmp_path):
    client, registry = _client("t1", ["list_cwd", "count_words"], tmp_path)
    client.post("/api/teach-repeat/record/start", json={"thread_id": "t1", "name": "demo"})
    resp = client.post("/api/teach-repeat/record/stop", json={"thread_id": "t1", "use_llm": True})
    data = resp.json()
    assert data["status"] == "promoted"
    assert len(data["forged"]) == 1
    promoted_name = data["forged"][0]
    assert registry.has(promoted_name)

    forged_skill_dir = tmp_path / "forged_skills"
    assert (forged_skill_dir / f"{promoted_name}.md").is_file()
    restarted_registry = SkillRegistry()
    for name in ("list_cwd", "count_words"):
        restarted_registry.register(registry.get(name), verify_tests=False)
    loaded = load_forged_skills_from_dir(forged_skill_dir, restarted_registry)
    assert promoted_name in loaded
    assert restarted_registry.has(promoted_name)


def test_rec_stop_quarantines_dangerous_conversation(tmp_path):
    client, registry = _client("t2", ["list_cwd", "exec_shell"], tmp_path)
    client.post("/api/teach-repeat/record/start", json={"thread_id": "t2", "name": "demo"})
    resp = client.post("/api/teach-repeat/record/stop", json={"thread_id": "t2"})
    data = resp.json()
    assert data["status"] == "quarantined"
    assert data["forged"] == []


def test_rec_stop_no_trajectory_for_thread(tmp_path):
    client, _registry = _client("t3", ["list_cwd", "count_words"], tmp_path)
    client.post("/api/teach-repeat/record/start", json={"thread_id": "other", "name": "demo"})
    resp = client.post("/api/teach-repeat/record/stop", json={"thread_id": "other"})
    assert resp.json()["status"] == "no_successful_trajectory"


def test_rec_stop_fails_closed_and_keeps_recording_when_persistence_is_unwired(
    tmp_path,
):
    client, registry = _client(
        "unwired",
        ["list_cwd", "count_words"],
        tmp_path,
        wire_persistence=False,
    )
    client.post(
        "/api/teach-repeat/record/start",
        json={"thread_id": "unwired", "name": "demo"},
    )

    response = client.post(
        "/api/teach-repeat/record/stop",
        json={"thread_id": "unwired"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "message": "teach-repeat forge dependencies unavailable",
        "missing": ["auto_persist_dir"],
    }
    assert registry.list_by_affinity("forged") == []
    assert RecorderStore(tmp_path).status("unwired")["status"] == "recording"


def test_rec_captures_semantic_events_and_survives_store_reload(tmp_path):
    client, _registry = _client("t4", ["list_cwd", "count_words"], tmp_path)
    started = client.post(
        "/api/teach-repeat/record/start",
        json={"thread_id": "human", "name": "填写表单", "provider": "hybrid"},
    ).json()
    assert started["session_id"].startswith("rec_")

    appended = client.post(
        "/api/teach-repeat/record/events",
        json={
            "thread_id": "human",
            "events": [
                {
                    "ts": "2026-08-24T10:00:00Z",
                    "source": "human",
                    "kind": "input",
                    "target": {"aria_label": "密码"},
                    "data": {"password": "must-not-persist"},
                },
                {
                    "ts": "2026-08-24T10:00:01Z",
                    "source": "browser",
                    "kind": "input",
                    "target": {"aria_label": "验证码"},
                    "data": {
                        "sensitive": True,
                        "value": "provider-forgot-to-redact",
                    },
                },
            ],
        },
    ).json()
    assert appended["accepted"] == 2
    assert appended["step_count"] == 2

    restored = RecorderStore(tmp_path).status("human")
    assert restored is not None
    assert restored["event_count"] == 2
    with open(restored["events_path"], encoding="utf-8") as event_stream:
        persisted = event_stream.read()
        assert "must-not-persist" not in persisted
        assert "provider-forgot-to-redact" not in persisted

    stopped = client.post(
        "/api/teach-repeat/record/stop",
        json={"thread_id": "human"},
    ).json()
    assert stopped["status"] == "captured"
    assert stopped["template_id"]
    assert stopped["event_count"] == 2

