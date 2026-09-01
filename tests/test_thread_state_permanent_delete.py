from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from runtime.memory.threads.event_log import EventLog, thread_log_path
from runtime.memory.threads.store import (
    ThreadPermanentlyDeletedError,
    ThreadStateStore,
)
from runtime.sensing.gateway._realtime_cerebrum_thread import _ensure_thread
from runtime.sensing.gateway.anthropic_compat.router import _start_turn_with_claim


def _durable_store(root: Path, mode: str) -> ThreadStateStore:
    if mode == "path":
        return ThreadStateStore(path=root / "threads.jsonl")
    return ThreadStateStore(per_agent_base=root)


@pytest.mark.parametrize("mode", ["path", "per_agent"])
def test_permanent_delete_is_durable_and_fences_fresh_and_stale_stores(
    tmp_path: Path,
    mode: str,
) -> None:
    root = tmp_path / mode
    deleting = _durable_store(root, mode)
    deleting.ensure_thread(
        "thread-gone",
        values={
            "title": "Gone",
            "messages": [{"type": "human", "content": "durable deletion needle"}],
        },
    )
    stale = _durable_store(root, mode)

    assert deleting.permanently_delete("thread-gone", "delete-token") is True
    assert deleting.permanently_delete("thread-gone", "delete-token") is True
    with pytest.raises(ThreadPermanentlyDeletedError):
        deleting.permanently_delete("thread-gone", "different-token")

    lease = deleting.thread_delete_lease("thread-gone")
    assert lease is not None
    assert lease.finalized is True
    assert lease.token == "delete-token"
    assert deleting._index is not None
    assert deleting._index.get("thread-gone") is None
    assert deleting.search_threads("needle") == []
    assert deleting.search() == []

    fresh = _durable_store(root, mode)
    assert fresh._index is not None
    assert fresh._index.get("thread-gone") is None
    for store in (deleting, stale, fresh):
        assert store.is_permanently_deleted("thread-gone") is True
        with pytest.raises(ThreadPermanentlyDeletedError):
            store.get("thread-gone")
        with pytest.raises(ThreadPermanentlyDeletedError):
            store.ensure_thread("thread-gone")
        with pytest.raises(ThreadPermanentlyDeletedError):
            store.update_state("thread-gone", values={"title": "resurrected"})
        with pytest.raises(ThreadPermanentlyDeletedError):
            store.fork_thread("thread-gone")


def test_in_progress_delete_fences_normal_reads_but_exact_token_can_resume(
    tmp_path: Path,
) -> None:
    first = ThreadStateStore(path=tmp_path / "threads.jsonl")
    original = first.ensure_thread(
        "thread-retry",
        metadata={"owner_actor_id": "alice", "workspace_path": "/managed/thread-retry"},
    )
    lease = first.begin_permanent_delete(
        "thread-retry",
        expected=original,
        metadata={"deletion_state": "deleting"},
    )

    reopened = ThreadStateStore(path=tmp_path / "threads.jsonl")
    with pytest.raises(ThreadPermanentlyDeletedError):
        reopened.get("thread-retry")
    snapshot = reopened.thread_for_permanent_delete("thread-retry", lease.token)
    assert snapshot is not None
    assert snapshot["status"] == "deleting"
    assert snapshot["metadata"]["owner_actor_id"] == "alice"
    assert snapshot["metadata"]["deletion_state"] == "deleting"
    with pytest.raises(ThreadPermanentlyDeletedError):
        reopened.thread_for_permanent_delete("thread-retry", "wrong-token")

    assert reopened.finalize_permanent_delete("thread-retry", lease.token) is True
    assert reopened.thread_for_permanent_delete("thread-retry", lease.token) is None


