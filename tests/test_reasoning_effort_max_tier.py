"""The 'max' reasoning-effort tier — the top rung above xhigh.

'max' requests the largest extended-thinking budget; thinking_budget_for_effort
still clamps it to the response's max_tokens, so it only outspends xhigh when
the per-call budget is large enough to allow it.
"""

from __future__ import annotations

from runtime.platform.models.llm import (
    _REASONING_EFFORTS,
    _THINKING_BUDGET_BY_EFFORT,
    normalize_reasoning_effort,
    thinking_budget_for_effort,
)


def test_max_is_a_recognised_tier() -> None:
    assert "max" in _REASONING_EFFORTS
    assert normalize_reasoning_effort("max") == "max"
    assert normalize_reasoning_effort("MAX") == "max"  # case-insensitive


def test_max_budget_is_the_ceiling() -> None:
    assert _THINKING_BUDGET_BY_EFFORT["max"] > _THINKING_BUDGET_BY_EFFORT["xhigh"]
    # With ample response room, max requests more thinking than xhigh.
    assert thinking_budget_for_effort("max", 100_000) == 16384
    assert thinking_budget_for_effort("xhigh", 100_000) == 8192


def test_max_is_clamped_to_response_max_tokens() -> None:
    # A small response budget collapses max to the same clamp as any tier.
    assert thinking_budget_for_effort("max", 4096) == 4096 - 256
    # Tiny budgets floor at 1024.
    assert thinking_budget_for_effort("max", 1000) == 1024

