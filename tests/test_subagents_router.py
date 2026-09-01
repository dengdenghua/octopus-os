from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.sensing.gateway.subagents_router import create_subagents_router  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_subagents_router())
    return TestClient(app)


def _authenticated_client(
    tmp_path: Path,
) -> tuple[TestClient, dict[str, str], Path]:
    from runtime.memory.threads import ThreadStateStore
    from runtime.safety.auth import Identity, IdentityStore
    from runtime.sensing.gateway.thread_workspace import managed_workspace_metadata

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    threads = ThreadStateStore()
    workspace_root = tmp_path / "managed-workspaces"
    for actor, tenant, thread_id in (
        ("alice", "tenant-a", "alice-thread"),
        ("bob", "tenant-b", "bob-thread"),
    ):
        allocation = managed_workspace_metadata(
            workspace_root,
            tenant_id=tenant,
            actor_id=actor,
            thread_id=thread_id,
        )
        Path(allocation["workspace_path"]).mkdir(parents=True)
        threads.ensure_thread(
            thread_id,
            metadata={
                "owner_actor_id": actor,
                "tenant_id": tenant,
                **allocation,
            },
        )
    app = FastAPI()
    app.include_router(
        create_subagents_router(
            thread_store=threads,
            workspace_root=workspace_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    return (
        TestClient(app),
        {
            "alice": "Bearer sk-alice",
            "bob": "Bearer sk-bob",
        },
        workspace_root,
    )


def test_authenticated_dispatch_uses_only_server_managed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway.thread_workspace import managed_workspace_path

    client, tokens, workspace_root = _authenticated_client(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"agent_id": args[0], "output": "ok", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call_subagent)
    payload = {
        "subagent_type": "researcher",
        "prompt": "inspect safely",
        "thread_id": "alice-thread",
        "extra_tools": ["exec_shell"],
        "context": {
            "workspace_path": "/etc",
            "_locked_write_root": "/",
            "tool_allowlist_mode": "all",
            "allowed_tools": ["exec_shell"],
            "actor": "mallory",
            "tenant_id": "tenant-z",
            "runtime_session_metadata": {
                "workspace_path": "/tmp/attacker",
                "tool_allowlist_mode": "all",
                "owner_actor_id": "mallory",
            },
        },
    }

    assert client.post("/api/subagents/dispatch", json=payload).status_code == 401
    assert (
        client.post(
            "/api/subagents/dispatch",
            headers={"Authorization": tokens["bob"]},
            json=payload,
        ).status_code
        == 404
    )
    response = client.post(
        "/api/subagents/dispatch",
        headers={"Authorization": tokens["alice"]},
        json=payload,
    )

    assert response.status_code == 200
    expected = managed_workspace_path(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="alice-thread",
    )
    call = calls[-1]["kwargs"]
    context = call["context"]
    assert call["workspace_path"] == str(expected)
    assert context["workspace_path"] == str(expected)
    assert context["_locked_write_root"] == str(expected)
    assert context["tool_allowlist_mode"] == "role"
    assert context["actor"] == "alice"
    assert context["owner_actor_id"] == "alice"
    assert context["tenant_id"] == "tenant-a"
    assert "allowed_tools" not in context
    assert "extra_tools" not in context
    assert context["runtime_session_metadata"]["workspace_path"] == str(expected)
    assert context["runtime_session_metadata"]["owner_actor_id"] == "alice"


def test_authenticated_stream_dispatch_and_sessions_share_owned_thread_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.subagents.sessions import SubagentSessionStore

    client, tokens, _workspace_root = _authenticated_client(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {"agent_id": args[0], "output": "ok", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call_subagent)
    sessions = SubagentSessionStore(base_dir=tmp_path / "sessions")
    alice_session = sessions.create(
        agent_id="researcher",
        thread_id="alice-thread",
        owner_actor_id="alice",
        tenant_id="tenant-a",
    )
    hidden_session = sessions.create(
        agent_id="writer",
        thread_id="alice-thread",
        owner_actor_id="bob",
        tenant_id="tenant-b",
    )
    monkeypatch.setattr(
        "runtime.execution.subagents.sessions.get_subagent_session_store",
        lambda: sessions,
    )

    payload = {
        "subagent_type": "researcher",
        "prompt": "stream safely",
        "thread_id": "alice-thread",
        "context": {"workspace_path": "/", "tool_allowlist_mode": "all"},
    }
    assert (
        client.post(
            "/api/subagents/dispatch/stream",
            headers={"Authorization": tokens["bob"]},
            json=payload,
        ).status_code
        == 404
    )
    with client.stream(
        "POST",
        "/api/subagents/dispatch/stream",
        headers={"Authorization": tokens["alice"]},
        json=payload,
    ) as response:
        assert response.status_code == 200
        response.read()
    assert calls[-1]["kwargs"]["context"]["tool_allowlist_mode"] == "role"
    assert calls[-1]["kwargs"]["context"]["workspace_path"] != "/"

    assert (
        client.get(
            "/api/subagents/sessions",
            headers={"Authorization": tokens["alice"]},
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/subagents/sessions",
            params={"target": "alice-thread"},
            headers={"Authorization": tokens["bob"]},
        ).status_code
        == 404
    )
    allowed = client.get(
        "/api/subagents/sessions",
        params={"target": "alice-thread"},
        headers={"Authorization": tokens["alice"]},
    )
    assert allowed.status_code == 200
    ids = {item["sessionId"] for item in allowed.json()["candidates"]}
    assert alice_session.session_id in ids
    assert hidden_session.session_id not in ids


def test_dispatch_bounds_timeout_and_enforces_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "agent_id": args[0],
            "output": "ok",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        fake_call_subagent,
    )

    response = _client().post(
        "/api/subagents/dispatch",
        json={
            "subagent_type": "researcher",
            "prompt": "check",
            "timeout_s": 999999,
        },
    )

    assert response.status_code == 200
    assert calls[0]["kwargs"]["timeout_s"] == 900
    assert calls[0]["kwargs"]["timeout_seconds"] == 900.0


def test_dispatch_raises_tiny_timeout_to_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "agent_id": args[0],
            "output": "ok",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        fake_call_subagent,
    )

    response = _client().post(
        "/api/subagents/dispatch",
        json={
            "subagent_type": "researcher",
            "prompt": "check",
            "timeout_s": -10,
        },
    )

    assert response.status_code == 200
    assert calls[0]["kwargs"]["timeout_s"] == 1
    assert calls[0]["kwargs"]["timeout_seconds"] == 1.0


def test_dispatch_forwards_top_level_trace_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "agent_id": args[0],
            "output": "ok",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        fake_call_subagent,
    )

    response = _client().post(
        "/api/subagents/dispatch",
        json={
            "subagent_type": "researcher",
            "prompt": "check",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "run_id": "run-1",
            "trace_id": "trace-1",
            "parent_task_id": "task-parent",
            "source": "router-test",
        },
    )

    assert response.status_code == 200
    ctx = calls[0]["kwargs"]["context"]
    assert ctx["thread_id"] == "thread-1"
    assert ctx["turn_id"] == "turn-1"
    assert ctx["run_id"] == "run-1"
    assert ctx["trace_id"] == "trace-1"
    assert ctx["parent_task_id"] == "task-parent"
    assert ctx["source"] == "router-test"


