"""One timeout policy for realtime and background Codex execution."""

from __future__ import annotations

import logging
import math
import os

DEFAULT_TIMEOUT_S = 30.0 * 60.0
MAX_TIMEOUT_S = 4.0 * 60.0 * 60.0
MIN_TIMEOUT_S = 30.0


def execution_timeout_s(override: object = None) -> float:
    raw = override or os.environ.get("ECHO_CODEX_APP_SERVER_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite timeout")
        return min(MAX_TIMEOUT_S, max(MIN_TIMEOUT_S, value))
    except (TypeError, ValueError, OverflowError):
        logging.getLogger(__name__).warning("Invalid Codex timeout; using default")
        return DEFAULT_TIMEOUT_S
