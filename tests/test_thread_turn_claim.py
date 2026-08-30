"""Cross-process acceptance tests for the per-thread turn authority."""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import time
from pathlib import Path
from typing import Any

import pytest

from runtime.platform.process import thread_turn_claim as claim_module
from runtime.platform.process.thread_turn_claim import (
    ThreadTurnClaimConflict,
    ThreadTurnClaimUnavailable,
    acquire_thread_turn_claim,
)
from runtime.protocol import JsonRpcErrorCode, Turn, TurnStatus
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway, _RpcError


class _RecordingConnection:
    def __init__(self) -> None:
        self.actor_id: str | None = None
        self.tenant_id: str | None = None
        self.watched_threads: set[str] = set()
        self.last_resumed_thread_id: str | None = None
        self._closed = False
        self.notifications: list[tuple[Any, dict[str, Any]]] = []

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))

    async def request_approval(
        self,
        _method: Any,
        _params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        del timeout
        return {"action": "accept"}

    def is_turn_interrupted(self, _turn_id: str) -> bool:
        return False

    def get_interrupt_reason(self, _turn_id: str) -> str | None:
        return None

    def register_turn(self, _turn_id: str) -> None:
        return None

    def unregister_turn(self, _turn_id: str) -> None:
        return None


class _TerminalBlockingConnection(_RecordingConnection):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_snapshot_started = asyncio.Event()
        self.release_terminal_snapshot = asyncio.Event()

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        await super().notify(method, params)
        if getattr(method, "value", method) == "turn/completed":
            self.terminal_snapshot_started.set()
            await self.release_terminal_snapshot.wait()


class _ProbeRuntime:
    def __init__(self, logs_root: Path) -> None:
        self._logs_root = logs_root
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self.start_calls = 0

    async def start_turn(self, params: dict[str, Any], _emitter: Any) -> Turn:
        self.start_calls += 1
        return Turn(threadId=params["threadId"], status=TurnStatus.COMPLETED)


class _PersistenceBlockingRuntime:
    def __init__(self, logs_root: Path) -> None:
        self._logs_root = logs_root
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self.runtime_entered = asyncio.Event()
        self.release_terminal_persistence = asyncio.Event()
        self.terminal_persisted = False

    async def start_turn(self, params: dict[str, Any], _emitter: Any) -> Turn:
        self.runtime_entered.set()
        await self.release_terminal_persistence.wait()
        self.terminal_persisted = True
        return Turn(threadId=params["threadId"], status=TurnStatus.COMPLETED)


class _NoAuthorityRuntime:
    def __init__(self) -> None:
        self.start_calls = 0

    async def start_turn(self, params: dict[str, Any], _emitter: Any) -> Turn:
        self.start_calls += 1
        return Turn(threadId=params["threadId"], status=TurnStatus.COMPLETED)


class _ProcessBlockingRuntime:
    def __init__(
        self,
        logs_root: Path,
        entered: Any,
        release: Any,
        turn_ids: Any,
    ) -> None:
        self._logs_root = logs_root
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self._entered = entered
        self._release = release
        self._turn_ids = turn_ids

    async def start_turn(self, params: dict[str, Any], emitter: Any) -> Turn:
        turn = Turn(threadId=params["threadId"])
        emitter.register_turn(turn.id)
        self._turn_ids.put(turn.id)
        self._entered.set()
        try:
            released = await asyncio.to_thread(self._release.wait, 20.0)
            if not released:
                raise TimeoutError("test turn release was not signalled")
            turn.status = TurnStatus.COMPLETED
            return turn
        finally:
            emitter.unregister_turn(turn.id)


class _PendingReportStore:
    def __init__(self) -> None:
        self.pending_reads = 0

    def pending_thread_reports(self, _thread_id: str) -> list[object]:
        self.pending_reads += 1
        return [object()]


def _hold_claim_process(
    logs_root: str,
    thread_id: str,
    turn_id: str,
    ready: Any,
    release: Any,
) -> None:
    claim = acquire_thread_turn_claim(logs_root, thread_id)
    claim.bind_turn(turn_id)
    ready.set()
    try:
        release.wait(30.0)
    finally:
        claim.release()


def _write_stale_turn_and_hold_claim_process(
    logs_root: str,
    thread_id: str,
    ready: Any,
    release: Any,
) -> None:
    from runtime.memory.threads.event_log import EventLog, thread_log_path

    claim = acquire_thread_turn_claim(logs_root, thread_id)
    turn = Turn(threadId=thread_id)
    log = EventLog(thread_log_path(Path(logs_root), thread_id))
    log.thread_started(thread_id)
    log.turn_started(thread_id, turn)
    claim.bind_turn(turn.id)
    ready.set()
    try:
        release.wait(30.0)
    finally:
        claim.release()


def _run_blocking_gateway_process(
    logs_root: str,
    thread_id: str,
    entered: Any,
    release: Any,
    turn_ids: Any,
    outcomes: Any,
) -> None:
    async def _run() -> None:
        runtime = _ProcessBlockingRuntime(Path(logs_root), entered, release, turn_ids)
        gateway = RealtimeGateway(runtime=runtime)
        conn = _RecordingConnection()
        try:
            result = await gateway._invoke_turn_start(  # noqa: SLF001
                {"threadId": thread_id, "input": []},
                conn,
            )
        except BaseException as exc:  # pragma: no cover - reported to parent process
            outcomes.put(("error", repr(exc)))
            raise
        outcomes.put(("ok", result["turn"]["status"]))

    asyncio.run(_run())


def _spawn_context() -> multiprocessing.context.SpawnContext:
    return multiprocessing.get_context("spawn")


def _join_or_terminate(process: multiprocessing.Process, *, timeout: float = 15.0) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5.0)


