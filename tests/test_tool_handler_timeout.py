"""Audit T-06: tool handler wall-clock ceiling."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar

import pytest

from runtime.execution.suckers.registry import Skill
from runtime.execution.tool_engine._executor_helpers import (
    _call_handler_with_transient_retry,
)


def test_hung_handler_times_out() -> None:
    release = threading.Event()

    def hung(**kwargs):
        release.wait(30)
        return "late"

    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="exceeded its timeout"):
        _call_handler_with_transient_retry(hung, {}, timeout_s=0.2)
    assert time.monotonic() - t0 < 5.0
    release.set()  # let the worker exit before the process does


def test_fast_handler_with_timeout_returns() -> None:
    def fast(**kwargs):
        return "done"

    out, tags = _call_handler_with_transient_retry(fast, {}, timeout_s=5.0)
    assert out == "done"
    assert tags == []


def test_timeout_worker_preserves_call_context() -> None:
    marker: ContextVar[str] = ContextVar("tool_timeout_context", default="missing")
    token = marker.set("parent-call")
    try:
        out, tags = _call_handler_with_transient_retry(
            lambda **_kwargs: marker.get(),
            {},
            timeout_s=5.0,
        )
    finally:
        marker.reset(token)

    assert out == "parent-call"
    assert tags == []


def test_no_timeout_keeps_direct_call() -> None:
    calls: list[str] = []

    def h(**kwargs):
        calls.append("ran")
        return "ok"

    out, _ = _call_handler_with_transient_retry(h, {}, timeout_s=None)
    assert out == "ok"
    assert calls == ["ran"]


def test_transient_retry_still_works_under_timeout() -> None:
    state = {"n": 0}

    def flaky(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise ConnectionError("boom")
        return "recovered"

    out, tags = _call_handler_with_transient_retry(flaky, {}, timeout_s=5.0)
    assert out == "recovered"
    assert tags and tags[0].startswith("transient_retry:")


def test_skill_timeout_field() -> None:
    s = Skill(name="t", description="d", trusted_source="builtin://x", handler=lambda **k: None)
    assert s.timeout_s is None  # default: no ceiling, backward compatible
    s2 = Skill(
        name="t2",
        description="d",
        trusted_source="builtin://x",
        handler=lambda **k: None,
        timeout_s=3.5,
    )
    assert s2.timeout_s == 3.5

