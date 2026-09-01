"""Optional side-channel for streaming subprocess stdout/stderr."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Literal

ToolOutputSink = Callable[[Literal["stdout", "stderr"], str], None]

_current_sink: ContextVar[ToolOutputSink | None] = ContextVar(
    "tool_output_sink",
    default=None,
)


def current_sink() -> ToolOutputSink | None:
    """Return the sink bound in the current context, or ``None``."""
    return _current_sink.get()


@contextlib.contextmanager
def push_sink(sink: ToolOutputSink) -> Iterator[None]:
    """Bind ``sink`` for the duration of the ``with`` block."""
    token = _current_sink.set(sink)
    try:
        yield
    finally:
        _current_sink.reset(token)


def emit(stream: Literal["stdout", "stderr"], chunk: str) -> None:
    """Forward ``chunk`` to the active sink, if any."""
    sink = _current_sink.get()
    if sink is None or not chunk:
        return
    with contextlib.suppress(Exception):
        sink(stream, chunk)
