"""Deterministic head/middle/tail pruning for over-budget tool results.

Ported from DeepSeek Harness' ``compaction-tool-result-pruner``: instead of a
plain head truncation, an over-budget tool result is rewritten to a bounded
head + fixed marker + bounded tail, so the model keeps both the beginning and
the end of the output (errors and final answers usually live at the tail).
The original full content stays in the append-only journal; this pruner only
bounds the rendered surface text.

Invariants (mirroring dsh):
- text within ``threshold_chars`` is returned untouched (``None`` result).
- a pruned result is strictly smaller than the input.
- a pruned result is at or under ``threshold_chars``, so a second pass is a
  no-op (with budgets where ``head + marker + tail <= threshold``).
- all counting and slicing is by Unicode code point, never UTF-16 code units.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from .tool_shadow_price import (
    PruneShadowPrice,
    default_shadow_price_sink,
    estimate_shadowed_tokens,
)

LOGGER = logging.getLogger(__name__)

PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"

DEFAULT_PRUNE_THRESHOLD_CHARS = 8192
DEFAULT_PRUNE_HEAD_CHARS = 4096
DEFAULT_PRUNE_TAIL_CHARS = 1024

# Shared master switch for the react-loop and native tool-bridge render
# paths. On by default (dsh compaction default); set
# ``ECHO_TOOL_PRUNE_MIDDLE=0`` to restore head-only truncation.
TOOL_RESULT_PRUNE_ENABLED = os.environ.get("ECHO_TOOL_PRUNE_MIDDLE", "1") != "0"

# Shadow-price sink (dsh ``compaction/prune`` protocol): called with one
# ``PruneShadowPrice`` per actual prune. Defaults to the process ledger
# (observability counters only — never billing); ``set_shadow_price_sink(None)``
# disables emission. Best-effort: a failing sink never breaks pruning.
_shadow_price_sink: Callable[[PruneShadowPrice], None] | None = default_shadow_price_sink


def set_shadow_price_sink(
    sink: Callable[[PruneShadowPrice], None] | None,
) -> None:
    """Install the shadow-price sink (``None`` disables emission)."""
    global _shadow_price_sink
    _shadow_price_sink = sink


def _emit_shadow_price(
    text_before: str,
    text_after: str,
    *,
    tool_name: str | None,
    call_id: str | None,
) -> None:
    """Best-effort emission of one shadow-price record (dsh protocol)."""
    sink = _shadow_price_sink
    if sink is None:
        return
    chars_removed = max(0, len(text_before) - len(text_after))
    try:
        sink(
            PruneShadowPrice(
                tool_name=tool_name,
                call_id=call_id,
                chars_before=len(text_before),
                chars_after=len(text_after),
                chars_removed=chars_removed,
                tokens_shadowed=estimate_shadowed_tokens(chars_removed),
            )
        )
    except Exception:  # noqa: BLE001 — observability must never break pruning
        LOGGER.warning("shadow-price sink failed", exc_info=True)


@dataclass(frozen=True, slots=True)
class ToolResultPrunePolicy:
    """Character budgets for tool-result pruning."""

    threshold_chars: int = DEFAULT_PRUNE_THRESHOLD_CHARS
    head_chars: int = DEFAULT_PRUNE_HEAD_CHARS
    tail_chars: int = DEFAULT_PRUNE_TAIL_CHARS
    marker: str = PRUNE_MARKER

    def __post_init__(self) -> None:
        validate_prune_budgets(
            threshold_chars=self.threshold_chars,
            head_chars=self.head_chars,
            tail_chars=self.tail_chars,
            marker=self.marker,
        )

    @property
    def emitted_chars(self) -> int:
        """Characters of a fully pruned result (head + marker + tail)."""
        return self.head_chars + len(self.marker) + self.tail_chars


def validate_prune_budgets(
    *,
    threshold_chars: int,
    head_chars: int,
    tail_chars: int,
    marker: str,
) -> None:
    """Validate prune budgets the way dsh's ``resolveConfig`` does.

    ``head + marker + tail`` must fit under the threshold, which guarantees a
    pruned result is both smaller than the input and within budget, so a second
    pass never rewrites it again.
    """
    if (
        not isinstance(threshold_chars, int)
        or isinstance(threshold_chars, bool)
        or threshold_chars <= 0
    ):
        raise ValueError(
            f"ToolResultPrunePolicy: threshold_chars ({threshold_chars!r}) must be a positive integer"
        )
    for name, value in (("head_chars", head_chars), ("tail_chars", tail_chars)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"ToolResultPrunePolicy: {name} ({value!r}) must be a non-negative integer"
            )
    emitted = head_chars + len(marker) + tail_chars
    if emitted > threshold_chars:
        raise ValueError(
            f"ToolResultPrunePolicy: head_chars + marker + tail_chars ({emitted}) "
            f"must be at most threshold_chars ({threshold_chars})"
        )


def prune_tool_result_text(
    text: str,
    *,
    policy: ToolResultPrunePolicy | None = None,
    threshold_chars: int = DEFAULT_PRUNE_THRESHOLD_CHARS,
    head_chars: int = DEFAULT_PRUNE_HEAD_CHARS,
    tail_chars: int = DEFAULT_PRUNE_TAIL_CHARS,
    marker: str = PRUNE_MARKER,
    tool_name: str | None = None,
    call_id: str | None = None,
) -> str | None:
    """Return a pruned copy of ``text``, or ``None`` when it is within budget.

    The pruned copy keeps the first ``head_chars`` and last ``tail_chars``
    Unicode code points, joined by ``marker``. ``None`` means the original text
    is already within budget and should be used untouched.
    """
    if policy is not None:
        threshold_chars = policy.threshold_chars
        head_chars = policy.head_chars
        tail_chars = policy.tail_chars
        marker = policy.marker
    else:
        validate_prune_budgets(
            threshold_chars=threshold_chars,
            head_chars=head_chars,
            tail_chars=tail_chars,
            marker=marker,
        )

    if len(text) <= threshold_chars:
        return None

    pruned = text[:head_chars] + marker
    if tail_chars > 0:
        pruned += text[-tail_chars:]

    # Defensive invariant: never grow the surface (config validation already
    # guarantees this for valid budgets, but callers may hand us odd values).
    if len(pruned) >= len(text):
        return None
    # dsh shadow-price protocol: price the shadowed span at emission time so a
    # pure consumer can subtract it without retaining per-node prices.
    _emit_shadow_price(text, pruned, tool_name=tool_name, call_id=call_id)
    return pruned


__all__ = [
    "DEFAULT_PRUNE_HEAD_CHARS",
    "DEFAULT_PRUNE_TAIL_CHARS",
    "DEFAULT_PRUNE_THRESHOLD_CHARS",
    "PRUNE_MARKER",
    "TOOL_RESULT_PRUNE_ENABLED",
    "ToolResultPrunePolicy",
    "prune_tool_result_text",
    "set_shadow_price_sink",
    "validate_prune_budgets",
]
