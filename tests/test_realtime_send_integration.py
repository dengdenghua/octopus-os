from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)

pytestmark = pytest.mark.skipif(
    FastAPI is None,
    reason="fastapi required for realtime websocket tests",
)


def _receive_with_deadline(ws: Any, timeout_seconds: float = 5.0) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        pending = executor.submit(ws.receive_text)
        return decode_message(pending.result(timeout=timeout_seconds))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _receive_until(
    ws: Any,
    predicate: Callable[[Any], bool],
    *,
    limit: int = 40,
) -> tuple[Any, list[Any]]:
    received: list[Any] = []
    for _ in range(limit):
        message = _receive_with_deadline(ws)
        received.append(message)
        if predicate(message):
            return message, received
    raise AssertionError("expected realtime frame never arrived")


def _request(ws: Any, request_id: int, method: str, params: dict[str, Any]) -> None:
    ws.send_text(
        encode_message(
            JsonRpcRequest(id=request_id, method=method, params=params),
        )
    )


def test_new_turn_reconnect_and_running_steer_keep_stable_message_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One user submit + one steering submit remain one turn and two rows.

    This exercises the public JSON-RPC/WebSocket boundary rather than calling
    the runtime directly: the requester disconnects while the server-resident
    turn is blocked, a new socket resumes it, and steering is accepted on that
    original running turn.
    """

    import runtime.core.cerebrum.react_loop as react_loop
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    model_entered = threading.Event()
    release_model = threading.Event()

    def held_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        model_entered.set()
        assert release_model.wait(5.0), "test never released the running turn"
        yield {"type": "text_delta", "delta": "已处理追加要求"}
        yield {"type": "react_completed"}

    monkeypatch.setattr(react_loop, "stream_react_loop", held_stream)

    logs_root = tmp_path / "threads"
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)

    thread_id = "thread-send-integration"
    user_item_id = "itm_user_ws_integration_1"
    steer_item_id = "itm_user_ws_steer_1"
    turn_id: str | None = None
    first_frames: list[Any] = []

    try:
        with TestClient(app) as client:
            # First transport: the human message becomes visible, then the
            # socket goes away while the model is still running.
            with client.websocket_connect("/api/realtime") as first_ws:
                _request(
                    first_ws,
                    1,
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "userItemId": user_item_id,
                        "input": [{"type": "text", "text": "开始检查"}],
                        "approvalPolicy": "never",
                    },
                )
                user_completed, first_frames = _receive_until(
                    first_ws,
                    lambda frame: (
                        isinstance(frame, Notification)
                        and frame.method == "item/completed"
                        and frame.params.get("item", {}).get("id") == user_item_id
                    ),
                )
                assert isinstance(user_completed, Notification)
                started = next(
                    frame
                    for frame in first_frames
                    if isinstance(frame, Notification) and frame.method == "turn/started"
                )
                turn_id = started.params["turn"]["id"]
                assert model_entered.wait(2.0), "model did not enter its running phase"

            assert turn_id is not None

            # Second transport: resume the same in-progress turn, then steer
            # it using the client-minted id that the UI already rendered.
            with client.websocket_connect("/api/realtime") as resumed_ws:
                _request(
                    resumed_ws,
                    2,
                    "thread/resume",
                    {"threadId": thread_id},
                )
                resume_response, _ = _receive_until(
                    resumed_ws,
                    lambda frame: isinstance(frame, JsonRpcResponse) and frame.id == 2,
                )
                assert isinstance(resume_response, JsonRpcResponse)
                assert resume_response.error is None
                resumed_turns = resume_response.result["turns"]
                assert len(resumed_turns) == 1
                assert resumed_turns[0]["id"] == turn_id
                assert [
                    item["id"]
                    for item in resumed_turns[0]["items"]
                    if item["type"] == "userMessage"
                ] == [user_item_id]

                _request(
                    resumed_ws,
                    3,
                    "turn/steer",
                    {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "itemId": steer_item_id,
                        "text": "顺便检查许可证",
                    },
                )
                steer_response, steer_frames = _receive_until(
                    resumed_ws,
                    lambda frame: isinstance(frame, JsonRpcResponse) and frame.id == 3,
                )
                assert isinstance(steer_response, JsonRpcResponse)
                assert steer_response.error is None
                assert steer_response.result == {
                    "turnId": turn_id,
                    "itemId": steer_item_id,
                    "accepted": True,
                }
                completed_steering = [
                    frame
                    for frame in steer_frames
                    if isinstance(frame, Notification)
                    and frame.method == "item/completed"
                    and frame.params.get("item", {}).get("id") == steer_item_id
                ]
                assert len(completed_steering) == 1

                release_model.set()
                terminal, terminal_frames = _receive_until(
                    resumed_ws,
                    lambda frame: (
                        isinstance(frame, Notification) and frame.method == "turn/completed"
                    ),
                )
                assert isinstance(terminal, Notification)
                assert terminal.params["turn"]["id"] == turn_id
                assert not any(
                    isinstance(frame, Notification) and frame.method == "turn/started"
                    for frame in terminal_frames
                )

                _request(
                    resumed_ws,
                    4,
                    "thread/resume",
                    {"threadId": thread_id},
                )
                final_resume, _ = _receive_until(
                    resumed_ws,
                    lambda frame: isinstance(frame, JsonRpcResponse) and frame.id == 4,
                )
                assert isinstance(final_resume, JsonRpcResponse)
                assert final_resume.error is None
                final_turns = final_resume.result["turns"]
                assert len(final_turns) == 1
                final_items = final_turns[0]["items"]
                assert [
                    item["id"]
                    for item in final_items
                    if item["type"] in {"userMessage", "steeringUserMessage"}
                ] == [user_item_id, steer_item_id]
    finally:
        release_model.set()


def test_durable_report_lock_does_not_block_interrupt_or_lose_late_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production ReAct wrapper must not wait on durable report I/O.

    The first turn is interrupted while a worker owns the subagent store's
    durable lock.  Its deferred report scan may finish only after the turn is
    terminal, in which case injection must return false and leave the report
    unacknowledged.  The next turn then consumes and acknowledges it once.

    Only ``_stream_react_loop_impl`` is replaced: the public
    ``stream_react_loop`` wrapper (and therefore its busy/idle bookkeeping) is
    intentionally real.  Replacing the public wrapper would hide the exact
    lock regression this test guards.
    """

    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.execution.subagents.sessions as sessions_module
    from runtime.execution.subagents.sessions import (
        SubagentSessionStore,
        get_subagent_session_store,
        set_subagent_session_store,
    )
    from runtime.safety.approval.cancellation import current_cancellation_token
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    first_model_entered = threading.Event()
    first_model_cancelled = threading.Event()
    release_first_model = threading.Event()
    second_turn_drained: list[str] = []
    model_calls = 0
    model_calls_lock = threading.Lock()

    def deterministic_inner_loop(
        *_args: Any,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        nonlocal model_calls
        with model_calls_lock:
            model_calls += 1
            call_number = model_calls
        if call_number == 1:
            first_model_entered.set()
            token = current_cancellation_token()
            deadline = time.monotonic() + 5.0
            while (
                not token.is_cancelled
                and not release_first_model.is_set()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if token.is_cancelled:
                first_model_cancelled.set()
                yield {"type": "react_cancelled", "iteration": 1}
                return
            yield {"type": "react_completed"}
            return

        steering_drain = kwargs.get("steering_drain")
        assert callable(steering_drain)
        second_turn_drained.extend(steering_drain())
        yield {"type": "react_completed"}

    monkeypatch.setattr(
        react_loop,
        "_stream_react_loop_impl",
        deterministic_inner_loop,
    )

    logs_root = tmp_path / "threads"
    store = SubagentSessionStore(base_dir=tmp_path / "subagent_sessions")
    previous_store = get_subagent_session_store()
    set_subagent_session_store(store)
    session = store.create(agent_id="researcher", thread_id="thread-lock-isolation")
    store.append_report(
        session.session_id,
        content="锁释放后仍需交付",
        delivery="quiet",
    )

    injection_results: list[bool] = []
    injection_attempted = threading.Event()
    original_inject = sessions_module.inject_report_into_thread

    def observed_inject(thread_id: str, content: str) -> bool:
        result = original_inject(thread_id, content)
        injection_results.append(result)
        injection_attempted.set()
        return result

    monkeypatch.setattr(
        sessions_module,
        "inject_report_into_thread",
        observed_inject,
    )

    durable_lock_held = threading.Event()
    release_durable_lock = threading.Event()
    durable_lock_released = threading.Event()

    def hold_durable_lock() -> None:
        with store._lock:  # noqa: SLF001 - deliberate contention boundary
            durable_lock_held.set()
            release_durable_lock.wait(10.0)
        durable_lock_released.set()

    lock_holder = threading.Thread(target=hold_durable_lock, daemon=True)
    lock_holder.start()
    assert durable_lock_held.wait(1.0)

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(logs_root),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    thread_id = "thread-lock-isolation"
    first_turn_id: str | None = None

    try:
        with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
            _request(
                ws,
                10,
                "turn/start",
                {
                    "threadId": thread_id,
                    "userItemId": "itm_user_lock_first",
                    "input": [{"type": "text", "text": "启动后立即停止"}],
                    "approvalPolicy": "never",
                },
            )
            user_completed, initial_frames = _receive_until(
                ws,
                lambda frame: (
                    isinstance(frame, Notification)
                    and frame.method == "item/completed"
                    and frame.params.get("item", {}).get("id") == "itm_user_lock_first"
                ),
            )
            assert isinstance(user_completed, Notification)
            started = next(
                frame
                for frame in initial_frames
                if isinstance(frame, Notification) and frame.method == "turn/started"
            )
            first_turn_id = started.params["turn"]["id"]

            # The lifecycle spends at most its one-second startup budget on
            # the deferred scan. The real wrapper's busy marker must then pass
            # independently while the durable lock is still held.
            assert first_model_entered.wait(2.0), (
                "stream_react_loop wrapper waited for the durable store lock"
            )
            assert not durable_lock_released.is_set()

            _request(
                ws,
                11,
                "turn/interrupt",
                {"threadId": thread_id, "turnId": first_turn_id},
            )
            interrupt_response: JsonRpcResponse | None = None
            turn_response: JsonRpcResponse | None = None
            terminal: Notification | None = None
            for _ in range(40):
                frame = _receive_with_deadline(ws, timeout_seconds=2.0)
                if isinstance(frame, JsonRpcResponse) and frame.id == 11:
                    interrupt_response = frame
                elif isinstance(frame, JsonRpcResponse) and frame.id == 10:
                    turn_response = frame
                elif (
                    isinstance(frame, Notification)
                    and frame.method == "turn/completed"
                    and frame.params.get("turn", {}).get("id") == first_turn_id
                ):
                    terminal = frame
                if (
                    interrupt_response is not None
                    and turn_response is not None
                    and terminal is not None
                ):
                    break

            assert interrupt_response is not None
            assert interrupt_response.error is None
            assert interrupt_response.result["interrupted"] is True
            assert first_model_cancelled.is_set()
            assert terminal is not None
            assert turn_response is not None and turn_response.error is None
            assert turn_response.result["turn"]["status"] == "cancelled"
            assert not any(
                item["type"] == "steeringUserMessage"
                for item in turn_response.result["turn"]["items"]
            )
            assert not durable_lock_released.is_set(), (
                "interrupt or terminal response waited for durable report I/O"
            )

            # Let the old scan finish only after teardown. It must observe no
            # accepting active turn, return false, and leave the durable report
            # for the next turn rather than acknowledging a dropped queue row.
            release_durable_lock.set()
            assert injection_attempted.wait(3.0)
            deadline = time.monotonic() + 3.0
            while getattr(runtime, "_pending_subagent_report_tasks", {}) and (
                time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert injection_results == [False]
            assert len(store.pending_reports(session.session_id)) == 1

            _request(
                ws,
                12,
                "turn/start",
                {
                    "threadId": thread_id,
                    "userItemId": "itm_user_lock_second",
                    "input": [{"type": "text", "text": "继续处理报告"}],
                    "approvalPolicy": "never",
                },
            )
            second_response, _ = _receive_until(
                ws,
                lambda frame: isinstance(frame, JsonRpcResponse) and frame.id == 12,
            )
            assert isinstance(second_response, JsonRpcResponse)
            assert second_response.error is None
            assert injection_results == [False, True]
            assert second_turn_drained == ["[子代理报告] 锁释放后仍需交付"]
            steering_items = [
                item
                for item in second_response.result["turn"]["items"]
                if item["type"] == "steeringUserMessage"
            ]
            assert [item["text"] for item in steering_items] == ["[子代理报告] 锁释放后仍需交付"]
            assert store.pending_reports(session.session_id) == []
    finally:
        release_first_model.set()
        release_durable_lock.set()
        lock_holder.join(timeout=3.0)
        set_subagent_session_store(previous_store)

