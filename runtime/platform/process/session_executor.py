from __future__ import annotations

import concurrent.futures as _cf
import contextvars as _cv
from collections.abc import Callable
from typing import Any


class SessionExecutor(_cf.ThreadPoolExecutor):
    """ThreadPoolExecutor that auto-propagates ContextVars to workers.

    Each ``submit`` captures the *caller's* current context at call time
    (not at pool-construction time · important for long-lived pools),
    wraps the callable in a ``parent_ctx.run(...)`` trampoline, and
    forwards the wrapped callable to the underlying executor.

    Everything else (shutdown, map, etc.) behaves like the stdlib pool.
    """

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> _cf.Future:  # type: ignore[override]
        ctx = _cv.copy_context()

        def _trampoline() -> Any:
            return ctx.run(fn, *args, **kwargs)

        return super().submit(_trampoline)

    # ``map`` in the stdlib calls our overridden ``submit`` under the
    # hood, so we get context propagation for free on map() too.


def run_in_context(ctx: _cv.Context, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` inside the supplied ``ctx`` snapshot.

    Thin convenience wrapper so callers don't have to import contextvars
    just to propagate a captured context. ``ctx.run`` is not re-entrant
    (a single Context object can only be active on one stack at a time)
    · fresh copies via ``contextvars.copy_context()`` are cheap.
    """
    return ctx.run(fn, *args, **kwargs)


__all__ = ["SessionExecutor", "run_in_context"]
