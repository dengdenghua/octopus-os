"""Tests for PromiseGate async concurrency control."""

import asyncio

import pytest

from runtime.execution.arms.promise_gate import GateError, PromiseGate


class TestPromiseGate:
    @pytest.mark.asyncio
    async def test_basic_acquire_release(self):
        gate = PromiseGate()
        assert not gate.is_locked
        await gate.acquire()
        assert gate.is_locked
        gate.release()
        assert not gate.is_locked

    @pytest.mark.asyncio
    async def test_context_manager(self):
        gate = PromiseGate()
        assert not gate.is_locked
        async with gate.enter():
            assert gate.is_locked
        assert not gate.is_locked

    @pytest.mark.asyncio
    async def test_sequential_access(self):
        gate = PromiseGate()
        results = []

        async def task(n):
            async with gate.enter():
                results.append(f"start_{n}")
                await asyncio.sleep(0.01)
                results.append(f"end_{n}")

        await asyncio.gather(task(1), task(2), task(3))

        # Verify sequential execution: start_1, end_1, start_2, end_2, ...
        assert results[0] == "start_1"
        assert results[1] == "end_1"
        assert results[2] == "start_2"
        assert results[3] == "end_2"

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        gate = PromiseGate()

        async with gate.enter("session_a"):
            assert gate.owner_session_id == "session_a"
            assert gate.is_locked

    @pytest.mark.asyncio
    async def test_different_session_rejected(self):
        gate = PromiseGate()

        # First acquire the gate
        await gate.acquire("session_a")
        assert gate.owner_session_id == "session_a"

        # Try to acquire from a different session (should fail immediately)
        with pytest.raises(GateError, match="cannot acquire gate"):
            await gate.acquire("session_b")

        gate.release()

    @pytest.mark.asyncio
    async def test_release_allows_next(self):
        gate = PromiseGate()
        order = []

        async def first():
            async with gate.enter():
                order.append("first_acquired")
                await asyncio.sleep(0.02)
                order.append("first_released")

        async def second():
            await asyncio.sleep(0.01)  # Let first acquire first
            async with gate.enter():
                order.append("second_acquired")

        await asyncio.gather(first(), second())

        assert order == [
            "first_acquired",
            "first_released",
            "second_acquired",
        ]

    @pytest.mark.asyncio
    async def test_reset(self):
        gate = PromiseGate()
        await gate.acquire("session_1")
        assert gate.is_locked
        await gate.reset()
        assert not gate.is_locked
        assert gate.owner_session_id is None

    @pytest.mark.asyncio
    async def test_multiple_waiters_queue(self):
        gate = PromiseGate()
        results = []

        async def worker(name, delay):
            async with gate.enter():
                results.append(f"{name}_start")
                await asyncio.sleep(delay)
                results.append(f"{name}_end")

        # Start all workers concurrently
        await asyncio.gather(
            worker("a", 0.03),
            worker("b", 0.02),
            worker("c", 0.01),
        )

        # Should execute in order due to gate serialization
        assert results == [
            "a_start",
            "a_end",
            "b_start",
            "b_end",
            "c_start",
            "c_end",
        ]

    @pytest.mark.asyncio
    async def test_exception_in_context_releases_gate(self):
        gate = PromiseGate()

        with pytest.raises(ValueError):
            async with gate.enter():
                assert gate.is_locked
                raise ValueError("test error")

        assert not gate.is_locked

    @pytest.mark.asyncio
    async def test_no_session_id_allows_any(self):
        gate = PromiseGate()

        # No session ID means no isolation
        await gate.acquire()
        gate.release()
        await gate.acquire()
        gate.release()

    @pytest.mark.asyncio
    async def test_concurrent_acquire_same_session(self):
        gate = PromiseGate()

        async def task():
            async with gate.enter("same_session"):
                await asyncio.sleep(0.01)

        # Multiple tasks with same session should work
        await asyncio.gather(task(), task(), task())
