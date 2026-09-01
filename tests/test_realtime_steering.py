from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from runtime.platform.process.thread_turn_claim import acquire_thread_turn_claim
from runtime.protocol import Turn
from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


class _Emitter:
    actor_id = None

    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        self.notifications.append((str(method), params))


@pytest.mark.asyncio
async def test_shutdown_drain_requests_checkpointed_pause_and_waits_for_turn(
    tmp_path: Path,
) -> None:
    from runtime.core.cerebrum.pause_control import get_pause_controller

    runtime = CerebrumRuntime(stack=object(), logs_root=str(tmp_path / "threads"))
    emitter = _Emitter()
    log = await runtime._ensure_thread("thread-shutdown", emitter)
    turn = Turn(thread_id="thread-shutdown", task_id="task-shutdown")
    log.turn_started(turn.thread_id, turn)
    runtime._active_turn_ids.add(turn.id)
    runtime._register_active_turn(turn, log)
    controller = get_pause_controller()
    controller.register_active(turn.task_id, thread_id=turn.thread_id, agent_id="general")

    async def _finish_at_safe_boundary() -> None:
        await asyncio.sleep(0.05)
        runtime._active_turn_ids.discard(turn.id)
        runtime._unregister_active_turn(turn.id)

    finisher = asyncio.create_task(_finish_at_safe_boundary())
    try:
        result = await runtime.drain_active_turns_for_shutdown(timeout_seconds=0.5)
        request = controller.get_request(turn.task_id)
        assert result == {
            "requested": [turn.task_id],
            "drained": [turn.id],
            "remaining": [],
        }
        assert request is not None
        assert request.requested_by == "server_shutdown"
        assert request.reason == "external"
    finally:
        await finisher
        controller.unregister_active(turn.task_id)
        controller.clear(turn.task_id)


def test_active_turn_lease_recovers_after_state_directory_is_recreated(
    tmp_path: Path,
) -> None:
    runtime = CerebrumRuntime(stack=object(), logs_root=str(tmp_path / "threads"))
    turn = Turn(thread_id="thread-recreated-state")

    runtime._write_active_turn_lease(turn)
    lease_path = runtime._active_turn_lease_path(turn.id)
    assert lease_path.is_file()

    shutil.rmtree(runtime._active_turn_lease_root)
    runtime._write_active_turn_lease(turn)

    payload = json.loads(lease_path.read_text(encoding="utf-8"))
    assert payload["turnId"] == turn.id
    assert payload["threadId"] == turn.thread_id
    assert payload["instanceId"] == runtime._instance_id


@pytest.mark.asyncio
async def test_turn_steer_is_persisted_and_queued_for_the_active_model(tmp_path: Path) -> None:
    runtime = CerebrumRuntime(stack=object(), logs_root=str(tmp_path / "threads"))
    emitter = _Emitter()
    log = await runtime._ensure_thread("thread-steer", emitter)
    turn = Turn(thread_id="thread-steer")
    log.turn_started(turn.thread_id, turn)
    runtime._active_turn_ids.add(turn.id)
    runtime._register_active_turn(turn, log)
    try:
        result = await runtime.handle_request(
            "turn/steer",
            {
                "threadId": "thread-steer",
                "turnId": turn.id,
                "itemId": "itm_client_1",
                "text": "先别改文件，先确认根因",
            },
            emitter,
        )

        assert result == {"turnId": turn.id, "itemId": "itm_client_1", "accepted": True}
        drained = runtime._drain_turn_steering(turn.id)
        assert drained == ["先别改文件，先确认根因"]
        assert runtime._drain_turn_steering(turn.id) == []
        runtime._turn_steering[turn.id].put(("itm_client_2", "随后到达的修正"))
        runtime._restore_turn_steering(turn.id, drained)
        assert runtime._drain_turn_steering(turn.id) == [
            "先别改文件，先确认根因",
            "随后到达的修正",
        ]
        assert len(turn.items) == 1
        assert turn.items[0].type == "steeringUserMessage"
        assert turn.items[0].status == "completed"
        assert (turn.items[0].timeline_sequence or 0) > 0
        assert [method for method, _ in emitter.notifications[-2:]] == [
            "item/started",
            "item/completed",
        ]
        replayed = log.replay()[0]
        assert [(item.id, item.type, item.status) for item in replayed.items] == [
            ("itm_client_1", "steeringUserMessage", "completed")
        ]
    finally:
        runtime._active_turn_ids.discard(turn.id)
        runtime._unregister_active_turn(turn.id)


