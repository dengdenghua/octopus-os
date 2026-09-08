"""Exercise the real MCP HTTP transport against Echo's native executor."""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("mcp", reason="optional MCP transport requires echo-os[mcp]")

from runtime.execution.request import (
    ExecutionRequest,
    ExecutionResources,
    ExecutionTask,
    current_execution_request,
)
from runtime.execution.suckers.builtins import _read_file
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.execution.suckers.write_skills import _write_text_file
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.host_mcp import HostMCPBridge
from runtime.execution.tool_engine.host_tool_broker import HostToolBroker
from runtime.memory.journal import InMemoryJournal
from runtime.platform.process.scope import ExecutionScope
from runtime.platform.process.session import Session, current_session
from runtime.safety.approval.approval_gate import AutoDenyProvider
from runtime.safety.auth import TrustEngine


def host(tmp_path, *, readonly=False, expired=False):
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "note.txt").write_text("before", encoding="utf-8")
    agent = SimpleNamespace(
        agent_id="coder",
        arms=[
            SimpleNamespace(
                arm_id="test", allowed_skills=["read_file", "write_text_file", "plugin_probe"]
            )
        ],
        extra_skills=[],
    )
    session = Session(
        actor="actor-a",
        agent=agent,
        thread_id="thread-a",
        turn_id="turn-a",
        metadata={
            "tenant_id": "tenant-a",
            "mode": "code",
            "workspace_path": str(workspace),
            "extra_workspaces": [str(workspace)],
        },
    )
    permissions = ExecutionScope(
        mode="code",
        requested_mode="code",
        readable_roots=(workspace,),
        writable_roots=() if readonly else (workspace,),
        network_policy="deny",
        shell_policy="deny",
    )
    request = ExecutionRequest(
        ExecutionTask(
            task_id="turn-a",
            thread_id="thread-a",
            actor_id="actor-a",
            tenant_id="tenant-a",
            goal="read and write a file",
            permissions=permissions,
            resources=ExecutionResources(5000, 1.0, time.monotonic() + (-1 if expired else 60)),
        ),
        "read and write a file",
    )

    session.execution_request = request

    def probe():
        active = current_session()
        assert current_execution_request().task.permissions is request.task.permissions
        assert current_execution_request().task.resources is request.task.resources
        assert current_execution_request().task.execution_engine == "native"
        return {
            "actor": active.actor,
            "tenant": active.metadata["tenant_id"],
            "turn": active.turn_id,
        }

    registry = SkillRegistry()
    for name, handler, source in [
        ("read_file", _read_file, "skill://public/read_file"),
        ("write_text_file", _write_text_file, "skill://public/write_text_file"),
        ("plugin_probe", probe, "plugin://fixture/probe"),
    ]:
        registry.register(
            Skill(
                name=name,
                description=name,
                handler=handler,
                trusted_source=source,
                affinity=["write"] if name == "write_text_file" else ["read"],
            ),
            verify_tests=False,
        )
    stack = SimpleNamespace(
        executor=ToolExecutor(
            registry=registry,
            immunity=TrustEngine(trusted_sources=["skill://public/*", "plugin://fixture/*"]),
            journal=InMemoryJournal(),
        )
    )
    broker = HostToolBroker(
        stack,
        agent,
        context={**session.metadata, "caller_session": session},
        goal=request.instruction,
        outer_thread_id="thread-a",
        outer_turn_id="turn-a",
        workspace=str(workspace),
        tenant_id="tenant-a",
        principal_id="actor-a",
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
        server_auto_approve=True,
        execution_engine="native",
    )
    return HostMCPBridge(broker, request, session), registry, workspace


