"""Cowork v2 HTTP endpoints: replay, nominate, catchup, tasks, breakout."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.group_store import GroupStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=GroupStore(base_dir=tmp_path)))
    return TestClient(app)


def test_replay_endpoint(tmp_path) -> None:
    c = _client(tmp_path)
    t = "rep"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "a", "kind": "agent"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "b", "kind": "agent"})
    c.request("DELETE", f"/api/cowork/{t}/members/a")
    now = c.get(f"/api/cowork/{t}").json()["state"]["roster"]
    assert {m["id"] for m in now} == {"b"}
    past = c.get(f"/api/cowork/{t}", params={"until_seq": 2}).json()["state"]["roster"]
    assert {m["id"] for m in past} == {"a", "b"}  # before the removal


def test_nominate_endpoint(tmp_path) -> None:
    c = _client(tmp_path)
    t = "nom"
    # The roster only knows agent ids, so the gate matches the message against id
    # tokens — agents are named for their domain ("database-expert", "css-guru").
    c.post(f"/api/cowork/{t}/members", json={"target_id": "database-expert", "kind": "agent"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "css-guru", "kind": "agent"})
    nominated = c.get(
        f"/api/cowork/{t}/nominate", params={"text": "optimize database index"}
    ).json()
    assert nominated["nominated"] == ["database-expert"]


def test_catchup_endpoint(tmp_path) -> None:
    c = _client(tmp_path)
    t = "cat"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "newbie", "kind": "agent"})
    c.post(f"/api/cowork/{t}/blackboard", json={"key": "decision", "value": "ship"})
    cu = c.get(f"/api/cowork/{t}/catchup/newbie").json()
    assert "newbie" in cu["roster"]
    assert cu["blackboard_keys"] == ["decision"]
    assert "render" in cu
    assert c.get(f"/api/cowork/{t}/catchup/ghost").status_code == 404


def test_async_task_flow_endpoint(tmp_path) -> None:
    c = _client(tmp_path)
    t = "task"
    r = c.post(f"/api/cowork/{t}/tasks", json={"assignee": "worker", "prompt": "do X"})
    assert r.status_code == 200
    task_id = r.json()["task"]["task_id"]
    assert c.get(f"/api/cowork/{t}/tasks").json()["tasks"][0]["status"] == "pending"
    done = c.post(f"/api/cowork/{t}/tasks/{task_id}/complete", json={"result": "done X"})
    assert done.status_code == 200
    assert any(v == "done X" for v in done.json()["blackboard"].values())


def test_breakout_endpoints(tmp_path) -> None:
    c = _client(tmp_path)
    t = "par"
    fork = c.post(
        f"/api/cowork/{t}/breakout",
        json={
            "child_thread": "child",
            "members": [{"id": "a", "kind": "agent"}],
            "grant": {"scope": "from_join"},
            "at_message": 3,
        },
    )
    assert fork.status_code == 200 and fork.json()["members"] == ["a"]
    assert {m["id"] for m in c.get("/api/cowork/child").json()["state"]["roster"]} == {"a"}
    merge = c.post(f"/api/cowork/{t}/breakout/child/merge", json={"summary": "use X"})
    assert merge.status_code == 200
    assert merge.json()["blackboard"]["breakout:child:summary"] == "use X"

