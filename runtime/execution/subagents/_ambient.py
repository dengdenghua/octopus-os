"""Ambient sub-agent session attribution for token/cost journaling.

The in-process sub-agent runner runs the child's react loop on the bridge's
worker thread. While that loop writes ``token_usage`` journal rows it needs
to know which durable sub-agent session the spend belongs to, so a resume
path can sum per-session token/cost from the log alone (the dsh session-log
invariant extended to spend). ``react_model_stream`` reads this ContextVar;
``bridge._do_call`` scopes it around the child run. Each worker thread holds
its own context, so concurrent sub-agents attribute correctly. When unset
(parent turns, one-shot/remote children) the id stays ``""`` and the usage
row is not attributed — graceful, never incorrect.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_current_subagent_session_id: ContextVar[str] = ContextVar(
    "_current_subagent_session_id", default=""
)


@contextmanager
def subagent_session_scope(session_id: str) -> Iterator[None]:
    """Set the ambient sub-agent session id for the duration of a child run."""
    token = _current_subagent_session_id.set(session_id or "")
    try:
        yield
    finally:
        _current_subagent_session_id.reset(token)


def current_subagent_session_id() -> str:
    return _current_subagent_session_id.get()


_current_react_stack: ContextVar[Any] = ContextVar("_current_react_stack", default=None)


@contextmanager
def react_stack_scope(stack: Any) -> Iterator[None]:
    """Expose the parent turn's react stack to sub-agents running in this
    worker thread (the sub-agent dispatch happens synchronously inside the
    parent's ``stream_react_loop`` tool call, on the same thread). Lets the
    ephemeral runner drive a child through the MAIN react loop instead of the
    bespoke mini-loop. Never persisted — ambient only."""
    token = _current_react_stack.set(stack)
    try:
        yield
    finally:
        _current_react_stack.reset(token)


def current_react_stack() -> Any:
    return _current_react_stack.get()


__all__ = [
    "current_react_stack",
    "current_subagent_session_id",
    "react_stack_scope",
    "subagent_session_scope",
]
