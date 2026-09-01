"""Canonical thread/project ACLs inherited from an active TeamRoom seat."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.session import link_room
from runtime.memory.threads.store import ThreadStateStore
from runtime.projectos.model import Milestone, Project
from runtime.projectos.store import ProjectStore
from runtime.protocol import JsonRpcRequest, JsonRpcResponse, decode_message, encode_message
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router
from runtime.sensing.gateway.projects_router import create_projects_router
from runtime.sensing.gateway.realtime_echo import EchoRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway, _RpcError
from runtime.sensing.gateway.thread_access import ThreadAccessResolver
from runtime.sensing.gateway.thread_state_router import create_thread_state_router


class _Rooms:
    """Small role-aware stand-in for the TeamRoom router's exported resolver."""

    def __init__(self, *, tenant_id: str = "tenant-a") -> None:
        self.tenant_id = tenant_id
        self.roles: dict[str, str] = {
            "alice": "owner",
            "bob": "member",
            "carol": "viewer",
            "mallory": "member",
        }
        self.statuses: dict[str, str] = {}

    def get_room_participant(
        self,
        room_id: str,
        actor_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        if room_id != "room-a" or tenant_id != self.tenant_id:
            return None
        role = self.roles.get(actor_id)
        if role is None:
            return None
        return {
            "id": f"actor-{actor_id}",
            "actor_id": actor_id,
            "role": role,
            "status": self.statuses.get(actor_id, "active"),
        }


def _identities() -> IdentityStore:
    identities = IdentityStore()
    for actor, tenant in (
        ("alice", "tenant-a"),
        ("bob", "tenant-a"),
        ("carol", "tenant-a"),
        ("mallory", "tenant-b"),
    ):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": tenant}),
            api_key_plaintext=f"sk-{actor}",
        )
    return identities


def _headers(actor: str) -> dict[str, str]:
    return {"Authorization": f"Bearer sk-{actor}"}


def _linked_state(tmp_path: Path):
    identities = _identities()
    rooms = _Rooms()
    threads = ThreadStateStore()
    threads.ensure_thread(
        "project-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
        values={
            "title": "Shared launch",
            "messages": [{"type": "human", "content": "Ship the launch"}],
        },
    )
    threads.ensure_thread(
        "private-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
        values={"title": "Private"},
    )
    groups = GroupStore(base_dir=tmp_path / "cowork")
    link_room(groups, "project-thread", "room-a", actor="alice")
    collaboration = CollaborationStore(base_dir=tmp_path / "cowork")
    collaboration.upsert_room(
        "project-thread",
        {"id": "room-a", "name": "Launch", "tenant_id": "tenant-a"},
    )
    return identities, rooms, threads, groups, collaboration


