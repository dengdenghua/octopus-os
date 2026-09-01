"""Regression tests for terminal_router.reap_sessions — bounds _sessions growth.

A terminal shell is kept across ws reconnects (persistent shell), but a client
that disconnects and never returns would otherwise leak its shell + subprocess
forever. reap_sessions() — called on each new connection — drops dead shells,
reaps idle-beyond-TTL ones, and hard-caps the live count (least-recently-active
evicted first), while never touching the session being (re)connected to.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import pytest

from runtime.sensing.gateway import terminal_router as tr


@pytest.fixture(autouse=True)
def _clean_sessions() -> Iterator[None]:
    tr._sessions.clear()
    yield
    tr._sessions.clear()


def _session(sid: str, *, alive: bool, idle_seconds: float = 0.0) -> tr.ShellSession:
    sess = tr.ShellSession(sid)
    sess._alive = alive
    sess.last_activity = time.monotonic() - idle_seconds
    tr._sessions[sid] = sess
    return sess


@pytest.mark.asyncio
async def test_reaps_dead_sessions() -> None:
    _session("dead", alive=False)
    _session("alive", alive=True)
    await tr.reap_sessions()
    assert "dead" not in tr._sessions
    assert "alive" in tr._sessions


@pytest.mark.asyncio
async def test_reaps_idle_alive_sessions() -> None:
    _session("idle", alive=True, idle_seconds=tr._IDLE_TTL_SECONDS + 60)
    _session("fresh", alive=True, idle_seconds=1.0)
    await tr.reap_sessions()
    assert "idle" not in tr._sessions
    assert "fresh" in tr._sessions


@pytest.mark.asyncio
async def test_exclude_id_never_reaped() -> None:
    # Even a session that looks dead + idle is kept if it's the one being
    # (re)connected to — so a reconnect within the window keeps its shell.
    _session("reconnecting", alive=False, idle_seconds=tr._IDLE_TTL_SECONDS + 60)
    await tr.reap_sessions(exclude_id="reconnecting")
    assert "reconnecting" in tr._sessions


@pytest.mark.asyncio
async def test_hard_cap_evicts_least_recently_active() -> None:
    # All alive and well within the idle TTL, but over the hard cap → the
    # least-recently-active sessions are evicted down to _MAX_SESSIONS.
    overflow = 5
    for i in range(tr._MAX_SESSIONS + overflow):
        # s0 most-idle (largest idle), last one freshest — all idle << TTL.
        _session(f"s{i}", alive=True, idle_seconds=float(tr._MAX_SESSIONS + overflow - i))
    await tr.reap_sessions()
    assert len(tr._sessions) == tr._MAX_SESSIONS
    # The most-idle ones are the evicted set.
    for i in range(overflow):
        assert f"s{i}" not in tr._sessions
    assert f"s{tr._MAX_SESSIONS + overflow - 1}" in tr._sessions  # freshest kept


# ── Background reaper loop (enforces the TTL without a new connection) ──


@pytest.fixture(autouse=True)
def _stop_any_reaper() -> Iterator[None]:
    yield
    # Never leak a running reaper task across tests.
    tr._reaper_task = None


@pytest.mark.asyncio
async def test_background_reaper_reaps_without_a_connection(monkeypatch) -> None:
    # An abandoned shell must be freed on the TTL even when nobody opens
    # another terminal — the background sweep is the only trigger here.
    monkeypatch.setattr(tr, "_REAP_SWEEP_SECONDS", 0.01)
    _session("abandoned", alive=True, idle_seconds=tr._IDLE_TTL_SECONDS + 60)
    await tr._start_reaper()
    try:
        for _ in range(50):  # up to ~0.5s
            await asyncio.sleep(0.02)
            if "abandoned" not in tr._sessions:
                break
        assert "abandoned" not in tr._sessions
    finally:
        await tr._stop_reaper()


@pytest.mark.asyncio
async def test_start_reaper_is_idempotent_and_stop_cancels() -> None:
    await tr._start_reaper()
    first = tr._reaper_task
    await tr._start_reaper()  # no-op while one is live
    assert tr._reaper_task is first
    await tr._stop_reaper()
    assert tr._reaper_task is None
    assert first is not None and first.cancelled()
    # stop is safe to call again with nothing running.
    await tr._stop_reaper()