def test_log_only_thread_can_receive_a_durable_tombstone(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    store = ThreadStateStore(path=path)

    assert store.permanently_delete("log-only-thread", "log-delete-token") is True
    reopened = ThreadStateStore(path=path)

    assert reopened.thread_delete_lease("log-only-thread") is not None
    with pytest.raises(ThreadPermanentlyDeletedError):
        reopened.get("log-only-thread")
    with pytest.raises(ThreadPermanentlyDeletedError):
        reopened.ensure_thread("log-only-thread")


def test_pure_memory_tombstone_is_local_to_the_store() -> None:
    deleted = ThreadStateStore()
    deleted.ensure_thread("memory-thread")
    assert deleted.permanently_delete("memory-thread", "memory-token") is True

    with pytest.raises(ThreadPermanentlyDeletedError):
        deleted.ensure_thread("memory-thread")

    independent = ThreadStateStore()
    assert independent.ensure_thread("memory-thread")["thread_id"] == "memory-thread"


class _Emitter:
    actor_id = None

    def __init__(self) -> None:
        self.notifications: list[tuple[Any, dict[str, Any]]] = []

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))

    def is_turn_interrupted(self, _turn_id: str) -> bool:
        return False

    def get_interrupt_reason(self, _turn_id: str) -> None:
        return None

    def register_turn(self, _turn_id: str) -> None:
        return None

    def unregister_turn(self, _turn_id: str) -> None:
        return None


class _RealtimeRuntime:
    def __init__(
        self,
        logs_root: Path,
        *,
        thread_store: Any = None,
        project_store: Any = None,
    ) -> None:
        self._logs_root = logs_root
        self._thread_store = thread_store
        self._project_store = project_store
        self._lock = asyncio.Lock()
        self._known_threads: set[str] = set()
        self.log_constructions = 0

    def _log_for(self, thread_id: str) -> EventLog:
        self.log_constructions += 1
        return EventLog(thread_log_path(self._logs_root, thread_id))


@pytest.mark.asyncio
async def test_realtime_new_thread_tombstone_does_not_create_event_log(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path / "state")
    store.permanently_delete("deleted-new", "new-token")
    runtime = _RealtimeRuntime(tmp_path / "logs", thread_store=store)
    emitter = _Emitter()

    with pytest.raises(ThreadPermanentlyDeletedError):
        await _ensure_thread(runtime, "deleted-new", emitter)  # type: ignore[arg-type]

    assert runtime.log_constructions == 0
    assert emitter.notifications == []
    assert not thread_log_path(runtime._logs_root, "deleted-new").exists()


@pytest.mark.asyncio
async def test_realtime_known_thread_reprobes_before_returning_event_log(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path / "state")
    store.ensure_thread("deleted-known")
    runtime = _RealtimeRuntime(tmp_path / "logs", thread_store=store)
    emitter = _Emitter()

    await _ensure_thread(runtime, "deleted-known", emitter)  # type: ignore[arg-type]
    log_path = thread_log_path(runtime._logs_root, "deleted-known")
    before = log_path.read_bytes()
    store.permanently_delete("deleted-known", "known-token")

    with pytest.raises(ThreadPermanentlyDeletedError):
        await _ensure_thread(runtime, "deleted-known", emitter)  # type: ignore[arg-type]

    assert runtime.log_constructions == 1
    assert log_path.read_bytes() == before
    assert len(emitter.notifications) == 1


@pytest.mark.asyncio
async def test_realtime_probe_failure_is_fail_closed_without_log_write(tmp_path: Path) -> None:
    class BrokenStore:
        def assert_not_permanently_deleted(self, _thread_id: str) -> None:
            raise OSError("delete tombstone storage unavailable")

    runtime = _RealtimeRuntime(tmp_path / "logs", thread_store=BrokenStore())
    emitter = _Emitter()

    with pytest.raises(OSError, match="tombstone storage unavailable"):
        await _ensure_thread(runtime, "probe-failed", emitter)  # type: ignore[arg-type]

    assert runtime.log_constructions == 0
    assert emitter.notifications == []
    assert not thread_log_path(runtime._logs_root, "probe-failed").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("finalized", [False, True])
async def test_realtime_rejects_project_delete_claim_and_tombstone(
    tmp_path: Path,
    finalized: bool,
) -> None:
    from runtime.projectos.store import ProjectStore

    projects = ProjectStore(base_dir=tmp_path / "projectos")
    lease = projects.begin_thread_delete("project-deleted")
    if finalized:
        projects.finalize_thread_delete("project-deleted", lease.token)
    runtime = _RealtimeRuntime(tmp_path / "logs", project_store=projects)

    with pytest.raises(ThreadPermanentlyDeletedError):
        await _ensure_thread(runtime, "project-deleted", _Emitter())  # type: ignore[arg-type]

    assert runtime.log_constructions == 0
    assert not thread_log_path(runtime._logs_root, "project-deleted").exists()


