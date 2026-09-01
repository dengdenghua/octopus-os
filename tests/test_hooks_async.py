"""
Async handler support for the lifecycle hooks system.

Contract pinned
---------------

1. ``async def`` handler returning a ``HookDecision`` is honored
2. Async handler returning None is treated as pass_through
3. Async handler raising is caught and treated as pass_through
   (same as sync)
4. Mixed chain · sync + async + sync · all fire in order
5. Async cancel short-circuits the chain (like sync cancel)
6. Async modify_args accumulates with later sync mods
7. Works inside a thread with a running event loop
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clear():
    from runtime.safety.hooks.registry import get_global_registry

    get_global_registry().clear()
    yield
    get_global_registry().clear()


class TestAsyncHandlers:
    def test_async_handler_returns_decision(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        async def _ah(event):
            await asyncio.sleep(0)  # force a yield
            return HookDecision.modify_args({"x": "async"})

        d = dispatch_pre_tool(sucker_id="s", args={"x": 0})
        assert d.modified_args == {"x": "async"}

    def test_async_handler_none_is_pass_through(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        async def _ah(event):
            await asyncio.sleep(0)
            return

        @register_hook(PreToolUseEvent)
        def _sync(event):
            return HookDecision.modify_args({"x": "sync"})

        d = dispatch_pre_tool(sucker_id="s", args={})
        assert d.modified_args == {"x": "sync"}

    def test_async_exception_tolerated(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        async def _ah(event):
            raise RuntimeError("async boom")

        @register_hook(PreToolUseEvent)
        def _good(event):
            return HookDecision.modify_args({"ok": True})

        d = dispatch_pre_tool(sucker_id="s", args={})
        assert d.modified_args == {"ok": True}

    def test_async_cancel_short_circuits(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        order: list[str] = []

        @register_hook(PreToolUseEvent)
        async def _a(event):
            order.append("a")
            return HookDecision.cancel("async_nope")

        @register_hook(PreToolUseEvent)
        def _b(event):
            order.append("b")
            return HookDecision.pass_through()

        d = dispatch_pre_tool(sucker_id="s", args={})
        assert d.cancelled is True
        assert d.reason == "async_nope"
        assert order == ["a"]

    def test_mixed_chain_ordering(self):
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        trace: list[str] = []

        @register_hook(PreToolUseEvent)
        def _s1(event):
            trace.append("s1")
            return HookDecision.modify_args({"x": 1})

        @register_hook(PreToolUseEvent)
        async def _a(event):
            await asyncio.sleep(0)
            trace.append("async")
            return HookDecision.modify_args({"x": 2})

        @register_hook(PreToolUseEvent)
        def _s2(event):
            trace.append("s2")
            return HookDecision.modify_args({"x": 3})

        d = dispatch_pre_tool(sucker_id="s", args={})
        assert trace == ["s1", "async", "s2"]
        assert d.modified_args == {"x": 3}

    def test_works_inside_running_loop(self):
        """Simulate being invoked from inside an already-running asyncio
        loop · dispatch should offload via worker thread."""
        from runtime.safety.hooks import (
            HookDecision,
            PreToolUseEvent,
            register_hook,
        )
        from runtime.safety.hooks.runner import dispatch_pre_tool

        @register_hook(PreToolUseEvent)
        async def _ah(event):
            await asyncio.sleep(0)
            return HookDecision.modify_args({"x": "from-loop"})

        async def _caller():
            # asyncio.to_thread runs dispatch in a worker thread
            # without a running loop · that's the expected normal
            # case. For "loop in current thread" we call dispatch
            # directly inside the coroutine.
            return dispatch_pre_tool(sucker_id="s", args={})

        d = asyncio.run(_caller())
        assert d.modified_args == {"x": "from-loop"}
