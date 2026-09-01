"""Cross-worker interrupt authority and durable control regression tests."""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.memory.threads.event_log import EventLog, thread_log_path
from runtime.memory.threads.store import ThreadStateStore
from runtime.platform.process.thread_turn_claim import acquire_thread_turn_claim
from runtime.protocol import JsonRpcErrorCode, Turn, TurnParams, TurnStatus
from runtime.sensing.gateway._realtime_gateway_connection import RpcConnection
from runtime.sensing.gateway.realtime_gateway import (
    RealtimeGateway,
    _ClaimAwareEmitter,
    _RpcError,
)
from runtime.sensing.gateway.realtime_interrupt_control import (
    InterruptAuthorityUnavailable,
    persist_interrupt_request,
    tail_contains_interrupt,
)


def _owned_params(thread_id: str, *, actor: str = "alice", tenant: str = "tenant-a") -> TurnParams:
    return TurnParams(
        threadId=thread_id,
        input=[
            {
                "type": "text",
                "text": "work",
                "metadata": {
                    "actor_id": actor,
                    "tenant_id": tenant,
                    "context": {"actor_id": actor, "tenant_id": tenant},
                },
            }
        ],
    )


class _ControlConnection:
    def __init__(self, actor: str | None, tenant: str | None) -> None:
        self.actor_id = actor
        self.tenant_id = tenant
        self.interrupted: set[str] = set()

    def request_interrupt(self, turn_id: str) -> None:
        self.interrupted.add(turn_id)


class _InterruptDelegate(_ControlConnection):
    def __init__(self) -> None:
        super().__init__(None, None)
        self.registered: set[str] = set()
        self.fired = asyncio.Event()

    async def notify(self, _method: Any, _params: dict[str, Any]) -> None:
        return None

    async def request_approval(
        self,
        _method: Any,
        _params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        del timeout
        return {"action": "decline"}

    def is_turn_interrupted(self, turn_id: str) -> bool:
        return turn_id in self.interrupted

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        return "interrupted" if self.is_turn_interrupted(turn_id) else None

    def register_turn(self, turn_id: str) -> None:
        self.registered.add(turn_id)

    def unregister_turn(self, turn_id: str) -> None:
        self.registered.discard(turn_id)

    def request_interrupt(self, turn_id: str) -> None:
        super().request_interrupt(turn_id)
        self.fired.set()


class _SilentWebSocket:
    async def send_text(self, _payload: str) -> None:
        return None


class _ApprovalRuntime:
    def __init__(self, logs_root: Path, entered: Any, outcomes: Any) -> None:
        self._logs_root = logs_root
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self._entered = entered
        self._outcomes = outcomes

    def _log_for(self, thread_id: str) -> EventLog:
        return EventLog(thread_log_path(self._logs_root, thread_id))

    async def start_turn(self, params: dict[str, Any], emitter: Any) -> Turn:
        validated = TurnParams.model_validate(params)
        thread_id = validated.thread_id
        log = self._log_for(thread_id)
        if not log.path.exists() or log.path.stat().st_size == 0:
            log.thread_started(thread_id)
        turn = Turn(threadId=thread_id, params=validated)
        emitter.register_turn(turn.id)
        try:
            log.turn_started(thread_id, turn)
            self._outcomes.put(("turn", turn.id))
            self._entered.set()
            decision = await emitter.request_approval(
                "item/commandExecution/requestApproval",
                {"threadId": thread_id, "turnId": turn.id, "timeoutMs": 30_000},
                timeout=30.0,
            )
            self._outcomes.put(("approval", decision))
            turn.status = (
                TurnStatus.INTERRUPTED
                if emitter.is_turn_interrupted(turn.id)
                else TurnStatus.COMPLETED
            )
            log.turn_completed(thread_id, turn.id, turn.status)
            return turn
        finally:
            emitter.unregister_turn(turn.id)


def _run_approval_owner(
    logs_root: str,
    thread_id: str,
    entered: Any,
    outcomes: Any,
) -> None:
    async def _run() -> None:
        runtime = _ApprovalRuntime(Path(logs_root), entered, outcomes)
        gateway = RealtimeGateway(runtime=runtime, require_auth=True)
        connection = RpcConnection(
            _SilentWebSocket(),  # type: ignore[arg-type]
            shared_interrupts=gateway._shared_interrupts,  # noqa: SLF001
            approval_timeout=30.0,
        )
        connection.actor_id = "alice"
        connection.tenant_id = "tenant-a"
        result = await gateway._invoke_turn_start(  # noqa: SLF001
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": "needs approval"}],
            },
            connection,
        )
        outcomes.put(("terminal", result["turn"]["status"]))

    asyncio.run(_run())


