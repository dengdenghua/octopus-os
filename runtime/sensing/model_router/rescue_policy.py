"""Compatibility exports for the canonical platform model-rescue policy."""

from __future__ import annotations

from runtime.platform.models.rescue_policy import (
    is_retryable_model_error,
    model_rescue_quality,
    next_custom_model_fallback,
    note_model_stall,
)

__all__: list[str] = [
    "is_retryable_model_error",
    "model_rescue_quality",
    "next_custom_model_fallback",
    "note_model_stall",
]
