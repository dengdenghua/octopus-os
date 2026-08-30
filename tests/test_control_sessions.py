from __future__ import annotations

import hashlib
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.control_sessions import ControlSessionStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.control_sessions_router import create_control_sessions_router


def _client(tmp_path):
    app = FastAPI()
    store = ControlSessionStore(base_dir=tmp_path)
    app.include_router(create_control_sessions_router(store=store))
    return TestClient(app), store


def test_control_session_replay_records_actions_and_evidence(tmp_path) -> None:
    client, _store = _client(tmp_path)

    created = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-browser-1",
            "owner_id": "agent-1",
            "owner_label": "Agent 1",
            "surface": "browser",
            "target_id": "tab-1",
            "metadata": {"thread_id": "thread-1"},
        },
    )
    assert created.status_code == 200
    assert created.json()["session"]["status"] == "idle"

    action = client.post(
        "/api/control-sessions/ctrl-browser-1/actions",
        json={
            "action_id": "action-1",
            "action_type": "navigate",
            "status": "running",
            "descriptor": {"type": "navigate", "url": "https://example.com"},
        },
    )
    assert action.status_code == 200
    assert action.json()["action"]["status"] == "running"

    evidence = client.post(
        "/api/control-sessions/ctrl-browser-1/evidence",
        json={
            "evidence_id": "evidence-1",
            "action_id": "action-1",
            "kind": "result",
            "action": "navigate",
            "ok": True,
            "summary": "loaded",
            "detail": {"url": "https://example.com"},
        },
    )
    assert evidence.status_code == 200
    assert evidence.json()["evidence"]["seq"] == 1

    updated = client.patch(
        "/api/control-sessions/ctrl-browser-1/actions/action-1",
        json={"status": "done", "result": {"title": "Example"}},
    )
    assert updated.status_code == 200
    assert updated.json()["action"]["status"] == "done"

    replay = client.get("/api/control-sessions/ctrl-browser-1/replay")
    data = replay.json()
    assert data["schema"] == "echo.control_session_replay.v1"
    assert data["session"]["session_id"] == "ctrl-browser-1"
    assert data["actions"][0]["action_type"] == "navigate"
    assert data["evidence"][0]["summary"] == "loaded"
    assert data["timeline"]["schema"] == "echo.control_session_replay_timeline.v1"
    assert data["timeline"]["count"] == 3
    assert [item["kind"] for item in data["timeline"]["items"]] == [
        "action",
        "evidence",
        "action",
    ]
    assert data["timeline"]["items"][1]["cursor"]
    assert data["timeline"]["items"][1] | {
        "at": data["timeline"]["items"][1]["at"],
        "cursor": data["timeline"]["items"][1]["cursor"],
    } == {
        "id": "evidence:evidence-1",
        "kind": "evidence",
        "phase": "evidence",
        "at": data["timeline"]["items"][1]["at"],
        "evidence_id": "evidence-1",
        "action_id": "action-1",
        "action": "navigate",
        "status": "ok",
        "summary": "loaded",
        "detail_href": "/api/control-sessions/ctrl-browser-1/evidence/evidence-1/detail",
        "cursor": data["timeline"]["items"][1]["cursor"],
    }
    timeline = client.get("/api/control-sessions/ctrl-browser-1/timeline")
    assert timeline.status_code == 200
    timeline_body = timeline.json()
    assert timeline_body["schema"] == "echo.control_session_replay_timeline.v1"
    assert timeline_body["session_id"] == "ctrl-browser-1"
    assert timeline_body["status"] == "idle"
    assert timeline_body["items"] == data["timeline"]["items"]
    assert all(item["cursor"] for item in timeline_body["items"])
    assert timeline_body["next_after"] == data["timeline"]["items"][-1]["at"]
    assert timeline_body["next_cursor"] == data["timeline"]["items"][-1]["cursor"]
    assert timeline_body["has_more"] is False
    after = data["timeline"]["items"][0]["at"]
    incremental = client.get(f"/api/control-sessions/ctrl-browser-1/timeline?after={after}")
    assert incremental.status_code == 200
    incremental_body = incremental.json()
    assert incremental_body["after"] == after
    assert incremental_body["count"] == 2
    assert incremental_body["next_after"] == incremental_body["items"][-1]["at"]
    assert incremental_body["next_cursor"] == incremental_body["items"][-1]["cursor"]
    assert incremental_body["has_more"] is False
    assert [item["id"] for item in incremental_body["items"]] == [
        "evidence:evidence-1",
        "action:action-1:completed",
    ]
    first_cursor = data["timeline"]["items"][0]["cursor"]
    cursor_incremental = client.get(
        f"/api/control-sessions/ctrl-browser-1/timeline?after_cursor={first_cursor}"
    )
    assert cursor_incremental.status_code == 200
    cursor_body = cursor_incremental.json()
    assert cursor_body["after_cursor"] == first_cursor
    assert [item["id"] for item in cursor_body["items"]] == [
        "evidence:evidence-1",
        "action:action-1:completed",
    ]
    assert "page.goto" in data["playwright_script"]

    detail = client.get("/api/control-sessions/ctrl-browser-1/evidence/evidence-1/detail")
    assert detail.status_code == 200
    assert detail.json() == {
        "schema": "echo.control_evidence_detail.v1",
        "session_id": "ctrl-browser-1",
        "evidence_id": "evidence-1",
        "source": "inline",
        "detail": {"url": "https://example.com"},
    }