def test_thread_and_cowork_roles_revoke_immediately(tmp_path: Path) -> None:
    identities, rooms, threads, groups, collaboration = _linked_state(tmp_path)
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=threads,
            identity_store=identities,
            require_auth=True,
            group_store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    app.include_router(
        create_cowork_group_router(
            store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
            runtime=SimpleNamespace(thread_store=threads),
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)

    for actor in ("bob", "carol"):
        assert client.get("/api/threads/project-thread", headers=_headers(actor)).status_code == 200
        assert (
            client.get("/api/threads/project-thread/state", headers=_headers(actor)).status_code
            == 200
        )
        history = client.post(
            "/api/threads/project-thread/history",
            headers=_headers(actor),
            json={},
        )
        assert history.status_code == 200
        assert history.json()
        assert client.get("/api/cowork/project-thread", headers=_headers(actor)).status_code == 200
        assert client.get("/api/collab/project-thread", headers=_headers(actor)).status_code == 200

    assert {
        row["thread_id"]
        for row in client.get("/api/threads/search", headers=_headers("carol")).json()["threads"]
    } == {"project-thread"}
    assert {
        row["thread_id"]
        for row in client.post(
            "/api/threads/search",
            headers=_headers("carol"),
            json={},
        ).json()
    } == {"project-thread"}

    # Viewer is read-only; member can use collaborative write surfaces, but
    # neither can take owner-only thread/group administration.
    assert (
        client.post(
            "/api/cowork/project-thread/blackboard",
            headers=_headers("carol"),
            json={"key": "viewer-write", "value": True},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/cowork/project-thread/blackboard",
            headers=_headers("bob"),
            json={"key": "member-write", "value": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/collab/project-thread/room-message",
            headers=_headers("bob"),
            json={"text": "member update", "participant_id": "actor-bob"},
        ).status_code
        == 200
    )
    for actor in ("bob", "carol"):
        assert (
            client.post(
                "/api/cowork/project-thread/mode",
                headers=_headers(actor),
                json={"mode": "swarm"},
            ).status_code
            == 404
        )
        assert (
            client.put(
                "/api/cowork/project-thread/roster",
                headers=_headers(actor),
                json={"agent_ids": ["worker"], "mode": "cluster"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/threads/project-thread/state",
                headers=_headers(actor),
                json={"values": {"title": "hijack"}},
            ).status_code
            == 404
        )

    assert client.get("/api/threads/private-thread", headers=_headers("bob")).status_code == 404
    assert client.get("/api/threads/project-thread", headers=_headers("mallory")).status_code == 404

    rooms.statuses["bob"] = "offline"
    assert client.get("/api/threads/project-thread", headers=_headers("bob")).status_code == 200
    assert (
        client.post(
            "/api/cowork/project-thread/blackboard",
            headers=_headers("bob"),
            json={"key": "offline-write", "value": True},
        ).status_code
        == 200
    )
    rooms.roles.pop("bob")
    assert client.get("/api/threads/project-thread", headers=_headers("bob")).status_code == 404
    assert client.get("/api/cowork/project-thread", headers=_headers("bob")).status_code == 404


def test_project_bound_room_participants_can_read_but_not_execute(tmp_path: Path) -> None:
    identities, rooms, threads, groups, collaboration = _linked_state(tmp_path)
    projects = ProjectStore(base_dir=tmp_path / "projects")
    projects.save_project(
        Project(
            id="P-shared",
            name="Shared project",
            goal="Launch",
            owner_id="alice",
            tenant_id="tenant-a",
        )
    )
    projects.save_milestone(
        "P-shared",
        Milestone(id="M-shared", name="Beta", goal="Ready for beta"),
    )
    projects.bind_thread("project-thread", "P-shared")

    app = FastAPI()
    app.include_router(
        create_projects_router(
            store=projects,
            group_store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
            thread_store=threads,
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)

    for actor in ("bob", "carol"):
        by_thread = client.get(
            "/api/projects/by-thread/project-thread",
            headers=_headers(actor),
        )
        assert by_thread.status_code == 200
        assert by_thread.json()["project"]["id"] == "P-shared"
        assert by_thread.json()["milestones"][0]["id"] == "M-shared"
        assert client.get("/api/projects/P-shared", headers=_headers(actor)).status_code == 200
        assert (
            client.get("/api/projects/P-shared/report", headers=_headers(actor)).status_code == 200
        )
        assert (
            client.post("/api/projects/P-shared/tick", headers=_headers(actor)).status_code == 404
        )

    assert client.get("/api/projects/P-shared", headers=_headers("mallory")).status_code == 404
    rooms.roles.pop("carol")
    assert client.get("/api/projects/P-shared", headers=_headers("carol")).status_code == 404
    assert (
        client.get(
            "/api/projects/by-thread/project-thread",
            headers=_headers("carol"),
        ).status_code
        == 404
    )


class _Emitter:
    def __init__(self, actor_id: str, tenant_id: str = "tenant-a") -> None:
        self.actor_id = actor_id
        self.tenant_id = tenant_id
        self.events: list[tuple[Any, dict[str, Any]]] = []

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        self.events.append((method, params))

    async def request_approval(self, method: Any, params: dict[str, Any], **kwargs: Any) -> Any:
        return {"action": "decline"}

    def register_turn(self, turn_id: str) -> None:
        return None

    def unregister_turn(self, turn_id: str) -> None:
        return None

    def is_turn_interrupted(self, turn_id: str) -> bool:
        return False


def test_realtime_viewer_reads_member_turns_and_removal_revokes(tmp_path: Path) -> None:
    identities, rooms, threads, groups, collaboration = _linked_state(tmp_path)
    resolver = ThreadAccessResolver(
        thread_store=threads,
        group_store=groups,
        collaboration_store=collaboration,
        team_rooms_router=rooms,
        identity_store=identities,
    )
    runtime = EchoRuntime(logs_root=tmp_path / "threads")
    runtime._thread_access_resolver = resolver

    viewer = _Emitter("carol")
    member = _Emitter("bob")
    assert (
        asyncio.run(
            runtime.handle_request(
                "thread/resume",
                {"threadId": "project-thread"},
                viewer,
            )
        )["thread"]["id"]
        == "project-thread"
    )
    with pytest.raises(_RpcError):
        asyncio.run(
            runtime.start_turn(
                {
                    "threadId": "project-thread",
                    "approvalPolicy": "never",
                    "input": [{"type": "text", "text": "viewer must not run"}],
                },
                viewer,
            )
        )

    turn = asyncio.run(
        runtime.start_turn(
            {
                "threadId": "project-thread",
                "approvalPolicy": "never",
                "input": [
                    {
                        "type": "text",
                        "text": "member can run",
                        "metadata": {"actor_id": "bob"},
                    }
                ],
            },
            member,
        )
    )
    assert turn.thread_id == "project-thread"

    gateway = RealtimeGateway(runtime=runtime, thread_access_resolver=resolver)
    conn = SimpleNamespace(actor_id="bob", tenant_id="tenant-a")
    decision = gateway._require_realtime_thread_access(
        "project-thread",
        conn,
        access="write",
    )
    sanitized = gateway._sanitize_turn_params(
        {"threadId": "project-thread", "input": [{"type": "text", "text": "x"}]},
        conn,
        thread_owner_actor_id=decision.owner_actor_id,
        thread_tenant_id=decision.tenant_id,
    )
    assert sanitized["owner_actor_id"] == "alice"
    assert sanitized["input"][0]["metadata"]["actor_id"] == "bob"
    assert sanitized["input"][0]["metadata"]["owner_actor_id"] == "alice"

    rooms.roles.pop("bob")
    with pytest.raises(_RpcError):
        asyncio.run(
            runtime.handle_request(
                "thread/resume",
                {"threadId": "project-thread"},
                member,
            )
        )


def test_realtime_project_command_is_owner_only_before_plan_or_binding(tmp_path: Path) -> None:
    identities, _rooms, threads, groups, collaboration = _linked_state(tmp_path)
    from runtime.memory.cowork.service import invite_member
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    invite_member(groups, "project-thread", actor="alice", target_id="builder", kind="agent")
    projects = ProjectStore(base_dir=tmp_path / "projects")
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "realtime-project-logs"),
        thread_store=threads,
        cowork_group_store=groups,
        collaboration_store=collaboration,
        project_store=projects,
    )

    turn = asyncio.run(
        runtime.start_turn(
            {
                "threadId": "project-thread",
                "approvalPolicy": "never",
                "input": [{"type": "text", "text": "/project run hijack owner thread"}],
            },
            _Emitter("bob"),
        )
    )

    assert turn.status == "failed"
    assert projects.project_for_thread("project-thread") is None
    assert projects.list_projects() == []
    errors = [item for item in turn.items if getattr(item, "type", "") == "error"]
    assert errors
    assert errors[-1].error_info["exception_type"] == "PermissionError"


def test_gateway_member_cannot_bind_project_to_owner_thread(tmp_path: Path) -> None:
    identities, rooms, threads, groups, collaboration = _linked_state(tmp_path)
    from runtime.memory.cowork.service import invite_member
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    invite_member(groups, "project-thread", actor="alice", target_id="builder", kind="agent")
    projects = ProjectStore(base_dir=tmp_path / "gateway-projects")
    resolver = ThreadAccessResolver(
        thread_store=threads,
        group_store=groups,
        collaboration_store=collaboration,
        team_rooms_router=rooms,
        identity_store=identities,
    )
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "gateway-project-logs"),
        workspace_root=tmp_path / "gateway-workspaces",
        thread_store=threads,
        cowork_group_store=groups,
        collaboration_store=collaboration,
        project_store=projects,
    )
    gateway = RealtimeGateway(
        runtime=runtime,
        identity_store=identities,
        require_auth=True,
        thread_access_resolver=resolver,
    )
    app = FastAPI()
    app.include_router(gateway.router)

    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/realtime",
            headers={"Authorization": "Bearer sk-bob"},
        ) as ws,
    ):
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="turn/start",
                    params={
                        "threadId": "project-thread",
                        "approvalPolicy": "never",
                        "input": [{"type": "text", "text": "/project run hijack"}],
                    },
                )
            )
        )
        response = None
        while response is None:
            message = decode_message(ws.receive_text())
            if isinstance(message, JsonRpcRequest):
                ws.send_text(
                    encode_message(JsonRpcResponse(id=message.id, result={"action": "decline"}))
                )
            elif isinstance(message, JsonRpcResponse) and message.id == 1:
                response = message

    assert response.result["turn"]["status"] == "failed"
    assert projects.project_for_thread("project-thread") is None
    assert projects.list_projects() == []