def test_claim_path_is_hash_only_and_conflict_turn_id_is_diagnostic(tmp_path: Path) -> None:
    thread_id = "tenant/private-thread-name"
    claim = acquire_thread_turn_claim(tmp_path, thread_id)
    try:
        assert claim.path.parent == tmp_path / ".thread-turn-locks"
        assert claim.path.name == f"{hashlib.sha256(thread_id.encode()).hexdigest()}.lock"
        assert thread_id not in str(claim.path)
        assert claim.bind_turn("turn-owner")

        with pytest.raises(ThreadTurnClaimConflict) as caught:
            acquire_thread_turn_claim(tmp_path, thread_id)
        assert caught.value.active_turn_id == "turn-owner"
        assert caught.value.claim_epoch == claim.claim_epoch
        assert caught.value.claim_epoch
    finally:
        claim.release()

    replacement = acquire_thread_turn_claim(tmp_path, thread_id)
    replacement.release()


def test_claim_never_degrades_when_authoritative_lock_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unavailable(_fd: int) -> None:
        raise ThreadTurnClaimUnavailable("no authoritative lock")

    monkeypatch.setattr(claim_module, "_try_lock", _unavailable)
    with pytest.raises(ThreadTurnClaimUnavailable, match="no authoritative lock"):
        acquire_thread_turn_claim(tmp_path, "fail-closed")


def test_gateway_without_authoritative_root_fails_closed() -> None:
    runtime = _NoAuthorityRuntime()
    gateway = RealtimeGateway(runtime=runtime)
    conn = _RecordingConnection()

    with pytest.raises(_RpcError) as caught:
        asyncio.run(
            gateway._invoke_turn_start(  # noqa: SLF001
                {"threadId": "no-authority", "input": []},
                conn,
            )
        )

    assert caught.value.code == JsonRpcErrorCode.INTERNAL_ERROR
    assert caught.value.data == {
        "reason": "thread_turn_claim_unavailable",
        "threadId": "no-authority",
        "retryable": False,
    }
    assert runtime.start_calls == 0
    assert conn.watched_threads == set()
    assert conn.notifications == []