def test_control_session_timeline_cursor_paginates_beyond_first_limit(tmp_path) -> None:
    client, _store = _client(tmp_path)
    created = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-paged-1",
            "owner_id": "agent-1",
            "owner_label": "Agent 1",
            "surface": "browser",
            "target_id": "tab-1",
        },
    )
    assert created.status_code == 200

    for idx in range(5):
        response = client.post(
            "/api/control-sessions/ctrl-paged-1/evidence",
            json={
                "evidence_id": f"evidence-{idx}",
                "kind": "log",
                "action": "swarm_step",
                "ok": True,
                "summary": f"step {idx}",
                "detail": {"idx": idx},
                "created_at": 1000.0 + idx,
            },
        )
        assert response.status_code == 200

    first_page = client.get("/api/control-sessions/ctrl-paged-1/timeline?limit=2").json()
    assert first_page["count"] == 2
    assert first_page["has_more"] is True
    assert [item["id"] for item in first_page["items"]] == [
        "evidence:evidence-0",
        "evidence:evidence-1",
    ]

    second_page = client.get(
        "/api/control-sessions/ctrl-paged-1/timeline?limit=2"
        f"&after_cursor={first_page['next_cursor']}"
    ).json()
    assert second_page["after_cursor"] == first_page["next_cursor"]
    assert second_page["count"] == 2
    assert second_page["has_more"] is True
    assert [item["id"] for item in second_page["items"]] == [
        "evidence:evidence-2",
        "evidence:evidence-3",
    ]

    third_page = client.get(
        "/api/control-sessions/ctrl-paged-1/timeline?limit=2"
        f"&after_cursor={second_page['next_cursor']}"
    ).json()
    assert third_page["count"] == 1
    assert third_page["has_more"] is False
    assert [item["id"] for item in third_page["items"]] == ["evidence:evidence-4"]


def test_control_session_timeline_pages_kimi_scale_swarm_replay(tmp_path) -> None:
    client, _store = _client(tmp_path)
    created = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-kimi-scale-1",
            "owner_id": "swarm-overview",
            "owner_label": "Swarm Overview",
            "surface": "backend_preview",
            "target_id": "agent-collaboration",
            "metadata": {
                "schema": "echo.group_fanout_capacity.v1",
                "requested_members": 300,
                "dispatched_members": 300,
            },
        },
    )
    assert created.status_code == 200

    for idx in range(300):
        response = client.post(
            "/api/control-sessions/ctrl-kimi-scale-1/evidence",
            json={
                "evidence_id": f"swarm-step-{idx:03d}",
                "kind": "log",
                "action": "swarm_step",
                "ok": True,
                "summary": f"agent {idx:03d} finished",
                "detail": {
                    "schema": "echo.swarm_replay_package.v1",
                    "agent_id": f"agent-{idx:03d}",
                    "capacity_tier": "kimi_scale",
                },
                "created_at": 2000.0 + idx / 1000,
            },
        )
        assert response.status_code == 200

    seen: list[str] = []
    after_cursor = ""
    for page_index in range(4):
        response = client.get(
            "/api/control-sessions/ctrl-kimi-scale-1/timeline?limit=75"
            + (f"&after_cursor={after_cursor}" if after_cursor else "")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["schema"] == "echo.control_session_replay_timeline.v1"
        assert body["count"] == 75
        assert body["has_more"] is (page_index < 3)
        seen.extend(item["id"] for item in body["items"])
        after_cursor = body["next_cursor"]

    assert len(seen) == 300
    assert len(set(seen)) == 300
    assert seen[0] == "evidence:swarm-step-000"
    assert seen[-1] == "evidence:swarm-step-299"


def test_control_session_takeover_pauses_and_counts(tmp_path) -> None:
    client, _store = _client(tmp_path)
    client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-computer-1",
            "owner_id": "agent",
            "owner_label": "Agent",
            "surface": "computer",
            "target_id": "local-pc",
        },
    )

    taken = client.post(
        "/api/control-sessions/ctrl-computer-1/takeover",
        json={
            "reason": "operator moved mouse",
            "owner_id": "human",
            "owner_label": "Human",
        },
    )
    assert taken.status_code == 200
    session = taken.json()["session"]
    assert session["status"] == "paused"
    assert session["paused"] is True
    assert session["owner_id"] == "human"
    assert session["takeover_count"] == 1

    resumed = client.post("/api/control-sessions/ctrl-computer-1/resume", json={})
    assert resumed.status_code == 200
    assert resumed.json()["session"]["status"] == "idle"


