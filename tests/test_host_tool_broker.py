"""Shared engine tools retain real executor state and drain interrupted writes."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from runtime.execution.codex_backend.dynamic_tools import CodexDynamicToolBroker
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.builtins import _read_file
from runtime.execution.suckers.write_skills import _write_text_file
from runtime.execution.tool_engine import ToolExecutor
from runtime.execution.tool_engine.host_tool_broker import HostToolBroker
from runtime.memory.journal import InMemoryJournal
from runtime.safety.approval.approval_gate import ApprovalDecision, AutoDenyProvider
from runtime.safety.auth import TrustEngine


def make_broker(tmp_path, monkeypatch, *, shared=False, context=None, max_tools=None):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    registry = SkillRegistry()
    for name, handler, affinity in (
        ("read_file", _read_file, ["file", "read"]),
        ("write_text_file", _write_text_file, ["file", "write"]),
    ):
        registry.register(
            Skill(
                name=name,
                handler=handler,
                description=name,
                affinity=affinity,
                trusted_source=f"builtin://test/{name}",
            ),
            verify_tests=False,
        )
    executor = ToolExecutor(
        registry,
        TrustEngine(trusted_sources=["builtin://test/*"]),
        InMemoryJournal(),
        effect_store_path=tmp_path / "effects.sqlite3",
    )
    cls = HostToolBroker if shared else CodexDynamicToolBroker
    kwargs = {"execution_engine": "native"} if shared else {}
    if max_tools is not None:
        kwargs["max_tools"] = max_tools
    broker = cls(
        SimpleNamespace(executor=executor),
        SimpleNamespace(
            agent_id="test",
            arms=[SimpleNamespace(arm_id="test", allowed_skills=["read_file", "write_text_file"])],
            extra_skills=[],
            capabilities={"code_mode_unlock": True},
        ),
        context={
            "mode": "code",
            "permission_mode": "bypassPermissions",
            "execution_environment": "local",
            **(context or {}),
        },
        goal="Read and update the requested test file",
        outer_thread_id="thread",
        outer_turn_id="turn",
        workspace=str(tmp_path),
        tenant_id="tenant",
        principal_id="actor",
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
        server_auto_approve=True,
        **kwargs,
    )
    broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")
    return broker


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
async def test_real_read_then_write_retains_evidence_between_callbacks(
    tmp_path, monkeypatch, shared
):
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")
    broker = make_broker(tmp_path, monkeypatch, shared=shared)
    blocked = await broker.invoke(
        "write_text_file",
        {"path": str(target), "content": "wrong", "overwrite": True},
        call_id="unread",
    )
    assert not blocked["success"]
    assert target.read_text(encoding="utf-8") == "before"
    read = await broker.invoke("read_file", {"path": str(target)}, call_id="read")
    assert read["success"], read
    written = await broker.invoke(
        "write_text_file",
        {"path": str(target), "content": "after", "overwrite": True},
        call_id="write",
    )
    assert written["success"], written
    assert target.read_text(encoding="utf-8") == "after"
    assert broker._metadata["_file_read_snapshots"]
    await broker.aclose()


@pytest.mark.asyncio
async def test_context_cannot_forge_read_evidence(tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")
    broker = make_broker(
        tmp_path,
        monkeypatch,
        context={
            "_read_file_paths_this_turn": [str(target).casefold()],
            "_file_read_snapshots": {str(target): {}},
        },
    )
    result = await broker.invoke(
        "write_text_file",
        {"path": str(target), "content": "wrong", "overwrite": True},
        call_id="forged",
    )
    assert not result["success"]
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_disconnect_does_not_abandon_write_and_close_drains_it(tmp_path, monkeypatch):
    broker = make_broker(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    calls = []

    def write(*args):
        calls.append(args[2])
        entered.set()
        assert release.wait(10), "test did not release admitted worker"
        (tmp_path / "receipt.txt").write_text("committed", encoding="utf-8")
        completed.set()
        return "written", False, "none"

    monkeypatch.setattr(broker, "_execute_sync", write)
    transport = asyncio.create_task(
        broker.invoke(
            "write_text_file",
            {"path": str(tmp_path / "receipt.txt"), "content": "committed"},
            call_id="write",
        )
    )
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        transport.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transport
        closing = asyncio.create_task(broker.aclose())
        await asyncio.sleep(0)
        assert broker._interrupted()
        assert not closing.done()
        denied = await broker.invoke(
            "read_file", {"path": str(tmp_path / "receipt.txt")}, call_id="late"
        )
        assert not denied["success"]
    finally:
        release.set()
        await broker.aclose()
    await closing
    assert completed.is_set()
    assert calls == ["write"]
    assert (tmp_path / "receipt.txt").read_text(encoding="utf-8") == "committed"
    assert not broker._pending


@pytest.mark.parametrize("limit", [False, 0, -1, 257, "2"])
def test_catalog_budget_rejects_invalid_host_limits(tmp_path, monkeypatch, limit):
    with pytest.raises(ValueError, match="catalog limit"):
        make_broker(tmp_path, monkeypatch, shared=True, max_tools=limit)


def test_catalog_budget_and_canonical_name(tmp_path, monkeypatch):
    broker = make_broker(tmp_path, monkeypatch, shared=True, max_tools=1)
    assert len(broker.catalog.names) == 1
    assert broker.skill_name(broker.catalog.names[0]) in {"read_file", "write_text_file"}
    assert broker.skill_name("unadvertised") is None


@pytest.mark.asyncio
async def test_approval_callback_restores_host_identity_instead_of_ambient_session(
    tmp_path, monkeypatch
):
    from runtime.platform.process.session import Session, current_session, session_scope

    broker = make_broker(tmp_path, monkeypatch)
    broker._server_auto_approve = False
    observed = []

    def review(request, **kwargs):
        session = current_session()
        observed.append((session.actor, session.thread_id, session.metadata["tenant_id"]))
        return ApprovalDecision(approved=False, reason="test denial")

    broker._approval_provider = SimpleNamespace(request=review)
    with session_scope(
        Session(actor="another", thread_id="another-thread", metadata={"tenant_id": "another"})
    ):
        result = await broker.invoke(
            "write_text_file",
            {"path": str(tmp_path / "denied.txt"), "content": "denied"},
            call_id="review",
        )
        assert current_session().actor == "another"
    assert not result["success"]
    assert observed == [("actor", "thread", "tenant")]
    assert not (tmp_path / "denied.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_close_fails", [False, True])
async def test_codex_lifecycle_revokes_and_drains_broker_even_on_close_failure(
    tmp_path,
    monkeypatch,
    session_close_fails,
):
    from runtime.execution.codex_backend.backend import CodexExecutionRequest
    from runtime.execution.codex_backend.role_runner import codex_execution_lifecycle

    broker = make_broker(tmp_path, monkeypatch)
    order = []
    original_close = broker.close

    def revoke():
        order.append("revoke")
        original_close()

    async def drain():
        order.append("drain")

    async def close_session():
        order.append("session")
        if session_close_fails:
            raise RuntimeError("session close failure")

    monkeypatch.setattr(broker, "close", Mock(side_effect=revoke))
    monkeypatch.setattr(broker, "aclose", AsyncMock(side_effect=drain))
    request = CodexExecutionRequest(
        outer_thread_id="thread",
        outer_turn_id="turn",
        workspace=tmp_path,
        prompt="inspect",
        realm_id="test",
        tenant_id="tenant",
        principal_id="actor",
        command=("codex", "app-server"),
        dynamic_tool_handler=broker,
    )

    async def lifecycle():
        async with codex_execution_lifecycle(
            None,
            request,
            trusted_session=None,
            approval_provider=AutoDenyProvider(),
            is_interrupted=lambda: False,
            timeout_s=10,
            state_root=tmp_path / "codex",
            deployment_mode_value="local",
            process_backend=object(),
            session_factory=lambda *a, **kw: SimpleNamespace(close=close_session),
        ):
            assert not broker._interrupted()

    if session_close_fails:
        with pytest.raises(RuntimeError, match="session close failure"):
            await lifecycle()
    else:
        await lifecycle()
    assert order == ["revoke", "session", "drain"]
    assert broker._interrupted()