@pytest.mark.asyncio
async def test_anthropic_active_turn_holds_claim_until_runtime_finishes(tmp_path: Path) -> None:
    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimConflict,
        acquire_thread_turn_claim,
    )

    class BlockingRuntime:
        def __init__(self) -> None:
            self._logs_root = tmp_path / "logs"
            self.entered = asyncio.Event()
            self.finish = asyncio.Event()

        async def start_turn(self, _params: dict[str, Any], _emitter: Any) -> str:
            self.entered.set()
            await self.finish.wait()
            return "done"

    runtime = BlockingRuntime()
    task = asyncio.create_task(
        _start_turn_with_claim(runtime, {"threadId": "anthropic-active"}, object())
    )
    await runtime.entered.wait()

    with pytest.raises(ThreadTurnClaimConflict):
        acquire_thread_turn_claim(runtime._logs_root, "anthropic-active")

    runtime.finish.set()
    assert await task == "done"
    with acquire_thread_turn_claim(runtime._logs_root, "anthropic-active"):
        pass


@pytest.mark.asyncio
async def test_anthropic_delete_first_claim_prevents_runtime_entry(tmp_path: Path) -> None:
    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimConflict,
        acquire_thread_turn_claim,
    )

    class ObservedRuntime:
        def __init__(self) -> None:
            self._logs_root = tmp_path / "logs"
            self.calls = 0

        async def start_turn(self, _params: dict[str, Any], _emitter: Any) -> None:
            self.calls += 1

    runtime = ObservedRuntime()
    delete_claim = acquire_thread_turn_claim(runtime._logs_root, "anthropic-delete-first")
    try:
        with pytest.raises(ThreadTurnClaimConflict):
            await _start_turn_with_claim(
                runtime,
                {"threadId": "anthropic-delete-first"},
                object(),
            )
        assert runtime.calls == 0
    finally:
        delete_claim.release()


@pytest.mark.parametrize("require_auth", [False, True])
def test_gateway_echo_tombstone_blocks_runtime_but_missing_thread_is_allowed(
    tmp_path: Path,
    require_auth: bool,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.protocol import Turn
    from runtime.safety.auth import Identity, IdentityStore
    from runtime.sensing.gateway.realtime_echo import EchoRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.gateway.thread_access import ThreadAccessResolver

    class CountingEchoRuntime(EchoRuntime):
        def __init__(self, logs_root: Path) -> None:
            super().__init__(logs_root=logs_root)
            self.calls = 0

        async def start_turn(self, params: dict[str, Any], emitter: Any) -> Turn:
            self.calls += 1
            return await super().start_turn(params, emitter)

    logs_root = tmp_path / "logs"
    store = ThreadStateStore(per_agent_base=tmp_path / "state")
    store.ensure_thread(
        "gateway-deleted",
        metadata=({"owner_actor_id": "alice", "tenant_id": "tenant-a"} if require_auth else {}),
    )
    store.permanently_delete("gateway-deleted", "gateway-delete-token")
    identities = IdentityStore()
    if require_auth:
        identities.add(
            Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
            api_key_plaintext="sk-alice",
        )
    resolver = ThreadAccessResolver(
        thread_store=store,
        identity_store=identities,
        allow_anonymous_ownerless=not require_auth,
    )
    runtime = CountingEchoRuntime(logs_root)
    runtime._thread_access_resolver = resolver
    gateway = RealtimeGateway(
        runtime=runtime,
        thread_access_resolver=resolver,
        identity_store=identities,
        require_auth=require_auth,
        allow_client_approval_bypass=True,
    )
    app = FastAPI()
    app.include_router(gateway.router)

    endpoint = "/api/realtime"
    headers = {"Authorization": "Bearer sk-alice"} if require_auth else {}
    with TestClient(app) as client:
        with client.websocket_connect(endpoint, headers=headers) as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "turn/start",
                    "params": {
                        "threadId": "gateway-deleted",
                        "input": [{"type": "text", "text": "must stay deleted"}],
                        "approvalPolicy": "never",
                    },
                }
            )
            while True:
                deleted_response = ws.receive_json()
                if deleted_response.get("id") == 1:
                    break
        assert deleted_response.get("error") is not None
        assert runtime.calls == 0
        assert not thread_log_path(logs_root, "gateway-deleted").exists()

        with client.websocket_connect(endpoint, headers=headers) as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "turn/start",
                    "params": {
                        "threadId": "gateway-truly-missing",
                        "input": [{"type": "text", "text": "new thread"}],
                        "approvalPolicy": "never",
                    },
                }
            )
            while True:
                missing_response = ws.receive_json()
                if missing_response.get("id") == 2:
                    break

    assert missing_response.get("result", {}).get("turn", {}).get("status") == "completed"
    assert runtime.calls == 1
    assert thread_log_path(logs_root, "gateway-truly-missing").is_file()


