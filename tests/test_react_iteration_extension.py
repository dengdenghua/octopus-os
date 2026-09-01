from __future__ import annotations

import json
import time

from runtime.core.cerebrum._react_execution_phase6g import (
    _auto_extend_iteration_limit,
    _is_making_iteration_progress,
)
from runtime.core.cerebrum.pause_control import PauseController
from runtime.core.cerebrum.react_loop import stream_react_loop
from runtime.core.cerebrum.react_loop_state import _LoopState
from runtime.core.cerebrum.react_types import ReActStep
from tests.test_react_loop import (
    _build_stack_with_executor,
    _drain,
    _intent,
    _ScriptedRouter,
)


def _successful_step(iteration: int, action: str) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        action=action,
        observation="ok",
        action_results=[{"tool_name": action.split("(", 1)[0], "ok": True}],
    )


def test_productive_trajectory_receives_two_bounded_in_place_extensions() -> None:
    controller = PauseController(store_path=None, autoload=False)
    controller.register_active("task-1", max_iterations=30)
    state = _LoopState(
        react_task_id="task-1",
        pause_controller=controller,
        iteration_base_limit=30,
        iteration_limit=30,
        steps=[
            _successful_step(1, 'read_file({"path":"a.py"})'),
            _successful_step(2, 'read_file({"path":"b.py"})'),
            _successful_step(3, 'grep_text({"query":"needle"})'),
            _successful_step(4, 'read_file({"path":"c.py"})'),
            _successful_step(5, 'exec_shell({"cmd":"pytest -q"})'),
        ],
    )

    assert _is_making_iteration_progress(state)
    assert _auto_extend_iteration_limit(state, 30) == 45
    assert _auto_extend_iteration_limit(state, 45) == 60
    assert _auto_extend_iteration_limit(state, 60) == 60
    assert controller.list_active()[0].max_iterations == 60


def test_repeated_or_failed_trajectory_does_not_auto_extend() -> None:
    repeated = [_successful_step(i, 'web_search({"query":"same"})') for i in range(1, 6)]
    state = _LoopState(
        react_task_id="task-loop",
        pause_controller=PauseController(store_path=None, autoload=False),
        iteration_base_limit=30,
        iteration_limit=30,
        steps=repeated,
    )

    assert not _is_making_iteration_progress(state)
    assert _auto_extend_iteration_limit(state, 30) == 30

    state.steps[-1].action_results = [{"tool_name": "web_search", "ok": False}]
    state.consecutive_same_failed_actions = 2
    assert not _is_making_iteration_progress(state)


def test_system_iteration_pauses_are_deduplicated_per_thread(tmp_path) -> None:
    controller = PauseController(store_path=tmp_path / "pause-state.json", autoload=False)
    controller.request_pause(
        "old-auto",
        reason="iteration_near_limit",
        requested_by="system",
        thread_id="thread-1",
    )
    controller.request_pause(
        "manual",
        reason="user_request",
        requested_by="operator",
        thread_id="thread-1",
    )
    controller.request_pause(
        "new-auto",
        reason="iteration_near_limit",
        requested_by="system",
        thread_id="thread-1",
    )

    pending_ids = {request.task_id for request in controller.list_pending()}
    assert pending_ids == {"manual", "new-auto"}


def test_explicit_none_pause_store_is_in_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    controller = PauseController(store_path=None, autoload=False)

    controller.request_pause(
        "memory-only",
        reason="user_request",
        requested_by="operator",
        thread_id="thread-memory",
    )

    assert controller.get_request("memory-only") is not None
    assert not (tmp_path / "pause_state.json").exists()


def test_existing_system_pauses_are_deduplicated_during_load(tmp_path) -> None:
    now = time.time()
    store = tmp_path / "pause-state.json"
    store.write_text(
        json.dumps(
            {
                "pending": [
                    {
                        "task_id": "old-auto",
                        "reason": "iteration_near_limit",
                        "requested_at": now - 100,
                        "requested_by": "system",
                        "thread_id": "thread-1",
                    }
                ],
                "paused": [
                    {
                        "task_id": "new-auto",
                        "reason": "model_spinning",
                        "requested_at": now,
                        "requested_by": "system",
                        "thread_id": "thread-1",
                    },
                    {
                        "task_id": "manual",
                        "reason": "user_request",
                        "requested_at": now - 50,
                        "requested_by": "operator",
                        "thread_id": "thread-1",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    controller = PauseController(store_path=store, autoload=True)

    assert {request.task_id for request in controller.list_pending()} == set()
    assert {request.task_id for request in controller.list_paused()} == {
        "new-auto",
        "manual",
    }


def test_stream_loop_continues_past_initial_limit_without_a_resume_turn() -> None:
    scripts = [
        f'Thought: inspect {index}\nAction: echo({{"text":"evidence-{index}"}})'
        for index in range(26)
    ]
    scripts.append("Final Answer: completed after the original iteration boundary")
    router = _ScriptedRouter(scripts)

    events, result = _drain(
        stream_react_loop(
            _build_stack_with_executor(router),
            _intent("perform a long evidence-backed analysis"),
            agent=None,
            max_iterations=15,
        )
    )

    assert result is not None and result.success
    assert router.calls == 27
    assert len(result.steps) == 27
    assert not any(event.get("type") == "react_paused" for event in events)
    assert result.final_answer == "completed after the original iteration boundary"

