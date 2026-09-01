"""Realtime compatibility for ownerless threads in local auth-off mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.session import link_room
from runtime.memory.threads.event_log import EventLog, thread_log_path
from runtime.memory.threads.store import ThreadStateStore
from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)
from runtime.sensing.gateway.realtime_echo import EchoRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
from runtime.sensing.gateway.thread_access import ThreadAccessResolver


def _ownerless_local_client(
    tmp_path: Path,
    *,
    thread_id: str,
) -> tuple[TestClient, Path]:
    logs_root = tmp_path / "threads"
    threads = ThreadStateStore()
    threads.ensure_thread(thread_id, metadata={"mode": "code"})
    log_path = thread_log_path(logs_root, thread_id)
    EventLog(log_path).thread_started(thread_id)

    resolver = ThreadAccessResolver(
        thread_store=threads,
        allow_anonymous_ownerless=True,
    )
    runtime = EchoRuntime(logs_root=logs_root)
    runtime._thread_access_resolver = resolver
    gateway = RealtimeGateway(
        runtime=runtime,
        require_auth=False,
        thread_access_resolver=resolver,
        allow_client_approval_bypass=True,
    )
    app = FastAPI()
    app.include_router(gateway.router)
    return TestClient(app), log_path


def _receive_response(ws: Any, request_id: int) -> JsonRpcResponse:
    while True:
        message = decode_message(ws.receive_text())
        if isinstance(message, JsonRpcResponse) and message.id == request_id:
            return message
        assert isinstance(message, Notification)


def test_auth_off_ownerless_thread_can_resume(tmp_path: Path) -> None:
    thread_id = "eval-ownerless-resume"
    client, _log_path = _ownerless_local_client(tmp_path, thread_id=thread_id)

    with client, client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="thread/resume",
                    params={"threadId": thread_id},
                )
            )
        )
        response = _receive_response(ws, 1)

    assert response.error is None
    assert response.result["thread"]["id"] == thread_id


def test_auth_off_ownerless_thread_can_start_followup(tmp_path: Path) -> None:
    thread_id = "eval-ownerless-followup"
    client, log_path = _ownerless_local_client(tmp_path, thread_id=thread_id)

    with client, client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=2,
                    method="turn/start",
                    params={
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": "continue"}],
                        "approvalPolicy": "never",
                    },
                )
            )
        )
        response = _receive_response(ws, 2)

    assert response.error is None
    assert response.result["turn"]["threadId"] == thread_id
    assert response.result["turn"]["status"] == "completed"
    assert '"event":"turn_started"' in log_path.read_text(encoding="utf-8")


def test_ownerless_compat_is_opt_in_and_does_not_cover_linked_rooms(tmp_path: Path) -> None:
    thread_id = "ownerless-linked"
    threads = ThreadStateStore()
    threads.ensure_thread(thread_id)

    strict = ThreadAccessResolver(thread_store=threads).resolve(thread_id, None)
    assert strict.thread is not None
    assert not strict.can_read
    assert not strict.can_write
    assert not strict.can_manage

    groups = GroupStore(base_dir=tmp_path / "cowork")
    link_room(groups, thread_id, "room-a", actor="local")
    linked = ThreadAccessResolver(
        thread_store=threads,
        group_store=groups,
        allow_anonymous_ownerless=True,
    ).resolve(thread_id, None)

    assert linked.room_id == "room-a"
    assert not linked.can_read
    assert not linked.can_write
    assert not linked.can_manage