@pytest.mark.asyncio
async def test_anthropic_tombstone_probe_blocks_custom_runtime_and_log(tmp_path: Path) -> None:
    class CustomRuntime:
        def __init__(self) -> None:
            self._logs_root = tmp_path / "logs"
            self._thread_store = ThreadStateStore(per_agent_base=tmp_path / "state")
            self.calls = 0

        async def start_turn(self, params: dict[str, Any], _emitter: Any) -> str:
            self.calls += 1
            EventLog(thread_log_path(self._logs_root, params["threadId"])).thread_started(
                params["threadId"]
            )
            return "done"

    runtime = CustomRuntime()
    runtime._thread_store.permanently_delete("anthropic-tombstone", "token")

    with pytest.raises(ThreadPermanentlyDeletedError):
        await _start_turn_with_claim(
            runtime,
            {"threadId": "anthropic-tombstone"},
            _Emitter(),
        )

    assert runtime.calls == 0
    assert not thread_log_path(runtime._logs_root, "anthropic-tombstone").exists()
    assert (
        await _start_turn_with_claim(
            runtime,
            {"threadId": "anthropic-missing"},
            _Emitter(),
        )
        == "done"
    )
    assert runtime.calls == 1


def test_turn_claim_retained_reference_survives_idempotent_owner_release(
    tmp_path: Path,
) -> None:
    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimConflict,
        acquire_thread_turn_claim,
    )

    claim = acquire_thread_turn_claim(tmp_path / "logs", "retained-background-write")
    retained = claim.retain_if_live()
    assert retained is not None

    claim.release()
    claim.release()
    assert claim.retain_if_live() is None
    with pytest.raises(ThreadTurnClaimConflict):
        acquire_thread_turn_claim(tmp_path / "logs", "retained-background-write")

    retained.release()
    retained.release()
    replacement = acquire_thread_turn_claim(tmp_path / "logs", "retained-background-write")
    replacement.release()