def test_control_session_rejects_invalid_surface(tmp_path) -> None:
    client, _store = _client(tmp_path)
    response = client.post(
        "/api/control-sessions",
        json={"session_id": "ctrl-bad-1", "surface": "spaceship"},
    )
    assert response.status_code == 400


def test_control_action_ttl_expires_and_emits_event(tmp_path) -> None:
    client, store = _client(tmp_path)
    created = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-expiry-1",
            "owner_id": "agent",
            "owner_label": "Agent",
            "surface": "computer",
            "target_id": "local-pc",
        },
    )
    assert created.status_code == 200

    action = client.post(
        "/api/control-sessions/ctrl-expiry-1/actions",
        json={
            "action_id": "action-expiring",
            "action_type": "click",
            "status": "running",
            "descriptor": {"type": "click", "x": 1, "y": 2},
            "ttl_seconds": 0.01,
        },
    )
    assert action.status_code == 200
    time.sleep(0.03)

    replay = client.get("/api/control-sessions/ctrl-expiry-1/replay").json()
    assert replay["session"]["status"] == "paused"
    assert replay["actions"][0]["status"] == "expired"
    assert replay["actions"][0]["error"] == "action expired"

    events = store.events_after("ctrl-expiry-1")
    assert any(event["type"] == "action_expired" for event in events)


def test_control_session_stores_large_swarm_replay_evidence_as_blob_ref(tmp_path) -> None:
    client, _store = _client(tmp_path)
    created = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-swarm-replay-1",
            "owner_id": "swarm-overview",
            "owner_label": "Swarm Overview",
            "surface": "backend_preview",
            "target_id": "agent-collaboration",
            "metadata": {"source": "swarm_replay_export"},
        },
    )
    assert created.status_code == 200

    replay_package = {
        "schema": "echo.swarm_replay_package.v1",
        "overview": {"status": "done", "resultCount": 60},
        "agents": [{"id": f"agent-{idx}", "status": "done"} for idx in range(12)],
        "timeline": [
            {
                "id": f"evt-{idx}",
                "agentId": f"agent-{idx % 12}",
                "event": "subagent",
                "status": "done",
                "summary": "x" * 2048,
            }
            for idx in range(700)
        ],
        "events": [{"id": f"evt-{idx}", "payload": "y" * 2048} for idx in range(700)],
    }
    raw = json.dumps(replay_package, ensure_ascii=False, sort_keys=True).encode()

    evidence = client.post(
        "/api/control-sessions/ctrl-swarm-replay-1/evidence",
        json={
            "evidence_id": "evidence-swarm-replay",
            "kind": "log",
            "action": "swarm_replay_export",
            "ok": True,
            "summary": "swarm replay package exported",
            "detail": replay_package,
        },
    )
    assert evidence.status_code == 200

    replay = client.get("/api/control-sessions/ctrl-swarm-replay-1/replay").json()
    detail = replay["evidence"][0]["detail"]
    evidence_node = next(
        item
        for item in replay["timeline"]["items"]
        if item["id"] == "evidence:evidence-swarm-replay"
    )
    assert evidence_node["detail_href"] == (
        "/api/control-sessions/ctrl-swarm-replay-1/evidence/evidence-swarm-replay/detail"
    )
    assert evidence_node["detail_schema"] == "echo.control_evidence_blob_ref.v1"
    assert evidence_node["truncated"] is True
    assert detail["schema"] == "echo.control_evidence_blob_ref.v1"
    assert detail["sha256"] == hashlib.sha256(raw).hexdigest()
    assert detail["bytes"] == len(raw)
    assert detail["truncated"] is True
    with open(detail["path"], "rb") as fh:
        blob = fh.read()
        assert hashlib.sha256(blob).hexdigest() == detail["sha256"]
        assert b"echo.swarm_replay_package.v1" in blob

    full_detail = client.get(
        "/api/control-sessions/ctrl-swarm-replay-1/evidence/evidence-swarm-replay/detail"
    )
    assert full_detail.status_code == 200
    body = full_detail.json()
    assert body["schema"] == "echo.control_evidence_detail.v1"
    assert body["source"] == "blob"
    assert body["detail"]["schema"] == "echo.swarm_replay_package.v1"
    assert len(body["detail"]["timeline"]) == 700

    other_session = client.post(
        "/api/control-sessions",
        json={
            "session_id": "ctrl-other-1",
            "owner_id": "agent",
            "owner_label": "Agent",
            "surface": "browser",
            "target_id": "tab",
        },
    )
    assert other_session.status_code == 200
    blocked = client.get("/api/control-sessions/ctrl-other-1/evidence/evidence-swarm-replay/detail")
    assert blocked.status_code == 404