def _hold_owned_turn(
    logs_root: str,
    thread_id: str,
    ready: Any,
    release: Any,
) -> None:
    root = Path(logs_root)
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    turn = Turn(threadId=thread_id, params=_owned_params(thread_id))
    claim = acquire_thread_turn_claim(root, thread_id)
    if not claim.bind_turn(turn.id):  # pragma: no cover - explicit child failure
        raise RuntimeError("failed to bind test claim")
    log.turn_started(thread_id, turn)
    ready.set()
    try:
        release.wait(30.0)
    finally:
        claim.release()


def _spawn_context() -> multiprocessing.context.SpawnContext:
    return multiprocessing.get_context("spawn")


def _join_or_terminate(process: multiprocessing.Process, timeout: float = 10.0) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5.0)


def _interrupt_via_gateway(
    logs_root: Path,
    thread_id: str,
    turn_id: str,
    *,
    actor: str,
    tenant: str,
) -> dict[str, Any]:
    from runtime.sensing.gateway.realtime_echo import EchoRuntime

    gateway = RealtimeGateway(
        runtime=EchoRuntime(logs_root=logs_root),
        require_auth=True,
    )
    connection = _ControlConnection(actor, tenant)
    return asyncio.run(
        gateway._invoke(  # noqa: SLF001
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            connection,  # type: ignore[arg-type]
        )
    )


def test_interrupt_authorization_hides_alice_turn_from_bob(tmp_path: Path) -> None:
    root = tmp_path / "threads"
    thread_id = "alice-owned"
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    turn = Turn(threadId=thread_id, params=_owned_params(thread_id))
    claim = acquire_thread_turn_claim(root, thread_id)
    assert claim.bind_turn(turn.id)
    log.turn_started(thread_id, turn)
    try:
        from runtime.sensing.gateway.realtime_echo import EchoRuntime

        gateway = RealtimeGateway(
            runtime=EchoRuntime(logs_root=root),
            require_auth=True,
        )
        with pytest.raises(_RpcError) as injected_epoch:
            asyncio.run(
                gateway._invoke(  # noqa: SLF001
                    "turn/interrupt",
                    {
                        "threadId": thread_id,
                        "turnId": turn.id,
                        "claimEpoch": claim.claim_epoch,
                    },
                    _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
                )
            )
        assert injected_epoch.value.code == JsonRpcErrorCode.INVALID_PARAMS

        with pytest.raises(_RpcError) as caught:
            _interrupt_via_gateway(
                root,
                thread_id,
                turn.id,
                actor="bob",
                tenant="tenant-b",
            )
        assert caught.value.code == JsonRpcErrorCode.THREAD_NOT_FOUND
        assert not any(event.event == "turn_interrupt_requested" for event in log.iter_events())

        with pytest.raises(_RpcError) as wrong_tenant:
            _interrupt_via_gateway(
                root,
                thread_id,
                turn.id,
                actor="alice",
                tenant="tenant-b",
            )
        assert wrong_tenant.value.code == JsonRpcErrorCode.THREAD_NOT_FOUND
        assert not any(event.event == "turn_interrupt_requested" for event in log.iter_events())

        accepted = _interrupt_via_gateway(
            root,
            thread_id,
            turn.id,
            actor="alice",
            tenant="tenant-a",
        )
        assert accepted == {"turnId": turn.id, "interrupted": True}
    finally:
        claim.release()