@pytest.mark.asyncio
async def test_background_watcher_write_first_makes_delete_return_409(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.process.thread_turn_claim import acquire_thread_turn_claim
    from runtime.protocol import CommandExecutionItem, Turn
    from runtime.sensing.gateway._realtime_claim_aware_emitter import _ClaimAwareEmitter
    from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState
    from runtime.sensing.gateway.thread_state_router import create_thread_state_router

    thread_id = "background-write-first"
    logs_root = tmp_path / "logs"
    store = ThreadStateStore(path=tmp_path / "threads.jsonl")
    store.ensure_thread(thread_id)
    runtime = _RealtimeRuntime(logs_root, thread_store=store)
    log = EventLog(thread_log_path(logs_root, thread_id))
    turn = Turn(threadId=thread_id)
    item = CommandExecutionItem(command="long background command")
    log.thread_started(thread_id)
    log.turn_started(thread_id, turn)
    log.item_started(thread_id, turn.id, item)

    claim = acquire_thread_turn_claim(logs_root, thread_id)
    emitter = _ClaimAwareEmitter(_Emitter(), claim, log=log, runtime=runtime)
    state = _ReactBridgeState()
    state.tools["call-bg"] = item
    write_entered = asyncio.Event()
    release_write = asyncio.Event()
    append_calls = 0
    original_append = state.append_tool_output

    async def _blocking_append(*args: Any, **kwargs: Any) -> None:
        nonlocal append_calls
        append_calls += 1
        if append_calls > 1:
            write_entered.set()
            await release_write.wait()
        await original_append(*args, **kwargs)

    monkeypatch.setattr(state, "append_tool_output", _blocking_append)
    monkeypatch.setattr(
        "runtime.execution.suckers.write_skills._read_background_output",
        lambda **_kwargs: {"status": "running", "stdout": "late output", "stderr": ""},
    )

    app = FastAPI()
    app.include_router(create_thread_state_router(store=store, logs_root=logs_root))
    with TestClient(app) as client:
        await state.track_background_tool(
            turn,
            log,
            emitter,
            {
                "tool_call_id": "call-bg",
                "task_id": "task-bg",
                "tool_name": "background_exec",
                "snapshot": {"status": "running"},
            },
        )
        watcher = state.background_tasks[-1]
        try:
            await asyncio.wait_for(write_entered.wait(), timeout=2.0)
            # The watcher borrowed the still-live foreground claim. Releasing
            # foreground ownership must not open a deletion race mid-append.
            claim.release()
            refused = client.delete(f"/api/threads/{thread_id}")
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "THREAD_TURN_ACTIVE"
            assert store.get(thread_id) is not None
        finally:
            release_write.set()
            watcher.cancel()
            with pytest.raises(asyncio.CancelledError):
                await watcher
            claim.release()

        assert client.delete(f"/api/threads/{thread_id}").status_code == 204


@pytest.mark.asyncio
async def test_delete_first_stops_background_watcher_without_late_archive_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.process.thread_turn_claim import acquire_thread_turn_claim
    from runtime.protocol import CommandExecutionItem, Turn
    from runtime.sensing.gateway._realtime_claim_aware_emitter import _ClaimAwareEmitter
    from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState
    from runtime.sensing.gateway.thread_state_router import create_thread_state_router

    thread_id = "background-delete-first"
    logs_root = tmp_path / "logs"
    store = ThreadStateStore(path=tmp_path / "threads.jsonl")
    store.ensure_thread(thread_id)
    runtime = _RealtimeRuntime(logs_root, thread_store=store)
    log = EventLog(thread_log_path(logs_root, thread_id))
    turn = Turn(threadId=thread_id)
    item = CommandExecutionItem(command="late background command")
    log.thread_started(thread_id)
    log.turn_started(thread_id, turn)
    log.item_started(thread_id, turn.id, item)

    claim = acquire_thread_turn_claim(logs_root, thread_id)
    emitter = _ClaimAwareEmitter(_Emitter(), claim, log=log, runtime=runtime)
    state = _ReactBridgeState()
    state.tools["call-late"] = item
    killed: list[str] = []
    monkeypatch.setattr(
        "runtime.execution.suckers.write_skills._read_background_output",
        lambda **_kwargs: {"status": "completed", "stdout": "must not append", "stderr": ""},
    )
    monkeypatch.setattr(
        "runtime.execution.suckers.write_skills._kill_background_exec",
        lambda **kwargs: killed.append(str(kwargs.get("task_id"))),
    )

    app = FastAPI()
    app.include_router(create_thread_state_router(store=store, logs_root=logs_root))
    with TestClient(app) as client:
        await state.track_background_tool(
            turn,
            log,
            emitter,
            {
                "tool_call_id": "call-late",
                "task_id": "task-late",
                "tool_name": "background_exec",
                "snapshot": {"status": "running"},
            },
        )
        watcher = state.background_tasks[-1]
        claim.release()
        assert client.delete(f"/api/threads/{thread_id}").status_code == 204
        archived = log.path.read_bytes()

        await asyncio.wait_for(watcher, timeout=2.0)

    assert killed == ["task-late"]
    assert log.path.read_bytes() == archived


def _seed_completed_turns(log: EventLog, thread_id: str, *, count: int = 3) -> None:
    from runtime.protocol import AgentMessageItem, ItemStatus, Turn, TurnStatus

    log.thread_started(thread_id)
    for index in range(count):
        turn = Turn(threadId=thread_id)
        item = AgentMessageItem(text=f"completed answer {index}")
        item.status = ItemStatus.COMPLETED
        log.turn_started(thread_id, turn)
        log.item_started(thread_id, turn.id, item)
        log.item_completed(thread_id, turn.id, item)
        log.turn_completed(thread_id, turn.id, TurnStatus.COMPLETED, error=None)


@pytest.mark.asyncio
async def test_delete_first_fences_compact_and_archive_without_log_growth(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.memory.threads.compaction import CompactionPolicy
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_echo import EchoRuntime
    from runtime.sensing.gateway.thread_state_router import create_thread_state_router

    thread_id = "maintenance-delete-first"
    logs_root = tmp_path / "logs"
    store = ThreadStateStore(path=tmp_path / "threads.jsonl")
    store.ensure_thread(thread_id)
    log = EventLog(thread_log_path(logs_root, thread_id))
    _seed_completed_turns(log, thread_id)
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
        thread_store=store,
        compaction_policy=CompactionPolicy(trigger_at=2, keep_recent=1),
    )
    echo = EchoRuntime(logs_root=logs_root)
    echo._thread_store = store

    app = FastAPI()
    app.include_router(create_thread_state_router(store=store, logs_root=logs_root))
    with TestClient(app) as client:
        assert client.delete(f"/api/threads/{thread_id}").status_code == 204
        archived = log.path.read_bytes()

        with pytest.raises(ThreadPermanentlyDeletedError):
            await runtime.handle_request(
                "thread/compact",
                {"threadId": thread_id},
                _Emitter(),
            )
        with pytest.raises(ThreadPermanentlyDeletedError):
            await runtime.handle_request(
                "thread/archive",
                {"threadId": thread_id},
                _Emitter(),
            )
        with pytest.raises(ThreadPermanentlyDeletedError):
            await echo.handle_request(
                "thread/archive",
                {"threadId": thread_id},
                _Emitter(),
            )

    assert log.path.read_bytes() == archived


@pytest.mark.asyncio
async def test_compact_first_holds_claim_so_delete_returns_409_then_succeeds(
    tmp_path: Path,
) -> None:
    import threading

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.memory.threads.compaction import CompactionPolicy
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.thread_state_router import create_thread_state_router

    thread_id = "maintenance-compact-first"
    logs_root = tmp_path / "logs"
    store = ThreadStateStore(path=tmp_path / "threads.jsonl")
    store.ensure_thread(thread_id)
    log = EventLog(thread_log_path(logs_root, thread_id))
    _seed_completed_turns(log, thread_id)
    compact_entered = threading.Event()
    release_compact = threading.Event()

    def _blocking_summary(_turns: Any) -> str:
        compact_entered.set()
        if not release_compact.wait(5.0):
            raise TimeoutError("compaction release was not signalled")
        return "durable compacted summary"

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
        thread_store=store,
        compaction_policy=CompactionPolicy(
            trigger_at=2,
            keep_recent=1,
            custom_summariser=_blocking_summary,
        ),
    )
    app = FastAPI()
    app.include_router(create_thread_state_router(store=store, logs_root=logs_root))

    compacting = asyncio.create_task(
        runtime.handle_request(
            "thread/compact",
            {"threadId": thread_id},
            _Emitter(),
        )
    )
    try:
        assert await asyncio.to_thread(compact_entered.wait, 2.0)
        with TestClient(app) as client:
            refused = client.delete(f"/api/threads/{thread_id}")
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "THREAD_TURN_ACTIVE"
            release_compact.set()
            result = await asyncio.wait_for(compacting, timeout=2.0)
            assert result["compacted"] is True
            assert client.delete(f"/api/threads/{thread_id}").status_code == 204
    finally:
        release_compact.set()
        if not compacting.done():
            await compacting

    with pytest.raises(ThreadPermanentlyDeletedError):
        store.get(thread_id)