def test_two_gateway_processes_allow_only_one_model_loop(tmp_path: Path) -> None:
    context = _spawn_context()
    entered = context.Event()
    release = context.Event()
    turn_ids = context.Queue()
    outcomes = context.Queue()
    logs_root = tmp_path / "threads"
    thread_id = "shared-thread"
    owner = context.Process(
        target=_run_blocking_gateway_process,
        args=(str(logs_root), thread_id, entered, release, turn_ids, outcomes),
    )
    owner.start()
    try:
        assert entered.wait(15.0), "owner gateway never entered its model loop"
        owner_turn_id = turn_ids.get(timeout=5.0)

        contender_runtime = _ProbeRuntime(logs_root)
        contender_gateway = RealtimeGateway(runtime=contender_runtime)
        contender_conn = _RecordingConnection()
        with pytest.raises(_RpcError) as caught:
            asyncio.run(
                contender_gateway._invoke_turn_start(  # noqa: SLF001
                    {"threadId": thread_id, "input": []},
                    contender_conn,
                )
            )

        assert caught.value.code == JsonRpcErrorCode.SERVER_BUSY
        assert caught.value.data == {
            "reason": "turn_already_active",
            "threadId": thread_id,
            "retryable": True,
            "activeTurnId": owner_turn_id,
        }
        assert contender_runtime.start_calls == 0
        assert contender_conn.watched_threads == set()
        assert contender_conn.notifications == []
    finally:
        release.set()
        _join_or_terminate(owner)

    assert owner.exitcode == 0
    assert outcomes.get(timeout=5.0) == ("ok", "completed")


def test_conflict_does_not_create_thread_or_turn_events(tmp_path: Path) -> None:
    from runtime.memory.threads.event_log import thread_log_path
    from runtime.sensing.gateway.realtime_echo import EchoRuntime

    logs_root = tmp_path / "threads"
    thread_id = "side-effect-free-conflict"
    owner = acquire_thread_turn_claim(logs_root, thread_id)
    owner.bind_turn("turn-existing")
    try:
        gateway = RealtimeGateway(runtime=EchoRuntime(logs_root=logs_root))
        conn = _RecordingConnection()
        with pytest.raises(_RpcError) as caught:
            asyncio.run(
                gateway._invoke_turn_start(  # noqa: SLF001
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": "must not persist"}],
                    },
                    conn,
                )
            )
    finally:
        owner.release()

    assert caught.value.data["reason"] == "turn_already_active"
    assert not thread_log_path(logs_root, thread_id).exists()
    assert conn.watched_threads == set()
    assert conn.notifications == []


def test_claim_ends_at_durable_terminal_boundary_before_blocked_live_fanout(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        logs_root = tmp_path / "threads"
        thread_id = "terminal-snapshot-boundary"
        runtime = _PersistenceBlockingRuntime(logs_root)
        gateway = RealtimeGateway(runtime=runtime)
        conn = _TerminalBlockingConnection()
        turn_task = asyncio.create_task(
            gateway._invoke_turn_start(  # noqa: SLF001
                {"threadId": thread_id, "input": []},
                conn,
            )
        )
        try:
            await asyncio.wait_for(runtime.runtime_entered.wait(), timeout=5.0)
            with pytest.raises(ThreadTurnClaimConflict):
                acquire_thread_turn_claim(logs_root, thread_id)

            runtime.release_terminal_persistence.set()
            await asyncio.wait_for(conn.terminal_snapshot_started.wait(), timeout=5.0)
            assert runtime.terminal_persisted is True
            assert not turn_task.done()
            assert gateway._turn_locks.active_keys() == 0  # noqa: SLF001

            replacement = acquire_thread_turn_claim(logs_root, thread_id)
            replacement.release()

            conn.release_terminal_snapshot.set()
            result = await asyncio.wait_for(turn_task, timeout=5.0)
            assert result["turn"]["status"] == "completed"
        finally:
            runtime.release_terminal_persistence.set()
            conn.release_terminal_snapshot.set()
            if not turn_task.done():
                await turn_task
            if thread_id in conn.watched_threads:
                gateway._unwatch_thread(thread_id)  # noqa: SLF001

    asyncio.run(_run())


def test_process_crash_releases_claim_without_ttl(tmp_path: Path) -> None:
    context = _spawn_context()
    ready = context.Event()
    release = context.Event()
    logs_root = tmp_path / "threads"
    thread_id = "crash-recovery"
    owner = context.Process(
        target=_hold_claim_process,
        args=(str(logs_root), thread_id, "turn-before-crash", ready, release),
    )
    owner.start()
    try:
        assert ready.wait(15.0), "claim owner never acquired the lock"

        with pytest.raises(ThreadTurnClaimConflict) as caught:
            acquire_thread_turn_claim(logs_root, thread_id)
        assert caught.value.active_turn_id == "turn-before-crash"

        owner.terminate()
        owner.join(10.0)
        assert not owner.is_alive()

        deadline = time.monotonic() + 5.0
        while True:
            try:
                replacement = acquire_thread_turn_claim(logs_root, thread_id)
                break
            except ThreadTurnClaimConflict:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        replacement.release()
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(5.0)


def test_crashed_turn_is_closed_on_resume_and_thread_accepts_a_new_turn(
    tmp_path: Path,
) -> None:
    from runtime.memory.threads.event_log import EventLog, thread_log_path
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_echo import EchoRuntime

    context = _spawn_context()
    ready = context.Event()
    release = context.Event()
    logs_root = tmp_path / "threads"
    thread_id = "crashed-stale-turn"
    owner = context.Process(
        target=_write_stale_turn_and_hold_claim_process,
        args=(str(logs_root), thread_id, ready, release),
    )
    owner.start()
    try:
        assert ready.wait(15.0), "crashing turn never reached its in-progress boundary"
        owner.terminate()
        owner.join(10.0)
        assert not owner.is_alive()
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(5.0)

    recovery_runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
    )
    recovered = asyncio.run(
        recovery_runtime.handle_request(
            "thread/resume",
            {"threadId": thread_id},
            _RecordingConnection(),
        )
    )
    assert recovered["turns"][0]["status"] == "failed"
    assert recovered["turns"][0]["error"]["code"] == "stale_in_progress_turn"

    gateway = RealtimeGateway(
        runtime=EchoRuntime(logs_root=logs_root),
        allow_client_approval_bypass=True,
    )
    conn = _RecordingConnection()
    try:
        result = asyncio.run(
            gateway._invoke_turn_start(  # noqa: SLF001
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "continue after recovery"}],
                    "approvalPolicy": "never",
                },
                conn,
            )
        )
    finally:
        if thread_id in conn.watched_threads:
            gateway._unwatch_thread(thread_id)  # noqa: SLF001

    assert result["turn"]["status"] == "completed"
    turns = EventLog(thread_log_path(logs_root, thread_id)).replay()
    assert [turn.status for turn in turns] == [TurnStatus.FAILED, TurnStatus.COMPLETED]


