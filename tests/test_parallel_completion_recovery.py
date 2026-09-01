from types import SimpleNamespace
from typing import Any

from runtime.core.cerebrum import react_parallel_dispatch
from runtime.core.cerebrum.react_execution import _has_unrecovered_beak_failure


def _beak_step(name: str, *, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        action=SimpleNamespace(name=name),
        result=SimpleNamespace(status=status, output={}),
    )


def _drain(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stopped:
            return events, stopped.value


def test_parallel_success_recovers_an_earlier_tool_failure(monkeypatch) -> None:
    successful_steps = []
    executor = SimpleNamespace(
        registry=SimpleNamespace(
            has=lambda name: name == "read_file",
            get=lambda _name: SimpleNamespace(affinity=[]),
        )
    )

    monkeypatch.setattr(
        react_parallel_dispatch,
        "_execute_action_via_beak",
        lambda _stack, action, **_kwargs: ("read succeeded", _beak_step("read_file")),
    )
    monkeypatch.setattr(
        react_parallel_dispatch,
        "_tool_event_extras_from_beak_step",
        lambda _step, _name: {},
    )

    _events, (_observation, results) = _drain(
        react_parallel_dispatch._dispatch_parallel_actions(
            [
                'read_file({"path": "a.py"})',
                'read_file({"path": "b.py"})',
            ],
            stack=SimpleNamespace(),
            executor=executor,
            iteration=2,
            react_task_id="task",
            agent=None,
            intent=SimpleNamespace(),
            beak_step_sink=successful_steps,
        )
    )

    assert [result["ok"] for result in results] == [True, True]
    assert len(successful_steps) == 2
    assert not _has_unrecovered_beak_failure(
        [_beak_step("grep_text", status="failed"), *successful_steps]
    )


# ═══════════════════════════════════════════════════════════
# Audit T-07: parallel batch wall-clock ceiling
# ═══════════════════════════════════════════════════════════


def test_parallel_batch_timeout_drains_hung_lane():
    """A hung lane must not pin the batch: beyond the ceiling the lane is
    timed out, the completed lane keeps its result, and the batch returns."""
    import concurrent.futures as _cf
    import threading
    import time

    from runtime.core.cerebrum.react_parallel_dispatch import (
        _collect_parallel_lane_results,
    )

    release = threading.Event()
    observations: list[str | None] = [None, None]
    beak_steps: list[Any] = [None, None]

    with _cf.ThreadPoolExecutor(max_workers=2) as pool:

        def _hung() -> tuple[str, None]:
            release.wait(30)
            return "hung-done", None

        def _fast() -> tuple[str, None]:
            return "fast-done", None

        futures = {pool.submit(_hung): 0, pool.submit(_fast): 1}
        t0 = time.monotonic()
        _collect_parallel_lane_results(futures, observations, beak_steps, timeout_s=1.0)
        elapsed = time.monotonic() - t0
        release.set()  # let the hung worker exit before the pool joins it

    assert elapsed < 5.0, f"batch did not time out: {elapsed:.2f}s"
    assert observations[1] == "fast-done"  # completed lane preserved
    assert observations[0] is not None and "超时" in observations[0]
    assert observations[0].startswith("(工具执行超时")


def test_parallel_batch_no_timeout_when_zero():
    """timeout_s=0 keeps the legacy indefinite wait (both lanes complete)."""
    import concurrent.futures as _cf

    from runtime.core.cerebrum.react_parallel_dispatch import (
        _collect_parallel_lane_results,
    )

    observations: list[str | None] = [None, None]
    beak_steps: list[Any] = [None, None]
    with _cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(lambda: ("a", None)): 0,
            pool.submit(lambda: ("b", None)): 1,
        }
        _collect_parallel_lane_results(futures, observations, beak_steps, timeout_s=0.0)
    assert observations == ["a", "b"]

