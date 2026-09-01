"""Shadow-price accounting for tool-result pruning (dsh compaction/prune)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.execution.tool_engine.tool_output_pruner import (
    PRUNE_MARKER,
    prune_tool_result_text,
    set_shadow_price_sink,
)
from runtime.execution.tool_engine.tool_protocol import (
    normalize_step_tool_result,
    normalize_tool_result,
    render_tool_output,
)
from runtime.execution.tool_engine.tool_shadow_price import (
    CHARS_PER_TOKEN,
    PruneShadowPrice,
    default_shadow_price_sink,
    estimate_shadowed_tokens,
    shadow_price_ledger,
)


@pytest.fixture(autouse=True)
def _isolate_sink_and_ledger():
    ledger = shadow_price_ledger()
    ledger.reset()
    yield
    set_shadow_price_sink(default_shadow_price_sink)
    ledger.reset()


def _capturing_sink():
    captured: list[PruneShadowPrice] = []

    def sink(price: PruneShadowPrice) -> None:
        captured.append(price)

    return captured, sink


# ═══════════════════════════════════════════════════════════
# estimator
# ═══════════════════════════════════════════════════════════


def test_estimate_shadowed_tokens_uses_fixed_density() -> None:
    assert CHARS_PER_TOKEN == 4
    assert estimate_shadowed_tokens(0) == 0
    assert estimate_shadowed_tokens(4) == 1
    assert estimate_shadowed_tokens(5) == 2
    assert estimate_shadowed_tokens(1000) == 250
    assert estimate_shadowed_tokens(1001) == 251


# ═══════════════════════════════════════════════════════════
# pruner emission
# ═══════════════════════════════════════════════════════════


def test_prune_emits_shadow_price_with_attribution() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)
    text = "x" * 10000
    pruned = prune_tool_result_text(
        text,
        tool_name="exec_shell",
        call_id="call-1",
    )
    assert pruned is not None
    assert PRUNE_MARKER in pruned
    assert len(captured) == 1
    price = captured[0]
    assert price.tool_name == "exec_shell"
    assert price.call_id == "call-1"
    assert price.chars_before == 10000
    assert price.chars_after == len(pruned)
    assert price.chars_removed == 10000 - len(pruned)
    assert price.tokens_shadowed == estimate_shadowed_tokens(price.chars_removed)


def test_prune_within_budget_emits_nothing() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)
    assert prune_tool_result_text("short", tool_name="read_file") is None
    assert captured == []


def test_prune_invariant_shortcut_emits_nothing() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)

    class _OddPolicy:
        # Duck-typed policy bypassing validation: the marker alone exceeds the
        # input, so the "never grow the surface" invariant trips.
        threshold_chars = 5
        head_chars = 0
        tail_chars = 0
        marker = "m" * 15

    result = prune_tool_result_text("x" * 10, policy=_OddPolicy())
    assert result is None
    assert captured == []


def test_sink_disabled_emits_nothing() -> None:
    set_shadow_price_sink(None)
    captured = []
    pruned = prune_tool_result_text("x" * 10000)
    assert pruned is not None
    assert captured == []


def test_failing_sink_never_breaks_prune() -> None:
    def boom(_price: PruneShadowPrice) -> None:
        raise RuntimeError("sink down")

    set_shadow_price_sink(boom)
    pruned = prune_tool_result_text("x" * 10000)
    assert pruned is not None
    assert PRUNE_MARKER in pruned


# ═══════════════════════════════════════════════════════════
# ledger
# ═══════════════════════════════════════════════════════════


def test_default_sink_accumulates_into_ledger() -> None:
    set_shadow_price_sink(default_shadow_price_sink)
    text = "x" * 10000
    pruned = prune_tool_result_text(text)
    assert pruned is not None
    snapshot = shadow_price_ledger().snapshot()
    assert snapshot["prunes"] == 1
    assert snapshot["chars_removed"] == 10000 - len(pruned)
    assert snapshot["tokens_shadowed"] == estimate_shadowed_tokens(10000 - len(pruned))


def test_ledger_reset_for_isolation() -> None:
    set_shadow_price_sink(default_shadow_price_sink)
    prune_tool_result_text("x" * 10000)
    ledger = shadow_price_ledger()
    assert ledger.snapshot()["prunes"] >= 1
    ledger.reset()
    assert ledger.snapshot() == {"prunes": 0, "chars_removed": 0, "tokens_shadowed": 0}


# ═══════════════════════════════════════════════════════════
# render/normalize attribution
# ═══════════════════════════════════════════════════════════


def test_render_tool_output_attributes_shadow_price() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)
    rendered = render_tool_output(
        "x" * 10000,
        max_chars=16000,
        prune_middle=True,
        tool_name="exec_shell",
        call_id="call-render",
    )
    assert PRUNE_MARKER in rendered
    assert len(captured) == 1
    assert captured[0].tool_name == "exec_shell"
    assert captured[0].call_id == "call-render"


def test_normalize_tool_result_uses_call_id() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)
    call = {"id": "tool-abc123", "name": "exec_shell", "input": {}}
    result = normalize_tool_result(
        call,
        "x" * 10000,
        origin="native",
        max_chars=16000,
        prune_middle=True,
        tool_name="exec_shell",
    )
    assert PRUNE_MARKER in result.rendered
    assert captured[0].call_id == "tool-abc123"
    assert captured[0].tool_name == "exec_shell"


def test_normalize_step_tool_result_attributes() -> None:
    captured, sink = _capturing_sink()
    set_shadow_price_sink(sink)
    call = {"id": "tool-step-1", "name": "web_search", "input": {}}
    step = SimpleNamespace(
        action=call,
        result=SimpleNamespace(output="x" * 10000, status="success", error_type=None),
    )
    result = normalize_step_tool_result(
        step,
        origin="native",
        max_chars=16000,
        prune_middle=True,
        tool_name="web_search",
    )
    assert PRUNE_MARKER in result.rendered
    assert captured[0].call_id == "tool-step-1"
    assert captured[0].tool_name == "web_search"