def test_auto_wake_releases_claim_before_blocked_live_fanout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.subagents import sessions

    async def _run() -> None:
        logs_root = tmp_path / "threads"
        thread_id = "auto-wake-terminal-fanout"
        store = _PendingReportStore()
        monkeypatch.setattr(sessions, "get_subagent_session_store", lambda: store)

        runtime = _ProbeRuntime(logs_root)
        gateway = RealtimeGateway(runtime=runtime)
        conn = _TerminalBlockingConnection()
        conn.watched_threads.add(thread_id)
        gateway._connections.add(conn)  # noqa: SLF001
        task = asyncio.create_task(gateway._maybe_auto_turn(thread_id))  # noqa: SLF001
        try:
            await asyncio.wait_for(conn.terminal_snapshot_started.wait(), timeout=5.0)
            assert not task.done()
            assert gateway._turn_locks.active_keys() == 0  # noqa: SLF001

            replacement = acquire_thread_turn_claim(logs_root, thread_id)
            replacement.release()

            conn.release_terminal_snapshot.set()
            await asyncio.wait_for(task, timeout=5.0)
            assert runtime.start_calls == 1
        finally:
            conn.release_terminal_snapshot.set()
            if not task.done():
                await task

    asyncio.run(_run())


def test_auto_wake_conflicts_with_claim_held_by_another_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.subagents import sessions

    context = _spawn_context()
    ready = context.Event()
    release = context.Event()
    logs_root = tmp_path / "threads"
    thread_id = "user-vs-auto-wake"
    owner = context.Process(
        target=_hold_claim_process,
        args=(str(logs_root), thread_id, "turn-user", ready, release),
    )
    owner.start()
    try:
        assert ready.wait(15.0), "user-turn claim owner never became ready"
        store = _PendingReportStore()
        monkeypatch.setattr(sessions, "get_subagent_session_store", lambda: store)

        runtime = _ProbeRuntime(logs_root)
        gateway = RealtimeGateway(runtime=runtime)
        conn = _RecordingConnection()
        conn.watched_threads.add(thread_id)
        gateway._connections.add(conn)  # noqa: SLF001

        asyncio.run(gateway._maybe_auto_turn(thread_id))  # noqa: SLF001

        assert store.pending_reads == 1
        assert runtime.start_calls == 0
        assert conn.notifications == []
    finally:
        release.set()
        _join_or_terminate(owner)

    assert owner.exitcode == 0

