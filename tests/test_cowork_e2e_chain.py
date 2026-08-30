"""End-to-end cowork chain over the HTTP API — the regression guard Codex #6.

Drives the whole WeChat-style flow through /api/cowork/*: pull people in →
roster → switch mode → shared blackboard → async task assign/complete →
search → presence/read → nominate → member view → catch-up. Proves the
surfaces built across this subsystem hold together as one chain.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.group_store import GroupStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=GroupStore(base_dir=tmp_path)))
    return TestClient(app)


def test_full_cowork_chain(tmp_path) -> None:
    c = _client(tmp_path)
    t = "thread-e2e"

    # 1) Start a 1:1, then pull in a specialist mid-thread with a from_join grant.
    assert (
        c.post(f"/api/cowork/{t}/members", json={"target_id": "user", "kind": "human"}).status_code
        == 200
    )
    assert (
        c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"}).status_code
        == 200
    )
    r = c.post(
        f"/api/cowork/{t}/members",
        json={
            "target_id": "bob",
            "kind": "agent",
            "grant": {"scope": "from_join"},
            "at_message": 5,
        },
    )
    assert r.status_code == 200
    assert {m["id"] for m in r.json()["state"]["roster"]} == {"user", "alice", "bob"}

    # 2) Swarm → both agents respond.
    body = c.post(f"/api/cowork/{t}/mode", json={"mode": "swarm"}).json()
    assert body["state"]["mode"] == "swarm"
    assert set(c.get(f"/api/cowork/{t}").json()["responders"]) == {"alice", "bob"}

    # 3) Shared blackboard write, attributed + visible to the group.
    c.post(
        f"/api/cowork/{t}/blackboard",
        json={"key": "decision", "value": "enter the nutrition market"},
    )
    assert (
        c.get(f"/api/cowork/{t}").json()["blackboard"]["decision"] == "enter the nutrition market"
    )

    # 4) Async task: assign → list shows it → complete lands on the blackboard.
    task = c.post(
        f"/api/cowork/{t}/tasks", json={"assignee": "alice", "prompt": "scan nutrition competitors"}
    ).json()["task"]
    assert task["status"] == "pending"
    assert any(
        x["task_id"] == task["task_id"] for x in c.get(f"/api/cowork/{t}/tasks").json()["tasks"]
    )
    done = c.post(
        f"/api/cowork/{t}/tasks/{task['task_id']}/complete",
        json={"result": "found 3 rivals undercutting on price"},
    )
    assert done.status_code == 200
    board = done.json()["blackboard"]
    assert any("rivals" in str(v) for v in board.values())  # result landed on the board

    # 5) Replayable search spans the surfaces.
    hits = c.get(f"/api/cowork/{t}/search", params={"q": "nutrition"}).json()["hits"]
    kinds = {h["kind"] for h in hits}
    assert "blackboard" in kinds and "task" in kinds

    # 6) Presence + read receipts.
    pres = {m["member_id"]: m for m in c.get(f"/api/cowork/{t}/presence").json()["members"]}
    assert pres["user"]["unread"] > 0
    assert c.post(f"/api/cowork/{t}/read", json={"member_id": "user"}).status_code == 200
    after = {m["member_id"]: m for m in c.get(f"/api/cowork/{t}/presence").json()["members"]}
    assert after["user"]["unread"] == 0

    # 7) Nomination gate + per-member context view + catch-up brief.
    nominated = c.get(f"/api/cowork/{t}/nominate", params={"text": "nutrition"}).json()["nominated"]
    assert isinstance(nominated, list)
    view = c.get(f"/api/cowork/{t}/view/bob", params={"max_message": 12}).json()
    assert view["member_id"] == "bob" and view["scope"] == "from_join"
    catchup = c.get(f"/api/cowork/{t}/catchup/bob")
    assert catchup.status_code == 200 and "render" in catchup.json()


def test_chain_survives_member_removal(tmp_path) -> None:
    """Removing a member folds the roster but the shared board (their writes)
    survives — the durable-memory guarantee the chain relies on."""
    c = _client(tmp_path)
    t = "thread-rm"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})
    c.post(f"/api/cowork/{t}/blackboard", json={"key": "k", "value": "v"})
    c.request("DELETE", f"/api/cowork/{t}/members/alice")
    snap = c.get(f"/api/cowork/{t}").json()
    assert "alice" not in {m["id"] for m in snap["state"]["roster"]}
    assert snap["blackboard"]["k"] == "v"

