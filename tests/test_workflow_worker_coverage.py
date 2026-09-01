"""Dense coverage for workflow worker helpers (audit Q-05)."""

from __future__ import annotations

import pytest

from runtime.execution.workflow.types import WorkflowError
from runtime.execution.workflow.worker import WorkflowExecution, _default_label


def _exec(**kw) -> WorkflowExecution:
    kw.setdefault("run_id", "r1")
    kw.setdefault("name", "demo")
    kw.setdefault("body", "return 1")
    kw.setdefault("args", None)
    kw.setdefault("max_total_agents", 100)
    kw.setdefault("max_concurrent_agents", 4)
    kw.setdefault("max_items_per_call", 4096)
    return WorkflowExecution(**kw)


def test_default_label() -> None:
    assert _default_label("short") == "short"
    assert _default_label("line1\nline2") == "line1"
    long_line = "x" * 60
    label = _default_label(long_line)
    assert len(label) == 48 and label.endswith("…")


def test_assert_item_cap() -> None:
    ex = _exec(max_items_per_call=10)
    ex._assert_item_cap(5, "hook")  # no raise
    with pytest.raises(WorkflowError):
        ex._assert_item_cap(11, "hook")


def test_read_agent_options() -> None:
    ex = _exec()
    assert ex._read_agent_options(None) == {}
    opts = ex._read_agent_options(
        {"label": "l", "phase": "p", "model": "m", "schema": {"type": "object"}}
    )
    assert opts["label"] == "l"
    with pytest.raises(WorkflowError):
        ex._read_agent_options("not-a-dict")
    with pytest.raises(WorkflowError):
        ex._read_agent_options({"bogus": 1})
    with pytest.raises(WorkflowError):
        ex._read_agent_options({"on_change": 1})  # deferred option
    with pytest.raises(WorkflowError):
        ex._read_agent_options({"label": 5})
    with pytest.raises(WorkflowError):
        ex._read_agent_options({"schema": []})
    with pytest.raises(WorkflowError):
        ex._read_agent_options({"schema": {"type": "array"}})

