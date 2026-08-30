"""Audit P-10: TentaclePool screen-change fan-out (dead queue removed)."""

from __future__ import annotations

import asyncio

from runtime.tentacle.pool import TentaclePool


def test_push_screen_change_reaches_subscribers() -> None:
    pool = TentaclePool()
    received: list[dict] = []

    async def sub(event: dict) -> None:
        received.append(event)

    pool.subscribe_screen_changes(sub)

    async def scenario() -> None:
        await pool.push_screen_change({"tentacle_id": "t1"})
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(received) == 1
    assert received[0]["event"] == "device.screen_changed"
    assert received[0]["tentacle_id"] == "t1"


def test_pool_has_no_dead_event_queue() -> None:
    pool = TentaclePool()
    assert not hasattr(pool, "_event_queue"), "dead unbounded queue should be gone"

