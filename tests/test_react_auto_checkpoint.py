"""Tests for periodic auto-checkpoint helpers (P3 long-task durability).

The two helpers exposed for the loop:

* ``_checkpoint_interval()`` — reads ECHO_CHECKPOINT_EVERY_N each
  call so an operator can flip the knob without restart.
* ``_should_auto_checkpoint(iteration, interval)`` — decides whether
  a given iteration triggers a write.

These are deliberately tiny pure functions so we can exercise the
lifecycle without spinning up the full react_loop generator (which
needs a full stack + journal + LLM router).
"""

from __future__ import annotations

import pytest
from runtime.core.cerebrum.react_loop import (
    _checkpoint_interval,
    _should_auto_checkpoint,
)

# ══════════════════════════════════════════════════════════════════
# _checkpoint_interval — env var parsing
# ══════════════════════════════════════════════════════════════════


class TestCheckpointInterval:
    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ECHO_CHECKPOINT_EVERY_N", raising=False)
        assert _checkpoint_interval() == 10

    def test_blank_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "   ")
        assert _checkpoint_interval() == 10

    def test_explicit_zero_returns_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "0")
        assert _checkpoint_interval() == 0

    def test_negative_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "-3")
        assert _checkpoint_interval() == 10

    def test_positive_int_returned(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "5")
        assert _checkpoint_interval() == 5

    def test_garbage_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "not-a-number")
        assert _checkpoint_interval() == 10

    def test_re_read_each_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No caching — operator can flip mid-flight.
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "3")
        assert _checkpoint_interval() == 3
        monkeypatch.setenv("ECHO_CHECKPOINT_EVERY_N", "7")
        assert _checkpoint_interval() == 7
        monkeypatch.delenv("ECHO_CHECKPOINT_EVERY_N")
        assert _checkpoint_interval() == 10


# ══════════════════════════════════════════════════════════════════
# _should_auto_checkpoint — gating
# ══════════════════════════════════════════════════════════════════


class TestShouldAutoCheckpoint:
    def test_off_when_interval_zero(self) -> None:
        # Explicit zero disables checkpointing.
        for it in range(1, 100):
            assert _should_auto_checkpoint(it, 0) is False

    def test_off_when_interval_negative(self) -> None:
        for it in range(1, 20):
            assert _should_auto_checkpoint(it, -1) is False

    def test_iteration_zero_never_fires(self) -> None:
        # Iteration 0 is "loop hasn't done anything yet" — pointless
        # to checkpoint there even if interval is set.
        assert _should_auto_checkpoint(0, 5) is False

    def test_fires_at_multiples_of_interval(self) -> None:
        interval = 5
        # Should fire at 5, 10, 15, ...
        assert _should_auto_checkpoint(5, interval) is True
        assert _should_auto_checkpoint(10, interval) is True
        assert _should_auto_checkpoint(15, interval) is True

    def test_does_not_fire_between_multiples(self) -> None:
        interval = 5
        for it in (1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14):
            assert _should_auto_checkpoint(it, interval) is False, (
                f"iteration {it} should NOT fire at interval {interval}"
            )

    def test_interval_one_fires_every_step(self) -> None:
        for it in range(1, 20):
            assert _should_auto_checkpoint(it, 1) is True

    def test_negative_iteration_does_not_fire(self) -> None:
        # Defensive — iteration shouldn't be negative, but if it is,
        # we don't want a math-modulo surprise.
        assert _should_auto_checkpoint(-5, 5) is False
