"""Shadow-price accounting for tool-result pruning.

Ported from DeepSeek Harness' ``compaction/prune`` shadow-price protocol:
when the pruner replaces an over-budget tool result, a log-only metering
record states the heuristic token price of the exact shadowed span, so a
pure consumer can subtract it without retaining per-node prices.

dsh's protocol lives on the session log (the metering event is appended
synchronously immediately before the replacement). This module is the
project's equivalent surface: ``tool_output_pruner`` emits one
``PruneShadowPrice`` per actual prune through a registered sink, with the
token price computed under dsh's fixed text-density estimator
(``ceil(chars / 4)``, ``CHARS_PER_TOKEN = 4``).

The shadow price is OBSERVABILITY, not billing: it prices content the model
never saw (context that pruning kept out of the request), so it must never
be added to ``UsagePricing`` (which would inflate the real bill). The
default sink accumulates into a process-level ledger whose snapshot can be
reported as "estimated tokens saved by pruning".
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

# Fixed text-density estimate used until exact tokenization is needed
# (mirrors dsh's ``CHARS_PER_TOKEN`` in ``token-meter/estimate.ts``).
CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class PruneShadowPrice:
    """One shadowed prune: what the model no longer sees, heuristically priced."""

    tool_name: str | None = None
    call_id: str | None = None
    chars_before: int = 0
    chars_after: int = 0
    chars_removed: int = 0
    tokens_shadowed: int = 0


def estimate_shadowed_tokens(chars_removed: int) -> int:
    """Heuristic token price of a shadowed span (dsh fixed density)."""
    if chars_removed <= 0:
        return 0
    return math.ceil(chars_removed / CHARS_PER_TOKEN)


class ShadowPriceLedger:
    """Process-level accumulation of shadow-price records (thread-safe).

    Counts prunes, characters removed, and estimated tokens shadowed.
    ``reset`` exists for test isolation only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prunes: int = 0
        self._chars_removed: int = 0
        self._tokens_shadowed: int = 0

    def record(self, price: PruneShadowPrice) -> None:
        with self._lock:
            self._prunes += 1
            self._chars_removed += price.chars_removed
            self._tokens_shadowed += price.tokens_shadowed

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "prunes": self._prunes,
                "chars_removed": self._chars_removed,
                "tokens_shadowed": self._tokens_shadowed,
            }

    def reset(self) -> None:
        with self._lock:
            self._prunes = 0
            self._chars_removed = 0
            self._tokens_shadowed = 0


_ledger = ShadowPriceLedger()


def default_shadow_price_sink(price: PruneShadowPrice) -> None:
    """Default sink: accumulate into the process ledger (never bills)."""
    _ledger.record(price)


def shadow_price_ledger() -> ShadowPriceLedger:
    """The process-level shadow ledger used by the default sink."""
    return _ledger


__all__ = [
    "CHARS_PER_TOKEN",
    "PruneShadowPrice",
    "ShadowPriceLedger",
    "default_shadow_price_sink",
    "estimate_shadowed_tokens",
    "shadow_price_ledger",
]