@pytest.mark.asyncio
async def test_turn_steer_crosses_runtime_instances_and_reaches_the_active_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_root = tmp_path / "threads"
    active_runtime = CerebrumRuntime(stack=object(), logs_root=str(logs_root))
    remote_runtime = CerebrumRuntime(stack=object(), logs_root=str(logs_root))
    active_emitter = _Emitter()
    remote_emitter = _Emitter()
    log = await active_runtime._ensure_thread("thread-shared", active_emitter)
    turn = Turn(thread_id="thread-shared")
    claim = acquire_thread_turn_claim(logs_root, turn.thread_id)
    assert claim.bind_turn(turn.id)
    log.turn_started(turn.thread_id, turn)
    active_runtime._active_turn_ids.add(turn.id)
    active_runtime._register_active_turn(turn, log)
    try:
        result = await remote_runtime.handle_request(
            "turn/steer",
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "itemId": "itm_from_second_tab",
                "text": "第二个标签页要求先验证再修改",
            },
            remote_emitter,
        )
        assert result["accepted"] is True

        resumed = await remote_runtime.handle_request(
            "thread/resume",
            {"threadId": turn.thread_id},
            remote_emitter,
        )
        assert resumed["turns"][-1]["status"] == "inProgress"
        assert resumed["turns"][-1]["items"][-1]["type"] == "steeringUserMessage"
        assert resumed["turns"][-1]["items"][-1]["id"] == "itm_from_second_tab"

        # The owner process discovers the durable item, feeds it to the model,
        # and mirrors it onto the original tab without an in-memory signal.
        with monkeypatch.context() as patch:
            patch.setattr(
                log,
                "replay",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("live steering must tail the log, not replay it")
                ),
            )
            await active_runtime._publish_discovered_steering(turn, active_emitter)
            assert active_runtime._drain_turn_steering(turn.id) == ["第二个标签页要求先验证再修改"]
        assert any(
            method == "item/completed" and params["item"]["id"] == "itm_from_second_tab"
            for method, params in active_emitter.notifications
        )
        assert [item.id for item in turn.items].count("itm_from_second_tab") == 1

        # Retrying the same client item id is idempotent across instances.
        await remote_runtime.handle_request(
            "turn/steer",
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "itemId": "itm_from_second_tab",
                "text": "第二个标签页要求先验证再修改",
            },
            remote_emitter,
        )
        replayed = log.replay()[0]
        assert [item.id for item in replayed.items].count("itm_from_second_tab") == 1
    finally:
        active_runtime._active_turn_ids.discard(turn.id)
        active_runtime._unregister_active_turn(turn.id)
        claim.release()


@pytest.mark.asyncio
async def test_remote_steer_rejects_a_stale_owner_lease(tmp_path: Path) -> None:
    logs_root = tmp_path / "threads"
    owner = CerebrumRuntime(stack=object(), logs_root=str(logs_root))
    remote = CerebrumRuntime(stack=object(), logs_root=str(logs_root))
    emitter = _Emitter()
    log = await owner._ensure_thread("thread-stale-owner", emitter)
    turn = Turn(thread_id="thread-stale-owner")
    log.turn_started(turn.thread_id, turn)
    owner._active_turn_ids.add(turn.id)
    owner._register_active_turn(turn, log)
    try:
        lease_path = owner._active_turn_lease_path(turn.id)
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        lease["updatedAt"] = time.time() - 60
        lease_path.write_text(json.dumps(lease), encoding="utf-8")

        with pytest.raises(Exception, match="target turn is not active"):
            await remote.handle_request(
                "turn/steer",
                {
                    "threadId": turn.thread_id,
                    "turnId": turn.id,
                    "text": "这条消息不能落到已经失联的执行器",
                },
                emitter,
            )
        resumed = await remote.handle_request(
            "thread/resume",
            {"threadId": turn.thread_id},
            emitter,
        )
        assert resumed["turns"][-1]["status"] == "failed"
        assert resumed["turns"][-1]["error"]["code"] == "stale_in_progress_turn"
    finally:
        owner._active_turn_ids.discard(turn.id)
        owner._unregister_active_turn(turn.id)


@pytest.mark.asyncio
async def test_turn_steer_rejects_a_finished_turn(tmp_path: Path) -> None:
    runtime = CerebrumRuntime(stack=object(), logs_root=str(tmp_path / "threads"))
    emitter = _Emitter()
    await runtime._ensure_thread("thread-finished", emitter)

    with pytest.raises(Exception, match="target turn is not active"):
        await runtime.handle_request(
            "turn/steer",
            {
                "threadId": "thread-finished",
                "turnId": "turn-finished",
                "text": "继续",
            },
            emitter,
        )


@pytest.mark.asyncio
async def test_per_turn_injection_budget_limits_report_flood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.sensing.gateway._realtime_cerebrum_steering import _inject_thread_steering

    monkeypatch.setattr(
        "runtime.sensing.gateway._realtime_cerebrum_steering._max_turn_steering_injections",
        lambda: 2,
    )
    runtime = CerebrumRuntime(stack=object(), logs_root=str(tmp_path / "threads"))
    emitter = _Emitter()
    log = await runtime._ensure_thread("thread-budget", emitter)
    turn = Turn(thread_id="thread-budget")
    log.turn_started(turn.thread_id, turn)
    runtime._active_turn_ids.add(turn.id)
    runtime._register_active_turn(turn, log)
    try:
        # Only the per-turn budget of injections is accepted; the rest stay
        # durable in the store (returned False → injected next wake/turn).
        assert _inject_thread_steering("thread-budget", "r1") is True
        assert _inject_thread_steering("thread-budget", "r2") is True
        assert _inject_thread_steering("thread-budget", "r3") is False
        assert runtime._drain_turn_steering(turn.id) == ["r1", "r2"]

        # A human's explicit steering is NOT throttled by the report budget:
        # it is a separate user-initiated lane, not a sub-agent report.
        result = await runtime.handle_request(
            "turn/steer",
            {
                "threadId": "thread-budget",
                "turnId": turn.id,
                "itemId": "itm_human_1",
                "text": "先确认根因再改",
            },
            emitter,
        )
        assert result["accepted"] is True
        assert runtime._drain_turn_steering(turn.id) == ["先确认根因再改"]
    finally:
        runtime._active_turn_ids.discard(turn.id)
        runtime._unregister_active_turn(turn.id)

