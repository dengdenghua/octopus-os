"""Model-aware guard routing — apply code-smell guards only to cheap models.

The insight: Opus/Sonnet rarely make basic coding mistakes (async-without-await,
broad-except, hardcoded paths), but Haiku/Flash/glm-4-flash do. Code-smell guards
were accumulating 0 hits because the main loop runs on premium models, but they're
still valuable for sub-agents running on cheap models.

This module decides which guard categories to apply based on the model tier.
"""

from __future__ import annotations


def classify_model_tier(model: str | None) -> str:
    """Classify model into 'premium', 'cheap', or 'unknown'.

    Premium models (Opus, Sonnet, o1) are assumed to handle code quality
    natively and don't need code-smell guards. Cheap models (Haiku, Flash,
    mini, glm-4, qwen) benefit from explicit code-quality gates.
    """
    if not model:
        return "unknown"

    model_lower = str(model).lower()

    # Premium tier — large reasoning models
    premium_markers = [
        "opus",
        "sonnet",
        "o1",
        "o3",
        "gpt-4-turbo",
        "gpt-4o",  # not gpt-4o-mini
        "claude-3-5",
        "claude-3-opus",
        "claude-3-sonnet",
        "deepseek-r1",
    ]

    # But NOT mini/flash variants of these
    cheap_markers = [
        "haiku",
        "flash",
        "mini",
        "glm-4",
        "qwen",
        "llama-3.1-8b",
        "mistral-7b",
        "gemma",
        "phi-",
    ]

    # Check cheap first (more specific)
    for marker in cheap_markers:
        if marker in model_lower:
            return "cheap"

    # Then premium
    for marker in premium_markers:
        if marker in model_lower:
            return "premium"

    # Unknown defaults to cheap (conservative — apply guards)
    return "unknown"


def should_apply_code_smell_guards(model: str | None) -> bool:
    """Return whether code-smell guards should run for this model.

    Premium models: NO (they don't make these mistakes)
    Cheap models: YES (they need the guardrails)
    Unknown: YES (conservative default)
    """
    tier = classify_model_tier(model)
    return tier in ("cheap", "unknown")


def guard_categories_for_model(
    model: str | None,
    *,
    base_categories: frozenset[str] | set[str] | None = None,
) -> frozenset[str]:
    """Return the guard categories that should apply to this model.

    Base categories (if provided) are always included. Code-smell is
    added only for cheap/unknown models.

    Usage:
        categories = guard_categories_for_model(
            model="claude-haiku-4-5-20251001",
            base_categories={"security", "protocol"}
        )
        # Returns: {"security", "protocol", "code-smell"}
    """
    base = set(base_categories) if base_categories else set()

    # Security, protocol, verification apply to all models
    # (These check correctness, not code style)

    # Code-smell only for cheap models
    if should_apply_code_smell_guards(model):
        base.add("code-smell")

    return frozenset(base)


def explain_guard_policy(model: str | None) -> str:
    """Human-readable explanation of guard policy for a model.

    Used in logs and debugging to explain why certain guards did/didn't fire.
    """
    tier = classify_model_tier(model)

    if tier == "premium":
        return (
            f"Model '{model}' classified as PREMIUM tier. "
            "Code-smell guards DISABLED (premium models handle code quality natively). "
            "Security, protocol, and verification guards still active."
        )
    if tier == "cheap":
        return (
            f"Model '{model}' classified as CHEAP tier. "
            "Code-smell guards ENABLED (cheap models need quality guardrails). "
            "All guard categories active."
        )
    return (
        f"Model '{model}' tier UNKNOWN. "
        "Code-smell guards ENABLED (conservative default). "
        "All guard categories active."
    )


__all__ = [
    "classify_model_tier",
    "should_apply_code_smell_guards",
    "guard_categories_for_model",
    "explain_guard_policy",
]
