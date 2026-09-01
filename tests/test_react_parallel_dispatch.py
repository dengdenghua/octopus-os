"""Deadline and cancellation contracts for native ReAct parallel tools."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

from runtime.core.cerebrum import react_parallel_dispatch as dispatch
from runtime.safety.approval.cancellation import (
    CancellationSource,
    current_cancellation_token,
    scoped_cancellation,
)


def _executor(*names: str) -> SimpleNamespace:
    available = set(names)
    return SimpleNamespace(
        registry=SimpleNamespace(
            has=lambda name: name in available,
            get=lambda _name: SimpleNamespace(affinity=[]),
        )
    )


def _drain(generator: Any) -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stopped:
            return events, stopped.value


def _dispatch(
    actions: list[str],
    executor: SimpleNamespace,
    *,
    timeout_s: float,
) -> tuple[list[dict[str, Any]], Any]:
    return _drain(
        dispatch._dispatch_parallel_actions(
            actions,
            stack=SimpleNamespace(),
            executor=executor,
            iteration=1,
            react_task_id="task-parallel-deadline",
            agent=None,
            intent=SimpleNamespace(),
            parallel_batch_timeout_s=timeout_s,
        )
    )


def test_parallel_deadline_returns_without_waiting_for_noncooperative_lane(
    monkeypatch: Any,
    caplog: Any,
) -> None:
    """A timed-out Python worker may unwind late but cannot pin the turn."""
    release = threading.Event()
    finished = threading.Event()

    def _blocking(_stack: Any, _action: str, **_kwargs: Any) -> tuple[str, None]:
        try:
            release.wait(1.0)
            raise RuntimeError("late lane failure")
        finally:
            finished.set()

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _blocking)
    caplog.set_level(logging.WARNING, logger=dispatch.__name__)

    started_at = time.monotonic()
    try:
        events, (_observation, results) = _dispatch(
            ['read_file({"path":"a"})', 'read_file({"path":"b"})'],
            _executor("read_file"),
            timeout_s=0.03,
        )
        elapsed = time.monotonic() - started_at
        assert elapsed < 0.3, f"deadline path still waited for workers: {elapsed:.3f}s"
        assert all("超时" in str(result["observation"]) for result in results)
        assert all(result["ok"] is False for result in results)
        assert sum(event.get("type") == "tool_end" for event in events) == 2
        assert all(
            event.get("status") == "error" for event in events if event.get("type") == "tool_end"
        )
    finally:
        release.set()

    assert finished.wait(1.0)
    deadline = time.monotonic() + 1.0
    while "late lane failure" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "late lane failure" in caplog.text


def test_parallel_deadline_cancels_every_cooperative_lane(monkeypatch: Any) -> None:
    started: set[str] = set()
    cancelled: set[str] = set()
    state_lock = threading.Lock()

    def _cooperative(_stack: Any, action: str, **_kwargs: Any) -> tuple[str, None]:
        label = "a" if '"a"' in action else "b"
        with state_lock:
            started.add(label)
        token = current_cancellation_token()
        deadline = time.monotonic() + 1.0
        while not token.is_cancelled and time.monotonic() < deadline:
            time.sleep(0.002)
        if token.is_cancelled:
            with state_lock:
                cancelled.add(label)
        return f"cancelled={token.is_cancelled}", None

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _cooperative)

    _dispatch(
        ['read_file({"path":"a"})', 'read_file({"path":"b"})'],
        _executor("read_file"),
        timeout_s=0.04,
    )

    deadline = time.monotonic() + 1.0
    while len(cancelled) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert started == {"a", "b"}
    assert cancelled == {"a", "b"}


def test_parent_cancellation_detaches_noncooperative_parallel_lanes(
    monkeypatch: Any,
) -> None:
    """A user interrupt must not wait for the much longer batch deadline."""
    source = CancellationSource()
    release = threading.Event()
    all_started = threading.Event()
    state_lock = threading.Lock()
    started = 0

    def _noncooperative(_stack: Any, _action: str, **_kwargs: Any) -> tuple[str, None]:
        nonlocal started
        with state_lock:
            started += 1
            if started == 2:
                all_started.set()
        release.wait(1.0)
        return "late-success", None

    def _cancel_parent() -> None:
        if all_started.wait(1.0):
            source.cancel(reason="user interrupt")

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _noncooperative)
    cancel_thread = threading.Thread(target=_cancel_parent, daemon=True)
    cancel_thread.start()

    started_at = time.monotonic()
    try:
        with scoped_cancellation(source.token):
            _events, (_observation, results) = _dispatch(
                ['read_file({"path":"a"})', 'read_file({"path":"b"})'],
                _executor("read_file"),
                timeout_s=5.0,
            )
        elapsed = time.monotonic() - started_at
        assert elapsed < 0.3, f"parent cancellation waited for workers: {elapsed:.3f}s"
        assert all("工具执行已取消" in str(result["observation"]) for result in results)
        assert all(result["ok"] is False for result in results)
    finally:
        release.set()
        cancel_thread.join(timeout=1.0)


def test_parallel_success_preserves_declared_result_and_event_order(monkeypatch: Any) -> None:
    def _out_of_order(_stack: Any, action: str, **_kwargs: Any) -> tuple[str, None]:
        if '"first"' in action:
            time.sleep(0.05)
            return "first-result", None
        return "second-result", None

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _out_of_order)

    events, (_observation, results) = _dispatch(
        [
            'read_file({"path":"first"})',
            'read_file({"path":"second"})',
        ],
        _executor("read_file"),
        timeout_s=1.0,
    )

    assert [result["observation"] for result in results] == [
        "first-result",
        "second-result",
    ]
    assert [event["tool_name"] for event in events if event.get("type") == "tool_end"] == [
        "read_file",
        "read_file",
    ]
    start_ids = [event["tool_call_id"] for event in events if event.get("type") == "tool_start"]
    end_ids = [event["tool_call_id"] for event in events if event.get("type") == "tool_end"]
    assert end_ids == start_ids


def test_parallel_receipts_use_each_returned_executor_result(monkeypatch: Any) -> None:
    def _returned_receipt(_stack: Any, action: str, **_kwargs: Any):
        trusted = '"trusted"' in action
        beak_step = SimpleNamespace(
            result=SimpleNamespace(
                status="success",
                output="ok",
                trusted_execution=trusted,
                execution_source="canonical_builtin" if trusted else "registered_noncanonical",
            )
        )
        return "ok", beak_step

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _returned_receipt)

    _events, (_observation, results) = _dispatch(
        [
            'read_file({"path":"trusted"})',
            'read_file({"path":"ordinary"})',
        ],
        _executor("read_file"),
        timeout_s=1.0,
    )

    assert [result["trusted_execution"] for result in results] == [True, False]
    assert [result["execution_source"] for result in results] == [
        "canonical_builtin",
        "registered_noncanonical",
    ]


def test_write_batch_stays_serial_and_ignores_parallel_detach_deadline(
    monkeypatch: Any,
) -> None:
    source = CancellationSource()
    first_started = threading.Event()
    finished: list[str] = []

    def _slow_write(_stack: Any, action: str, **_kwargs: Any) -> tuple[str, None]:
        first_started.set()
        time.sleep(0.04)
        finished.append(action)
        return f"done:{action}", None

    def _cancel_parent() -> None:
        if first_started.wait(1.0):
            source.cancel(reason="user interrupt")

    monkeypatch.setattr(dispatch, "_execute_action_via_beak", _slow_write)
    cancel_thread = threading.Thread(target=_cancel_parent, daemon=True)
    cancel_thread.start()

    started_at = time.monotonic()
    try:
        with scoped_cancellation(source.token):
            _events, (_observation, results) = _dispatch(
                [
                    'write_text_file({"path":"a","content":"x"})',
                    'read_file({"path":"a"})',
                ],
                _executor("write_text_file", "read_file"),
                timeout_s=0.005,
            )
    finally:
        cancel_thread.join(timeout=1.0)
    elapsed = time.monotonic() - started_at

    assert elapsed >= 0.07
    assert len(finished) == 2
    assert all("超时" not in str(result["observation"]) for result in results)