async def rpc(client, request_id, method, params=None):
    response = await client.post(
        "", json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    response.raise_for_status()
    data = response.json()
    assert "error" not in data, data
    return data["result"]


@pytest.mark.asyncio
async def test_authenticated_mcp_native_io_plugin_context_and_revocation(tmp_path):
    bridge, registry, workspace = host(tmp_path)
    async with bridge.serve() as connection:
        async with httpx.AsyncClient(trust_env=False) as anonymous:
            assert (await anonymous.post(connection.url, json={})).status_code == 401
        headers = {
            "Authorization": f"Bearer {connection.token}",
            "Accept": "application/json, text/event-stream",
        }
        async with httpx.AsyncClient(
            base_url=connection.url, headers=headers, trust_env=False
        ) as client:
            initial = await rpc(
                client,
                0,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
            assert initial["serverInfo"]["name"] == "Echo host tools"
            catalog = await rpc(client, 1, "tools/list")
            assert {t["name"] for t in catalog["tools"]} == {
                "read_file",
                "write_text_file",
                "plugin_probe",
            }
            read = await rpc(
                client, 2, "tools/call", {"name": "read_file", "arguments": {"path": "note.txt"}}
            )
            assert not read["isError"] and "before" in str(read["content"])
            args = {
                "name": "write_text_file",
                "arguments": {"path": "note.txt", "content": "after", "overwrite": True},
            }
            written = await rpc(client, 3, "tools/call", args)
            assert not written["isError"], written
            assert (workspace / "note.txt").read_text(encoding="utf-8") == "after"
            denied = await rpc(
                client,
                4,
                "tools/call",
                {
                    "name": "write_text_file",
                    "arguments": {"path": str(tmp_path / "outside.txt"), "content": "denied"},
                },
            )
            assert denied["isError"] and not (tmp_path / "outside.txt").exists()
            probe = await rpc(client, 5, "tools/call", {"name": "plugin_probe"})
            assert not probe["isError"]
            assert all(x in json.dumps(probe) for x in ["actor-a", "tenant-a", "turn-a"])
            registry.disable("plugin_probe")
            revoked = await rpc(client, 6, "tools/call", {"name": "plugin_probe"})
            assert revoked["isError"] and "disabled" in str(revoked["content"])
            bridge.broker.close()
            stopped = await rpc(
                client, 7, "tools/call", {"name": "read_file", "arguments": {"path": "note.txt"}}
            )
            assert stopped["isError"]
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            await client.post(connection.url, timeout=1)


@pytest.mark.asyncio
async def test_foreign_session_cannot_bind_to_host_task(tmp_path):
    bridge, _, _ = host(tmp_path)
    other = Session(
        actor="actor-b", thread_id="thread-a", turn_id="turn-a", metadata={"tenant_id": "tenant-a"}
    )
    with pytest.raises(ValueError, match="authenticated task"):
        HostMCPBridge(bridge.broker, bridge.request, other)


@pytest.mark.asyncio
async def test_read_only_task_can_read_but_cannot_write(tmp_path):
    bridge, _, workspace = host(tmp_path, readonly=True)
    async with (
        bridge.serve() as connection,
        httpx.AsyncClient(
            base_url=connection.url,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {connection.token}",
                "Accept": "application/json, text/event-stream",
            },
        ) as client,
    ):
        read = await rpc(
            client, 1, "tools/call", {"name": "read_file", "arguments": {"path": "note.txt"}}
        )
        assert not read["isError"], read
        write = await rpc(
            client,
            2,
            "tools/call",
            {
                "name": "write_text_file",
                "arguments": {"path": "new.txt", "content": "blocked"},
            },
        )
        assert write["isError"] and not (workspace / "new.txt").exists()


@pytest.mark.asyncio
async def test_closing_transport_drains_started_execution(tmp_path):
    bridge, _, _ = host(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {"success": True, "contentItems": [{"type": "inputText", "text": "done"}]}

    bridge.broker.invoke = slow
    from mcp import types

    async with bridge.serve():
        call = asyncio.create_task(
            bridge.call_tool(
                SimpleNamespace(request_id=1), types.CallToolRequestParams(name="read_file")
            )
        )
        await entered.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert len(bridge.pending) == 1
        release.set()
    assert not bridge.pending


@pytest.mark.parametrize(
    "mode,operator_allows,expected,environment",
    [
        ("bypassPermissions", True, "bypassPermissions", "local"),
        ("acceptEdits", True, "acceptEdits", "sandbox"),
        ("default", True, "default", "sandbox"),
        ("bypassPermissions", False, "default", "sandbox"),
    ],
)
def test_saved_thread_preserves_current_permission_choice(
    tmp_path, mode, operator_allows, expected, environment
):
    from runtime.execution.tool_engine.session_metadata import project_tool_session_metadata
    from runtime.platform.process.scope import resolve_execution_scope
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_turn_input import _build_intent

    params = TurnParams.model_validate(
        {
            "threadId": "saved-thread",
            "cwd": str(tmp_path),
            "approvalPolicy": "never",
            "input": [
                {
                    "type": "text",
                    "text": "read note.txt",
                    "metadata": {
                        "context": {
                            "mode": "code",
                            "permission_mode": mode,
                            "execution_environment": "local",
                        }
                    },
                }
            ],
        }
    )
    # Exercise the real merge, whose presentation allowlist drops permission fields.
    store = SimpleNamespace(
        get=lambda _: {"metadata": {"mode": "code", "permission_mode": "bypassPermissions"}}
    )
    intent = _build_intent(
        "read note.txt", params, thread_store=store, allow_client_auto_approve=operator_allows
    )
    scope = resolve_execution_scope(
        Session(
            thread_id="saved-thread", metadata=project_tool_session_metadata(intent.user_context)
        )
    )
    assert scope.permission_mode == expected
    assert scope.execution_environment == environment
    assert scope.allows_read(tmp_path.parent / "outside.txt") is (expected == "bypassPermissions")
    assert intent.user_context["auto_approve"] is (expected == "bypassPermissions")


@pytest.mark.asyncio
async def test_authenticated_transport_rejects_foreign_origin_host_and_oversized_body(tmp_path):
    bridge, _, _ = host(tmp_path)
    async with bridge.serve() as connection, httpx.AsyncClient(trust_env=False) as client:
        headers = {
            "Authorization": f"Bearer {connection.token}",
            "Accept": "application/json, text/event-stream",
        }
        response = await client.post(
            connection.url, headers={**headers, "Host": "attacker.example"}, json={}
        )
        assert response.status_code in {403, 421}
        response = await client.post(
            connection.url, headers={**headers, "Origin": "https://attacker.example"}, json={}
        )
        assert response.status_code == 403
        response = await client.post(
            connection.url,
            headers={**headers, "Content-Type": "application/json"},
            content=b"x" * 262_145,
        )
        assert response.status_code == 413
        assert connection.token not in repr(connection)
    with pytest.raises(RuntimeError, match="already served"):
        async with bridge.serve():
            pytest.fail("closed bridge was reopened")


@pytest.mark.parametrize("changed", ["permissions", "resources", "task_id"])
def test_same_named_task_cannot_replace_broker_authority(tmp_path, changed):
    bridge, _, _ = host(tmp_path)
    original = bridge.request
    # Create a second unbound transport over the captured broker to isolate
    # the authority check from the separate single-binding check.
    bridge.broker._inner_thread_id = None
    task = original.task
    replacement = {
        "permissions": replace(task.permissions, writable_roots=()),
        "resources": replace(task.resources, deadline=None),
        "task_id": "other-task",
    }[changed]
    forged = replace(original, task=replace(task, **{changed: replacement}))
    session = replace(bridge.session, execution_request=forged, turn_id=forged.task.task_id)
    with pytest.raises(ValueError, match="authenticated task authority"):
        HostMCPBridge(bridge.broker, forged, session)


@pytest.mark.asyncio
async def test_expired_task_cannot_list_or_execute_tools(tmp_path):
    from mcp import types

    bridge, _, workspace = host(tmp_path, expired=True)
    assert (await bridge.list_tools(None, None)).tools == []
    result = await bridge.call_tool(
        SimpleNamespace(request_id=1),
        types.CallToolRequestParams(
            name="write_text_file", arguments={"path": "blocked.txt", "content": "no"}
        ),
    )
    assert result.is_error and "deadline" in str(result.content)
    assert not (workspace / "blocked.txt").exists()
