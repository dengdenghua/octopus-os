"""Anthropic prompt-cache hint helpers.

Anthropic's prompt-caching API accepts up to **4 cache breakpoints**
per request. Cache hits cost ≈ 10% of normal input tokens and live
for 5 minutes; cache writes cost ≈ 125% but are amortized across
every subsequent read.

This module picks the right breakpoints for a given ``ModelRequest``:

1. **System prompt** — usually the biggest, most stable segment.
2. **Tool specs** — same every turn across a conversation.
3. **Conversation prefix** — older turns in a multi-turn chat.

Callers typically don't need to call these directly; the Anthropic
adapter invokes ``prepare_cached_system`` / ``prepare_cached_tools``
before sending the request. Exported so alternative adapters
(OpenAI-compat proxies that forward to Anthropic, etc.) can opt in.

Design notes
------------
* Minimum segment size to cache is 1024 tokens for Sonnet/Opus,
  2048 for Haiku — below that the Anthropic API silently drops the
  ``cache_control`` hint. We apply a conservative 3000-character
  threshold (≈ 1000 tokens at ~3 chars/token) so we don't waste
  a breakpoint on a tiny header.
* Breakpoints are assigned **left-to-right** in priority order. Any
  budget left over after system+tools is spent on the oldest
  conversation turns so multi-turn chats warm the cache.
* All functions are **pure** and side-effect-free — they return new
  data structures so the caller can choose whether to install them.
"""

from __future__ import annotations

from typing import Any

# ── Thresholds ───────────────────────────────────────────────
# Anthropic's minimum cacheable size (tokens) varies by model:
#   - Haiku family: 2048 tokens
#   - Sonnet / Opus: 1024 tokens
# We use characters as a cheap proxy: roughly 3 chars ≈ 1 token for
# English. 3000 chars ≈ 1000 tokens, which is safe even for Haiku
# when we trust that tool specs / system prompts are dense text.
MIN_CACHE_CHARS: int = 3000

# Anthropic supports up to 4 breakpoints per request.
MAX_BREAKPOINTS: int = 4


def prepare_cached_system(
    system_text: str,
    *,
    min_chars: int = MIN_CACHE_CHARS,
) -> list[dict[str, Any]] | str:
    """Wrap ``system_text`` in a cache-enabled block list if large enough.

    Returns:
        - ``[{"type": "text", "text": ..., "cache_control": {...}}]``
          when the system prompt is big enough to benefit from caching.
        - The raw string otherwise (the Anthropic API accepts both).
    """
    if not system_text or len(system_text) < min_chars:
        return system_text
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def prepare_cached_tools(
    tools: list[dict[str, Any]],
    *,
    min_total_chars: int = MIN_CACHE_CHARS,
) -> list[dict[str, Any]]:
    """Attach a cache breakpoint to the *last* tool in ``tools``.

    Anthropic caches everything up to and including a block with
    ``cache_control`` — so a single hint on the last tool makes the
    entire tool list cacheable. Skips caching when the total encoded
    tool list is smaller than ``min_total_chars`` (not worth a
    breakpoint).

    Returns a shallow copy of ``tools`` — the original list is never
    mutated so callers that cache the tool list themselves stay safe.
    """
    if not tools:
        return tools
    total = sum(
        len(str(t.get("name", "")))
        + len(str(t.get("description", "")))
        + len(str(t.get("input_schema", "")))
        for t in tools
    )
    if total < min_total_chars:
        return [dict(t) for t in tools]
    out = [dict(t) for t in tools[:-1]]
    last = dict(tools[-1])
    last["cache_control"] = {"type": "ephemeral"}
    out.append(last)
    return out


def budget_breakpoints(
    *,
    has_system_cache: bool,
    has_tools_cache: bool,
    messages_remaining: int,
) -> int:
    """Return how many cache breakpoints are still available for
    conversation messages given what's already been spent.

    Anthropic's limit is 4 breakpoints total. System cache uses 1,
    tools cache uses 1, leaving up to 2 for conversation prefix.
    """
    used = (1 if has_system_cache else 0) + (1 if has_tools_cache else 0)
    return max(0, min(MAX_BREAKPOINTS - used, messages_remaining))


def mark_cache_breakpoint(message: dict[str, Any]) -> dict[str, Any]:
    """Add a ``cache_control: ephemeral`` to the last content block
    of ``message``.

    Input message shape (Anthropic content blocks)::

        {"role": "user", "content": [{"type": "text", "text": "..."}]}

    Returns a shallow copy — the original is not mutated.
    """
    out = dict(message)
    content = out.get("content")
    if isinstance(content, str):
        out["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return out
    if isinstance(content, list) and content:
        new_blocks = [dict(b) for b in content[:-1]]
        last = dict(content[-1])
        last["cache_control"] = {"type": "ephemeral"}
        new_blocks.append(last)
        out["content"] = new_blocks
    return out


def estimate_cache_savings(
    input_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> dict[str, float]:
    """Compute what a single turn's cache usage saved vs. the
    uncached equivalent. Costs are relative (1.0 = one un-cached
    input token), not absolute dollars — the caller multiplies by
    the model's per-token price to get dollars.

    Reads are 10% of normal cost; writes are 125% of normal cost.

    Returns a dict with::

        {"uncached_cost": float,
         "actual_cost": float,
         "savings_ratio": float,
         "cache_hit_ratio": float}
    """
    effective = max(0, input_tokens)
    reads = max(0, cache_read_tokens)
    writes = max(0, cache_creation_tokens)

    uncached = float(effective + reads + writes)
    actual = float(effective) + 0.10 * reads + 1.25 * writes
    savings = 1.0 - (actual / uncached) if uncached > 0 else 0.0
    hit_ratio = reads / (effective + reads + writes) if (effective + reads + writes) > 0 else 0.0
    return {
        "uncached_cost": uncached,
        "actual_cost": actual,
        "savings_ratio": round(savings, 4),
        "cache_hit_ratio": round(hit_ratio, 4),
    }


__all__ = [
    "MAX_BREAKPOINTS",
    "MIN_CACHE_CHARS",
    "budget_breakpoints",
    "estimate_cache_savings",
    "mark_cache_breakpoint",
    "prepare_cached_system",
    "prepare_cached_tools",
]