def test_stream_dispatch_uses_bounded_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        event_emitter = kwargs.get("event_emitter")
        if event_emitter:
            event_emitter({"type": "sub_text_delta", "delta": "hi"})
        return {
            "agent_id": args[0],
            "output": "ok",
            "success": True,
            "error": None,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        fake_call_subagent,
    )

    with _client().stream(
        "POST",
        "/api/subagents/dispatch/stream",
        json={
            "subagent_type": "researcher",
            "prompt": "check",
            "timeout_s": 1800,
        },
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert calls[0]["kwargs"]["timeout_s"] == 900
    assert calls[0]["kwargs"]["timeout_seconds"] == 900.0
    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "done"
    assert any(event.get("type") == "result" for event in events)


def test_stream_dispatch_preserves_trace_context_in_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        event_emitter = kwargs.get("event_emitter")
        ctx = kwargs["context"]
        if event_emitter:
            event_emitter(
                {
                    "type": "sub_text_delta",
                    "delta": "hi",
                    "trace": {
                        "thread_id": ctx["thread_id"],
                        "turn_id": ctx["turn_id"],
                    },
                    "thread_id": ctx["thread_id"],
                    "turn_id": ctx["turn_id"],
                }
            )
        return {
            "agent_id": args[0],
            "output": "ok",
            "success": True,
            "error": None,
            "trace": {
                "thread_id": ctx["thread_id"],
                "turn_id": ctx["turn_id"],
            },
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        fake_call_subagent,
    )

    with _client().stream(
        "POST",
        "/api/subagents/dispatch/stream",
        json={
            "subagent_type": "researcher",
            "prompt": "check",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
        },
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0]["trace"] == {"thread_id": "thread-1", "turn_id": "turn-1"}
    result = next(event for event in events if event.get("type") == "result")
    assert result["trace"] == {"thread_id": "thread-1", "turn_id": "turn-1"}


def test_list_subagent_session_candidates(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.execution.subagents.sessions import SubagentSessionStore

    store = SubagentSessionStore(base_dir=tmp_path / "s")
    s1 = store.create(agent_id="researcher", thread_id="t1")
    s2 = store.create(agent_id="writer", thread_id="t2")
    monkeypatch.setattr(
        "runtime.execution.subagents.sessions.get_subagent_session_store",
        lambda: store,
    )

    # Candidates are scoped to the calling thread (target): only same-thread
    # sessions surface, cross-thread sessions stay private (IDOR guard).
    response = _client().get(
        "/api/subagents/sessions",
        params={"target": "t1"},
    )
    assert response.status_code == 200
    ids = [c["sessionId"] for c in response.json()["candidates"]]
    assert s1.session_id in ids
    assert s2.session_id not in ids

    response = _client().get(
        "/api/subagents/sessions",
        params={"target": "t2"},
    )
    ids = [c["sessionId"] for c in response.json()["candidates"]]
    assert s2.session_id in ids
    assert s1.session_id not in ids


def test_list_subagent_session_candidates_query_and_target(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.execution.subagents.sessions import SubagentSessionStore

    store = SubagentSessionStore(base_dir=tmp_path / "s")
    s1 = store.create(agent_id="researcher", thread_id="t1")
    s2 = store.create(agent_id="writer", thread_id="t1")
    s3 = store.create(agent_id="coder", thread_id="t2")
    monkeypatch.setattr(
        "runtime.execution.subagents.sessions.get_subagent_session_store",
        lambda: store,
    )

    # target scopes to the thread; query filters by id substring.
    response = _client().get(
        "/api/subagents/sessions",
        params={"target": "t1", "query": s1.session_id[:8]},
    )
    assert response.status_code == 200
    ids = [c["sessionId"] for c in response.json()["candidates"]]
    assert s1.session_id in ids
    assert s2.session_id not in ids  # filtered by query
    assert s3.session_id not in ids  # different thread

    # Missing store → empty candidates, still 200.
    monkeypatch.setattr(
        "runtime.execution.subagents.sessions.get_subagent_session_store",
        lambda: None,
    )
    response = _client().get("/api/subagents/sessions")
    assert response.status_code == 200
    assert response.json()["candidates"] == []


def test_subagent_bus_sse_snapshot_replays_events() -> None:
    """GET /api/subagents/stream/{root} with limit returns a bounded snapshot."""
    from runtime.execution.subagents.event_bus import (
        EVT_SUB_CONCLUDED,
        EVT_SUB_STARTED,
        publish_subagent_event,
        reset_for_tests,
    )

    reset_for_tests()
    try:
        publish_subagent_event(
            EVT_SUB_STARTED,
            {"role": "researcher"},
            thread_id="c",
            root_thread_id="streamroot",
        )
        publish_subagent_event(
            EVT_SUB_CONCLUDED,
            {"role": "researcher", "ok": True},
            thread_id="c",
            root_thread_id="streamroot",
        )
        response = _client().get("/api/subagents/stream/streamroot", params={"limit": 2})
        assert response.status_code == 200
        types = []
        for line in response.text.splitlines():
            if line.startswith("data: "):
                types.append(json.loads(line[6:]).get("type"))
        assert types == ["sub_started", "sub_concluded", "done"]
    finally:
        reset_for_tests()


def test_subagent_bus_sse_empty_root_returns_done() -> None:
    response = _client().get("/api/subagents/stream/nope", params={"limit": 5})
    assert response.status_code == 200
    assert response.text.strip().endswith('{"type":"done"}')


def test_subagent_bus_sse_requires_owned_root_thread() -> None:
    """Auth alone is insufficient: the event-bus root must belong to caller."""
    from runtime.execution.subagents.event_bus import (
        EVT_SUB_CONCLUDED,
        publish_subagent_event,
        reset_for_tests,
    )
    from runtime.memory.threads import ThreadStateStore
    from runtime.safety.auth import Identity, IdentityStore

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    threads = ThreadStateStore()
    threads.ensure_thread(
        "private-root",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    app = FastAPI()
    app.include_router(
        create_subagents_router(
            thread_store=threads,
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)

    reset_for_tests()
    try:
        publish_subagent_event(
            EVT_SUB_CONCLUDED,
            {"role": "researcher", "ok": True, "output": "private result"},
            thread_id="child",
            root_thread_id="private-root",
        )
        endpoint = "/api/subagents/stream/private-root"
        assert client.get(endpoint, params={"limit": 1}).status_code == 401
        assert (
            client.get(
                endpoint,
                params={"limit": 1},
                headers={"Authorization": "Bearer sk-bob"},
            ).status_code
            == 404
        )
        allowed = client.get(
            endpoint,
            params={"limit": 1},
            headers={"Authorization": "Bearer sk-alice"},
        )
        assert allowed.status_code == 200
        assert "private result" in allowed.text
    finally:
        reset_for_tests()


def test_subagent_bus_live_queue_drops_oldest_when_full() -> None:
    import asyncio

    from runtime.sensing.gateway.subagents_router import _put_bounded_bus_event

    async def exercise() -> list[int]:
        queue: asyncio.Queue[dict[str, int]] = asyncio.Queue(maxsize=2)
        _put_bounded_bus_event(queue, {"seq": 1})
        _put_bounded_bus_event(queue, {"seq": 2})
        _put_bounded_bus_event(queue, {"seq": 3})
        return [(await queue.get())["seq"], (await queue.get())["seq"]]

    assert asyncio.run(exercise()) == [2, 3]


def _authenticated_dispatch_app(tmp_path: Any) -> tuple[TestClient, Any, Any]:
    from runtime.memory.threads import ThreadStateStore
    from runtime.safety.auth import Identity, IdentityStore
    from runtime.sensing.gateway.thread_workspace import managed_workspace_metadata

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    threads = ThreadStateStore()
    workspace_root = tmp_path / "managed"
    allocation = managed_workspace_metadata(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="alice-thread",
    )
    Path(allocation["workspace_path"]).mkdir(parents=True)
    threads.ensure_thread(
        "alice-thread",
        metadata={
            **allocation,
            "owner_actor_id": "alice",
            "tenant_id": "tenant-a",
        },
    )
    app = FastAPI()
    app.include_router(
        create_subagents_router(
            thread_store=threads,
            workspace_root=workspace_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    return TestClient(app), threads, Path(allocation["workspace_path"])


def test_authenticated_direct_dispatch_overrides_workspace_identity_and_tool_policy(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_call_subagent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        emitter = kwargs.get("event_emitter")
        if emitter is not None:
            emitter({"type": "sub_text_delta", "delta": "ok"})
        return {"agent_id": args[0], "output": "ok", "success": True, "error": None}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake_call_subagent)
    client, _threads, managed = _authenticated_dispatch_app(tmp_path)
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}
    attack = {
        "subagent_type": "researcher",
        "prompt": "inspect",
        "thread_id": "alice-thread",
        "extra_tools": ["exec_shell", "write_text_file"],
        "context": {
            "actor": "mallory",
            "tenant_id": "tenant-x",
            "workspace_path": "/",
            "_locked_write_root": "/",
            "allowed_write_paths": ["/"],
            "tool_allowlist_mode": "all",
            "extra_tool_allowlist": ["*"],
            "runtime_session_metadata": {
                "workspace_path": "/tmp/escape",
                "_artifact_output_root": "/tmp/public",
            },
        },
    }

    assert (
        client.post(
            "/api/subagents/dispatch", headers=alice, json={**attack, "thread_id": None}
        ).status_code
        == 400
    )
    assert client.post("/api/subagents/dispatch", headers=bob, json=attack).status_code == 404
    allowed = client.post("/api/subagents/dispatch", headers=alice, json=attack)
    assert allowed.status_code == 200
    with client.stream(
        "POST",
        "/api/subagents/dispatch/stream",
        headers=alice,
        json=attack,
    ) as streamed:
        assert streamed.status_code == 200
        assert '"type":"done"' in streamed.read().decode("utf-8")

    assert len(calls) == 2
    for call in calls:
        context = call["kwargs"]["context"]
        assert call["kwargs"]["workspace_path"] == str(managed)
        assert context["thread_id"] == "alice-thread"
        assert context["actor"] == "alice"
        assert context["owner_actor_id"] == "alice"
        assert context["tenant_id"] == "tenant-a"
        assert context["workspace_path"] == str(managed)
        assert context["_locked_write_root"] == str(managed)
        assert context["tool_allowlist_mode"] == "role"
        assert "extra_tools" not in context
        runtime_metadata = context["runtime_session_metadata"]
        assert runtime_metadata["workspace_path"] == str(managed)
        assert runtime_metadata["_locked_write_root"] == str(managed)
        assert runtime_metadata["_artifact_output_root"] == str(managed / "output" / "final")


def test_authenticated_session_listing_and_continue_are_actor_tenant_scoped(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.subagents.sessions import SubagentSessionStore

    client, _threads, _managed = _authenticated_dispatch_app(tmp_path)
    store = SubagentSessionStore(base_dir=tmp_path / "sessions")
    alice_session = store.create(
        agent_id="researcher",
        thread_id="alice-thread",
        owner_actor_id="alice",
        tenant_id="tenant-a",
    )
    bob_session = store.create(
        agent_id="researcher",
        # Same parent thread id demonstrates why thread scoping alone is not
        # sufficient for a durable transcript boundary.
        thread_id="alice-thread",
        owner_actor_id="bob",
        tenant_id="tenant-b",
    )
    monkeypatch.setattr(
        "runtime.execution.subagents.sessions.get_subagent_session_store",
        lambda: store,
    )
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}

    assert client.get("/api/subagents/sessions", headers=alice).status_code == 400
    assert (
        client.get(
            "/api/subagents/sessions",
            headers=bob,
            params={"target": "alice-thread"},
        ).status_code
        == 404
    )
    listed = client.get(
        "/api/subagents/sessions",
        headers=alice,
        params={"target": "alice-thread"},
    )
    assert listed.status_code == 200
    ids = {item["sessionId"] for item in listed.json()["candidates"]}
    assert alice_session.session_id in ids
    assert bob_session.session_id not in ids

    continued = client.post(
        "/api/subagents/dispatch",
        headers=alice,
        json={
            "subagent_type": "researcher",
            "prompt": "continue private transcript",
            "thread_id": "alice-thread",
            "continue_session_id": bob_session.session_id,
        },
    )
    assert continued.status_code == 400
    assert "unknown subagent session" in continued.json()["detail"]