def test_thread_store_principal_overrides_historical_turn_metadata(tmp_path: Path) -> None:
    """The server allocation, not old/client-era turn metadata, owns authz."""

    root = tmp_path / "threads"
    thread_id = "store-owned"
    store = ThreadStateStore(path=tmp_path / "thread-state.jsonl")
    store.ensure_thread(
        thread_id,
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    # Simulate historical metadata that predates the authenticated gateway
    # overwrite.  It must not grant Bob control once the store has an owner.
    turn = Turn(threadId=thread_id, params=_owned_params(thread_id, actor="bob", tenant="tenant-b"))
    claim = acquire_thread_turn_claim(root, thread_id)
    assert claim.bind_turn(turn.id)
    log.turn_started(thread_id, turn)
    gateway = RealtimeGateway(
        runtime=SimpleNamespace(_logs_root=root, _thread_store=store),
        require_auth=True,
    )
    try:
        with pytest.raises(_RpcError) as denied:
            asyncio.run(
                gateway._invoke(  # noqa: SLF001
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn.id},
                    _ControlConnection("bob", "tenant-b"),  # type: ignore[arg-type]
                )
            )
        assert denied.value.code == JsonRpcErrorCode.THREAD_NOT_FOUND
        assert not any(event.event == "turn_interrupt_requested" for event in log.iter_events())

        accepted = asyncio.run(
            gateway._invoke(  # noqa: SLF001
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn.id},
                _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
            )
        )
        assert accepted == {"turnId": turn.id, "interrupted": True}
    finally:
        claim.release()


def test_thread_store_owner_lookup_failure_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "threads"
    thread_id = "store-read-failure"
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    turn = Turn(threadId=thread_id, params=_owned_params(thread_id))
    claim = acquire_thread_turn_claim(root, thread_id)
    assert claim.bind_turn(turn.id)
    log.turn_started(thread_id, turn)

    class _UnavailableStore:
        def get(self, _thread_id: str) -> None:
            raise OSError("owner store unavailable")

    gateway = RealtimeGateway(
        runtime=SimpleNamespace(_logs_root=root, _thread_store=_UnavailableStore()),
        require_auth=True,
    )
    try:
        with pytest.raises(_RpcError) as caught:
            asyncio.run(
                gateway._invoke(  # noqa: SLF001
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn.id},
                    _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
                )
            )
        assert caught.value.code == JsonRpcErrorCode.INTERNAL_ERROR
        assert not any(event.event == "turn_interrupt_requested" for event in log.iter_events())
    finally:
        claim.release()


def test_ownerless_local_journal_retains_no_auth_interrupt_compatibility(
    tmp_path: Path,
) -> None:
    root = tmp_path / "threads"
    thread_id = "ownerless-local"
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    turn = Turn(threadId=thread_id, params=TurnParams(threadId=thread_id, input=[]))
    claim = acquire_thread_turn_claim(root, thread_id)
    assert claim.bind_turn(turn.id)
    log.turn_started(thread_id, turn)
    gateway = RealtimeGateway(
        runtime=SimpleNamespace(_logs_root=root),
        require_auth=False,
    )
    try:
        accepted = asyncio.run(
            gateway._invoke(  # noqa: SLF001
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn.id},
                _ControlConnection(None, None),  # type: ignore[arg-type]
            )
        )
        assert accepted == {"turnId": turn.id, "interrupted": True}
    finally:
        claim.release()


