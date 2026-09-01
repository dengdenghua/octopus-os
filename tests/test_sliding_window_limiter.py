"""SlidingWindowLimiter · lenient per-key rate ceiling with self-cleanup.

Locks: bursts up to `limit` pass, the next is refused, the window slides
so capacity returns as hits age out, and idle buckets are reclaimed so a
rotating-key flood can't grow the map without bound.
"""

from __future__ import annotations

from runtime.platform.process.sliding_window_limiter import SlidingWindowLimiter


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_allows_burst_up_to_limit_then_refuses():
    clk = _FakeClock()
    lim = SlidingWindowLimiter(limit=3, window_s=60.0, clock=clk)
    assert [lim.allow("a") for _ in range(3)] == [True, True, True]
    assert lim.allow("a") is False  # 4th within the window is refused
    assert lim.allow("a") is False  # refused calls record nothing


def test_window_slides_capacity_returns():
    clk = _FakeClock()
    lim = SlidingWindowLimiter(limit=2, window_s=60.0, clock=clk)
    assert lim.allow("a") and lim.allow("a")
    assert lim.allow("a") is False
    clk.advance(61.0)  # both earlier hits age out of the window
    assert lim.allow("a") is True  # capacity restored


def test_keys_are_independent():
    clk = _FakeClock()
    lim = SlidingWindowLimiter(limit=1, window_s=60.0, clock=clk)
    assert lim.allow("alice") is True
    assert lim.allow("bob") is True  # bob unaffected by alice's usage
    assert lim.allow("alice") is False


def test_idle_buckets_are_reclaimed():
    clk = _FakeClock()
    lim = SlidingWindowLimiter(limit=5, window_s=60.0, clock=clk)
    for i in range(50):
        lim.allow(f"k{i}")
    assert lim.active_keys() == 50
    # Advance past the window + sweep cadence, then poke once to trigger
    # the amortized sweep — every idle bucket should be gone.
    clk.advance(120.0)
    lim.allow("trigger")
    assert lim.active_keys() == 1  # only the fresh "trigger" bucket remains


def test_active_bucket_survives_sweep():
    clk = _FakeClock()
    lim = SlidingWindowLimiter(limit=5, window_s=60.0, clock=clk)
    lim.allow("stale")
    clk.advance(40.0)
    lim.allow("active")  # keep this one warm
    clk.advance(40.0)  # now "stale" is >60s old; sweep cadence elapsed
    lim.allow("active")  # triggers sweep; "active" must be kept
    assert "stale" not in _keys(lim)
    assert "active" in _keys(lim)


def _keys(lim: SlidingWindowLimiter) -> set[str]:
    return set(lim._hits)  # test-only introspection