def test_control_sessions_require_auth_when_enabled(tmp_path) -> None:
    store_id = IdentityStore()
    store_id.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_control_sessions_router(
            store=ControlSessionStore(base_dir=tmp_path),
            identity_store=store_id,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/control-sessions").status_code == 401
    assert (
        client.post(
            "/api/control-sessions",
            json={"session_id": "ctrl-auth-1", "surface": "browser", "target_id": "tab"},
        ).status_code
        == 401
    )
    assert client.post("/api/control-sessions/ctrl-auth-1/takeover").status_code == 401

    ok = client.get(
        "/api/control-sessions",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert ok.status_code == 200


def test_control_sessions_object_level_ownership_isolates_actors(tmp_path) -> None:
    # Regression for the control-session IDOR: with auth on, one authenticated
    # user must not be able to enumerate, read, or drive another user's session.
    store_id = IdentityStore()
    store_id.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    store_id.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")
    app = FastAPI()
    app.include_router(
        create_control_sessions_router(
            store=ControlSessionStore(base_dir=tmp_path),
            identity_store=store_id,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}

    created = client.post(
        "/api/control-sessions",
        json={"session_id": "ctrl-alice-1", "surface": "browser", "target_id": "tab"},
        headers=alice,
    )
    assert created.status_code == 200
    assert created.json()["session"]["creator_actor"] == "alice"

    client.post(
        "/api/control-sessions/ctrl-alice-1/evidence",
        json={"evidence_id": "ev-1", "kind": "log", "summary": "hi", "detail": {"x": 1}},
        headers=alice,
    )

    # Bob cannot enumerate Alice's session...
    bob_list = client.get("/api/control-sessions", headers=bob)
    assert bob_list.status_code == 200
    assert bob_list.json()["count"] == 0

    # ...and every per-session route returns 404 (not 403 — don't confirm it exists).
    for method, path in [
        ("get", "/api/control-sessions/ctrl-alice-1"),
        ("get", "/api/control-sessions/ctrl-alice-1/replay"),
        ("get", "/api/control-sessions/ctrl-alice-1/timeline"),
        ("get", "/api/control-sessions/ctrl-alice-1/evidence/ev-1/detail"),
        ("post", "/api/control-sessions/ctrl-alice-1/takeover"),
        ("post", "/api/control-sessions/ctrl-alice-1/stop"),
        ("post", "/api/control-sessions/ctrl-alice-1/pause"),
    ]:
        resp = getattr(client, method)(path, headers=bob)
        assert resp.status_code == 404, f"{method} {path} leaked to non-owner: {resp.status_code}"

    bob_action = client.post(
        "/api/control-sessions/ctrl-alice-1/actions",
        json={"action_type": "click", "descriptor": {}},
        headers=bob,
    )
    assert bob_action.status_code == 404

    # Bob also can't hijack the id via create-or-takeover.
    bob_takeover = client.post(
        "/api/control-sessions",
        json={"session_id": "ctrl-alice-1", "surface": "browser", "takeover": True},
        headers=bob,
    )
    assert bob_takeover.status_code == 404

    # Alice retains full access to her own session.
    assert client.get("/api/control-sessions/ctrl-alice-1", headers=alice).status_code == 200
    assert client.get("/api/control-sessions", headers=alice).json()["count"] == 1
    assert (
        client.get(
            "/api/control-sessions/ctrl-alice-1/evidence/ev-1/detail", headers=alice
        ).status_code
        == 200
    )


def test_legacy_unowned_control_sessions_are_admin_only(tmp_path) -> None:
    store_id = IdentityStore()
    store_id.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    store_id.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )
    sessions = ControlSessionStore(base_dir=tmp_path)
    sessions.upsert_session(
        session_id="ctrl-legacy-1",
        surface="browser",
        target_id="tab",
        creator_actor=None,
    )
    app = FastAPI()
    app.include_router(
        create_control_sessions_router(
            store=sessions,
            identity_store=store_id,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice"}
    admin = {"Authorization": "Bearer sk-admin"}

    assert client.get("/api/control-sessions/ctrl-legacy-1", headers=alice).status_code == 404
    assert (
        client.post(
            "/api/control-sessions/ctrl-legacy-1/takeover",
            headers=alice,
        ).status_code
        == 404
    )
    assert client.get("/api/control-sessions", headers=alice).json()["count"] == 0

    assert client.get("/api/control-sessions/ctrl-legacy-1", headers=admin).status_code == 200
    admin_list = client.get("/api/control-sessions", headers=admin).json()
    assert [item["session_id"] for item in admin_list["sessions"]] == ["ctrl-legacy-1"]

