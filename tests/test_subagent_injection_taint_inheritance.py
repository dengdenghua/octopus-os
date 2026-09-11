"""Producer side of prompt-injection taint inheritance.

When an injection-tainted parent spawns a sub-agent, the taint must cross
the spawn boundary so the sub-agent can't launder a risky action. The taint
lives in a per-thread ``contextvar`` that does NOT propagate across the
thread-pool boundary, so each spawn path captures it in the parent's context
and stamps it into the sub-agent's ``context`` (consumed at the sub-agent's
react-loop start, or — for ephemeral runs — re-marked before tool dispatch).

These tests pin the CAPTURE half of that contract (the consume half lives in
``test_react_loop.py`` and ``test_ephemeral_runner.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.safety.validation.prompt_injection import (
    mark_injection_taint,
    reset_injection_taint,
)


@pytest.fixture(autouse=True)
def _reset_taint():
    reset_injection_taint()
    yield
    reset_injection_taint()


# ── bridge.call_subagent (primary delegation path) ──────────────────────────


def _capturing_runner(captured: dict[str, Any]):
    def _runner(prompt, *, subagent_name, context):  # noqa: ARG001
        captured["context"] = context
        return "done"

    return _runner


def test_call_subagent_stamps_inherited_taint_when_parent_tainted():
    from runtime.execution.subagents import bridge

    captured: dict[str, Any] = {}
    orig = bridge._RUNNER
    bridge._RUNNER = _capturing_runner(captured)
    try:
        mark_injection_taint("high")
        bridge.call_subagent(
            agent_id="custom_explorer",
            role="researcher",
            prompt="explore",
        )
    finally:
        bridge._RUNNER = orig

    ctx = captured.get("context") or {}
    assert ctx.get("_inherited_injection_taint") == "high"


def test_call_subagent_no_taint_leaves_context_clean():
    from runtime.execution.subagents import bridge

    captured: dict[str, Any] = {}
    orig = bridge._RUNNER
    bridge._RUNNER = _capturing_runner(captured)
    try:
        # parent NOT tainted
        bridge.call_subagent(
            agent_id="custom_explorer",
            role="researcher",
            prompt="explore",
        )
    finally:
        bridge._RUNNER = orig

    ctx = captured.get("context") or {}
    assert "_inherited_injection_taint" not in ctx


# ── ParallelTaskRunner.submit (/api/tasks parallel path) ────────────────────


def test_parallel_runner_submit_captures_parent_taint(monkeypatch):
    from runtime.execution.misc.parallel_runner import (
        ParallelTask,
        ParallelTaskRunner,
    )

    runner = ParallelTaskRunner(max_workers=1)
    # Don't actually run the react loop (needs app state) — we only assert the
    # synchronous capture that happens in submit() before the pool boundary.
    monkeypatch.setattr(runner, "_run_task", lambda task_id: None)

    mark_injection_taint("high")
    task = ParallelTask(prompt="do risky thing", workspace_path="/tmp/x")
    runner.submit(task)

    assert task.context.get("_inherited_injection_taint") == "high"


def test_parallel_runner_submit_no_taint_leaves_context_clean(monkeypatch):
    from runtime.execution.misc.parallel_runner import (
        ParallelTask,
        ParallelTaskRunner,
    )

    runner = ParallelTaskRunner(max_workers=1)
    monkeypatch.setattr(runner, "_run_task", lambda task_id: None)

    # parent NOT tainted
    task = ParallelTask(prompt="clean thing")
    runner.submit(task)

    assert "_inherited_injection_taint" not in task.context
