"""Round, truncation, and model-selection policy for ephemeral agents."""

from __future__ import annotations

from typing import Any

EPHEMERAL_MAX_ROUNDS: int = 5
EPHEMERAL_MAX_ROUNDS_BY_ROLE: dict[str, int | None] = {
    "researcher": 40,
    "synthesizer": 20,
    "explorer": None,
    "implementer": 50,
    "debugger": 40,
    "architect": 25,
    "designer": 30,
    "planner": 20,
}

# Compatibility constant: sub-agent budgets now inherit parent/session accounting.
EPHEMERAL_TOKEN_BUDGET: int = 0


class EphemeralRoundCapExceeded(RuntimeError):
    """Raised when a sub-agent fails to converge within its round cap."""

    def __init__(self, partial_text: str, rounds: int, role_id: str) -> None:
        super().__init__(
            f"sub-agent {role_id!r} exceeded round cap ({rounds}) "
            f"without converging · {len(partial_text)} chars of partial output"
        )
        self.partial_text = partial_text
        self.rounds = rounds
        self.role_id = role_id


_LENGTH_LIMIT_FINISH_REASONS = {
    "length",
    "max_tokens",
    "max_output_tokens",
    "output_limit",
    "token_limit",
}

CONTINUE_AFTER_LENGTH_LIMIT = (
    "Your previous response was cut off by the output length limit. "
    "Continue exactly where it stopped, do not repeat earlier text, "
    "and finish every missing requirement."
)

_TRUNCATION_ENDINGS = (
    ".",
    "!",
    "?",
    "。",
    "！",
    "？",
    ")",
    "]",
    "】",
    "」",
    "”",
    "'",
    '"',
    "`",
    "…",
)


def is_length_limited_finish(reason: str | None) -> bool:
    return (reason or "").strip().lower() in _LENGTH_LIMIT_FINISH_REASONS


def looks_truncated_text(text: str, *, output_tokens: int, max_tokens: int) -> bool:
    """Best-effort fallback when a provider omits a truncation finish reason."""

    stripped = text.rstrip()
    if not stripped:
        return False
    if (
        max_tokens > 0
        and output_tokens > 0
        and output_tokens >= max(1, int(max_tokens * 0.9))
        and len(stripped) >= max(1200, int(max_tokens * 0.75))
    ):
        return True
    if max_tokens > 0 and len(stripped) >= max(2000, int(max_tokens * 1.5)):
        return not stripped.endswith(_TRUNCATION_ENDINGS)
    return bool(len(stripped) >= 1200 and not stripped.endswith(_TRUNCATION_ENDINGS))


def select_call_model(default_model: str, context: Any) -> str:
    """Honor one call's explicit model override, falling back to the factory model."""

    if isinstance(context, dict):
        override = context.get("model_name")
        if isinstance(override, str) and override.strip():
            return override.strip()
    return default_model


__all__ = [
    "CONTINUE_AFTER_LENGTH_LIMIT",
    "EPHEMERAL_MAX_ROUNDS",
    "EPHEMERAL_MAX_ROUNDS_BY_ROLE",
    "EPHEMERAL_TOKEN_BUDGET",
    "EphemeralRoundCapExceeded",
    "is_length_limited_finish",
    "looks_truncated_text",
    "select_call_model",
]