def test_authenticated_turn_metadata_is_overwritten_by_gateway(tmp_path: Path) -> None:
    gateway = RealtimeGateway(
        runtime=SimpleNamespace(_logs_root=tmp_path / "threads"),
        require_auth=True,
    )
    cleaned = gateway._sanitize_turn_params(  # noqa: SLF001
        {
            "threadId": "sanitized-owner",
            "input": [
                {
                    "type": "text",
                    "text": "work",
                    "metadata": {
                        "actor_id": "mallory",
                        "owner_actor_id": "mallory",
                        "tenant_id": "tenant-z",
                        "context": {
                            "actor_id": "mallory",
                            "owner_actor_id": "mallory",
                            "tenant_id": "tenant-z",
                        },
                    },
                }
            ],
        },
        _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
    )
    metadata = cleaned["input"][0]["metadata"]
    assert metadata["actor_id"] == "alice"
    assert metadata["owner_actor_id"] == "alice"
    assert metadata["tenant_id"] == "tenant-a"
    assert metadata["context"]["actor_id"] == "alice"
    assert metadata["context"]["owner_actor_id"] == "alice"
    assert metadata["context"]["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_interrupt_tail_failure_latches_local_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway import realtime_gateway as gateway_module

    root = tmp_path / "threads"
    thread_id = "tail-read-failure"
    turn_id = "turn-tail-read-failure"
    claim = acquire_thread_turn_claim(root, thread_id)
    delegate = _InterruptDelegate()
    emitter = _ClaimAwareEmitter(
        delegate,  # type: ignore[arg-type]
        claim,
        log=EventLog(thread_log_path(root, thread_id)),
    )

    def _raise_tail_error(*_args: Any, **_kwargs: Any) -> tuple[bool, int]:
        raise OSError("shared journal unavailable")

    monkeypatch.setattr(gateway_module, "tail_contains_interrupt", _raise_tail_error)
    try:
        emitter.register_turn(turn_id)
        await asyncio.wait_for(delegate.fired.wait(), timeout=0.5)
        assert delegate.is_turn_interrupted(turn_id) is True
    finally:
        emitter.unregister_turn(turn_id)
        claim.release()


def test_cross_worker_interrupt_releases_resident_approval(tmp_path: Path) -> None:
    context = _spawn_context()
    entered = context.Event()
    outcomes = context.Queue()
    root = tmp_path / "threads"
    thread_id = "approval-cross-worker"
    owner = context.Process(
        target=_run_approval_owner,
        args=(str(root), thread_id, entered, outcomes),
    )
    owner.start()
    try:
        assert entered.wait(15.0), "resident approval owner never became ready"
        kind, turn_id = outcomes.get(timeout=5.0)
        assert kind == "turn"

        with pytest.raises(_RpcError) as caught:
            _interrupt_via_gateway(
                root,
                thread_id,
                turn_id,
                actor="bob",
                tenant="tenant-b",
            )
        assert caught.value.code == JsonRpcErrorCode.THREAD_NOT_FOUND
        assert owner.is_alive(), "foreign actor unexpectedly stopped the turn"

        started = time.monotonic()
        assert _interrupt_via_gateway(
            root,
            thread_id,
            turn_id,
            actor="alice",
            tenant="tenant-a",
        ) == {"turnId": turn_id, "interrupted": True}
        _join_or_terminate(owner)
        assert time.monotonic() - started < 5.0
        assert owner.exitcode == 0

        observed = [outcomes.get(timeout=2.0), outcomes.get(timeout=2.0)]
        assert ("approval", {"action": "decline", "reason": "turn interrupted"}) in observed
        assert ("terminal", "interrupted") in observed
        events = list(EventLog(thread_log_path(root, thread_id)).iter_events())
        controls = [event for event in events if event.event == "turn_interrupt_requested"]
        assert len(controls) == 1
        assert controls[0].payload["requestedByActor"] == "alice"
        assert controls[0].payload["tenantId"] == "tenant-a"
        assert events[-1].event == "turn_completed"
        assert events[-1].payload["status"] == "interrupted"
    finally:
        if owner.is_alive():
            owner.terminate()
        owner.join(5.0)


def test_failed_durable_append_never_fires_local_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway import realtime_gateway as gateway_module
    from runtime.sensing.gateway.realtime_echo import EchoRuntime

    gateway = RealtimeGateway(
        runtime=EchoRuntime(logs_root=tmp_path / "threads"),
        require_auth=True,
    )
    turn_id = "turn-local"
    gateway._shared_interrupts.register(turn_id)  # noqa: SLF001

    def _fail(**_kwargs: Any) -> None:
        raise InterruptAuthorityUnavailable("fsync failed")

    monkeypatch.setattr(gateway_module, "persist_interrupt_request", _fail)
    with pytest.raises(_RpcError) as caught:
        asyncio.run(
            gateway._invoke(  # noqa: SLF001
                "turn/interrupt",
                {"threadId": "thread-local", "turnId": turn_id},
                _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
            )
        )
    assert caught.value.code == JsonRpcErrorCode.INTERNAL_ERROR
    assert gateway._shared_interrupts.is_interrupted(turn_id) is False  # noqa: SLF001
    gateway._shared_interrupts.unregister(turn_id)  # noqa: SLF001


def test_delayed_interrupt_epoch_cannot_poison_next_claim(tmp_path: Path) -> None:
    root = tmp_path / "threads"
    thread_id = "claim-aba"
    log = EventLog(thread_log_path(root, thread_id))
    log.thread_started(thread_id)
    first = Turn(threadId=thread_id, params=_owned_params(thread_id))
    first_claim = acquire_thread_turn_claim(root, thread_id)
    assert first_claim.bind_turn(first.id)
    log.turn_started(thread_id, first)
    persisted = persist_interrupt_request(
        logs_root=root,
        log=log,
        thread_id=thread_id,
        turn_id=first.id,
        actor_id="alice",
        tenant_id="tenant-a",
        auth_required=True,
    )
    first_claim.release()

    second = Turn(threadId=thread_id, params=_owned_params(thread_id))
    second_claim = acquire_thread_turn_claim(root, thread_id)
    assert second_claim.claim_epoch != persisted.claim_epoch
    assert second_claim.bind_turn(second.id)
    log.turn_started(thread_id, second)
    try:
        matched, _ = tail_contains_interrupt(
            log,
            0,
            thread_id=thread_id,
            turn_id=second.id,
            claim_epoch=second_claim.claim_epoch,
        )
        assert matched is False
    finally:
        second_claim.release()


def test_crashed_owner_and_terminal_turn_are_not_acknowledged_active(tmp_path: Path) -> None:
    context = _spawn_context()
    ready = context.Event()
    release = context.Event()
    root = tmp_path / "threads"
    thread_id = "crashed-interrupt-owner"
    owner = context.Process(
        target=_hold_owned_turn,
        args=(str(root), thread_id, ready, release),
    )
    owner.start()
    assert ready.wait(15.0)
    owner.terminate()
    owner.join(10.0)
    assert not owner.is_alive()

    log = EventLog(thread_log_path(root, thread_id))
    turn = log.replay()[0]
    assert _interrupt_via_gateway(
        root,
        thread_id,
        turn.id,
        actor="alice",
        tenant="tenant-a",
    ) == {"turnId": turn.id, "interrupted": False}
    assert not any(event.event == "turn_interrupt_requested" for event in log.iter_events())

    log.turn_completed(thread_id, turn.id, TurnStatus.FAILED)
    assert _interrupt_via_gateway(
        root,
        thread_id,
        turn.id,
        actor="alice",
        tenant="tenant-a",
    ) == {"turnId": turn.id, "interrupted": False}


def test_resume_never_reaps_a_live_claim_from_an_expired_or_missing_lease(
    tmp_path: Path,
) -> None:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    context = _spawn_context()
    ready = context.Event()
    release = context.Event()
    root = tmp_path / "threads"
    thread_id = "no-ttl-resume"
    owner = context.Process(
        target=_hold_owned_turn,
        args=(str(root), thread_id, ready, release),
    )
    owner.start()
    try:
        assert ready.wait(15.0)
        runtime = CerebrumRuntime(stack=object(), agent=object(), logs_root=str(root))
        resumed = asyncio.run(
            runtime.handle_request(
                "thread/resume",
                {"threadId": thread_id},
                _ControlConnection("alice", "tenant-a"),  # type: ignore[arg-type]
            )
        )
        assert resumed["turns"][0]["status"] == "inProgress"
        assert not any(
            event.event == "turn_completed"
            for event in EventLog(thread_log_path(root, thread_id)).iter_events()
        )
    finally:
        release.set()
        _join_or_terminate(owner)
    assert owner.exitcode == 0

